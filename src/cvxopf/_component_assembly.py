"""Shared orchestration for typed component adapters.

This module owns the repeated mechanics of preparing component collections,
creating builder-owned variables, binding injection scales, invoking adapter
hooks, and publishing component state. Formulation modules retain ownership
of network variables, network equations, contribution ordering, objectives,
and ``OPFBuild`` construction.
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
    bind_injection_scale,
)


@dataclass(frozen=True)
class ComponentRequest:
    """One supplied component collection and its normalized external inputs."""

    adapter: ComponentAdapter[Any, Any]
    units: tuple[Any, ...]
    inputs: Any = None
    required_capability: FormulationCapability | None = None


@dataclass(frozen=True)
class PreparedComponents:
    """Ordered prepared component registry plus compatibility flat data."""

    formulation: Formulation
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
        if not request.units:
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
    return PreparedComponents(formulation, components, flat_data)


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
        variables = {
            spec.name: cp.Variable(
                spec.shape,
                name=f"{spec.name}{variable_suffix}",
                **spec.attributes,
            )
            for spec in binding.variable_specs(
                component.units, component.data, context
            )
        }
        injection = binding.injections(
            component.units, component.data, variables, context
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
        contributions[name] = StepContribution(
            variables=variables,
            injection=injection,
            operating_constraints=operating_constraints,
            network_constraints=network_constraints,
            cost=cost,
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
    if len(step_contributions) != context.horizon_steps:
        raise ValueError(
            "step contribution count must equal horizon_steps"
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
            costs.append(contribution.cost)
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
            terminal_costs.append(contribution.terminal_cost)
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


def publish_component_variables(
    step_contributions: Sequence[Mapping[str, StepContribution]],
    *,
    multistep: bool,
) -> dict[str, cp.Variable | list[cp.Variable]]:
    """Flatten component variables into the established ``OPFBuild`` schema."""
    if not step_contributions:
        return {}
    published: dict[str, cp.Variable | list[cp.Variable]] = {}
    for name, contribution in step_contributions[0].items():
        for variable_name, variable in contribution.variables.items():
            if variable_name in published:
                raise ValueError(
                    f"duplicate published component variable {variable_name!r}"
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
) -> dict[str, object]:
    """Flatten active component metadata into the established data schema."""
    published: dict[str, object] = {}
    for component in prepared.components.values():
        binding = component.adapter.formulations[prepared.formulation]
        if binding.capability is FormulationCapability.NULL:
            continue
        metadata = component.adapter.metadata(
            component.data, prepared.formulation
        )
        overlap = set(published).intersection(metadata)
        if overlap:
            raise ValueError(
                f"duplicate published component metadata: {sorted(overlap)}"
            )
        published.update(metadata)
    return published
