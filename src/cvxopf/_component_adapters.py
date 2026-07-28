"""Typed adapters over the existing generator and ND component functions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence, cast

import cvxpy as cp
import numpy as np

from cvxopf import generator, nondispatchable
from cvxopf._component_adapter import (
    ACNetworkState,
    ComponentAdapter,
    Formulation,
    FormulationAdapter,
    FormulationCapability,
    HorizonContext,
    HorizonContribution,
    InjectionContribution,
    PreparationContext,
    StepContext,
    VariableSpec,
)
from cvxopf.generator import DispatchableGenerator
from cvxopf.nondispatchable import NondispatchableUnit


def _array(prepared: Mapping[str, object], key: str) -> np.ndarray:
    """Return one prepared numerical array under the typed adapter contract."""
    return cast(np.ndarray, prepared[key])


def _generator_prepare(
    units: Sequence[DispatchableGenerator],
    inputs: None,
    context: PreparationContext,
) -> Mapping[str, object]:
    return generator._prepare_data(
        list(units),
        context.base_mva,
        context.nb,
        dict(context.ext_to_int),
        set(context.ext_bus_ids),
    )


def _generator_metadata(
    prepared: Mapping[str, object],
    formulation: Formulation,
) -> Mapping[str, object]:
    return generator._build_metadata(
        dict(prepared), reactive=formulation == "ac"
    )


def _generator_variable_specs(
    units: Sequence[DispatchableGenerator],
    prepared: Mapping[str, object],
    context: StepContext,
) -> tuple[VariableSpec, ...]:
    shape = (cast(int, prepared["ng"]),)
    specs = [VariableSpec("Pg", shape)]
    if context.formulation == "ac":
        specs.append(VariableSpec("Qg", shape))
    return tuple(specs)


def _generator_injections(
    units: Sequence[DispatchableGenerator],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> InjectionContribution:
    if context.formulation == "ac":
        p_pu, q_pu, inv_base_mva = generator.ac_injections(
            list(units),
            variables["Pg"],
            variables["Qg"],
            dict(context.ext_to_int),
            incidence=_array(prepared, "Cg"),
        )
    else:
        p_pu, q_pu, inv_base_mva = generator.dc_injections(
            list(units),
            variables["Pg"],
            dict(context.ext_to_int),
            incidence=_array(prepared, "Cg"),
        )
    return InjectionContribution(p_pu, q_pu, inv_base_mva)


def _generator_operating_constraints(
    units: Sequence[DispatchableGenerator],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> tuple[cp.Constraint, ...]:
    if context.formulation == "ac":
        constraints = generator.ac_operating_constraints(
            variables["Pg"],
            variables["Qg"],
            _array(prepared, "Pgmin"),
            _array(prepared, "Pgmax"),
            _array(prepared, "Qgmin"),
            _array(prepared, "Qgmax"),
        )
    else:
        constraints = generator.dc_operating_constraints(
            variables["Pg"],
            _array(prepared, "Pgmin"),
            _array(prepared, "Pgmax"),
        )
    return tuple(constraints)


def _generator_network_constraints(
    units: Sequence[DispatchableGenerator],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> tuple[cp.Constraint, ...]:
    state = context.network_state
    if context.formulation == "ac":
        assert isinstance(state, ACNetworkState)
        constraints = generator.ac_network_constraints(
            list(units),
            state.voltage,
            dict(context.ext_to_int),
            state.controlled_buses,
            enforce_vset=state.enforce_vset,
        )
    else:
        constraints = generator.dc_network_constraints(
            list(units),
            state,
            dict(context.ext_to_int),
            (),
            enforce_vset=False,
        )
    return tuple(constraints)


def _generator_step_cost(
    units: Sequence[DispatchableGenerator],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> cp.Expression:
    return generator.gen_cost_expr(
        _array(prepared, "gencost"),
        context.base_mva * variables["Pg"],
    )


def _generator_horizon(
    units: Sequence[DispatchableGenerator],
    prepared: Mapping[str, object],
    variable_history: Mapping[str, Sequence[cp.Variable]],
    context: HorizonContext,
) -> HorizonContribution:
    q_history = variable_history.get("Qg")
    constraints = generator.coupling_constraints(
        list(units),
        list(variable_history["Pg"]),
        None if q_history is None else list(q_history),
        context.delta,
    )
    return HorizonContribution(constraints=tuple(constraints))


GENERATOR_AC = FormulationAdapter[DispatchableGenerator](
    capability=FormulationCapability.ACTIVE,
    variable_specs=_generator_variable_specs,
    injections=_generator_injections,
    operating_constraints=_generator_operating_constraints,
    network_constraints=_generator_network_constraints,
    step_cost=_generator_step_cost,
    horizon=_generator_horizon,
)
GENERATOR_DC = FormulationAdapter[DispatchableGenerator](
    capability=FormulationCapability.ACTIVE,
    variable_specs=_generator_variable_specs,
    injections=_generator_injections,
    operating_constraints=_generator_operating_constraints,
    network_constraints=_generator_network_constraints,
    step_cost=_generator_step_cost,
    horizon=_generator_horizon,
)
GENERATOR_ADAPTER = ComponentAdapter[DispatchableGenerator, None](
    name="generator",
    prepare=_generator_prepare,
    metadata=_generator_metadata,
    formulations={
        "ac": GENERATOR_AC,
        "lossy_dc": GENERATOR_DC,
        "singlenode_dc": GENERATOR_DC,
    },
)


@dataclass(frozen=True)
class NondispatchableInputs:
    """Normalized ND availability supplied to component preparation."""

    available_mw: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "available_mw",
            np.array(self.available_mw, dtype=float, copy=True),
        )


def _nd_prepare(
    units: Sequence[NondispatchableUnit],
    inputs: NondispatchableInputs,
    context: PreparationContext,
) -> Mapping[str, object]:
    available = inputs.available_mw
    expected_shape = (context.horizon_steps, len(units))
    if available.shape != expected_shape:
        raise ValueError(
            "nondispatchable availability must have shape "
            f"{expected_shape}, got {available.shape}"
        )
    if not np.all(np.isfinite(available)) or np.any(available < 0):
        raise ValueError(
            "nondispatchable availability must contain finite, "
            "nonnegative values"
        )
    prepared = nondispatchable._prepare_data(
        list(units),
        context.nb,
        dict(context.ext_to_int),
        set(context.ext_bus_ids),
    )
    prepared["nd_available_mw"] = np.array(available, copy=True)
    prepared["horizon_steps"] = context.horizon_steps
    prepared["is_multistep"] = context.is_multistep
    return prepared


def _nd_metadata(
    prepared: Mapping[str, object],
    formulation: Formulation,
) -> Mapping[str, object]:
    metadata = dict(nondispatchable._build_metadata(dict(prepared)))
    available = _array(prepared, "nd_available_mw")
    if cast(bool, prepared["is_multistep"]):
        metadata["nd_available"] = available
    else:
        metadata["nd_p_available"] = available[0]
    return metadata


def _nd_variable_specs(
    units: Sequence[NondispatchableUnit],
    prepared: Mapping[str, object],
    context: StepContext,
) -> tuple[VariableSpec, ...]:
    shape = (cast(int, prepared["nnd"]),)
    specs = [VariableSpec("p_nd", shape)]
    if context.formulation == "ac":
        specs.append(VariableSpec("q_nd", shape))
    return tuple(specs)


def _nd_injections(
    units: Sequence[NondispatchableUnit],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> InjectionContribution:
    if context.formulation == "ac":
        p_pu, q_pu, inv_base_mva = nondispatchable.ac_injections(
            list(units),
            variables["p_nd"],
            variables["q_nd"],
            dict(context.ext_to_int),
            incidence=_array(prepared, "Cnd"),
        )
    else:
        p_pu, q_pu, inv_base_mva = nondispatchable.dc_injections(
            list(units),
            variables["p_nd"],
            dict(context.ext_to_int),
            incidence=_array(prepared, "Cnd"),
        )
    return InjectionContribution(p_pu, q_pu, inv_base_mva)


def _nd_operating_constraints(
    units: Sequence[NondispatchableUnit],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> tuple[cp.Constraint, ...]:
    available = _array(prepared, "nd_available_mw")[context.step]
    if context.formulation == "ac":
        constraints = nondispatchable.ac_operating_constraints(
            list(units),
            variables["p_nd"],
            variables["q_nd"],
            available,
        )
    else:
        constraints = nondispatchable.dc_operating_constraints(
            list(units),
            variables["p_nd"],
            available,
        )
    return tuple(constraints)


def _nd_horizon(
    units: Sequence[NondispatchableUnit],
    prepared: Mapping[str, object],
    variable_history: Mapping[str, Sequence[cp.Variable]],
    context: HorizonContext,
) -> HorizonContribution:
    q_history = variable_history.get("q_nd")
    constraints = nondispatchable.coupling_constraints(
        list(units),
        list(variable_history["p_nd"]),
        None if q_history is None else list(q_history),
        context.delta,
    )
    return HorizonContribution(constraints=tuple(constraints))


ND_AC = FormulationAdapter[NondispatchableUnit](
    capability=FormulationCapability.ACTIVE,
    variable_specs=_nd_variable_specs,
    injections=_nd_injections,
    operating_constraints=_nd_operating_constraints,
    horizon=_nd_horizon,
)
ND_DC = FormulationAdapter[NondispatchableUnit](
    capability=FormulationCapability.ACTIVE,
    variable_specs=_nd_variable_specs,
    injections=_nd_injections,
    operating_constraints=_nd_operating_constraints,
    horizon=_nd_horizon,
)
NONDISPATCHABLE_ADAPTER = ComponentAdapter[
    NondispatchableUnit, NondispatchableInputs
](
    name="nondispatchable",
    prepare=_nd_prepare,
    metadata=_nd_metadata,
    formulations={
        "ac": ND_AC,
        "lossy_dc": ND_DC,
        "singlenode_dc": ND_DC,
    },
)
