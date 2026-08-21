"""Public contract for hierarchical lossy-DC to AC control.

Milestone 17 separates controller configuration and auditable result records
from the private orchestration implementation. This module owns the public
types and thin solve entry point; it contains no solve loop and imports no
experiment code.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from math import isfinite
from numbers import Integral, Real
from types import MappingProxyType
from typing import Literal, Mapping

import numpy as np
import pandas as pd
from cvxpy.reductions.solvers.defines import INSTALLED_SOLVERS

from cvxopf.generator import DispatchableGenerator
from cvxopf.hvdc import HVDCLink
from cvxopf.load import Load
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.problem import OPFBuild, OPFOptions
from cvxopf.storage import StorageUnitIdeal


OuterPolicy = Literal["frozen", "replan_every_step"]
InnerTerminalPolicy = Literal["hard_equality", "quadratic_soft"]
InitializationPolicy = Literal["flat_only", "shifted_with_recovery"]
AttemptSlotState = Literal[
    "executed",
    "construction_error",
    "source_unavailable",
    "not_needed_after_acceptance",
]
AttemptRole = Literal[
    "primary_controlling",
    "target_free",
    "copied_target_free",
    "perturbed_target_free",
    "perturbed_causal",
]
AttemptSourceKind = Literal["generated_flat", "attempt"]
OuterTerminalMode = Literal[
    "none",
    "equality",
    "shortfall",
    "linear",
    "quadratic",
    "shortfall_linear",
    "shortfall_quadratic",
]
AttemptOutcome = Literal[
    "accepted",
    "solver_certified_infeasible",
    "solver_failure",
    "unusable_primal",
]

ACCEPTED_SOLVER_STATUSES = frozenset({"optimal", "optimal_inaccurate"})
_CERTIFIED_INFEASIBLE_STATUSES = frozenset(
    {"infeasible", "infeasible_inaccurate"}
)
_SLOT_STATES = frozenset(
    {
        "executed",
        "construction_error",
        "source_unavailable",
        "not_needed_after_acceptance",
    }
)
_ATTEMPT_ROLES = frozenset(
    {
        "primary_controlling",
        "target_free",
        "copied_target_free",
        "perturbed_target_free",
        "perturbed_causal",
    }
)
_ATTEMPT_SOURCE_KINDS = frozenset({"generated_flat", "attempt"})
_OUTER_TERMINAL_MODES = frozenset(
    {
        "none",
        "equality",
        "shortfall",
        "linear",
        "quadratic",
        "shortfall_linear",
        "shortfall_quadratic",
    }
)


def _positive_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{name} must be an integer")
    result = int(value)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _finite_real(
    name: str, value: object, *, positive: bool = False, nonnegative: bool = False
) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real scalar")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and result < 0:
        raise ValueError(f"{name} must be nonnegative")
    return result


def _nonempty_name(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _readonly_array(value: object, *, name: str) -> np.ndarray:
    array = np.asarray(value).copy()
    if array.dtype.kind not in "biufc":
        raise TypeError(f"{name} must contain numeric values")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain only finite values")
    array.setflags(write=False)
    return array


def _readonly_float_mapping(
    values: Mapping[str, object], *, name: str, nonnegative: bool = False
) -> Mapping[str, float]:
    copied: dict[str, float] = {}
    for key, value in values.items():
        copied[_nonempty_name(f"{name} key", key)] = _finite_real(
            f"{name}[{key!r}]", value, nonnegative=nonnegative
        )
    return MappingProxyType(copied)


def _readonly_array_mapping(
    values: Mapping[str, object], *, name: str
) -> Mapping[str, np.ndarray]:
    copied = {
        _nonempty_name(f"{name} key", key): _readonly_array(
            value, name=f"{name}[{key!r}]"
        )
        for key, value in values.items()
    }
    return MappingProxyType(copied)


def _readonly_result_value(value: object) -> object:
    """Recursively snapshot one retained extraction result value."""
    if isinstance(value, np.ndarray):
        copied = value.copy()
        copied.setflags(write=False)
        return copied
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                deepcopy(key): _readonly_result_value(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_readonly_result_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_readonly_result_value(item) for item in value)
    return deepcopy(value)


def _readonly_result_mapping(
    result: Mapping[str, object],
) -> Mapping[str, object]:
    """Return a structurally read-only snapshot of extracted results."""
    return MappingProxyType(
        {key: _readonly_result_value(value) for key, value in result.items()}
    )


def _copy_frame(frame: pd.DataFrame | None, *, name: str) -> pd.DataFrame | None:
    if frame is None:
        return None
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{name} must be a pandas DataFrame or None")
    copied = frame.copy(deep=True)
    try:
        values = copied.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must contain numeric values") from exc
    if not np.all(np.isfinite(values)):
        raise ValueError(f"{name} must contain only finite values")
    return copied


@dataclass(frozen=True)
class HierarchicalAcceptanceTolerances:
    """Absolute residual thresholds used by the fixed M17 primal gate."""

    soc_recurrence_mwh_abs: float = 1e-4
    terminal_soc_mwh_abs: float = 1e-3
    soft_terminal_cost_abs: float = 1e-6
    ac_active_balance_pu_abs: float = 1e-6
    ac_reactive_balance_pu_abs: float = 1e-6
    dc_injection_reporting_mw_abs: float = 1e-4
    dc_nodal_balance_pu_abs: float = 1e-6
    voltage_bound_pu_abs: float = 1e-6
    branch_mva_abs: float = 1e-4
    branch_normalized_squared_residual: float = 1e-7

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            object.__setattr__(
                self,
                name,
                _finite_real(name, getattr(self, name), nonnegative=True),
            )


@dataclass(frozen=True)
class ShiftedRecoveryConfig:
    """Deterministic recovery parameters for ``shifted_with_recovery``."""

    perturbation_scales: tuple[float, ...] = (1e-4, 1e-3, 1e-2)
    seed_base: int = 17_000_000

    def __post_init__(self) -> None:
        scales = tuple(
            _finite_real(
                f"perturbation_scales[{index}]", value, positive=True
            )
            for index, value in enumerate(self.perturbation_scales)
        )
        if len(scales) != 3:
            raise ValueError(
                "shifted_with_recovery requires exactly three perturbation scales"
            )
        if len(set(scales)) != len(scales):
            raise ValueError("perturbation_scales must be unique")
        if tuple(sorted(scales)) != scales:
            raise ValueError("perturbation_scales must be strictly increasing")
        if isinstance(self.seed_base, bool) or not isinstance(
            self.seed_base, Integral
        ):
            raise TypeError("seed_base must be an integer")
        if int(self.seed_base) < 0:
            raise ValueError("seed_base must be nonnegative")
        object.__setattr__(self, "perturbation_scales", scales)
        object.__setattr__(self, "seed_base", int(self.seed_base))


@dataclass(frozen=True)
class HierarchicalPolicy:
    """Controller choices, distinct from model and solver configuration."""

    ac_window_steps: int
    outer_policy: OuterPolicy = "replan_every_step"
    inner_terminal_policy: InnerTerminalPolicy = "hard_equality"
    initialization_policy: InitializationPolicy = "shifted_with_recovery"
    quadratic_soft_weight: float | None = None
    recovery: ShiftedRecoveryConfig | None = None
    tolerances: HierarchicalAcceptanceTolerances = field(
        default_factory=HierarchicalAcceptanceTolerances
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "ac_window_steps",
            _positive_int("ac_window_steps", self.ac_window_steps),
        )
        if self.outer_policy not in {"frozen", "replan_every_step"}:
            raise ValueError(f"unsupported outer_policy {self.outer_policy!r}")
        if self.inner_terminal_policy not in {
            "hard_equality",
            "quadratic_soft",
        }:
            raise ValueError(
                "inner_terminal_policy must be 'hard_equality' or "
                "'quadratic_soft'"
            )
        if self.initialization_policy not in {
            "flat_only",
            "shifted_with_recovery",
        }:
            raise ValueError(
                "initialization_policy must be 'flat_only' or "
                "'shifted_with_recovery'"
            )
        if self.inner_terminal_policy == "quadratic_soft":
            if self.quadratic_soft_weight is None:
                raise ValueError(
                    "quadratic_soft requires quadratic_soft_weight"
                )
            object.__setattr__(
                self,
                "quadratic_soft_weight",
                _finite_real(
                    "quadratic_soft_weight",
                    self.quadratic_soft_weight,
                    positive=True,
                ),
            )
        elif self.quadratic_soft_weight is not None:
            raise ValueError(
                "quadratic_soft_weight is valid only for quadratic_soft"
            )
        if self.initialization_policy == "shifted_with_recovery":
            if self.recovery is None:
                object.__setattr__(self, "recovery", ShiftedRecoveryConfig())
            elif not isinstance(self.recovery, ShiftedRecoveryConfig):
                raise TypeError("recovery must be ShiftedRecoveryConfig")
        elif self.recovery is not None:
            raise ValueError("flat_only cannot define recovery parameters")
        if not isinstance(self.tolerances, HierarchicalAcceptanceTolerances):
            raise TypeError(
                "tolerances must be HierarchicalAcceptanceTolerances"
            )
        reference = HierarchicalAcceptanceTolerances()
        looser = [
            name
            for name in reference.__dataclass_fields__
            if getattr(self.tolerances, name) > getattr(reference, name)
        ]
        if looser:
            raise ValueError(
                "hierarchical tolerances cannot be looser than the reviewed "
                f"M17 gate: {looser}"
            )


_COMMON_SOLVE_OPTIONS: dict[str, str] = {
    "verbose": "bool",
    "warm_start": "bool",
}
_CLARABEL_OPTIONS: dict[str, str] = {
    **_COMMON_SOLVE_OPTIONS,
    "max_iter": "positive_int",
    "time_limit": "positive_real",
    "tol_gap_abs": "positive_real",
    "tol_gap_rel": "positive_real",
    "tol_feas": "positive_real",
    "tol_infeas_abs": "positive_real",
    "tol_infeas_rel": "positive_real",
}
_IPOPT_OPTIONS: dict[str, str] = {
    **_COMMON_SOLVE_OPTIONS,
    "max_iter": "positive_int",
    "max_cpu_time": "positive_real",
    "tol": "positive_real",
    "acceptable_tol": "positive_real",
    "constr_viol_tol": "positive_real",
    "dual_inf_tol": "positive_real",
    "compl_inf_tol": "positive_real",
    "acceptable_iter": "positive_int",
    "print_level": "nonnegative_int",
    "sb": "string",
    "mu_strategy": "string",
    "linear_solver": "string",
    "warm_start_init_point": "string",
}


def _validate_solve_option(name: str, value: object, kind: str) -> object:
    if kind == "bool":
        if not isinstance(value, bool):
            raise TypeError(f"solve option {name!r} must be Boolean")
        return value
    if kind in {"positive_int", "nonnegative_int"}:
        if isinstance(value, bool) or not isinstance(value, Integral):
            raise TypeError(f"solve option {name!r} must be an integer")
        numeric = int(value)
        if (kind == "positive_int" and numeric <= 0) or (
            kind == "nonnegative_int" and numeric < 0
        ):
            qualifier = "positive" if kind == "positive_int" else "nonnegative"
            raise ValueError(f"solve option {name!r} must be {qualifier}")
        if name == "print_level" and numeric > 12:
            raise ValueError("solve option 'print_level' must not exceed 12")
        return numeric
    if kind == "positive_real":
        return _finite_real(f"solve option {name!r}", value, positive=True)
    if kind == "string":
        text = _nonempty_name(f"solve option {name!r}", value)
        allowed = {
            "sb": {"yes", "no"},
            "warm_start_init_point": {"yes", "no"},
            "mu_strategy": {"monotone", "adaptive"},
        }
        if name in allowed and text not in allowed[name]:
            raise ValueError(
                f"solve option {name!r} must be one of {sorted(allowed[name])}"
            )
        return text
    raise RuntimeError(f"unknown solve-option validator {kind!r}")


@dataclass(frozen=True)
class LayerSolveConfig:
    """One supported solver and its validated, structurally read-only options."""

    solver: str
    options: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        solver = _nonempty_name("solver", self.solver).upper()
        schemas = {"CLARABEL": _CLARABEL_OPTIONS, "IPOPT": _IPOPT_OPTIONS}
        if solver not in schemas:
            raise ValueError(f"unsupported hierarchical solver {solver!r}")
        if not isinstance(self.options, Mapping):
            raise TypeError("solve options must be a mapping")
        schema = schemas[solver]
        copied: dict[str, object] = {}
        for raw_name, raw_value in self.options.items():
            name = _nonempty_name("solve option key", raw_name)
            if name in {"solver", "nlp"}:
                raise ValueError(f"solve option {name!r} is layer-owned")
            if name not in schema:
                raise ValueError(
                    f"unsupported {solver} solve option {name!r}"
                )
            copied[name] = deepcopy(
                _validate_solve_option(name, raw_value, schema[name])
            )
        object.__setattr__(self, "solver", solver)
        object.__setattr__(self, "options", MappingProxyType(copied))


@dataclass(frozen=True)
class HierarchicalSolveConfig:
    """Separate validated solve controls for the outer and inner layers."""

    outer: LayerSolveConfig = field(
        default_factory=lambda: LayerSolveConfig("CLARABEL")
    )
    ac: LayerSolveConfig = field(
        default_factory=lambda: LayerSolveConfig("IPOPT")
    )

    def __post_init__(self) -> None:
        if not isinstance(self.outer, LayerSolveConfig) or not isinstance(
            self.ac, LayerSolveConfig
        ):
            raise TypeError("outer and ac must be LayerSolveConfig values")
        if self.outer.solver != "CLARABEL":
            raise ValueError("outer hierarchical solver must be CLARABEL")
        if self.ac.solver != "IPOPT":
            raise ValueError("AC hierarchical solver must be IPOPT")
        installed = set(INSTALLED_SOLVERS)
        missing = {self.outer.solver, self.ac.solver} - installed
        if missing:
            raise ValueError(
                f"hierarchical solvers are not installed: {sorted(missing)}"
            )


def _device_ids(
    units: tuple[object, ...], *, family: str, required: bool
) -> tuple[str, ...]:
    ids: list[str] = []
    for index, unit in enumerate(units):
        value = getattr(unit, "device_id", None)
        if value is None and not required:
            continue
        ids.append(_nonempty_name(f"{family}[{index}].device_id", value))
    if len(set(ids)) != len(ids):
        raise ValueError(f"{family} device IDs must be unique")
    return tuple(ids)


def _validate_frame(
    frame: pd.DataFrame | None,
    *,
    name: str,
    horizon_steps: int,
    expected_columns: tuple[str, ...] | None,
    required: bool,
) -> pd.DataFrame | None:
    copied = _copy_frame(frame, name=name)
    if copied is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    if len(copied) != horizon_steps:
        raise ValueError(
            f"{name} must have {horizon_steps} rows, got {len(copied)}"
        )
    if expected_columns is not None and tuple(copied.columns) != expected_columns:
        raise ValueError(
            f"{name} columns must exactly match device order "
            f"{expected_columns}, got {tuple(copied.columns)}"
        )
    return copied


@dataclass(frozen=True)
class HierarchicalInputs:
    """Validated, defensively copied physical data for one hierarchy.

    The frozen container prevents field replacement, but pandas frames,
    device objects, and ``OPFOptions`` retain their ordinary mutable APIs.
    Stage 5 therefore takes a fresh private execution snapshot before any
    builder is called; callers must not treat this bundle as deeply immutable.
    """

    case: Mapping[str, object]
    horizon_steps: int
    delta: float
    generators: tuple[DispatchableGenerator, ...]
    loads: tuple[Load, ...]
    storage: tuple[StorageUnitIdeal, ...]
    df_load_p: pd.DataFrame
    df_load_q: pd.DataFrame | None = None
    nondispatchable: tuple[NondispatchableUnit, ...] = ()
    df_nd: pd.DataFrame | None = None
    hvdc: tuple[HVDCLink, ...] = ()
    df_hvdc_min: pd.DataFrame | None = None
    df_hvdc_max: pd.DataFrame | None = None
    options: OPFOptions = field(default_factory=OPFOptions)
    storage_device_ids: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        horizon = _positive_int("horizon_steps", self.horizon_steps)
        delta = _finite_real("delta", self.delta, positive=True)
        if not isinstance(self.case, Mapping):
            raise TypeError("case must be a mapping")
        case_copy: dict[str, object] = {}
        for key, value in self.case.items():
            name = _nonempty_name("case key", key)
            case_copy[name] = (
                _readonly_array(value, name=f"case[{name!r}]")
                if isinstance(value, np.ndarray)
                else deepcopy(value)
            )
        if not isinstance(self.options, OPFOptions):
            raise TypeError("options must be OPFOptions")
        if not self.options.init_flat:
            raise ValueError(
                "hierarchical initialization requires OPFOptions.init_flat=True"
            )
        if not self.options.enforce_branch_limits:
            raise ValueError(
                "hierarchical AC execution requires "
                "OPFOptions.enforce_branch_limits=True"
            )
        generators = tuple(deepcopy(tuple(self.generators)))
        loads = tuple(deepcopy(tuple(self.loads)))
        storage = tuple(deepcopy(tuple(self.storage)))
        nondispatchable = tuple(deepcopy(tuple(self.nondispatchable)))
        hvdc = tuple(deepcopy(tuple(self.hvdc)))
        fleet_types = (
            ("generators", generators, DispatchableGenerator),
            ("loads", loads, Load),
            ("storage", storage, StorageUnitIdeal),
            ("nondispatchable", nondispatchable, NondispatchableUnit),
            ("hvdc", hvdc, HVDCLink),
        )
        for family, units, expected_type in fleet_types:
            if any(not isinstance(unit, expected_type) for unit in units):
                raise TypeError(
                    f"{family} must contain only {expected_type.__name__} values"
                )
        if not generators:
            raise ValueError(
                "hierarchical control requires at least one explicit generator"
            )
        if not storage:
            raise ValueError("hierarchical control requires at least one storage unit")
        storage_ids = _device_ids(storage, family="storage", required=True)
        load_ids = _device_ids(loads, family="loads", required=True)
        if any(unit.shedding_cost_per_mwh is not None for unit in loads):
            raise ValueError(
                "M17 hierarchical control does not yet support sheddable loads"
            )
        nd_ids = _device_ids(
            nondispatchable,
            family="nondispatchable",
            required=self.df_nd is not None,
        )
        hvdc_ids = _device_ids(
            hvdc,
            family="hvdc",
            required=self.df_hvdc_min is not None
            or self.df_hvdc_max is not None,
        )
        df_load_p = _validate_frame(
            self.df_load_p,
            name="df_load_p",
            horizon_steps=horizon,
            expected_columns=load_ids,
            required=True,
        )
        df_load_q = _validate_frame(
            self.df_load_q,
            name="df_load_q",
            horizon_steps=horizon,
            expected_columns=load_ids,
            required=False,
        )
        df_nd = _validate_frame(
            self.df_nd,
            name="df_nd",
            horizon_steps=horizon,
            expected_columns=nd_ids if self.df_nd is not None else None,
            required=False,
        )
        if (self.df_hvdc_min is None) != (self.df_hvdc_max is None):
            raise ValueError("df_hvdc_min and df_hvdc_max must be supplied together")
        df_hvdc_min = _validate_frame(
            self.df_hvdc_min,
            name="df_hvdc_min",
            horizon_steps=horizon,
            expected_columns=hvdc_ids if self.df_hvdc_min is not None else None,
            required=False,
        )
        df_hvdc_max = _validate_frame(
            self.df_hvdc_max,
            name="df_hvdc_max",
            horizon_steps=horizon,
            expected_columns=hvdc_ids if self.df_hvdc_max is not None else None,
            required=False,
        )
        frame_indices = [
            frame.index
            for frame in (df_load_p, df_load_q, df_nd, df_hvdc_min, df_hvdc_max)
            if frame is not None
        ]
        if any(not frame_indices[0].equals(index) for index in frame_indices[1:]):
            raise ValueError("all hierarchical trajectory indices must match")
        if not frame_indices[0].is_unique:
            raise ValueError("hierarchical trajectory index must be unique")
        object.__setattr__(self, "case", MappingProxyType(case_copy))
        object.__setattr__(self, "horizon_steps", horizon)
        object.__setattr__(self, "delta", delta)
        object.__setattr__(self, "generators", generators)
        object.__setattr__(self, "loads", loads)
        object.__setattr__(self, "storage", storage)
        object.__setattr__(self, "nondispatchable", nondispatchable)
        object.__setattr__(self, "hvdc", hvdc)
        object.__setattr__(self, "df_load_p", df_load_p)
        object.__setattr__(self, "df_load_q", df_load_q)
        object.__setattr__(self, "df_nd", df_nd)
        object.__setattr__(self, "df_hvdc_min", df_hvdc_min)
        object.__setattr__(self, "df_hvdc_max", df_hvdc_max)
        object.__setattr__(self, "options", deepcopy(self.options))
        object.__setattr__(self, "storage_device_ids", storage_ids)


@dataclass(frozen=True)
class IPOPTStartEvidence:
    """Verified complete canonicalized IPOPT starting-point record."""

    complete_x0: np.ndarray
    layout: tuple[Mapping[str, object], ...]
    layout_signature: str
    model_coordinate_count: int
    auxiliary_coordinate_count: int
    object_ids_before: Mapping[str, tuple[int, ...]]
    object_ids_after: Mapping[str, tuple[int, ...]]

    def __post_init__(self) -> None:
        x0 = _readonly_array(self.complete_x0, name="complete_x0")
        if x0.ndim != 1:
            raise ValueError("complete_x0 must be one-dimensional")
        model_count = _positive_int(
            "model_coordinate_count", self.model_coordinate_count
        )
        if isinstance(self.auxiliary_coordinate_count, bool) or not isinstance(
            self.auxiliary_coordinate_count, Integral
        ):
            raise TypeError("auxiliary_coordinate_count must be an integer")
        auxiliary_count = int(self.auxiliary_coordinate_count)
        if auxiliary_count < 0:
            raise ValueError("auxiliary_coordinate_count must be nonnegative")
        if model_count + auxiliary_count != x0.size:
            raise ValueError("IPOPT coordinate counts do not match complete_x0")
        signature = _nonempty_name("layout_signature", self.layout_signature)
        layout_items = tuple(deepcopy(dict(item)) for item in self.layout)
        offset = 0
        original_coordinates = 0
        for index, item in enumerate(layout_items):
            required = {"name", "start", "stop", "is_original_variable"}
            if not required.issubset(item):
                raise ValueError(
                    f"layout[{index}] must contain {sorted(required)}"
                )
            _nonempty_name(f"layout[{index}].name", item["name"])
            start = item["start"]
            stop = item["stop"]
            if (
                isinstance(start, bool)
                or not isinstance(start, Integral)
                or isinstance(stop, bool)
                or not isinstance(stop, Integral)
            ):
                raise TypeError("layout start and stop offsets must be integers")
            start_int = int(start)
            stop_int = int(stop)
            if start_int != offset or stop_int <= start_int:
                raise ValueError("IPOPT layout must be contiguous and nonempty")
            if not isinstance(item["is_original_variable"], bool):
                raise TypeError("layout is_original_variable must be Boolean")
            if item["is_original_variable"]:
                original_coordinates += stop_int - start_int
            item["start"] = start_int
            item["stop"] = stop_int
            offset = stop_int
        if offset != x0.size or original_coordinates != model_count:
            raise ValueError("IPOPT layout does not match coordinate counts")
        layout = tuple(MappingProxyType(item) for item in layout_items)
        before = MappingProxyType(deepcopy(dict(self.object_ids_before)))
        after = MappingProxyType(deepcopy(dict(self.object_ids_after)))
        if before != after:
            raise ValueError("problem object identity changed across the solve")
        object.__setattr__(self, "complete_x0", x0)
        object.__setattr__(self, "layout", layout)
        object.__setattr__(self, "layout_signature", signature)
        object.__setattr__(self, "model_coordinate_count", model_count)
        object.__setattr__(self, "auxiliary_coordinate_count", auxiliary_count)
        object.__setattr__(self, "object_ids_before", before)
        object.__setattr__(self, "object_ids_after", after)


@dataclass(frozen=True)
class HierarchicalSolveAudit:
    """Raw solve outcome and independently reconstructed acceptance evidence."""

    status: str | None
    outcome: AttemptOutcome
    accepted_primal: bool
    missing_or_nonfinite_fields: tuple[str, ...]
    identity_error: str | None
    residuals: Mapping[str, float]
    exception: str | None
    wall_time_seconds: float
    solver_num_iters: int | str | None
    solver_setup_time_seconds: float | None
    solver_solve_time_seconds: float | None

    def __post_init__(self) -> None:
        if self.outcome not in {
            "accepted",
            "solver_certified_infeasible",
            "solver_failure",
            "unusable_primal",
        }:
            raise ValueError(f"unsupported solve outcome {self.outcome!r}")
        if self.accepted_primal != (self.outcome == "accepted"):
            raise ValueError("accepted_primal must agree with outcome")
        if self.accepted_primal and self.status not in ACCEPTED_SOLVER_STATUSES:
            raise ValueError("accepted primal requires an eligible solver status")
        missing = tuple(
            _nonempty_name("missing_or_nonfinite_field", value)
            for value in self.missing_or_nonfinite_fields
        )
        if len(missing) != len(set(missing)):
            raise ValueError("missing_or_nonfinite_fields must be unique")
        object.__setattr__(self, "missing_or_nonfinite_fields", missing)
        if self.identity_error is not None:
            _nonempty_name("identity_error", self.identity_error)
        if self.exception is not None:
            _nonempty_name("exception", self.exception)
        if self.exception is not None and self.outcome != "solver_failure":
            raise ValueError("an exception requires outcome='solver_failure'")
        if self.exception is None and self.outcome == "solver_failure":
            raise ValueError("solver_failure requires an exception")
        if self.exception is None and self.status in _CERTIFIED_INFEASIBLE_STATUSES:
            if self.outcome != "solver_certified_infeasible":
                raise ValueError(
                    "an infeasible status requires certified-infeasible outcome"
                )
        elif self.outcome == "solver_certified_infeasible":
            raise ValueError(
                "solver_certified_infeasible requires an infeasible status"
            )
        if (
            self.exception is None
            and self.status not in ACCEPTED_SOLVER_STATUSES
            and self.status not in _CERTIFIED_INFEASIBLE_STATUSES
            and self.outcome != "unusable_primal"
        ):
            raise ValueError(
                "remaining nonexception solver statuses require unusable_primal"
            )
        if self.accepted_primal and (
            missing or self.identity_error is not None or self.exception is not None
        ):
            raise ValueError(
                "accepted primal cannot retain missing fields, an identity "
                "error, or an exception"
            )
        object.__setattr__(
            self,
            "residuals",
            _readonly_float_mapping(
                self.residuals, name="residuals", nonnegative=True
            ),
        )
        object.__setattr__(
            self,
            "wall_time_seconds",
            _finite_real(
                "wall_time_seconds", self.wall_time_seconds, nonnegative=True
            ),
        )
        for name in ("solver_setup_time_seconds", "solver_solve_time_seconds"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    _finite_real(name, value, nonnegative=True),
                )


@dataclass(frozen=True)
class OuterPlanRecord:
    """One retained lossy-DC plan and its aligned storage signposts."""

    outer_plan_id: str
    created_iteration: int
    global_interval_start: int
    global_interval_stop: int
    local_boundary_indices: np.ndarray
    global_boundary_indices: np.ndarray
    storage_device_ids: tuple[str, ...]
    terminal_modes: Mapping[str, OuterTerminalMode]
    boundary_soc_mwh: np.ndarray | None
    build: OPFBuild
    result: Mapping[str, object]
    audit: HierarchicalSolveAudit

    def __post_init__(self) -> None:
        _nonempty_name("outer_plan_id", self.outer_plan_id)
        for name in (
            "created_iteration",
            "global_interval_start",
            "global_interval_stop",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Integral):
                raise TypeError(f"{name} must be an integer")
            if int(value) < 0:
                raise ValueError(f"{name} must be nonnegative")
            object.__setattr__(self, name, int(value))
        if self.global_interval_stop <= self.global_interval_start:
            raise ValueError("outer plan interval must be nonempty")
        if self.created_iteration != self.global_interval_start:
            raise ValueError(
                "created_iteration must equal global_interval_start"
            )
        if not isinstance(self.build, OPFBuild):
            raise TypeError("build must be OPFBuild")
        if not isinstance(self.audit, HierarchicalSolveAudit):
            raise TypeError("audit must be HierarchicalSolveAudit")
        ids = tuple(
            _nonempty_name("storage_device_id", value)
            for value in self.storage_device_ids
        )
        if len(ids) != len(set(ids)):
            raise ValueError("storage_device_ids must be unique")
        object.__setattr__(self, "storage_device_ids", ids)
        terminal_modes = {
            _nonempty_name("terminal mode storage ID", key): value
            for key, value in self.terminal_modes.items()
        }
        if set(terminal_modes) != set(ids):
            raise ValueError("terminal_modes must match storage_device_ids")
        invalid_modes = sorted(set(terminal_modes.values()) - _OUTER_TERMINAL_MODES)
        if invalid_modes:
            raise ValueError(f"unsupported outer terminal modes {invalid_modes}")
        object.__setattr__(
            self, "terminal_modes", MappingProxyType(terminal_modes)
        )
        local = _readonly_array(
            self.local_boundary_indices, name="local_boundary_indices"
        )
        global_indices = _readonly_array(
            self.global_boundary_indices, name="global_boundary_indices"
        )
        expected_local = np.arange(
            self.global_interval_stop - self.global_interval_start + 1
        )
        if local.ndim != 1 or not np.array_equal(local, expected_local):
            raise ValueError("local boundary indices must be consecutive from zero")
        if global_indices.ndim != 1 or not np.array_equal(
            global_indices, self.global_interval_start + expected_local
        ):
            raise ValueError("global boundary indices do not match plan interval")
        object.__setattr__(self, "local_boundary_indices", local)
        object.__setattr__(self, "global_boundary_indices", global_indices)
        if self.boundary_soc_mwh is not None:
            boundary = _readonly_array(
                self.boundary_soc_mwh, name="boundary_soc_mwh"
            )
            if boundary.shape != (len(local), len(ids)):
                raise ValueError(
                    "boundary_soc_mwh must have shape "
                    f"({len(local)}, {len(ids)})"
                )
            object.__setattr__(
                self,
                "boundary_soc_mwh",
                boundary,
            )
        elif self.audit.accepted_primal:
            raise ValueError("accepted outer plan requires boundary_soc_mwh")
        if not isinstance(self.result, Mapping):
            raise TypeError("result must be a mapping")
        object.__setattr__(self, "result", _readonly_result_mapping(self.result))


@dataclass(frozen=True)
class ACAttemptRecord:
    """One resolved AC attempt slot with state-dependent retained payload."""

    attempt_id: str
    slot_state: AttemptSlotState
    role: AttemptRole
    transformation: str
    ordinal: int
    iteration: int
    local_interval_start: int
    local_interval_stop: int
    global_interval_start: int
    global_interval_stop: int
    outer_plan_id: str
    source_kind: AttemptSourceKind | None
    source_attempt_id: str | None
    inner_terminal_policy: InnerTerminalPolicy
    storage_device_ids: tuple[str, ...]
    initial_soc_mwh: Mapping[str, float]
    target_soc_mwh: Mapping[str, float]
    terminal_deviation_mwh: Mapping[str, float] | None
    build: OPFBuild | None
    raw_start: Mapping[str, np.ndarray] | None
    assigned_start: Mapping[str, np.ndarray] | None
    solver_evidence: IPOPTStartEvidence | None
    result: Mapping[str, object] | None
    audit: HierarchicalSolveAudit | None
    reason: str | None
    supplied_executed_action: bool = False
    scale: float | None = None
    seed: int | None = None

    def __post_init__(self) -> None:
        _nonempty_name("attempt_id", self.attempt_id)
        _nonempty_name("outer_plan_id", self.outer_plan_id)
        _nonempty_name("transformation", self.transformation)
        if self.slot_state not in _SLOT_STATES:
            raise ValueError(f"unsupported attempt slot state {self.slot_state!r}")
        if self.role not in _ATTEMPT_ROLES:
            raise ValueError(f"unsupported attempt role {self.role!r}")
        if (
            self.source_kind is not None
            and self.source_kind not in _ATTEMPT_SOURCE_KINDS
        ):
            raise ValueError(f"unsupported attempt source kind {self.source_kind!r}")
        if self.source_attempt_id is not None:
            _nonempty_name("source_attempt_id", self.source_attempt_id)
        if self.source_kind == "generated_flat" and self.source_attempt_id is not None:
            raise ValueError("generated-flat sources cannot name an attempt")
        if self.source_kind == "attempt" and self.source_attempt_id is None:
            raise ValueError("attempt-derived sources require source_attempt_id")
        if self.source_kind is None and self.source_attempt_id is not None:
            raise ValueError("source_attempt_id requires source_kind='attempt'")
        if self.inner_terminal_policy not in {
            "hard_equality",
            "quadratic_soft",
        }:
            raise ValueError("unsupported inner_terminal_policy")
        for name in (
            "iteration",
            "local_interval_start",
            "local_interval_stop",
            "global_interval_start",
            "global_interval_stop",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Integral):
                raise TypeError(f"{name} must be an integer")
            if int(value) < 0:
                raise ValueError(f"{name} must be nonnegative")
            object.__setattr__(self, name, int(value))
        if self.local_interval_start != 0:
            raise ValueError("AC local_interval_start must be zero")
        if self.local_interval_stop <= 0:
            raise ValueError("AC local interval must be nonempty")
        if self.global_interval_start != self.iteration:
            raise ValueError("global_interval_start must equal iteration")
        if (
            self.global_interval_stop - self.global_interval_start
            != self.local_interval_stop
        ):
            raise ValueError("local and global AC window lengths must match")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, Integral):
            raise TypeError("ordinal must be an integer")
        if int(self.ordinal) < 0:
            raise ValueError("ordinal must be nonnegative")
        object.__setattr__(self, "ordinal", int(self.ordinal))
        ids = tuple(
            _nonempty_name("storage_device_id", value)
            for value in self.storage_device_ids
        )
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("storage_device_ids must be nonempty and unique")
        initial = _readonly_float_mapping(
            self.initial_soc_mwh, name="initial_soc_mwh", nonnegative=True
        )
        target = _readonly_float_mapping(
            self.target_soc_mwh, name="target_soc_mwh", nonnegative=True
        )
        if set(initial) != set(ids) or set(target) != set(ids):
            raise ValueError(
                "initial and target SoC mappings must match storage_device_ids"
            )
        object.__setattr__(self, "storage_device_ids", ids)
        object.__setattr__(self, "initial_soc_mwh", initial)
        object.__setattr__(self, "target_soc_mwh", target)
        if self.terminal_deviation_mwh is not None:
            deviation = _readonly_float_mapping(
                self.terminal_deviation_mwh,
                name="terminal_deviation_mwh",
            )
            if set(deviation) != set(ids):
                raise ValueError(
                    "terminal deviation must match storage_device_ids"
                )
            object.__setattr__(self, "terminal_deviation_mwh", deviation)
        if self.raw_start is not None:
            object.__setattr__(
                self,
                "raw_start",
                _readonly_array_mapping(self.raw_start, name="raw_start"),
            )
        if self.assigned_start is not None:
            object.__setattr__(
                self,
                "assigned_start",
                _readonly_array_mapping(self.assigned_start, name="assigned_start"),
            )
        if self.result is not None:
            object.__setattr__(
                self, "result", _readonly_result_mapping(self.result)
            )
        if self.build is not None and not isinstance(self.build, OPFBuild):
            raise TypeError("build must be OPFBuild or None")
        if self.solver_evidence is not None and not isinstance(
            self.solver_evidence, IPOPTStartEvidence
        ):
            raise TypeError("solver_evidence must be IPOPTStartEvidence or None")
        if self.audit is not None and not isinstance(
            self.audit, HierarchicalSolveAudit
        ):
            raise TypeError("audit must be HierarchicalSolveAudit or None")
        if self.slot_state == "executed":
            required = {
                "build": self.build,
                "raw_start": self.raw_start,
                "assigned_start": self.assigned_start,
                "solver_evidence": self.solver_evidence,
                "result": self.result,
                "audit": self.audit,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                raise ValueError(
                    f"executed attempt requires payload fields {missing}"
                )
            if set(self.raw_start) != set(self.assigned_start):
                raise ValueError("raw and assigned start namespaces must match")
            if any(
                self.raw_start[name].shape != self.assigned_start[name].shape
                for name in self.raw_start
            ):
                raise ValueError("raw and assigned start shapes must match")
            if (
                sum(value.size for value in self.assigned_start.values())
                != self.solver_evidence.model_coordinate_count
            ):
                raise ValueError(
                    "assigned model start size must match IPOPT model coordinates"
                )
            if self.source_kind is None:
                raise ValueError("executed attempts require an initialization source")
        elif self.slot_state == "construction_error":
            if any(
                value is not None
                for value in (
                    self.solver_evidence,
                    self.result,
                    self.audit,
                    self.terminal_deviation_mwh,
                )
            ):
                raise ValueError(
                    "construction_error cannot retain solver evidence, result, or audit"
                )
            _nonempty_name("reason", self.reason)
        else:
            if any(
                value is not None
                for value in (
                    self.build,
                    self.raw_start,
                    self.assigned_start,
                    self.solver_evidence,
                    self.result,
                    self.audit,
                    self.terminal_deviation_mwh,
                )
            ):
                raise ValueError(
                    f"{self.slot_state} cannot retain execution payload"
                )
            _nonempty_name("reason", self.reason)
        if self.supplied_executed_action:
            if (
                self.slot_state != "executed"
                or self.audit is None
                or not self.audit.accepted_primal
                or self.role == "target_free"
            ):
                raise ValueError(
                    "executed action requires an accepted controlling attempt"
                )
        elif (
            self.slot_state == "executed"
            and self.audit is not None
            and self.audit.accepted_primal
            and self.role != "target_free"
        ):
            raise ValueError(
                "an accepted controlling attempt must supply the executed action"
            )
        if self.role == "target_free" and self.terminal_deviation_mwh is not None:
            raise ValueError("target-free attempts cannot report terminal deviation")
        if (
            self.slot_state == "executed"
            and self.audit is not None
            and self.audit.accepted_primal
            and self.role != "target_free"
            and self.terminal_deviation_mwh is None
        ):
            raise ValueError(
                "accepted controlling attempts require terminal deviation"
            )
        perturbation_roles = {"perturbed_target_free", "perturbed_causal"}
        if self.role in perturbation_roles:
            if self.scale is None or self.seed is None:
                raise ValueError("perturbation attempts require scale and seed")
        elif self.scale is not None or self.seed is not None:
            raise ValueError("scale and seed are valid only for perturbation attempts")
        if self.scale is not None:
            object.__setattr__(
                self, "scale", _finite_real("scale", self.scale, positive=True)
            )
        if self.seed is not None:
            if isinstance(self.seed, bool) or not isinstance(self.seed, Integral):
                raise TypeError("seed must be an integer")
            if int(self.seed) < 0:
                raise ValueError("seed must be nonnegative")
            object.__setattr__(self, "seed", int(self.seed))


@dataclass(frozen=True)
class ExecutedIntervalRecord:
    """Physical and economic accounting for one accepted first AC action."""

    iteration: int
    controlling_attempt_id: str
    generation_cost: float
    storage_cycling_cost: float
    renewable_curtailment_mwh: float
    active_loss_mwh: float
    active_loss_crosscheck_mw_abs: float
    state_transition_residual_mwh_abs: float
    voltage_violation_pu: float
    thermal_residual_mva: float
    normalized_squared_thermal_residual: float

    def __post_init__(self) -> None:
        _nonempty_name("controlling_attempt_id", self.controlling_attempt_id)
        if isinstance(self.iteration, bool) or not isinstance(
            self.iteration, Integral
        ):
            raise TypeError("iteration must be an integer")
        if int(self.iteration) < 0:
            raise ValueError("iteration must be nonnegative")
        object.__setattr__(self, "iteration", int(self.iteration))
        for name in self.__dataclass_fields__:
            if name in {"iteration", "controlling_attempt_id"}:
                continue
            nonnegative = name != "generation_cost"
            object.__setattr__(
                self,
                name,
                _finite_real(
                    name, getattr(self, name), nonnegative=nonnegative
                ),
            )


@dataclass(frozen=True)
class HierarchicalProvenance:
    """Normalized solver and software context retained for one run."""

    solve_config: HierarchicalSolveConfig
    software_versions: Mapping[str, str]
    accepted_solver_statuses: frozenset[str] = ACCEPTED_SOLVER_STATUSES

    def __post_init__(self) -> None:
        if not isinstance(self.solve_config, HierarchicalSolveConfig):
            raise TypeError("solve_config must be HierarchicalSolveConfig")
        if frozenset(self.accepted_solver_statuses) != ACCEPTED_SOLVER_STATUSES:
            raise ValueError("accepted_solver_statuses is fixed by M17")
        versions = {
            _nonempty_name("software name", key): _nonempty_name(
                f"software_versions[{key!r}]", value
            )
            for key, value in self.software_versions.items()
        }
        object.__setattr__(
            self, "software_versions", MappingProxyType(versions)
        )
        object.__setattr__(
            self, "accepted_solver_statuses", ACCEPTED_SOLVER_STATUSES
        )


_OUTER_COMMON_RESIDUAL_NAMES = frozenset(
    {
        "soc_recurrence_mwh_abs",
        "dc_injection_reporting_mw_abs",
        "dc_nodal_balance_pu_abs",
    }
)
_AC_RESIDUAL_NAMES = frozenset(
    {
        "soc_recurrence_mwh_abs",
        "ac_active_balance_pu_abs",
        "ac_reactive_balance_pu_abs",
        "voltage_bound_pu_abs",
        "branch_mva_abs",
        "branch_normalized_squared_residual",
        "curtailment_nonnegativity_pu_abs",
        "branch_loss_nonnegativity_pu_abs",
    }
)

_RESIDUAL_TOLERANCE_FIELDS = {
    "curtailment_nonnegativity_pu_abs": "ac_active_balance_pu_abs",
    "branch_loss_nonnegativity_pu_abs": "ac_active_balance_pu_abs",
}


def _validate_accepted_residuals(
    audit: HierarchicalSolveAudit,
    tolerances: HierarchicalAcceptanceTolerances,
    *,
    required: frozenset[str],
    record_name: str,
) -> None:
    """Complete the status/outcome classifier using policy residual gates."""
    if audit.status not in ACCEPTED_SOLVER_STATUSES:
        return
    missing = sorted(required - set(audit.residuals))
    failures = {
        name: (
            audit.residuals[name],
            getattr(tolerances, _RESIDUAL_TOLERANCE_FIELDS.get(name, name)),
        )
        for name in required
        if name in audit.residuals
        and audit.residuals[name]
        > getattr(tolerances, _RESIDUAL_TOLERANCE_FIELDS.get(name, name))
    }
    other_gate_failure = bool(
        audit.missing_or_nonfinite_fields
        or audit.identity_error is not None
        or audit.exception is not None
    )
    gates_passed = not missing and not failures and not other_gate_failure
    if not audit.accepted_primal:
        if gates_passed:
            raise ValueError(
                f"{record_name} outcome does not match the accepted-primal gate"
            )
        return
    if missing:
        raise ValueError(
            f"accepted {record_name} is missing required residuals {missing}"
        )
    if failures:
        raise ValueError(
            f"accepted {record_name} exceeds policy residual tolerances: "
            f"{failures}"
        )


def _validate_attempt_registries(
    attempts: tuple[ACAttemptRecord, ...], policy: HierarchicalPolicy
) -> None:
    """Validate the complete per-window slot structure selected by policy."""
    if list(attempts) != sorted(
        attempts, key=lambda attempt: (attempt.iteration, attempt.ordinal)
    ):
        raise ValueError("AC attempts must be ordered by iteration and ordinal")
    grouped: dict[int, list[ACAttemptRecord]] = {}
    for attempt in attempts:
        grouped.setdefault(attempt.iteration, []).append(attempt)
        if (
            policy.initialization_policy == "shifted_with_recovery"
            and attempt.source_kind == "generated_flat"
            and attempt.iteration != 0
        ):
            raise ValueError("generated-flat sources are valid only at iteration zero")
    for iteration, window_attempts in grouped.items():
        if policy.initialization_policy == "flat_only":
            if len(window_attempts) != 1:
                raise ValueError(
                    "flat_only requires exactly one attempt slot per window"
                )
            primary = window_attempts[0]
            if (
                primary.ordinal != 0
                or primary.role != "primary_controlling"
                or primary.transformation != "flat"
                or primary.scale is not None
                or primary.seed is not None
            ):
                raise ValueError(
                    "flat_only requires ordinal-zero flat primary_controlling"
                )
            if (
                primary.slot_state == "executed"
                and (
                    primary.source_kind != "generated_flat"
                    or primary.source_attempt_id is not None
                )
            ):
                raise ValueError(
                    "executed flat_only attempts require generated-flat provenance"
                )
            continue

        expected_roles = (
            "primary_controlling",
            "target_free",
            "copied_target_free",
            "perturbed_target_free",
            "perturbed_target_free",
            "perturbed_target_free",
            "perturbed_causal",
            "perturbed_causal",
            "perturbed_causal",
        )
        if len(window_attempts) != len(expected_roles):
            raise ValueError(
                "shifted_with_recovery requires exactly nine attempt slots "
                "per window"
            )
        if tuple(attempt.ordinal for attempt in window_attempts) != tuple(range(9)):
            raise ValueError(
                "shifted_with_recovery attempt ordinals must be exactly 0..8"
            )
        if tuple(attempt.role for attempt in window_attempts) != expected_roles:
            raise ValueError(
                "shifted_with_recovery attempt roles do not match the registry"
            )
        causal_transformation = "flat" if iteration == 0 else "shifted_preceding"
        expected_transformations = (
            causal_transformation,
            causal_transformation,
            "copy_target_free",
            "perturb_target_free",
            "perturb_target_free",
            "perturb_target_free",
            "perturb_causal",
            "perturb_causal",
            "perturb_causal",
        )
        if (
            tuple(attempt.transformation for attempt in window_attempts)
            != expected_transformations
        ):
            raise ValueError(
                "shifted_with_recovery transformations do not match the registry"
            )
        recovery = policy.recovery
        if recovery is None:  # defensive; policy validation already prevents this
            raise ValueError("shifted_with_recovery requires recovery configuration")
        scales = recovery.perturbation_scales
        expected_scales = (None, None, None, *scales, *scales)
        if tuple(attempt.scale for attempt in window_attempts) != expected_scales:
            raise ValueError(
                "shifted_with_recovery perturbation scales do not match policy"
            )
        expected_seeds: list[int | None] = [None, None, None]
        for source_code in (1, 2):
            expected_seeds.extend(
                recovery.seed_base + 100 * iteration + 10 * source_code + index
                for index in range(1, len(scales) + 1)
            )
        if tuple(attempt.seed for attempt in window_attempts) != tuple(expected_seeds):
            raise ValueError(
                "shifted_with_recovery perturbation seeds do not match policy"
            )


@dataclass(frozen=True)
class HierarchicalResult:
    """Complete retained audit tree and realized hierarchical trajectory."""

    policy: HierarchicalPolicy
    provenance: HierarchicalProvenance
    horizon_steps: int
    delta: float
    storage_device_ids: tuple[str, ...]
    outer_plans: Mapping[str, OuterPlanRecord]
    ac_attempts: tuple[ACAttemptRecord, ...]
    executed_intervals: tuple[ExecutedIntervalRecord, ...]
    realized_soc_mwh: np.ndarray
    executed_b_mw: np.ndarray
    trajectory_summary: Mapping[str, float]
    completed_intervals: int
    completion_fraction: float
    completed: bool
    termination_iteration: int | None
    termination_reason: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.policy, HierarchicalPolicy):
            raise TypeError("policy must be HierarchicalPolicy")
        if not isinstance(self.provenance, HierarchicalProvenance):
            raise TypeError("provenance must be HierarchicalProvenance")
        horizon = _positive_int("horizon_steps", self.horizon_steps)
        delta = _finite_real("delta", self.delta, positive=True)
        ids = tuple(
            _nonempty_name("storage_device_id", value)
            for value in self.storage_device_ids
        )
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("storage_device_ids must be nonempty and unique")
        plans = dict(self.outer_plans)
        if any(not isinstance(plan, OuterPlanRecord) for plan in plans.values()):
            raise TypeError("outer_plans must contain OuterPlanRecord values")
        if any(key != plan.outer_plan_id for key, plan in plans.items()):
            raise ValueError("outer plan mapping keys must match record IDs")
        attempts = tuple(self.ac_attempts)
        if any(not isinstance(attempt, ACAttemptRecord) for attempt in attempts):
            raise TypeError("ac_attempts must contain ACAttemptRecord values")
        if len({attempt.attempt_id for attempt in attempts}) != len(attempts):
            raise ValueError("AC attempt IDs must be unique")
        _validate_attempt_registries(attempts, self.policy)
        executed = tuple(self.executed_intervals)
        if any(
            not isinstance(record, ExecutedIntervalRecord) for record in executed
        ):
            raise TypeError(
                "executed_intervals must contain ExecutedIntervalRecord values"
            )
        if any(plan.storage_device_ids != ids for plan in plans.values()):
            raise ValueError("outer plan storage identity differs from result")
        if any(attempt.storage_device_ids != ids for attempt in attempts):
            raise ValueError("AC attempt storage identity differs from result")
        if any(
            attempt.inner_terminal_policy != self.policy.inner_terminal_policy
            for attempt in attempts
        ):
            raise ValueError(
                "every AC attempt must match the configured inner terminal policy"
            )
        if any(attempt.outer_plan_id not in plans for attempt in attempts):
            raise ValueError("AC attempt references an unknown outer plan")
        plan_iterations = [plan.created_iteration for plan in plans.values()]
        if len(plan_iterations) != len(set(plan_iterations)):
            raise ValueError("outer plans must have unique creation iterations")
        if self.policy.outer_policy == "frozen":
            if len(plans) != 1 or plan_iterations != [0]:
                raise ValueError(
                    "frozen policy requires exactly one iteration-zero outer plan"
                )
            frozen_plan_id = next(iter(plans))
            if any(
                attempt.outer_plan_id != frozen_plan_id for attempt in attempts
            ):
                raise ValueError(
                    "frozen attempts must reference the iteration-zero outer plan"
                )
        else:
            if sorted(plan_iterations) != list(range(len(plan_iterations))):
                raise ValueError(
                    "replan_every_step outer-plan iterations must be consecutive "
                    "from zero"
                )
            if any(
                plans[attempt.outer_plan_id].created_iteration != attempt.iteration
                for attempt in attempts
            ):
                raise ValueError(
                    "replan_every_step attempts must reference their iteration's plan"
                )
        if any(plan.global_interval_stop > horizon for plan in plans.values()):
            raise ValueError("outer plan interval exceeds the result horizon")
        if any(attempt.global_interval_stop > horizon for attempt in attempts):
            raise ValueError("AC attempt window exceeds the result horizon")
        for attempt in attempts:
            plan = plans[attempt.outer_plan_id]
            if not plan.audit.accepted_primal:
                raise ValueError(
                    "AC attempts require an accepted referenced outer plan"
                )
            if (
                attempt.global_interval_start < plan.global_interval_start
                or attempt.global_interval_stop > plan.global_interval_stop
            ):
                raise ValueError(
                    "AC attempt window must lie within its referenced outer plan"
                )
        for plan in plans.values():
            required_outer = _OUTER_COMMON_RESIDUAL_NAMES
            modes = set(plan.terminal_modes.values())
            if modes & {"equality", "shortfall"}:
                required_outer = required_outer | {"terminal_soc_mwh_abs"}
            if modes & {
                "linear",
                "quadratic",
                "shortfall_linear",
                "shortfall_quadratic",
            }:
                required_outer = required_outer | {"soft_terminal_cost_abs"}
            _validate_accepted_residuals(
                plan.audit,
                self.policy.tolerances,
                required=required_outer,
                record_name=f"outer plan {plan.outer_plan_id!r}",
            )
        attempt_by_id = {attempt.attempt_id: attempt for attempt in attempts}
        attempt_positions = {
            attempt.attempt_id: index for index, attempt in enumerate(attempts)
        }
        for attempt in attempts:
            required_residuals = _AC_RESIDUAL_NAMES
            if attempt.role != "target_free":
                terminal_residual = (
                    "terminal_soc_mwh_abs"
                    if attempt.inner_terminal_policy == "hard_equality"
                    else "soft_terminal_cost_abs"
                )
                required_residuals = required_residuals | {terminal_residual}
            if attempt.audit is not None:
                _validate_accepted_residuals(
                    attempt.audit,
                    self.policy.tolerances,
                    required=required_residuals,
                    record_name=f"AC attempt {attempt.attempt_id!r}",
                )
            source_id = attempt.source_attempt_id
            if source_id is None:
                if attempt.slot_state == "executed" and attempt.role in {
                    "copied_target_free",
                    "perturbed_target_free",
                }:
                    raise ValueError(
                        f"AC attempt role {attempt.role!r} requires a source"
                    )
                if (
                    attempt.slot_state == "executed"
                    and attempt.role == "perturbed_causal"
                    and attempt.source_kind != "generated_flat"
                ):
                    raise ValueError(
                        "a source-free causal perturbation must use generated_flat"
                    )
                continue
            source = attempt_by_id.get(source_id)
            if source is None:
                raise ValueError(
                    f"AC attempt {attempt.attempt_id!r} references unknown "
                    f"source {source_id!r}"
                )
            if source_id == attempt.attempt_id:
                raise ValueError("AC attempts cannot reference themselves")
            if attempt_positions[source_id] >= attempt_positions[attempt.attempt_id]:
                raise ValueError("AC attempt sources must be causally prior")
            if source.iteration > attempt.iteration:
                raise ValueError("AC attempt source cannot come from a future iteration")
            if attempt.slot_state == "executed" and (
                source.slot_state != "executed"
                or source.audit is None
                or not source.audit.accepted_primal
            ):
                raise ValueError(
                    "an executed sourced attempt requires an accepted executed source"
                )
            if attempt.role in {
                "primary_controlling",
                "target_free",
                "perturbed_causal",
            }:
                if source.iteration != attempt.iteration - 1:
                    raise ValueError(
                        "causal source must come from the immediately preceding "
                        "iteration"
                    )
                if not source.supplied_executed_action:
                    raise ValueError(
                        "prior-window causal source must have supplied an "
                        "executed action"
                    )
            elif source.iteration != attempt.iteration:
                raise ValueError(
                    "within-window recovery source must share the attempt iteration"
                )
            if attempt.role in {
                "copied_target_free",
                "perturbed_target_free",
            } and source.role != "target_free":
                raise ValueError(
                    f"AC attempt role {attempt.role!r} requires a target-free source"
                )
        if isinstance(self.completed_intervals, bool) or not isinstance(
            self.completed_intervals, Integral
        ):
            raise TypeError("completed_intervals must be an integer")
        completed_intervals = int(self.completed_intervals)
        if completed_intervals < 0 or completed_intervals > horizon:
            raise ValueError("completed_intervals must lie within the horizon")
        if completed_intervals != len(executed):
            raise ValueError("completed_intervals must equal executed record count")
        fraction = _finite_real(
            "completion_fraction", self.completion_fraction, nonnegative=True
        )
        if fraction > 1:
            raise ValueError("completion_fraction must not exceed one")
        expected_fraction = completed_intervals / horizon
        if fraction != expected_fraction:
            raise ValueError(
                "completion_fraction must equal completed_intervals / horizon_steps"
            )
        if self.completed != (completed_intervals == horizon):
            raise ValueError("completed must agree with horizon coverage")
        if self.completed and (
            fraction != 1.0
            or self.termination_iteration is not None
            or self.termination_reason is not None
        ):
            raise ValueError("completed results cannot carry termination data")
        if not self.completed and self.termination_reason is None:
            raise ValueError("incomplete results require termination_reason")
        if not self.completed:
            if isinstance(self.termination_iteration, bool) or not isinstance(
                self.termination_iteration, Integral
            ):
                raise TypeError(
                    "incomplete results require an integer termination_iteration"
                )
            if self.termination_iteration != completed_intervals:
                raise ValueError(
                    "termination_iteration must equal completed_intervals"
                )
        unsuccessful_plans = [
            plan for plan in plans.values() if not plan.audit.accepted_primal
        ]
        if unsuccessful_plans:
            if len(unsuccessful_plans) != 1 or self.completed:
                raise ValueError(
                    "an unsuccessful outer solve must be the sole terminal failure"
                )
            failed_plan = unsuccessful_plans[0]
            if (
                failed_plan.created_iteration != completed_intervals
                or failed_plan.created_iteration != max(plan_iterations)
                or any(
                    attempt.outer_plan_id == failed_plan.outer_plan_id
                    for attempt in attempts
                )
            ):
                raise ValueError(
                    "an unsuccessful outer plan must terminate before AC registration"
                )
        soc = _readonly_array(self.realized_soc_mwh, name="realized_soc_mwh")
        battery = _readonly_array(self.executed_b_mw, name="executed_b_mw")
        if soc.shape != (completed_intervals + 1, len(ids)):
            raise ValueError(
                "realized_soc_mwh must have shape "
                f"({completed_intervals + 1}, {len(ids)})"
            )
        if battery.shape != (completed_intervals, len(ids)):
            raise ValueError(
                "executed_b_mw must have shape "
                f"({completed_intervals}, {len(ids)})"
            )
        for attempt in attempts:
            if attempt.iteration > completed_intervals:
                raise ValueError("AC attempt iteration exceeds realized state coverage")
            initial = np.array(
                [attempt.initial_soc_mwh[device_id] for device_id in ids]
            )
            if not np.array_equal(initial, soc[attempt.iteration]):
                raise ValueError(
                    "AC attempt initial SoC does not match the realized state"
                )
            plan = plans[attempt.outer_plan_id]
            if plan.boundary_soc_mwh is None:
                raise ValueError("referenced outer plan has no SoC signposts")
            boundary_row = (
                attempt.global_interval_stop - plan.global_interval_start
            )
            target = np.array(
                [attempt.target_soc_mwh[device_id] for device_id in ids]
            )
            if not np.array_equal(target, plan.boundary_soc_mwh[boundary_row]):
                raise ValueError(
                    "AC attempt target SoC does not match its outer-plan signpost"
                )
        if [record.iteration for record in executed] != list(
            range(completed_intervals)
        ):
            raise ValueError("executed interval iterations must be consecutive")
        for record in executed:
            attempt = attempt_by_id.get(record.controlling_attempt_id)
            if attempt is None or not attempt.supplied_executed_action:
                raise ValueError(
                    "each executed interval must reference its controlling attempt"
                )
            if attempt.iteration != record.iteration:
                raise ValueError(
                    "executed interval and controlling attempt iterations must match"
                )
        executed_by_iteration = {record.iteration: record for record in executed}
        attempts_by_iteration: dict[int, list[ACAttemptRecord]] = {}
        for attempt in attempts:
            attempts_by_iteration.setdefault(attempt.iteration, []).append(attempt)
        if (
            not self.completed
            and not unsuccessful_plans
            and completed_intervals not in attempts_by_iteration
        ):
            raise ValueError(
                "an accepted terminal outer plan requires a complete AC attempt "
                "registry at the termination iteration"
            )
        for iteration, window_attempts in attempts_by_iteration.items():
            suppliers = [
                attempt
                for attempt in window_attempts
                if attempt.supplied_executed_action
            ]
            expected_supplier_count = 1 if iteration in executed_by_iteration else 0
            if len(suppliers) != expected_supplier_count:
                raise ValueError(
                    "each executed window requires exactly one action-supplying "
                    "attempt; failed windows require none"
                )
            if not suppliers:
                continue
            supplier = suppliers[0]
            if (
                executed_by_iteration[iteration].controlling_attempt_id
                != supplier.attempt_id
            ):
                raise ValueError(
                    "executed interval must reference the unique accepted attempt"
                )
            if any(
                later.slot_state != "not_needed_after_acceptance"
                for later in window_attempts
                if later.ordinal > supplier.ordinal
            ):
                raise ValueError(
                    "all slots after the first accepted controlling attempt must "
                    "be not_needed_after_acceptance"
                )
        object.__setattr__(self, "storage_device_ids", ids)
        object.__setattr__(self, "outer_plans", MappingProxyType(plans))
        object.__setattr__(self, "ac_attempts", attempts)
        object.__setattr__(self, "executed_intervals", executed)
        object.__setattr__(
            self,
            "realized_soc_mwh",
            soc,
        )
        object.__setattr__(
            self,
            "executed_b_mw",
            battery,
        )
        object.__setattr__(
            self,
            "trajectory_summary",
            _readonly_float_mapping(
                self.trajectory_summary, name="trajectory_summary"
            ),
        )
        object.__setattr__(self, "completed_intervals", completed_intervals)
        object.__setattr__(self, "completion_fraction", fraction)
        object.__setattr__(self, "horizon_steps", horizon)
        object.__setattr__(self, "delta", delta)


def solve_hierarchical_opf(
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    solve_config: HierarchicalSolveConfig = HierarchicalSolveConfig(),
) -> HierarchicalResult:
    """Run the M17 lossy-DC-to-AC hierarchical controller."""
    from cvxopf._hierarchical_solver import solve_hierarchical_opf as _solve

    return _solve(inputs, policy, solve_config)


__all__ = [
    "ACCEPTED_SOLVER_STATUSES",
    "ACAttemptRecord",
    "AttemptOutcome",
    "AttemptRole",
    "AttemptSourceKind",
    "AttemptSlotState",
    "ExecutedIntervalRecord",
    "HierarchicalAcceptanceTolerances",
    "HierarchicalInputs",
    "HierarchicalPolicy",
    "HierarchicalProvenance",
    "HierarchicalResult",
    "HierarchicalSolveAudit",
    "HierarchicalSolveConfig",
    "IPOPTStartEvidence",
    "InitializationPolicy",
    "InnerTerminalPolicy",
    "LayerSolveConfig",
    "OuterPlanRecord",
    "OuterPolicy",
    "OuterTerminalMode",
    "ShiftedRecoveryConfig",
    "solve_hierarchical_opf",
]
