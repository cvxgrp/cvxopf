"""Build-time numerical representation policy and component-owned box evidence.

Solver-coordinate transformations are deliberately not enabled by this assembly
checkpoint. No variable names or arbitrary user equalities select fixed boxes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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

    Enabled graphs are inspectable, but prepared solving is unavailable until
    the solver-boundary checkpoint implements restoration and attempt evidence.
    """

    normalize_device_limits: bool = False
    exact_fixed_boxes: bool = False
    canonical_scaling: Literal["none", "joint5"] = "none"

    def __post_init__(self) -> None:
        for name in ("normalize_device_limits", "exact_fixed_boxes"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a bool")
        if not isinstance(
            self.canonical_scaling, str
        ) or self.canonical_scaling not in ("none", "joint5"):
            raise ValueError("canonical_scaling must be 'none' or 'joint5'")

    @property
    def enabled(self) -> bool:
        return (
            self.normalize_device_limits
            or self.exact_fixed_boxes
            or self.canonical_scaling != "none"
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
