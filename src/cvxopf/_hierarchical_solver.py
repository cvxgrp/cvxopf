"""Private M17 hierarchical-controller orchestration.

This module executes the typed contract defined in :mod:`cvxopf.hierarchical`.
It intentionally composes the existing public OPF builders and result
extractor; it does not own device equations or network physics.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
import re
from time import perf_counter
from typing import Any, Literal, Mapping, cast
import warnings

import cvxpy as cp
import numpy as np
import pandas as pd
from cvxpy.reductions.solvers.nlp_solving_chain import _set_nlp_initial_point
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
from cvxpy.reductions.cvx_attr2constr import CvxAttr2Constr
from cvxpy.reductions.dnlp2smooth.dnlp2smooth import Dnlp2Smooth
from cvxpy.reductions.solvers.solving_chain import SolvingChain

from cvxopf.hierarchical import (
    ACCEPTED_SOLVER_STATUSES,
    ACAttemptRecord,
    AttemptOutcome,
    AttemptRole,
    AttemptSlotState,
    ExecutedIntervalRecord,
    HierarchicalInputs,
    HierarchicalPolicy,
    HierarchicalProvenance,
    HierarchicalResult,
    HierarchicalSolveAudit,
    HierarchicalSolveConfig,
    IPOPTStartEvidence,
    OuterPlanRecord,
    OuterTerminalMode,
)
from cvxopf.generator import (
    DispatchableGenerator,
    gen_cost_expr,
    generator_gencost,
)
from cvxopf.hvdc import HVDCLink
from cvxopf.load import Load
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.problem import OPFBuild, OPFOptions, build_opf_multistep
from cvxopf.results import extract_results
from cvxopf.storage import (
    StorageUnitIdeal, _validate_connection_window, storage_cost_expr,
)


_STEP_NAME = re.compile(r"^(?P<base>.+)_(?P<step>\d+)$")
_OUTER_REQUIRED_FIELDS = (
    "objective", "b", "soc", "Pg", "p_net", "p_flows",
    "p_load", "q_load", "p_load_served",
)
_AC_REQUIRED_FIELDS = (
    "objective", "b", "b_q", "soc", "Pg", "Qg", "Vm", "Va_deg",
    "p_net", "q_net", "branch_p_from", "branch_q_from", "branch_p_to",
    "branch_q_to", "branch_s_from", "branch_s_to",
    "p_load", "q_load", "p_load_served", "q_load_served",
)


def _required_fields(
    snapshot: _ExecutionInputs, *, ac: bool
) -> tuple[str, ...]:
    fields = list(_AC_REQUIRED_FIELDS if ac else _OUTER_REQUIRED_FIELDS)
    if snapshot.nondispatchable:
        fields.extend(("p_nd", "curtailment"))
        if ac:
            fields.append("q_nd")
    if snapshot.hvdc:
        fields.extend(("p_hvdc_in", "p_hvdc_out", "hvdc_loss"))
    return tuple(fields)


@dataclass(frozen=True)
class _ExecutionInputs:
    """Private deep snapshot used throughout one controller execution."""

    case: dict[str, object]
    horizon_steps: int
    delta: float
    generators: tuple[DispatchableGenerator, ...]
    loads: tuple[Load, ...]
    storage: tuple[StorageUnitIdeal, ...]
    nondispatchable: tuple[NondispatchableUnit, ...]
    hvdc: tuple[HVDCLink, ...]
    df_load_p: pd.DataFrame
    df_load_q: pd.DataFrame | None
    df_nd: pd.DataFrame | None
    df_hvdc_min: pd.DataFrame | None
    df_hvdc_max: pd.DataFrame | None
    options: OPFOptions
    storage_device_ids: tuple[str, ...]


@dataclass(frozen=True)
class _AttemptSlot:
    ordinal: int
    role: AttemptRole
    transformation: str
    scale: float | None
    seed: int | None


@dataclass(frozen=True)
class _X0Run:
    assigned_start: Mapping[str, np.ndarray]
    evidence: IPOPTStartEvidence | None
    exception: str | None
    elapsed_seconds: float


def _execution_snapshot(inputs: HierarchicalInputs) -> _ExecutionInputs:
    """Take the S4-promised private snapshot before any builder is called."""
    return _ExecutionInputs(
        case=deepcopy(dict(inputs.case)),
        horizon_steps=inputs.horizon_steps,
        delta=inputs.delta,
        generators=tuple(deepcopy(inputs.generators)),
        loads=tuple(deepcopy(inputs.loads)),
        storage=tuple(deepcopy(inputs.storage)),
        nondispatchable=tuple(deepcopy(inputs.nondispatchable)),
        hvdc=tuple(deepcopy(inputs.hvdc)),
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
        storage_device_ids=tuple(inputs.storage_device_ids),
    )


def _aligned(values: Mapping[str, float], ids: tuple[str, ...], label: str) -> np.ndarray:
    if set(values) != set(ids):
        raise ValueError(f"{label} storage identity does not match the controller fleet")
    return np.array([values[device_id] for device_id in ids], dtype=float)


def _initial_soc(snapshot: _ExecutionInputs) -> dict[str, float]:
    return {
        device_id: float(unit.initial_soc)
        for device_id, unit in zip(
            snapshot.storage_device_ids, snapshot.storage, strict=True
        )
    }


def _outer_storage(
    snapshot: _ExecutionInputs, realized_soc: Mapping[str, float]
) -> tuple[StorageUnitIdeal, ...]:
    initial = _aligned(realized_soc, snapshot.storage_device_ids, "realized SoC")
    return tuple(
        replace(unit, initial_soc=float(initial[index]))
        for index, unit in enumerate(snapshot.storage)
    )


def _inner_storage(
    snapshot: _ExecutionInputs,
    policy: HierarchicalPolicy,
    realized_soc: Mapping[str, float],
    target_soc: Mapping[str, float] | None,
) -> tuple[StorageUnitIdeal, ...]:
    initial = _aligned(realized_soc, snapshot.storage_device_ids, "realized SoC")
    target = (
        None
        if target_soc is None
        else _aligned(target_soc, snapshot.storage_device_ids, "terminal target")
    )
    units: list[StorageUnitIdeal] = []
    for index, unit in enumerate(snapshot.storage):
        terminal_soc = None if target is None else float(target[index])
        terminal_constraint = None
        terminal_cost = None
        terminal_weight = None
        if target is not None and policy.inner_terminal_policy == "hard_equality":
            terminal_constraint = "equality"
        elif target is not None:
            terminal_cost = "quadratic"
            terminal_weight = policy.quadratic_soft_weight
        units.append(replace(
            unit,
            initial_soc=float(initial[index]),
            terminal_soc=terminal_soc,
            terminal_constraint=terminal_constraint,
            terminal_cost=terminal_cost,
            terminal_weight=terminal_weight,
        ))
    return tuple(units)


def _localize_storage_windows(
    storage: tuple[StorageUnitIdeal, ...],
    start: int,
    stop: int,
    horizon_steps: int,
) -> tuple[StorageUnitIdeal, ...]:
    """Intersect global connection windows with one local solve window."""
    localized = []
    for index, unit in enumerate(storage):
        _validate_connection_window(unit, index, horizon_steps=horizon_steps)
        if unit.connection_window is None:
            localized.append(unit)
            continue
        arrival, departure = unit.connection_window
        overlap_start = max(arrival, start)
        overlap_stop = min(departure, stop)
        window = (
            (overlap_start - start, overlap_stop - start)
            if overlap_start < overlap_stop else (0, 0)
        )
        localized.append(replace(unit, connection_window=window))
    return tuple(localized)


def _build_window(
    snapshot: _ExecutionInputs,
    formulation: Literal["ac", "lossy_dc"],
    start: int,
    stop: int,
    storage: tuple[StorageUnitIdeal, ...],
) -> OPFBuild:
    if not 0 <= start < stop <= snapshot.horizon_steps:
        raise ValueError(f"invalid half-open interval [{start}, {stop})")
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message=".*reactive load input metadata.*", category=UserWarning
        )
        warnings.filterwarnings(
            "ignore", message="Storage apparent_power_rating is applied.*",
            category=UserWarning,
        )
        return build_opf_multistep(
            deepcopy(snapshot.case),
            T=stop - start,
            formulation=formulation,
            options=deepcopy(snapshot.options),
            generators=list(deepcopy(snapshot.generators)),
            loads=list(deepcopy(snapshot.loads)),
            df_load_p=snapshot.df_load_p.iloc[start:stop].copy(),
            df_load_q=(
                None
                if snapshot.df_load_q is None
                else snapshot.df_load_q.iloc[start:stop].copy()
            ),
            nondispatchable=list(deepcopy(snapshot.nondispatchable)),
            df_nd=(
                None
                if snapshot.df_nd is None
                else snapshot.df_nd.iloc[start:stop].copy()
            ),
            storage=list(_localize_storage_windows(
                storage, start, stop, snapshot.horizon_steps
            )),
            hvdc=list(deepcopy(snapshot.hvdc)),
            df_hvdc_min=(
                None
                if snapshot.df_hvdc_min is None
                else snapshot.df_hvdc_min.iloc[start:stop].copy()
            ),
            df_hvdc_max=(
                None
                if snapshot.df_hvdc_max is None
                else snapshot.df_hvdc_max.iloc[start:stop].copy()
            ),
            delta=snapshot.delta,
        )


def _as_2d(value: object) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    return array.reshape(1, -1) if array.ndim == 1 else array


def _finite_fields(result: Mapping[str, object], fields: tuple[str, ...]) -> tuple[str, ...]:
    missing: list[str] = []
    for name in fields:
        value = result.get(name)
        try:
            finite = value is not None and np.isfinite(
                np.asarray(value, dtype=float)
            ).all()
        except (TypeError, ValueError):
            finite = False
        if not finite:
            missing.append(name)
    return tuple(missing)


def _identity_error(
    snapshot: _ExecutionInputs, build: OPFBuild, result: Mapping[str, object]
) -> str | None:
    expected = snapshot.storage_device_ids
    build_ids = tuple(
        str(value) for value in cast(Any, build.data["storage_device_ids"])
    )
    result_ids = tuple(
        str(value) for value in cast(Any, result["storage_device_ids"])
    )
    explicit = np.asarray(result["storage_device_id_is_explicit"], dtype=bool)
    if not explicit.all():
        return "one or more storage IDs are build-local rather than explicit"
    if build_ids != expected or result_ids != expected:
        return "storage identity or ordering differs across hierarchy layers"
    return None


def _variables_by_name(build: OPFBuild) -> dict[str, cp.Variable]:
    variables = build.prob.variables()
    names = [variable.name() for variable in variables]
    if len(names) != len(set(names)):
        raise ValueError("hierarchical initialization requires unique variable names")
    return dict(zip(names, variables, strict=True))


def _complete_start(build: OPFBuild) -> dict[str, np.ndarray]:
    _set_nlp_initial_point(build.prob)
    result: dict[str, np.ndarray] = {}
    for name, variable in _variables_by_name(build).items():
        if variable.value is None:
            raise RuntimeError(f"CVXPY did not initialize {name}")
        result[name] = np.asarray(variable.value, dtype=float).copy()
    return result


def _assign_start(build: OPFBuild, values: Mapping[str, np.ndarray]) -> None:
    variables = _variables_by_name(build)
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
    normalized = []
    auxiliary_index = 0
    for item in layout:
        original = bool(item["is_original_variable"])
        label = str(item["name"]) if original else f"auxiliary_{auxiliary_index}"
        auxiliary_index += int(not original)
        normalized.append({
            "label": label,
            "shape": item["shape"],
            "start": item["start"],
            "stop": item["stop"],
            "is_original_variable": original,
        })
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


def _solve_ac_with_verified_x0(
    build: OPFBuild, solve_config: HierarchicalSolveConfig
) -> _X0Run:
    """Solve through a build-local IPOPT instance and retain its exact x0."""
    assigned = _complete_start(build)
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
        expected = np.concatenate([
            np.asarray(variable.value, dtype=float).flatten(order="F")
            for variable in reduced_variables
        ])
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
                originals_match &= np.array_equal(
                    captured_x0[offset:stop], model_value
                )
            layout.append({
                "name": variable.name(),
                "shape": tuple(variable.shape),
                "start": offset,
                "stop": stop,
                "is_original_variable": is_original,
            })
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
        # CVXPY's runtime class is intentionally untyped in its public stubs.
        # Dynamic construction keeps that third-party boundary local without
        # weakening strict checking for the surrounding orchestration module.
        solver_type = type(
            "_BuildLocalCapturingIPOPT",
            (IPOPT,),
            {"solve_via_data": capturing_solve_via_data},
        )
        solver = solver_type()
        chain = SolvingChain(reductions=[
            CvxAttr2Constr(reduce_bounds=not solver.BOUNDED_VARIABLES),
            Dnlp2Smooth(),
            solver,
        ])
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


def _device_injections(
    snapshot: _ExecutionInputs,
    result: Mapping[str, object],
    *,
    reactive: bool,
) -> tuple[np.ndarray, np.ndarray | None]:
    bus_ids = [int(value) for value in np.asarray(snapshot.case["bus"])[:, 0]]
    bus_index = {bus_id: index for index, bus_id in enumerate(bus_ids)}
    p = np.zeros((_as_2d(result["Pg"]).shape[0], len(bus_ids)))
    q = np.zeros_like(p) if reactive else None
    for column, unit in enumerate(snapshot.generators):
        p[:, bus_index[unit.bus]] += _as_2d(result["Pg"])[:, column]
        if reactive and q is not None:
            q[:, bus_index[unit.bus]] += _as_2d(result["Qg"])[:, column]
    for column, unit in enumerate(snapshot.storage):
        p[:, bus_index[unit.bus]] += _as_2d(result["b"])[:, column]
        if reactive and q is not None:
            q[:, bus_index[unit.bus]] += _as_2d(result["b_q"])[:, column]
    for column, unit in enumerate(snapshot.nondispatchable):
        p[:, bus_index[unit.bus]] += _as_2d(result["p_nd"])[:, column]
        if reactive and q is not None:
            q[:, bus_index[unit.bus]] += _as_2d(result["q_nd"])[:, column]
    for column, unit in enumerate(snapshot.loads):
        p[:, bus_index[unit.bus]] -= _as_2d(result["p_load_served"])[:, column]
        if reactive and q is not None:
            q[:, bus_index[unit.bus]] -= _as_2d(
                result["q_load_served"]
            )[:, column]
    for column, link in enumerate(snapshot.hvdc):
        p[:, bus_index[link.from_bus]] += _as_2d(result["p_hvdc_in"])[:, column]
        p[:, bus_index[link.to_bus]] += _as_2d(result["p_hvdc_out"])[:, column]
    return p, q


def _soc_residual(
    snapshot: _ExecutionInputs, build: OPFBuild, result: Mapping[str, object]
) -> float:
    soc = _as_2d(result["soc"])
    b = _as_2d(result["b"])
    initial = np.asarray(build.data["storage_initial_soc"], dtype=float)
    previous = np.vstack([initial, soc[:-1]])
    return float(np.max(np.abs(soc - previous + snapshot.delta * b)))


def _terminal_deviation(
    snapshot: _ExecutionInputs,
    result: Mapping[str, object],
    target: Mapping[str, float] | None,
) -> tuple[dict[str, float] | None, float | None]:
    if target is None:
        return None, None
    values = _as_2d(result["soc"])[-1] - _aligned(
        target, snapshot.storage_device_ids, "terminal target"
    )
    return (
        {
            device_id: float(values[index])
            for index, device_id in enumerate(snapshot.storage_device_ids)
        },
        float(np.max(np.abs(values))),
    )


def _network_limit_residuals(
    snapshot: _ExecutionInputs,
    result: Mapping[str, object],
    *,
    first_only: bool = False,
) -> tuple[float, float, float]:
    vm = _as_2d(result["Vm"])
    s_from = _as_2d(result["branch_s_from"])
    s_to = _as_2d(result["branch_s_to"])
    if first_only:
        vm, s_from, s_to = vm[:1], s_from[:1], s_to[:1]
    bus = np.asarray(snapshot.case["bus"], dtype=float)
    voltage = float(np.max(np.maximum.reduce([
        vm - bus[:, 11], bus[:, 12] - vm, np.zeros_like(vm)
    ])))
    branch = np.asarray(snapshot.case["branch"], dtype=float)
    constrained = (
        (branch[:, 10] == 1)
        & np.isfinite(branch[:, 5])
        & (branch[:, 5] > 0)
    )
    if not np.any(constrained):
        return voltage, 0.0, 0.0
    ratings = branch[constrained, 5]
    apparent = np.concatenate(
        [s_from[:, constrained], s_to[:, constrained]], axis=1
    )
    both = np.concatenate([ratings, ratings])
    thermal = float(np.max(np.maximum(apparent - both, 0.0)))
    normalized = float(np.max(np.maximum(
        (apparent**2 - both**2) / both**2, 0.0
    )))
    return voltage, thermal, normalized


def _dc_incidence(snapshot: _ExecutionInputs) -> np.ndarray:
    bus = np.asarray(snapshot.case["bus"], dtype=float)
    branch = np.asarray(snapshot.case["branch"], dtype=float)
    bus_index = {int(value): index for index, value in enumerate(bus[:, 0])}
    incidence = np.zeros((len(bus), len(branch)))
    for row, values in enumerate(branch):
        incidence[bus_index[int(values[0])], row] = -1.0
        incidence[bus_index[int(values[1])], row] = 1.0
    return incidence


def _outer_residuals(
    snapshot: _ExecutionInputs,
    build: OPFBuild,
    result: Mapping[str, object],
    target: Mapping[str, float] | None,
) -> dict[str, float]:
    p_device, _ = _device_injections(snapshot, result, reactive=False)
    p_net = _as_2d(result["p_net"])
    p_flows = _as_2d(result["p_flows"])
    base_mva = float(
        np.asarray(snapshot.case["baseMVA"], dtype=float).item()
    )
    residuals = {
        "soc_recurrence_mwh_abs": _soc_residual(snapshot, build, result),
        "dc_injection_reporting_mw_abs": float(np.max(np.abs(p_device - p_net))),
        "dc_nodal_balance_pu_abs": float(np.max(np.abs(
            (p_flows @ _dc_incidence(snapshot).T + p_net)
            / base_mva
        ))),
    }
    if target is not None:
        terminal_soc = _as_2d(result["soc"])[-1]
        hard_residuals: list[float] = []
        expected_soft_cost = 0.0
        for index, (device_id, unit) in enumerate(zip(
            snapshot.storage_device_ids, snapshot.storage, strict=True
        )):
            if unit.terminal_soc is None:
                continue
            if device_id not in target:
                raise RuntimeError(
                    f"outer target is missing storage device {device_id!r}"
                )
            deviation = float(terminal_soc[index] - target[device_id])
            if unit.terminal_constraint == "equality":
                hard_residuals.append(abs(deviation))
            elif unit.terminal_constraint == "shortfall":
                hard_residuals.append(max(-deviation, 0.0))
            if unit.terminal_cost is not None:
                if unit.terminal_weight is None:
                    raise RuntimeError("outer terminal cost lacks a weight")
                magnitude = (
                    max(-deviation, 0.0)
                    if unit.terminal_cost.startswith("shortfall_")
                    else abs(deviation)
                )
                expected_soft_cost += unit.terminal_weight * (
                    magnitude**2
                    if unit.terminal_cost.endswith("quadratic")
                    else magnitude
                )
        if hard_residuals:
            residuals["terminal_soc_mwh_abs"] = max(hard_residuals)
        if any(unit.terminal_cost is not None for unit in snapshot.storage):
            reported = float(cast(Any, result["storage_terminal_cost"]))
            residuals["soft_terminal_cost_abs"] = abs(
                reported - expected_soft_cost
            )
    return residuals


def _ac_residuals(
    snapshot: _ExecutionInputs,
    policy: HierarchicalPolicy,
    build: OPFBuild,
    result: Mapping[str, object],
    target: Mapping[str, float] | None,
) -> tuple[dict[str, float], dict[str, float] | None]:
    p_device, q_device = _device_injections(snapshot, result, reactive=True)
    if q_device is None:
        raise RuntimeError("AC diagnostics require reactive injections")
    base_mva = float(cast(Any, snapshot.case["baseMVA"]))
    voltage, thermal, normalized = _network_limit_residuals(snapshot, result)
    branch_loss_by_step = np.sum(
        _as_2d(result["branch_p_from"])
        + _as_2d(result["branch_p_to"]),
        axis=1,
    )
    residuals = {
        "soc_recurrence_mwh_abs": _soc_residual(snapshot, build, result),
        "ac_active_balance_pu_abs": float(np.max(np.abs(
            (p_device - _as_2d(result["p_net"])) / base_mva
        ))),
        "ac_reactive_balance_pu_abs": float(np.max(np.abs(
            (q_device - _as_2d(result["q_net"])) / base_mva
        ))),
        "voltage_bound_pu_abs": voltage,
        "branch_mva_abs": thermal,
        "branch_normalized_squared_residual": normalized,
        "curtailment_nonnegativity_pu_abs": (
            float(np.max(np.maximum(-_as_2d(result["curtailment"]), 0.0)))
            / base_mva
            if snapshot.nondispatchable
            else 0.0
        ),
        "branch_loss_nonnegativity_pu_abs": float(
            np.max(np.maximum(-branch_loss_by_step, 0.0)) / base_mva
        ),
    }
    deviations, terminal = _terminal_deviation(snapshot, result, target)
    if target is not None and policy.inner_terminal_policy == "hard_equality":
        if terminal is None:
            raise RuntimeError("hard terminal residual is unavailable")
        residuals["terminal_soc_mwh_abs"] = terminal
    elif target is not None:
        if policy.quadratic_soft_weight is None or deviations is None:
            raise RuntimeError("quadratic terminal policy data are unavailable")
        weight = policy.quadratic_soft_weight
        expected = weight * sum(value**2 for value in deviations.values())
        residuals["soft_terminal_cost_abs"] = abs(
            float(cast(Any, result["storage_terminal_cost"])) - expected
        )
    return residuals, deviations


def _audit(
    build: OPFBuild,
    result: Mapping[str, object],
    exception: str | None,
    elapsed: float,
    required_fields: tuple[str, ...],
    residuals: Mapping[str, float],
    identity_error: str | None,
    tolerances: Mapping[str, float],
) -> HierarchicalSolveAudit:
    status_value = result.get("status")
    status = None if status_value is None else str(status_value)
    missing = _finite_fields(result, required_fields)
    accepted = (
        exception is None
        and status in ACCEPTED_SOLVER_STATUSES
        and not missing
        and identity_error is None
        and all(
            np.isfinite(value) and value <= tolerances[name]
            for name, value in residuals.items()
        )
    )
    if exception is not None:
        outcome: AttemptOutcome = "solver_failure"
    elif status in {"infeasible", "infeasible_inaccurate"}:
        outcome = "solver_certified_infeasible"
    elif accepted:
        outcome = "accepted"
    else:
        outcome = "unusable_primal"
    stats = build.prob.solver_stats
    return HierarchicalSolveAudit(
        status=status,
        outcome=outcome,
        accepted_primal=accepted,
        missing_or_nonfinite_fields=missing,
        identity_error=identity_error,
        residuals=residuals,
        exception=exception,
        wall_time_seconds=elapsed,
        solver_num_iters=getattr(stats, "num_iters", None),
        solver_setup_time_seconds=getattr(stats, "setup_time", None),
        solver_solve_time_seconds=getattr(stats, "solve_time", None),
    )


def _tolerance_mapping(policy: HierarchicalPolicy) -> dict[str, float]:
    values = {
        name: float(getattr(policy.tolerances, name))
        for name in policy.tolerances.__dataclass_fields__
    }
    values["curtailment_nonnegativity_pu_abs"] = (
        policy.tolerances.ac_active_balance_pu_abs
    )
    values["branch_loss_nonnegativity_pu_abs"] = (
        policy.tolerances.ac_active_balance_pu_abs
    )
    return values


def _solve_outer(
    snapshot: _ExecutionInputs,
    policy: HierarchicalPolicy,
    solve_config: HierarchicalSolveConfig,
    iteration: int,
    realized_soc: Mapping[str, float],
) -> OuterPlanRecord:
    storage = _outer_storage(snapshot, realized_soc)
    build = _build_window(
        snapshot, "lossy_dc", iteration, snapshot.horizon_steps, storage
    )
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
    target = {
        str(unit.device_id): float(unit.terminal_soc)
        for unit in storage
        if unit.device_id is not None and unit.terminal_soc is not None
    }
    required_fields = _required_fields(snapshot, ac=False)
    if any(unit.terminal_cost is not None for unit in storage):
        required_fields += ("storage_terminal_cost",)
    missing = _finite_fields(result, required_fields)
    residuals = (
        {}
        if missing
        else _outer_residuals(snapshot, build, result, target or None)
    )
    audit = _audit(
        build, result, exception, elapsed, required_fields, residuals,
        _identity_error(snapshot, build, result), _tolerance_mapping(policy),
    )
    boundaries = None
    if audit.accepted_primal:
        boundaries = np.vstack([
            np.asarray(build.data["storage_initial_soc"], dtype=float),
            _as_2d(result["soc"]),
        ])
    local = np.arange(snapshot.horizon_steps - iteration + 1)
    modes: dict[str, OuterTerminalMode] = {
        str(unit.device_id): (
            cast(
                OuterTerminalMode,
                unit.terminal_constraint or unit.terminal_cost or "none",
            )
        )
        for unit in storage
    }
    return OuterPlanRecord(
        outer_plan_id=f"outer-{iteration:03d}",
        created_iteration=iteration,
        global_interval_start=iteration,
        global_interval_stop=snapshot.horizon_steps,
        local_boundary_indices=local,
        global_boundary_indices=iteration + local,
        storage_device_ids=snapshot.storage_device_ids,
        terminal_modes=modes,
        boundary_soc_mwh=boundaries,
        build=build,
        result=result,
        audit=audit,
    )


def _registry(iteration: int, policy: HierarchicalPolicy) -> tuple[_AttemptSlot, ...]:
    if policy.initialization_policy == "flat_only":
        return (_AttemptSlot(0, "primary_controlling", "flat", None, None),)
    recovery = policy.recovery
    if recovery is None:
        raise RuntimeError("shifted recovery configuration is unavailable")
    causal = "flat" if iteration == 0 else "shifted_preceding"
    slots = [
        _AttemptSlot(0, "primary_controlling", causal, None, None),
        _AttemptSlot(1, "target_free", causal, None, None),
        _AttemptSlot(2, "copied_target_free", "copy_target_free", None, None),
    ]
    ordinal = 3
    for source_code, role, transformation in cast(
        tuple[tuple[int, AttemptRole, str], ...],
        (
        (1, "perturbed_target_free", "perturb_target_free"),
        (2, "perturbed_causal", "perturb_causal"),
        ),
    ):
        for scale_index, scale in enumerate(
            recovery.perturbation_scales, start=1
        ):
            slots.append(_AttemptSlot(
                ordinal,
                role,
                transformation,
                scale,
                recovery.seed_base
                + 100 * iteration
                + 10 * source_code
                + scale_index,
            ))
            ordinal += 1
    return tuple(slots)


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


def _shifted_start(
    preceding: Mapping[str, np.ndarray],
    destination: OPFBuild,
    snapshot: _ExecutionInputs,
    policy: HierarchicalPolicy,
    realized_soc: Mapping[str, float],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    variables = _variables_by_name(destination)
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
    state = _aligned(realized_soc, snapshot.storage_device_ids, "realized SoC")
    for step in soc_steps:
        b_name = f"b_{step}"
        if b_name not in projected:
            raise ValueError(f"shifted start lacks {b_name}")
        state = state - snapshot.delta * projected[b_name]
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


def _perturbed_start(
    center: Mapping[str, np.ndarray],
    destination: OPFBuild,
    *,
    scale: float,
    seed: int,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    variables = _variables_by_name(destination)
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


def _solution_values(build: OPFBuild) -> dict[str, np.ndarray]:
    return {
        name: np.asarray(variable.value, dtype=float).copy()
        for name, variable in _variables_by_name(build).items()
    }


def _attempt_id(iteration: int, slot: _AttemptSlot) -> str:
    return f"ac-{iteration:03d}-{slot.ordinal:02d}-{slot.role}"


def _empty_attempt(
    snapshot: _ExecutionInputs,
    policy: HierarchicalPolicy,
    iteration: int,
    stop: int,
    outer_plan: OuterPlanRecord,
    initial: Mapping[str, float],
    target: Mapping[str, float],
    slot: _AttemptSlot,
    state: AttemptSlotState,
    reason: str,
    *,
    source_attempt_id: str | None = None,
    source_kind: Literal["generated_flat", "attempt"] | None = None,
    build: OPFBuild | None = None,
    raw_start: Mapping[str, np.ndarray] | None = None,
    assigned_start: Mapping[str, np.ndarray] | None = None,
) -> ACAttemptRecord:
    return ACAttemptRecord(
        attempt_id=_attempt_id(iteration, slot),
        slot_state=state,
        role=slot.role,
        transformation=slot.transformation,
        ordinal=slot.ordinal,
        iteration=iteration,
        local_interval_start=0,
        local_interval_stop=stop - iteration,
        global_interval_start=iteration,
        global_interval_stop=stop,
        outer_plan_id=outer_plan.outer_plan_id,
        source_kind=source_kind,
        source_attempt_id=source_attempt_id,
        inner_terminal_policy=policy.inner_terminal_policy,
        storage_device_ids=snapshot.storage_device_ids,
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


def _execute_attempt(
    snapshot: _ExecutionInputs,
    policy: HierarchicalPolicy,
    solve_config: HierarchicalSolveConfig,
    iteration: int,
    stop: int,
    outer_plan: OuterPlanRecord,
    initial: Mapping[str, float],
    target: Mapping[str, float],
    slot: _AttemptSlot,
    *,
    target_free: bool,
    raw_start: Mapping[str, np.ndarray] | None,
    assigned_start: Mapping[str, np.ndarray] | None,
    source_kind: Literal["generated_flat", "attempt"],
    source_attempt_id: str | None,
    prebuilt: OPFBuild | None = None,
) -> ACAttemptRecord:
    build = prebuilt
    retained_assigned: Mapping[str, np.ndarray] | None = None
    try:
        storage = _inner_storage(
            snapshot, policy, initial, None if target_free else target
        )
        if build is None:
            build = _build_window(snapshot, "ac", iteration, stop, storage)
        if assigned_start is not None:
            _assign_start(build, assigned_start)
            retained_assigned = assigned_start
        x0_run = _solve_ac_with_verified_x0(build, solve_config)
    except Exception as exc:
        return _empty_attempt(
            snapshot, policy, iteration, stop, outer_plan, initial, target,
            slot, "construction_error",
            f"ac_construction_error:{type(exc).__name__}: {exc}",
            source_attempt_id=source_attempt_id, source_kind=source_kind,
            build=build, raw_start=raw_start,
            assigned_start=retained_assigned,
        )
    result = extract_results(build)
    fields = _required_fields(snapshot, ac=True) + (
        ("storage_terminal_cost",)
        if not target_free and policy.inner_terminal_policy == "quadratic_soft"
        else ()
    )
    missing = _finite_fields(result, fields)
    deviations = None
    residuals: dict[str, float] = {}
    if not missing:
        residuals, deviations = _ac_residuals(
            snapshot, policy, build, result, None if target_free else target
        )
    audit = _audit(
        build, result, x0_run.exception, x0_run.elapsed_seconds, fields,
        residuals, _identity_error(snapshot, build, result),
        _tolerance_mapping(policy),
    )
    raw = x0_run.assigned_start if raw_start is None else raw_start
    assigned = x0_run.assigned_start
    if x0_run.evidence is None:
        return _empty_attempt(
            snapshot, policy, iteration, stop, outer_plan, initial, target,
            slot, "construction_error", "IPOPT x0 was not captured",
            source_attempt_id=source_attempt_id, source_kind=source_kind,
            build=build, raw_start=raw,
            assigned_start=assigned,
        )
    return ACAttemptRecord(
        attempt_id=_attempt_id(iteration, slot),
        slot_state="executed",
        role=slot.role,
        transformation=slot.transformation,
        ordinal=slot.ordinal,
        iteration=iteration,
        local_interval_start=0,
        local_interval_stop=stop - iteration,
        global_interval_start=iteration,
        global_interval_stop=stop,
        outer_plan_id=outer_plan.outer_plan_id,
        source_kind=source_kind,
        source_attempt_id=source_attempt_id,
        inner_terminal_policy=policy.inner_terminal_policy,
        storage_device_ids=snapshot.storage_device_ids,
        initial_soc_mwh=initial,
        target_soc_mwh=target,
        terminal_deviation_mwh=deviations,
        build=build,
        raw_start=raw,
        assigned_start=assigned,
        solver_evidence=x0_run.evidence,
        result=result,
        audit=audit,
        reason=None,
        supplied_executed_action=(audit.accepted_primal and not target_free),
        scale=slot.scale,
        seed=slot.seed,
    )


def _attempt_solution(attempt: ACAttemptRecord) -> dict[str, np.ndarray] | None:
    if (
        attempt.slot_state != "executed"
        or attempt.audit is None
        or not attempt.audit.accepted_primal
        or attempt.build is None
    ):
        return None
    return _solution_values(attempt.build)


def _window_attempts(
    snapshot: _ExecutionInputs,
    policy: HierarchicalPolicy,
    solve_config: HierarchicalSolveConfig,
    iteration: int,
    stop: int,
    outer_plan: OuterPlanRecord,
    initial: Mapping[str, float],
    target: Mapping[str, float],
    preceding_attempt: ACAttemptRecord | None,
) -> tuple[tuple[ACAttemptRecord, ...], ACAttemptRecord | None]:
    slots = _registry(iteration, policy)
    records: list[ACAttemptRecord] = []
    accepted: ACAttemptRecord | None = None

    if policy.initialization_policy == "flat_only":
        primary = _execute_attempt(
            snapshot, policy, solve_config, iteration, stop, outer_plan,
            initial, target, slots[0], target_free=False, raw_start=None,
            assigned_start=None, source_kind="generated_flat",
            source_attempt_id=None,
        )
        return (primary,), primary if primary.supplied_executed_action else None

    causal_raw: Mapping[str, np.ndarray] | None = None
    causal_start: Mapping[str, np.ndarray] | None = None
    primary_build: OPFBuild | None = None
    causal_source_kind: Literal["generated_flat", "attempt"] = "generated_flat"
    causal_source_id: str | None = None
    if iteration > 0:
        if preceding_attempt is None:
            reason = "preceding accepted controlling prediction is unavailable"
            return (
                tuple(
                    _empty_attempt(
                        snapshot, policy, iteration, stop, outer_plan, initial,
                        target, slot, "source_unavailable", reason
                    )
                    for slot in slots
                ),
                None,
            )
        preceding_values = _attempt_solution(preceding_attempt)
        if preceding_values is None:
            reason = "preceding accepted controlling prediction is unavailable"
            return (
                tuple(
                    _empty_attempt(
                        snapshot, policy, iteration, stop, outer_plan, initial,
                        target, slot, "source_unavailable", reason
                    )
                    for slot in slots
                ),
                None,
            )
        try:
            primary_build = _build_window(
                snapshot, "ac", iteration, stop,
                _inner_storage(snapshot, policy, initial, target),
            )
            causal_raw, causal_start = _shifted_start(
                preceding_values, primary_build, snapshot, policy, initial
            )
        except Exception as exc:
            reason = f"causal_start_construction_error:{type(exc).__name__}: {exc}"
            records.append(_empty_attempt(
                snapshot, policy, iteration, stop, outer_plan, initial, target,
                slots[0], "construction_error", reason,
                source_attempt_id=preceding_attempt.attempt_id,
                source_kind="attempt",
            ))
            records.extend(
                _empty_attempt(
                    snapshot, policy, iteration, stop, outer_plan, initial,
                    target, slot, "source_unavailable", reason
                )
                for slot in slots[1:]
            )
            return tuple(records), None
        causal_source_kind = "attempt"
        causal_source_id = preceding_attempt.attempt_id

    primary = _execute_attempt(
        snapshot, policy, solve_config, iteration, stop, outer_plan, initial,
        target, slots[0], target_free=False, raw_start=causal_raw,
        assigned_start=causal_start, source_kind=causal_source_kind,
        source_attempt_id=causal_source_id,
        prebuilt=primary_build,
    )
    records.append(primary)
    if primary.supplied_executed_action:
        accepted = primary

    target_free: ACAttemptRecord | None = None
    if accepted is None:
        target_free = _execute_attempt(
            snapshot, policy, solve_config, iteration, stop, outer_plan,
            initial, target, slots[1], target_free=True,
            raw_start=causal_raw, assigned_start=causal_start,
            source_kind=causal_source_kind,
            source_attempt_id=causal_source_id,
        )
        records.append(target_free)
    else:
        records.append(_empty_attempt(
            snapshot, policy, iteration, stop, outer_plan, initial, target,
            slots[1], "not_needed_after_acceptance",
            "primary controlling attempt accepted",
        ))

    target_free_values = (
        None if target_free is None else _attempt_solution(target_free)
    )
    if accepted is None and target_free_values is not None:
        if target_free is None:
            raise RuntimeError("target-free values lack their source record")
        copied = _execute_attempt(
            snapshot, policy, solve_config, iteration, stop, outer_plan,
            initial, target, slots[2], target_free=False,
            raw_start=target_free_values, assigned_start=target_free_values,
            source_kind="attempt", source_attempt_id=target_free.attempt_id,
        )
        records.append(copied)
        if copied.supplied_executed_action:
            accepted = copied
    elif accepted is not None:
        records.append(_empty_attempt(
            snapshot, policy, iteration, stop, outer_plan, initial, target,
            slots[2], "not_needed_after_acceptance",
            "earlier controlling attempt accepted",
        ))
    else:
        records.append(_empty_attempt(
            snapshot, policy, iteration, stop, outer_plan, initial, target,
            slots[2], "source_unavailable", "target-free solve was not accepted",
        ))

    causal_center = causal_start
    if causal_center is None:
        causal_center = primary.assigned_start
    for slot in slots[3:]:
        if accepted is not None:
            record = _empty_attempt(
                snapshot, policy, iteration, stop, outer_plan, initial, target,
                slot, "not_needed_after_acceptance",
                "earlier controlling attempt accepted",
            )
        else:
            is_target_free = slot.role == "perturbed_target_free"
            center = target_free_values if is_target_free else causal_center
            if center is None:
                record = _empty_attempt(
                    snapshot, policy, iteration, stop, outer_plan, initial,
                    target, slot, "source_unavailable",
                    "perturbation center is unavailable",
                )
            else:
                source_id = (
                    target_free.attempt_id
                    if is_target_free and target_free is not None
                    else causal_source_id
                )
                source_kind: Literal["generated_flat", "attempt"] = (
                    "attempt" if source_id is not None else "generated_flat"
                )
                build = None
                try:
                    if slot.scale is None or slot.seed is None:
                        raise RuntimeError("perturbation slot lacks scale or seed")
                    build = _build_window(
                        snapshot, "ac", iteration, stop,
                        _inner_storage(snapshot, policy, initial, target),
                    )
                    raw, projected = _perturbed_start(
                        center, build, scale=slot.scale, seed=slot.seed
                    )
                except Exception as exc:
                    record = _empty_attempt(
                        snapshot, policy, iteration, stop, outer_plan, initial,
                        target, slot, "construction_error",
                        f"perturbation_construction_error:{type(exc).__name__}: {exc}",
                        source_attempt_id=source_id,
                        source_kind=source_kind,
                        build=build,
                    )
                else:
                    record = _execute_attempt(
                        snapshot, policy, solve_config, iteration, stop,
                        outer_plan, initial, target, slot, target_free=False,
                        raw_start=raw, assigned_start=projected,
                        source_kind=source_kind, source_attempt_id=source_id,
                    )
                    if record.supplied_executed_action:
                        accepted = record
        records.append(record)
    if len(records) != len(slots):
        raise RuntimeError("hierarchical attempt registry cardinality changed")
    return tuple(records), accepted


def _outer_target(
    outer_plan: OuterPlanRecord, global_boundary: int
) -> dict[str, float]:
    if outer_plan.boundary_soc_mwh is None:
        raise ValueError("accepted outer plan has no state signposts")
    local = global_boundary - outer_plan.global_interval_start
    values = outer_plan.boundary_soc_mwh[local]
    return dict(zip(outer_plan.storage_device_ids, values, strict=True))


def _executed_record(
    snapshot: _ExecutionInputs,
    attempt: ACAttemptRecord,
    policy: HierarchicalPolicy,
) -> ExecutedIntervalRecord:
    if attempt.result is None:
        raise RuntimeError("executed attempt has no result")
    result = attempt.result
    pg = _as_2d(result["Pg"])[0]
    generation_expression = gen_cost_expr(
        generator_gencost(list(snapshot.generators)), cp.Constant(pg)
    )
    generation_rate = float(cast(Any, generation_expression.value))
    b = _as_2d(result["b"])[0]
    storage_expression = storage_cost_expr(
        list(snapshot.storage), cp.Constant(b)
    )
    storage_rate = float(cast(Any, storage_expression.value))
    curtailment = 0.0
    if snapshot.nondispatchable:
        curtailment_values = _as_2d(result["curtailment"])[0]
        tolerance_mw = (
            float(cast(Any, snapshot.case["baseMVA"]))
            * policy.tolerances.ac_active_balance_pu_abs
        )
        minimum = float(np.min(curtailment_values))
        if minimum < -tolerance_mw:
            raise RuntimeError(
                "accepted AC action violates nondispatchable curtailment "
                f"nonnegativity by {-minimum:.6g} MW"
            )
        curtailment = float(np.sum(np.maximum(curtailment_values, 0.0)))
    raw_branch_loss = float(
        np.sum(_as_2d(result["branch_p_from"])[0])
        + np.sum(_as_2d(result["branch_p_to"])[0])
    )
    tolerance_mw = (
        float(cast(Any, snapshot.case["baseMVA"]))
        * policy.tolerances.ac_active_balance_pu_abs
    )
    if raw_branch_loss < -tolerance_mw:
        raise RuntimeError(
            "accepted AC action violates branch-loss nonnegativity by "
            f"{-raw_branch_loss:.6g} MW"
        )
    branch_loss = max(raw_branch_loss, 0.0)
    injection_loss = float(np.sum(_as_2d(result["p_net"])[0]))
    initial = _aligned(
        attempt.initial_soc_mwh, snapshot.storage_device_ids, "attempt initial SoC"
    )
    first_soc = _as_2d(result["soc"])[0]
    state_residual = float(np.max(np.abs(
        first_soc - initial + snapshot.delta * b
    )))
    voltage, thermal, normalized = _network_limit_residuals(
        snapshot, result, first_only=True
    )
    return ExecutedIntervalRecord(
        iteration=attempt.iteration,
        controlling_attempt_id=attempt.attempt_id,
        generation_cost=snapshot.delta * generation_rate,
        storage_cycling_cost=snapshot.delta * storage_rate,
        renewable_curtailment_mwh=snapshot.delta * curtailment,
        active_loss_mwh=snapshot.delta * branch_loss,
        active_loss_crosscheck_mw_abs=abs(raw_branch_loss - injection_loss),
        state_transition_residual_mwh_abs=state_residual,
        voltage_violation_pu=voltage,
        thermal_residual_mva=thermal,
        normalized_squared_thermal_residual=normalized,
    )


def _summary(
    intervals: list[ExecutedIntervalRecord],
    plans: Mapping[str, OuterPlanRecord],
    attempts: list[ACAttemptRecord],
) -> dict[str, float]:
    totals = {
        name: float(sum(getattr(record, name) for record in intervals))
        for name in (
            "generation_cost", "storage_cycling_cost",
            "renewable_curtailment_mwh", "active_loss_mwh",
        )
    }
    totals.update({
        "maximum_voltage_violation_pu": max(
            (record.voltage_violation_pu for record in intervals), default=0.0
        ),
        "maximum_thermal_residual_mva": max(
            (record.thermal_residual_mva for record in intervals), default=0.0
        ),
        "maximum_normalized_squared_thermal_residual": max(
            (record.normalized_squared_thermal_residual for record in intervals),
            default=0.0,
        ),
        "cumulative_absolute_signpost_deviation_mwh": float(sum(
            sum(abs(value) for value in attempt.terminal_deviation_mwh.values())
            for attempt in attempts
            if attempt.supplied_executed_action
            and attempt.terminal_deviation_mwh is not None
        )),
        "runtime_seconds": float(
            sum(plan.audit.wall_time_seconds for plan in plans.values())
            + sum(
                attempt.audit.wall_time_seconds
                for attempt in attempts
                if attempt.audit is not None
            )
        ),
    })
    return totals


def _software_versions() -> dict[str, str]:
    versions = {"python": __import__("platform").python_version()}
    for package in (
        "cvxopf", "cvxpy", "numpy", "pandas", "clarabel", "cyipopt"
    ):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "unknown"
    try:
        from cyipopt import IPOPT_VERSION

        versions["ipopt"] = ".".join(str(value) for value in IPOPT_VERSION)
    except (ImportError, TypeError):
        versions["ipopt"] = "unknown"
    return versions


def solve_hierarchical_opf(
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    solve_config: HierarchicalSolveConfig = HierarchicalSolveConfig(),
) -> HierarchicalResult:
    """Execute receding-window AC control from lossy-DC energy signposts.

    The function takes one private input snapshot, retains every outer plan and
    AC attempt, and advances the realized state only from an accepted
    controlling AC solve.  It never executes a target-free recovery result.
    """
    if not isinstance(inputs, HierarchicalInputs):
        raise TypeError("inputs must be HierarchicalInputs")
    if not isinstance(policy, HierarchicalPolicy):
        raise TypeError("policy must be HierarchicalPolicy")
    if not isinstance(solve_config, HierarchicalSolveConfig):
        raise TypeError("solve_config must be HierarchicalSolveConfig")
    snapshot = _execution_snapshot(inputs)
    ids = snapshot.storage_device_ids
    realized = _initial_soc(snapshot)
    realized_history = [_aligned(realized, ids, "initial SoC")]
    executed_b: list[np.ndarray] = []
    executed_records: list[ExecutedIntervalRecord] = []
    plans: dict[str, OuterPlanRecord] = {}
    attempts: list[ACAttemptRecord] = []
    frozen_plan: OuterPlanRecord | None = None
    preceding_attempt: ACAttemptRecord | None = None
    termination_iteration: int | None = None
    termination_reason: str | None = None

    for iteration in range(snapshot.horizon_steps):
        if policy.outer_policy == "frozen":
            if frozen_plan is None:
                frozen_plan = _solve_outer(
                    snapshot, policy, solve_config, 0, realized
                )
                plans[frozen_plan.outer_plan_id] = frozen_plan
            outer = frozen_plan
        else:
            outer = _solve_outer(
                snapshot, policy, solve_config, iteration, realized
            )
            plans[outer.outer_plan_id] = outer
        if not outer.audit.accepted_primal:
            termination_iteration = iteration
            termination_reason = f"outer_{outer.audit.outcome}"
            break

        stop = min(
            snapshot.horizon_steps, iteration + policy.ac_window_steps
        )
        target = _outer_target(outer, stop)
        window_records, accepted = _window_attempts(
            snapshot, policy, solve_config, iteration, stop, outer, realized,
            target, preceding_attempt,
        )
        attempts.extend(window_records)
        if accepted is None:
            termination_iteration = iteration
            terminal_executed = [
                record for record in window_records if record.slot_state == "executed"
            ]
            if terminal_executed:
                last = terminal_executed[-1]
                outcome = None if last.audit is None else last.audit.outcome
                termination_reason = f"ac_recovery_exhausted:{outcome}"
            else:
                termination_reason = "ac_recovery_exhausted:no_solver_attempt"
            break
        if accepted.result is None:
            raise RuntimeError("accepted controlling attempt has no result")
        first_b = _as_2d(accepted.result["b"])[0]
        first_soc = _as_2d(accepted.result["soc"])[0]
        reconstructed = _aligned(realized, ids, "realized SoC") - (
            snapshot.delta * first_b
        )
        if np.max(np.abs(reconstructed - first_soc)) > (
            policy.tolerances.soc_recurrence_mwh_abs
        ):
            raise RuntimeError(
                "accepted AC first action disagrees with first post-step SoC"
            )
        executed_b.append(first_b.copy())
        executed_records.append(_executed_record(snapshot, accepted, policy))
        realized_history.append(reconstructed.copy())
        realized = dict(zip(ids, reconstructed, strict=True))
        preceding_attempt = accepted

    completed_intervals = len(executed_b)
    completed = completed_intervals == snapshot.horizon_steps
    return HierarchicalResult(
        policy=policy,
        provenance=HierarchicalProvenance(
            solve_config=solve_config,
            software_versions=_software_versions(),
        ),
        horizon_steps=snapshot.horizon_steps,
        delta=snapshot.delta,
        storage_device_ids=ids,
        outer_plans=plans,
        ac_attempts=tuple(attempts),
        executed_intervals=tuple(executed_records),
        realized_soc_mwh=np.asarray(realized_history),
        executed_b_mw=(
            np.asarray(executed_b)
            if executed_b
            else np.empty((0, len(ids)))
        ),
        trajectory_summary=_summary(executed_records, plans, attempts),
        completed_intervals=completed_intervals,
        completion_fraction=completed_intervals / snapshot.horizon_steps,
        completed=completed,
        termination_iteration=None if completed else termination_iteration,
        termination_reason=None if completed else termination_reason,
    )


__all__ = ["solve_hierarchical_opf"]
