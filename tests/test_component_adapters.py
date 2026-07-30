"""Equivalence tests for typed adapters over existing component functions."""

import cvxpy as cp
import numpy as np
import pytest

from cvxopf import generator
from cvxopf._component_adapter import (
    ACNetworkState,
    DCNetworkState,
    HorizonContext,
    PreparationContext,
    StepContext,
    bind_injection_scale,
)
from cvxopf._component_adapters import (
    GENERATOR_ADAPTER,
    NONDISPATCHABLE_ADAPTER,
    STORAGE_ADAPTER,
    NondispatchableInputs,
)
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.storage import StorageUnitIdeal
from cvxopf.testcases import case9


def _case_context(horizon_steps=1, is_multistep=None):
    case = case9()
    ext_bus_ids = frozenset(case["bus"][:, 0].astype(int))
    ext_to_int = {
        bus_id: index for index, bus_id in enumerate(sorted(ext_bus_ids))
    }
    return case, PreparationContext(
        base_mva=float(case["baseMVA"]),
        nb=len(ext_bus_ids),
        ext_to_int=ext_to_int,
        ext_bus_ids=ext_bus_ids,
        horizon_steps=horizon_steps,
        delta=1.0,
        is_multistep=is_multistep,
    )


def _variables(specs):
    return {
        spec.name: cp.Variable(spec.shape, **dict(spec.attributes))
        for spec in specs
    }


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_generator_adapter_preserves_injections_constraints_and_cost(formulation):
    case, preparation = _case_context()
    units = tuple(generator.gen_from_matpower(case["gen"], case["gencost"]))
    prepared = GENERATOR_ADAPTER.prepare(units, None, preparation)
    metadata = GENERATOR_ADAPTER.metadata(prepared, formulation)
    assert ("Qgmin" in metadata) is (formulation == "ac")
    assert ("Qgmax" in metadata) is (formulation == "ac")
    assert set(metadata) == (
        {"ng", "Cg", "gen_bus", "gencost", "Pgmin", "Pgmax",
         "Qgmin", "Qgmax"}
        if formulation == "ac"
        else {"ng", "Cg", "gen_bus", "gencost", "Pgmin", "Pgmax"}
    )
    if formulation == "ac":
        network_state = ACNetworkState(
            voltage=cp.Variable(preparation.nb),
            controlled_buses=(0, 1, 2),
            enforce_vset=False,
        )
    else:
        network_state = DCNetworkState()
    step = StepContext(
        formulation,
        step=0,
        base_mva=preparation.base_mva,
        ext_to_int=preparation.ext_to_int,
        network_state=network_state,
    )
    binding = GENERATOR_ADAPTER.formulations[formulation]
    assert binding.variable_specs is not None
    assert binding.injections is not None
    assert binding.operating_constraints is not None
    assert binding.network_constraints is not None
    assert binding.step_cost is not None
    assert binding.horizon is not None

    variables = _variables(binding.variable_specs(units, prepared, step))
    injection = binding.injections(units, prepared, variables, step)
    constraints = binding.operating_constraints(
        units, prepared, variables, step
    )
    network_constraints = binding.network_constraints(
        units, prepared, variables, step
    )
    cost = binding.step_cost(units, prepared, variables, step)
    horizon = binding.horizon(
        units,
        prepared,
        {name: [variable] for name, variable in variables.items()},
        HorizonContext(formulation, 1, 1.0),
    )

    assert injection.inv_base_mva is None
    assert injection.p_pu is not None
    if formulation == "ac":
        assert injection.q_pu is not None
    else:
        assert injection.q_pu is None
    assert all(constraint.is_dcp() for constraint in constraints)
    assert network_constraints == ()
    assert cost.is_dcp()
    assert horizon.constraints == ()
    variables["Pg"].value = np.array([0.5, 0.6, 0.7])
    expected_p, expected_q, expected_scale = (
        generator.ac_injections(
            list(units),
            variables["Pg"],
            variables["Qg"],
            dict(preparation.ext_to_int),
            incidence=prepared["Cg"],
        )
        if formulation == "ac"
        else generator.dc_injections(
            list(units),
            variables["Pg"],
            dict(preparation.ext_to_int),
            incidence=prepared["Cg"],
        )
    )
    assert expected_scale is None
    np.testing.assert_allclose(injection.p_pu.value, expected_p.value)
    if formulation == "ac":
        variables["Qg"].value = np.array([0.1, -0.1, 0.2])
        np.testing.assert_allclose(injection.q_pu.value, expected_q.value)


@pytest.mark.parametrize(
    ("formulation", "expected_variables"),
    [
        ("ac", {"p_nd", "q_nd"}),
        ("lossy_dc", {"p_nd"}),
        ("singlenode_dc", {"p_nd"}),
    ],
)
def test_nd_adapter_preserves_scaled_injections_and_dcp_constraints(
    formulation, expected_variables
):
    _, preparation = _case_context(horizon_steps=2)
    units = (
        NondispatchableUnit(
            bus=5,
            p_available=20.0,
            apparent_power_rating=25.0,
            device_id="nd",
        ),
    )
    inputs = NondispatchableInputs(np.array([[20.0], [15.0]]))
    prepared = NONDISPATCHABLE_ADAPTER.prepare(
        units, inputs, preparation
    )
    step = StepContext(
        formulation,
        step=1,
        base_mva=preparation.base_mva,
        ext_to_int=preparation.ext_to_int,
        network_state=(
            ACNetworkState(cp.Variable(preparation.nb), (), False)
            if formulation == "ac"
            else DCNetworkState()
        ),
    )
    binding = NONDISPATCHABLE_ADAPTER.formulations[formulation]
    assert binding.variable_specs is not None
    assert binding.injections is not None
    assert binding.operating_constraints is not None
    assert binding.horizon is not None
    assert binding.network_constraints is None
    assert binding.step_cost is None

    variables = _variables(binding.variable_specs(units, prepared, step))
    assert set(variables) == expected_variables
    injection = binding.injections(units, prepared, variables, step)
    constraints = binding.operating_constraints(
        units, prepared, variables, step
    )

    assert injection.inv_base_mva is not None
    assert injection.inv_base_mva.value is None
    bind_injection_scale(injection, preparation.base_mva)
    variables["p_nd"].value = np.array([10.0])
    if formulation == "ac":
        variables["q_nd"].value = np.array([5.0])
    expected_p = np.zeros(preparation.nb)
    expected_p[preparation.ext_to_int[5]] = 0.1
    np.testing.assert_allclose(injection.p_pu.value, expected_p)
    if formulation == "ac":
        expected_q = np.zeros(preparation.nb)
        expected_q[preparation.ext_to_int[5]] = 0.05
        np.testing.assert_allclose(injection.q_pu.value, expected_q)
    else:
        assert injection.q_pu is None
    assert all(constraint.is_dcp() for constraint in constraints)
    assert constraints[1].args[1].value[0] == pytest.approx(15.0)


def test_nd_adapter_publishes_horizon_appropriate_availability_key():
    _, single_context = _case_context(horizon_steps=1)
    unit = NondispatchableUnit(5, 20.0, 25.0, device_id="nd")
    single = NONDISPATCHABLE_ADAPTER.prepare(
        (unit,), NondispatchableInputs(np.array([[20.0]])), single_context
    )
    single_metadata = NONDISPATCHABLE_ADAPTER.metadata(single, "ac")
    assert "nd_p_available" in single_metadata
    assert "nd_available" not in single_metadata

    _, multi_context = _case_context(horizon_steps=2)
    multi = NONDISPATCHABLE_ADAPTER.prepare(
        (unit,),
        NondispatchableInputs(np.array([[20.0], [15.0]])),
        multi_context,
    )
    multi_metadata = NONDISPATCHABLE_ADAPTER.metadata(multi, "lossy_dc")
    assert "nd_available" in multi_metadata
    assert "nd_p_available" not in multi_metadata

    _, one_step_multi_context = _case_context(
        horizon_steps=1, is_multistep=True
    )
    one_step_multi = NONDISPATCHABLE_ADAPTER.prepare(
        (unit,),
        NondispatchableInputs(np.array([[20.0]])),
        one_step_multi_context,
    )
    one_step_multi_metadata = NONDISPATCHABLE_ADAPTER.metadata(
        one_step_multi, "lossy_dc"
    )
    assert "nd_available" in one_step_multi_metadata
    assert "nd_p_available" not in one_step_multi_metadata


@pytest.mark.parametrize(
    "available",
    [
        np.ones((1, 2)),
        np.array([[np.nan]]),
        np.array([[-1.0]]),
    ],
)
def test_nd_adapter_rejects_invalid_normalized_availability(available):
    _, context = _case_context(horizon_steps=1)
    unit = NondispatchableUnit(5, 20.0, 25.0, device_id="nd")
    with pytest.raises(ValueError, match="availability"):
        NONDISPATCHABLE_ADAPTER.prepare(
            (unit,), NondispatchableInputs(available), context
        )


@pytest.mark.parametrize(
    ("formulation", "expected_variables"),
    [
        ("ac", {"b", "b_q", "soc"}),
        ("lossy_dc", {"b", "soc"}),
        ("singlenode_dc", {"b", "soc"}),
    ],
)
def test_storage_adapter_preserves_step_and_horizon_contributions(
    formulation, expected_variables
):
    _, preparation = _case_context(horizon_steps=2)
    unit = StorageUnitIdeal(
        bus=1,
        apparent_power_rating=20.0,
        capacity=50.0,
        initial_soc=25.0,
        aging_weight=0.1,
        terminal_soc=30.0,
        terminal_cost="quadratic",
        terminal_weight=2.0,
    )
    units = (unit,)
    prepared = STORAGE_ADAPTER.prepare(units, None, preparation)
    metadata = STORAGE_ADAPTER.metadata(prepared, formulation)
    assert metadata["storage_delta"] == pytest.approx(1.0)

    step = StepContext(
        formulation,
        step=0,
        base_mva=preparation.base_mva,
        ext_to_int=preparation.ext_to_int,
        network_state=(
            ACNetworkState(cp.Variable(preparation.nb), (), False)
            if formulation == "ac"
            else DCNetworkState()
        ),
    )
    binding = STORAGE_ADAPTER.formulations[formulation]
    assert binding.variable_specs is not None
    assert binding.injections is not None
    assert binding.operating_constraints is not None
    assert binding.step_cost is not None
    assert binding.horizon is not None
    assert binding.network_constraints is None

    variables_0 = _variables(
        binding.variable_specs(units, prepared, step)
    )
    variables_1 = _variables(
        binding.variable_specs(units, prepared, step)
    )
    assert set(variables_0) == expected_variables
    injection = binding.injections(
        units, prepared, variables_0, step
    )
    assert injection.inv_base_mva is not None
    assert injection.inv_base_mva.value is None
    bind_injection_scale(injection, preparation.base_mva)
    assert injection.p_pu is not None
    assert (injection.q_pu is not None) is (formulation == "ac")
    constraints = binding.operating_constraints(
        units, prepared, variables_0, step
    )
    assert all(constraint.is_dcp() for constraint in constraints)
    assert binding.step_cost(
        units, prepared, variables_0, step
    ).is_dcp()

    horizon = binding.horizon(
        units,
        prepared,
        {
            "b": [variables_0["b"], variables_1["b"]],
            "soc": [variables_0["soc"], variables_1["soc"]],
        },
        HorizonContext(formulation, 2, 1.0),
    )
    assert len(horizon.constraints) == 2
    assert all(constraint.is_dcp() for constraint in horizon.constraints)
    assert horizon.terminal_cost is not None
    assert horizon.terminal_cost.is_dcp()


def test_storage_adapter_t1_hard_terminal_policy_has_no_terminal_cost():
    _, preparation = _case_context(horizon_steps=1)
    unit = StorageUnitIdeal(
        bus=1,
        apparent_power_rating=20.0,
        capacity=50.0,
        initial_soc=25.0,
        terminal_soc=30.0,
        terminal_constraint="shortfall",
    )
    units = (unit,)
    prepared = STORAGE_ADAPTER.prepare(units, None, preparation)
    step = StepContext(
        "lossy_dc",
        step=0,
        base_mva=preparation.base_mva,
        ext_to_int=preparation.ext_to_int,
        network_state=DCNetworkState(),
    )
    binding = STORAGE_ADAPTER.formulations["lossy_dc"]
    assert binding.variable_specs is not None
    assert binding.horizon is not None
    variables = _variables(
        binding.variable_specs(units, prepared, step)
    )

    horizon = binding.horizon(
        units,
        prepared,
        {"b": [variables["b"]], "soc": [variables["soc"]]},
        HorizonContext("lossy_dc", 1, 1.0),
    )

    assert len(horizon.constraints) == 2
    assert horizon.terminal_cost is None
