"""Tests for shared typed component orchestration."""

import ast
from dataclasses import dataclass
from dataclasses import replace
import inspect
from typing import Mapping, Sequence
from unittest.mock import Mock

import cvxpy as cp
import numpy as np
import pytest

from cvxopf import DispatchableGenerator, HVDCLink, StorageUnitIdeal
from cvxopf import ac_problem, dc_problem, singlenode_dc_problem
from cvxopf._component_adapter import (
    ACNetworkState,
    ComponentAdapter,
    DCNetworkState,
    Formulation,
    FormulationAdapter,
    FormulationCapability,
    HorizonContext,
    HorizonContribution,
    InjectionContribution,
    PreparationContext,
    StepContribution,
    StepContext,
    VariableSpec,
)
from cvxopf._component_adapters import (
    GENERATOR_ADAPTER,
    HVDC_ADAPTER,
    STORAGE_ADAPTER,
    component_requests,
)
from cvxopf._component_assembly import (
    ComponentRequest,
    aggregate_horizon_contributions,
    aggregate_step_contributions,
    assemble_component_horizon,
    assemble_component_step,
    merge_prepared_component_data,
    prepare_components,
    publish_component_expressions,
    publish_component_metadata,
    publish_component_variables,
)


def _preparation(horizon_steps=1):
    return PreparationContext(
        base_mva=100.0,
        nb=2,
        ext_to_int={1: 0, 2: 1},
        ext_bus_ids=frozenset({1, 2}),
        horizon_steps=horizon_steps,
        delta=0.5,
        is_multistep=horizon_steps > 1,
    )


@dataclass(frozen=True)
class _ToyUnit:
    """Test-only memoryless injection used to prove adapter extensibility."""

    bus: int
    p_max_mw: float


def _toy_prepare(
    units: Sequence[_ToyUnit],
    inputs: None,
    context: PreparationContext,
) -> Mapping[str, object]:
    incidence = np.zeros((context.nb, len(units)))
    for column, unit in enumerate(units):
        incidence[context.ext_to_int[unit.bus], column] = 1.0
    return {
        "ntoy": len(units),
        "Ctoy": incidence,
        "toy_p_max_mw": np.array(
            [unit.p_max_mw for unit in units], dtype=float
        ),
    }


def _toy_metadata(
    prepared: Mapping[str, object],
    formulation: Formulation,
) -> Mapping[str, object]:
    return {"ntoy": prepared["ntoy"], "toy_formulation": formulation}


def _toy_variable_specs(
    units: Sequence[_ToyUnit],
    prepared: Mapping[str, object],
    context: StepContext,
) -> tuple[VariableSpec, ...]:
    return (VariableSpec("p_toy", (len(units),)),)


def _toy_injections(
    units: Sequence[_ToyUnit],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> InjectionContribution:
    scale = cp.Parameter(nonneg=True, name=f"toy_inv_base_{context.step}")
    incidence = prepared["Ctoy"]
    assert isinstance(incidence, np.ndarray)
    p_pu = scale * (incidence @ variables["p_toy"])
    q_pu = (
        scale * (incidence @ (0.25 * variables["p_toy"]))
        if context.formulation == "ac"
        else None
    )
    return InjectionContribution(p_pu, q_pu, scale)


def _toy_operating_constraints(
    units: Sequence[_ToyUnit],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> tuple[cp.Constraint, ...]:
    p_max = prepared["toy_p_max_mw"]
    assert isinstance(p_max, np.ndarray)
    return (variables["p_toy"] >= 0, variables["p_toy"] <= p_max)


def _toy_network_constraints(
    units: Sequence[_ToyUnit],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> tuple[cp.Constraint, ...]:
    return (cp.sum(variables["p_toy"]) <= 100.0,)


def _toy_step_cost(
    units: Sequence[_ToyUnit],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> cp.Expression:
    return 2.0 * cp.sum(variables["p_toy"])


def _toy_step_expressions(
    units: Sequence[_ToyUnit],
    prepared: Mapping[str, object],
    variables: Mapping[str, cp.Variable],
    context: StepContext,
) -> Mapping[str, cp.Expression]:
    return {"toy_total_mw": cp.sum(variables["p_toy"])}


def _toy_horizon(
    units: Sequence[_ToyUnit],
    prepared: Mapping[str, object],
    variable_history: Mapping[str, Sequence[cp.Variable]],
    context: HorizonContext,
) -> HorizonContribution:
    return HorizonContribution(
        expressions={
            "toy_horizon_marker": cp.sum(variable_history["p_toy"][-1])
        }
    )


def _toy_formulation() -> FormulationAdapter[_ToyUnit]:
    return FormulationAdapter(
        capability=FormulationCapability.ACTIVE,
        variable_specs=_toy_variable_specs,
        injections=_toy_injections,
        operating_constraints=_toy_operating_constraints,
        network_constraints=_toy_network_constraints,
        step_cost=_toy_step_cost,
        step_expressions=_toy_step_expressions,
        horizon=_toy_horizon,
    )


TOY_ADAPTER = ComponentAdapter[_ToyUnit, None](
    name="toy",
    prepare=_toy_prepare,
    metadata=_toy_metadata,
    formulations={
        "ac": _toy_formulation(),
        "lossy_dc": _toy_formulation(),
        "singlenode_dc": _toy_formulation(),
    },
)


_VECTOR_SCALE = cp.Parameter(2)
_UNUSED_SCALE = cp.Parameter(nonneg=True)


@pytest.mark.parametrize(
    ("formulation", "expects_q"),
    [
        ("ac", True),
        ("lossy_dc", False),
        ("singlenode_dc", False),
    ],
)
def test_repository_component_extension_composes_through_shared_assembly(
    formulation, expects_q
):
    """One centralized request exercises every generic adapter contribution."""
    unit = _ToyUnit(bus=2, p_max_mw=20.0)
    prepared = prepare_components(
        (ComponentRequest(TOY_ADAPTER, (unit,)),),
        formulation,
        _preparation(),
    )
    network_state = (
        ACNetworkState(cp.Variable(2), (), False)
        if formulation == "ac"
        else DCNetworkState()
    )
    step = assemble_component_step(
        prepared,
        StepContext(formulation, 0, 100.0, {1: 0, 2: 1}, network_state),
    )
    contribution = step["toy"]
    aggregate = aggregate_step_contributions(step)

    assert tuple(contribution.variables) == ("p_toy",)
    assert contribution.injection.inv_base_mva is not None
    assert contribution.injection.inv_base_mva.value == pytest.approx(0.01)
    assert (contribution.injection.q_pu is not None) is expects_q
    assert len(aggregate.operating_constraints) == 2
    assert len(aggregate.network_constraints) == 1
    assert aggregate.cost is contribution.cost
    assert tuple(aggregate.expressions) == ("toy_total_mw",)
    with pytest.raises(TypeError):
        contribution.expressions["other"] = cp.Constant(0.0)

    published_variables = publish_component_variables(
        (step,), multistep=False
    )
    assert published_variables["p_toy"] is contribution.variables["p_toy"]
    assert publish_component_metadata(prepared) == {
        "ntoy": 1,
        "toy_formulation": formulation,
    }

    horizon = assemble_component_horizon(
        prepared,
        (step,),
        HorizonContext(formulation, 1, 0.5),
    )
    toy_horizon = horizon["toy"]
    assert toy_horizon.constraints == ()
    assert toy_horizon.terminal_cost is None
    assert tuple(toy_horizon.expressions) == ("toy_horizon_marker",)
    horizon_aggregate = aggregate_horizon_contributions(horizon)
    published_expressions = publish_component_expressions(
        (aggregate,),
        horizon_aggregate,
        {"p_net": cp.Constant(np.zeros(2))},
        multistep=False,
    )
    assert isinstance(published_expressions["p_net"], cp.Expression)
    assert isinstance(
        published_expressions["toy_total_mw"], cp.Expression
    )
    assert isinstance(
        published_expressions["toy_horizon_marker"], cp.Expression
    )


def test_repository_component_extension_publishes_multistep_expressions():
    unit = _ToyUnit(bus=2, p_max_mw=20.0)
    prepared = prepare_components(
        (ComponentRequest(TOY_ADAPTER, (unit,)),),
        "lossy_dc",
        _preparation(horizon_steps=2),
    )
    steps = tuple(
        assemble_component_step(
            prepared,
            StepContext(
                "lossy_dc",
                step,
                100.0,
                {1: 0, 2: 1},
                DCNetworkState(),
            ),
            variable_suffix=f"_{step}",
        )
        for step in range(2)
    )
    horizon = assemble_component_horizon(
        prepared,
        steps,
        HorizonContext("lossy_dc", 2, 0.5),
    )
    horizon_aggregate = aggregate_horizon_contributions(horizon)

    published = publish_component_expressions(
        tuple(aggregate_step_contributions(step) for step in steps),
        horizon_aggregate,
        {"p_net": [cp.Constant(np.zeros(2)) for _ in range(2)]},
        multistep=True,
    )

    assert isinstance(published["p_net"], list)
    assert isinstance(published["toy_total_mw"], list)
    assert len(published["toy_total_mw"]) == 2
    assert all(
        isinstance(expression, cp.Expression)
        for expression in published["toy_total_mw"]
    )
    assert isinstance(published["toy_horizon_marker"], cp.Expression)


def test_repository_component_extension_can_select_explicit_null_capability():
    formulations = dict(TOY_ADAPTER.formulations)
    formulations["singlenode_dc"] = FormulationAdapter(
        capability=FormulationCapability.NULL
    )
    adapter = replace(TOY_ADAPTER, formulations=formulations)
    prepared = prepare_components(
        (
            ComponentRequest(
                adapter,
                (_ToyUnit(bus=2, p_max_mw=20.0),),
                required_capability=FormulationCapability.NULL,
            ),
        ),
        "singlenode_dc",
        _preparation(),
    )
    step = assemble_component_step(
        prepared,
        StepContext(
            "singlenode_dc",
            0,
            100.0,
            {1: 0, 2: 1},
            DCNetworkState(),
        ),
    )

    assert step["toy"].variables == {}
    assert step["toy"].injection == InjectionContribution(None, None)
    assert publish_component_metadata(prepared) == {}


def test_component_rejects_duplicate_variable_specs_before_construction():
    def duplicate_specs(units, prepared, context):
        return (
            VariableSpec("p_toy", (1,)),
            VariableSpec("p_toy", (1,)),
        )

    formulations = dict(TOY_ADAPTER.formulations)
    formulations["lossy_dc"] = replace(
        formulations["lossy_dc"],
        variable_specs=duplicate_specs,
    )
    adapter = replace(TOY_ADAPTER, formulations=formulations)
    prepared = prepare_components(
        (ComponentRequest(adapter, (_ToyUnit(2, 20.0),)),),
        "lossy_dc",
        _preparation(),
    )

    with pytest.raises(
        ValueError,
        match=r"component 'toy' requested duplicate variables: \['p_toy'\]",
    ):
        assemble_component_step(
            prepared,
            StepContext(
                "lossy_dc", 0, 100.0, {1: 0, 2: 1}, DCNetworkState()
            ),
        )


@pytest.mark.parametrize(
    ("formulation", "injection", "message"),
    [
        (
            "lossy_dc",
            InjectionContribution(cp.Constant(1.0), None),
            r"injection p_pu must have shape \(2,\), got \(\)",
        ),
        (
            "lossy_dc",
            InjectionContribution(cp.Constant(np.zeros(3)), None),
            r"injection p_pu must have shape \(2,\), got \(3,\)",
        ),
        (
            "ac",
            InjectionContribution(
                cp.Constant(np.zeros(2)),
                cp.Constant(np.zeros((2, 1))),
            ),
            r"injection q_pu must have shape \(2,\), got \(2, 1\)",
        ),
        (
            "lossy_dc",
            InjectionContribution(
                cp.Constant(np.zeros(2)),
                cp.Constant(np.zeros(2)),
            ),
            r"returned a reactive injection.*q_pu must be None",
        ),
        (
            "lossy_dc",
            InjectionContribution(
                None,
                None,
                cp.Parameter(nonneg=True),
            ),
            r"returned inv_base_mva without an injection channel",
        ),
        (
            "lossy_dc",
            InjectionContribution(
                cp.Constant(np.zeros(2)),
                None,
                cp.Constant(1.0),
            ),
            r"inv_base_mva must be a scalar cp.Parameter",
        ),
        (
            "lossy_dc",
            InjectionContribution(
                cp.multiply(_VECTOR_SCALE, np.ones(2)),
                None,
                _VECTOR_SCALE,
            ),
            r"inv_base_mva must be scalar, got shape \(2,\)",
        ),
        (
            "lossy_dc",
            InjectionContribution(
                cp.Constant(np.zeros(2)),
                None,
                _UNUSED_SCALE,
            ),
            r"inv_base_mva does not occur in any injection channel",
        ),
    ],
)
def test_component_injection_contract_rejects_malformed_channels(
    formulation, injection, message
):
    def malformed_injection(units, prepared, variables, context):
        return injection

    formulations = dict(TOY_ADAPTER.formulations)
    formulations[formulation] = replace(
        formulations[formulation],
        injections=malformed_injection,
    )
    adapter = replace(TOY_ADAPTER, formulations=formulations)
    prepared = prepare_components(
        (ComponentRequest(adapter, (_ToyUnit(2, 20.0),)),),
        formulation,
        _preparation(),
    )
    network_state = (
        ACNetworkState(cp.Variable(2), (), False)
        if formulation == "ac"
        else DCNetworkState()
    )

    with pytest.raises(ValueError, match=message):
        assemble_component_step(
            prepared,
            StepContext(
                formulation,
                0,
                100.0,
                {1: 0, 2: 1},
                network_state,
            ),
        )


def test_component_publication_rejects_formulation_namespace_collisions():
    prepared = prepare_components(
        (ComponentRequest(TOY_ADAPTER, (_ToyUnit(2, 20.0),)),),
        "lossy_dc",
        _preparation(),
    )
    step = assemble_component_step(
        prepared,
        StepContext(
            "lossy_dc", 0, 100.0, {1: 0, 2: 1}, DCNetworkState()
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"component 'toy' variable 'p_toy' collides with an already "
            r"published variable"
        ),
    ):
        publish_component_variables(
            (step,),
            {"p_toy": cp.Variable(1)},
            multistep=False,
        )

    with pytest.raises(
        ValueError,
        match=(
            r"component 'toy' metadata collides with already published data: "
            r"\['ntoy'\]"
        ),
    ):
        publish_component_metadata(prepared, {"ntoy": 0})


def test_prepared_data_merge_rejects_formulation_namespace_collisions():
    prepared = prepare_components(
        (ComponentRequest(TOY_ADAPTER, (_ToyUnit(2, 20.0),)),),
        "lossy_dc",
        _preparation(),
    )
    formulation_data = {"nb": 2}

    merged = merge_prepared_component_data(prepared, formulation_data)
    assert merged["nb"] == 2
    assert merged["ntoy"] == 1
    assert formulation_data == {"nb": 2}

    with pytest.raises(
        ValueError,
        match=(
            r"component prepared data collides with formulation parser data: "
            r"\['ntoy'\]"
        ),
    ):
        merge_prepared_component_data(prepared, {"ntoy": 0})


@pytest.mark.parametrize(
    "second_variables",
    [
        {"other": cp.Variable(1)},
        {"p_toy": cp.Variable(2)},
    ],
)
def test_multistep_variable_schema_must_be_stable(second_variables):
    prepared = prepare_components(
        (ComponentRequest(TOY_ADAPTER, (_ToyUnit(2, 20.0),)),),
        "lossy_dc",
        _preparation(horizon_steps=2),
    )
    contexts = [
        StepContext(
            "lossy_dc", step, 100.0, {1: 0, 2: 1}, DCNetworkState()
        )
        for step in range(2)
    ]
    steps = [
        assemble_component_step(
            prepared, context, variable_suffix=f"_{context.step}"
        )
        for context in contexts
    ]
    steps[1] = {
        "toy": replace(steps[1]["toy"], variables=second_variables)
    }

    with pytest.raises(
        ValueError,
        match=r"component 'toy' has an inconsistent variable schema at step 1",
    ):
        assemble_component_horizon(
            prepared,
            steps,
            HorizonContext("lossy_dc", 2, 0.5),
        )
    with pytest.raises(
        ValueError,
        match=r"component 'toy' has an inconsistent variable schema at step 1",
    ):
        publish_component_variables(steps, multistep=True)


def test_step_expression_hook_is_invoked_once_per_active_component_step():
    expression_hook = Mock(wraps=_toy_step_expressions)
    formulations = dict(TOY_ADAPTER.formulations)
    formulations["lossy_dc"] = replace(
        formulations["lossy_dc"],
        step_expressions=expression_hook,
    )
    adapter = replace(TOY_ADAPTER, formulations=formulations)
    prepared = prepare_components(
        (ComponentRequest(adapter, (_ToyUnit(2, 20.0),)),),
        "lossy_dc",
        _preparation(),
    )

    step = assemble_component_step(
        prepared,
        StepContext(
            "lossy_dc", 0, 100.0, {1: 0, 2: 1}, DCNetworkState()
        ),
    )

    expression_hook.assert_called_once()
    assert tuple(step["toy"].expressions) == ("toy_total_mw",)


def test_step_expression_aggregation_rejects_duplicate_flat_names():
    first = StepContribution(
        variables={},
        injection=InjectionContribution(None, None),
        expressions={"shared_metric": cp.Constant(1.0)},
    )
    second = StepContribution(
        variables={},
        injection=InjectionContribution(None, None),
        expressions={"shared_metric": cp.Constant(2.0)},
    )

    with pytest.raises(
        ValueError,
        match=(
            r"component 'second' published duplicate expressions: "
            r"\['shared_metric'\]"
        ),
    ):
        aggregate_step_contributions({"first": first, "second": second})


def test_step_expression_aggregation_rejects_empty_name():
    contribution = StepContribution(
        variables={},
        injection=InjectionContribution(None, None),
        expressions={"": cp.Constant(1.0)},
    )

    with pytest.raises(
        ValueError,
        match=(
            "component 'toy' published an empty or non-string expression name"
        ),
    ):
        aggregate_step_contributions({"toy": contribution})


def test_step_cost_aggregation_rejects_vector_contribution():
    contribution = StepContribution(
        variables={},
        injection=InjectionContribution(None, None),
        cost=cp.Constant([1.0, 2.0]),
    )
    with pytest.raises(
        ValueError,
        match="component 'toy' step cost must be a scalar cp.Expression",
    ):
        aggregate_step_contributions({"toy": contribution})


def test_horizon_expression_aggregation_rejects_duplicate_flat_names():
    with pytest.raises(
        ValueError,
        match=(
            r"component 'second' published duplicate horizon expressions: "
            r"\['shared_metric'\]"
        ),
    ):
        aggregate_horizon_contributions(
            {
                "first": HorizonContribution(
                    expressions={"shared_metric": cp.Constant(1.0)}
                ),
                "second": HorizonContribution(
                    expressions={"shared_metric": cp.Constant(2.0)}
                ),
            }
        )


def test_horizon_expression_aggregation_rejects_empty_name():
    contribution = HorizonContribution(
        expressions={"": cp.Constant(1.0)}
    )

    with pytest.raises(
        ValueError,
        match=(
            "component 'toy' published an empty or non-string horizon "
            "expression name"
        ),
    ):
        aggregate_horizon_contributions({"toy": contribution})


def test_terminal_cost_aggregation_rejects_vector_contribution():
    contribution = HorizonContribution(
        terminal_cost=cp.Constant([1.0, 2.0])
    )
    with pytest.raises(
        ValueError,
        match="component 'toy' terminal cost must be a scalar cp.Expression",
    ):
        aggregate_horizon_contributions({"toy": contribution})


def test_expression_publication_rejects_inconsistent_multistep_keys():
    first = StepContribution(
        variables={},
        injection=InjectionContribution(None, None),
        expressions={"first": cp.Constant(1.0)},
    )
    second = StepContribution(
        variables={},
        injection=InjectionContribution(None, None),
        expressions={"second": cp.Constant(2.0)},
    )

    with pytest.raises(
        ValueError, match="inconsistent component expression keys at step 1"
    ):
        publish_component_expressions(
            (first, second),
            HorizonContribution(),
            {},
            multistep=True,
        )


@pytest.mark.parametrize(
    ("step_count", "multistep", "message"),
    [
        (0, False, "publication requires at least one step contribution"),
        (0, True, "publication requires at least one step contribution"),
        (1, False, None),
        (1, True, None),
        (
            2,
            False,
            "single-step publication requires exactly one step "
            "contribution, got 2",
        ),
        (2, True, None),
    ],
)
def test_publication_helpers_share_step_cardinality_contract(
    step_count, multistep, message
):
    step = {
        "toy": StepContribution(
            variables={"p_toy": cp.Variable(1)},
            injection=InjectionContribution(None, None),
            expressions={"toy_metric": cp.Constant(1.0)},
        )
    }
    aggregate = aggregate_step_contributions(step)
    steps = tuple(step for _ in range(step_count))
    aggregates = tuple(aggregate for _ in range(step_count))

    if message is not None:
        with pytest.raises(ValueError, match=message):
            publish_component_variables(steps, multistep=multistep)
        with pytest.raises(ValueError, match=message):
            publish_component_expressions(
                aggregates,
                HorizonContribution(),
                {},
                multistep=multistep,
            )
        return

    variables = publish_component_variables(steps, multistep=multistep)
    expressions = publish_component_expressions(
        aggregates,
        HorizonContribution(),
        {},
        multistep=multistep,
    )
    if multistep:
        assert isinstance(variables["p_toy"], list)
        assert len(variables["p_toy"]) == step_count
        assert isinstance(expressions["toy_metric"], list)
        assert len(expressions["toy_metric"]) == step_count
    else:
        assert isinstance(variables["p_toy"], cp.Variable)
        assert isinstance(expressions["toy_metric"], cp.Expression)


def test_multistep_t1_publication_remains_a_list():
    variable = cp.Variable(1)
    step = {
        "toy": StepContribution(
            variables={"p_toy": variable},
            injection=InjectionContribution(None, None),
            expressions={"toy_metric": cp.sum(variable)},
        )
    }
    aggregate = aggregate_step_contributions(step)

    variables = publish_component_variables((step,), multistep=True)
    expressions = publish_component_expressions(
        (aggregate,),
        HorizonContribution(),
        {},
        multistep=True,
    )

    assert variables["p_toy"] == [variable]
    assert isinstance(expressions["toy_metric"], list)
    assert len(expressions["toy_metric"]) == 1


@pytest.mark.parametrize(
    ("multistep", "step_count", "formulation_variables", "message"),
    [
        (
            False,
            1,
            {"network": [cp.Variable(1)]},
            "single-step formulation variable 'network' must be a cp.Variable",
        ),
        (
            True,
            1,
            {"network": cp.Variable(1)},
            "multistep formulation variable 'network' must be a list",
        ),
        (
            True,
            2,
            {"network": [cp.Variable(1)]},
            "multistep formulation variable 'network' must have 2 entries",
        ),
        (
            True,
            1,
            {"network": [cp.Constant(1.0)]},
            "multistep formulation variable 'network' must contain only",
        ),
    ],
)
def test_formulation_variable_schema_must_match_publication_mode(
    multistep, step_count, formulation_variables, message
):
    step = {
        "toy": StepContribution(
            variables={"p_toy": cp.Variable(1)},
            injection=InjectionContribution(None, None),
        )
    }
    with pytest.raises(ValueError, match=message):
        publish_component_variables(
            tuple(step for _ in range(step_count)),
            formulation_variables,
            multistep=multistep,
        )


def test_formulation_variable_schema_preserves_both_t1_modes():
    step = {
        "toy": StepContribution(
            variables={"p_toy": cp.Variable(1)},
            injection=InjectionContribution(None, None),
        )
    }
    single_network = cp.Variable(1)
    multi_network = cp.Variable(1)

    single = publish_component_variables(
        (step,),
        {"network": single_network},
        multistep=False,
    )
    multi = publish_component_variables(
        (step,),
        {"network": [multi_network]},
        multistep=True,
    )

    assert single["network"] is single_network
    assert multi["network"] == [multi_network]


@pytest.mark.parametrize(
    ("horizon_expressions", "compatibility_expressions", "message"),
    [
        (
            {"shared": cp.Constant(2.0)},
            {},
            "horizon expressions collide with published expressions",
        ),
        (
            {},
            {"shared": cp.Constant(3.0)},
            "component expressions collide with formulation compatibility",
        ),
    ],
)
def test_expression_publication_rejects_namespace_collisions(
    horizon_expressions, compatibility_expressions, message
):
    step = StepContribution(
        variables={},
        injection=InjectionContribution(None, None),
        expressions={"shared": cp.Constant(1.0)},
    )

    with pytest.raises(ValueError, match=message):
        publish_component_expressions(
            (step,),
            HorizonContribution(expressions=horizon_expressions),
            compatibility_expressions,
            multistep=False,
        )


def test_shared_assembly_composes_active_components_across_horizon():
    generator = DispatchableGenerator(
        bus=1,
        p_max_mw=100.0,
        q_min_mvar=-50.0,
        q_max_mvar=50.0,
    )
    storage = StorageUnitIdeal(
        bus=2,
        apparent_power_rating=20.0,
        capacity=40.0,
        initial_soc=20.0,
    )
    prepared = prepare_components(
        (
            ComponentRequest(GENERATOR_ADAPTER, (generator,)),
            ComponentRequest(STORAGE_ADAPTER, (storage,)),
        ),
        "lossy_dc",
        _preparation(horizon_steps=2),
    )
    context_0 = StepContext(
        "lossy_dc", 0, 100.0, {1: 0, 2: 1}, DCNetworkState()
    )
    context_1 = StepContext(
        "lossy_dc", 1, 100.0, {1: 0, 2: 1}, DCNetworkState()
    )
    step_0 = assemble_component_step(
        prepared, context_0, variable_suffix="_0"
    )
    step_1 = assemble_component_step(
        prepared, context_1, variable_suffix="_1"
    )

    assert tuple(step_0) == ("generator", "storage")
    assert step_0["generator"].variables["Pg"].name() == "Pg_0"
    assert step_1["storage"].variables["soc"].name() == "soc_1"
    scale = step_0["storage"].injection.inv_base_mva
    assert scale is not None
    assert scale.value == pytest.approx(0.01)
    assert step_0["generator"].injection.inv_base_mva is None
    step_aggregate = aggregate_step_contributions(step_0)
    assert step_aggregate.injection.p_pu is not None
    assert step_aggregate.injection.q_pu is None
    assert len(step_aggregate.operating_constraints) == (
        len(step_0["generator"].operating_constraints)
        + len(step_0["storage"].operating_constraints)
    )
    assert step_aggregate.cost is not None

    horizon = assemble_component_horizon(
        prepared,
        (step_0, step_1),
        HorizonContext("lossy_dc", 2, 0.5),
    )
    assert len(horizon["storage"].constraints) == 2
    assert horizon["generator"].constraints == ()
    horizon_aggregate = aggregate_horizon_contributions(horizon)
    assert len(horizon_aggregate.constraints) == 2

    variables = publish_component_variables(
        (step_0, step_1), multistep=True
    )
    assert set(variables) == {"Pg", "b", "soc"}
    assert len(variables["Pg"]) == 2
    metadata = publish_component_metadata(prepared)
    assert {"ng", "ns", "storage_delta"} <= set(metadata)


def test_common_registry_has_one_order_for_every_formulation():
    generator = DispatchableGenerator(bus=1, p_max_mw=100.0)
    storage = StorageUnitIdeal(2, 20.0, 40.0, 20.0)
    link = HVDCLink(1, 2, -10.0, 10.0)
    for formulation in ("ac", "lossy_dc", "singlenode_dc"):
        requests = component_requests(
            formulation,
            generators=(generator,),
            storage_units=(storage,),
            hvdc_links=(link,),
        )
        assert tuple(request.adapter.name for request in requests) == (
            "generator",
            "storage",
            "nondispatchable",
            "hvdc",
        )
        expected_hvdc = (
            FormulationCapability.NULL
            if formulation == "singlenode_dc"
            else FormulationCapability.ACTIVE
        )
        assert requests[-1].required_capability is expected_hvdc


def test_shared_assembly_distinguishes_absent_and_explicit_null():
    link = HVDCLink(1, 2, -10.0, 10.0)
    context = _preparation()
    absent = prepare_components(
        (
            ComponentRequest(
                HVDC_ADAPTER,
                (),
                required_capability=FormulationCapability.NULL,
            ),
        ),
        "singlenode_dc",
        context,
    )
    assert absent.components == {}

    explicit_null = prepare_components(
        (
            ComponentRequest(
                HVDC_ADAPTER,
                (link,),
                required_capability=FormulationCapability.NULL,
            ),
        ),
        "singlenode_dc",
        context,
    )
    assert tuple(explicit_null.components) == ("hvdc",)
    step = assemble_component_step(
        explicit_null,
        StepContext(
            "singlenode_dc",
            0,
            100.0,
            {1: 0, 2: 1},
            DCNetworkState(),
        ),
    )
    assert step["hvdc"].variables == {}
    assert step["hvdc"].injection.p_pu is None
    assert publish_component_variables((step,), multistep=False) == {}
    assert publish_component_metadata(explicit_null) == {}


def test_required_capability_is_checked_only_for_supplied_components():
    formulations = dict(HVDC_ADAPTER.formulations)
    formulations["singlenode_dc"] = formulations["lossy_dc"]
    corrupted = replace(HVDC_ADAPTER, formulations=formulations)
    request = ComponentRequest(
        corrupted,
        (),
        required_capability=FormulationCapability.NULL,
    )
    prepare_components((request,), "singlenode_dc", _preparation())

    with pytest.raises(RuntimeError, match="registered null HVDC"):
        prepare_components(
            (replace(request, units=(HVDCLink(1, 2, -10.0, 10.0),)),),
            "singlenode_dc",
            _preparation(),
        )


def test_component_request_units_are_normalized_to_a_tuple():
    unit = _ToyUnit(2, 20.0)
    units = [unit]

    request = ComponentRequest(TOY_ADAPTER, units)
    units.clear()

    assert request.units == (unit,)
    assert isinstance(request.units, tuple)


def test_shared_assembly_rejects_formulation_and_horizon_mismatches():
    generator = DispatchableGenerator(
        bus=1, p_max_mw=100.0, cost_coeffs=(0.0, 1.0, 0.0)
    )
    prepared = prepare_components(
        (ComponentRequest(GENERATOR_ADAPTER, (generator,)),),
        "lossy_dc",
        _preparation(),
    )
    with pytest.raises(ValueError, match="step formulation"):
        assemble_component_step(
            prepared,
            StepContext(
                "singlenode_dc",
                0,
                100.0,
                {1: 0, 2: 1},
                DCNetworkState(),
            ),
        )
    step = assemble_component_step(
        prepared,
        StepContext(
            "lossy_dc",
            0,
            100.0,
            {1: 0, 2: 1},
            DCNetworkState(),
        ),
    )
    with pytest.raises(ValueError, match="horizon_steps"):
        assemble_component_horizon(
            prepared,
            (step,),
            HorizonContext("lossy_dc", 2, 1.0),
        )

    assert isinstance(step["generator"].cost, cp.Expression)
    np.testing.assert_equal(
        step["generator"].variables["Pg"].shape, (1,)
    )


@pytest.mark.parametrize(
    ("step", "base_mva", "ext_to_int", "message"),
    [
        (0, 50.0, {1: 0, 2: 1}, "step base_mva does not match"),
        (0, 100.0, {1: 1, 2: 0}, "step ext_to_int does not match"),
        (1, 100.0, {1: 0, 2: 1}, "step index 1 is outside"),
    ],
)
def test_step_context_must_match_component_preparation(
    step, base_mva, ext_to_int, message
):
    prepared = prepare_components(
        (ComponentRequest(TOY_ADAPTER, (_ToyUnit(2, 20.0),)),),
        "lossy_dc",
        _preparation(),
    )
    with pytest.raises(ValueError, match=message):
        assemble_component_step(
            prepared,
            StepContext(
                "lossy_dc",
                step,
                base_mva,
                ext_to_int,
                DCNetworkState(),
            ),
        )


@pytest.mark.parametrize(
    ("horizon_steps", "delta", "message"),
    [
        (1, 1.0, "horizon delta does not match"),
        (2, 0.5, "horizon_steps does not match"),
    ],
)
def test_horizon_context_must_match_component_preparation(
    horizon_steps, delta, message
):
    prepared = prepare_components(
        (ComponentRequest(TOY_ADAPTER, (_ToyUnit(2, 20.0),)),),
        "lossy_dc",
        _preparation(),
    )
    step = assemble_component_step(
        prepared,
        StepContext(
            "lossy_dc", 0, 100.0, {1: 0, 2: 1}, DCNetworkState()
        ),
    )
    with pytest.raises(ValueError, match=message):
        assemble_component_horizon(
            prepared,
            (step,),
            HorizonContext("lossy_dc", horizon_steps, delta),
        )


def test_formulation_builders_do_not_call_adapter_hooks_directly():
    hook_names = {
        "prepare",
        "variable_specs",
        "injections",
        "operating_constraints",
        "network_constraints",
        "step_cost",
        "step_expressions",
        "horizon",
        "metadata",
    }
    for module in (ac_problem, dc_problem, singlenode_dc_problem):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        direct_hook_calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in hook_names
        }
        assert direct_hook_calls == set()
        assert "ComponentRequest(" not in source
        assert "_ADAPTER" not in source
