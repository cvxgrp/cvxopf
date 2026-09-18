"""Formulation-specific leaf-bound qualification for M14a.1.

This is an isolated vectorized characterization fixture.  It does not change
the public OPF builders or pre-authorize a bound representation for M14b.
"""

from __future__ import annotations

from dataclasses import dataclass
import resource
import sys
from typing import Any, Literal, cast
import time

import cvxpy as cp
import numpy as np

from cvxopf.cost import poly_cost_expr
from cvxopf.network import (
    make_branch_node_incidence_matrix,
    make_incidence_matrix,
    make_ybus_matpower,
    reindex_case_to_consecutive,
)
from cvxopf.testcases import case9


Formulation = Literal["ac", "lossy_dc", "singlenode_dc"]
BoundEncoding = Literal["explicit", "leaf"]
BoundProfile = Literal["static", "time_varying"]
FORMULATIONS: tuple[Formulation, ...] = ("ac", "lossy_dc", "singlenode_dc")
BOUND_ENCODINGS: tuple[BoundEncoding, ...] = ("explicit", "leaf")
BOUND_PROFILES: tuple[BoundProfile, ...] = ("static", "time_varying")
HORIZON = 3
AUDIT_TOLERANCE = 1e-6
PAIR_ABSOLUTE_TOLERANCE = 2e-4


def _peak_rss_bytes() -> int:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(raw if sys.platform == "darwin" else raw * 1024)


@dataclass(frozen=True)
class QualificationBuild:
    """One build-ready paired qualification problem."""

    formulation: Formulation
    encoding: BoundEncoding
    profile: BoundProfile
    problem: cp.Problem
    variables: dict[str, cp.Variable]
    inputs: dict[str, np.ndarray | float | int]

    def solve(self, *, convex_probe: bool = False) -> None:
        """Solve with the formulation's frozen qualification configuration."""
        if convex_probe:
            self.problem.solve(solver=cp.CLARABEL, canon_backend=cp.SCIPY_CANON_BACKEND)
        elif self.formulation == "ac":
            self.problem.solve(
                solver=cp.IPOPT,
                nlp=True,
                max_iter=500,
                print_level=0,
                sb="yes",
            )
        else:
            self.problem.solve(solver=cp.CLARABEL, canon_backend=cp.SCIPY_CANON_BACKEND)


def _bounds(
    lower: np.ndarray,
    upper: np.ndarray,
    profile: BoundProfile,
) -> tuple[np.ndarray, np.ndarray]:
    """Return static broadcast or fully materialized time-varying boxes."""
    if profile == "static":
        shape = (lower.size, HORIZON)
        return np.broadcast_to(lower[:, None], shape), np.broadcast_to(
            upper[:, None], shape
        )
    phase = np.linspace(0.0, 1.0, HORIZON)[None, :]
    width = upper - lower
    # Move both faces without excluding the nominal Case9 optimum.
    return (
        lower[:, None] + 0.002 * width[:, None] * phase,
        upper[:, None] - 0.002 * width[:, None] * phase,
    )


def _variable(
    name: str,
    shape: tuple[int, ...],
    lower: np.ndarray,
    upper: np.ndarray,
    encoding: BoundEncoding,
) -> tuple[cp.Variable, list[cp.Constraint]]:
    if encoding == "leaf":
        return cp.Variable(shape, name=name, bounds=[lower, upper]), []
    variable = cp.Variable(shape, name=name)
    return variable, [variable >= lower, variable <= upper]


def _case_data(profile: BoundProfile) -> dict[str, Any]:
    ppc, _mapping = reindex_case_to_consecutive(cast(dict[str, Any], case9()))
    base = float(ppc["baseMVA"])
    bus = np.asarray(ppc["bus"], dtype=float)
    gen = np.asarray(ppc["gen"], dtype=float)
    pg = _bounds(gen[:, 9] / base, gen[:, 8] / base, profile)
    qg = _bounds(gen[:, 4] / base, gen[:, 3] / base, profile)
    voltage = _bounds(bus[:, 12], bus[:, 11], profile)
    demand_scale = np.array([0.99, 1.0, 1.01])
    return {
        "ppc": ppc,
        "base": base,
        "bus": bus,
        "gen": gen,
        "Cg": make_incidence_matrix(ppc),
        "Pg_lower": pg[0],
        "Pg_upper": pg[1],
        "Qg_lower": qg[0],
        "Qg_upper": qg[1],
        "v_lower": voltage[0],
        "v_upper": voltage[1],
        "Pd": bus[:, 2, None] / base * demand_scale,
        "Qd": bus[:, 3, None] / base * demand_scale,
    }


def _generation_cost(data: dict[str, Any], pg: cp.Variable) -> cp.Expression:
    return sum(
        (
            poly_cost_expr(data["ppc"]["gencost"], pg[:, step] * data["base"])
            for step in range(HORIZON)
        ),
        start=cp.Constant(0.0),
    )


def build_qualification(
    formulation: Formulation,
    encoding: BoundEncoding,
    profile: BoundProfile,
) -> QualificationBuild:
    """Build one Case9 time-last qualification model."""
    if formulation not in FORMULATIONS:
        raise ValueError("unsupported formulation")
    if encoding not in BOUND_ENCODINGS:
        raise ValueError("unsupported bound encoding")
    if profile not in BOUND_PROFILES:
        raise ValueError("unsupported bound profile")
    data = _case_data(profile)
    ng = int(data["gen"].shape[0])
    nb = int(data["bus"].shape[0])
    pg, constraints = _variable(
        "Pg",
        (ng, HORIZON),
        data["Pg_lower"],
        data["Pg_upper"],
        encoding,
    )
    variables = {"Pg": pg}

    if formulation == "singlenode_dc":
        constraints.append(cp.sum(pg, axis=0) == cp.sum(data["Pd"], axis=0))
        objective = _generation_cost(data, pg)
    elif formulation == "lossy_dc":
        branch = np.asarray(data["ppc"]["branch"], dtype=float)
        nl = int(branch.shape[0])
        flow_limit = branch[:, 5] / data["base"]
        flow_limit = np.where(flow_limit == 0.0, 1e4, flow_limit)
        flow_profile = _bounds(-flow_limit, flow_limit, profile)
        flow, flow_constraints = _variable(
            "p_flows",
            (nl, HORIZON),
            flow_profile[0],
            flow_profile[1],
            encoding,
        )
        variables["p_flows"] = flow
        data["p_flows_lower"] = flow_profile[0]
        data["p_flows_upper"] = flow_profile[1]
        constraints.extend(flow_constraints)
        incidence = make_branch_node_incidence_matrix(data["ppc"])
        constraints.append(incidence @ flow + data["Cg"] @ pg - data["Pd"] == 0)
        resistance = branch[:, 2, None]
        objective = _generation_cost(data, pg) + 1e-3 * cp.sum(
            cp.multiply(resistance, cp.square(flow))
        )
    else:
        qg, qg_constraints = _variable(
            "Qg",
            (ng, HORIZON),
            data["Qg_lower"],
            data["Qg_upper"],
            encoding,
        )
        voltage, voltage_constraints = _variable(
            "v",
            (nb, HORIZON),
            data["v_lower"],
            data["v_upper"],
            encoding,
        )
        theta = cp.Variable((nb, HORIZON), name="theta")
        variables.update(Qg=qg, v=voltage, theta=theta)
        constraints.extend(qg_constraints)
        constraints.extend(voltage_constraints)
        constraints.append(theta[0, :] == 0.0)
        admittance = make_ybus_matpower(data["ppc"])
        conductance = admittance.real
        susceptance = admittance.imag
        data["G"] = conductance
        data["B"] = susceptance
        for step in range(HORIZON):
            angle = theta[:, step, None] - theta[None, :, step]
            voltage_product = voltage[:, step, None] @ voltage[None, :, step]
            active = cp.sum(
                cp.multiply(
                    voltage_product,
                    cp.multiply(conductance, cp.nlp.cos(angle))
                    + cp.multiply(susceptance, cp.nlp.sin(angle)),
                ),
                axis=1,
            )
            reactive = cp.sum(
                cp.multiply(
                    voltage_product,
                    cp.multiply(conductance, cp.nlp.sin(angle))
                    - cp.multiply(susceptance, cp.nlp.cos(angle)),
                ),
                axis=1,
            )
            constraints.extend(
                (
                    active == data["Cg"] @ pg[:, step] - data["Pd"][:, step],
                    reactive == data["Cg"] @ qg[:, step] - data["Qd"][:, step],
                )
            )
        objective = _generation_cost(data, pg)

    return QualificationBuild(
        formulation,
        encoding,
        profile,
        cp.Problem(cp.Minimize(objective), constraints),
        variables,
        {
            key: value
            for key, value in data.items()
            if key != "ppc" and isinstance(value, (np.ndarray, float, int))
        },
    )


def _source_structure(build: QualificationBuild) -> dict[str, Any]:
    problem = build.problem
    metrics = problem.size_metrics
    constraints = problem.constraints
    return {
        "variable_objects": len(problem.variables()),
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
        cp.CLARABEL, canon_backend=cp.SCIPY_CANON_BACKEND
    )
    dimensions = data["dims"]
    matrix = data["A"]
    quadratic = data.get("P")
    return {
        "backend": "SCIPY",
        "canonical_variables": int(data["c"].shape[0]),
        "equality_rows": int(dimensions.zero),
        "nonnegative_rows": int(dimensions.nonneg),
        "coefficient_rows": int(matrix.shape[0]),
        "coefficient_columns": int(matrix.shape[1]),
        "coefficient_nonzeros": int(matrix.nnz),
        "quadratic_nonzeros": 0 if quadratic is None else int(quadratic.nnz),
        "reductions": [type(item).__name__ for item in chain.reductions],
    }


def _initialize(build: QualificationBuild) -> None:
    for name, variable in build.variables.items():
        lower = (
            np.asarray(build.inputs[f"{name}_lower"], dtype=float)
            if f"{name}_lower" in build.inputs
            else None
        )
        upper = (
            np.asarray(build.inputs[f"{name}_upper"], dtype=float)
            if f"{name}_upper" in build.inputs
            else None
        )
        if lower is not None and upper is not None:
            variable.value = np.broadcast_to(
                (lower + upper) / 2.0, variable.shape
            ).copy()
        elif name == "theta":
            variable.value = np.zeros(variable.shape)
        elif name == "p_flows":
            variable.value = np.zeros(variable.shape)


def _audit(
    build: QualificationBuild, values: dict[str, np.ndarray]
) -> dict[str, float]:
    pg = values["Pg"]
    lower = np.broadcast_to(build.inputs["Pg_lower"], pg.shape)
    upper = np.broadcast_to(build.inputs["Pg_upper"], pg.shape)
    residuals = {"Pg_box_abs": float(max(0.0, np.max(lower - pg), np.max(pg - upper)))}
    if build.formulation == "singlenode_dc":
        residuals["active_balance_abs"] = float(
            np.max(np.abs(np.sum(pg, axis=0) - np.sum(build.inputs["Pd"], axis=0)))
        )
    elif build.formulation == "lossy_dc":
        flow = values["p_flows"]
        ppc, _ = reindex_case_to_consecutive(cast(dict[str, Any], case9()))
        balance = (
            make_branch_node_incidence_matrix(ppc) @ flow
            + build.inputs["Cg"] @ pg
            - build.inputs["Pd"]
        )
        residuals["active_balance_abs"] = float(np.max(np.abs(balance)))
        residuals["flow_box_abs"] = float(
            max(
                0.0,
                np.max(build.inputs["p_flows_lower"] - flow),
                np.max(flow - build.inputs["p_flows_upper"]),
            )
        )
    else:
        qg = values["Qg"]
        voltage = values["v"]
        residuals["Qg_box_abs"] = float(
            max(
                0.0,
                np.max(np.broadcast_to(build.inputs["Qg_lower"], qg.shape) - qg),
                np.max(qg - np.broadcast_to(build.inputs["Qg_upper"], qg.shape)),
            )
        )
        residuals["voltage_box_abs"] = float(
            max(
                0.0,
                np.max(
                    np.broadcast_to(build.inputs["v_lower"], voltage.shape) - voltage
                ),
                np.max(
                    voltage - np.broadcast_to(build.inputs["v_upper"], voltage.shape)
                ),
            )
        )
        theta = values["theta"]
        conductance = np.asarray(build.inputs["G"], dtype=float)
        susceptance = np.asarray(build.inputs["B"], dtype=float)
        incidence = np.asarray(build.inputs["Cg"], dtype=float)
        active_demand = np.asarray(build.inputs["Pd"], dtype=float)
        reactive_demand = np.asarray(build.inputs["Qd"], dtype=float)
        angle = theta[:, None, :] - theta[None, :, :]
        voltage_product = voltage[:, None, :] * voltage[None, :, :]
        active = np.sum(
            voltage_product
            * (
                conductance[:, :, None] * np.cos(angle)
                + susceptance[:, :, None] * np.sin(angle)
            ),
            axis=1,
        )
        reactive = np.sum(
            voltage_product
            * (
                conductance[:, :, None] * np.sin(angle)
                - susceptance[:, :, None] * np.cos(angle)
            ),
            axis=1,
        )
        residuals["active_balance_abs"] = float(
            np.max(np.abs(active - (incidence @ pg - active_demand)))
        )
        residuals["reactive_balance_abs"] = float(
            np.max(np.abs(reactive - (incidence @ qg - reactive_demand)))
        )
        residuals["reference_angle_abs"] = float(np.max(np.abs(theta[0, :])))
    return residuals


def _binding_probe(build: QualificationBuild) -> list[dict[str, Any]]:
    """Drive both faces of every candidate box active under the same solver."""
    names = ["Pg"]
    if build.formulation == "lossy_dc":
        names.append("p_flows")
    elif build.formulation == "ac":
        names.extend(("Qg", "v"))
    records = []
    for name in names:
        lower = np.asarray(build.inputs[f"{name}_lower"], dtype=float)
        upper = np.asarray(build.inputs[f"{name}_upper"], dtype=float)
        variable, constraints = _variable(
            name,
            tuple(int(item) for item in lower.shape),
            lower,
            upper,
            build.encoding,
        )
        parity = np.indices(lower.shape).sum(axis=0) % 2 == 0
        desired = np.where(parity, lower, upper)
        width = np.maximum(upper - lower, 1.0)
        target = np.where(parity, lower - width, upper + width)
        variable.value = (lower + upper) / 2.0
        probe = QualificationBuild(
            build.formulation,
            build.encoding,
            "time_varying",
            cp.Problem(cp.Minimize(cp.sum_squares(variable - target)), constraints),
            {name: variable},
            {f"{name}_lower": lower, f"{name}_upper": upper},
        )
        exception = None
        try:
            probe.solve(convex_probe=True)
        except Exception as error:
            exception = f"{type(error).__name__}: {error}"
        value = (
            None if variable.value is None else np.asarray(variable.value, dtype=float)
        )
        face_residual = (
            None if value is None else float(np.max(np.abs(value - desired)))
        )
        records.append(
            {
                "variable": name,
                "status": probe.problem.status,
                "solver": probe.problem.solver_stats.solver_name,
                "canonicalization_backend": "SCIPY",
                "exception": exception,
                "lower_face_coordinates": int(np.count_nonzero(parity)),
                "upper_face_coordinates": int(np.count_nonzero(~parity)),
                "maximum_face_residual": face_residual,
                "accepted": exception is None
                and probe.problem.status in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}
                and face_residual is not None
                and face_residual <= AUDIT_TOLERANCE,
                "values": None if value is None else value.tolist(),
            }
        )
    return records


def run_qualification(
    formulation: Formulation,
    encoding: BoundEncoding,
    profile: BoundProfile,
) -> dict[str, Any]:
    """Build, solve, extract, and independently audit one qualification arm."""
    started = time.perf_counter()
    build = build_qualification(formulation, encoding, profile)
    construction = time.perf_counter() - started
    rss_after_construction = _peak_rss_bytes()
    source = _source_structure(build)
    canonical = None
    canonicalization = None
    if formulation != "ac":
        started = time.perf_counter()
        canonical = _canonical_structure(build.problem)
        canonicalization = time.perf_counter() - started
    rss_after_canonicalization = _peak_rss_bytes()
    _initialize(build)
    started = time.perf_counter()
    exception = None
    try:
        build.solve()
    except Exception as error:  # retain formulation-local solver behavior
        exception = f"{type(error).__name__}: {error}"
    solve = time.perf_counter() - started
    rss_after_solve = _peak_rss_bytes()
    complete_values = all(
        variable.value is not None for variable in build.variables.values()
    )
    values = (
        {
            name: np.asarray(variable.value, dtype=float)
            for name, variable in build.variables.items()
        }
        if complete_values
        else {}
    )
    finite_values = bool(values) and all(
        np.isfinite(value).all() for value in values.values()
    )
    residuals = _audit(build, values) if finite_values else {}
    stats = build.problem.solver_stats
    objective = build.problem.value
    finite_objective = objective is not None and np.isfinite(float(objective))
    accepted = (
        exception is None
        and build.problem.status in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}
        and finite_values
        and finite_objective
        and bool(residuals)
        and max(residuals.values()) <= AUDIT_TOLERANCE
    )
    binding_probes = _binding_probe(build)
    return {
        "formulation": formulation,
        "encoding": encoding,
        "profile": profile,
        "status": build.problem.status,
        "accepted": accepted,
        "exception": exception,
        "objective": float(objective) if finite_objective else None,
        "values": {name: value.tolist() for name, value in values.items()},
        "residuals": residuals,
        "binding_probes": binding_probes,
        "source_structure": source,
        "canonical_structure": canonical,
        "timing_seconds": {
            "construction": construction,
            "canonicalization": canonicalization,
            "solve": solve,
        },
        "process_peak_rss_bytes": {
            "after_construction": rss_after_construction,
            "after_canonicalization": rss_after_canonicalization,
            "after_solve": rss_after_solve,
        },
        "solver": stats.solver_name,
        "solver_iterations": stats.num_iters,
        "solve_time": stats.solve_time,
    }


def compare_pair(explicit: dict[str, Any], leaf: dict[str, Any]) -> dict[str, Any]:
    """Compare one formulation/profile pair without cross-formulation inference."""
    if (explicit["formulation"], explicit["profile"]) != (
        leaf["formulation"],
        leaf["profile"],
    ):
        raise ValueError("qualification pair identity mismatch")
    names = set(explicit["values"])
    schemas_match = names == set(leaf["values"])
    value_residuals = {
        name: float(
            np.max(
                np.abs(
                    np.asarray(explicit["values"][name], dtype=float)
                    - np.asarray(leaf["values"][name], dtype=float)
                )
            )
        )
        for name in sorted(names.intersection(leaf["values"]))
    }
    objective_residual = (
        None
        if explicit["objective"] is None or leaf["objective"] is None
        else abs(float(explicit["objective"]) - float(leaf["objective"]))
    )
    accepted = bool(explicit["accepted"] and leaf["accepted"])
    explicit_probes = {probe["variable"]: probe for probe in explicit["binding_probes"]}
    leaf_probes = {probe["variable"]: probe for probe in leaf["binding_probes"]}
    probe_schemas_match = set(explicit_probes) == set(leaf_probes)
    probe_value_residuals = {
        name: float(
            np.max(
                np.abs(
                    np.asarray(explicit_probes[name]["values"], dtype=float)
                    - np.asarray(leaf_probes[name]["values"], dtype=float)
                )
            )
        )
        for name in sorted(set(explicit_probes).intersection(leaf_probes))
        if explicit_probes[name]["values"] is not None
        and leaf_probes[name]["values"] is not None
    }
    binding_probes_passed = (
        probe_schemas_match
        and all(probe["accepted"] for probe in explicit_probes.values())
        and all(probe["accepted"] for probe in leaf_probes.values())
        and len(probe_value_residuals) == len(explicit_probes)
        and max(probe_value_residuals.values(), default=0.0) <= PAIR_ABSOLUTE_TOLERANCE
    )
    gated_value_names = ("Pg",)
    equivalent = (
        accepted
        and schemas_match
        and binding_probes_passed
        and objective_residual is not None
        and objective_residual <= PAIR_ABSOLUTE_TOLERANCE
        and max((value_residuals[name] for name in gated_value_names), default=0.0)
        <= PAIR_ABSOLUTE_TOLERANCE
    )
    return {
        "formulation": explicit["formulation"],
        "profile": explicit["profile"],
        "both_accepted": accepted,
        "result_schemas_match": schemas_match,
        "binding_probes_passed": binding_probes_passed,
        "binding_probe_value_absolute_residuals": probe_value_residuals,
        "equivalent": equivalent,
        "objective_absolute_residual": objective_residual,
        "gated_value_names": list(gated_value_names),
        "value_absolute_residuals": value_residuals,
        "explicit": explicit,
        "leaf": leaf,
    }


def formulation_decision(
    formulation: Formulation, pairs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply the formulation-local qualification rule."""
    isolated_passed = all(pair["equivalent"] for pair in pairs)
    production_qualified = isolated_passed and formulation != "ac"
    candidate_boxes = {
        "ac": ["Pg", "Qg", "v"],
        "lossy_dc": ["Pg", "p_flows"],
        "singlenode_dc": ["Pg"],
    }[formulation]
    return {
        "isolated_leaf_compatibility_passed": isolated_passed,
        "leaf_bounds_qualified": production_qualified,
        "selected_representation": "leaf" if production_qualified else "explicit",
        "qualified_variable_boxes": candidate_boxes if production_qualified else [],
        "isolated_candidate_boxes": candidate_boxes,
        "profiles": list(BOUND_PROFILES),
        "reason": (
            "both frozen profiles and binding probes passed"
            if production_qualified
            else (
                "isolated AC compatibility passed, but the production lifted "
                "DNLP and terminal-policy risk remains; retain explicit inequalities"
                if isolated_passed and formulation == "ac"
                else "a paired gate regressed; retain explicit inequalities"
            )
        ),
    }


def run_all() -> dict[str, Any]:
    """Run the complete frozen matrix and decide each formulation separately."""
    pairs = []
    decisions: dict[str, dict[str, Any]] = {}
    for formulation in FORMULATIONS:
        formulation_pairs = []
        for profile in BOUND_PROFILES:
            pair = compare_pair(
                run_qualification(formulation, "explicit", profile),
                run_qualification(formulation, "leaf", profile),
            )
            pairs.append(pair)
            formulation_pairs.append(pair)
        decisions[formulation] = formulation_decision(formulation, formulation_pairs)
    return {
        "schema_version": 1,
        "stage": "M14a.1_leaf_bound_qualification",
        "horizon": HORIZON,
        "audit_tolerance": AUDIT_TOLERANCE,
        "pair_absolute_tolerance": PAIR_ABSOLUTE_TOLERANCE,
        "decisions": decisions,
        "pairs": pairs,
        "cross_formulation_inference_permitted": False,
    }
