"""Focused component-box qualification for the M14b vectorized contract.

The paired harness changes only the candidate box representation.  Both arms
retain identical component data, equations, costs, CLARABEL configuration, and
SCIPY canonicalization.  These compact fixtures qualify representation; they
do not replace the formulation-level M14c equivalence runs.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import resource
import sys
import time
from types import MappingProxyType
from typing import Any, Literal, Mapping

import cvxpy as cp
import numpy as np


Formulation = Literal["lossy_dc", "singlenode_dc"]
GateName = Literal["storage", "nondispatchable", "load_shedding", "hvdc"]
BoundEncoding = Literal["explicit", "leaf"]
FORMULATIONS: tuple[Formulation, ...] = ("lossy_dc", "singlenode_dc")
GATE_PAIRS: tuple[tuple[Formulation, GateName], ...] = (
    ("lossy_dc", "storage"),
    ("singlenode_dc", "storage"),
    ("lossy_dc", "nondispatchable"),
    ("singlenode_dc", "nondispatchable"),
    ("lossy_dc", "load_shedding"),
    ("singlenode_dc", "load_shedding"),
    ("lossy_dc", "hvdc"),
)
HORIZON = 4
DELTA_HOURS = 1.0
AUDIT_TOLERANCE = 2e-6
PAIR_ABSOLUTE_TOLERANCE = 2e-5
ACCEPTED_STATUSES = {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}
CLARABEL_SOLVE_OPTIONS: Mapping[str, float | int] = MappingProxyType(
    {
        "max_iter": 500,
        "tol_gap_abs": 1e-9,
        "tol_gap_rel": 1e-9,
        "tol_feas": 1e-9,
    }
)


def _peak_rss_bytes() -> int:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(raw if sys.platform == "darwin" else raw * 1024)


@dataclass(frozen=True)
class CandidateBox:
    """One exact time-last candidate box and its public family identity."""

    variable_name: str
    family: str
    lower: np.ndarray
    upper: np.ndarray

    def __post_init__(self) -> None:
        lower = np.array(self.lower, dtype=float, copy=True)
        upper = np.array(self.upper, dtype=float, copy=True)
        if not self.variable_name or not self.family:
            raise ValueError("candidate box names must be nonempty")
        if lower.shape != upper.shape or lower.size == 0:
            raise ValueError("candidate box faces must be nonempty and aligned")
        if not np.isfinite(lower).all() or not np.isfinite(upper).all():
            raise ValueError("candidate box faces must be finite")
        if np.any(lower > upper):
            raise ValueError("candidate box lower face exceeds upper face")
        lower.flags.writeable = False
        upper.flags.writeable = False
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)


@dataclass(frozen=True)
class ComponentProbeBuild:
    """One build-ready component-box arm consumed by the shared harness."""

    formulation: Formulation
    gate: GateName
    encoding: BoundEncoding
    problem: cp.Problem
    variables: Mapping[str, cp.Variable]
    expressions: Mapping[str, cp.Expression]
    expression_views: Mapping[str, Literal["interval", "horizon"]]
    inputs: Mapping[str, object]
    candidate_boxes: tuple[CandidateBox, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "variables", MappingProxyType(dict(self.variables)))
        object.__setattr__(
            self, "expressions", MappingProxyType(dict(self.expressions))
        )
        views = dict(self.expression_views)
        if set(views) != set(self.expressions):
            raise ValueError("expression views must exactly match expressions")
        object.__setattr__(self, "expression_views", MappingProxyType(views))
        object.__setattr__(self, "inputs", MappingProxyType(dict(self.inputs)))

    def solve(self) -> None:
        """Use the exact frozen convex solver and canonicalization backend."""
        self.problem.solve(
            solver=cp.CLARABEL,
            canon_backend=cp.SCIPY_CANON_BACKEND,
            **CLARABEL_SOLVE_OPTIONS,
        )


def _candidate_variable(
    box: CandidateBox,
    encoding: BoundEncoding,
) -> tuple[cp.Variable, list[cp.Constraint]]:
    """Construct one candidate using only the requested representation."""
    if encoding == "leaf":
        return cp.Variable(
            box.lower.shape,
            name=box.variable_name,
            bounds=[box.lower, box.upper],
        ), []
    variable = cp.Variable(box.lower.shape, name=box.variable_name)
    return variable, [variable >= box.lower, variable <= box.upper]


def _incidence(device_buses: np.ndarray, *, buses: int = 2) -> np.ndarray:
    matrix = np.zeros((buses, device_buses.size))
    matrix[device_buses, np.arange(device_buses.size)] = 1.0
    return matrix


def _network_model(
    formulation: Formulation,
    injection: cp.Expression,
    demand: np.ndarray,
    *,
    generation_upper: np.ndarray | None = None,
) -> tuple[
    dict[str, cp.Variable],
    list[cp.Constraint],
    cp.Expression,
    cp.Expression,
]:
    """Attach the same component injection to a compact DC network context."""
    if injection.shape != (2, HORIZON):
        raise ValueError("network injection must have shape (2, HORIZON)")
    if demand.shape != (2, HORIZON):
        raise ValueError("network demand must have shape (2, HORIZON)")
    total_upper = (
        np.full(HORIZON, 100.0)
        if generation_upper is None
        else np.asarray(generation_upper, dtype=float)
    )
    if total_upper.shape != (HORIZON,):
        raise ValueError("generation upper profile must have shape (HORIZON,)")
    constraints: list[cp.Constraint] = []
    variables: dict[str, cp.Variable] = {}
    if formulation == "singlenode_dc":
        generation = cp.Variable((1, HORIZON), name="Pg")
        constraints.extend((generation >= 0.0, generation <= total_upper[None, :]))
        p_net = (
            cp.sum(generation, axis=0)
            + cp.sum(injection, axis=0)
            - np.sum(demand, axis=0)
        )
        constraints.append(p_net == 0.0)
        variables["Pg"] = generation
        cost = 0.02 * cp.sum_squares(generation)
        return variables, constraints, cost, p_net

    generation = cp.Variable((2, HORIZON), name="Pg")
    flow = cp.Variable((1, HORIZON), name="p_flows")
    per_bus_upper = np.broadcast_to(total_upper[None, :] / 2.0, (2, HORIZON))
    constraints.extend(
        (
            generation >= 0.0,
            generation <= per_bus_upper,
            flow >= -50.0,
            flow <= 50.0,
            generation[0, :] + injection[0, :] - demand[0, :] - flow[0, :] == 0,
            generation[1, :] + injection[1, :] - demand[1, :] + flow[0, :] == 0,
        )
    )
    p_net = generation + injection - demand
    variables.update(Pg=generation, p_flows=flow)
    cost = 0.02 * cp.sum_squares(generation) + 0.001 * cp.sum_squares(flow)
    return variables, constraints, cost, p_net


def _storage_build(
    formulation: Formulation,
    encoding: BoundEncoding,
) -> ComponentProbeBuild:
    device_ids = np.array(["equality", "shortfall", "soft"], dtype=object)
    power_rating = np.array([4.0, 3.0, 5.0])
    capacity = np.array([10.0, 8.0, 12.0])
    initial = np.array([5.0, 4.0, 6.0])
    b_box = CandidateBox(
        "b",
        "storage_real_power",
        np.broadcast_to(-power_rating[:, None], (3, HORIZON)),
        np.broadcast_to(power_rating[:, None], (3, HORIZON)),
    )
    soc_box = CandidateBox(
        "soc",
        "storage_soc",
        np.zeros((3, HORIZON + 1)),
        np.broadcast_to(capacity[:, None], (3, HORIZON + 1)),
    )
    b, b_constraints = _candidate_variable(b_box, encoding)
    soc, soc_constraints = _candidate_variable(soc_box, encoding)
    reference = np.array(
        [[2.0, -2.0, 1.0, -1.0], [1.0, 1.0, -1.0, -1.0], [2.0, 1.0, -1.0, 0.0]]
    )
    constraints = [*b_constraints, *soc_constraints]
    constraints.extend(
        (
            soc[:, 0] == initial,
            soc[:, 1:] == soc[:, :-1] - DELTA_HOURS * b,
            soc[0, -1] == 5.0,
            soc[1, -1] >= 3.0,
        )
    )
    aging_cost = 0.02 * DELTA_HOURS * cp.sum(cp.abs(b))
    terminal_cost = 2.0 * cp.square(soc[2, -1] - 5.0)
    injection = _incidence(np.array([0, 1, 1])) @ b
    demand = np.array([[8.0, 9.0, 8.0, 10.0], [6.0, 5.0, 7.0, 6.0]], dtype=float)
    network_variables, network_constraints, network_cost, p_net = _network_model(
        formulation, injection, demand
    )
    constraints.extend(network_constraints)
    objective = (
        network_cost + cp.sum_squares(b - reference) + aging_cost + terminal_cost
    )
    return ComponentProbeBuild(
        formulation,
        "storage",
        encoding,
        cp.Problem(cp.Minimize(objective), constraints),
        {"b": b, "soc": soc, **network_variables},
        {
            "storage_cost": aging_cost,
            "storage_terminal_cost": terminal_cost,
            "storage_injection": injection,
            "storage_terminal_deviation": soc[:, -1] - np.array([5.0, 3.0, 5.0]),
            "p_net": p_net,
        },
        {
            "storage_cost": "horizon",
            "storage_terminal_cost": "horizon",
            "storage_injection": "interval",
            "storage_terminal_deviation": "horizon",
            "p_net": "interval",
        },
        {
            "device_ids": device_ids,
            "power_rating": power_rating,
            "capacity": capacity,
            "initial_soc": initial,
            "terminal_target": np.array([5.0, 3.0, 5.0]),
            "reference": reference,
            "demand": demand,
        },
        (b_box, soc_box),
    )


def _nondispatchable_build(
    formulation: Formulation,
    encoding: BoundEncoding,
) -> ComponentProbeBuild:
    device_ids = np.array(["solar-a", "wind-b", "solar-c"], dtype=object)
    buses = np.array([0, 1, 1])
    rating = np.array([4.0, 3.0, 5.0])
    availability = np.array(
        [[0.0, 2.0, 6.0, 1.0], [5.0, 1.0, 3.0, 6.0], [2.0, 4.0, 0.0, 5.0]]
    )
    effective_upper = np.minimum(availability, rating[:, None])
    box = CandidateBox(
        "p_nd",
        "nondispatchable_real_power",
        np.zeros_like(effective_upper),
        effective_upper,
    )
    if encoding == "explicit":
        power = cp.Variable(box.lower.shape, name=box.variable_name)
        constraints = [
            power >= 0.0,
            power <= availability,
            power <= rating[:, None],
        ]
    else:
        power, box_constraints = _candidate_variable(box, encoding)
        constraints = list(box_constraints)
    injection = _incidence(buses) @ power
    demand = np.array([[12.0, 13.0, 11.0, 14.0], [10.0, 9.0, 12.0, 10.0]], dtype=float)
    network_variables, network_constraints, network_cost, p_net = _network_model(
        formulation, injection, demand
    )
    constraints.extend(network_constraints)
    curtailment = availability - power
    probe_preference = 3.0 * DELTA_HOURS * cp.sum(curtailment) + 1e-3 * cp.sum_squares(
        power
    )
    objective = network_cost + probe_preference
    return ComponentProbeBuild(
        formulation,
        "nondispatchable",
        encoding,
        cp.Problem(cp.Minimize(objective), constraints),
        {"p_nd": power, **network_variables},
        {
            "curtailment": curtailment,
            "nondispatchable_preference_probe_only": probe_preference,
            "nondispatchable_injection": injection,
            "p_net": p_net,
        },
        {
            "curtailment": "interval",
            "nondispatchable_preference_probe_only": "horizon",
            "nondispatchable_injection": "interval",
            "p_net": "interval",
        },
        {
            "device_ids": device_ids,
            "device_buses": buses,
            "availability": availability,
            "rating": rating,
            "effective_upper": effective_upper,
            "demand": demand,
        },
        (box,),
    )


def _load_shedding_build(
    formulation: Formulation,
    encoding: BoundEncoding,
) -> ComponentProbeBuild:
    device_ids = np.array(["industrial", "commercial"], dtype=object)
    buses = np.array([0, 1])
    load = np.array([[8.0, 10.0, 12.0, 9.0], [5.0, 7.0, 6.0, 8.0]])
    reactive_load = np.array([[2.4, 3.0, 3.6, 2.7], [1.0, 1.4, 1.2, 1.6]], dtype=float)
    eligibility = np.array([[0.0, 1.0, 1.0, 1.0], [1.0, 1.0, 0.0, 1.0]])
    maximum_fraction = np.array([0.5, 0.25])
    upper = maximum_fraction[:, None] * eligibility
    box = CandidateBox(
        "load_shed_fraction",
        "load_shed_fraction",
        np.zeros_like(upper),
        upper,
    )
    fraction, box_constraints = _candidate_variable(box, encoding)
    constraints = list(box_constraints)
    shed = cp.multiply(load, fraction)
    served = load - shed
    shed_total = cp.sum(shed, axis=0)
    ens_by_load = DELTA_HOURS * cp.sum(shed, axis=1)
    ens = cp.sum(ens_by_load)
    injection = -(_incidence(buses) @ served)
    demand = np.zeros((2, HORIZON))
    total_load = np.sum(load, axis=0)
    generation_upper = total_load - np.array([0.5, 1.0, 1.0, 1.0])
    network_variables, network_constraints, network_cost, p_net = _network_model(
        formulation,
        injection,
        demand,
        generation_upper=generation_upper,
    )
    constraints.extend(network_constraints)
    costs = np.array([100.0, 180.0])
    shedding_cost = DELTA_HOURS * cp.sum(cp.multiply(costs[:, None], shed))
    objective = network_cost + shedding_cost + 1e-4 * cp.sum_squares(fraction)
    return ComponentProbeBuild(
        formulation,
        "load_shedding",
        encoding,
        cp.Problem(cp.Minimize(objective), constraints),
        {"load_shed_fraction": fraction, **network_variables},
        {
            "p_load": cp.Constant(load),
            "q_load": cp.Constant(reactive_load),
            "p_load_shed": shed,
            "p_load_shed_total": shed_total,
            "p_load_served": served,
            "energy_not_served_by_load": ens_by_load,
            "energy_not_served": ens,
            "load_shedding_cost": shedding_cost,
            "load_injection": injection,
            "p_net": p_net,
        },
        {
            "p_load": "interval",
            "q_load": "interval",
            "p_load_shed": "interval",
            "p_load_shed_total": "interval",
            "p_load_served": "interval",
            "energy_not_served_by_load": "horizon",
            "energy_not_served": "horizon",
            "load_shedding_cost": "horizon",
            "load_injection": "interval",
            "p_net": "interval",
        },
        {
            "device_ids": device_ids,
            "device_buses": buses,
            "p_load": load,
            "q_load": reactive_load,
            "eligibility": eligibility,
            "maximum_fraction": maximum_fraction,
            "cost_per_mwh": costs,
            "generation_upper": generation_upper,
            "demand": demand,
        },
        (box,),
    )


def _hvdc_build(encoding: BoundEncoding) -> ComponentProbeBuild:
    device_ids = np.array(
        ["positive", "negative", "straddling", "degenerate", "varying"],
        dtype=object,
    )
    lower = np.array(
        [
            [1.0, 1.0, 1.5, 1.5],
            [-5.0, -4.0, -4.0, -3.0],
            [-3.0] * 4,
            [2.0] * 4,
            [0.0, 1.0, 2.0, 1.0],
        ]
    )
    upper = np.array(
        [
            [4.0, 4.5, 4.5, 5.0],
            [-1.0, -1.0, -0.5, -0.5],
            [3.0] * 4,
            [2.0] * 4,
            [2.0, 3.0, 4.0, 2.0],
        ]
    )
    loss_fraction = np.array([0.05, 0.08, 0.1, 0.02, 0.03])
    box = CandidateBox("p_hvdc_in", "hvdc_input_power", lower, upper)
    p_in, box_constraints = _candidate_variable(box, encoding)
    p_out = cp.Variable((5, HORIZON), name="p_hvdc_out")
    constraints = list(box_constraints)
    constraints.extend(
        (
            p_out[0, :] == -p_in[0, :] / (1.0 - loss_fraction[0]),
            p_out[1, :] == -(1.0 - loss_fraction[1]) * p_in[1, :],
            p_out[2, :] == -p_in[2, :],
            p_out[3, :] == -p_in[3, :] / (1.0 - loss_fraction[3]),
            p_out[4, :] == -p_in[4, :] / (1.0 - loss_fraction[4]),
        )
    )
    from_incidence = _incidence(np.zeros(5, dtype=int))
    to_incidence = _incidence(np.ones(5, dtype=int))
    injection = from_incidence @ p_in + to_incidence @ p_out
    demand = np.array([[14.0, 14.0, 15.0, 15.0], [11.0, 12.0, 11.0, 12.0]], dtype=float)
    network_variables, network_constraints, network_cost, p_net = _network_model(
        "lossy_dc", injection, demand
    )
    constraints.extend(network_constraints)
    desired = np.where(np.indices(lower.shape).sum(axis=0) % 2 == 0, lower, upper)
    cost = 0.01 * cp.sum_squares(p_in) + 0.02 * cp.sum(cp.abs(p_in))
    objective = network_cost + cp.sum_squares(p_in - desired) + cost
    return ComponentProbeBuild(
        "lossy_dc",
        "hvdc",
        encoding,
        cp.Problem(cp.Minimize(objective), constraints),
        {"p_hvdc_in": p_in, "p_hvdc_out": p_out, **network_variables},
        {
            "hvdc_loss": -(p_in + p_out),
            "hvdc_cost": cost,
            "hvdc_injection": injection,
            "p_net": p_net,
        },
        {
            "hvdc_loss": "interval",
            "hvdc_cost": "horizon",
            "hvdc_injection": "interval",
            "p_net": "interval",
        },
        {
            "device_ids": device_ids,
            "loss_fraction": loss_fraction,
            "desired": desired,
            "demand": demand,
            "direction": np.array(
                ["positive", "negative", "straddling", "positive", "positive"],
                dtype=object,
            ),
        },
        (box,),
    )


def build_probe(
    formulation: Formulation,
    gate: GateName,
    encoding: BoundEncoding,
) -> ComponentProbeBuild:
    """Build one arm from the frozen formulation/component registry."""
    if (formulation, gate) not in GATE_PAIRS:
        raise ValueError(f"gate {gate!r} does not apply to {formulation!r}")
    if encoding not in {"explicit", "leaf"}:
        raise ValueError("unsupported bound encoding")
    if gate == "storage":
        return _storage_build(formulation, encoding)
    if gate == "nondispatchable":
        return _nondispatchable_build(formulation, encoding)
    if gate == "load_shedding":
        return _load_shedding_build(formulation, encoding)
    return _hvdc_build(encoding)


def _source_structure(build: ComponentProbeBuild) -> dict[str, Any]:
    metrics = build.problem.size_metrics
    constraints = build.problem.constraints
    return {
        "problem_is_dcp": build.problem.is_dcp(),
        "objective_is_dcp": build.problem.objective.is_dcp(),
        "all_constraints_dcp": all(item.is_dcp() for item in constraints),
        "variable_objects": len(build.problem.variables()),
        "constraint_objects": len(constraints),
        "equality_objects": sum(
            isinstance(item, cp.constraints.Equality) for item in constraints
        ),
        "explicit_inequality_objects": sum(
            isinstance(item, cp.constraints.Inequality) for item in constraints
        ),
        "scalar_variables": int(metrics.num_scalar_variables),
        "scalar_equalities": int(metrics.num_scalar_eq_constr),
        "explicit_scalar_inequalities": int(metrics.num_scalar_leq_constr),
        "variable_shapes": {
            name: list(variable.shape) for name, variable in build.variables.items()
        },
    }


def _canonical_structure(problem: cp.Problem) -> dict[str, Any]:
    data, chain, _inverse = problem.get_problem_data(
        cp.CLARABEL,
        canon_backend=cp.SCIPY_CANON_BACKEND,
    )
    dimensions = data["dims"]
    matrix = data["A"]
    quadratic = data.get("P")
    return {
        "backend": "SCIPY",
        "canonical_variables": int(data["c"].shape[0]),
        "equality_rows": int(dimensions.zero),
        "nonnegative_rows": int(dimensions.nonneg),
        "soc_dimensions": [int(value) for value in dimensions.soc],
        "coefficient_rows": int(matrix.shape[0]),
        "coefficient_columns": int(matrix.shape[1]),
        "coefficient_nonzeros": int(matrix.nnz),
        "quadratic_nonzeros": 0 if quadratic is None else int(quadratic.nnz),
        "reductions": [type(item).__name__ for item in chain.reductions],
    }


def _values(build: ComponentProbeBuild) -> dict[str, np.ndarray]:
    return {
        name: np.asarray(variable.value, dtype=float)
        for name, variable in build.variables.items()
        if variable.value is not None
    }


def _expression_values(build: ComponentProbeBuild) -> dict[str, np.ndarray]:
    return {
        name: np.asarray(expression.value, dtype=float)
        for name, expression in build.expressions.items()
        if expression.value is not None
    }


def _fixture_sha256(build: ComponentProbeBuild) -> str:
    """Bind all exogenous arrays and candidate faces independent of encoding."""
    digest = hashlib.sha256()
    digest.update(f"{build.formulation}/{build.gate}".encode())
    for name, raw_value in sorted(build.inputs.items()):
        value = np.asarray(raw_value)
        digest.update(name.encode())
        digest.update(str(value.shape).encode())
        digest.update(value.dtype.str.encode())
        if value.dtype.kind in {"O", "U", "S"}:
            digest.update(json.dumps(value.tolist(), separators=(",", ":")).encode())
        else:
            digest.update(np.ascontiguousarray(value).tobytes())
    for box in build.candidate_boxes:
        digest.update(box.family.encode())
        digest.update(box.variable_name.encode())
        digest.update(np.ascontiguousarray(box.lower).tobytes())
        digest.update(np.ascontiguousarray(box.upper).tobytes())
    return digest.hexdigest()


def _box_residuals(
    build: ComponentProbeBuild,
    values: Mapping[str, np.ndarray],
) -> dict[str, float]:
    residuals: dict[str, float] = {}
    for box in build.candidate_boxes:
        value = values[box.variable_name]
        residuals[f"{box.family}_box_abs"] = float(
            max(0.0, np.max(box.lower - value), np.max(value - box.upper))
        )
    return residuals


def _network_residuals(
    build: ComponentProbeBuild,
    values: Mapping[str, np.ndarray],
    injection: np.ndarray,
    published_p_net: np.ndarray,
) -> dict[str, float]:
    demand = np.asarray(build.inputs["demand"], dtype=float)
    generation = values["Pg"]
    if build.formulation == "singlenode_dc":
        expected_p_net = (
            np.sum(generation, axis=0)
            + np.sum(injection, axis=0)
            - np.sum(demand, axis=0)
        )
        balance = expected_p_net
    else:
        flow = values["p_flows"][0, :]
        expected_p_net = generation + injection - demand
        balance = np.vstack(
            (
                expected_p_net[0, :] - flow,
                expected_p_net[1, :] + flow,
            )
        )
    return {
        "active_balance_abs": float(np.max(np.abs(balance))),
        "p_net_reconstruction_abs": float(
            np.max(np.abs(expected_p_net - published_p_net))
        ),
    }


def _network_cost_value(
    build: ComponentProbeBuild,
    values: Mapping[str, np.ndarray],
) -> float:
    cost = 0.02 * np.sum(values["Pg"] ** 2)
    if build.formulation == "lossy_dc":
        cost += 0.001 * np.sum(values["p_flows"] ** 2)
    return float(cost)


def _objective_residual(build: ComponentProbeBuild, reconstructed: float) -> float:
    objective = build.problem.value
    if objective is None or not np.isfinite(float(objective)):
        return float("inf")
    return abs(float(objective) - reconstructed)


def _audit(
    build: ComponentProbeBuild,
    values: Mapping[str, np.ndarray],
    expressions: Mapping[str, np.ndarray],
) -> dict[str, float]:
    residuals = _box_residuals(build, values)
    if build.gate == "storage":
        b = values["b"]
        soc = values["soc"]
        initial = np.asarray(build.inputs["initial_soc"], dtype=float)
        targets = np.asarray(build.inputs["terminal_target"], dtype=float)
        residuals.update(
            storage_initial_abs=float(np.max(np.abs(soc[:, 0] - initial))),
            storage_recurrence_abs=float(
                np.max(np.abs(soc[:, 1:] - soc[:, :-1] + DELTA_HOURS * b))
            ),
            equality_terminal_abs=float(abs(soc[0, -1] - targets[0])),
            shortfall_terminal_abs=float(max(0.0, targets[1] - soc[1, -1])),
            storage_cost_abs=float(
                abs(expressions["storage_cost"] - 0.02 * np.sum(np.abs(b)))
            ),
            storage_terminal_cost_abs=float(
                abs(
                    expressions["storage_terminal_cost"]
                    - 2.0 * (soc[2, -1] - targets[2]) ** 2
                )
            ),
        )
        reconstructed_objective = (
            _network_cost_value(build, values)
            + np.sum((b - np.asarray(build.inputs["reference"], dtype=float)) ** 2)
            + float(expressions["storage_cost"])
            + float(expressions["storage_terminal_cost"])
        )
        residuals["objective_reconstruction_abs"] = _objective_residual(
            build, float(reconstructed_objective)
        )
        injection = _incidence(np.array([0, 1, 1])) @ b
        residuals["component_injection_abs"] = float(
            np.max(np.abs(injection - expressions["storage_injection"]))
        )
        residuals.update(
            _network_residuals(build, values, injection, expressions["p_net"])
        )
        return residuals

    if build.gate == "nondispatchable":
        power = values["p_nd"]
        availability = np.asarray(build.inputs["availability"], dtype=float)
        rating = np.asarray(build.inputs["rating"], dtype=float)
        curtailment = availability - power
        residuals.update(
            availability_abs=float(max(0.0, np.max(power - availability))),
            rating_abs=float(max(0.0, np.max(power - rating[:, None]))),
            curtailment_abs=float(
                np.max(np.abs(curtailment - expressions["curtailment"]))
            ),
            curtailment_nonnegative_abs=float(max(0.0, -np.min(curtailment))),
            nondispatchable_preference_probe_only_abs=float(
                abs(
                    expressions["nondispatchable_preference_probe_only"]
                    - (
                        3.0 * DELTA_HOURS * np.sum(curtailment)
                        + 1e-3 * np.sum(power**2)
                    )
                )
            ),
        )
        reconstructed_objective = _network_cost_value(build, values) + float(
            expressions["nondispatchable_preference_probe_only"]
        )
        residuals["objective_reconstruction_abs"] = _objective_residual(
            build, float(reconstructed_objective)
        )
        injection = (
            _incidence(np.asarray(build.inputs["device_buses"], dtype=int)) @ power
        )
        residuals["component_injection_abs"] = float(
            np.max(np.abs(injection - expressions["nondispatchable_injection"]))
        )
        residuals.update(
            _network_residuals(build, values, injection, expressions["p_net"])
        )
        return residuals

    if build.gate == "load_shedding":
        fraction = values["load_shed_fraction"]
        load = np.asarray(build.inputs["p_load"], dtype=float)
        reactive_load = np.asarray(build.inputs["q_load"], dtype=float)
        eligibility = np.asarray(build.inputs["eligibility"], dtype=float)
        maximum = np.asarray(build.inputs["maximum_fraction"], dtype=float)
        shed = load * fraction
        served = load - shed
        cost = np.asarray(build.inputs["cost_per_mwh"], dtype=float)
        residuals.update(
            eligibility_abs=float(
                max(0.0, np.max(fraction - maximum[:, None] * eligibility))
            ),
            q_load_reconstruction_abs=float(
                np.max(np.abs(reactive_load - expressions["q_load"]))
            ),
            ineligible_fraction_abs=float(np.max(np.abs(fraction[eligibility == 0.0]))),
            shed_reconstruction_abs=float(
                np.max(np.abs(shed - expressions["p_load_shed"]))
            ),
            shed_total_reconstruction_abs=float(
                np.max(np.abs(np.sum(shed, axis=0) - expressions["p_load_shed_total"]))
            ),
            served_reconstruction_abs=float(
                np.max(np.abs(served - expressions["p_load_served"]))
            ),
            energy_not_served_by_load_abs=float(
                np.max(
                    np.abs(
                        DELTA_HOURS * np.sum(shed, axis=1)
                        - expressions["energy_not_served_by_load"]
                    )
                )
            ),
            energy_not_served_abs=float(
                abs(DELTA_HOURS * np.sum(shed) - expressions["energy_not_served"])
            ),
            shedding_cost_abs=float(
                abs(
                    expressions["load_shedding_cost"]
                    - DELTA_HOURS * np.sum(cost[:, None] * shed)
                )
            ),
        )
        reconstructed_objective = (
            _network_cost_value(build, values)
            + float(expressions["load_shedding_cost"])
            + 1e-4 * np.sum(fraction**2)
        )
        residuals["objective_reconstruction_abs"] = _objective_residual(
            build, float(reconstructed_objective)
        )
        injection = -(
            _incidence(np.asarray(build.inputs["device_buses"], dtype=int)) @ served
        )
        residuals["component_injection_abs"] = float(
            np.max(np.abs(injection - expressions["load_injection"]))
        )
        residuals.update(
            _network_residuals(build, values, injection, expressions["p_net"])
        )
        return residuals

    p_in = values["p_hvdc_in"]
    p_out = values["p_hvdc_out"]
    loss_fraction = np.asarray(build.inputs["loss_fraction"], dtype=float)
    expected_out = np.vstack(
        (
            -p_in[0, :] / (1.0 - loss_fraction[0]),
            -(1.0 - loss_fraction[1]) * p_in[1, :],
            -p_in[2, :],
            -p_in[3, :] / (1.0 - loss_fraction[3]),
            -p_in[4, :] / (1.0 - loss_fraction[4]),
        )
    )
    loss = -(p_in + p_out)
    residuals.update(
        hvdc_coupling_abs=float(np.max(np.abs(p_out - expected_out))),
        hvdc_loss_abs=float(np.max(np.abs(loss - expressions["hvdc_loss"]))),
        hvdc_loss_nonnegative_abs=float(max(0.0, -np.min(loss))),
        hvdc_cost_abs=float(
            abs(
                expressions["hvdc_cost"]
                - 0.01 * np.sum(p_in**2)
                - 0.02 * np.sum(np.abs(p_in))
            )
        ),
    )
    reconstructed_objective = (
        _network_cost_value(build, values)
        + np.sum((p_in - np.asarray(build.inputs["desired"], dtype=float)) ** 2)
        + float(expressions["hvdc_cost"])
    )
    residuals["objective_reconstruction_abs"] = _objective_residual(
        build, float(reconstructed_objective)
    )
    injection = (
        _incidence(np.zeros(5, dtype=int)) @ p_in
        + _incidence(np.ones(5, dtype=int)) @ p_out
    )
    residuals["component_injection_abs"] = float(
        np.max(np.abs(injection - expressions["hvdc_injection"]))
    )
    residuals.update(_network_residuals(build, values, injection, expressions["p_net"]))
    return residuals


def _binding_probe(
    box: CandidateBox,
    encoding: BoundEncoding,
) -> dict[str, Any]:
    """Drive both nondegenerate faces and every fixed coordinate exactly."""
    variable, constraints = _candidate_variable(box, encoding)
    parity = np.indices(box.lower.shape).sum(axis=0) % 2 == 0
    fixed = box.lower == box.upper
    desired = np.where(parity, box.lower, box.upper)
    width = np.maximum(box.upper - box.lower, 1.0)
    target = np.where(parity, box.lower - width, box.upper + width)
    problem = cp.Problem(cp.Minimize(cp.sum_squares(variable - target)), constraints)
    exception = None
    try:
        problem.solve(
            solver=cp.CLARABEL,
            canon_backend=cp.SCIPY_CANON_BACKEND,
            **CLARABEL_SOLVE_OPTIONS,
        )
    except Exception as error:
        exception = f"{type(error).__name__}: {error}"
    value = None if variable.value is None else np.asarray(variable.value, dtype=float)
    residual = None if value is None else float(np.max(np.abs(value - desired)))
    stats = problem.solver_stats
    return {
        "family": box.family,
        "variable": box.variable_name,
        "status": problem.status,
        "solver": None if stats is None else stats.solver_name,
        "canonicalization_backend": "SCIPY",
        "exception": exception,
        "lower_face_coordinates": int(np.count_nonzero(parity & ~fixed)),
        "upper_face_coordinates": int(np.count_nonzero(~parity & ~fixed)),
        "fixed_coordinates": int(np.count_nonzero(fixed)),
        "maximum_face_residual": residual,
        "accepted": exception is None
        and problem.status in ACCEPTED_STATUSES
        and residual is not None
        and residual <= AUDIT_TOLERANCE,
        "values": None if value is None else value.tolist(),
    }


def run_arm(
    formulation: Formulation,
    gate: GateName,
    encoding: BoundEncoding,
) -> dict[str, Any]:
    """Build, characterize, solve, extract, and audit one shared-harness arm."""
    started = time.perf_counter()
    build = build_probe(formulation, gate, encoding)
    construction = time.perf_counter() - started
    rss_after_construction = _peak_rss_bytes()
    source = _source_structure(build)
    started = time.perf_counter()
    canonical = _canonical_structure(build.problem)
    canonicalization = time.perf_counter() - started
    rss_after_canonicalization = _peak_rss_bytes()
    exception = None
    started = time.perf_counter()
    try:
        build.solve()
    except Exception as error:
        exception = f"{type(error).__name__}: {error}"
    solve_seconds = time.perf_counter() - started
    rss_after_solve = _peak_rss_bytes()
    values = _values(build)
    expression_values = _expression_values(build)
    complete = set(values) == set(build.variables) and set(expression_values) == set(
        build.expressions
    )
    finite = complete and all(
        np.isfinite(value).all()
        for value in (*values.values(), *expression_values.values())
    )
    residuals = _audit(build, values, expression_values) if finite else {}
    objective = build.problem.value
    finite_objective = objective is not None and np.isfinite(float(objective))
    status = build.problem.status
    accepted = (
        exception is None
        and status in ACCEPTED_STATUSES
        and finite
        and finite_objective
        and bool(residuals)
        and max(residuals.values()) <= AUDIT_TOLERANCE
    )
    if exception is not None:
        classification = "solver_failure"
    elif status in {cp.INFEASIBLE, cp.INFEASIBLE_INACCURATE}:
        classification = "solver_certified_infeasible"
    elif accepted:
        classification = "accepted"
    else:
        classification = "unusable_primal"
    public_results = {
        name: np.moveaxis(value, -1, 0).tolist()
        for name, value in values.items()
        if value.ndim > 0 and value.shape[-1] == HORIZON
    }
    for name, value in expression_values.items():
        view = build.expression_views[name]
        if view == "interval":
            if value.ndim == 0 or value.shape[-1] != HORIZON:
                raise ValueError(
                    f"interval expression {name!r} has invalid shape {value.shape}"
                )
            if name in public_results:
                raise ValueError(f"duplicate public result source {name!r}")
            public_results[name] = np.moveaxis(value, -1, 0).tolist()
        elif not name.endswith("cost") and not name.endswith("_probe_only"):
            if name in public_results:
                raise ValueError(f"duplicate public result source {name!r}")
            public_results[name] = value.tolist()
    if "soc" in values:
        public_results["soc"] = np.moveaxis(values["soc"][:, 1:], -1, 0).tolist()
    scalar_costs = {
        name: float(value)
        for name, value in expression_values.items()
        if value.shape == () and name.endswith("cost")
    }
    probe_only_objective_terms = {
        name: float(value)
        for name, value in expression_values.items()
        if value.shape == () and name.endswith("_probe_only")
    }
    stats = build.problem.solver_stats
    return {
        "formulation": formulation,
        "gate": gate,
        "encoding": encoding,
        "status": status,
        "classification": classification,
        "accepted": accepted,
        "exception": exception,
        "objective": float(objective) if finite_objective else None,
        "component_costs": scalar_costs,
        "probe_only_objective_terms": probe_only_objective_terms,
        "public_results": public_results,
        "residuals": residuals,
        "binding_probes": [
            _binding_probe(box, encoding) for box in build.candidate_boxes
        ],
        "source_structure": source,
        "canonical_structure": canonical,
        "timing_seconds": {
            "construction": construction,
            "canonicalization": canonicalization,
            "solve": solve_seconds,
        },
        "process_peak_rss_bytes": {
            "after_construction": rss_after_construction,
            "after_canonicalization": rss_after_canonicalization,
            "after_solve": rss_after_solve,
        },
        "solver": None if stats is None else stats.solver_name,
        "solver_iterations": None if stats is None else stats.num_iters,
        "solve_time": None if stats is None else stats.solve_time,
        "device_ids": np.asarray(build.inputs["device_ids"], dtype=object).tolist(),
        "fixture_sha256": _fixture_sha256(build),
    }


def compare_pair(explicit: dict[str, Any], leaf: dict[str, Any]) -> dict[str, Any]:
    """Apply one formulation-local explicit-versus-leaf equivalence gate."""
    if (explicit["formulation"], explicit["gate"]) != (
        leaf["formulation"],
        leaf["gate"],
    ):
        raise ValueError("component qualification pair identity mismatch")
    public_names = set(explicit["public_results"])
    schemas_match = public_names == set(leaf["public_results"])
    public_residuals = {
        name: float(
            np.max(
                np.abs(
                    np.asarray(explicit["public_results"][name], dtype=float)
                    - np.asarray(leaf["public_results"][name], dtype=float)
                )
            )
        )
        for name in sorted(public_names.intersection(leaf["public_results"]))
    }
    cost_names = set(explicit["component_costs"])
    cost_schemas_match = cost_names == set(leaf["component_costs"])
    cost_residuals = {
        name: abs(
            float(explicit["component_costs"][name])
            - float(leaf["component_costs"][name])
        )
        for name in sorted(cost_names.intersection(leaf["component_costs"]))
    }
    probe_term_names = set(explicit["probe_only_objective_terms"])
    probe_term_schemas_match = probe_term_names == set(
        leaf["probe_only_objective_terms"]
    )
    probe_term_residuals = {
        name: abs(
            float(explicit["probe_only_objective_terms"][name])
            - float(leaf["probe_only_objective_terms"][name])
        )
        for name in sorted(
            probe_term_names.intersection(leaf["probe_only_objective_terms"])
        )
    }
    objective_residual = (
        None
        if explicit["objective"] is None or leaf["objective"] is None
        else abs(float(explicit["objective"]) - float(leaf["objective"]))
    )
    explicit_probes = {probe["family"]: probe for probe in explicit["binding_probes"]}
    leaf_probes = {probe["family"]: probe for probe in leaf["binding_probes"]}
    probe_schemas_match = set(explicit_probes) == set(leaf_probes)
    probe_residuals = {
        family: float(
            np.max(
                np.abs(
                    np.asarray(explicit_probes[family]["values"], dtype=float)
                    - np.asarray(leaf_probes[family]["values"], dtype=float)
                )
            )
        )
        for family in sorted(set(explicit_probes).intersection(leaf_probes))
        if explicit_probes[family]["values"] is not None
        and leaf_probes[family]["values"] is not None
    }
    binding_passed = (
        probe_schemas_match
        and all(probe["accepted"] for probe in explicit_probes.values())
        and all(probe["accepted"] for probe in leaf_probes.values())
        and len(probe_residuals) == len(explicit_probes)
        and max(probe_residuals.values(), default=0.0) <= PAIR_ABSOLUTE_TOLERANCE
    )
    both_accepted = bool(explicit["accepted"] and leaf["accepted"])
    fixture_match = explicit["fixture_sha256"] == leaf["fixture_sha256"]
    equivalent = (
        both_accepted
        and fixture_match
        and schemas_match
        and cost_schemas_match
        and probe_term_schemas_match
        and binding_passed
        and objective_residual is not None
        and objective_residual <= PAIR_ABSOLUTE_TOLERANCE
        and max(public_residuals.values(), default=0.0) <= PAIR_ABSOLUTE_TOLERANCE
        and max(cost_residuals.values(), default=0.0) <= PAIR_ABSOLUTE_TOLERANCE
        and max(probe_term_residuals.values(), default=0.0) <= PAIR_ABSOLUTE_TOLERANCE
    )
    families = sorted(explicit_probes)
    return {
        "formulation": explicit["formulation"],
        "gate": explicit["gate"],
        "box_families": families,
        "both_accepted": both_accepted,
        "fixture_sha256": explicit["fixture_sha256"] if fixture_match else None,
        "fixture_fingerprints_match": fixture_match,
        "result_schemas_match": schemas_match,
        "cost_schemas_match": cost_schemas_match,
        "probe_only_objective_term_schemas_match": probe_term_schemas_match,
        "binding_probes_passed": binding_passed,
        "equivalent": equivalent,
        "objective_absolute_residual": objective_residual,
        "component_cost_absolute_residuals": cost_residuals,
        "probe_only_objective_term_absolute_residuals": probe_term_residuals,
        "public_result_absolute_residuals": public_residuals,
        "binding_probe_absolute_residuals": probe_residuals,
        "explicit": explicit,
        "leaf": leaf,
    }


def pair_decisions(pair: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Freeze one local decision per candidate box family."""
    passed = bool(pair["equivalent"])
    return {
        family: {
            "leaf_bounds_qualified": passed,
            "selected_representation": "leaf" if passed else "explicit",
            "reason": (
                "paired model and binding probe passed"
                if passed
                else "paired gate regressed; retain explicit inequalities"
            ),
        }
        for family in pair["box_families"]
    }


def run_all() -> dict[str, Any]:
    """Run all seven gates and retain nine formulation/family decisions."""
    pairs = []
    decisions: dict[str, dict[str, Any]] = {}
    for formulation, gate in GATE_PAIRS:
        pair = compare_pair(
            run_arm(formulation, gate, "explicit"),
            run_arm(formulation, gate, "leaf"),
        )
        pairs.append(pair)
        for family, decision in pair_decisions(pair).items():
            decisions[f"{formulation}/{family}"] = decision
    return {
        "schema_version": 1,
        "stage": "M14b_component_box_qualification",
        "horizon": HORIZON,
        "delta_hours": DELTA_HOURS,
        "audit_tolerance": AUDIT_TOLERANCE,
        "pair_absolute_tolerance": PAIR_ABSOLUTE_TOLERANCE,
        "solver": "CLARABEL",
        "solver_options": dict(CLARABEL_SOLVE_OPTIONS),
        "canonicalization_backend": "SCIPY",
        "cross_formulation_inference_permitted": False,
        "pairs": pairs,
        "decisions": decisions,
    }
