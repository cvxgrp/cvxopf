"""Private typed contracts for component contributions and assembly.

This module defines the formulation-independent vocabulary and invariants used
by every component adapter. It contains no device-specific physics; concrete
bindings over the authoritative device modules live in ``_component_adapters``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from numbers import Real
from types import MappingProxyType
from typing import (
    Generic,
    Literal,
    Mapping,
    Protocol,
    Sequence,
    TypeVar,
)

import cvxpy as cp


Formulation = Literal["ac", "lossy_dc", "singlenode_dc"]
UnitT = TypeVar("UnitT")
UnitT_contra = TypeVar("UnitT_contra", contravariant=True)
InputT = TypeVar("InputT")
InputT_contra = TypeVar("InputT_contra", contravariant=True)
KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")


def _readonly(
    values: Mapping[KeyT, ValueT],
) -> Mapping[KeyT, ValueT]:
    """Defensively copy a mapping behind a read-only interface."""
    return MappingProxyType(dict(values))


def _validate_positive_real(name: str, value: object) -> None:
    """Require a finite, strictly positive real scalar."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real scalar")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0")


class FormulationCapability(Enum):
    """A component's relationship to one formulation."""

    ACTIVE = "active"
    NULL = "null"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class PreparationContext:
    """Formulation-independent inputs available during component preparation."""

    base_mva: float
    nb: int
    ext_to_int: Mapping[int, int]
    ext_bus_ids: frozenset[int]
    horizon_steps: int
    delta: float
    is_multistep: bool | None = None

    def __post_init__(self) -> None:
        _validate_positive_real("base_mva", self.base_mva)
        if (
            not isinstance(self.nb, int)
            or isinstance(self.nb, bool)
            or self.nb <= 0
        ):
            raise ValueError("nb must be a positive integer")
        if (
            not isinstance(self.horizon_steps, int)
            or isinstance(self.horizon_steps, bool)
            or self.horizon_steps <= 0
        ):
            raise ValueError("horizon_steps must be a positive integer")
        _validate_positive_real("delta", self.delta)
        if self.horizon_steps > 1 and self.is_multistep is False:
            raise ValueError(
                "is_multistep must be True when horizon_steps > 1"
            )
        object.__setattr__(self, "ext_to_int", _readonly(self.ext_to_int))
        if self.is_multistep is None:
            object.__setattr__(
                self, "is_multistep", self.horizon_steps > 1
            )


@dataclass(frozen=True)
class ACNetworkState:
    """AC network state currently exposed to device-to-network constraints."""

    voltage: cp.Variable
    controlled_buses: tuple[int, ...]
    enforce_vset: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "controlled_buses", tuple(self.controlled_buses)
        )


@dataclass(frozen=True)
class DCNetworkState:
    """Explicit empty network state for DC component constraints."""


NetworkState = ACNetworkState | DCNetworkState


@dataclass(frozen=True)
class StepContext:
    """Typed build and network state for one component assembly step."""

    formulation: Formulation
    step: int
    base_mva: float
    ext_to_int: Mapping[int, int]
    network_state: NetworkState

    def __post_init__(self) -> None:
        if not isinstance(self.step, int) or isinstance(self.step, bool):
            raise ValueError("step must be a nonnegative integer")
        if self.step < 0:
            raise ValueError("step must be a nonnegative integer")
        _validate_positive_real("base_mva", self.base_mva)
        if self.formulation == "ac":
            if not isinstance(self.network_state, ACNetworkState):
                raise ValueError(
                    "formulation='ac' requires ACNetworkState"
                )
        elif not isinstance(self.network_state, DCNetworkState):
            raise ValueError(
                f"formulation={self.formulation!r} requires DCNetworkState"
            )
        object.__setattr__(self, "ext_to_int", _readonly(self.ext_to_int))


@dataclass(frozen=True)
class HorizonContext:
    """Global temporal inputs exposed after per-step assembly."""

    formulation: Formulation
    horizon_steps: int
    delta: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.horizon_steps, int)
            or isinstance(self.horizon_steps, bool)
            or self.horizon_steps <= 0
        ):
            raise ValueError("horizon_steps must be a positive integer")
        _validate_positive_real("delta", self.delta)


@dataclass(frozen=True)
class VariableSpec:
    """Declarative request for a builder-owned CVXPY variable."""

    name: str
    shape: tuple[int, ...]
    attributes: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("variable specification name must be nonempty")
        object.__setattr__(self, "attributes", _readonly(self.attributes))


@dataclass(frozen=True)
class InjectionContribution:
    """Bus-scattered nodal injection channels in per-unit network units.

    ``p_pu`` and ``q_pu`` have shape ``(nb,)`` and use positive sign for
    injection into the network. A component whose decision variables use
    engineering units constructs these expressions with an unbound
    ``inv_base_mva`` parameter and returns it here. Components whose variables
    already use per-unit quantities return ``None``. The shared assembler,
    which owns the network base, binds every returned parameter exactly once.
    """

    p_pu: cp.Expression | None
    q_pu: cp.Expression | None
    inv_base_mva: cp.Parameter | None = None


def bind_injection_scale(
    contribution: InjectionContribution,
    base_mva: float,
) -> None:
    """Bind one component-created inverse-base parameter in assembler scope."""
    if not math.isfinite(base_mva) or base_mva <= 0:
        raise ValueError(f"base_mva must be finite and > 0, got {base_mva}")
    parameter = contribution.inv_base_mva
    if parameter is None:
        return
    if parameter.value is not None:
        raise ValueError("injection scaling parameter is already bound")
    parameter.value = 1.0 / base_mva


@dataclass(frozen=True)
class StepContribution:
    """Normalized per-step output assembled from explicit component hooks."""

    variables: Mapping[str, cp.Variable]
    injection: InjectionContribution
    operating_constraints: tuple[cp.Constraint, ...] = ()
    network_constraints: tuple[cp.Constraint, ...] = ()
    cost: cp.Expression | None = None
    cost_expression_name: str | None = None
    expressions: Mapping[str, cp.Expression] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "variables", _readonly(self.variables))
        object.__setattr__(
            self,
            "operating_constraints",
            tuple(self.operating_constraints),
        )
        object.__setattr__(
            self,
            "network_constraints",
            tuple(self.network_constraints),
        )
        object.__setattr__(self, "expressions", _readonly(self.expressions))


@dataclass(frozen=True)
class HorizonContribution:
    """Normalized cross-step and terminal output for one component."""

    constraints: tuple[cp.Constraint, ...] = ()
    terminal_cost: cp.Expression | None = None
    expressions: Mapping[str, cp.Expression] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "expressions", _readonly(self.expressions))


class PrepareHook(Protocol[UnitT_contra, InputT_contra]):
    """Validate and vectorize one component collection."""

    def __call__(
        self,
        units: Sequence[UnitT_contra],
        inputs: InputT_contra,
        context: PreparationContext,
    ) -> Mapping[str, object]: ...


class MetadataHook(Protocol):
    """Select prepared fields published through ``OPFBuild.data``."""

    def __call__(
        self,
        prepared: Mapping[str, object],
        formulation: Formulation,
    ) -> Mapping[str, object]: ...


class VariableSpecHook(Protocol[UnitT_contra]):
    """Describe variables that remain owned and created by a builder."""

    def __call__(
        self,
        units: Sequence[UnitT_contra],
        prepared: Mapping[str, object],
        context: StepContext,
    ) -> tuple[VariableSpec, ...]: ...


class InjectionHook(Protocol[UnitT_contra]):
    """Return the component's typed nodal injection channels."""

    def __call__(
        self,
        units: Sequence[UnitT_contra],
        prepared: Mapping[str, object],
        variables: Mapping[str, cp.Variable],
        context: StepContext,
    ) -> InjectionContribution: ...


class ConstraintHook(Protocol[UnitT_contra]):
    """Return operating or device-to-network constraints for one step."""

    def __call__(
        self,
        units: Sequence[UnitT_contra],
        prepared: Mapping[str, object],
        variables: Mapping[str, cp.Variable],
        context: StepContext,
    ) -> tuple[cp.Constraint, ...]: ...


class StepCostHook(Protocol[UnitT_contra]):
    """Return one component's per-step cost contribution."""

    def __call__(
        self,
        units: Sequence[UnitT_contra],
        prepared: Mapping[str, object],
        variables: Mapping[str, cp.Variable],
        context: StepContext,
    ) -> cp.Expression | None: ...


class StepExpressionHook(Protocol[UnitT_contra]):
    """Return named per-step expressions retained for result reporting."""

    def __call__(
        self,
        units: Sequence[UnitT_contra],
        prepared: Mapping[str, object],
        variables: Mapping[str, cp.Variable],
        context: StepContext,
    ) -> Mapping[str, cp.Expression]: ...


class HorizonHook(Protocol[UnitT_contra]):
    """Return coupling constraints and terminal contributions once per horizon."""

    def __call__(
        self,
        units: Sequence[UnitT_contra],
        prepared: Mapping[str, object],
        variable_history: Mapping[str, Sequence[cp.Variable]],
        context: HorizonContext,
    ) -> HorizonContribution: ...


@dataclass(frozen=True)
class FormulationAdapter(Generic[UnitT]):
    """Explicit component hooks for one formulation capability."""

    capability: FormulationCapability
    variable_specs: VariableSpecHook[UnitT] | None = None
    injections: InjectionHook[UnitT] | None = None
    operating_constraints: ConstraintHook[UnitT] | None = None
    network_constraints: ConstraintHook[UnitT] | None = None
    step_cost: StepCostHook[UnitT] | None = None
    step_expressions: StepExpressionHook[UnitT] | None = None
    horizon: HorizonHook[UnitT] | None = None

    def __post_init__(self) -> None:
        required = (
            self.variable_specs,
            self.injections,
            self.operating_constraints,
            self.horizon,
        )
        if self.capability is FormulationCapability.ACTIVE:
            if any(hook is None for hook in required):
                raise ValueError(
                    "active formulation adapters require variable, injection, "
                    "operating-constraint, and horizon hooks"
                )
        elif any(
            hook is not None
            for hook in (
                *required,
                self.network_constraints,
                self.step_cost,
                self.step_expressions,
            )
        ):
            raise ValueError(
                "null and unsupported formulation adapters cannot define hooks"
            )


@dataclass(frozen=True)
class ComponentAdapter(Generic[UnitT, InputT]):
    """Typed internal contract for one component family."""

    name: str
    prepare: PrepareHook[UnitT, InputT]
    metadata: MetadataHook
    formulations: Mapping[Formulation, FormulationAdapter[UnitT]]
    cost_expression_name: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("component adapter name must be nonempty")
        if (
            self.cost_expression_name is not None
            and (
                not isinstance(self.cost_expression_name, str)
                or not self.cost_expression_name
            )
        ):
            raise ValueError(
                "component cost expression name must be a nonempty string"
            )
        expected = {"ac", "lossy_dc", "singlenode_dc"}
        if set(self.formulations) != expected:
            raise ValueError(
                "component adapter formulations must contain exactly "
                f"{sorted(expected)}"
            )
        object.__setattr__(
            self, "formulations", _readonly(self.formulations)
        )


@dataclass(frozen=True)
class PreparedComponent(Generic[UnitT, InputT]):
    """Validated component collection with read-only mapping structure."""

    adapter: ComponentAdapter[UnitT, InputT]
    units: tuple[UnitT, ...]
    data: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "units", tuple(self.units))
        object.__setattr__(self, "data", _readonly(self.data))
