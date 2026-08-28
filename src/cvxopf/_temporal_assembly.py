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
ResultTemporalView = Literal[
    "interval",
    "all_boundaries",
    "post_step_boundaries",
    "horizon",
]
BoxDecisionAuthority = Literal[
    "m14a1_qualified",
    "m14b_qualified",
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
class ResultProjectionSpec:
    """Typed projection from one internal value to its public result layout.

    Vectorized builders retain one time-last CVXPY object.  This schema makes
    the public layout explicit instead of asking result extraction to infer a
    temporal axis from a name or a coincidentally matching shape.
    """

    name: str
    internal_native_shape: tuple[int, ...]
    public_native_shape: tuple[int, ...]
    temporal_view: ResultTemporalView

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("result projection name must be nonempty")
        _validate_native_shape(self.internal_native_shape, allow_zero=True)
        _validate_native_shape(self.public_native_shape, allow_zero=True)
        if self.temporal_view not in {
            "interval",
            "all_boundaries",
            "post_step_boundaries",
            "horizon",
        }:
            raise ValueError(f"invalid result temporal view {self.temporal_view!r}")
        if int(np.prod(self.internal_native_shape, dtype=int)) != int(
            np.prod(self.public_native_shape, dtype=int)
        ):
            raise ValueError(
                "internal and public native result shapes must contain the "
                "same number of coordinates"
            )
        remaining_public = iter(self.public_native_shape)
        expected_public = next(remaining_public, None)
        for dimension in self.internal_native_shape:
            if dimension == expected_public:
                expected_public = next(remaining_public, None)
            elif dimension != 1:
                break
        else:
            if expected_public is None:
                return
        raise ValueError(
            "public native result shape must be obtained from the internal "
            "native shape only by removing singleton axes"
        )

    def internal_shape(self, horizon_steps: int) -> tuple[int, ...]:
        """Return the exact solved-value shape required by this projection."""
        _validate_horizon_steps(horizon_steps)
        if self.temporal_view == "horizon":
            return self.internal_native_shape
        length = horizon_steps + (
            self.temporal_view in {"all_boundaries", "post_step_boundaries"}
        )
        return self.internal_native_shape + (length,)

    def public_shape(self, horizon_steps: int) -> tuple[int, ...]:
        """Return the exact public result shape produced by this projection."""
        _validate_horizon_steps(horizon_steps)
        if self.temporal_view == "horizon":
            return self.public_native_shape
        length = horizon_steps + (self.temporal_view == "all_boundaries")
        return (length,) + self.public_native_shape

    def project(self, values: np.ndarray, horizon_steps: int) -> np.ndarray:
        """Validate and project a solved time-last value exactly once."""
        array = np.asarray(values)
        expected = self.internal_shape(horizon_steps)
        if array.shape != expected:
            raise ValueError(
                f"result source {self.name!r} must have internal shape "
                f"{expected}, got {array.shape}"
            )
        if self.temporal_view == "horizon":
            return np.reshape(array, self.public_native_shape)
        if self.temporal_view == "post_step_boundaries":
            array = array[..., 1:]
        time_first = np.moveaxis(array, -1, 0)
        return np.reshape(time_first, self.public_shape(horizon_steps))


@dataclass(frozen=True)
class ResultProjectionRegistry:
    """Immutable source-specific projection registry for one OPF build."""

    variables: Mapping[str, ResultProjectionSpec] = field(default_factory=dict)
    expressions: Mapping[str, ResultProjectionSpec] = field(default_factory=dict)

    def __post_init__(self) -> None:
        variables = dict(self.variables)
        expressions = dict(self.expressions)
        for source_kind, projections in (
            ("variable", variables),
            ("expression", expressions),
        ):
            mismatches = sorted(
                name
                for name, projection in projections.items()
                if projection.name != name
            )
            if mismatches:
                raise ValueError(
                    f"{source_kind} result projection keys must match their "
                    f"declared names: {mismatches}"
                )
        object.__setattr__(self, "variables", MappingProxyType(variables))
        object.__setattr__(self, "expressions", MappingProxyType(expressions))

    def projection_for(
        self,
        source_kind: Literal["variable", "expression"],
        name: str,
    ) -> ResultProjectionSpec:
        """Return one required projection with a source-specific error."""
        if source_kind not in {"variable", "expression"}:
            raise ValueError(f"unknown result projection source {source_kind!r}")
        projections = self.variables if source_kind == "variable" else self.expressions
        try:
            return projections[name]
        except KeyError as error:
            raise ValueError(
                f"vectorized result {source_kind} {name!r} has no declared "
                "public projection"
            ) from error

    @property
    def is_empty(self) -> bool:
        """Return whether no source has a public projection."""
        return not self.variables and not self.expressions


def merge_result_projection_registries(
    *registries: ResultProjectionRegistry,
) -> ResultProjectionRegistry:
    """Merge disjoint registries without silently replacing a declaration."""
    variables: dict[str, ResultProjectionSpec] = {}
    expressions: dict[str, ResultProjectionSpec] = {}
    for registry in registries:
        for source_name, source, destination in (
            ("variable", registry.variables, variables),
            ("expression", registry.expressions, expressions),
        ):
            duplicates = set(destination).intersection(source)
            if duplicates:
                raise ValueError(
                    f"duplicate {source_name} result projections: {sorted(duplicates)}"
                )
            destination.update(source)
    return ResultProjectionRegistry(variables=variables, expressions=expressions)


@dataclass(frozen=True)
class HorizonVariableSpec:
    """Declarative schema for one interval or boundary CVXPY variable."""

    name: str
    native_shape: tuple[int, ...]
    temporal_class: Literal["interval", "boundary"] = "interval"
    attributes: Mapping[str, object] = field(default_factory=dict)
    public_native_shape: tuple[int, ...] | None = None
    result_view: (
        Literal["interval", "all_boundaries", "post_step_boundaries"] | None
    ) = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("horizon variable name must be nonempty")
        _validate_native_shape(self.native_shape, allow_zero=False)
        if self.temporal_class not in {"interval", "boundary"}:
            raise ValueError("horizon variables must be interval or boundary")
        public_native_shape = (
            self.native_shape
            if self.public_native_shape is None
            else self.public_native_shape
        )
        _validate_native_shape(public_native_shape, allow_zero=False)
        view = self.result_view
        if view is None:
            if self.temporal_class == "boundary":
                raise ValueError(
                    "boundary variables require an explicit public result view"
                )
            view = "interval"
        if self.temporal_class == "interval" and view != "interval":
            raise ValueError("interval variables require an interval result view")
        if self.temporal_class == "boundary" and view not in {
            "all_boundaries",
            "post_step_boundaries",
        }:
            raise ValueError("boundary variables require a boundary result view")
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))
        object.__setattr__(self, "public_native_shape", public_native_shape)
        object.__setattr__(self, "result_view", view)
        ResultProjectionSpec(
            self.name,
            self.native_shape,
            public_native_shape,
            view,
        )

    def shape(self, horizon_steps: int) -> tuple[int, ...]:
        """Return the variable's time-last horizon shape."""
        return TemporalFieldSpec(
            self.name, self.native_shape, self.temporal_class
        ).internal_shape(horizon_steps)

    def result_projection(self) -> ResultProjectionSpec:
        """Return the public projection carried by this variable schema."""
        assert self.public_native_shape is not None
        assert self.result_view is not None
        return ResultProjectionSpec(
            self.name,
            self.native_shape,
            self.public_native_shape,
            self.result_view,
        )


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


def _m14b_qualified_decisions(
    formulation: Formulation,
    families: tuple[VariableBoxFamily, ...],
) -> dict[tuple[Formulation, VariableBoxFamily], BoxRepresentationDecision]:
    return {
        (formulation, family): _decision("leaf", "m14b_qualified")
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
        **_m14b_qualified_decisions(
            "lossy_dc",
            (
                VariableBoxFamily.STORAGE_REAL_POWER,
                VariableBoxFamily.STORAGE_SOC,
                VariableBoxFamily.NONDISPATCHABLE_REAL_POWER,
                VariableBoxFamily.HVDC_INPUT_POWER,
                VariableBoxFamily.LOAD_SHED_FRACTION,
            ),
        ),
        **_m14b_qualified_decisions(
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
    return tuple(
        dict.fromkeys(family for _, family in pending_component_box_pairs())
    )


def pending_component_box_pairs() -> tuple[tuple[Formulation, VariableBoxFamily], ...]:
    """Return only physically applicable pending formulation/box pairs."""
    return tuple(
        pair
        for pair, decision in _BOX_REPRESENTATIONS.items()
        if decision.requires_focused_qualification
    )
