"""Experiment-owned streaming hierarchy built only from public cvxopf APIs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import re
from time import perf_counter
from typing import Mapping, Sequence, cast
import warnings

import numpy as np
import pandas as pd
import cvxpy as cp
from cvxpy.reductions.solvers.nlp_solving_chain import _set_nlp_initial_point

from cvxopf import (
    HierarchicalInputs,
    HierarchicalPolicy,
    HierarchicalSolveConfig,
    OPFBuild,
    StorageUnitIdeal,
    build_opf_multistep,
    extract_results,
)

from experiments.case118_annual_hierarchy.audit import ProbeAudit, audit_probe
from experiments.case118_annual_hierarchy.p0_fixture import (
    P0_EXPECTED_POLICY_SHA256,
    P0_EXPECTED_SOLVE_CONFIG_SHA256,
    policy_sha256,
    solve_config_sha256,
)


_STEP_NAME = re.compile(r"^(?P<base>.+)_(?P<step>\d+)$")


@dataclass(frozen=True)
class StreamingOuterPlan:
    """One retained frozen lossy-DC plan and its global SoC boundaries."""

    outer_plan_id: str
    build: OPFBuild
    result: Mapping[str, object]
    audit: ProbeAudit
    exception: str | None
    wall_time_seconds: float
    storage_device_ids: tuple[str, ...]
    global_boundary_indices: np.ndarray
    boundary_soc_mwh: np.ndarray | None

    @property
    def accepted_primal(self) -> bool:
        return self.exception is None and self.audit.accepted_primal

    def target_at(self, global_boundary: int) -> dict[str, float]:
        """Return one ID-aligned outer signpost at a global boundary."""
        if self.boundary_soc_mwh is None:
            raise ValueError("outer plan has no accepted SoC trajectory")
        matches = np.flatnonzero(self.global_boundary_indices == global_boundary)
        if matches.size != 1:
            raise ValueError("requested boundary is absent from outer plan")
        row = self.boundary_soc_mwh[int(matches[0])]
        return dict(zip(self.storage_device_ids, row.tolist(), strict=True))


def _copy_case(case: Mapping[str, object]) -> dict[str, object]:
    copied: dict[str, object] = {}
    for name, value in case.items():
        copied[name] = (
            np.asarray(value).copy() if isinstance(value, np.ndarray) else deepcopy(value)
        )
    return copied


def snapshot_inputs(inputs: HierarchicalInputs) -> HierarchicalInputs:
    """Take the private immutable-by-ownership snapshot used by one run."""
    return HierarchicalInputs(
        case=_copy_case(inputs.case),
        horizon_steps=inputs.horizon_steps,
        delta=inputs.delta,
        generators=tuple(replace(unit) for unit in inputs.generators),
        loads=tuple(replace(unit) for unit in inputs.loads),
        storage=tuple(replace(unit) for unit in inputs.storage),
        nondispatchable=tuple(replace(unit) for unit in inputs.nondispatchable),
        hvdc=tuple(replace(unit) for unit in inputs.hvdc),
        df_load_p=inputs.df_load_p.copy(deep=True),
        df_load_q=(
            None if inputs.df_load_q is None else inputs.df_load_q.copy(deep=True)
        ),
        df_nd=None if inputs.df_nd is None else inputs.df_nd.copy(deep=True),
        df_hvdc_min=(
            None
            if inputs.df_hvdc_min is None
            else inputs.df_hvdc_min.copy(deep=True)
        ),
        df_hvdc_max=(
            None
            if inputs.df_hvdc_max is None
            else inputs.df_hvdc_max.copy(deep=True)
        ),
        options=deepcopy(inputs.options),
    )


def validate_streaming_policy(policy: HierarchicalPolicy) -> None:
    """Reject any controller choice outside the frozen case118 experiment."""
    if policy.ac_window_steps != 3:
        raise ValueError("P0 streaming requires three-hour AC windows")
    if policy.outer_policy != "frozen":
        raise ValueError("P0 streaming requires one frozen outer plan")
    if policy.inner_terminal_policy != "hard_equality":
        raise ValueError("P0 streaming requires hard_equality terminal targets")
    if policy.initialization_policy != "shifted_with_recovery":
        raise ValueError("P0 streaming requires shifted_with_recovery")
    if policy_sha256(policy) != P0_EXPECTED_POLICY_SHA256:
        raise ValueError("P0 streaming policy differs from the frozen contract")


def validate_solve_config(config: HierarchicalSolveConfig) -> None:
    """Reject solver or option drift before constructing a scientific solve."""
    if solve_config_sha256(config) != P0_EXPECTED_SOLVE_CONFIG_SHA256:
        raise ValueError("P0 solve configuration differs from the frozen contract")


def _slice(frame: pd.DataFrame | None, start: int, stop: int) -> pd.DataFrame | None:
    return None if frame is None else frame.iloc[start:stop].copy(deep=True)


def build_window(
    inputs: HierarchicalInputs,
    formulation: str,
    start: int,
    stop: int,
    storage: Sequence[StorageUnitIdeal],
) -> OPFBuild:
    """Build one exact global slice through the existing public OPF builder."""
    if not (0 <= start < stop <= inputs.horizon_steps):
        raise ValueError("invalid half-open hierarchy window")
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="df_load_q is retained as reactive load.*"
        )
        warnings.filterwarnings(
            "ignore", message="Storage apparent_power_rating is applied.*"
        )
        return build_opf_multistep(
            _copy_case(inputs.case),
            T=stop - start,
            formulation=formulation,
            options=deepcopy(inputs.options),
            delta=inputs.delta,
            generators=[replace(unit) for unit in inputs.generators],
            loads=[replace(unit) for unit in inputs.loads],
            df_load_p=cast(pd.DataFrame, _slice(inputs.df_load_p, start, stop)),
            df_load_q=_slice(inputs.df_load_q, start, stop),
            nondispatchable=[replace(unit) for unit in inputs.nondispatchable],
            df_nd=_slice(inputs.df_nd, start, stop),
            storage=[replace(unit) for unit in storage],
            hvdc=[replace(unit) for unit in inputs.hvdc],
            df_hvdc_min=_slice(inputs.df_hvdc_min, start, stop),
            df_hvdc_max=_slice(inputs.df_hvdc_max, start, stop),
        )


def _storage_ids(storage: Sequence[StorageUnitIdeal]) -> tuple[str, ...]:
    ids = tuple(unit.device_id for unit in storage)
    if any(device_id is None for device_id in ids):
        raise ValueError("streaming storage requires explicit device IDs")
    return cast(tuple[str, ...], ids)


def variables_by_name(build: OPFBuild) -> dict[str, cp.Variable]:
    """Return the complete unambiguous model-variable namespace."""
    variables = build.prob.variables()
    names = [variable.name() for variable in variables]
    if len(names) != len(set(names)):
        raise ValueError("streaming initialization requires unique variable names")
    return dict(zip(names, variables, strict=True))


def complete_flat_start(build: OPFBuild) -> dict[str, np.ndarray]:
    """Construct the historical CVXPY DNLP flat starting point."""
    _set_nlp_initial_point(build.prob)
    result: dict[str, np.ndarray] = {}
    for name, variable in variables_by_name(build).items():
        if variable.value is None:
            raise RuntimeError(f"CVXPY did not initialize {name}")
        result[name] = np.asarray(variable.value, dtype=float).copy()
    return result


def assign_start(build: OPFBuild, values: Mapping[str, np.ndarray]) -> None:
    """Assign one complete named start without positional assumptions."""
    variables = variables_by_name(build)
    if set(values) != set(variables):
        raise ValueError("starting-value namespace does not match destination")
    for name, variable in variables.items():
        value = np.asarray(values[name], dtype=float)
        if value.shape != variable.shape:
            raise ValueError(f"starting-value shape mismatch for {name}")
        variable.value = value.copy()


def _values_by_step(
    values: Mapping[str, np.ndarray],
) -> tuple[dict[str, dict[int, np.ndarray]], dict[str, np.ndarray]]:
    stepped: dict[str, dict[int, np.ndarray]] = {}
    unsuffixed: dict[str, np.ndarray] = {}
    for name, value in values.items():
        match = _STEP_NAME.fullmatch(name)
        if match is None:
            unsuffixed[name] = np.asarray(value, dtype=float).copy()
        else:
            stepped.setdefault(match.group("base"), {})[
                int(match.group("step"))
            ] = np.asarray(value, dtype=float).copy()
    return stepped, unsuffixed


def shifted_start(
    preceding: Mapping[str, np.ndarray],
    destination: OPFBuild,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    realized_soc: Mapping[str, float],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Shift the immediately preceding prediction into the next AC window."""
    variables = variables_by_name(destination)
    source_stepped, source_unsuffixed = _values_by_step(preceding)
    raw: dict[str, np.ndarray] = {}
    projected: dict[str, np.ndarray] = {}
    soc_steps: list[int] = []
    for name, variable in variables.items():
        match = _STEP_NAME.fullmatch(name)
        if match is None:
            if name not in source_unsuffixed:
                raise ValueError(f"shift source lacks variable {name}")
            candidate = source_unsuffixed[name]
        else:
            base = match.group("base")
            step = int(match.group("step"))
            if base == "soc":
                soc_steps.append(step)
                continue
            if base not in source_stepped:
                raise ValueError(f"shift source lacks family {base}")
            family = source_stepped[base]
            shifted = step + 1
            if shifted in family:
                candidate = family[shifted]
            elif base in {"b", "b_q"}:
                candidate = np.zeros(variable.shape)
            else:
                candidate = family[max(family)]
        candidate = np.asarray(candidate, dtype=float)
        if candidate.shape != variable.shape:
            raise ValueError(f"shifted shape mismatch for {name}")
        raw[name] = candidate.copy()
        projected[name] = np.asarray(variable.project(candidate), dtype=float)
    if sorted(soc_steps) != list(range(len(soc_steps))):
        raise ValueError("destination SoC steps are not consecutive")
    ids = _storage_ids(inputs.storage)
    if set(realized_soc) != set(ids):
        raise ValueError("realized SoC identities do not match storage fleet")
    state = np.asarray([realized_soc[device_id] for device_id in ids], dtype=float)
    for step in soc_steps:
        b_name = f"b_{step}"
        if b_name not in projected:
            raise ValueError(f"shifted start lacks {b_name}")
        state = state - inputs.delta * projected[b_name]
        name = f"soc_{step}"
        candidate = state.copy()
        leaf = np.asarray(variables[name].project(candidate), dtype=float)
        if np.max(np.abs(leaf - candidate)) > policy.tolerances.soc_recurrence_mwh_abs:
            raise ValueError(f"reconstructed {name} violates destination bounds")
        raw[name] = candidate.copy()
        projected[name] = candidate.copy()
    if set(projected) != set(variables):
        raise ValueError("shift did not initialize every destination variable")
    return raw, projected


def perturbed_start(
    center: Mapping[str, np.ndarray],
    destination: OPFBuild,
    *,
    scale: float,
    seed: int,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Apply the frozen deterministic Fortran-order perturbation."""
    variables = variables_by_name(destination)
    if set(center) != set(variables):
        raise ValueError("perturbation center does not match destination")
    rng = np.random.default_rng(seed)
    raw: dict[str, np.ndarray] = {}
    projected: dict[str, np.ndarray] = {}
    for name in sorted(variables):
        variable = variables[name]
        value = np.asarray(center[name], dtype=float)
        flat = value.flatten(order="F")
        change = scale * np.maximum(1.0, np.abs(flat)) * rng.standard_normal(flat.size)
        candidate = (flat + change).reshape(value.shape, order="F")
        raw[name] = candidate.copy()
        projected[name] = np.asarray(variable.project(candidate), dtype=float)
    return raw, projected


def solve_frozen_outer(
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    solve_config: HierarchicalSolveConfig,
) -> StreamingOuterPlan:
    """Build and solve the one retained full-horizon lossy-DC plan."""
    validate_streaming_policy(policy)
    validate_solve_config(solve_config)
    storage = tuple(replace(unit) for unit in inputs.storage)
    build = build_window(inputs, "lossy_dc", 0, inputs.horizon_steps, storage)
    exception = None
    started = perf_counter()
    try:
        build.solve(
            solver=solve_config.outer.solver,
            nlp=False,
            **dict(solve_config.outer.options),
        )
    except Exception as exc:
        exception = f"{type(exc).__name__}: {exc}"
    elapsed = perf_counter() - started
    result = extract_results(build)
    audit = audit_probe(
        inputs.case,
        build,
        result,
        generators=inputs.generators,
        loads=inputs.loads,
        nondispatchable=inputs.nondispatchable,
        storage=storage,
        delta=inputs.delta,
        branch_limit_sentinel=inputs.options.branch_limit_sentinel,
        tolerances=policy.tolerances,
    )
    boundaries = None
    if exception is None and audit.accepted_primal:
        boundaries = np.vstack(
            [
                np.asarray([unit.initial_soc for unit in storage], dtype=float),
                np.asarray(result["soc"], dtype=float),
            ]
        )
    return StreamingOuterPlan(
        outer_plan_id="outer-000",
        build=build,
        result=result,
        audit=audit,
        exception=exception,
        wall_time_seconds=elapsed,
        storage_device_ids=_storage_ids(storage),
        global_boundary_indices=np.arange(inputs.horizon_steps + 1),
        boundary_soc_mwh=boundaries,
    )


__all__ = [
    "StreamingOuterPlan",
    "assign_start",
    "build_window",
    "complete_flat_start",
    "perturbed_start",
    "snapshot_inputs",
    "shifted_start",
    "solve_frozen_outer",
    "validate_solve_config",
    "validate_streaming_policy",
    "variables_by_name",
]
