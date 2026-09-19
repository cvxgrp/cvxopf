"""Focused tests for one-call vectorized component assembly."""

from dataclasses import dataclass, replace
from typing import Mapping, Sequence
from unittest.mock import Mock

import cvxpy as cp
import numpy as np
import pytest

from cvxopf._component_adapter import (
    ComponentAdapter,
    DCNetworkState,
    FormulationAdapter,
    FormulationCapability,
    HorizonContribution,
    InjectionContribution,
    PreparationContext,
    VariableSpec,
    VectorizedContext,
    VectorizedModelContribution,
)
from cvxopf._component_assembly import (
    ComponentRequest,
    aggregate_vectorized_contributions,
    assemble_component_vectorized,
    integrate_vectorized_component_stage_costs,
    integrate_vectorized_stage_cost_rate,
    prepare_components,
    publish_vectorized_component_expressions,
    publish_vectorized_component_variables,
    vectorized_component_result_projections,
)
from cvxopf._temporal_assembly import HorizonVariableSpec
from cvxopf.problem import OPFBuild


@dataclass(frozen=True)
class _Unit:
    bus: int


def _prepare(units, inputs, context):
    incidence = np.zeros((context.nb, len(units)))
    for column, unit in enumerate(units):
        incidence[context.ext_to_int[unit.bus], column] = 1.0
    return {"incidence": incidence}


def _metadata(prepared, formulation):
    return {}


def _step_specs(units, prepared, context):
    return (VariableSpec("p", (len(units),)),)


def _step_injection(units, prepared, variables, context):
    return InjectionContribution(cp.Constant(np.zeros(2)), None)


def _step_constraints(units, prepared, variables, context):
    return (variables["p"] >= 0,)


def _step_horizon(units, prepared, variables, context):
    return HorizonContribution()


_CALLS = {"specs": 0, "assembly": 0}


def _vector_specs(
    units: Sequence[_Unit],
    prepared: Mapping[str, object],
    context: VectorizedContext,
) -> tuple[HorizonVariableSpec, ...]:
    _CALLS["specs"] += 1
    return (HorizonVariableSpec("p", (len(units),)),)


def _vector_assembly(
    units: Sequence[_Unit],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: VectorizedContext,
) -> VectorizedModelContribution:
    _CALLS["assembly"] += 1
    incidence = prepared["incidence"]
    assert isinstance(incidence, np.ndarray)
    scale = cp.Parameter(nonneg=True, name="test_vector_inv_base")
    power = variables["p"]
    return VectorizedModelContribution(
        injection=InjectionContribution(scale * (incidence @ power), None, scale),
        operating_constraints=(power >= 0, power <= 5),
        stage_cost_rate=cp.sum(power, axis=0),
        expressions={"test_power": power},
        horizon=HorizonContribution(
            constraints=(cp.sum(power[:, -1]) <= 8,),
            terminal_cost=cp.sum(power[:, -1]),
            expressions={"test_terminal_power": cp.sum(power[:, -1])},
        ),
    )


def _active(*, vectorized: bool = True) -> FormulationAdapter[_Unit]:
    return FormulationAdapter(
        capability=FormulationCapability.ACTIVE,
        variable_specs=_step_specs,
        injections=_step_injection,
        operating_constraints=_step_constraints,
        horizon=_step_horizon,
        vectorized_variable_specs=_vector_specs if vectorized else None,
        vectorized_assembly=_vector_assembly if vectorized else None,
    )


def _adapter(
    binding: FormulationAdapter[_Unit] | None = None,
) -> ComponentAdapter[_Unit, None]:
    null = FormulationAdapter[_Unit](capability=FormulationCapability.NULL)
    return ComponentAdapter(
        name="test_vectorized",
        prepare=_prepare,
        metadata=_metadata,
        formulations={
            "ac": null,
            "lossy_dc": _active() if binding is None else binding,
            "singlenode_dc": null,
        },
        cost_expression_name="test_cost",
    )


def _prepared(
    binding: FormulationAdapter[_Unit] | None = None,
    *,
    horizon_steps: int = 4,
):
    units = (_Unit(1), _Unit(2))
    context = PreparationContext(
        base_mva=100.0,
        nb=2,
        ext_to_int={1: 0, 2: 1},
        ext_bus_ids=frozenset({1, 2}),
        horizon_steps=horizon_steps,
        delta=0.5,
        is_multistep=True,
    )
    return prepare_components(
        (ComponentRequest(_adapter(binding), units),),
        "lossy_dc",
        context,
    )


def _context(*, horizon_steps: int = 4) -> VectorizedContext:
    return VectorizedContext(
        "lossy_dc",
        horizon_steps=horizon_steps,
        delta=0.5,
        base_mva=100.0,
        ext_to_int={1: 0, 2: 1},
        network_state=DCNetworkState(),
    )


def test_vectorized_component_is_built_once_with_builder_owned_variable():
    _CALLS.update(specs=0, assembly=0)

    contributions = assemble_component_vectorized(_prepared(), _context())
    contribution = contributions["test_vectorized"]
    variable = contribution.variables["p"]

    assert _CALLS == {"specs": 1, "assembly": 1}
    assert variable.shape == (2, 4)
    assert contribution.model.injection.p_pu.shape == (2, 4)
    assert contribution.model.injection.inv_base_mva.value == pytest.approx(0.01)
    assert contribution.model.stage_cost_rate.shape == (4,)
    assert contribution.cost_expression_name == "test_cost"
    assert contribution.variable_specs["p"].shape(4) == (2, 4)
    assert contribution.model.expressions["test_power"] is variable
    assert all(
        constraint.is_dcp()
        for constraint in (
            *contribution.model.operating_constraints,
            *contribution.model.horizon.constraints,
        )
    )
    with pytest.raises(TypeError):
        contribution.variables["other"] = cp.Variable(1)


def test_vectorized_component_multistep_t1_keeps_time_axis_and_one_call():
    _CALLS.update(specs=0, assembly=0)

    contributions = assemble_component_vectorized(
        _prepared(horizon_steps=1),
        _context(horizon_steps=1),
    )
    contribution = contributions["test_vectorized"]

    assert _CALLS == {"specs": 1, "assembly": 1}
    assert contribution.variables["p"].shape == (2, 1)
    assert contribution.model.injection.p_pu.shape == (2, 1)
    assert contribution.model.stage_cost_rate.shape == (1,)


def test_vectorized_aggregation_integration_and_publication_remain_horizon_native():
    contributions = assemble_component_vectorized(_prepared(), _context())
    component = contributions["test_vectorized"]

    aggregate = aggregate_vectorized_contributions(contributions)
    costs = integrate_vectorized_component_stage_costs(contributions, 0.5)
    network_variable = cp.Variable((1, 4), name="network_flow")
    network_expression = cp.Constant(np.zeros((1, 4)))
    variables = publish_vectorized_component_variables(
        aggregate, {"network_flow": network_variable}
    )
    expressions = publish_vectorized_component_expressions(
        aggregate, {"network_loss": network_expression}
    )

    assert aggregate.variables["p"] is component.variables["p"]
    assert aggregate.model.injection.p_pu is component.model.injection.p_pu
    assert aggregate.model.stage_cost_rate is component.model.stage_cost_rate
    assert variables == {
        "network_flow": network_variable,
        "p": component.variables["p"],
    }
    assert all(isinstance(variable, cp.Variable) for variable in variables.values())
    assert expressions["network_loss"] is network_expression
    assert expressions["test_power"] is component.model.expressions["test_power"]
    assert (
        expressions["test_terminal_power"]
        is component.model.horizon.expressions["test_terminal_power"]
    )
    assert set(costs) == {"test_cost"}
    component.variables["p"].value = np.ones((2, 4))
    assert costs["test_cost"].value == pytest.approx(4.0)


def test_vectorized_component_publication_retains_typed_result_projections():
    contributions = assemble_component_vectorized(_prepared(), _context())
    aggregate = aggregate_vectorized_contributions(contributions)
    costs = integrate_vectorized_component_stage_costs(contributions, 0.5)

    projections = vectorized_component_result_projections(
        aggregate,
        integrated_component_costs=costs,
    )

    assert projections.variables["p"].internal_shape(4) == (2, 4)
    assert projections.variables["p"].public_shape(4) == (4, 2)
    assert projections.expressions["test_power"].temporal_view == "interval"
    assert projections.expressions["test_terminal_power"].temporal_view == "horizon"
    assert projections.expressions["test_cost"].temporal_view == "horizon"
    assert projections.expressions["test_cost"].public_shape(4) == ()


def test_vectorized_stage_cost_integration_requires_convex_rate_vector():
    with pytest.raises(ValueError, match="one-dimensional"):
        integrate_vectorized_stage_cost_rate(cp.Constant(1.0), 1.0)
    variable = cp.Variable(3)
    with pytest.raises(ValueError, match="must be convex"):
        integrate_vectorized_stage_cost_rate(-cp.square(variable), 1.0)


def test_vectorized_aggregation_rejects_flat_namespace_collisions():
    contribution = assemble_component_vectorized(_prepared(), _context())[
        "test_vectorized"
    ]
    other_variable = cp.Variable((2, 4), name="other")
    other = replace(
        contribution,
        variables={"other": other_variable},
        variable_specs={"other": HorizonVariableSpec("other", (2,))},
        model=replace(
            contribution.model,
            expressions={"test_power": other_variable},
        ),
    )

    with pytest.raises(ValueError, match="duplicate vectorized variables"):
        aggregate_vectorized_contributions(
            {"first": contribution, "second": contribution}
        )
    with pytest.raises(ValueError, match="duplicate vectorized expressions"):
        aggregate_vectorized_contributions({"first": contribution, "second": other})


def test_vectorized_publication_rejects_formulation_collisions():
    aggregate = aggregate_vectorized_contributions(
        assemble_component_vectorized(_prepared(), _context())
    )

    with pytest.raises(ValueError, match="already published"):
        publish_vectorized_component_variables(aggregate, {"p": cp.Variable((2, 4))})
    with pytest.raises(ValueError, match="compatibility expressions"):
        publish_vectorized_component_expressions(
            aggregate, {"test_power": cp.Constant(np.zeros((2, 4)))}
        )
    with pytest.raises(ValueError, match="horizon expressions"):
        publish_vectorized_component_expressions(
            aggregate,
            {"test_terminal_power": cp.Constant(0.0)},
        )


def test_vectorized_component_cost_names_remain_globally_unique():
    contribution = assemble_component_vectorized(_prepared(), _context())[
        "test_vectorized"
    ]

    with pytest.raises(ValueError, match="duplicate integrated"):
        integrate_vectorized_component_stage_costs(
            {"first": contribution, "second": contribution},
            0.5,
        )


def test_vectorized_convex_solve_forces_scipy_canonicalization():
    problem = Mock()
    build = OPFBuild(
        prob=problem,
        variables={},
        data={},
        formulation="lossy_dc",
        is_convex=True,
        temporal_assembly="vectorized",
    )

    build.solve()

    kwargs = problem.solve.call_args.kwargs
    assert build.canonicalization_backend == "SCIPY"
    assert kwargs["solver"] == cp.CLARABEL
    assert kwargs["nlp"] is False
    assert kwargs["canon_backend"] == cp.SCIPY_CANON_BACKEND
    with pytest.raises(ValueError, match="require SCIPY"):
        build.solve(canon_backend=cp.CPP_CANON_BACKEND)


def test_build_backend_identity_is_representation_and_formulation_specific():
    problem = Mock()

    assert OPFBuild(problem, {}, {}, "lossy_dc", True).canonicalization_backend == "CPP"
    assert (
        OPFBuild(
            problem,
            {},
            {},
            "singlenode_dc",
            True,
            temporal_assembly="vectorized",
        ).canonicalization_backend
        == "SCIPY"
    )
    assert (
        OPFBuild(
            problem,
            {},
            {},
            "ac",
            False,
            temporal_assembly="vectorized",
        ).canonicalization_backend
        == "DNLP_IPOPT"
    )


def test_active_stepwise_binding_may_exist_before_vectorized_migration():
    with pytest.raises(ValueError, match="no vectorized horizon binding"):
        assemble_component_vectorized(_prepared(_active(vectorized=False)), _context())


def test_vectorized_hooks_must_be_registered_as_a_pair():
    with pytest.raises(ValueError, match="must be defined together"):
        replace(_active(), vectorized_assembly=None)


def test_null_binding_cannot_register_vectorized_hooks():
    with pytest.raises(ValueError, match="cannot define hooks"):
        FormulationAdapter(
            capability=FormulationCapability.NULL,
            vectorized_variable_specs=_vector_specs,
            vectorized_assembly=_vector_assembly,
        )


def test_vectorized_assembly_rejects_injection_shape_drift():
    def bad_assembly(units, prepared, variables, context):
        return VectorizedModelContribution(
            injection=InjectionContribution(cp.Constant(np.zeros((2, 3))), None)
        )

    binding = replace(_active(), vectorized_assembly=bad_assembly)
    with pytest.raises(ValueError, match=r"must have shape \(2, 4\)"):
        assemble_component_vectorized(_prepared(binding), _context())


def test_vectorized_assembly_rejects_scalar_stage_cost_rate():
    def bad_assembly(units, prepared, variables, context):
        return VectorizedModelContribution(
            injection=InjectionContribution(cp.Constant(np.zeros((2, 4))), None),
            stage_cost_rate=cp.sum(variables["p"]),
        )

    binding = replace(_active(), vectorized_assembly=bad_assembly)
    with pytest.raises(ValueError, match="stage cost rate must have shape"):
        assemble_component_vectorized(_prepared(binding), _context())


def test_vectorized_assembly_rejects_concave_stage_cost_rate():
    def bad_assembly(units, prepared, variables, context):
        return VectorizedModelContribution(
            injection=InjectionContribution(cp.Constant(np.zeros((2, 4))), None),
            stage_cost_rate=-cp.sum(cp.square(variables["p"]), axis=0),
        )

    binding = replace(_active(), vectorized_assembly=bad_assembly)
    with pytest.raises(ValueError, match="stage cost rate must be convex"):
        assemble_component_vectorized(_prepared(binding), _context())


def test_vectorized_assembly_rejects_concave_terminal_cost():
    def bad_assembly(units, prepared, variables, context):
        return VectorizedModelContribution(
            injection=InjectionContribution(cp.Constant(np.zeros((2, 4))), None),
            horizon=HorizonContribution(
                terminal_cost=-cp.sum(cp.square(variables["p"][:, -1]))
            ),
        )

    binding = replace(_active(), vectorized_assembly=bad_assembly)
    with pytest.raises(ValueError, match="terminal cost must be scalar convex"):
        assemble_component_vectorized(_prepared(binding), _context())


def test_vectorized_assembly_enforces_device_dcp_boundary():
    def bad_assembly(units, prepared, variables, context):
        power = variables["p"]
        return VectorizedModelContribution(
            injection=InjectionContribution(cp.Constant(np.zeros((2, 4))), None),
            operating_constraints=(cp.square(power) == 1,),
        )

    binding = replace(_active(), vectorized_assembly=bad_assembly)
    with pytest.raises(ValueError, match="constraints must be DCP"):
        assemble_component_vectorized(_prepared(binding), _context())


def test_vectorized_assembly_requires_affine_nodal_injections():
    def bad_assembly(units, prepared, variables, context):
        power = variables["p"]
        return VectorizedModelContribution(
            injection=InjectionContribution(cp.square(power), None),
        )

    binding = replace(_active(), vectorized_assembly=bad_assembly)
    with pytest.raises(ValueError, match="injection p_pu must be affine"):
        assemble_component_vectorized(_prepared(binding), _context())


def test_vectorized_step_expression_requires_final_time_axis():
    def bad_assembly(units, prepared, variables, context):
        return VectorizedModelContribution(
            injection=InjectionContribution(cp.Constant(np.zeros((2, 4))), None),
            expressions={"collapsed": cp.sum(variables["p"])},
        )

    binding = replace(_active(), vectorized_assembly=bad_assembly)
    with pytest.raises(ValueError, match="must have final axis 4"):
        assemble_component_vectorized(_prepared(binding), _context())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("horizon_steps", 3, "horizon_steps"),
        ("delta", 1.0, "delta"),
        ("base_mva", 50.0, "base_mva"),
        ("ext_to_int", {1: 1, 2: 0}, "ext_to_int"),
    ],
)
def test_vectorized_context_must_match_preparation(field, value, message):
    context = replace(_context(), **{field: value})

    with pytest.raises(ValueError, match=message):
        assemble_component_vectorized(_prepared(), context)


def test_vectorized_context_requires_formulation_network_state_pairing():
    with pytest.raises(ValueError, match="requires ACNetworkState"):
        VectorizedContext(
            "ac",
            horizon_steps=2,
            delta=1.0,
            base_mva=100.0,
            ext_to_int={1: 0},
            network_state=DCNetworkState(),
        )


def test_duplicate_vectorized_variable_names_are_rejected():
    def duplicate_specs(units, prepared, context):
        return (
            HorizonVariableSpec("p", (2,)),
            HorizonVariableSpec("p", (2,)),
        )

    binding = replace(_active(), vectorized_variable_specs=duplicate_specs)
    with pytest.raises(ValueError, match="duplicate vectorized variables"):
        assemble_component_vectorized(_prepared(binding), _context())
