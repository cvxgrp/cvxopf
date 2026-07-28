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
)


def _prepare(units, context):
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
    data = adapter.prepare((object(),), context)
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
    assert injection.p is variables["p"]
    assert injection.q is None
    assert not hasattr(injection, "engineering_scale")


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
