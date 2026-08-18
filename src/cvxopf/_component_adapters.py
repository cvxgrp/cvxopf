"""Typed adapters over the existing device-component functions.

This module binds device-owned modeling functions to the generic contracts in
``_component_adapter``. The bindings normalize how builders invoke components
without duplicating their physics, feasible sets, or cost models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence, cast

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
)
from cvxopf._component_assembly import ComponentRequest
from cvxopf.generator import DispatchableGenerator
from cvxopf.hvdc import HVDCLink
from cvxopf.load import Load
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.storage import StorageUnitIdeal


def _array(prepared: Mapping[str, object], key: str) -> np.ndarray:
    """Return one prepared numerical array under the typed adapter contract."""
    return cast(np.ndarray, prepared[key])


def _load_prepare(
    units: Sequence[Load],
    inputs: "LoadInputs | None",
    context: PreparationContext,
) -> Mapping[str, object]:
    """Prepare fixed loads while S1 shedding semantics remain inactive."""
    if any(unit.shedding_cost_per_mwh is not None for unit in units):
        raise NotImplementedError(
            "sheddable Load optimization is not implemented until "
            "Milestone 19 Stage 4; set shedding_cost_per_mwh=None"
        )
    prepared = load._prepare_data(
        list(units),
        context.nb,
        dict(context.ext_to_int),
        set(context.ext_bus_ids),
    )
    if inputs is None:
        p_mw = np.tile(
            _array(prepared, "load_p_mw"),
            (context.horizon_steps, 1),
        )
        q_mvar = np.tile(
            _array(prepared, "load_q_mvar"),
            (context.horizon_steps, 1),
        )
    else:
        p_mw = inputs.p_mw
        q_mvar = inputs.q_mvar
    expected_shape = (context.horizon_steps, len(units))
    if p_mw.shape != expected_shape or q_mvar.shape != expected_shape:
        raise ValueError(
            "load input channels must both have shape "
            f"{expected_shape}, got {p_mw.shape} and {q_mvar.shape}"
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
    prepared["_load_p_mw_by_step"] = np.array(p_mw, copy=True)
    prepared["_load_q_mvar_by_step"] = np.array(q_mvar, copy=True)
    return prepared


def _load_metadata(
    prepared: Mapping[str, object],
    formulation: Formulation,
) -> Mapping[str, object]:
    return load._build_metadata(dict(prepared))


def _load_variable_specs(
    units: Sequence[Load],
    prepared: Mapping[str, object],
    context: StepContext,
) -> tuple[VariableSpec, ...]:
    return ()


def _load_injections(
    units: Sequence[Load],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> InjectionContribution:
    p_load = _array(prepared, "_load_p_mw_by_step")[context.step]
    incidence = _array(prepared, "Cload")
    if context.formulation == "ac":
        p_pu, q_pu, inv_base_mva = load.ac_injections(
            p_load,
            _array(prepared, "_load_q_mvar_by_step")[context.step],
            incidence,
        )
    else:
        p_pu, q_pu, inv_base_mva = load.dc_injections(
            p_load, incidence
        )
    return InjectionContribution(p_pu, q_pu, inv_base_mva)


def _load_operating_constraints(
    units: Sequence[Load],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> tuple[cp.Constraint, ...]:
    constraint_method = (
        load.ac_operating_constraints
        if context.formulation == "ac"
        else load.dc_operating_constraints
    )
    return tuple(constraint_method())


def _load_step_expressions(
    units: Sequence[Load],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> Mapping[str, cp.Expression]:
    return load.fixed_expressions(
        _array(prepared, "_load_p_mw_by_step")[context.step],
        _array(prepared, "_load_q_mvar_by_step")[context.step],
        reactive_service=context.formulation == "ac",
    )


def _load_horizon(
    units: Sequence[Load],
    prepared: Mapping[str, object],
    variable_history: Mapping[str, Sequence[cp.Variable]],
    context: HorizonContext,
) -> HorizonContribution:
    return HorizonContribution(
        constraints=tuple(load.coupling_constraints())
    )


LOAD_ACTIVE = FormulationAdapter[Load](
    capability=FormulationCapability.ACTIVE,
    variable_specs=_load_variable_specs,
    injections=_load_injections,
    operating_constraints=_load_operating_constraints,
    step_expressions=_load_step_expressions,
    horizon=_load_horizon,
)


@dataclass(frozen=True)
class LoadInputs:
    """Normalized load channels and effective reactive definition by device."""

    p_mw: np.ndarray
    q_mvar: np.ndarray
    has_reactive: np.ndarray | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "p_mw", np.array(self.p_mw, dtype=float, copy=True)
        )
        object.__setattr__(
            self, "q_mvar", np.array(self.q_mvar, dtype=float, copy=True)
        )
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
        "ac": LOAD_ACTIVE,
        "lossy_dc": LOAD_ACTIVE,
        "singlenode_dc": LOAD_ACTIVE,
    },
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
    terminal_cost = storage.terminal_cost_expr(
        list(units), soc_history[-1]
    )
    return HorizonContribution(
        constraints=tuple(constraints),
        terminal_cost=terminal_cost,
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

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "p_min_mw", np.array(self.p_min_mw, dtype=float, copy=True)
        )
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
        p_min = np.tile(p_min, (context.horizon_steps, 1))
        p_max = np.tile(p_max, (context.horizon_steps, 1))
    else:
        p_min = inputs.p_min_mw
        p_max = inputs.p_max_mw
    expected_shape = (context.horizon_steps, len(units))
    if p_min.shape != expected_shape or p_max.shape != expected_shape:
        raise ValueError(
            "HVDC bounds must both have shape "
            f"{expected_shape}, got {p_min.shape} and {p_max.shape}"
        )
    if not np.all(np.isfinite(p_min)) or not np.all(np.isfinite(p_max)):
        raise ValueError("HVDC bounds must contain only finite values")
    if np.any(p_min > p_max):
        raise ValueError("HVDC bounds must satisfy p_min_mw <= p_max_mw")
    prepared["hvdc_p_min_mw"] = np.array(p_min, copy=True)
    prepared["hvdc_p_max_mw"] = np.array(p_max, copy=True)
    return prepared


def _hvdc_metadata(
    prepared: Mapping[str, object],
    formulation: Formulation,
) -> Mapping[str, object]:
    return hvdc._build_metadata(dict(prepared))


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
        hvdc.ac_injections
        if context.formulation == "ac"
        else hvdc.dc_injections
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


HVDC_ACTIVE = FormulationAdapter[HVDCLink](
    capability=FormulationCapability.ACTIVE,
    variable_specs=_hvdc_variable_specs,
    injections=_hvdc_injections,
    operating_constraints=_hvdc_operating_constraints,
    step_cost=_hvdc_step_cost,
    horizon=_hvdc_horizon,
)
HVDC_NULL = FormulationAdapter[HVDCLink](
    capability=FormulationCapability.NULL,
)
HVDC_ADAPTER = ComponentAdapter[HVDCLink, HVDCInputs | None](
    name="hvdc",
    prepare=_hvdc_prepare,
    metadata=_hvdc_metadata,
    formulations={
        "ac": HVDC_ACTIVE,
        "lossy_dc": HVDC_ACTIVE,
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
