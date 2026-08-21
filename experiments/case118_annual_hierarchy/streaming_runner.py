"""Experiment-owned streaming hierarchy built only from public cvxopf APIs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass, replace
from hashlib import sha256
import json
import re
from time import perf_counter
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Sequence, cast
import warnings

import numpy as np
import pandas as pd
import cvxpy as cp
from cvxpy.reductions.solvers.nlp_solving_chain import _set_nlp_initial_point
from cvxpy.reductions.cvx_attr2constr import CvxAttr2Constr
from cvxpy.reductions.dnlp2smooth.dnlp2smooth import Dnlp2Smooth
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
from cvxpy.reductions.solvers.solving_chain import SolvingChain

from cvxopf import (
    HierarchicalInputs,
    HierarchicalPolicy,
    HierarchicalSolveConfig,
    ACAttemptRecord,
    HierarchicalSolveAudit,
    IPOPTStartEvidence,
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
from experiments.case118_annual_hierarchy.streaming_schema import (
    ATTEMPT_ROLES,
    PERTURBATION_SCALES,
    attempt_id,
    perturbation_seed,
)


_STEP_NAME = re.compile(r"^(?P<base>.+)_(?P<step>\d+)$")
PhaseObserver = Callable[[str, int, int], None]


def _signpost_sha256(
    storage_device_ids: Sequence[str],
    global_boundary_indices: np.ndarray,
    boundary_soc_mwh: np.ndarray | None,
) -> str:
    digest = sha256()
    digest.update(json.dumps(list(storage_device_ids), separators=(",", ":")).encode())
    for values in (global_boundary_indices, boundary_soc_mwh):
        if values is None:
            digest.update(b"none\0")
            continue
        array = np.ascontiguousarray(np.asarray(values), dtype="<f8")
        digest.update(f"shape={array.shape}|".encode())
        digest.update(array.tobytes(order="C"))
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class StreamingOuterPlan:
    """One retained frozen lossy-DC plan and its global SoC boundaries."""

    outer_plan_id: str
    build: OPFBuild | None
    result: Mapping[str, object]
    audit: ProbeAudit
    exception: str | None
    wall_time_seconds: float
    storage_device_ids: tuple[str, ...]
    input_fingerprint: str
    horizon_steps: int
    delta_hours: float
    policy_sha256: str
    solve_config_sha256: str
    signpost_sha256: str
    global_boundary_indices: np.ndarray
    boundary_soc_mwh: np.ndarray | None

    def __post_init__(self) -> None:
        indices = np.asarray(self.global_boundary_indices, dtype=int).copy()
        if indices.ndim != 1 or not np.array_equal(
            indices, np.arange(self.horizon_steps + 1)
        ):
            raise ValueError("outer global boundary indices must span the horizon")
        boundary = None
        if self.boundary_soc_mwh is not None:
            boundary = np.asarray(self.boundary_soc_mwh, dtype=float).copy()
            if boundary.shape != (indices.size, len(self.storage_device_ids)):
                raise ValueError("outer SoC signposts have the wrong shape")
            if not np.all(np.isfinite(boundary)):
                raise ValueError("outer SoC signposts must be finite")
        actual_hash = _signpost_sha256(self.storage_device_ids, indices, boundary)
        if actual_hash != self.signpost_sha256:
            raise ValueError("outer signpost integrity hash mismatch")
        indices.setflags(write=False)
        if boundary is not None:
            boundary.setflags(write=False)
        object.__setattr__(self, "global_boundary_indices", indices)
        object.__setattr__(self, "boundary_soc_mwh", boundary)

    def verify_signpost_integrity(self) -> None:
        """Reject any retained signpost drift before target selection."""
        actual = _signpost_sha256(
            self.storage_device_ids,
            self.global_boundary_indices,
            self.boundary_soc_mwh,
        )
        if actual != self.signpost_sha256:
            raise ValueError("outer signpost integrity hash mismatch")

    @property
    def accepted_primal(self) -> bool:
        return self.exception is None and self.audit.accepted_primal

    def target_at(self, global_boundary: int) -> dict[str, float]:
        """Return one ID-aligned outer signpost at a global boundary."""
        self.verify_signpost_integrity()
        if self.boundary_soc_mwh is None:
            raise ValueError("outer plan has no accepted SoC trajectory")
        matches = np.flatnonzero(self.global_boundary_indices == global_boundary)
        if matches.size != 1:
            raise ValueError("requested boundary is absent from outer plan")
        row = self.boundary_soc_mwh[int(matches[0])]
        return dict(zip(self.storage_device_ids, row.tolist(), strict=True))


@dataclass(frozen=True)
class StreamingWindowResult:
    """One complete nine-slot decision and its optional state advance."""

    iteration: int
    interval_stop: int
    attempts: tuple[ACAttemptRecord, ...]
    controlling_attempt: ACAttemptRecord | None
    post_step_soc_mwh: Mapping[str, float] | None


@dataclass(frozen=True)
class CausalControllerSource:
    """Build-free preceding controller data required by shifted recovery."""

    attempt_id: str
    ordinal: int
    role: str
    iteration: int
    global_interval_start: int
    global_interval_stop: int
    outer_plan_id: str
    storage_device_ids: tuple[str, ...]
    initial_soc_mwh: Mapping[str, float]
    first_soc_mwh: np.ndarray
    first_b_mw: np.ndarray
    solution_values: Mapping[str, np.ndarray]

    @property
    def local_interval_start(self) -> int:
        return 0

    @property
    def local_interval_stop(self) -> int:
        return self.global_interval_stop - self.global_interval_start

    def __post_init__(self) -> None:
        ids = tuple(self.storage_device_ids)
        initial = {name: float(value) for name, value in self.initial_soc_mwh.items()}
        if set(initial) != set(ids) or not all(
            np.isfinite(value) for value in initial.values()
        ):
            raise ValueError("causal source initial SoC does not match storage IDs")
        first_soc = np.asarray(self.first_soc_mwh, dtype=float).copy()
        first_b = np.asarray(self.first_b_mw, dtype=float).copy()
        if first_soc.shape != (len(self.storage_device_ids),) or first_b.shape != (
            len(self.storage_device_ids),
        ):
            raise ValueError("causal source first-step storage arrays have wrong shape")
        if not np.all(np.isfinite(first_soc)) or not np.all(np.isfinite(first_b)):
            raise ValueError("causal source first-step storage arrays must be finite")
        values = {
            name: np.asarray(value, dtype=float).copy()
            for name, value in self.solution_values.items()
        }
        if not values or any(
            not np.all(np.isfinite(value)) for value in values.values()
        ):
            raise ValueError(
                "causal source solution values must be complete and finite"
            )
        first_soc.setflags(write=False)
        first_b.setflags(write=False)
        for value in values.values():
            value.setflags(write=False)
        object.__setattr__(self, "storage_device_ids", ids)
        object.__setattr__(self, "initial_soc_mwh", MappingProxyType(initial))
        object.__setattr__(self, "first_soc_mwh", first_soc)
        object.__setattr__(self, "first_b_mw", first_b)
        object.__setattr__(self, "solution_values", MappingProxyType(values))


@dataclass(frozen=True)
class _X0Run:
    assigned_start: Mapping[str, np.ndarray]
    evidence: IPOPTStartEvidence | None
    exception: str | None
    elapsed_seconds: float


@dataclass(frozen=True)
class _Slot:
    ordinal: int
    role: str
    transformation: str
    scale: float | None = None
    seed: int | None = None


def _copy_case(case: Mapping[str, object]) -> dict[str, object]:
    copied: dict[str, object] = {}
    for name, value in case.items():
        copied[name] = (
            np.asarray(value).copy()
            if isinstance(value, np.ndarray)
            else deepcopy(value)
        )
    return copied


def _fingerprint_value(value: object) -> object:
    if isinstance(value, pd.DataFrame):
        array = np.asarray(value.to_numpy(), dtype="<f8")
        return {
            "columns": [str(column) for column in value.columns],
            "index": [str(item) for item in value.index],
            "shape": array.shape,
            "sha256": sha256(
                np.ascontiguousarray(array).tobytes(order="C")
            ).hexdigest(),
        }
    if isinstance(value, np.ndarray):
        array = np.ascontiguousarray(value)
        return {
            "dtype": array.dtype.str,
            "shape": array.shape,
            "sha256": sha256(array.tobytes(order="C")).hexdigest(),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return _fingerprint_value(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _fingerprint_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_fingerprint_value(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported execution-fingerprint value {type(value).__name__}")


def execution_input_sha256(inputs: HierarchicalInputs) -> str:
    """Fingerprint the complete owned physical/model input snapshot."""
    payload = {
        "case": inputs.case,
        "horizon_steps": inputs.horizon_steps,
        "delta": inputs.delta,
        "generators": inputs.generators,
        "loads": inputs.loads,
        "storage": inputs.storage,
        "nondispatchable": inputs.nondispatchable,
        "hvdc": inputs.hvdc,
        "df_load_p": inputs.df_load_p,
        "df_load_q": inputs.df_load_q,
        "df_nd": inputs.df_nd,
        "df_hvdc_min": inputs.df_hvdc_min,
        "df_hvdc_max": inputs.df_hvdc_max,
        "options": inputs.options,
    }
    encoded = json.dumps(
        _fingerprint_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return sha256(encoded).hexdigest()


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
            None if inputs.df_hvdc_min is None else inputs.df_hvdc_min.copy(deep=True)
        ),
        df_hvdc_max=(
            None if inputs.df_hvdc_max is None else inputs.df_hvdc_max.copy(deep=True)
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


def _object_ids(build: OPFBuild) -> dict[str, tuple[int, ...]]:
    return {
        "variables": tuple(id(value) for value in build.prob.variables()),
        "constraints": tuple(id(value) for value in build.prob.constraints),
        "parameters": tuple(id(value) for value in build.prob.parameters()),
    }


def _layout_signature(layout: tuple[Mapping[str, object], ...]) -> str:
    normalized: list[dict[str, object]] = []
    auxiliary_index = 0
    for item in layout:
        original = bool(item["is_original_variable"])
        label = str(item["name"]) if original else f"auxiliary_{auxiliary_index}"
        auxiliary_index += int(not original)
        normalized.append(
            {
                "label": label,
                "shape": item["shape"],
                "start": item["start"],
                "stop": item["stop"],
                "is_original_variable": original,
            }
        )
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


def solve_ac_with_verified_x0(
    build: OPFBuild, solve_config: HierarchicalSolveConfig
) -> _X0Run:
    """Solve through a build-local IPOPT class and retain the exact reduced x0."""
    validate_solve_config(solve_config)
    assigned = complete_flat_start(build)
    original_variables = {variable.id: variable for variable in build.prob.variables()}
    before = _object_ids(build)
    captured_x0: np.ndarray | None = None
    captured_layout: tuple[dict[str, object], ...] | None = None

    def capturing_solve_via_data(
        self: object,
        data: Mapping[str, object],
        warm_start: bool,
        verbose: bool,
        solver_opts: dict[str, object],
        solver_cache: object = None,
    ) -> object:
        nonlocal captured_x0, captured_layout
        captured_x0 = np.asarray(data["x0"], dtype=float).copy()
        reduced_problem = cast(Any, data["problem"])
        reduced_variables = reduced_problem.variables()
        expected = np.concatenate(
            [
                np.asarray(variable.value, dtype=float).flatten(order="F")
                for variable in reduced_variables
            ]
        )
        layout: list[dict[str, object]] = []
        seen: set[int] = set()
        offset = 0
        originals_match = True
        for variable in reduced_variables:
            stop = offset + variable.size
            is_original = variable.id in original_variables
            if is_original:
                seen.add(variable.id)
                model_value = np.asarray(
                    original_variables[variable.id].value, dtype=float
                ).flatten(order="F")
                originals_match &= np.array_equal(captured_x0[offset:stop], model_value)
            layout.append(
                {
                    "name": variable.name(),
                    "shape": tuple(variable.shape),
                    "start": offset,
                    "stop": stop,
                    "is_original_variable": is_original,
                }
            )
            offset = stop
        captured_layout = tuple(layout)
        if not (
            offset == captured_x0.size
            and np.array_equal(captured_x0, expected)
            and seen == set(original_variables)
            and originals_match
        ):
            raise RuntimeError("assigned CVXPY values do not match IPOPT x0")
        return IPOPT.solve_via_data(
            self, data, warm_start, verbose, solver_opts, solver_cache
        )

    exception: str | None = None
    started = perf_counter()
    try:
        options = dict(solve_config.ac.options)
        warm_start = bool(options.pop("warm_start", False))
        verbose = bool(options.pop("verbose", False))
        if not verbose:
            options.setdefault("print_level", 0)
            options.setdefault("sb", "yes")
        solver_type = type(
            "_StreamingCapturingIPOPT",
            (IPOPT,),
            {"solve_via_data": capturing_solve_via_data},
        )
        solver = solver_type()
        chain = SolvingChain(
            reductions=[
                CvxAttr2Constr(reduce_bounds=not solver.BOUNDED_VARIABLES),
                Dnlp2Smooth(),
                solver,
            ]
        )
        canonical_problem, inverse_data = chain.apply(problem=build.prob)
        solution = solver.solve_via_data(
            canonical_problem,
            warm_start,
            verbose,
            solver_opts=options,
            solver_cache=None,
        )
        build.prob.unpack_results(solution, chain, inverse_data)
    except Exception as exc:
        exception = f"{type(exc).__name__}: {exc}"
    elapsed = perf_counter() - started
    after = _object_ids(build)
    evidence = None
    if captured_x0 is not None and captured_layout is not None:
        model_count = sum(
            cast(int, item["stop"]) - cast(int, item["start"])
            for item in captured_layout
            if bool(item["is_original_variable"])
        )
        evidence = IPOPTStartEvidence(
            complete_x0=captured_x0,
            layout=captured_layout,
            layout_signature=_layout_signature(captured_layout),
            model_coordinate_count=model_count,
            auxiliary_coordinate_count=captured_x0.size - model_count,
            object_ids_before=before,
            object_ids_after=after,
        )
    return _X0Run(assigned, evidence, exception, elapsed)


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
            stepped.setdefault(match.group("base"), {})[int(match.group("step"))] = (
                np.asarray(value, dtype=float).copy()
            )
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
    global_indices = np.arange(inputs.horizon_steps + 1)
    return StreamingOuterPlan(
        outer_plan_id="outer-000",
        build=build,
        result=result,
        audit=audit,
        exception=exception,
        wall_time_seconds=elapsed,
        storage_device_ids=_storage_ids(storage),
        input_fingerprint=execution_input_sha256(inputs),
        horizon_steps=inputs.horizon_steps,
        delta_hours=inputs.delta,
        policy_sha256=policy_sha256(policy),
        solve_config_sha256=solve_config_sha256(solve_config),
        signpost_sha256=_signpost_sha256(
            _storage_ids(storage), global_indices, boundaries
        ),
        global_boundary_indices=global_indices,
        boundary_soc_mwh=boundaries,
    )


def _validate_outer_binding(
    outer: StreamingOuterPlan,
    *,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    solve_config: HierarchicalSolveConfig,
) -> None:
    if outer.input_fingerprint != execution_input_sha256(inputs):
        raise ValueError("outer plan does not match the execution input snapshot")
    if outer.horizon_steps != inputs.horizon_steps:
        raise ValueError("outer plan horizon does not match the execution snapshot")
    if outer.delta_hours != inputs.delta:
        raise ValueError("outer plan timestep does not match the execution snapshot")
    if outer.storage_device_ids != _storage_ids(inputs.storage):
        raise ValueError(
            "outer plan storage identities do not match the execution snapshot"
        )
    if outer.policy_sha256 != policy_sha256(policy):
        raise ValueError("outer plan policy hash does not match the execution policy")
    if outer.solve_config_sha256 != solve_config_sha256(solve_config):
        raise ValueError(
            "outer plan solver hash does not match the execution configuration"
        )


def _p0_registry(iteration: int) -> tuple[_Slot, ...]:
    """Register the case118 P0 policy's closed nine-slot lifecycle."""
    causal = "flat" if iteration == 0 else "shifted_preceding"
    slots = [
        _Slot(0, ATTEMPT_ROLES[0], causal),
        _Slot(1, ATTEMPT_ROLES[1], causal),
        _Slot(2, ATTEMPT_ROLES[2], "copy_target_free"),
    ]
    for ordinal in range(3, 9):
        scale = PERTURBATION_SCALES[(ordinal - 3) % 3]
        transformation = "perturb_target_free" if ordinal < 6 else "perturb_causal"
        slots.append(
            _Slot(
                ordinal,
                ATTEMPT_ROLES[ordinal],
                transformation,
                scale,
                perturbation_seed(iteration, ordinal),
            )
        )
    return tuple(slots)


def _inner_storage(
    inputs: HierarchicalInputs,
    initial: Mapping[str, float],
    target: Mapping[str, float] | None,
) -> tuple[StorageUnitIdeal, ...]:
    ids = _storage_ids(inputs.storage)
    if set(initial) != set(ids) or (target is not None and set(target) != set(ids)):
        raise ValueError("inner storage state identities do not match the fleet")
    return tuple(
        replace(
            unit,
            initial_soc=float(initial[device_id]),
            terminal_soc=None if target is None else float(target[device_id]),
            terminal_constraint=None if target is None else "equality",
            terminal_cost=None,
            terminal_weight=None,
        )
        for unit, device_id in zip(inputs.storage, ids, strict=True)
    )


def _audit_attempt(
    build: OPFBuild,
    result: Mapping[str, object],
    probe: ProbeAudit,
    exception: str | None,
    elapsed: float,
) -> HierarchicalSolveAudit:
    status = probe.status
    accepted = exception is None and probe.accepted_primal
    if exception is not None:
        outcome = "solver_failure"
    elif status in {"infeasible", "infeasible_inaccurate"}:
        outcome = "solver_certified_infeasible"
    elif accepted:
        outcome = "accepted"
    else:
        outcome = "unusable_primal"
    stats = build.prob.solver_stats
    return HierarchicalSolveAudit(
        status=status,
        outcome=cast(Any, outcome),
        accepted_primal=accepted,
        missing_or_nonfinite_fields=probe.missing_or_nonfinite_fields,
        identity_error=probe.identity_error,
        residuals=probe.residuals,
        exception=exception,
        wall_time_seconds=elapsed,
        solver_num_iters=getattr(stats, "num_iters", None),
        solver_setup_time_seconds=getattr(stats, "setup_time", None),
        solver_solve_time_seconds=getattr(stats, "solve_time", None),
    )


def _empty_attempt(
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    outer: StreamingOuterPlan,
    iteration: int,
    stop: int,
    initial: Mapping[str, float],
    target: Mapping[str, float],
    slot: _Slot,
    state: Literal[
        "construction_error", "source_unavailable", "not_needed_after_acceptance"
    ],
    reason: str,
    *,
    source_kind: Literal["generated_flat", "attempt"] | None = None,
    source_attempt_id: str | None = None,
    build: OPFBuild | None = None,
    raw_start: Mapping[str, np.ndarray] | None = None,
    assigned_start: Mapping[str, np.ndarray] | None = None,
) -> ACAttemptRecord:
    return ACAttemptRecord(
        attempt_id=attempt_id(iteration, slot.ordinal),
        slot_state=state,
        role=cast(Any, slot.role),
        transformation=slot.transformation,
        ordinal=slot.ordinal,
        iteration=iteration,
        local_interval_start=0,
        local_interval_stop=stop - iteration,
        global_interval_start=iteration,
        global_interval_stop=stop,
        outer_plan_id=outer.outer_plan_id,
        source_kind=source_kind,
        source_attempt_id=source_attempt_id,
        inner_terminal_policy=policy.inner_terminal_policy,
        storage_device_ids=_storage_ids(inputs.storage),
        initial_soc_mwh=initial,
        target_soc_mwh=target,
        terminal_deviation_mwh=None,
        build=build,
        raw_start=raw_start,
        assigned_start=assigned_start,
        solver_evidence=None,
        result=None,
        audit=None,
        reason=reason,
        scale=slot.scale,
        seed=slot.seed,
    )


def _solution_values(attempt: ACAttemptRecord) -> dict[str, np.ndarray] | None:
    if (
        attempt.slot_state != "executed"
        or attempt.audit is None
        or not attempt.audit.accepted_primal
        or attempt.build is None
    ):
        return None
    return {
        name: np.asarray(variable.value, dtype=float).copy()
        for name, variable in variables_by_name(attempt.build).items()
    }


def causal_source_from_attempt(attempt: ACAttemptRecord) -> CausalControllerSource:
    """Detach the shifted-recovery source from its live AC build."""
    values = _solution_values(attempt)
    if values is None or attempt.result is None:
        raise ValueError("causal source requires an accepted live controller")
    ids = attempt.storage_device_ids
    steps = attempt.local_interval_stop
    return CausalControllerSource(
        attempt_id=attempt.attempt_id,
        ordinal=attempt.ordinal,
        role=attempt.role,
        iteration=attempt.iteration,
        global_interval_start=attempt.global_interval_start,
        global_interval_stop=attempt.global_interval_stop,
        outer_plan_id=attempt.outer_plan_id,
        storage_device_ids=ids,
        initial_soc_mwh=attempt.initial_soc_mwh,
        first_soc_mwh=np.asarray(attempt.result["soc"], dtype=float).reshape(
            steps, len(ids)
        )[0],
        first_b_mw=np.asarray(attempt.result["b"], dtype=float).reshape(
            steps, len(ids)
        )[0],
        solution_values=values,
    )


def _validate_preceding_controller(
    attempt: ACAttemptRecord | CausalControllerSource,
    *,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    outer: StreamingOuterPlan,
    iteration: int,
) -> None:
    """Require the actual immediately preceding controlling prediction."""
    expected_iteration = iteration - 1
    expected_stop = min(
        expected_iteration + policy.ac_window_steps, inputs.horizon_steps
    )
    if attempt.iteration != expected_iteration:
        raise ValueError(
            "causal source must come from the immediately preceding iteration"
        )
    if attempt.ordinal not in range(len(ATTEMPT_ROLES)):
        raise ValueError("causal source ordinal is outside the frozen registry")
    expected_role = ATTEMPT_ROLES[attempt.ordinal]
    if attempt.role != expected_role:
        raise ValueError("causal source role does not match its frozen ordinal")
    if attempt.attempt_id != attempt_id(expected_iteration, attempt.ordinal):
        raise ValueError("causal source attempt ID does not match the frozen registry")
    if (
        attempt.global_interval_start != expected_iteration
        or attempt.global_interval_stop != expected_stop
        or attempt.local_interval_start != 0
        or attempt.local_interval_stop != expected_stop - expected_iteration
    ):
        raise ValueError("causal source has the wrong preceding-window interval")
    if attempt.outer_plan_id != outer.outer_plan_id:
        raise ValueError("causal source does not belong to the frozen outer plan")
    if attempt.storage_device_ids != _storage_ids(inputs.storage):
        raise ValueError("causal source storage identities do not match the fleet")
    if isinstance(attempt, ACAttemptRecord):
        if (
            attempt.slot_state != "executed"
            or attempt.audit is None
            or not attempt.audit.accepted_primal
            or not attempt.supplied_executed_action
            or attempt.role == "target_free"
            or attempt.result is None
            or attempt.build is None
        ):
            raise ValueError("causal source must be an accepted controlling attempt")


def _validate_realized_state(
    realized_soc_mwh: Mapping[str, float],
    *,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    iteration: int,
    preceding: ACAttemptRecord | CausalControllerSource | None,
) -> dict[str, float]:
    """Bind the observed state to the frozen initial or preceding action."""
    ids = _storage_ids(inputs.storage)
    if set(realized_soc_mwh) != set(ids):
        raise ValueError("realized SoC identities do not match storage fleet")
    realized = np.asarray([realized_soc_mwh[device_id] for device_id in ids])
    if not np.all(np.isfinite(realized)):
        raise ValueError("realized SoC values must be finite")
    if iteration == 0:
        expected = np.asarray(
            [unit.initial_soc for unit in inputs.storage], dtype=float
        )
    else:
        if preceding is None:
            raise RuntimeError("validated preceding controller lacks a result")
        if isinstance(preceding, CausalControllerSource):
            first_soc = preceding.first_soc_mwh
            first_b = preceding.first_b_mw
        else:
            if preceding.result is None:
                raise RuntimeError("validated preceding controller lacks a result")
            first_soc = np.asarray(preceding.result["soc"], dtype=float).reshape(
                preceding.local_interval_stop, len(ids)
            )[0]
            first_b = np.asarray(preceding.result["b"], dtype=float).reshape(
                preceding.local_interval_stop, len(ids)
            )[0]
        preceding_initial = np.asarray(
            [preceding.initial_soc_mwh[device_id] for device_id in ids],
            dtype=float,
        )
        reconstructed = preceding_initial - inputs.delta * first_b
        tolerance = policy.tolerances.soc_recurrence_mwh_abs
        if float(np.max(np.abs(first_soc - reconstructed))) > tolerance:
            raise ValueError("preceding controller violates the SoC recurrence")
        expected = first_soc
    if (
        float(np.max(np.abs(realized - expected)))
        > policy.tolerances.soc_recurrence_mwh_abs
    ):
        raise ValueError("realized SoC does not match the physical state handoff")
    return dict(zip(ids, realized.tolist(), strict=True))


def _execute_attempt(
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    solve_config: HierarchicalSolveConfig,
    outer: StreamingOuterPlan,
    iteration: int,
    stop: int,
    initial: Mapping[str, float],
    target: Mapping[str, float],
    slot: _Slot,
    *,
    target_free: bool,
    raw_start: Mapping[str, np.ndarray] | None,
    assigned_start: Mapping[str, np.ndarray] | None,
    source_kind: Literal["generated_flat", "attempt"],
    source_attempt_id: str | None,
    prebuilt: OPFBuild | None = None,
    phase_observer: PhaseObserver | None = None,
) -> ACAttemptRecord:
    build = prebuilt
    retained_assigned: Mapping[str, np.ndarray] | None = None
    try:
        storage = _inner_storage(inputs, initial, None if target_free else target)
        if build is None:
            if phase_observer is not None:
                phase_observer("before_ac_build", iteration, slot.ordinal)
            build = build_window(inputs, "ac", iteration, stop, storage)
            if phase_observer is not None:
                phase_observer("after_ac_build", iteration, slot.ordinal)
        if assigned_start is not None:
            assign_start(build, assigned_start)
            retained_assigned = assigned_start
        if phase_observer is not None:
            phase_observer("before_ac_solve", iteration, slot.ordinal)
        run = solve_ac_with_verified_x0(build, solve_config)
        if phase_observer is not None:
            phase_observer("after_ac_solve", iteration, slot.ordinal)
    except Exception as exc:
        return _empty_attempt(
            inputs,
            policy,
            outer,
            iteration,
            stop,
            initial,
            target,
            slot,
            "construction_error",
            f"ac_construction_error:{type(exc).__name__}: {exc}",
            source_kind=source_kind,
            source_attempt_id=source_attempt_id,
            build=build,
            raw_start=raw_start,
            assigned_start=retained_assigned,
        )
    result = extract_results(build)
    probe = audit_probe(
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
        include_terminal=not target_free,
    )
    audit = _audit_attempt(build, result, probe, run.exception, run.elapsed_seconds)
    raw = run.assigned_start if raw_start is None else raw_start
    if run.evidence is None:
        return _empty_attempt(
            inputs,
            policy,
            outer,
            iteration,
            stop,
            initial,
            target,
            slot,
            "construction_error",
            "IPOPT x0 was not captured",
            source_kind=source_kind,
            source_attempt_id=source_attempt_id,
            build=build,
            raw_start=raw,
            assigned_start=run.assigned_start,
        )
    deviation = None
    if not target_free and result.get("soc") is not None:
        final = np.asarray(result["soc"], dtype=float).reshape(stop - iteration, -1)[-1]
        ids = _storage_ids(inputs.storage)
        deviation = {
            device_id: float(final[index] - target[device_id])
            for index, device_id in enumerate(ids)
        }
    return ACAttemptRecord(
        attempt_id=attempt_id(iteration, slot.ordinal),
        slot_state="executed",
        role=cast(Any, slot.role),
        transformation=slot.transformation,
        ordinal=slot.ordinal,
        iteration=iteration,
        local_interval_start=0,
        local_interval_stop=stop - iteration,
        global_interval_start=iteration,
        global_interval_stop=stop,
        outer_plan_id=outer.outer_plan_id,
        source_kind=source_kind,
        source_attempt_id=source_attempt_id,
        inner_terminal_policy=policy.inner_terminal_policy,
        storage_device_ids=_storage_ids(inputs.storage),
        initial_soc_mwh=initial,
        target_soc_mwh=target,
        terminal_deviation_mwh=deviation,
        build=build,
        raw_start=raw,
        assigned_start=run.assigned_start,
        solver_evidence=run.evidence,
        result=result,
        audit=audit,
        reason=None,
        supplied_executed_action=audit.accepted_primal and not target_free,
        scale=slot.scale,
        seed=slot.seed,
    )


def execute_streaming_window(
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    solve_config: HierarchicalSolveConfig,
    outer: StreamingOuterPlan,
    iteration: int,
    realized_soc_mwh: Mapping[str, float],
    preceding_controlling_attempt: ACAttemptRecord | CausalControllerSource | None,
    phase_observer: PhaseObserver | None = None,
) -> StreamingWindowResult:
    """Resolve one frozen nine-slot AC window and advance at most once."""
    validate_streaming_policy(policy)
    validate_solve_config(solve_config)
    if not outer.accepted_primal:
        raise ValueError("AC window requires an accepted frozen outer plan")
    _validate_outer_binding(
        outer,
        inputs=inputs,
        policy=policy,
        solve_config=solve_config,
    )
    if not 0 <= iteration < inputs.horizon_steps:
        raise ValueError("streaming iteration lies outside the horizon")
    if iteration == 0 and preceding_controlling_attempt is not None:
        raise ValueError("iteration zero cannot have a preceding controller")
    if iteration > 0:
        if preceding_controlling_attempt is None:
            raise ValueError("later windows require the preceding controller")
        _validate_preceding_controller(
            preceding_controlling_attempt,
            inputs=inputs,
            policy=policy,
            outer=outer,
            iteration=iteration,
        )
    initial = _validate_realized_state(
        realized_soc_mwh,
        inputs=inputs,
        policy=policy,
        iteration=iteration,
        preceding=preceding_controlling_attempt,
    )
    stop = min(iteration + policy.ac_window_steps, inputs.horizon_steps)
    target = outer.target_at(stop)
    ids = _storage_ids(inputs.storage)
    slots = _p0_registry(iteration)
    records: list[ACAttemptRecord] = []
    accepted: ACAttemptRecord | None = None

    causal_raw: Mapping[str, np.ndarray] | None = None
    causal_start: Mapping[str, np.ndarray] | None = None
    primary_build: OPFBuild | None = None
    causal_source_kind: Literal["generated_flat", "attempt"] = "generated_flat"
    causal_source_id: str | None = None
    if iteration > 0:
        if preceding_controlling_attempt is None:
            raise RuntimeError("validated preceding controller disappeared")
        preceding_values = (
            preceding_controlling_attempt.solution_values
            if isinstance(preceding_controlling_attempt, CausalControllerSource)
            else _solution_values(preceding_controlling_attempt)
        )
        if preceding_values is None:
            raise RuntimeError("validated preceding controller lacks solution values")
        try:
            if phase_observer is not None:
                phase_observer("before_ac_build", iteration, slots[0].ordinal)
            primary_build = build_window(
                inputs,
                "ac",
                iteration,
                stop,
                _inner_storage(inputs, initial, target),
            )
            if phase_observer is not None:
                phase_observer("after_ac_build", iteration, slots[0].ordinal)
            causal_raw, causal_start = shifted_start(
                preceding_values,
                primary_build,
                inputs,
                policy,
                initial,
            )
        except Exception as exc:
            reason = f"causal_start_construction_error:{type(exc).__name__}: {exc}"
            records.append(
                _empty_attempt(
                    inputs,
                    policy,
                    outer,
                    iteration,
                    stop,
                    initial,
                    target,
                    slots[0],
                    "construction_error",
                    reason,
                    source_kind="attempt",
                    source_attempt_id=preceding_controlling_attempt.attempt_id,
                    build=primary_build,
                    raw_start=causal_raw,
                    assigned_start=causal_start,
                )
            )
            records.extend(
                _empty_attempt(
                    inputs,
                    policy,
                    outer,
                    iteration,
                    stop,
                    initial,
                    target,
                    slot,
                    "source_unavailable",
                    reason,
                )
                for slot in slots[1:]
            )
            return StreamingWindowResult(iteration, stop, tuple(records), None, None)
        causal_source_kind = "attempt"
        causal_source_id = preceding_controlling_attempt.attempt_id

    primary = _execute_attempt(
        inputs,
        policy,
        solve_config,
        outer,
        iteration,
        stop,
        initial,
        target,
        slots[0],
        target_free=False,
        raw_start=causal_raw,
        assigned_start=causal_start,
        source_kind=causal_source_kind,
        source_attempt_id=causal_source_id,
        prebuilt=primary_build,
        phase_observer=phase_observer,
    )
    records.append(primary)
    if primary.supplied_executed_action:
        accepted = primary

    target_free: ACAttemptRecord | None = None
    if accepted is None:
        target_free = _execute_attempt(
            inputs,
            policy,
            solve_config,
            outer,
            iteration,
            stop,
            initial,
            target,
            slots[1],
            target_free=True,
            raw_start=causal_raw,
            assigned_start=causal_start,
            source_kind=causal_source_kind,
            source_attempt_id=causal_source_id,
            phase_observer=phase_observer,
        )
        records.append(target_free)
    else:
        records.append(
            _empty_attempt(
                inputs,
                policy,
                outer,
                iteration,
                stop,
                initial,
                target,
                slots[1],
                "not_needed_after_acceptance",
                "primary controlling attempt accepted",
            )
        )

    target_free_values = None if target_free is None else _solution_values(target_free)
    if accepted is None and target_free_values is not None and target_free is not None:
        copied = _execute_attempt(
            inputs,
            policy,
            solve_config,
            outer,
            iteration,
            stop,
            initial,
            target,
            slots[2],
            target_free=False,
            raw_start=target_free_values,
            assigned_start=target_free_values,
            source_kind="attempt",
            source_attempt_id=target_free.attempt_id,
            phase_observer=phase_observer,
        )
        records.append(copied)
        if copied.supplied_executed_action:
            accepted = copied
    elif accepted is not None:
        records.append(
            _empty_attempt(
                inputs,
                policy,
                outer,
                iteration,
                stop,
                initial,
                target,
                slots[2],
                "not_needed_after_acceptance",
                "earlier controlling attempt accepted",
            )
        )
    else:
        records.append(
            _empty_attempt(
                inputs,
                policy,
                outer,
                iteration,
                stop,
                initial,
                target,
                slots[2],
                "source_unavailable",
                "target-free solve was not accepted",
            )
        )

    causal_center = causal_start if causal_start is not None else primary.assigned_start
    for slot in slots[3:]:
        if accepted is not None:
            record = _empty_attempt(
                inputs,
                policy,
                outer,
                iteration,
                stop,
                initial,
                target,
                slot,
                "not_needed_after_acceptance",
                "earlier controlling attempt accepted",
            )
        else:
            target_free_role = slot.role == "perturbed_target_free"
            center = target_free_values if target_free_role else causal_center
            if center is None:
                record = _empty_attempt(
                    inputs,
                    policy,
                    outer,
                    iteration,
                    stop,
                    initial,
                    target,
                    slot,
                    "source_unavailable",
                    "perturbation center is unavailable",
                )
            else:
                source_id = (
                    target_free.attempt_id
                    if target_free_role and target_free is not None
                    else causal_source_id
                )
                source_kind: Literal["generated_flat", "attempt"] = (
                    "attempt" if source_id is not None else "generated_flat"
                )
                build = None
                try:
                    if slot.scale is None or slot.seed is None:
                        raise RuntimeError("perturbation slot lacks scale or seed")
                    if phase_observer is not None:
                        phase_observer("before_ac_build", iteration, slot.ordinal)
                    build = build_window(
                        inputs,
                        "ac",
                        iteration,
                        stop,
                        _inner_storage(inputs, initial, target),
                    )
                    if phase_observer is not None:
                        phase_observer("after_ac_build", iteration, slot.ordinal)
                    raw, projected = perturbed_start(
                        center, build, scale=slot.scale, seed=slot.seed
                    )
                except Exception as exc:
                    record = _empty_attempt(
                        inputs,
                        policy,
                        outer,
                        iteration,
                        stop,
                        initial,
                        target,
                        slot,
                        "construction_error",
                        f"perturbation_construction_error:{type(exc).__name__}: {exc}",
                        source_kind=source_kind,
                        source_attempt_id=source_id,
                        build=build,
                    )
                else:
                    record = _execute_attempt(
                        inputs,
                        policy,
                        solve_config,
                        outer,
                        iteration,
                        stop,
                        initial,
                        target,
                        slot,
                        target_free=False,
                        raw_start=raw,
                        assigned_start=projected,
                        source_kind=source_kind,
                        source_attempt_id=source_id,
                        phase_observer=phase_observer,
                    )
                    if record.supplied_executed_action:
                        accepted = record
        records.append(record)

    if len(records) != 9:
        raise RuntimeError("streaming attempt registry cardinality changed")
    post = None
    if accepted is not None:
        if accepted.result is None:
            raise RuntimeError("controlling attempt lacks an extracted result")
        first_soc = np.asarray(accepted.result["soc"], dtype=float).reshape(
            stop - iteration, len(ids)
        )[0]
        first_b = np.asarray(accepted.result["b"], dtype=float).reshape(
            stop - iteration, len(ids)
        )[0]
        expected = np.asarray([initial[device_id] for device_id in ids]) - (
            inputs.delta * first_b
        )
        residual = float(np.max(np.abs(first_soc - expected)))
        if residual > policy.tolerances.soc_recurrence_mwh_abs:
            raise RuntimeError("accepted controlling action violates SoC recurrence")
        post = dict(zip(ids, first_soc.tolist(), strict=True))
    return StreamingWindowResult(iteration, stop, tuple(records), accepted, post)


__all__ = [
    "StreamingOuterPlan",
    "StreamingWindowResult",
    "assign_start",
    "build_window",
    "complete_flat_start",
    "execute_streaming_window",
    "perturbed_start",
    "snapshot_inputs",
    "shifted_start",
    "solve_ac_with_verified_x0",
    "solve_frozen_outer",
    "validate_solve_config",
    "validate_streaming_policy",
    "variables_by_name",
]
