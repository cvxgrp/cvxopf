"""Build-time numerical representation policy and component-owned box evidence.

No variable names or arbitrary user equalities select fixed boxes. Solver
transformations retain this provenance through the canonical inverse chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence
from types import MappingProxyType

import cvxpy as cp
import numpy as np

from cvxopf._temporal_assembly import (
    BoxRepresentation,
    Formulation,
    PreparedBoxBounds,
    VariableBoxFamily,
)


@dataclass(frozen=True)
class NumericalPreparation:
    """Opt-in, build-time policy; all production defaults remain disabled.

    Prepared solves retain native and original-space diagnostic evidence;
    numerical qualification and any default adoption remain separate gates.
    ``cost_coordinates`` is AC-only: represent battery cycling and shedding in
    cost units internally, restore engineering units publicly, and retain
    advisory economic-accuracy warnings without rejecting native solutions.
    ``objective_assembly='hourly'`` preserves the original aggregate-interval
    objective (including non-unit durations). ``'component_first'`` integrates
    contributions separately in the solve graph; initially it requires
    standalone, vectorized AC with cost coordinates. Neither option changes
    economic weights or the public physical graph, and no retry is implied.
    """

    normalize_device_limits: bool = False
    exact_fixed_boxes: bool = False
    canonical_scaling: Literal["none", "joint5"] = "none"
    cost_coordinates: bool = False
    objective_assembly: Literal["hourly", "component_first"] = "hourly"

    def __post_init__(self) -> None:
        for name in ("normalize_device_limits", "exact_fixed_boxes", "cost_coordinates"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a bool")
        if not isinstance(
            self.canonical_scaling, str
        ) or self.canonical_scaling not in ("none", "joint5"):
            raise ValueError("canonical_scaling must be 'none' or 'joint5'")
        if not isinstance(self.objective_assembly, str) or self.objective_assembly not in ("hourly", "component_first"):
            raise ValueError("objective_assembly must be 'hourly' or 'component_first'")
        if self.objective_assembly == "component_first" and not self.cost_coordinates:
            raise ValueError("component_first objective assembly requires cost_coordinates=True")

    @property
    def enabled(self) -> bool:
        return (
            self.normalize_device_limits
            or self.exact_fixed_boxes
            or self.canonical_scaling != "none"
            or self.cost_coordinates
            or self.objective_assembly != "hourly"
        )

    def validate_formulation(self, formulation: Formulation) -> None:
        if formulation not in ("ac", "socp", "lossy_dc", "singlenode_dc"):
            raise ValueError(f"unsupported formulation {formulation!r}")
        if (
            formulation in ("lossy_dc", "singlenode_dc")
            and self.normalize_device_limits
        ):
            raise ValueError("DC formulations do not support normalized device limits")
        if formulation == "ac" and self.canonical_scaling != "none":
            raise ValueError("AC does not support canonical scaling")
        if formulation != "ac" and self.cost_coordinates:
            raise ValueError("cost coordinates are qualified for AC only")

    def validate_assembly(self, temporal_assembly: str) -> None:
        if self.objective_assembly == "component_first" and temporal_assembly != "vectorized":
            raise ValueError("component_first objective assembly requires standalone vectorized AC")


def validate_preparation(
    policy: NumericalPreparation, formulation: Formulation
) -> None:
    if not isinstance(policy, NumericalPreparation):
        raise TypeError("numerical_preparation must be NumericalPreparation")
    policy.validate_formulation(formulation)


def reject_hierarchical_preparation(policy: NumericalPreparation) -> None:
    if not isinstance(policy, NumericalPreparation):
        raise TypeError("numerical_preparation must be NumericalPreparation")
    if policy.enabled:
        raise ValueError("prepared hierarchical execution is deferred to Milestone 21")


EXACT_BOX_FAMILIES = frozenset(
    (
        VariableBoxFamily.DISPATCHABLE_P,
        VariableBoxFamily.NONDISPATCHABLE_REAL_POWER,
    )
)


def _readonly_copy(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values)
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)


def exact_mask(box: PreparedBoxBounds) -> np.ndarray:
    """Ordinary exact equality, never proximity or an inferred user equality."""
    return np.isfinite(box.lower) & np.isfinite(box.upper) & (box.lower == box.upper)


@dataclass(frozen=True)
class ExactBoxBinding:
    """One builder-owned variable and its explicit defining fixed-box equality.

    Equality rows follow ``fixed_indices`` in Fortran variable order. Retained
    IDs are CVXPY IDs for reduction-chain identity resolution, not Python IDs.
    """

    family: VariableBoxFamily
    variable_id: int
    variable_shape: tuple[int, ...]
    bounds: PreparedBoxBounds
    fixed_indices: np.ndarray
    equality_id: int

    def __post_init__(self) -> None:
        if self.family not in EXACT_BOX_FAMILIES:
            raise ValueError("only Pg and ND real-power boxes are selectable")
        if self.bounds.lower.shape != self.variable_shape:
            raise ValueError("exact box bounds must match the original variable shape")
        lower = _readonly_copy(self.bounds.lower)
        upper = _readonly_copy(self.bounds.upper)
        if not np.all(np.isfinite(lower)) or not np.all(np.isfinite(upper)):
            raise ValueError("selected box faces must be finite numeric data")
        if np.any(lower > upper):
            raise ValueError("selected box faces must be ordered")
        bounds = PreparedBoxBounds(lower, upper)
        indices = np.asarray(self.fixed_indices)
        expected = np.flatnonzero(exact_mask(bounds).ravel(order="F"))
        if indices.dtype.kind not in "iu" or not np.array_equal(indices, expected):
            raise ValueError("fixed indices must exactly select the prepared box")
        if not indices.size:
            raise ValueError("an exact box binding requires fixed entries")
        for identity in (self.variable_id, self.equality_id):
            if type(identity) is not int or identity <= 0:
                raise ValueError("bindings require positive CVXPY identities")
        object.__setattr__(self, "bounds", bounds)
        object.__setattr__(self, "fixed_indices", _readonly_copy(indices))


@dataclass(frozen=True)
class OperatingSetContribution:
    """Operating constraints plus explicit fixed-box provenance."""

    constraints: tuple[cp.Constraint, ...] = ()
    exact_boxes: tuple[ExactBoxBinding, ...] = ()

    def __post_init__(self) -> None:
        constraints = tuple(self.constraints)
        bindings = tuple(self.exact_boxes)
        identities = {constraint.id for constraint in constraints}
        if any(binding.equality_id not in identities for binding in bindings):
            raise ValueError("defining equalities must belong to the operating set")
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "exact_boxes", bindings)


def prepared_leaf_bounds(box: PreparedBoxBounds, *, exact: bool) -> list[np.ndarray]:
    """Keep non-fixed leaf faces intact; explicit equalities own fixed entries."""
    lower, upper = box.lower.copy(), box.upper.copy()
    if exact:
        mask = exact_mask(box)
        lower[mask], upper[mask] = -np.inf, np.inf
    return [lower, upper]


def emit_exact_box(
    variable: cp.Variable,
    box: PreparedBoxBounds,
    family: VariableBoxFamily,
    representation: BoxRepresentation,
) -> OperatingSetContribution:
    """Emit exact equalities and retain the qualified non-fixed box authority."""
    if family not in EXACT_BOX_FAMILIES or box.lower.shape != variable.shape:
        raise ValueError("selected box family/shape does not match the variable")
    if representation not in ("leaf", "explicit"):
        raise ValueError("unknown box representation")
    flat = cp.reshape(variable, (variable.size,), order="F")
    fixed = np.flatnonzero(exact_mask(box).ravel(order="F"))
    free = np.flatnonzero(~exact_mask(box).ravel(order="F"))
    lower, upper = box.lower.ravel(order="F"), box.upper.ravel(order="F")
    constraints: list[cp.Constraint] = []
    bindings: tuple[ExactBoxBinding, ...] = ()
    if fixed.size:
        equality = flat[fixed] == lower[fixed]
        constraints.append(equality)
        bindings = (
            ExactBoxBinding(
                family, variable.id, variable.shape, box, fixed, equality.id
            ),
        )
    if free.size and representation == "explicit":
        constraints.extend((flat[free] >= lower[free], flat[free] <= upper[free]))
    return OperatingSetContribution(tuple(constraints), bindings)


def finite_vector(value: Any, size: int, label: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != (size,) or not np.isfinite(array).all():
        raise ValueError(f"invalid {label} shape or nonfinite values")
    return array


@dataclass(frozen=True)
class FixedCoordinateMap:
    """Exact substitution x[free]=y, x[fixed]=values and retained row order."""

    full_size: int
    row_count: int
    fixed: np.ndarray
    values: np.ndarray
    dropped: np.ndarray
    _free: np.ndarray = field(init=False, repr=False)
    _kept: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.full_size) is not int or type(self.row_count) is not int:
            raise ValueError("solver dimensions must be integers")
        if self.full_size <= 0 or self.row_count < 0:
            raise ValueError("invalid solver dimensions")
        for name, bound in (("fixed", self.full_size), ("dropped", self.row_count)):
            indices = np.asarray(getattr(self, name))
            if (indices.ndim != 1 or indices.dtype.kind not in "iu"
                    or np.any(indices < 0) or np.any(indices >= bound)
                    or np.unique(indices).size != indices.size):
                raise ValueError(f"invalid {name} indices")
            object.__setattr__(self, name, _readonly_copy(indices))
        if self.dropped.size != self.fixed.size:
            raise ValueError("one defining row is required per fixed coordinate")
        object.__setattr__(self, "values", _readonly_copy(
            finite_vector(self.values, self.fixed.size, "fixed values")))
        if self.fixed.size == self.full_size:
            raise ValueError("empty solver coordinate space is unsupported")
        object.__setattr__(self, "_free", _readonly_copy(np.setdiff1d(np.arange(self.full_size), self.fixed)))
        object.__setattr__(self, "_kept", _readonly_copy(np.setdiff1d(np.arange(self.row_count), self.dropped)))

    @property
    def free(self) -> np.ndarray:
        return self._free

    @property
    def kept(self) -> np.ndarray:
        return self._kept

    def expand(self, reduced: np.ndarray) -> np.ndarray:
        full = np.zeros(self.full_size)
        full[self.free] = finite_vector(reduced, self.free.size, "reduced primal")
        full[self.fixed] = self.values
        return full

    def select(self, full: np.ndarray) -> np.ndarray:
        return finite_vector(full, self.full_size, "full primal")[self.free].copy()


def resolve_fixed_map(
    bindings: Sequence[ExactBoxBinding], inverse_data: Sequence[Any],
    variable_inverse: Any, constraints: Sequence[Any], full_size: int,
    row_count: int,
) -> FixedCoordinateMap:
    """Follow reduction identities; never select arbitrary unary equality rows."""
    rows: dict[int, np.ndarray] = {}
    offset = 0
    for constraint in constraints:
        rows[constraint.id] = np.arange(offset, offset + constraint.size)
        offset += constraint.size
    if offset != row_count:
        raise RuntimeError("canonical constraint layout changed")
    fixed, values, dropped = [], [], []
    for binding in bindings:
        variable_id, equality_id = binding.variable_id, binding.equality_id
        for inverse in inverse_data:
            cons_map = getattr(inverse, "cons_id_map", {})
            if isinstance(inverse, tuple) and len(inverse) == 3:
                new_variables, _, cons_map = inverse
                if variable_id in new_variables:
                    variable_id = new_variables[variable_id].id
            equality_id = cons_map.get(equality_id, equality_id)
        if (variable_id not in variable_inverse.var_offsets
                or variable_inverse.var_shapes[variable_id] != binding.variable_shape
                or equality_id not in rows):
            raise RuntimeError("selected box identity lost during canonicalization")
        selected_rows = rows[equality_id]
        if selected_rows.size != binding.fixed_indices.size:
            raise RuntimeError("defining equality dimensions changed")
        fixed.extend(variable_inverse.var_offsets[variable_id] + binding.fixed_indices)
        values.extend(binding.bounds.lower.ravel(order="F")[binding.fixed_indices])
        dropped.extend(selected_rows)
    return FixedCoordinateMap(full_size, row_count, np.array(fixed, dtype=int),
                              np.array(values, dtype=float), np.array(dropped, dtype=int))


def frozen_record(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Defensive immutable diagnostics, never a live native solver object."""
    def freeze(value: Any) -> Any:
        if isinstance(value, np.ndarray):
            return _readonly_copy(value)
        if isinstance(value, Mapping):
            return frozen_record(value)
        if isinstance(value, (list, tuple)):
            return tuple(freeze(item) for item in value)
        return value
    return MappingProxyType({name: freeze(value) for name, value in record.items()})


@dataclass(frozen=True)
class PreparationEvidence:
    coordinates: FixedCoordinateMap
    native: Mapping[str, Any]
    checks: Mapping[str, Any]
    variable_scale: np.ndarray
    row_scale: np.ndarray
    objective_offset: float = 0.0
    assigned_x0: np.ndarray | None = None
    adjusted_x0: np.ndarray | None = None
    reduced_x0: np.ndarray | None = None
    start_layout: tuple[tuple[int, tuple[int, ...], int, int], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "native", frozen_record(self.native))
        object.__setattr__(self, "checks", frozen_record(self.checks))
        for name in ("variable_scale", "row_scale", "assigned_x0", "adjusted_x0", "reduced_x0"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _readonly_copy(np.asarray(value)))
        for name, size in (("variable_scale", self.coordinates.free.size),
                           ("row_scale", self.coordinates.kept.size)):
            values = finite_vector(getattr(self, name), size, name)
            if np.any(values <= 0):
                raise ValueError("preparation scales must be positive")
        for name, size in (("assigned_x0", self.coordinates.full_size),
                           ("adjusted_x0", self.coordinates.full_size),
                           ("reduced_x0", self.coordinates.free.size)):
            if getattr(self, name) is not None:
                finite_vector(getattr(self, name), size, name)
        if not np.isfinite(self.objective_offset):
            raise ValueError("nonfinite preparation offset")


def clear_prepared_result(build: Any, *, clear_variables: bool = True) -> None:
    """Keep Parameters/assigned starts intact until captured; clear publications."""
    if clear_variables:
        for variable in build.prob.variables():
            variable.save_value(None)
    for constraint in build.prob.constraints:
        for dual in constraint.dual_variables:
            dual.save_value(None)
    build.prob._value = None
    build.prob._status = None
    build.prob._solution = None
    build.prob._solver_stats = None


def prepared_options(build: Any, kwargs: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Closed preflight at the supported stock solver boundaries."""
    options = dict(kwargs)
    expected = cp.CLARABEL if build.is_convex else cp.IPOPT
    if options.pop("solver", expected) != expected:
        raise ValueError(f"prepared execution requires {expected}")
    if options.pop("nlp", not build.is_convex) is not (not build.is_convex):
        raise ValueError("prepared execution requires the formulation's nlp mode")
    for name in ("best_of", "accept_unknown"):
        if name in options:
            raise ValueError(f"prepared execution does not support {name}")
    if options.pop("warm_start", False):
        raise ValueError("prepared warm starts are unsupported")
    if options.get("warm_start_init_point", "no") != "no":
        raise ValueError("prepared IPOPT warm starts are unsupported")
    if not build.is_convex and options.get("hessian_approximation", "exact") != "exact":
        raise ValueError("prepared AC requires exact Hessians")
    verbose = bool(options.pop("verbose", False))
    return verbose, options
