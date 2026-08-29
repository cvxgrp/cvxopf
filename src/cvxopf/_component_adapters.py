"""Typed adapters over the existing device-component functions.

This module binds device-owned modeling functions to the generic contracts in
``_component_adapter``. The bindings normalize how builders invoke components
without duplicating their physics, feasible sets, or cost models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Sequence, cast

import cvxpy as cp
import numpy as np

from cvxopf import generator, hvdc, load, nondispatchable, storage
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
    VectorizedContext,
    VectorizedModelContribution,
)
from cvxopf._component_assembly import ComponentRequest
from cvxopf._temporal_assembly import (
    HorizonVariableSpec,
    TemporalClass,
    VariableBoxFamily,
    box_representation_decision,
    prepare_box_bounds,
)
from cvxopf.generator import DispatchableGenerator
from cvxopf.hvdc import HVDCLink
from cvxopf.load import Load
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.storage import StorageUnitIdeal


def _array(prepared: Mapping[str, object], key: str) -> np.ndarray:
    """Return one prepared numerical array under the typed adapter contract."""
    return cast(np.ndarray, prepared[key])


def _leaf_bounds(
    lower: np.ndarray,
    upper: np.ndarray,
    *,
    native_shape: tuple[int, ...],
    context: VectorizedContext,
    family: VariableBoxFamily,
    lower_temporal_class: TemporalClass = "static",
    upper_temporal_class: TemporalClass = "static",
    variable_temporal_class: Literal["interval", "boundary"] = "interval",
) -> Mapping[str, object]:
    """Return validated CVXPY leaf-bound attributes for one horizon variable."""
    decision = box_representation_decision(context.formulation, family)
    if decision.representation != "leaf":
        raise RuntimeError(
            f"{context.formulation} {family.value} is not qualified for leaf bounds"
        )
    box = prepare_box_bounds(
        lower,
        upper,
        native_shape=native_shape,
        horizon_steps=context.horizon_steps,
        lower_temporal_class=lower_temporal_class,
        upper_temporal_class=upper_temporal_class,
        variable_temporal_class=variable_temporal_class,
    )
    return {"bounds": [box.lower, box.upper]}


def _time_major_values(
    values: np.ndarray,
    temporal_class: TemporalClass,
    *,
    horizon_steps: int,
    device_count: int,
    label: str,
) -> np.ndarray:
    """Validate temporal provenance and expose a non-tiled time-major view."""
    source = np.asarray(values, dtype=float)
    expected = (
        (device_count,) if temporal_class == "static" else (horizon_steps, device_count)
    )
    if source.shape != expected:
        raise ValueError(
            f"{label} with temporal_class={temporal_class!r} must have shape "
            f"{expected}, got {source.shape}"
        )
    if temporal_class == "static":
        return np.broadcast_to(source[np.newaxis, :], (horizon_steps, device_count))
    return source


def _load_prepare(
    units: Sequence[Load],
    inputs: "LoadInputs | None",
    context: PreparationContext,
) -> Mapping[str, object]:
    """Prepare fixed or sheddable loads and synchronized exogenous data."""
    prepared = load._prepare_data(
        list(units),
        context.nb,
        dict(context.ext_to_int),
        set(context.ext_bus_ids),
    )
    if inputs is None:
        p_source = _array(prepared, "load_p_mw")
        q_source = _array(prepared, "load_q_mvar")
        p_temporal_class: TemporalClass = "static"
        q_temporal_class: TemporalClass = "static"
    else:
        p_source = inputs.p_mw
        q_source = inputs.q_mvar
        p_temporal_class = inputs.p_temporal_class
        q_temporal_class = inputs.q_temporal_class
    p_mw = _time_major_values(
        p_source,
        p_temporal_class,
        horizon_steps=context.horizon_steps,
        device_count=len(units),
        label="load input channels (active)",
    )
    q_mvar = _time_major_values(
        q_source,
        q_temporal_class,
        horizon_steps=context.horizon_steps,
        device_count=len(units),
        label="load input channels (reactive)",
    )
    if not np.all(np.isfinite(p_mw)) or not np.all(np.isfinite(q_mvar)):
        raise ValueError("load input channels must contain only finite values")
    if inputs is not None and inputs.has_reactive is not None:
        if inputs.has_reactive.shape != (len(units),):
            raise ValueError(
                "load reactive-channel metadata must have shape "
                f"({len(units)},), got {inputs.has_reactive.shape}"
            )
        prepared = dict(prepared)
        prepared["load_has_reactive"] = inputs.has_reactive
    prepared["_load_p_mw_source"] = p_source
    prepared["_load_q_mvar_source"] = q_source
    prepared["_load_p_temporal_class"] = p_temporal_class
    prepared["_load_q_temporal_class"] = q_temporal_class
    prepared["_load_vectorized_assembly"] = (
        False if inputs is None else inputs.vectorized_assembly
    )
    prepared["_load_p_mw_by_step"] = p_mw
    prepared["_load_q_mvar_by_step"] = q_mvar
    vectorized_preparation = cast(bool, prepared["_load_vectorized_assembly"])
    prepared["_load_parameters"] = (
        None
        if cast(int, prepared["nsheddable"]) == 0 or vectorized_preparation
        else load._PreparedLoadParameters.create(p_mw, q_mvar)
    )
    return prepared


def _load_metadata(
    prepared: Mapping[str, object],
    formulation: Formulation,
) -> Mapping[str, object]:
    metadata = dict(load._build_metadata(dict(prepared)))
    if cast(bool, prepared["_load_vectorized_assembly"]):
        metadata["load_p_temporal_class"] = prepared["_load_p_temporal_class"]
        metadata["load_q_temporal_class"] = prepared["_load_q_temporal_class"]
        metadata["load_p_source_mw"] = prepared["_load_p_mw_source"]
        metadata["load_q_source_mvar"] = prepared["_load_q_mvar_source"]
    return metadata


def _load_variable_specs(
    units: Sequence[Load],
    prepared: Mapping[str, object],
    context: StepContext,
) -> tuple[VariableSpec, ...]:
    nsheddable = cast(int, prepared["nsheddable"])
    if nsheddable == 0:
        return ()
    return (VariableSpec("load_shed_fraction", (nsheddable,)),)


def _load_step_channels(
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> Mapping[str, cp.Expression]:
    """Return all load input, served, and conditional shed channels."""
    parameters = cast(load._PreparedLoadParameters | None, prepared["_load_parameters"])
    if parameters is None:
        p_load = cp.Constant(_array(prepared, "_load_p_mw_by_step")[context.step])
        q_load = cp.Constant(_array(prepared, "_load_q_mvar_by_step")[context.step])
        p_eligible = cp.Constant(np.empty(0))
    else:
        p_load = parameters.p_load_mw[context.step]
        q_load = parameters.q_load_mvar[context.step]
        p_eligible = parameters.p_eligible_mw[context.step]
    channels = load.served_and_shed_expressions(
        p_load,
        q_load,
        p_eligible,
        variables.get("load_shed_fraction"),
        _array(prepared, "sheddable_load_indices"),
        cast(int, prepared["nload"]),
    )
    if context.formulation != "ac":
        channels.pop("q_load_served", None)
        channels.pop("q_load_shed", None)
    return channels


def _load_injections(
    units: Sequence[Load],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> InjectionContribution:
    channels = _load_step_channels(prepared, variables, context)
    incidence = _array(prepared, "Cload")
    if context.formulation == "ac":
        p_pu, q_pu, inv_base_mva = load.ac_injections(
            channels["p_load_served"],
            channels["q_load_served"],
            incidence,
        )
    else:
        p_pu, q_pu, inv_base_mva = load.dc_injections(
            channels["p_load_served"], incidence
        )
    return InjectionContribution(p_pu, q_pu, inv_base_mva)


def _load_operating_constraints(
    units: Sequence[Load],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> tuple[cp.Constraint, ...]:
    fraction = variables.get("load_shed_fraction")
    if fraction is None:
        return ()
    parameters = cast(load._PreparedLoadParameters, prepared["_load_parameters"])
    indices = _array(prepared, "sheddable_load_indices")
    maximum_fraction = _array(prepared, "load_max_shed_fraction")[indices]
    constraint_method = (
        load.ac_operating_constraints
        if context.formulation == "ac"
        else load.dc_operating_constraints
    )
    return tuple(
        constraint_method(
            fraction,
            maximum_fraction,
            parameters.eligibility_mask[context.step, indices],
        )
    )


def _load_step_cost(
    units: Sequence[Load],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> cp.Expression | None:
    channels = _load_step_channels(prepared, variables, context)
    p_shed = channels.get("p_load_shed")
    if p_shed is None:
        return None
    indices = _array(prepared, "sheddable_load_indices")
    costs = _array(prepared, "load_shedding_cost_per_mwh")[indices]
    return load.shedding_cost_rate(p_shed, costs)


def _load_step_expressions(
    units: Sequence[Load],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> Mapping[str, cp.Expression]:
    return _load_step_channels(prepared, variables, context)


def _load_horizon(
    units: Sequence[Load],
    prepared: Mapping[str, object],
    variable_history: Mapping[str, Sequence[cp.Variable]],
    context: HorizonContext,
) -> HorizonContribution:
    if cast(int, prepared["nsheddable"]) == 0:
        return HorizonContribution(constraints=tuple(load.coupling_constraints()))
    parameters = cast(load._PreparedLoadParameters, prepared["_load_parameters"])
    indices = _array(prepared, "sheddable_load_indices")
    fractions = variable_history["load_shed_fraction"]
    shed_by_step = [
        cp.multiply(
            fractions[step],
            parameters.p_eligible_mw[step, indices],
        )
        for step in range(context.horizon_steps)
    ]
    ens_by_load = cp.multiply(context.delta, sum(shed_by_step))
    return HorizonContribution(
        expressions={
            "energy_not_served_by_load": ens_by_load,
            "energy_not_served": cp.sum(ens_by_load),
        }
    )


def _load_vectorized_variable_specs(
    units: Sequence[Load],
    prepared: Mapping[str, object],
    context: VectorizedContext,
) -> tuple[HorizonVariableSpec, ...]:
    nsheddable = cast(int, prepared["nsheddable"])
    if nsheddable == 0:
        return ()
    indices = _array(prepared, "sheddable_load_indices")
    maximum = _array(prepared, "load_max_shed_fraction")[indices]
    p_temporal_class = cast(TemporalClass, prepared["_load_p_temporal_class"])
    active = (
        _array(prepared, "_load_p_mw_source")[indices]
        if p_temporal_class == "static"
        else _array(prepared, "_load_p_mw_by_step")[:, indices]
    )
    upper = (active > 0.0).astype(float) * (
        maximum if p_temporal_class == "static" else maximum[np.newaxis, :]
    )
    attributes = _leaf_bounds(
        np.zeros(nsheddable),
        upper,
        native_shape=(nsheddable,),
        context=context,
        family=VariableBoxFamily.LOAD_SHED_FRACTION,
        lower_temporal_class="static",
        upper_temporal_class=p_temporal_class,
    )
    return (
        HorizonVariableSpec(
            "load_shed_fraction",
            (nsheddable,),
            attributes=attributes,
        ),
    )


def _load_vectorized_assembly(
    units: Sequence[Load],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: VectorizedContext,
) -> VectorizedModelContribution:
    p_values = _array(prepared, "_load_p_mw_by_step").T
    q_values = _array(prepared, "_load_q_mvar_by_step").T
    p_load = cp.Constant(p_values)
    q_load = cp.Constant(q_values)
    expressions: dict[str, cp.Expression] = {
        "p_load": p_load,
        "q_load": q_load,
    }
    fraction = variables.get("load_shed_fraction")
    stage_cost_rate: cp.Expression | None = None
    horizon = HorizonContribution()
    if fraction is None:
        p_served = p_load
        expressions["p_load_served"] = p_served
    else:
        indices = _array(prepared, "sheddable_load_indices")
        p_temporal_class = cast(TemporalClass, prepared["_load_p_temporal_class"])
        eligible_values = (
            np.maximum(_array(prepared, "_load_p_mw_source"), 0.0)[:, np.newaxis]
            if p_temporal_class == "static"
            else np.maximum(p_values, 0.0)
        )
        channels = load.served_and_shed_expressions(
            p_load,
            q_load,
            cp.Constant(eligible_values),
            fraction,
            indices,
            cast(int, prepared["nload"]),
            interval_axis=1,
        )
        channels.pop("q_load_served", None)
        channels.pop("q_load_shed", None)
        p_shed = channels["p_load_shed"]
        p_served = channels["p_load_served"]
        costs = _array(prepared, "load_shedding_cost_per_mwh")[indices]
        stage_cost_rate = load.shedding_cost_rate(
            p_shed,
            costs[:, np.newaxis],
            interval_axis=1,
        )
        ens_by_load = cp.multiply(context.delta, cp.sum(p_shed, axis=1))
        expressions.update(channels)
        horizon = HorizonContribution(
            expressions={
                "energy_not_served_by_load": ens_by_load,
                "energy_not_served": cp.sum(ens_by_load),
            }
        )
    p_pu, q_pu, scale = load.dc_injections(p_served, _array(prepared, "Cload"))
    return VectorizedModelContribution(
        injection=InjectionContribution(p_pu, q_pu, scale),
        stage_cost_rate=stage_cost_rate,
        expressions=expressions,
        horizon=horizon,
    )


LOAD_AC = FormulationAdapter[Load](
    capability=FormulationCapability.ACTIVE,
    variable_specs=_load_variable_specs,
    injections=_load_injections,
    operating_constraints=_load_operating_constraints,
    step_cost=_load_step_cost,
    step_expressions=_load_step_expressions,
    horizon=_load_horizon,
)
LOAD_DC = FormulationAdapter[Load](
    capability=FormulationCapability.ACTIVE,
    variable_specs=_load_variable_specs,
    injections=_load_injections,
    operating_constraints=_load_operating_constraints,
    step_cost=_load_step_cost,
    step_expressions=_load_step_expressions,
    horizon=_load_horizon,
    vectorized_variable_specs=_load_vectorized_variable_specs,
    vectorized_assembly=_load_vectorized_assembly,
)


@dataclass(frozen=True)
class LoadInputs:
    """Normalized load channels and effective reactive definition by device."""

    p_mw: np.ndarray
    q_mvar: np.ndarray
    has_reactive: np.ndarray | None = None
    p_temporal_class: TemporalClass = "interval"
    q_temporal_class: TemporalClass = "interval"
    vectorized_assembly: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "p_mw", np.array(self.p_mw, dtype=float, copy=True))
        object.__setattr__(
            self, "q_mvar", np.array(self.q_mvar, dtype=float, copy=True)
        )
        if self.p_temporal_class not in {"static", "interval"}:
            raise ValueError("active load temporal class must be static or interval")
        if self.q_temporal_class not in {"static", "interval"}:
            raise ValueError("reactive load temporal class must be static or interval")
        if self.has_reactive is not None:
            object.__setattr__(
                self,
                "has_reactive",
                np.array(self.has_reactive, dtype=bool, copy=True),
            )


LOAD_ADAPTER = ComponentAdapter[Load, LoadInputs | None](
    name="load",
    prepare=_load_prepare,
    metadata=_load_metadata,
    formulations={
        "ac": LOAD_AC,
        "lossy_dc": LOAD_DC,
        "singlenode_dc": LOAD_DC,
    },
    cost_expression_name="load_shedding_cost",
)


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
    return generator._build_metadata(dict(prepared), reactive=formulation == "ac")


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


def _generator_vectorized_variable_specs(
    units: Sequence[DispatchableGenerator],
    prepared: Mapping[str, object],
    context: VectorizedContext,
) -> tuple[HorizonVariableSpec, ...]:
    ng = cast(int, prepared["ng"])
    attributes = _leaf_bounds(
        _array(prepared, "Pgmin"),
        _array(prepared, "Pgmax"),
        native_shape=(ng,),
        context=context,
        family=VariableBoxFamily.DISPATCHABLE_P,
    )
    return (HorizonVariableSpec("Pg", (ng,), attributes=attributes),)


def _generator_vectorized_assembly(
    units: Sequence[DispatchableGenerator],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: VectorizedContext,
) -> VectorizedModelContribution:
    p_pu, q_pu, scale = generator.dc_injections(
        list(units),
        variables["Pg"],
        dict(context.ext_to_int),
        incidence=_array(prepared, "Cg"),
    )
    cost_rate = generator.horizon_cost_rate(
        _array(prepared, "gencost"),
        context.base_mva * variables["Pg"],
        context.horizon_steps,
    )
    return VectorizedModelContribution(
        injection=InjectionContribution(p_pu, q_pu, scale),
        stage_cost_rate=cost_rate,
    )


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
    vectorized_variable_specs=_generator_vectorized_variable_specs,
    vectorized_assembly=_generator_vectorized_assembly,
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
    temporal_class: TemporalClass = "interval"
    vectorized_assembly: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "available_mw",
            np.array(self.available_mw, dtype=float, copy=True),
        )
        if self.temporal_class not in {"static", "interval"}:
            raise ValueError(
                "nondispatchable temporal class must be static or interval"
            )


def _nd_prepare(
    units: Sequence[NondispatchableUnit],
    inputs: NondispatchableInputs,
    context: PreparationContext,
) -> Mapping[str, object]:
    source = inputs.available_mw
    available = _time_major_values(
        source,
        inputs.temporal_class,
        horizon_steps=context.horizon_steps,
        device_count=len(units),
        label="nondispatchable availability",
    )
    if not np.all(np.isfinite(available)) or np.any(available < 0):
        raise ValueError(
            "nondispatchable availability must contain finite, nonnegative values"
        )
    prepared = nondispatchable._prepare_data(
        list(units),
        context.nb,
        dict(context.ext_to_int),
        set(context.ext_bus_ids),
    )
    prepared["nd_available_source_mw"] = source
    prepared["nd_available_temporal_class"] = inputs.temporal_class
    prepared["nd_vectorized_assembly"] = inputs.vectorized_assembly
    prepared["nd_available_mw"] = available
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
    if cast(bool, prepared["nd_vectorized_assembly"]):
        metadata["nd_available_temporal_class"] = prepared[
            "nd_available_temporal_class"
        ]
        metadata["nd_available_source_mw"] = prepared["nd_available_source_mw"]
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


def _nd_vectorized_variable_specs(
    units: Sequence[NondispatchableUnit],
    prepared: Mapping[str, object],
    context: VectorizedContext,
) -> tuple[HorizonVariableSpec, ...]:
    nnd = cast(int, prepared["nnd"])
    temporal_class = cast(TemporalClass, prepared["nd_available_temporal_class"])
    available = (
        _array(prepared, "nd_available_source_mw")
        if temporal_class == "static"
        else _array(prepared, "nd_available_mw")
    )
    rating = _array(prepared, "nd_apparent_power_rating")
    upper = np.minimum(
        available,
        rating if temporal_class == "static" else rating[np.newaxis, :],
    )
    attributes = _leaf_bounds(
        np.zeros(nnd),
        upper,
        native_shape=(nnd,),
        context=context,
        family=VariableBoxFamily.NONDISPATCHABLE_REAL_POWER,
        lower_temporal_class="static",
        upper_temporal_class=temporal_class,
    )
    return (HorizonVariableSpec("p_nd", (nnd,), attributes=attributes),)


def _nd_vectorized_assembly(
    units: Sequence[NondispatchableUnit],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: VectorizedContext,
) -> VectorizedModelContribution:
    p_pu, q_pu, scale = nondispatchable.dc_injections(
        list(units),
        variables["p_nd"],
        dict(context.ext_to_int),
        incidence=_array(prepared, "Cnd"),
    )
    return VectorizedModelContribution(
        injection=InjectionContribution(p_pu, q_pu, scale)
    )


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
    vectorized_variable_specs=_nd_vectorized_variable_specs,
    vectorized_assembly=_nd_vectorized_assembly,
)
NONDISPATCHABLE_ADAPTER = ComponentAdapter[NondispatchableUnit, NondispatchableInputs](
    name="nondispatchable",
    prepare=_nd_prepare,
    metadata=_nd_metadata,
    formulations={
        "ac": ND_AC,
        "lossy_dc": ND_DC,
        "singlenode_dc": ND_DC,
    },
)


def _storage_prepare(
    units: Sequence[StorageUnitIdeal],
    inputs: None,
    context: PreparationContext,
) -> Mapping[str, object]:
    prepared = storage._prepare_data(
        list(units),
        context.nb,
        dict(context.ext_to_int),
        set(context.ext_bus_ids),
    )
    prepared["storage_delta"] = context.delta
    return prepared


def _storage_metadata(
    prepared: Mapping[str, object],
    formulation: Formulation,
) -> Mapping[str, object]:
    return storage._build_metadata(dict(prepared))


def _storage_variable_specs(
    units: Sequence[StorageUnitIdeal],
    prepared: Mapping[str, object],
    context: StepContext,
) -> tuple[VariableSpec, ...]:
    shape = (cast(int, prepared["ns"]),)
    specs = [VariableSpec("b", shape)]
    if context.formulation == "ac":
        specs.append(VariableSpec("b_q", shape))
    specs.append(VariableSpec("soc", shape))
    return tuple(specs)


def _storage_injections(
    units: Sequence[StorageUnitIdeal],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> InjectionContribution:
    if context.formulation == "ac":
        p_pu, q_pu, inv_base_mva = storage.ac_injections(
            list(units),
            variables["b"],
            variables["b_q"],
            dict(context.ext_to_int),
            incidence=_array(prepared, "Cs"),
        )
    else:
        p_pu, q_pu, inv_base_mva = storage.dc_injections(
            list(units),
            variables["b"],
            dict(context.ext_to_int),
            incidence=_array(prepared, "Cs"),
        )
    return InjectionContribution(p_pu, q_pu, inv_base_mva)


def _storage_operating_constraints(
    units: Sequence[StorageUnitIdeal],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> tuple[cp.Constraint, ...]:
    if context.formulation == "ac":
        constraints = storage.ac_operating_constraints(
            list(units),
            variables["b"],
            variables["b_q"],
            variables["soc"],
        )
    else:
        constraints = storage.dc_operating_constraints(
            list(units),
            variables["b"],
            variables["soc"],
        )
    return tuple(constraints)


def _storage_step_cost(
    units: Sequence[StorageUnitIdeal],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> cp.Expression:
    return storage.storage_cost_expr(list(units), variables["b"])


def _storage_horizon(
    units: Sequence[StorageUnitIdeal],
    prepared: Mapping[str, object],
    variable_history: Mapping[str, Sequence[cp.Variable]],
    context: HorizonContext,
) -> HorizonContribution:
    b_history = list(variable_history["b"])
    soc_history = list(variable_history["soc"])
    constraints = storage.coupling_constraints(
        list(units), b_history, soc_history, context.delta
    )
    terminal_cost = storage.terminal_cost_expr(list(units), soc_history[-1])
    return HorizonContribution(
        constraints=tuple(constraints),
        terminal_cost=terminal_cost,
    )


def _storage_vectorized_variable_specs(
    units: Sequence[StorageUnitIdeal],
    prepared: Mapping[str, object],
    context: VectorizedContext,
) -> tuple[HorizonVariableSpec, ...]:
    ns = cast(int, prepared["ns"])
    rating = _array(prepared, "storage_apparent_power_rating")
    capacity = _array(prepared, "storage_capacity")
    power_attributes = _leaf_bounds(
        -rating,
        rating,
        native_shape=(ns,),
        context=context,
        family=VariableBoxFamily.STORAGE_REAL_POWER,
    )
    soc_attributes = _leaf_bounds(
        np.zeros(ns),
        capacity,
        native_shape=(ns,),
        context=context,
        family=VariableBoxFamily.STORAGE_SOC,
        variable_temporal_class="boundary",
    )
    return (
        HorizonVariableSpec("b", (ns,), attributes=power_attributes),
        HorizonVariableSpec(
            "soc",
            (ns,),
            temporal_class="boundary",
            attributes=soc_attributes,
            result_view="post_step_boundaries",
        ),
    )


def _storage_vectorized_assembly(
    units: Sequence[StorageUnitIdeal],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: VectorizedContext,
) -> VectorizedModelContribution:
    power = variables["b"]
    soc = variables["soc"]
    p_pu, q_pu, scale = storage.dc_injections(
        list(units),
        power,
        dict(context.ext_to_int),
        incidence=_array(prepared, "Cs"),
    )
    constraints = storage.vectorized_coupling_constraints(
        list(units), power, soc, context.delta
    )
    stage_cost_rate = storage.vectorized_storage_cost_rate(list(units), power)
    terminal_cost = storage.terminal_cost_expr(list(units), soc[:, -1])
    horizon_expressions = (
        {} if terminal_cost is None else {"storage_terminal_cost": terminal_cost}
    )
    return VectorizedModelContribution(
        injection=InjectionContribution(p_pu, q_pu, scale),
        stage_cost_rate=stage_cost_rate,
        horizon=HorizonContribution(
            constraints=tuple(constraints),
            terminal_cost=terminal_cost,
            expressions=horizon_expressions,
        ),
    )


STORAGE_AC = FormulationAdapter[StorageUnitIdeal](
    capability=FormulationCapability.ACTIVE,
    variable_specs=_storage_variable_specs,
    injections=_storage_injections,
    operating_constraints=_storage_operating_constraints,
    step_cost=_storage_step_cost,
    horizon=_storage_horizon,
)
STORAGE_DC = FormulationAdapter[StorageUnitIdeal](
    capability=FormulationCapability.ACTIVE,
    variable_specs=_storage_variable_specs,
    injections=_storage_injections,
    operating_constraints=_storage_operating_constraints,
    step_cost=_storage_step_cost,
    horizon=_storage_horizon,
    vectorized_variable_specs=_storage_vectorized_variable_specs,
    vectorized_assembly=_storage_vectorized_assembly,
)
STORAGE_ADAPTER = ComponentAdapter[StorageUnitIdeal, None](
    name="storage",
    prepare=_storage_prepare,
    metadata=_storage_metadata,
    formulations={
        "ac": STORAGE_AC,
        "lossy_dc": STORAGE_DC,
        "singlenode_dc": STORAGE_DC,
    },
)


@dataclass(frozen=True)
class HVDCInputs:
    """Normalized per-step HVDC transfer boxes in MW."""

    p_min_mw: np.ndarray
    p_max_mw: np.ndarray
    temporal_class: TemporalClass = "interval"
    vectorized_assembly: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "p_min_mw", np.array(self.p_min_mw, dtype=float, copy=True)
        )
        if self.temporal_class not in {"static", "interval"}:
            raise ValueError("HVDC temporal class must be static or interval")
        object.__setattr__(
            self, "p_max_mw", np.array(self.p_max_mw, dtype=float, copy=True)
        )


def _hvdc_prepare(
    units: Sequence[HVDCLink],
    inputs: HVDCInputs | None,
    context: PreparationContext,
) -> Mapping[str, object]:
    prepared = hvdc._prepare_data(
        list(units),
        context.nb,
        dict(context.ext_to_int),
        set(context.ext_bus_ids),
    )
    if inputs is None:
        p_min, p_max = hvdc._hvdc_static_box(list(units))
        temporal_class: TemporalClass = "static"
    else:
        p_min = inputs.p_min_mw
        p_max = inputs.p_max_mw
        temporal_class = inputs.temporal_class
    p_min_source = p_min
    p_max_source = p_max
    expected_shape = (
        (len(units),)
        if temporal_class == "static"
        else (context.horizon_steps, len(units))
    )
    if p_min_source.shape != expected_shape or p_max_source.shape != expected_shape:
        raise ValueError(
            "HVDC bounds must both have shape "
            f"{expected_shape}, got {p_min_source.shape} and {p_max_source.shape}"
        )
    p_min = _time_major_values(
        p_min_source,
        temporal_class,
        horizon_steps=context.horizon_steps,
        device_count=len(units),
        label="HVDC lower bounds",
    )
    p_max = _time_major_values(
        p_max_source,
        temporal_class,
        horizon_steps=context.horizon_steps,
        device_count=len(units),
        label="HVDC upper bounds",
    )
    if not np.all(np.isfinite(p_min)) or not np.all(np.isfinite(p_max)):
        raise ValueError("HVDC bounds must contain only finite values")
    if np.any(p_min > p_max):
        raise ValueError("HVDC bounds must satisfy p_min_mw <= p_max_mw")
    prepared["hvdc_p_min_source_mw"] = p_min_source
    prepared["hvdc_p_max_source_mw"] = p_max_source
    prepared["hvdc_temporal_class"] = temporal_class
    prepared["hvdc_vectorized_assembly"] = (
        False if inputs is None else inputs.vectorized_assembly
    )
    prepared["hvdc_p_min_mw"] = p_min
    prepared["hvdc_p_max_mw"] = p_max
    return prepared


def _hvdc_metadata(
    prepared: Mapping[str, object],
    formulation: Formulation,
) -> Mapping[str, object]:
    metadata = dict(hvdc._build_metadata(dict(prepared)))
    if cast(bool, prepared["hvdc_vectorized_assembly"]):
        metadata["hvdc_temporal_class"] = prepared["hvdc_temporal_class"]
        metadata["hvdc_p_min_source_mw"] = prepared["hvdc_p_min_source_mw"]
        metadata["hvdc_p_max_source_mw"] = prepared["hvdc_p_max_source_mw"]
    return metadata


def _hvdc_variable_specs(
    units: Sequence[HVDCLink],
    prepared: Mapping[str, object],
    context: StepContext,
) -> tuple[VariableSpec, ...]:
    shape = (cast(int, prepared["n_hvdc"]),)
    return (
        VariableSpec("p_hvdc_in", shape),
        VariableSpec("p_hvdc_out", shape),
    )


def _hvdc_injections(
    units: Sequence[HVDCLink],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> InjectionContribution:
    injection_method = (
        hvdc.ac_injections if context.formulation == "ac" else hvdc.dc_injections
    )
    p_pu, q_pu, inv_base_mva = injection_method(
        list(units),
        variables["p_hvdc_in"],
        variables["p_hvdc_out"],
        dict(context.ext_to_int),
        incidence=(
            _array(prepared, "Ch_from"),
            _array(prepared, "Ch_to"),
        ),
    )
    return InjectionContribution(p_pu, q_pu, inv_base_mva)


def _hvdc_operating_constraints(
    units: Sequence[HVDCLink],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> tuple[cp.Constraint, ...]:
    constraint_method = (
        hvdc.ac_operating_constraints
        if context.formulation == "ac"
        else hvdc.dc_operating_constraints
    )
    constraints = constraint_method(
        list(units),
        variables["p_hvdc_in"],
        variables["p_hvdc_out"],
        _array(prepared, "hvdc_p_min_mw")[context.step],
        _array(prepared, "hvdc_p_max_mw")[context.step],
        context.step,
    )
    return tuple(constraints)


def _hvdc_step_cost(
    units: Sequence[HVDCLink],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> cp.Expression:
    return hvdc.hvdc_cost_expr(list(units), variables["p_hvdc_in"])


def _hvdc_horizon(
    units: Sequence[HVDCLink],
    prepared: Mapping[str, object],
    variable_history: Mapping[str, Sequence[cp.Variable]],
    context: HorizonContext,
) -> HorizonContribution:
    constraints = hvdc.coupling_constraints(
        list(units),
        list(variable_history["p_hvdc_in"]),
        list(variable_history["p_hvdc_out"]),
        context.delta,
    )
    return HorizonContribution(constraints=tuple(constraints))


def _hvdc_vectorized_variable_specs(
    units: Sequence[HVDCLink],
    prepared: Mapping[str, object],
    context: VectorizedContext,
) -> tuple[HorizonVariableSpec, ...]:
    count = cast(int, prepared["n_hvdc"])
    temporal_class = cast(TemporalClass, prepared["hvdc_temporal_class"])
    lower = (
        _array(prepared, "hvdc_p_min_source_mw")
        if temporal_class == "static"
        else _array(prepared, "hvdc_p_min_mw")
    )
    upper = (
        _array(prepared, "hvdc_p_max_source_mw")
        if temporal_class == "static"
        else _array(prepared, "hvdc_p_max_mw")
    )
    attributes = _leaf_bounds(
        lower,
        upper,
        native_shape=(count,),
        context=context,
        family=VariableBoxFamily.HVDC_INPUT_POWER,
        lower_temporal_class=temporal_class,
        upper_temporal_class=temporal_class,
    )
    return (
        HorizonVariableSpec("p_hvdc_in", (count,), attributes=attributes),
        HorizonVariableSpec("p_hvdc_out", (count,)),
    )


def _hvdc_vectorized_coefficients(
    units: Sequence[HVDCLink],
    prepared: Mapping[str, object],
) -> np.ndarray:
    """Select owner-defined loss branches before any static broadcast."""
    temporal_class = cast(TemporalClass, prepared["hvdc_temporal_class"])
    if temporal_class == "static":
        coefficients = hvdc.loss_branch_coefficients(
            list(units),
            _array(prepared, "hvdc_p_min_source_mw"),
            _array(prepared, "hvdc_p_max_source_mw"),
        )
        return coefficients[:, np.newaxis]
    return hvdc.loss_branch_coefficients(
        list(units),
        _array(prepared, "hvdc_p_min_mw").T,
        _array(prepared, "hvdc_p_max_mw").T,
    )


def _hvdc_vectorized_assembly(
    units: Sequence[HVDCLink],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: VectorizedContext,
) -> VectorizedModelContribution:
    p_in = variables["p_hvdc_in"]
    p_out = variables["p_hvdc_out"]
    p_pu, q_pu, scale = hvdc.dc_injections(
        list(units),
        p_in,
        p_out,
        dict(context.ext_to_int),
        incidence=(
            _array(prepared, "Ch_from"),
            _array(prepared, "Ch_to"),
        ),
    )
    coefficients = _hvdc_vectorized_coefficients(units, prepared)
    cost_rate = hvdc.hvdc_cost_expr(list(units), p_in)
    return VectorizedModelContribution(
        injection=InjectionContribution(p_pu, q_pu, scale),
        operating_constraints=(p_out == cp.multiply(coefficients, p_in),),
        stage_cost_rate=cost_rate,
    )


HVDC_AC = FormulationAdapter[HVDCLink](
    capability=FormulationCapability.ACTIVE,
    variable_specs=_hvdc_variable_specs,
    injections=_hvdc_injections,
    operating_constraints=_hvdc_operating_constraints,
    step_cost=_hvdc_step_cost,
    horizon=_hvdc_horizon,
)
HVDC_DC = FormulationAdapter[HVDCLink](
    capability=FormulationCapability.ACTIVE,
    variable_specs=_hvdc_variable_specs,
    injections=_hvdc_injections,
    operating_constraints=_hvdc_operating_constraints,
    step_cost=_hvdc_step_cost,
    horizon=_hvdc_horizon,
    vectorized_variable_specs=_hvdc_vectorized_variable_specs,
    vectorized_assembly=_hvdc_vectorized_assembly,
)
HVDC_NULL = FormulationAdapter[HVDCLink](
    capability=FormulationCapability.NULL,
)
HVDC_ADAPTER = ComponentAdapter[HVDCLink, HVDCInputs | None](
    name="hvdc",
    prepare=_hvdc_prepare,
    metadata=_hvdc_metadata,
    formulations={
        "ac": HVDC_AC,
        "lossy_dc": HVDC_DC,
        "singlenode_dc": HVDC_NULL,
    },
)


def component_requests(
    formulation: Formulation,
    *,
    generators: Sequence[DispatchableGenerator],
    load_units: Sequence[Load] = (),
    load_inputs: LoadInputs | None = None,
    load_participates_when_empty: bool = False,
    storage_units: Sequence[StorageUnitIdeal] = (),
    nondispatchable_units: Sequence[NondispatchableUnit] = (),
    nondispatchable_inputs: NondispatchableInputs | None = None,
    hvdc_links: Sequence[HVDCLink] = (),
    hvdc_inputs: HVDCInputs | None = None,
) -> tuple[ComponentRequest, ...]:
    """Return the common ordered registry for one formulation build."""
    hvdc_capability = (
        FormulationCapability.NULL
        if formulation == "singlenode_dc"
        else FormulationCapability.ACTIVE
    )
    return (
        ComponentRequest(
            GENERATOR_ADAPTER,
            tuple(generators),
            required_capability=FormulationCapability.ACTIVE,
        ),
        ComponentRequest(
            LOAD_ADAPTER,
            tuple(load_units),
            load_inputs,
            required_capability=FormulationCapability.ACTIVE,
            participates_when_empty=load_participates_when_empty,
        ),
        ComponentRequest(
            STORAGE_ADAPTER,
            tuple(storage_units),
            required_capability=FormulationCapability.ACTIVE,
        ),
        ComponentRequest(
            NONDISPATCHABLE_ADAPTER,
            tuple(nondispatchable_units),
            nondispatchable_inputs,
            required_capability=FormulationCapability.ACTIVE,
        ),
        ComponentRequest(
            HVDC_ADAPTER,
            tuple(hvdc_links),
            hvdc_inputs,
            required_capability=hvdc_capability,
        ),
    )
