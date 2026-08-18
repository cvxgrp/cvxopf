"""Shared orchestration for typed component adapters.

This module owns the repeated mechanics of preparing component collections,
creating builder-owned variables, binding injection scales, invoking adapter
hooks, integrating stage-cost rates, and publishing component state.
Formulation modules retain ownership of network variables, network equations,
network-specific stage rates, contribution ordering, horizon-boundary
composition, and ``OPFBuild`` construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence, cast

import cvxpy as cp

from cvxopf._component_adapter import (
    ComponentAdapter,
    Formulation,
    FormulationCapability,
    HorizonContext,
    HorizonContribution,
    InjectionContribution,
    PreparationContext,
    PreparedComponent,
    StepContext,
    StepContribution,
    _validate_positive_real,
    bind_injection_scale,
)


@dataclass(frozen=True)
class ComponentRequest:
    """One supplied component collection and its normalized external inputs."""

    adapter: ComponentAdapter[Any, Any]
    units: tuple[Any, ...]
    inputs: Any = None
    required_capability: FormulationCapability | None = None
    participates_when_empty: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "units", tuple(self.units))


@dataclass(frozen=True)
class PreparedComponents:
    """Ordered prepared component registry plus compatibility flat data."""

    formulation: Formulation
    context: PreparationContext
    components: Mapping[str, PreparedComponent[Any, Any]]
    flat_data: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "components",
            MappingProxyType(dict(self.components)),
        )
        object.__setattr__(
            self,
            "flat_data",
            MappingProxyType(dict(self.flat_data)),
        )


def prepare_components(
    requests: Sequence[ComponentRequest],
    formulation: Formulation,
    context: PreparationContext,
) -> PreparedComponents:
    """Prepare supplied active components and retain explicit null entries."""
    components: dict[str, PreparedComponent[Any, Any]] = {}
    flat_data: dict[str, object] = {}
    for request in requests:
        if not request.units and not request.participates_when_empty:
            continue
        adapter = request.adapter
        if adapter.name in components:
            raise ValueError(f"duplicate component adapter {adapter.name!r}")
        binding = adapter.formulations[formulation]
        if (
            request.required_capability is not None
            and binding.capability is not request.required_capability
        ):
            raise RuntimeError(
                f"{formulation} requires the registered "
                f"{request.required_capability.value} {adapter.name.upper()} "
                "capability"
            )
        if binding.capability is FormulationCapability.UNSUPPORTED:
            raise ValueError(
                f"component {adapter.name!r} does not support "
                f"formulation {formulation!r}"
            )
        prepared_data: Mapping[str, object]
        if binding.capability is FormulationCapability.NULL:
            prepared_data = {}
        else:
            prepared_data = adapter.prepare(
                request.units, request.inputs, context
            )
            overlap = set(flat_data).intersection(prepared_data)
            if overlap:
                raise ValueError(
                    f"component {adapter.name!r} prepared duplicate data "
                    f"keys: {sorted(overlap)}"
                )
            flat_data.update(prepared_data)
        components[adapter.name] = PreparedComponent(
            adapter=adapter,
            units=request.units,
            data=prepared_data,
        )
    return PreparedComponents(formulation, context, components, flat_data)


def merge_prepared_component_data(
    prepared: PreparedComponents,
    formulation_data: Mapping[str, object],
) -> dict[str, object]:
    """Merge prepared component data into a formulation parser namespace."""
    overlap = set(formulation_data).intersection(prepared.flat_data)
    if overlap:
        raise ValueError(
            "component prepared data collides with formulation parser data: "
            f"{sorted(overlap)}"
        )
    merged = dict(formulation_data)
    merged.update(prepared.flat_data)
    return merged


def _validate_step_variable_schemas(
    step_contributions: Sequence[Mapping[str, StepContribution]],
    *,
    expected_components: Sequence[str] | None = None,
) -> None:
    """Require stable component, variable-name, and shape schemas by step."""
    if not step_contributions:
        raise ValueError("at least one step contribution is required")
    first = step_contributions[0]
    component_names = tuple(first)
    component_name_set = set(component_names)
    if (
        expected_components is not None
        and component_name_set != set(expected_components)
    ):
        raise ValueError(
            "step 0 component keys do not match the prepared registry: "
            f"expected {sorted(expected_components)}, "
            f"got {sorted(component_names)}"
        )
    variable_schemas = {
        component_name: {
            variable_name: variable.shape
            for variable_name, variable in contribution.variables.items()
        }
        for component_name, contribution in first.items()
    }
    for step, contributions in enumerate(step_contributions[1:], start=1):
        if set(contributions) != component_name_set:
            raise ValueError(
                f"inconsistent component keys at step {step}: "
                f"expected {sorted(component_names)}, "
                f"got {sorted(contributions)}"
            )
        for component_name in component_names:
            actual_schema = {
                variable_name: variable.shape
                for variable_name, variable
                in contributions[component_name].variables.items()
            }
            if actual_schema != variable_schemas[component_name]:
                raise ValueError(
                    f"component {component_name!r} has an inconsistent "
                    f"variable schema at step {step}: expected "
                    f"{variable_schemas[component_name]}, got {actual_schema}"
                )


def _validate_injection_contribution(
    component_name: str,
    contribution: InjectionContribution,
    *,
    formulation: Formulation,
    nb: int,
) -> None:
    """Enforce exact nodal-channel shapes and formulation channel support."""
    if formulation != "ac" and contribution.q_pu is not None:
        raise ValueError(
            f"component {component_name!r} returned a reactive injection "
            f"for formulation {formulation!r}; q_pu must be None"
        )
    expected_shape = (nb,)
    for channel_name, expression in (
        ("p_pu", contribution.p_pu),
        ("q_pu", contribution.q_pu),
    ):
        if expression is not None and expression.shape != expected_shape:
            raise ValueError(
                f"component {component_name!r} injection {channel_name} "
                f"must have shape {expected_shape}, got {expression.shape}"
            )
    parameter = contribution.inv_base_mva
    if (
        parameter is not None
        and contribution.p_pu is None
        and contribution.q_pu is None
    ):
        raise ValueError(
            f"component {component_name!r} returned inv_base_mva "
            "without an injection channel"
        )
    if parameter is None:
        return
    if not isinstance(parameter, cp.Parameter):
        raise ValueError(
            f"component {component_name!r} inv_base_mva must be "
            "a scalar cp.Parameter"
        )
    if parameter.shape != ():
        raise ValueError(
            f"component {component_name!r} inv_base_mva must be scalar, "
            f"got shape {parameter.shape}"
        )
    channels = tuple(
        expression
        for expression in (contribution.p_pu, contribution.q_pu)
        if expression is not None
    )
    if not any(
        parameter is used_parameter
        for expression in channels
        for used_parameter in expression.parameters()
    ):
        raise ValueError(
            f"component {component_name!r} inv_base_mva does not occur "
            "in any injection channel"
        )


def assemble_component_step(
    prepared: PreparedComponents,
    context: StepContext,
    *,
    variable_suffix: str = "",
) -> Mapping[str, StepContribution]:
    """Assemble all supplied component contributions for one network step."""
    if context.formulation != prepared.formulation:
        raise ValueError(
            "step formulation does not match prepared component formulation"
        )
    if context.base_mva != prepared.context.base_mva:
        raise ValueError(
            "step base_mva does not match component preparation"
        )
    if context.ext_to_int != prepared.context.ext_to_int:
        raise ValueError(
            "step ext_to_int does not match component preparation"
        )
    if context.step >= prepared.context.horizon_steps:
        raise ValueError(
            f"step index {context.step} is outside prepared horizon "
            f"[0, {prepared.context.horizon_steps})"
        )
    contributions: dict[str, StepContribution] = {}
    for name, component in prepared.components.items():
        binding = component.adapter.formulations[context.formulation]
        if binding.capability is FormulationCapability.NULL:
            contributions[name] = StepContribution(
                variables={},
                injection=InjectionContribution(None, None),
            )
            continue
        if binding.capability is not FormulationCapability.ACTIVE:
            raise ValueError(
                f"component {name!r} is not active for "
                f"formulation {context.formulation!r}"
            )
        assert binding.variable_specs is not None
        assert binding.injections is not None
        assert binding.operating_constraints is not None
        assert binding.horizon is not None
        specs = binding.variable_specs(
            component.units, component.data, context
        )
        variable_names = [spec.name for spec in specs]
        duplicate_names = sorted(
            {
                name
                for name in variable_names
                if variable_names.count(name) > 1
            }
        )
        if duplicate_names:
            raise ValueError(
                f"component {name!r} requested duplicate variables: "
                f"{duplicate_names}"
            )
        variables = {
            spec.name: cp.Variable(
                spec.shape,
                name=f"{spec.name}{variable_suffix}",
                **spec.attributes,
            )
            for spec in specs
        }
        injection = binding.injections(
            component.units, component.data, variables, context
        )
        _validate_injection_contribution(
            name,
            injection,
            formulation=context.formulation,
            nb=prepared.context.nb,
        )
        bind_injection_scale(injection, context.base_mva)
        operating_constraints = binding.operating_constraints(
            component.units, component.data, variables, context
        )
        network_constraints = (
            ()
            if binding.network_constraints is None
            else binding.network_constraints(
                component.units, component.data, variables, context
            )
        )
        cost = (
            None
            if binding.step_cost is None
            else binding.step_cost(
                component.units, component.data, variables, context
            )
        )
        expressions = (
            {}
            if binding.step_expressions is None
            else binding.step_expressions(
                component.units, component.data, variables, context
            )
        )
        contributions[name] = StepContribution(
            variables=variables,
            injection=injection,
            operating_constraints=operating_constraints,
            network_constraints=network_constraints,
            cost=cost,
            expressions=expressions,
        )
    return MappingProxyType(contributions)


def assemble_component_horizon(
    prepared: PreparedComponents,
    step_contributions: Sequence[Mapping[str, StepContribution]],
    context: HorizonContext,
) -> Mapping[str, HorizonContribution]:
    """Invoke every supplied component's horizon capability exactly once."""
    if context.formulation != prepared.formulation:
        raise ValueError(
            "horizon formulation does not match prepared component formulation"
        )
    if context.horizon_steps != prepared.context.horizon_steps:
        raise ValueError(
            "horizon_steps does not match component preparation"
        )
    if context.delta != prepared.context.delta:
        raise ValueError("horizon delta does not match component preparation")
    if len(step_contributions) != context.horizon_steps:
        raise ValueError(
            "step contribution count must equal horizon_steps"
        )
    _validate_step_variable_schemas(
        step_contributions,
        expected_components=tuple(prepared.components),
    )
    contributions: dict[str, HorizonContribution] = {}
    for name, component in prepared.components.items():
        binding = component.adapter.formulations[context.formulation]
        if binding.capability is FormulationCapability.NULL:
            contributions[name] = HorizonContribution()
            continue
        assert binding.horizon is not None
        variable_names = step_contributions[0][name].variables
        variable_history = {
            variable_name: [
                step[name].variables[variable_name]
                for step in step_contributions
            ]
            for variable_name in variable_names
        }
        contributions[name] = binding.horizon(
            component.units,
            component.data,
            variable_history,
            context,
        )
    return MappingProxyType(contributions)


def aggregate_step_contributions(
    contributions: Mapping[str, StepContribution],
) -> StepContribution:
    """Compose ordered component outputs without introducing network physics."""
    variables: dict[str, cp.Variable] = {}
    operating_constraints: list[cp.Constraint] = []
    network_constraints: list[cp.Constraint] = []
    p_injections: list[cp.Expression] = []
    q_injections: list[cp.Expression] = []
    costs: list[cp.Expression] = []
    expressions: dict[str, cp.Expression] = {}
    for name, contribution in contributions.items():
        duplicate_variables = set(variables).intersection(
            contribution.variables
        )
        if duplicate_variables:
            raise ValueError(
                f"component {name!r} published duplicate variables: "
                f"{sorted(duplicate_variables)}"
            )
        variables.update(contribution.variables)
        if contribution.injection.p_pu is not None:
            p_injections.append(contribution.injection.p_pu)
        if contribution.injection.q_pu is not None:
            q_injections.append(contribution.injection.q_pu)
        operating_constraints.extend(contribution.operating_constraints)
        network_constraints.extend(contribution.network_constraints)
        if contribution.cost is not None:
            if (
                not isinstance(contribution.cost, cp.Expression)
                or not contribution.cost.is_scalar()
            ):
                raise ValueError(
                    f"component {name!r} step cost must be a scalar "
                    "cp.Expression"
                )
            costs.append(contribution.cost)
        _validate_expression_names(
            name, contribution.expressions, horizon=False
        )
        duplicate_expressions = set(expressions).intersection(
            contribution.expressions
        )
        if duplicate_expressions:
            raise ValueError(
                f"component {name!r} published duplicate expressions: "
                f"{sorted(duplicate_expressions)}"
            )
        expressions.update(contribution.expressions)

    def ordered_sum(values: Sequence[cp.Expression]) -> cp.Expression | None:
        if not values:
            return None
        return cast(cp.Expression, sum(values[1:], start=values[0]))

    return StepContribution(
        variables=variables,
        injection=InjectionContribution(
            ordered_sum(p_injections),
            ordered_sum(q_injections),
        ),
        operating_constraints=tuple(operating_constraints),
        network_constraints=tuple(network_constraints),
        cost=ordered_sum(costs),
        expressions=expressions,
    )


def _validate_expression_names(
    component_name: str,
    expressions: Mapping[str, cp.Expression],
    *,
    horizon: bool,
) -> None:
    """Require nonempty string keys in the flattened expression namespace."""
    for expression_name in expressions:
        if not isinstance(expression_name, str) or not expression_name:
            scope = "horizon " if horizon else ""
            raise ValueError(
                f"component {component_name!r} published an empty or "
                f"non-string {scope}expression name"
            )


def aggregate_horizon_contributions(
    contributions: Mapping[str, HorizonContribution],
) -> HorizonContribution:
    """Compose ordered horizon constraints and terminal costs."""
    constraints: list[cp.Constraint] = []
    terminal_costs: list[cp.Expression] = []
    expressions: dict[str, cp.Expression] = {}
    for name, contribution in contributions.items():
        constraints.extend(contribution.constraints)
        if contribution.terminal_cost is not None:
            if (
                not isinstance(contribution.terminal_cost, cp.Expression)
                or not contribution.terminal_cost.is_scalar()
            ):
                raise ValueError(
                    f"component {name!r} terminal cost must be a scalar "
                    "cp.Expression"
                )
            terminal_costs.append(contribution.terminal_cost)
        _validate_expression_names(
            name, contribution.expressions, horizon=True
        )
        duplicate_expressions = set(expressions).intersection(
            contribution.expressions
        )
        if duplicate_expressions:
            raise ValueError(
                f"component {name!r} published duplicate horizon expressions: "
                f"{sorted(duplicate_expressions)}"
            )
        expressions.update(contribution.expressions)
    terminal_cost = (
        None
        if not terminal_costs
        else cast(
            cp.Expression,
            sum(terminal_costs[1:], start=terminal_costs[0]),
        )
    )
    return HorizonContribution(
        constraints=tuple(constraints),
        terminal_cost=terminal_cost,
        expressions=expressions,
    )


def integrate_stage_cost_rates(
    stage_cost_rates: Sequence[cp.Expression],
    delta: float,
) -> cp.Expression:
    """Integrate scalar stage-cost rates over equal-duration intervals."""
    _validate_positive_real("delta", delta)
    if not stage_cost_rates:
        raise ValueError("stage-cost integration requires at least one rate")
    for index, rate in enumerate(stage_cost_rates):
        if not isinstance(rate, cp.Expression) or not rate.is_scalar():
            raise ValueError(
                f"stage cost rate at index {index} must be a scalar "
                "cp.Expression"
            )
    summed_rate = cast(
        cp.Expression,
        sum(stage_cost_rates[1:], start=stage_cost_rates[0]),
    )
    return cp.multiply(delta, summed_rate)


def integrate_component_stage_costs(
    step_contributions: Sequence[Mapping[str, StepContribution]],
    delta: float,
) -> Mapping[str, cp.Expression]:
    """Return one integrated, named cost for each cost-bearing component."""
    _validate_step_variable_schemas(step_contributions)
    costs: dict[str, cp.Expression] = {}
    for component_name in step_contributions[0]:
        rates = [
            step[component_name].cost for step in step_contributions
        ]
        if all(rate is None for rate in rates):
            continue
        if any(rate is None for rate in rates):
            raise ValueError(
                f"component {component_name!r} has inconsistent step-cost "
                "availability across the horizon"
            )
        costs[f"{component_name}_cost"] = integrate_stage_cost_rates(
            cast(list[cp.Expression], rates),
            delta,
        )
    return MappingProxyType(costs)


def _validate_publication_step_count(
    step_count: int,
    *,
    multistep: bool,
) -> None:
    """Enforce the shared single- versus multistep publication cardinality."""
    if step_count == 0:
        raise ValueError(
            "publication requires at least one step contribution"
        )
    if not multistep and step_count != 1:
        raise ValueError(
            "single-step publication requires exactly one step "
            f"contribution, got {step_count}"
        )


def _validate_formulation_variable_schema(
    formulation_variables: Mapping[
        str, cp.Variable | list[cp.Variable]
    ],
    *,
    step_count: int,
    multistep: bool,
) -> None:
    """Require formulation-owned variables to match publication mode."""
    for name, value in formulation_variables.items():
        if not multistep:
            if not isinstance(value, cp.Variable):
                raise ValueError(
                    f"single-step formulation variable {name!r} must be "
                    "a cp.Variable"
                )
            continue
        if not isinstance(value, list):
            raise ValueError(
                f"multistep formulation variable {name!r} must be a list "
                "of cp.Variable objects"
            )
        if len(value) != step_count:
            raise ValueError(
                f"multistep formulation variable {name!r} must have "
                f"{step_count} entries, got {len(value)}"
            )
        if any(not isinstance(variable, cp.Variable) for variable in value):
            raise ValueError(
                f"multistep formulation variable {name!r} must contain "
                "only cp.Variable objects"
            )


def publish_component_variables(
    step_contributions: Sequence[Mapping[str, StepContribution]],
    formulation_variables: Mapping[
        str, cp.Variable | list[cp.Variable]
    ] | None = None,
    *,
    multistep: bool,
) -> dict[str, cp.Variable | list[cp.Variable]]:
    """Merge component variables into the formulation-owned build schema."""
    _validate_publication_step_count(
        len(step_contributions), multistep=multistep
    )
    formulation_variables = (
        {} if formulation_variables is None else formulation_variables
    )
    _validate_formulation_variable_schema(
        formulation_variables,
        step_count=len(step_contributions),
        multistep=multistep,
    )
    _validate_step_variable_schemas(step_contributions)
    published = dict(formulation_variables)
    for name, contribution in step_contributions[0].items():
        for variable_name, variable in contribution.variables.items():
            if variable_name in published:
                raise ValueError(
                    f"component {name!r} variable {variable_name!r} "
                    "collides with an already published variable"
                )
            published[variable_name] = (
                [step[name].variables[variable_name]
                 for step in step_contributions]
                if multistep
                else variable
            )
    return published


def publish_component_metadata(
    prepared: PreparedComponents,
    formulation_metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Merge active component metadata into the formulation-owned data."""
    published = (
        {} if formulation_metadata is None else dict(formulation_metadata)
    )
    for name, component in prepared.components.items():
        binding = component.adapter.formulations[prepared.formulation]
        if binding.capability is FormulationCapability.NULL:
            continue
        metadata = component.adapter.metadata(
            component.data, prepared.formulation
        )
        overlap = set(published).intersection(metadata)
        if overlap:
            raise ValueError(
                f"component {name!r} metadata collides with already "
                f"published data: {sorted(overlap)}"
            )
        published.update(metadata)
    return published


def publish_component_expressions(
    step_aggregates: Sequence[StepContribution],
    horizon_contribution: HorizonContribution,
    compatibility_expressions: Mapping[
        str, cp.Expression | list[cp.Expression]
    ],
    *,
    multistep: bool,
) -> dict[str, cp.Expression | list[cp.Expression]]:
    """Publish component expressions beside formulation compatibility fields.

    Per-step expression names must be identical across a multistep horizon.
    Horizon expressions are published once. No component or horizon expression
    may collide with another contribution or with a formulation-owned
    compatibility expression.
    """
    _validate_publication_step_count(
        len(step_aggregates), multistep=multistep
    )
    step_expressions = [
        aggregate.expressions for aggregate in step_aggregates
    ]
    expression_names = tuple(step_expressions[0])
    expected_names = set(expression_names)
    for step, expressions in enumerate(step_expressions[1:], start=1):
        if set(expressions) != expected_names:
            raise ValueError(
                "inconsistent component expression keys at "
                f"step {step}: expected {sorted(expected_names)}, "
                f"got {sorted(expressions)}"
            )

    published = dict(compatibility_expressions)
    compatibility_collisions = set(published).intersection(expression_names)
    if compatibility_collisions:
        raise ValueError(
            "component expressions collide with formulation compatibility "
            f"expressions: {sorted(compatibility_collisions)}"
        )
    for name in expression_names:
        published[name] = (
            [expressions[name] for expressions in step_expressions]
            if multistep
            else step_expressions[0][name]
        )

    horizon_collisions = set(published).intersection(
        horizon_contribution.expressions
    )
    if horizon_collisions:
        raise ValueError(
            "horizon expressions collide with published expressions: "
            f"{sorted(horizon_collisions)}"
        )
    published.update(horizon_contribution.expressions)
    return published
