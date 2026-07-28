"""Private typed contracts for component contributions and assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
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
KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")


def _readonly(
    values: Mapping[KeyT, ValueT],
) -> Mapping[KeyT, ValueT]:
    """Defensively copy a mapping behind a read-only interface."""
    return MappingProxyType(dict(values))


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "ext_to_int", _readonly(self.ext_to_int))


@dataclass(frozen=True)
class ACNetworkState:
    """AC network state currently exposed to device-to-network constraints."""

    voltage: cp.Variable
    controlled_buses: tuple[int, ...]
    enforce_vset: bool


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
        object.__setattr__(self, "ext_to_int", _readonly(self.ext_to_int))


@dataclass(frozen=True)
class HorizonContext:
    """Global temporal inputs exposed after per-step assembly."""

    formulation: Formulation
    horizon_steps: int
    delta: float


@dataclass(frozen=True)
class VariableSpec:
    """Declarative request for a builder-owned CVXPY variable."""

    name: str
    shape: tuple[int, ...]
    attributes: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", _readonly(self.attributes))


@dataclass(frozen=True)
class InjectionContribution:
    """Bus-scattered nodal injection channels in engineering units.

    ``p`` is an ``(nb,)`` expression in MW and ``q``, when present, is an
    ``(nb,)`` expression in MVAr. Positive values inject power into the
    network. Components do not apply per-unit scaling. The shared assembler
    alone creates and binds one ``1 / baseMVA`` parameter and converts these
    expressions before the formulation constructs its nodal balance.
    """

    p: cp.Expression | None
    q: cp.Expression | None


@dataclass(frozen=True)
class StepContribution:
    """Normalized per-step output assembled from explicit component hooks."""

    variables: Mapping[str, cp.Variable]
    injection: InjectionContribution
    operating_constraints: tuple[cp.Constraint, ...] = ()
    network_constraints: tuple[cp.Constraint, ...] = ()
    cost: cp.Expression | None = None
    expressions: Mapping[str, cp.Expression] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "variables", _readonly(self.variables))
        object.__setattr__(self, "expressions", _readonly(self.expressions))


@dataclass(frozen=True)
class HorizonContribution:
    """Normalized cross-step and terminal output for one component."""

    constraints: tuple[cp.Constraint, ...] = ()
    terminal_cost: cp.Expression | None = None
    expressions: Mapping[str, cp.Expression] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "expressions", _readonly(self.expressions))


class PrepareHook(Protocol[UnitT_contra]):
    """Validate and vectorize one component collection."""

    def __call__(
        self,
        units: Sequence[UnitT_contra],
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
    ) -> cp.Expression: ...


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
            )
        ):
            raise ValueError(
                "null and unsupported formulation adapters cannot define hooks"
            )


@dataclass(frozen=True)
class ComponentAdapter(Generic[UnitT]):
    """Typed internal contract for one component family."""

    name: str
    prepare: PrepareHook[UnitT]
    metadata: MetadataHook
    formulations: Mapping[Formulation, FormulationAdapter[UnitT]]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("component adapter name must be nonempty")
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
class PreparedComponent(Generic[UnitT]):
    """Validated component collection with read-only mapping structure."""

    adapter: ComponentAdapter[UnitT]
    units: tuple[UnitT, ...]
    data: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", _readonly(self.data))
