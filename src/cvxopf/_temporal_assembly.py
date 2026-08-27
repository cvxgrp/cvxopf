"""Typed internal contracts for horizon-vectorized model assembly.

This module contains representation mechanics only. It does not own component
physics or formulation equations. The public stepwise builders remain the
default while M14b composes these contracts into the vectorized path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Literal, Mapping

import numpy as np

Formulation = Literal["ac", "lossy_dc", "singlenode_dc"]
TemporalClass = Literal["static", "interval", "boundary"]
BoxRepresentation = Literal["explicit", "leaf"]
BoxDecisionAuthority = Literal[
    "m14a1_qualified",
    "existing_production",
    "pending_component_gate",
    "ac_explicit_policy",
]


def _validate_horizon_steps(horizon_steps: int) -> None:
    if (
        not isinstance(horizon_steps, int)
        or isinstance(horizon_steps, bool)
        or horizon_steps <= 0
    ):
        raise ValueError("horizon_steps must be a positive integer")


def _validate_native_shape(native_shape: tuple[int, ...], *, allow_zero: bool) -> None:
    if any(
        not isinstance(dimension, int)
        or isinstance(dimension, bool)
        or dimension < (0 if allow_zero else 1)
        for dimension in native_shape
    ):
        qualifier = "nonnegative" if allow_zero else "positive"
        raise ValueError(f"native_shape dimensions must be {qualifier} integers")


@dataclass(frozen=True)
class TemporalFieldSpec:
    """One schema-owned static, interval, or boundary model field."""

    name: str
    native_shape: tuple[int, ...]
    temporal_class: TemporalClass

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("temporal field name must be nonempty")
        _validate_native_shape(self.native_shape, allow_zero=True)
        if self.temporal_class not in {"static", "interval", "boundary"}:
            raise ValueError(f"invalid temporal class {self.temporal_class!r}")

    def internal_shape(self, horizon_steps: int) -> tuple[int, ...]:
        """Return the native or time-last internal shape."""
        _validate_horizon_steps(horizon_steps)
        if self.temporal_class == "static":
            return self.native_shape
        extra = horizon_steps + (self.temporal_class == "boundary")
        return self.native_shape + (extra,)

    def public_shape(self, horizon_steps: int) -> tuple[int, ...]:
        """Return the retained time-first public input/result shape."""
        _validate_horizon_steps(horizon_steps)
        if self.temporal_class == "static":
            return self.native_shape
        extra = horizon_steps + (self.temporal_class == "boundary")
        return (extra,) + self.native_shape

    def to_internal(self, values: np.ndarray, horizon_steps: int) -> np.ndarray:
        """Validate one public array and move its temporal axis exactly once."""
        array = np.asarray(values)
        expected = self.public_shape(horizon_steps)
        if array.shape != expected:
            raise ValueError(
                f"field {self.name!r} must have public shape {expected}, "
                f"got {array.shape}"
            )
        if self.temporal_class == "static":
            return array
        return np.moveaxis(array, 0, -1)

    def to_public(self, values: np.ndarray, horizon_steps: int) -> np.ndarray:
        """Validate one internal array and restore its public time-first axis."""
        array = np.asarray(values)
        expected = self.internal_shape(horizon_steps)
        if array.shape != expected:
            raise ValueError(
                f"field {self.name!r} must have internal shape {expected}, "
                f"got {array.shape}"
            )
        if self.temporal_class == "static":
            return array
        return np.moveaxis(array, -1, 0)


@dataclass(frozen=True)
class HorizonVariableSpec:
    """Declarative schema for one interval or boundary CVXPY variable."""

    name: str
    native_shape: tuple[int, ...]
    temporal_class: Literal["interval", "boundary"] = "interval"
    attributes: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("horizon variable name must be nonempty")
        _validate_native_shape(self.native_shape, allow_zero=False)
        if self.temporal_class not in {"interval", "boundary"}:
            raise ValueError("horizon variables must be interval or boundary")
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    def shape(self, horizon_steps: int) -> tuple[int, ...]:
        """Return the variable's time-last horizon shape."""
        return TemporalFieldSpec(
            self.name, self.native_shape, self.temporal_class
        ).internal_shape(horizon_steps)


def broadcast_static_bound(
    values: np.ndarray | float,
    *,
    native_shape: tuple[int, ...],
    horizon_steps: int,
    temporal_class: Literal["interval", "boundary"] = "interval",
) -> np.ndarray:
    """Expose static bound data at exact time-last shape without tiling it."""
    _validate_native_shape(native_shape, allow_zero=False)
    _validate_horizon_steps(horizon_steps)
    if temporal_class not in {"interval", "boundary"}:
        raise ValueError("bound temporal class must be interval or boundary")
    source = np.asarray(values)
    if source.shape != native_shape:
        raise ValueError(
            f"static bound must have native shape {native_shape}, got {source.shape}"
        )
    extra = horizon_steps + (temporal_class == "boundary")
    return np.broadcast_to(source[..., np.newaxis], native_shape + (extra,))


@dataclass(frozen=True)
class PreparedBoxBounds:
    """Validated lower/upper arrays at one exact horizon-variable shape."""

    lower: np.ndarray
    upper: np.ndarray

    def __post_init__(self) -> None:
        if self.lower.shape != self.upper.shape:
            raise ValueError("prepared lower and upper bounds must align")
        if self.lower.flags.writeable or self.upper.flags.writeable:
            raise ValueError("prepared bounds must be read-only")


def _prepare_box_face(
    values: np.ndarray | float,
    *,
    face_name: Literal["lower", "upper"],
    native_shape: tuple[int, ...],
    horizon_steps: int,
    data_temporal_class: TemporalClass,
    variable_temporal_class: Literal["interval", "boundary"],
) -> np.ndarray:
    """Normalize one bound face to the target variable's time-last shape."""
    if variable_temporal_class not in {"interval", "boundary"}:
        raise ValueError("box variable temporal class must be interval or boundary")
    if data_temporal_class == "static":
        internal = broadcast_static_bound(
            values,
            native_shape=native_shape,
            horizon_steps=horizon_steps,
            temporal_class=variable_temporal_class,
        )
    else:
        if data_temporal_class != variable_temporal_class:
            raise ValueError(
                f"time-varying {face_name} bound data must match the target "
                "variable temporal class"
            )
        field = TemporalFieldSpec(
            f"{face_name} box bound", native_shape, data_temporal_class
        )
        internal = field.to_internal(np.asarray(values), horizon_steps)
    if not np.all(np.isfinite(internal)):
        raise ValueError(f"{face_name} bounds must contain only finite values")
    readonly = np.asarray(internal).view()
    readonly.flags.writeable = False
    return readonly


def prepare_box_bounds(
    lower: np.ndarray | float,
    upper: np.ndarray | float,
    *,
    native_shape: tuple[int, ...],
    horizon_steps: int,
    lower_temporal_class: TemporalClass,
    upper_temporal_class: TemporalClass,
    variable_temporal_class: Literal["interval", "boundary"],
) -> PreparedBoxBounds:
    """Normalize independently temporal box faces to one variable shape."""
    lower_internal = _prepare_box_face(
        lower,
        face_name="lower",
        native_shape=native_shape,
        horizon_steps=horizon_steps,
        data_temporal_class=lower_temporal_class,
        variable_temporal_class=variable_temporal_class,
    )
    upper_internal = _prepare_box_face(
        upper,
        face_name="upper",
        native_shape=native_shape,
        horizon_steps=horizon_steps,
        data_temporal_class=upper_temporal_class,
        variable_temporal_class=variable_temporal_class,
    )
    if np.any(lower_internal > upper_internal):
        raise ValueError("lower bounds must not exceed upper bounds")
    return PreparedBoxBounds(lower_internal, upper_internal)


class VariableBoxFamily(Enum):
    """Closed set of independently represented elementwise operating boxes."""

    DISPATCHABLE_P = "dispatchable_p"
    DISPATCHABLE_Q = "dispatchable_q"
    AC_VOLTAGE = "ac_voltage"
    DC_BRANCH_FLOW = "dc_branch_flow"
    STORAGE_REAL_POWER = "storage_real_power"
    STORAGE_SOC = "storage_soc"
    NONDISPATCHABLE_REAL_POWER = "nondispatchable_real_power"
    HVDC_INPUT_POWER = "hvdc_input_power"
    LOAD_SHED_FRACTION = "load_shed_fraction"


@dataclass(frozen=True)
class BoxRepresentationDecision:
    """Frozen representation selected for one formulation/box family."""

    representation: BoxRepresentation
    authority: BoxDecisionAuthority
    requires_focused_qualification: bool = False


def _decision(
    representation: BoxRepresentation,
    authority: BoxDecisionAuthority,
    *,
    pending: bool = False,
) -> BoxRepresentationDecision:
    return BoxRepresentationDecision(representation, authority, pending)


def _pending_decisions(
    formulation: Formulation,
    families: tuple[VariableBoxFamily, ...],
) -> dict[tuple[Formulation, VariableBoxFamily], BoxRepresentationDecision]:
    return {
        (formulation, family): _decision(
            "explicit", "pending_component_gate", pending=True
        )
        for family in families
    }


_BOX_REPRESENTATIONS: Mapping[
    tuple[Formulation, VariableBoxFamily], BoxRepresentationDecision
] = MappingProxyType(
    {
        ("lossy_dc", VariableBoxFamily.DISPATCHABLE_P): _decision(
            "leaf", "m14a1_qualified"
        ),
        ("lossy_dc", VariableBoxFamily.DC_BRANCH_FLOW): _decision(
            "leaf", "m14a1_qualified"
        ),
        ("singlenode_dc", VariableBoxFamily.DISPATCHABLE_P): _decision(
            "leaf", "m14a1_qualified"
        ),
        ("ac", VariableBoxFamily.DISPATCHABLE_P): _decision(
            "explicit", "ac_explicit_policy"
        ),
        ("ac", VariableBoxFamily.DISPATCHABLE_Q): _decision(
            "explicit", "ac_explicit_policy"
        ),
        ("ac", VariableBoxFamily.AC_VOLTAGE): _decision("leaf", "existing_production"),
        **_pending_decisions(
            "lossy_dc",
            (
                VariableBoxFamily.STORAGE_REAL_POWER,
                VariableBoxFamily.STORAGE_SOC,
                VariableBoxFamily.NONDISPATCHABLE_REAL_POWER,
                VariableBoxFamily.HVDC_INPUT_POWER,
                VariableBoxFamily.LOAD_SHED_FRACTION,
            ),
        ),
        **_pending_decisions(
            "singlenode_dc",
            (
                VariableBoxFamily.STORAGE_REAL_POWER,
                VariableBoxFamily.STORAGE_SOC,
                VariableBoxFamily.NONDISPATCHABLE_REAL_POWER,
                VariableBoxFamily.LOAD_SHED_FRACTION,
            ),
        ),
        **{
            ("ac", family): _decision("explicit", "ac_explicit_policy")
            for family in (
                VariableBoxFamily.STORAGE_SOC,
                VariableBoxFamily.NONDISPATCHABLE_REAL_POWER,
                VariableBoxFamily.HVDC_INPUT_POWER,
                VariableBoxFamily.LOAD_SHED_FRACTION,
            )
        },
    }
)


def box_representation_decision(
    formulation: Formulation,
    family: VariableBoxFamily,
) -> BoxRepresentationDecision:
    """Return one frozen decision, rejecting inapplicable combinations."""
    try:
        return _BOX_REPRESENTATIONS[(formulation, family)]
    except KeyError as error:
        raise ValueError(
            f"box family {family.value!r} does not apply to formulation {formulation!r}"
        ) from error


def pending_component_box_families() -> tuple[VariableBoxFamily, ...]:
    """Return the component-owned box families requiring focused gates."""
    return (
        VariableBoxFamily.STORAGE_REAL_POWER,
        VariableBoxFamily.STORAGE_SOC,
        VariableBoxFamily.NONDISPATCHABLE_REAL_POWER,
        VariableBoxFamily.HVDC_INPUT_POWER,
        VariableBoxFamily.LOAD_SHED_FRACTION,
    )


def pending_component_box_pairs() -> tuple[tuple[Formulation, VariableBoxFamily], ...]:
    """Return only physically applicable pending formulation/box pairs."""
    return tuple(
        pair
        for pair, decision in _BOX_REPRESENTATIONS.items()
        if decision.requires_focused_qualification
    )
