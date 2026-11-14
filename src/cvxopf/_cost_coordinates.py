"""Cost-valued solver coordinates with an unchanged physical reporting graph.

Component adapters declare the exact stage-rate expression and its linear or
absolute-value weights. The solve-local transformation uses ``y = delta*w*x``
where the weight is positive, and identity coordinates where it is zero.
In the default hourly mode it replaces only the declared cost subtree.
Opt-in component-first assembly rebuilds the solve-local objective from typed,
complete contributions, in qualified order. Generator, HVDC, terminal and
unrelated costs retain their original meaning and integration factors in both
modes; the original objective remains the public reporting authority.

Each solve snapshots the current weights (including load Parameters), maps the
complete physical start, and restores public variables by inverse scaling.
Public graph/Parameter identities and constraint IDs are retained. No global
CVXPY registry or solver method is patched and no experimental code is used.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, TYPE_CHECKING, Mapping, Sequence
import warnings

import cvxpy as cp
import numpy as np

if TYPE_CHECKING:
    from cvxopf.problem import OPFBuild
    from cvxopf._component_adapter import StepContribution, VectorizedComponentContribution


class CostAccuracyWarning(RuntimeWarning):
    """Advisory economic discrepancy; native solver status is not changed."""


class _CyclingAbsolute(cp.abs):
    """An ordinary abs carrying provenance through CVXPY's tree copies.

    Only the local DNLP reduction below handles this tagged atom, by calling
    the installed ordinary abs canonicalizer. Its mathematics is unchanged.
    """

    def __init__(self, expression: cp.Expression, binding_index: int) -> None:
        self.binding_index = binding_index
        super().__init__(expression)

    def get_data(self) -> list[int]:
        return [self.binding_index]


@dataclass(frozen=True)
class CostCoordinateLeaf:
    """One physical coordinate and its component-owned cost subtotal."""

    variable: cp.Variable
    weights: cp.Expression
    rate: cp.Expression
    kind: Literal["cycling", "shedding", "linear_cycling"]

    def __post_init__(self) -> None:
        if self.kind not in ("cycling", "shedding", "linear_cycling"):
            raise ValueError("unsupported cost-coordinate kind")
        if not self.weights.is_constant() or not self.rate.is_convex():
            raise ValueError("cost weights must be constant and rates DCP-convex")
        if self.kind != "cycling" and not self.rate.is_affine():
            raise ValueError("linear cost-coordinate rates must be affine")
        if {v.id for v in self.rate.variables()} != {self.variable.id}:
            raise ValueError("cost subtotal must own exactly its declared variable")


@dataclass(frozen=True)
class CostCoordinateTerm:
    """One complete stage rate, constructed from its ordered leaf subtotals.

    Identity coverage, rather than a numerical sample, prevents partial or
    overlapping declarations. One-leaf contributions keep their existing graph;
    multi-leaf contributions explicitly sum the same owned subtotal objects.
    """

    rate: cp.Expression
    leaves: tuple[CostCoordinateLeaf, ...]
    interval_axis: int | None = None

    def __post_init__(self) -> None:
        from cvxpy.atoms.affine.add_expr import AddExpression

        object.__setattr__(self, "leaves", tuple(self.leaves))
        if not self.leaves or len({leaf.variable.id for leaf in self.leaves}) != len(self.leaves):
            raise ValueError("cost-coordinate leaves require unique variable ownership")
        parts = (self.rate,) if len(self.leaves) == 1 else (
            tuple(self.rate.args) if isinstance(self.rate, AddExpression) else ()
        )
        if ([id(part) for part in parts] != [id(leaf.rate) for leaf in self.leaves]
                or any(leaf.rate.shape != self.rate.shape for leaf in self.leaves)):
            raise ValueError("cost coordinates require complete declared subtotal coverage")


@dataclass(frozen=True)
class CoordinateBinding:
    """One solve-local physical/solver pair and its exact declared rate."""

    term: CostCoordinateTerm
    coordinate: CostCoordinateLeaf
    leaf: cp.Variable
    scale: np.ndarray
    rate: cp.Expression
    absolute: cp.Expression | None
    physical_start: np.ndarray


@dataclass(frozen=True)
class ObjectiveCostContribution:
    """Complete component-owned objective contribution, retained at assembly.

    ``integrated_stage`` is already multiplied by the interval duration;
    ``terminal`` is a once-per-horizon cost. A declared coordinate term owns
    the entire ``stage_rate``, so unsupported partial substitutions fail rather
    than silently discarding an unrelated cost. Public expression names play
    no role in deciding which terms enter the objective.
    """

    component: str
    stage_rate: cp.Expression | None
    integrated_stage: cp.Expression | None
    terminal: cp.Expression | None
    coordinates: tuple[CostCoordinateTerm, ...] = ()

    def __post_init__(self) -> None:
        if (self.stage_rate is None) != (self.integrated_stage is None):
            raise ValueError("stage rate and integrated cost must be retained together")
        if self.coordinates and (len(self.coordinates) != 1 or self.coordinates[0].rate is not self.stage_rate):
            raise ValueError("component-first requires a complete declared stage-rate substitution")


def component_first_objective(build: OPFBuild, substitutions: Mapping[int, cp.Expression],
                              bindings: Sequence[CoordinateBinding]) -> cp.Minimize:
    """Integrate complete typed costs, flattening the qualified priced leaves.

    Ordinary contributions retain registry order, followed by cycling and then
    shedding cost coordinates, and finally terminal costs. The positive-weight
    leaves already contain duration times price; summing them must not apply
    either factor again. Mixed zero weights retain identity physical coordinates
    but never acquire a priced cycling epigraph.
    """
    contributions = build._objective_cost_contributions
    if build.formulation != "ac":
        raise ValueError("component-first objective assembly supports AC only")
    build.numerical_preparation.validate_formulation("ac")
    build.numerical_preparation.validate_assembly(build.temporal_assembly)
    if build.prob.objective.expr is not build._objective_cost_expression:
        raise ValueError("objective no longer matches its typed component assembly")
    if not contributions:
        raise ValueError("component-first objective requires typed component cost contributions")
    declared = [(id(term), id(leaf)) for contribution in contributions
                for term in contribution.coordinates for leaf in term.leaves]
    if declared != [(id(binding.term), id(binding.coordinate)) for binding in bindings]:
        raise ValueError("component-first coordinate contribution coverage differs")
    parts = [contribution.integrated_stage.tree_copy(substitutions)
             for contribution in contributions
             if contribution.integrated_stage is not None and not contribution.coordinates]
    for kinds in (("cycling", "linear_cycling"), ("shedding",)):
        for binding in bindings:
            coordinate = binding.coordinate
            if coordinate.kind not in kinds:
                continue
            if coordinate.kind == "cycling":
                if binding.absolute is not None:
                    parts.append(cp.sum(binding.absolute))
            else:
                active = np.broadcast_to(np.asarray(coordinate.weights.value) > 0, binding.leaf.shape)
                parts.append(cp.sum(binding.leaf) if active.all()
                             else cp.sum(cp.multiply(active.astype(float), binding.leaf)))
    parts.extend(contribution.terminal.tree_copy(substitutions)
                 for contribution in contributions if contribution.terminal is not None)
    if not parts:
        raise ValueError("component-first objective has no supported cost contributions")
    return cp.Minimize(sum(parts[1:], start=parts[0]))


def collect_cost_terms(
    contributions: Sequence[Mapping[str, StepContribution]] | Mapping[str, VectorizedComponentContribution],
) -> tuple[CostCoordinateTerm, ...]:
    """Flatten typed component contributions without classifying variable names."""
    if isinstance(contributions, Mapping):
        return tuple(term for contribution in contributions.values() for term in contribution.model.cost_coordinates)
    return tuple(term for group in contributions for contribution in group.values()
                 for term in contribution.cost_coordinates)


def _coordinate_binding(term: CostCoordinateTerm, coordinate: CostCoordinateLeaf,
                        index: int, delta: float) -> CoordinateBinding:
    """Snapshot and map one leaf; rate ownership remains with its term."""
    weights = np.broadcast_to(np.asarray(coordinate.weights.value, float), coordinate.variable.shape)
    if not np.isfinite(weights).all() or np.any(weights < 0):
        raise ValueError("cost-coordinate weights must be finite and nonnegative")
    if coordinate.kind == "linear_cycling" and np.any(weights <= 0):
        raise ValueError("linear throughput weights must be strictly positive")
    scale = np.where(weights > 0, delta * weights, 1.)
    if not np.isfinite(scale).all() or np.any(scale <= 0):
        raise ValueError("invalid cost-coordinate scale")
    leaf = cp.Variable(coordinate.variable.shape, name=f"cost_coordinate_{index}")
    leaf.save_value(np.asarray(coordinate.variable.value) * scale)
    active = weights > 0
    absolute = None
    if coordinate.kind == "cycling":
        # Only positive-weight entries have an epigraph. Reassemble by time
        # for vectorized rates, retaining zero-weight physical coordinates.
        value = _CyclingAbsolute(leaf, index)
        if active.all():
            absolute = value
            weighted = cp.multiply(weights / scale, absolute)
        else:
            from scipy import sparse
            indices = np.flatnonzero(active.ravel(order="F"))
            if indices.size:
                absolute = _CyclingAbsolute(cp.reshape(leaf, (leaf.size,), order="F")[indices], index)
                scatter = sparse.csc_matrix((np.ones(indices.size), (indices, np.arange(indices.size))),
                                            shape=(leaf.size, indices.size))
                value = cp.reshape(scatter @ absolute, leaf.shape, order="F")
                weighted = cp.multiply(weights / scale, value)
            else:
                weighted = 0. * leaf
    else:
        weighted = cp.multiply(coordinate.weights / scale, leaf)
    rate = cp.sum(weighted, axis=term.interval_axis)
    return CoordinateBinding(term, coordinate, leaf, scale.copy(), rate, absolute,
                             np.asarray(coordinate.variable.value).copy())


def transform_cost_coordinates(build: OPFBuild) -> tuple[OPFBuild, tuple[CoordinateBinding, ...]]:
    """Construct an ephemeral solve graph; leave the owner's graph unchanged."""
    terms = build._cost_coordinate_terms
    variables = [leaf.variable.id for term in terms for leaf in term.leaves]
    if (len(set(variables)) != len(variables)
            or len({id(term.rate) for term in terms}) != len(terms)):
        raise ValueError("overlapping cost-coordinate ownership")
    substitutions: dict[int, cp.Expression] = {}
    bindings = []
    for term in terms:
        rates = []
        for coordinate in term.leaves:
            binding = _coordinate_binding(term, coordinate, len(bindings), build._cost_coordinate_delta)
            substitutions[id(coordinate.variable)] = cp.multiply(1. / binding.scale, binding.leaf)
            bindings.append(binding)
            rates.append(binding.rate)
        substitutions[id(term.rate)] = sum(rates[1:], start=rates[0])
    component_first = build.numerical_preparation.objective_assembly == "component_first"
    if not bindings and not component_first:
        return build, ()
    objective = (component_first_objective(build, substitutions, bindings) if component_first
                 else build.prob.objective.tree_copy(substitutions))
    problem = cp.Problem(objective,
                         [constraint.tree_copy(substitutions) for constraint in build.prob.constraints])
    if {c.id for c in problem.constraints} != {c.id for c in build.prob.constraints}:
        raise RuntimeError("cost-coordinate transformation changed constraint identity")
    replaced = {binding.coordinate.variable.id for binding in bindings}
    if replaced & {variable.id for variable in problem.variables()}:
        raise RuntimeError("cost-coordinate substitution left a physical leaf in the solve graph")
    return replace(build, prob=problem), tuple(bindings)


def cost_canonicalization(bindings: tuple[CoordinateBinding, ...]) -> Any:
    """Record declared cycling auxiliaries by copied provenance, not shape.

    The installed DNLP reduction performs its ordinary canonicalization. This
    local subclass records only the output of our own tagged absolute-value atoms,
    avoiding accidental attribution of PWL or HVDC auxiliaries to cycling.
    """
    from cvxpy.reductions.dnlp2smooth.dnlp2smooth import Dnlp2Smooth

    class CostCanonicalization(Dnlp2Smooth):
        def __init__(self) -> None:
            super().__init__()
            self.cycling_auxiliaries = []

        def canonicalize_expr(self, expr: Any, args: Any, affine_above: bool) -> Any:
            if isinstance(expr, _CyclingAbsolute):
                result, constraints = self.smooth_canon_methods[cp.abs](expr, args)
                if not isinstance(result, cp.Variable):
                    raise RuntimeError("installed DNLP absolute-value representation is unsupported")
                self.cycling_auxiliaries.append((bindings[expr.binding_index], result))
                return result, constraints
            return super().canonicalize_expr(expr, args, affine_above)

    return CostCanonicalization()


def retain_coordinate_maps(build: OPFBuild, bindings: tuple[CoordinateBinding, ...]) -> None:
    """Retain start/scaling provenance even when the native solve failed."""
    evidence = build.preparation_evidence
    if evidence is not None and bindings:
        maps = tuple(dict(kind=binding.coordinate.kind, physical_variable_id=binding.coordinate.variable.id,
                          solver_variable_id=binding.leaf.id, scale=binding.scale,
                          physical_start=binding.physical_start)
                     for binding in bindings)
        build._preparation_evidence = replace(evidence, checks={**evidence.checks, "cost_coordinate_maps": maps})


def restore_cost_coordinates(build: OPFBuild, solver_build: OPFBuild,
                             bindings: tuple[CoordinateBinding, ...], reduction: Any) -> None:
    """Restore physical primals and publish advisory, non-cancelling cost checks."""
    for binding in bindings:
        value = binding.leaf.value
        if value is None or not np.isfinite(value).all():
            raise ValueError("unavailable cost-coordinate primal")
        binding.coordinate.variable.save_value(np.asarray(value) / binding.scale)
    # IPOPT publishes primal/status/objective, not constraint duals. Preserve
    # the stock result contract without asking it to invert another graph.
    build.prob._status = solver_build.prob.status
    build.prob._value = solver_build.prob.value
    from cvxpy.reductions.solution import Solution
    solution = solver_build.prob.solution
    build.prob._solution = Solution(solution.status, solution.opt_val,
                                   {variable.id: variable.value for variable in build.prob.variables()},
                                   solution.dual_vars, solution.attr)
    build.prob._solver_stats = solver_build.prob.solver_stats
    physical = float(build.prob.objective.value)
    native = float(build.preparation_evidence.native["obj_val"])
    cycling = sum(build._cost_coordinate_delta * float(np.sum(binding.coordinate.rate.value))
                  for binding in bindings if binding.coordinate.kind == "cycling")
    slack = []
    full = build.preparation_evidence.coordinates.expand(build.preparation_evidence.native["x"])
    layout = {item[0]: (item[2], item[3]) for item in build.preparation_evidence.start_layout}
    for binding, auxiliary in reduction.cycling_auxiliaries:
        start, stop = layout[auxiliary.id]
        t = full[start:stop].reshape(auxiliary.shape, order="F")
        actual = np.asarray(binding.absolute.args[0].value)
        slack.extend((t - np.abs(actual)).ravel())
    if sum(binding.absolute is not None for binding in bindings) != len(reduction.cycling_auxiliaries):
        raise RuntimeError("cycling auxiliary accounting is incomplete")
    excess = float(np.sum(slack))
    slack_l1 = float(np.sum(np.abs(slack)))
    limit = 1e-4 + 1e-6 * abs(cycling)
    total_limit = 1e-4 + 1e-6 * abs(physical)
    unexplained = native - physical - excess
    record = dict(native_objective=native, physical_objective=physical,
                  cycling_physical_cost=cycling, cycling_epigraph_excess=excess,
                  cycling_slack_l1=slack_l1, cycling_limit=limit,
                  cycling_warning=slack_l1 > limit,
                  unexplained_objective_difference=unexplained, objective_limit=total_limit,
                  accounting_warning=abs(unexplained) > total_limit)
    linear = [binding for binding in bindings if binding.coordinate.kind == "linear_cycling"]
    if linear:
        record.update(
            linear_throughput_physical_cost=sum(
                build._cost_coordinate_delta * float(np.sum(binding.coordinate.rate.value))
                for binding in linear
            ),
            linear_throughput_epigraph_excess=0.,
        )
    if not all(np.isfinite(value) for value in record.values()):
        raise ValueError("nonfinite cost-coordinate accounting")
    evidence = build.preparation_evidence
    build._preparation_evidence = replace(evidence, checks={**evidence.checks, "cost_accounting": record})


def emit_cost_warning(build: OPFBuild) -> None:
    """Emit after restoration/exception cleanup, so warning-as-error retains results."""
    record = build.preparation_evidence.checks.get("cost_accounting", {})
    if record.get("cycling_warning") or record.get("accounting_warning"):
        warnings.warn("AC economic accuracy warning; inspect preparation_evidence.checks['cost_accounting']. "
                      "The returned solution and native status are retained.", CostAccuracyWarning, stacklevel=3)
