"""Tests for the private M16+ typed component contracts."""

import cvxpy as cp
import pytest

from cvxopf._component_adapter import (
    ACNetworkState,
    ComponentAdapter,
    DCNetworkState,
    FormulationAdapter,
    FormulationCapability,
    HorizonContext,
    HorizonContribution,
    InjectionContribution,
    PreparationContext,
    PreparedComponent,
    StepContribution,
    StepContext,
    VariableSpec,
    bind_injection_scale,
)


def _prepare(units, inputs, context):
    return {"count": len(units), "base_mva": context.base_mva}


def _metadata(prepared, formulation):
    return {"count": prepared["count"]}


def _variable_specs(units, prepared, context):
    return (VariableSpec("p", (int(prepared["count"]),)),)


def _injections(units, prepared, variables, context):
    return InjectionContribution(variables["p"], None)


def _constraints(units, prepared, variables, context):
    return (variables["p"] >= 0,)


def _cost(units, prepared, variables, context):
    return cp.sum(variables["p"])


def _horizon(units, prepared, variable_history, context):
    return HorizonContribution()


def _active():
    return FormulationAdapter(
        capability=FormulationCapability.ACTIVE,
        variable_specs=_variable_specs,
        injections=_injections,
        operating_constraints=_constraints,
        step_cost=_cost,
        horizon=_horizon,
    )


def _adapter():
    return ComponentAdapter(
        name="test_component",
        prepare=_prepare,
        metadata=_metadata,
        formulations={
            "ac": _active(),
            "lossy_dc": _active(),
            "singlenode_dc": FormulationAdapter(
                capability=FormulationCapability.NULL
            ),
        },
    )


def test_adapter_preserves_builder_variable_ownership():
    adapter = _adapter()
    context = PreparationContext(
        base_mva=100.0,
        nb=2,
        ext_to_int={1: 0, 2: 1},
        ext_bus_ids=frozenset({1, 2}),
        horizon_steps=1,
        delta=1.0,
    )
    data = adapter.prepare((object(),), None, context)
    prepared = PreparedComponent(adapter, (object(),), data)
    step_context = StepContext(
        "ac",
        0,
        100.0,
        {1: 0, 2: 1},
        ACNetworkState(cp.Variable(2), (0,), False),
    )
    binding = adapter.formulations["ac"]

    specs = binding.variable_specs(
        prepared.units, prepared.data, step_context
    )
    assert specs == (VariableSpec("p", (1,)),)
    assert all(not isinstance(spec, cp.Variable) for spec in specs)

    variables = {"p": cp.Variable(1)}
    injection = binding.injections(
        prepared.units, prepared.data, variables, step_context
    )
    assert injection.p_pu is variables["p"]
    assert injection.q_pu is None
    assert injection.inv_base_mva is None


def test_active_binding_requires_core_hooks():
    with pytest.raises(ValueError, match="active formulation adapters require"):
        FormulationAdapter(capability=FormulationCapability.ACTIVE)


@pytest.mark.parametrize(
    "capability",
    [FormulationCapability.NULL, FormulationCapability.UNSUPPORTED],
)
def test_inactive_binding_rejects_hooks(capability):
    with pytest.raises(ValueError, match="cannot define hooks"):
        FormulationAdapter(
            capability=capability,
            variable_specs=_variable_specs,
        )


def test_null_binding_cannot_publish_step_expressions():
    with pytest.raises(ValueError, match="cannot define hooks"):
        FormulationAdapter(
            capability=FormulationCapability.NULL,
            step_expressions=lambda units, prepared, variables, context: {
                "forbidden": cp.Constant(0.0)
            },
        )


def test_component_adapter_requires_explicit_formulation_capabilities():
    with pytest.raises(ValueError, match="contain exactly"):
        ComponentAdapter(
            name="incomplete",
            prepare=_prepare,
            metadata=_metadata,
            formulations={"ac": _active()},
        )


def test_component_adapter_name_must_be_nonempty():
    with pytest.raises(ValueError, match="name must be nonempty"):
        ComponentAdapter(
            name="",
            prepare=_prepare,
            metadata=_metadata,
            formulations={
                formulation: _active()
                for formulation in ("ac", "lossy_dc", "singlenode_dc")
            },
        )


def test_variable_specification_name_must_be_nonempty():
    with pytest.raises(
        ValueError, match="variable specification name must be nonempty"
    ):
        VariableSpec("", (1,))


def test_step_and_horizon_contexts_keep_temporal_roles_separate():
    step = StepContext(
        "lossy_dc",
        step=2,
        base_mva=100.0,
        ext_to_int={1: 0},
        network_state=DCNetworkState(),
    )
    horizon = HorizonContext("lossy_dc", horizon_steps=4, delta=0.25)
    assert step.step == 2
    assert horizon.horizon_steps == 4
    assert horizon.delta == pytest.approx(0.25)


@pytest.mark.parametrize("step", [True, -1, 0.5])
def test_step_context_rejects_invalid_step(step):
    with pytest.raises(ValueError, match="step must be a nonnegative integer"):
        StepContext(
            "lossy_dc",
            step,
            100.0,
            {1: 0},
            DCNetworkState(),
        )


@pytest.mark.parametrize(
    ("base_mva", "error"),
    [
        (True, TypeError),
        ("100", TypeError),
        (0.0, ValueError),
        (float("nan"), ValueError),
    ],
)
def test_step_context_rejects_invalid_base_mva(base_mva, error):
    with pytest.raises(error, match="base_mva must be"):
        StepContext(
            "lossy_dc",
            0,
            base_mva,
            {1: 0},
            DCNetworkState(),
        )


@pytest.mark.parametrize("horizon_steps", [True, 0, 1.5])
def test_horizon_context_rejects_invalid_step_count(horizon_steps):
    with pytest.raises(
        ValueError, match="horizon_steps must be a positive integer"
    ):
        HorizonContext("lossy_dc", horizon_steps, 1.0)


@pytest.mark.parametrize(
    ("delta", "error"),
    [
        (True, TypeError),
        ("1", TypeError),
        (0.0, ValueError),
        (float("inf"), ValueError),
        (float("nan"), ValueError),
    ],
)
def test_horizon_context_rejects_invalid_delta(delta, error):
    with pytest.raises(error, match="delta must be"):
        HorizonContext("lossy_dc", 1, delta)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"base_mva": 0.0}, "base_mva must be finite and > 0"),
        ({"base_mva": float("nan")}, "base_mva must be finite and > 0"),
        ({"nb": 0}, "nb must be a positive integer"),
        ({"horizon_steps": 0}, "horizon_steps must be a positive integer"),
        ({"delta": 0.0}, "delta must be finite and > 0"),
        (
            {"horizon_steps": 2, "is_multistep": False},
            "is_multistep must be True when horizon_steps > 1",
        ),
    ],
)
def test_preparation_context_rejects_invalid_primitives(overrides, message):
    values = {
        "base_mva": 100.0,
        "nb": 1,
        "ext_to_int": {1: 0},
        "ext_bus_ids": frozenset({1}),
        "horizon_steps": 1,
        "delta": 1.0,
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        PreparationContext(**values)


@pytest.mark.parametrize("is_multistep", [False, True])
def test_preparation_context_preserves_both_t1_modes(is_multistep):
    context = PreparationContext(
        base_mva=100.0,
        nb=1,
        ext_to_int={1: 0},
        ext_bus_ids=frozenset({1}),
        horizon_steps=1,
        delta=1.0,
        is_multistep=is_multistep,
    )
    assert context.is_multistep is is_multistep


@pytest.mark.parametrize(
    ("formulation", "network_state", "message"),
    [
        ("ac", DCNetworkState(), "requires ACNetworkState"),
        (
            "lossy_dc",
            ACNetworkState(cp.Variable(1), (), False),
            "requires DCNetworkState",
        ),
        (
            "singlenode_dc",
            ACNetworkState(cp.Variable(1), (), False),
            "requires DCNetworkState",
        ),
    ],
)
def test_step_context_rejects_inconsistent_network_state(
    formulation, network_state, message
):
    with pytest.raises(ValueError, match=message):
        StepContext(
            formulation,
            step=0,
            base_mva=100.0,
            ext_to_int={1: 0},
            network_state=network_state,
        )


def test_prepared_data_and_formulation_registry_are_read_only_copies():
    formulations = {
        "ac": _active(),
        "lossy_dc": _active(),
        "singlenode_dc": FormulationAdapter(
            capability=FormulationCapability.NULL
        ),
    }
    adapter = ComponentAdapter(
        name="test_component",
        prepare=_prepare,
        metadata=_metadata,
        formulations=formulations,
    )
    data = {"count": 1}
    prepared = PreparedComponent(adapter, (object(),), data)

    formulations["ac"] = FormulationAdapter(
        capability=FormulationCapability.NULL
    )
    data["count"] = 2
    assert adapter.formulations["ac"].capability is FormulationCapability.ACTIVE
    assert prepared.data["count"] == 1
    with pytest.raises(TypeError):
        adapter.formulations["ac"] = formulations["ac"]
    with pytest.raises(TypeError):
        prepared.data["count"] = 3


def test_context_and_contribution_mappings_are_read_only_copies():
    ext_to_int = {1: 0}
    context = PreparationContext(
        base_mva=100.0,
        nb=1,
        ext_to_int=ext_to_int,
        ext_bus_ids=frozenset({1}),
        horizon_steps=1,
        delta=1.0,
    )
    attributes = {"nonneg": True}
    spec = VariableSpec("p", (1,), attributes)
    variables = {"p": cp.Variable(1)}
    expressions = {"reported_p": variables["p"]}
    contribution = StepContribution(
        variables=variables,
        injection=InjectionContribution(variables["p"], None),
        expressions=expressions,
    )

    ext_to_int[1] = 2
    attributes["nonneg"] = False
    variables.clear()
    expressions.clear()
    assert context.ext_to_int[1] == 0
    assert spec.attributes["nonneg"] is True
    assert set(contribution.variables) == {"p"}
    assert set(contribution.expressions) == {"reported_p"}
    with pytest.raises(TypeError):
        spec.attributes["nonneg"] = False


def test_sequence_fields_are_normalized_to_tuples():
    adapter = _adapter()
    unit = object()
    units = [unit]
    controlled_buses = [0, 1]
    variable = cp.Variable(2)
    operating_constraints = [variable >= 0]
    network_constraints = [variable <= 1]
    horizon_constraints = [variable[0] == variable[1]]

    prepared = PreparedComponent(adapter, units, {})
    network_state = ACNetworkState(variable, controlled_buses, False)
    step = StepContribution(
        variables={"p": variable},
        injection=InjectionContribution(variable, None),
        operating_constraints=operating_constraints,
        network_constraints=network_constraints,
    )
    horizon = HorizonContribution(constraints=horizon_constraints)

    units.clear()
    controlled_buses.clear()
    operating_constraints.clear()
    network_constraints.clear()
    horizon_constraints.clear()

    assert prepared.units == (unit,)
    assert network_state.controlled_buses == (0, 1)
    assert len(step.operating_constraints) == 1
    assert len(step.network_constraints) == 1
    assert len(horizon.constraints) == 1
    assert isinstance(prepared.units, tuple)
    assert isinstance(network_state.controlled_buses, tuple)
    assert isinstance(step.operating_constraints, tuple)
    assert isinstance(step.network_constraints, tuple)
    assert isinstance(horizon.constraints, tuple)


def test_assembler_binds_component_created_scaling_parameter_once():
    inv_base_mva = cp.Parameter(nonneg=True)
    p_mw = cp.Variable(1)
    contribution = InjectionContribution(
        p_pu=cp.multiply(inv_base_mva, p_mw),
        q_pu=None,
        inv_base_mva=inv_base_mva,
    )
    bind_injection_scale(contribution, 100.0)
    p_mw.value = [25.0]

    assert inv_base_mva.value == pytest.approx(0.01)
    assert contribution.p_pu.value[0] == pytest.approx(0.25)
    with pytest.raises(ValueError, match="already bound"):
        bind_injection_scale(contribution, 100.0)


@pytest.mark.parametrize("base_mva", [0.0, -1.0, float("nan"), float("inf")])
def test_assembler_rejects_invalid_network_base(base_mva):
    contribution = InjectionContribution(cp.Constant([0.0]), None)
    with pytest.raises(ValueError, match="base_mva"):
        bind_injection_scale(contribution, base_mva)
