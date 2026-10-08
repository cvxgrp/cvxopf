"""SOCP device integration through the authoritative component infrastructure.

No network solve is needed: compare AC/SOCP device contributions at identical
primal values and check the representation-specific voltage binding separately.
"""

from dataclasses import replace

import cvxpy as cp
import numpy as np
import pytest

from cvxopf import generator
from cvxopf._component_adapter import (
    ACNetworkState, DCNetworkState, SquaredVoltageNetworkState,
    FormulationCapability, PreparationContext, StepContext, VectorizedContext,
    HorizonContext,
)
from cvxopf._component_adapters import (
    component_requests, NondispatchableInputs, LoadInputs,
    GENERATOR_ADAPTER, STORAGE_ADAPTER, NONDISPATCHABLE_ADAPTER,
    HVDC_ADAPTER, LOAD_ADAPTER,
)
from cvxopf._component_assembly import (
    prepare_components, assemble_component_step, aggregate_step_contributions,
    assemble_component_horizon, aggregate_horizon_contributions,
    assemble_component_vectorized, aggregate_vectorized_contributions,
    integrate_vectorized_stage_cost_rate, integrate_vectorized_component_stage_costs,
    publish_component_metadata, publish_vectorized_component_variables,
    publish_vectorized_component_expressions, vectorized_component_result_projections,
)
from cvxopf._temporal_assembly import (
    supports_reactive_power, VariableBoxFamily, box_representation_decision,
)
from cvxopf.load import Load
from cvxopf.storage import StorageUnitIdeal
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.hvdc import HVDCLink


def _prepare(formulation, terminal):
    generators = [generator.DispatchableGenerator(
        bus=10, p_max_mw=120, q_min_mvar=-40, q_max_mvar=50,
        cost_coeffs=(1, 2, 0.03), vg=1.04,
    )]
    storage = [StorageUnitIdeal(
        bus=30, apparent_power_rating=10, capacity=20, initial_soc=12,
        terminal_soc=10,
        **({"terminal_constraint": "equality"} if terminal == "hard" else
           {"terminal_cost": "quadratic", "terminal_weight": 3.0}),
    )]
    preparation = PreparationContext(100.0, 2, {10: 0, 30: 1}, frozenset({10, 30}), 3, 0.5)
    requests = component_requests(
        formulation, generators=generators,
        load_units=[Load(bus=30, p_load_mw=30, q_load_mvar=10,
                         device_id="demand", shedding_cost_per_mwh=1000)],
        load_inputs=LoadInputs(np.array([[30], [0], [40]]), np.array([[10], [3], [12]])),
        storage_units=storage,
        nondispatchable_units=[NondispatchableUnit(
            bus=30, p_available=4, apparent_power_rating=8,
        )],
        nondispatchable_inputs=NondispatchableInputs(np.array([[4], [6], [3]])),
        hvdc_links=[HVDCLink(from_bus=10, to_bus=30, p_min_mw=-10, p_max_mw=-1,
                            loss_percent=3)],
    )
    return prepare_components(requests, formulation, preparation), preparation


def _state(formulation, shape=(2, 3), enforce=True):
    voltage = cp.Variable(shape)
    voltage.value = np.full(shape, 1.04**2 if formulation == "socp" else 1.04)
    cls = SquaredVoltageNetworkState if formulation == "socp" else ACNetworkState
    return cls(voltage, (0,), enforce)


@pytest.mark.parametrize("terminal", ["hard", "soft"])
def test_vectorized_socp_reuses_complete_ac_device_contributions(terminal):
    built = []
    for formulation in ("ac", "socp"):
        prepared, prep = _prepare(formulation, terminal)
        context = VectorizedContext(formulation, 3, 0.5, 100, prep.ext_to_int, _state(formulation))
        contributions = assemble_component_vectorized(prepared, context)
        aggregate = aggregate_vectorized_contributions(contributions)
        values = {
            "Pg": [[0.6, 0.5, 0.7]], "Qg": [[0.1, -0.1, 0.2]],
            "b": [[1, 2, 3]], "b_q": [[2, -1, 0]],
            "soc": [[12, 11.5, 10.5, 9]],
            "p_nd": [[3, 4, 2]], "q_nd": [[1, -1, 0.5]],
            "load_shed_fraction": [[0.2, 0, 0.1]],
            "p_hvdc_in": [[-2, -3, -4]], "p_hvdc_out": [[1.94, 2.91, 3.88]],
        }
        assert set(aggregate.variables) == set(values)
        for name, value in values.items():
            aggregate.variables[name].value = np.array(value)
        model = aggregate.model
        constraints = (*model.operating_constraints, *model.network_constraints, *model.horizon.constraints)
        assert all(constraint.is_dcp() for constraint in constraints)
        cost = integrate_vectorized_stage_cost_rate(model.stage_cost_rate, 0.5)
        if model.horizon.terminal_cost is not None:
            cost += model.horizon.terminal_cost
        assert cost.is_dcp()
        component_costs = integrate_vectorized_component_stage_costs(contributions, 0.5)
        projections = vectorized_component_result_projections(aggregate, component_costs)
        variables = publish_vectorized_component_variables(aggregate, {})
        expressions = publish_vectorized_component_expressions(aggregate, component_costs)
        metadata = publish_component_metadata(prepared, {})
        assert "Qgmin" in metadata and "load_device_ids" in metadata
        np.testing.assert_array_equal(
            projections.variables["soc"].project(variables["soc"].value, 3),
            np.array([[11.5], [10.5], [9]]),
        )
        assert expressions["energy_not_served"].value == pytest.approx(5.0)
        # Reactive shedding follows the same served fraction; zero active load
        # cannot shed its nonzero reactive demand.
        np.testing.assert_allclose(model.expressions["q_load_served"].value, [[8, 3, 10.8]])
        built.append((model, constraints, cost, expressions, metadata))
    ac, socp = built
    for channel in ("p_pu", "q_pu"):
        np.testing.assert_allclose(getattr(ac[0].injection, channel).value,
                                   getattr(socp[0].injection, channel).value)
    assert len(ac[1]) == len(socp[1])
    for left, right in zip(ac[1], socp[1], strict=True):
        np.testing.assert_allclose(left.violation(), right.violation(), atol=1e-12)
    assert ac[2].value == pytest.approx(socp[2].value)
    assert ac[3].keys() == socp[3].keys()
    for name in ac[3]:
        np.testing.assert_allclose(ac[3][name].value, socp[3][name].value)
    assert ac[4].keys() == socp[4].keys()


@pytest.mark.parametrize("terminal", ["hard", "soft"])
def test_socp_step_and_horizon_assembly_preserve_device_physics(terminal):
    records = []
    for formulation in ("ac", "socp"):
        prepared, prep = _prepare(formulation, terminal)
        steps, aggregates = [], []
        for step in range(3):
            context = StepContext(formulation, step, 100, prep.ext_to_int,
                                  _state(formulation, (2,)))
            contributions = assemble_component_step(prepared, context)
            aggregate = aggregate_step_contributions(contributions)
            for name, variable in aggregate.variables.items():
                variable.value = np.full(variable.shape, 0.2 if name != "soc" else 10.0)
            steps.append(contributions)
            aggregates.append(aggregate)
        horizon = aggregate_horizon_contributions(assemble_component_horizon(
            prepared, steps, HorizonContext(formulation, 3, 0.5),
        ))
        assert all(c.is_dcp() for c in horizon.constraints)
        records.append((aggregates, horizon))
    for ac, socp in zip(records[0][0], records[1][0], strict=True):
        assert ac.variables.keys() == socp.variables.keys()
        np.testing.assert_allclose(ac.injection.p_pu.value, socp.injection.p_pu.value)
        np.testing.assert_allclose(ac.injection.q_pu.value, socp.injection.q_pu.value)
        assert ac.cost.value == pytest.approx(socp.cost.value)
        for a, b in zip(ac.operating_constraints, socp.operating_constraints, strict=True):
            np.testing.assert_allclose(a.violation(), b.violation())
    for a, b in zip(records[0][1].constraints, records[1][1].constraints, strict=True):
        np.testing.assert_allclose(a.violation(), b.violation())


@pytest.mark.parametrize("formulation", ["ac", "socp", "lossy_dc", "singlenode_dc"])
def test_reactive_capability_is_independent_of_convexity(formulation):
    assert supports_reactive_power(formulation) is (formulation in ("ac", "socp"))


@pytest.mark.parametrize("adapter", [GENERATOR_ADAPTER, STORAGE_ADAPTER,
                                     NONDISPATCHABLE_ADAPTER, HVDC_ADAPTER, LOAD_ADAPTER])
def test_socp_registration_reuses_device_binding(adapter):
    assert adapter.formulations["socp"] is adapter.formulations["ac"]
    assert adapter.formulations["socp"].capability is FormulationCapability.ACTIVE


@pytest.mark.parametrize("state", [DCNetworkState(), ACNetworkState(cp.Variable(2), (), False)])
@pytest.mark.parametrize("context_type", [StepContext, VectorizedContext])
def test_socp_rejects_wrong_network_representation(state, context_type):
    kwargs = {"step": 0} if context_type is StepContext else {"horizon_steps": 1, "delta": 1}
    with pytest.raises(ValueError, match="requires SquaredVoltageNetworkState"):
        context_type("socp", base_mva=100, ext_to_int={10: 0, 30: 1},
                     network_state=state, **kwargs)


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_existing_formulations_reject_squared_voltage_state(formulation):
    with pytest.raises(ValueError, match="requires"):
        StepContext(formulation, 0, 100, {10: 0, 30: 1}, _state("socp"))


def test_setpoint_selection_and_binding_have_one_rule():
    first = generator.DispatchableGenerator(bus=10, p_max_mw=10, vg=1.03)
    units = [replace(first, status=0, vg=1.2), first,
             replace(first, vg=0.95), replace(first, bus=30, vg=1.1)]
    selected = generator.voltage_setpoints(units, {10: 0, 30: 1}, (0,))
    assert selected == {0: 1.03}
    for squared in (False, True):
        voltage = cp.Variable((2, 3))
        voltage.value = np.full((2, 3), 1.03**2 if squared else 1.03)
        constraints = generator.voltage_setpoint_constraints(
            units, voltage, {10: 0, 30: 1}, (0,), enforce_vset=True, squared=squared,
        )
        assert len(constraints) == 1
        np.testing.assert_array_equal(constraints[0].violation(), 0)
        assert generator.voltage_setpoint_constraints(
            units, voltage, {}, (), enforce_vset=False, squared=squared,
        ) == []


@pytest.mark.parametrize("vectorized", [False, True])
@pytest.mark.parametrize("formulation", ["ac", "socp"])
@pytest.mark.parametrize("enforce", [False, True])
def test_negative_selected_setpoint_rejected_only_by_enabled_squared_binding(
    vectorized, formulation, enforce,
):
    prep = PreparationContext(100, 1, {10: 0}, frozenset({10}), 2, 0.5)
    units = [generator.DispatchableGenerator(bus=10, p_max_mw=10, vg=-1.03)]
    prepared = prepare_components(
        component_requests(formulation, generators=units), formulation, prep,
    )
    shape = (1, 2) if vectorized else (1,)
    state = _state(formulation, shape, enforce)
    if vectorized:
        context = VectorizedContext(formulation, 2, 0.5, 100, prep.ext_to_int, state)
        assemble = assemble_component_vectorized
    else:
        context = StepContext(formulation, 0, 100, prep.ext_to_int, state)
        assemble = assemble_component_step
    if formulation == "socp" and enforce:
        with pytest.raises(ValueError, match="nonnegative selected.*bus 0.*vg=-1.03"):
            assemble(prepared, context)
        return
    contributions = assemble(prepared, context)
    contribution = contributions["generator"]
    constraints = (contribution.model.network_constraints if vectorized
                   else contribution.network_constraints)
    if not enforce:
        assert constraints == ()
    else:
        # Existing AC retains the negative equality; it is not squared or rejected.
        state.voltage.value = np.full(shape, -1.03)
        assert len(constraints) == 1
        np.testing.assert_array_equal(constraints[0].violation(), 0)


def test_unselected_negative_setpoints_do_not_block_squared_binding():
    first = generator.DispatchableGenerator(bus=10, p_max_mw=10, vg=1.03)
    units = [replace(first, status=0, vg=-1.2), first,
             replace(first, vg=-0.95), replace(first, bus=30, vg=-1.1)]
    voltage = cp.Variable(2)
    voltage.value = np.full(2, 1.03**2)
    constraints = generator.voltage_setpoint_constraints(
        units, voltage, {10: 0, 30: 1}, (0,), enforce_vset=True, squared=True,
    )
    assert len(constraints) == 1
    np.testing.assert_array_equal(constraints[0].violation(), 0)


@pytest.mark.parametrize("family", [
    VariableBoxFamily.DISPATCHABLE_P, VariableBoxFamily.DISPATCHABLE_Q,
    VariableBoxFamily.STORAGE_SOC, VariableBoxFamily.NONDISPATCHABLE_REAL_POWER,
    VariableBoxFamily.HVDC_INPUT_POWER, VariableBoxFamily.LOAD_SHED_FRACTION,
])
def test_socp_has_explicit_device_bound_policy_not_inherited_qualification(family):
    decision = box_representation_decision("socp", family)
    assert decision.representation == "explicit"
    assert decision.authority == "socp_explicit_policy"
    assert not decision.requires_focused_qualification
