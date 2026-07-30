"""M4 Stage-0 characterization of AC branch terminal-flow expressions.

This script is intentionally self-contained. It exercises candidate branch
mathematics around the existing public builders without modifying production
assembly.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
import tracemalloc

import cvxpy as cp
import numpy as np
import pandas as pd

from cvxopf.network import make_ybus_matpower, reindex_case_to_consecutive
from cvxopf.problem import OPFOptions, build_opf, build_opf_multistep
from cvxopf.testcases import case9, case14, case30, case39, case57, case118
from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.testcases.case9_pwl import case9_pwl
from cvxopf.testcases.case30pwl import case30pwl


F_BUS = 0
T_BUS = 1
BR_R = 2
BR_X = 3
BR_B = 4
RATE_A = 5
TAP = 8
SHIFT = 9
BR_STATUS = 10

RESULTS_PATH = Path(__file__).parent / "results" / "s0_results.json"


@dataclass(frozen=True)
class BranchPrimitive:
    """Branch-only terminal admittances in original branch-table row order."""

    f_bus: np.ndarray
    t_bus: np.ndarray
    status: np.ndarray
    rate_a_mva: np.ndarray
    yff: np.ndarray
    yft: np.ndarray
    ytf: np.ndarray
    ytt: np.ndarray


@dataclass(frozen=True)
class TerminalExpressions:
    """Four ordered per-unit terminal-power expression vectors."""

    p_from: cp.Expression
    q_from: cp.Expression
    p_to: cp.Expression
    q_to: cp.Expression


CASE_FACTORIES = {
    "case9": case9,
    "case9_pwl": case9_pwl,
    "case9_dcline": case9_dcline,
    "case14": case14,
    "case30": case30,
    "case30pwl": case30pwl,
    "case39": case39,
    "case57": case57,
    "case118": case118,
}


def branch_primitive(case: dict) -> BranchPrimitive:
    """Build the Stage-0 candidate primitive, skipping inactive rows early."""
    branch = np.asarray(case["branch"])
    nl = branch.shape[0]
    status_raw = branch[:, BR_STATUS]
    status = status_raw == 1

    yff = np.zeros(nl, dtype=complex)
    yft = np.zeros(nl, dtype=complex)
    ytf = np.zeros(nl, dtype=complex)
    ytt = np.zeros(nl, dtype=complex)

    for e in range(nl):
        if not status[e]:
            continue
        r = float(branch[e, BR_R])
        x = float(branch[e, BR_X])
        b = float(branch[e, BR_B])
        tap = float(branch[e, TAP])
        shift = float(branch[e, SHIFT])

        admittance = 1.0 / complex(r, x)
        charging = 0.5j * b
        if tap == 0.0:
            tap = 1.0
        ratio = tap * np.exp(1j * np.deg2rad(shift))

        yff[e] = (admittance + charging) / (ratio * np.conj(ratio))
        yft[e] = -admittance / np.conj(ratio)
        ytf[e] = -admittance / ratio
        ytt[e] = admittance + charging

    return BranchPrimitive(
        f_bus=branch[:, F_BUS].astype(int),
        t_bus=branch[:, T_BUS].astype(int),
        status=status,
        rate_a_mva=branch[:, RATE_A].astype(float),
        yff=yff,
        yft=yft,
        ytf=ytf,
        ytt=ytt,
    )


def _terminal_pair(
    theta: cp.Variable,
    voltage: cp.Variable,
    i: int,
    j: int,
    yii: complex,
    yij: complex,
) -> tuple[cp.Expression, cp.Expression]:
    """Return one terminal's scalar real and reactive power expressions."""
    vi = voltage[i, 0]
    vj = voltage[j, 0]
    angle = theta[i, 0] - theta[j, 0]
    cosine = cp.nlp.cos(angle)
    sine = cp.nlp.sin(angle)
    self_p = float(yii.real) * vi**2
    self_q = -float(yii.imag) * vi**2
    cross_scale = vi * vj
    cross_p = cross_scale * (float(yij.real) * cosine + float(yij.imag) * sine)
    cross_q = cross_scale * (float(yij.real) * sine - float(yij.imag) * cosine)
    return self_p + cross_p, self_q + cross_q


def terminal_expressions(
    theta: cp.Variable,
    voltage: cp.Variable,
    primitive: BranchPrimitive,
) -> TerminalExpressions:
    """Construct scalar-indexed direct expressions in branch-table order."""
    nl = len(primitive.f_bus)
    if nl == 0:
        empty = cp.Constant(np.empty(0))
        return TerminalExpressions(empty, empty, empty, empty)

    p_from = []
    q_from = []
    p_to = []
    q_to = []
    for e in range(nl):
        if not primitive.status[e]:
            zero = cp.Constant(0.0)
            p_from.append(zero)
            q_from.append(zero)
            p_to.append(zero)
            q_to.append(zero)
            continue
        f = int(primitive.f_bus[e])
        t = int(primitive.t_bus[e])
        pf, qf = _terminal_pair(
            theta, voltage, f, t, primitive.yff[e], primitive.yft[e]
        )
        pt, qt = _terminal_pair(
            theta, voltage, t, f, primitive.ytt[e], primitive.ytf[e]
        )
        p_from.append(pf)
        q_from.append(qf)
        p_to.append(pt)
        q_to.append(qt)
    return TerminalExpressions(
        cp.hstack(p_from),
        cp.hstack(q_from),
        cp.hstack(p_to),
        cp.hstack(q_to),
    )


def independent_terminal_power(
    voltage_magnitude: np.ndarray,
    voltage_angle: np.ndarray,
    primitive: BranchPrimitive,
) -> tuple[np.ndarray, np.ndarray]:
    """Evaluate terminal powers by independent complex current arithmetic."""
    complex_voltage = voltage_magnitude * np.exp(1j * voltage_angle)
    vf = complex_voltage[primitive.f_bus]
    vt = complex_voltage[primitive.t_bus]
    current_from = primitive.yff * vf + primitive.yft * vt
    current_to = primitive.ytf * vf + primitive.ytt * vt
    return vf * np.conj(current_from), vt * np.conj(current_to)


def normalized_limit_constraints(
    expressions: TerminalExpressions,
    primitive: BranchPrimitive,
    base_mva: float,
) -> list[cp.Constraint]:
    """Apply both terminal limits with unit right-hand sides."""
    constraints = []
    constrained = np.flatnonzero(
        primitive.status
        & np.isfinite(primitive.rate_a_mva)
        & (primitive.rate_a_mva > 0)
    )
    for e in constrained:
        rating_pu = float(primitive.rate_a_mva[e] / base_mva)
        constraints.extend(
            [
                cp.square(expressions.p_from[e] / rating_pu)
                + cp.square(expressions.q_from[e] / rating_pu)
                <= 1.0,
                cp.square(expressions.p_to[e] / rating_pu)
                + cp.square(expressions.q_to[e] / rating_pu)
                <= 1.0,
            ]
        )
    return constraints


def lifted_definition_constraints(
    direct: TerminalExpressions,
    primitive: BranchPrimitive,
) -> tuple[TerminalExpressions, list[cp.Constraint]]:
    """Lift terminal powers, with an explicit zero-branch exception."""
    nl = len(primitive.f_bus)
    if nl == 0:
        empty = cp.Constant(np.empty(0))
        return TerminalExpressions(empty, empty, empty, empty), []

    lifted = TerminalExpressions(
        cp.Variable(nl, name="s0_p_from"),
        cp.Variable(nl, name="s0_q_from"),
        cp.Variable(nl, name="s0_p_to"),
        cp.Variable(nl, name="s0_q_to"),
    )
    equalities = [
        lifted.p_from == direct.p_from,
        lifted.q_from == direct.q_from,
        lifted.p_to == direct.p_to,
        lifted.q_to == direct.q_to,
    ]
    return lifted, equalities


def _solve_problem(problem: cp.Problem) -> tuple[str, float]:
    start = time.perf_counter()
    problem.solve(
        solver=cp.IPOPT,
        nlp=True,
        verbose=False,
        print_level=0,
        sb="yes",
    )
    return str(problem.status), time.perf_counter() - start


def _measure_build(
    case: dict,
    sparsity_tol: float = 0.0,
    *,
    sparse_pq: bool = True,
):
    tracemalloc.start()
    start = time.perf_counter()
    build = build_opf(
        case,
        formulation="ac",
        options=OPFOptions(
            sparsity_tol=sparsity_tol,
            sparse_pq=sparse_pq,
        ),
    )
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return build, elapsed, peak


def characterize_baseline(
    case_name: str,
    *,
    sparse_pq: bool = True,
) -> dict:
    """Measure the unchanged pre-M4 AC problem."""
    original = CASE_FACTORIES[case_name]()
    build, build_seconds, peak_bytes = _measure_build(original, sparse_pq=sparse_pq)
    status, solve_seconds = _solve_problem(build.prob)
    size = build.prob.size_metrics
    return {
        "case": case_name,
        "mode": "pre_m4_baseline",
        "sparse_pq": sparse_pq,
        "status": status,
        "objective": (None if build.prob.value is None else float(build.prob.value)),
        "baseline_build_seconds": build_seconds,
        "report_expression_seconds": 0.0,
        "solve_wall_seconds": solve_seconds,
        "peak_build_bytes": int(peak_bytes),
        "num_scalar_variables": int(size.num_scalar_variables),
        "num_scalar_equalities": int(size.num_scalar_eq_constr),
        "num_scalar_inequalities": int(size.num_scalar_leq_constr),
    }


def characterize_case(
    case_name: str,
    *,
    enforce: bool,
    sparse_pq: bool = True,
) -> dict:
    """Measure baseline/reporting/direct-or-lifted solve behavior."""
    original = CASE_FACTORIES[case_name]()
    reindexed, _ = reindex_case_to_consecutive(original)
    primitive = branch_primitive(reindexed)
    build, build_seconds, peak_bytes = _measure_build(original, sparse_pq=sparse_pq)

    report_start = time.perf_counter()
    direct = terminal_expressions(
        build.variables["theta"], build.variables["v"], primitive
    )
    report_seconds = time.perf_counter() - report_start

    extra_constraints = (
        normalized_limit_constraints(direct, primitive, float(reindexed["baseMVA"]))
        if enforce
        else []
    )
    problem = cp.Problem(
        build.prob.objective,
        [*build.prob.constraints, *extra_constraints],
    )
    status, solve_seconds = _solve_problem(problem)

    voltage = build.variables["v"].value
    angle = build.variables["theta"].value
    max_fixed_error = None
    if voltage is not None and angle is not None:
        sf, st = independent_terminal_power(
            voltage.flatten(), angle.flatten(), primitive
        )
        errors = [
            np.max(np.abs(np.asarray(direct.p_from.value) - sf.real)),
            np.max(np.abs(np.asarray(direct.q_from.value) - sf.imag)),
            np.max(np.abs(np.asarray(direct.p_to.value) - st.real)),
            np.max(np.abs(np.asarray(direct.q_to.value) - st.imag)),
        ]
        max_fixed_error = float(max(errors))

    size = problem.size_metrics
    return {
        "case": case_name,
        "mode": ("direct_enforced" if enforce else "unused_direct_reporting"),
        "sparse_pq": sparse_pq,
        "status": status,
        "objective": (None if problem.value is None else float(problem.value)),
        "baseline_build_seconds": build_seconds,
        "report_expression_seconds": report_seconds,
        "solve_wall_seconds": solve_seconds,
        "peak_build_bytes": int(peak_bytes),
        "num_scalar_variables": int(size.num_scalar_variables),
        "num_scalar_equalities": int(size.num_scalar_eq_constr),
        "num_scalar_inequalities": int(size.num_scalar_leq_constr),
        "fixed_state_max_abs_error_pu": max_fixed_error,
    }


def characterize_lifted(
    case_name: str,
    *,
    enforce: bool,
    sparse_pq: bool = True,
) -> dict:
    original = CASE_FACTORIES[case_name]()
    reindexed, _ = reindex_case_to_consecutive(original)
    primitive = branch_primitive(reindexed)
    build, build_seconds, peak_bytes = _measure_build(original, sparse_pq=sparse_pq)
    direct_start = time.perf_counter()
    direct = terminal_expressions(
        build.variables["theta"], build.variables["v"], primitive
    )
    direct_seconds = time.perf_counter() - direct_start

    lift_start = time.perf_counter()
    lifted, extra_constraints = lifted_definition_constraints(direct, primitive)
    if enforce:
        extra_constraints.extend(
            normalized_limit_constraints(lifted, primitive, float(reindexed["baseMVA"]))
        )
    lift_seconds = time.perf_counter() - lift_start
    problem = cp.Problem(
        build.prob.objective,
        [*build.prob.constraints, *extra_constraints],
    )
    status, solve_seconds = _solve_problem(problem)
    size = problem.size_metrics
    return {
        "case": case_name,
        "mode": ("lifted_enforced" if enforce else "lifted_reporting"),
        "sparse_pq": sparse_pq,
        "status": status,
        "objective": (None if problem.value is None else float(problem.value)),
        "baseline_build_seconds": build_seconds,
        "report_expression_seconds": direct_seconds,
        "lift_expression_seconds": lift_seconds,
        "solve_wall_seconds": solve_seconds,
        "peak_build_bytes": int(peak_bytes),
        "num_scalar_variables": int(size.num_scalar_variables),
        "num_scalar_equalities": int(size.num_scalar_eq_constr),
        "num_scalar_inequalities": int(size.num_scalar_leq_constr),
    }


def characterize_sparsity(case_name: str, tolerances: tuple[float, ...]) -> list[dict]:
    """Measure thresholded nodal inconsistency against exact Ybus physics."""
    records = []
    original = CASE_FACTORIES[case_name]()
    reindexed, _ = reindex_case_to_consecutive(original)
    exact_ybus = make_ybus_matpower(reindexed)
    for tolerance in tolerances:
        build, build_seconds, peak_bytes = _measure_build(
            original, sparsity_tol=tolerance
        )
        status, solve_seconds = _solve_problem(build.prob)
        voltage = build.variables["v"].value
        angle = build.variables["theta"].value
        mismatch_mva = None
        if voltage is not None and angle is not None:
            vm = voltage.flatten()
            va = angle.flatten()
            complex_voltage = vm * np.exp(1j * va)
            exact_injection = complex_voltage * np.conj(exact_ybus @ complex_voltage)
            model_injection = np.asarray(build.variables["p"].value) + 1j * np.asarray(
                build.variables["q"].value
            )
            mismatch_mva = float(
                np.max(np.abs(exact_injection - model_injection))
                * float(reindexed["baseMVA"])
            )
        records.append(
            {
                "case": case_name,
                "sparsity_tol": tolerance,
                "status": status,
                "build_seconds": build_seconds,
                "solve_wall_seconds": solve_seconds,
                "peak_build_bytes": int(peak_bytes),
                "nnz_retained": int(len(build.data["rows"])),
                "max_exact_nodal_mismatch_mva": mismatch_mva,
            }
        )
    return records


def characterize_multistep(*, lifted: bool, enforce: bool = True) -> dict:
    """Measure candidate reporting/enforcement structures for T=3."""
    original = case9()
    reindexed, _ = reindex_case_to_consecutive(original)
    primitive = branch_primitive(reindexed)
    t_steps = 3
    active = original["bus"][:, 2]
    reactive = original["bus"][:, 3]
    df_p = pd.DataFrame(np.tile(active, (t_steps, 1)))
    df_q = pd.DataFrame(np.tile(reactive, (t_steps, 1)))

    start = time.perf_counter()
    build = build_opf_multistep(
        original,
        df_p,
        df_q,
        T=t_steps,
        formulation="ac",
    )
    baseline_seconds = time.perf_counter() - start
    report_start = time.perf_counter()
    expressions = [
        terminal_expressions(theta, voltage, primitive)
        for theta, voltage in zip(
            build.variables["theta"], build.variables["v"], strict=True
        )
    ]
    report_seconds = time.perf_counter() - report_start
    constraints = []
    for expression in expressions:
        if lifted:
            lifted_expression, step_constraints = lifted_definition_constraints(
                expression, primitive
            )
            constraints.extend(step_constraints)
            if enforce:
                constraints.extend(
                    normalized_limit_constraints(
                        lifted_expression,
                        primitive,
                        float(reindexed["baseMVA"]),
                    )
                )
        elif enforce:
            constraints.extend(
                normalized_limit_constraints(
                    expression, primitive, float(reindexed["baseMVA"])
                )
            )
    problem = cp.Problem(
        build.prob.objective,
        [*build.prob.constraints, *constraints],
    )
    status, solve_seconds = _solve_problem(problem)
    return {
        "case": "case9",
        "mode": (
            "multistep_lifted_enforced"
            if lifted and enforce
            else (
                "multistep_lifted_reporting"
                if lifted
                else (
                    "multistep_direct_enforced"
                    if enforce
                    else "multistep_unused_direct_reporting"
                )
            )
        ),
        "T": t_steps,
        "status": status,
        "objective": (None if problem.value is None else float(problem.value)),
        "baseline_build_seconds": baseline_seconds,
        "report_expression_seconds": report_seconds,
        "solve_wall_seconds": solve_seconds,
        "num_scalar_variables": int(problem.size_metrics.num_scalar_variables),
        "num_scalar_equalities": int(problem.size_metrics.num_scalar_eq_constr),
        "num_scalar_inequalities": int(problem.size_metrics.num_scalar_leq_constr),
    }


def audit_branch_statuses() -> dict:
    audit = {}
    for name, factory in CASE_FACTORIES.items():
        values = np.asarray(factory()["branch"])[:, BR_STATUS]
        audit[name] = sorted(np.unique(values).tolist())
    return audit


def fixed_voltage_checks() -> dict:
    """Exercise parallel, transformer, reversal, inactive, and empty cases."""
    branch = np.array(
        [
            [0, 1, 0.01, 0.10, 0.04, 100.0, 0, 0, 1.10, 15.0, 1, -360, 360],
            [0, 1, 0.03, 0.20, 0.08, 90.0, 0, 0, 0.95, -7.0, 1, -360, 360],
            [1, 2, 0.0, 0.0, 0.0, 50.0, 0, 0, 0.0, 0.0, 0, -360, 360],
        ],
        dtype=float,
    )
    primitive = branch_primitive({"branch": branch})
    theta = cp.Variable((3, 1))
    voltage = cp.Variable((3, 1))
    theta.value = np.array([[0.02], [-0.11], [0.07]])
    voltage.value = np.array([[1.03], [0.97], [1.01]])
    expressions = terminal_expressions(theta, voltage, primitive)
    sf, st = independent_terminal_power(
        voltage.value.flatten(), theta.value.flatten(), primitive
    )
    direct_errors = [
        np.max(np.abs(np.asarray(expressions.p_from.value) - sf.real)),
        np.max(np.abs(np.asarray(expressions.q_from.value) - sf.imag)),
        np.max(np.abs(np.asarray(expressions.p_to.value) - st.real)),
        np.max(np.abs(np.asarray(expressions.q_to.value) - st.imag)),
    ]

    reversed_primitive = BranchPrimitive(
        f_bus=primitive.t_bus.copy(),
        t_bus=primitive.f_bus.copy(),
        status=primitive.status.copy(),
        rate_a_mva=primitive.rate_a_mva.copy(),
        yff=primitive.ytt.copy(),
        yft=primitive.ytf.copy(),
        ytf=primitive.yft.copy(),
        ytt=primitive.yff.copy(),
    )
    reversed_expressions = terminal_expressions(theta, voltage, reversed_primitive)
    reversal_errors = [
        np.max(
            np.abs(
                np.asarray(reversed_expressions.p_from.value)
                - np.asarray(expressions.p_to.value)
            )
        ),
        np.max(
            np.abs(
                np.asarray(reversed_expressions.q_from.value)
                - np.asarray(expressions.q_to.value)
            )
        ),
        np.max(
            np.abs(
                np.asarray(reversed_expressions.p_to.value)
                - np.asarray(expressions.p_from.value)
            )
        ),
        np.max(
            np.abs(
                np.asarray(reversed_expressions.q_to.value)
                - np.asarray(expressions.q_from.value)
            )
        ),
    ]

    empty_primitive = branch_primitive({"branch": np.empty((0, 13), dtype=float)})
    empty_expressions = terminal_expressions(
        cp.Variable((1, 1)),
        cp.Variable((1, 1)),
        empty_primitive,
    )
    empty_lifted, empty_equalities = lifted_definition_constraints(
        empty_expressions, empty_primitive
    )
    return {
        "max_direct_vs_complex_error_pu": float(max(direct_errors)),
        "max_reversed_terminal_swap_error_pu": float(max(reversal_errors)),
        "inactive_coefficients_exact_zero": bool(
            primitive.yff[2] == 0
            and primitive.yft[2] == 0
            and primitive.ytf[2] == 0
            and primitive.ytt[2] == 0
        ),
        "empty_expression_shapes": [
            list(empty_expressions.p_from.shape),
            list(empty_expressions.q_from.shape),
            list(empty_expressions.p_to.shape),
            list(empty_expressions.q_to.shape),
        ],
        "empty_lifted_shapes": [
            list(empty_lifted.p_from.shape),
            list(empty_lifted.q_from.shape),
            list(empty_lifted.p_to.shape),
            list(empty_lifted.q_to.shape),
        ],
        "empty_lifted_defining_equalities": len(empty_equalities),
    }


def repeated_structure_comparison(
    case_name: str,
    repetitions: int = 3,
) -> list[dict]:
    """Repeat both constrained structures in alternating order."""
    records = []
    for repetition in range(repetitions):
        if repetition % 2 == 0:
            direct = characterize_case(case_name, enforce=True)
            lifted = characterize_lifted(case_name, enforce=True)
        else:
            lifted = characterize_lifted(case_name, enforce=True)
            direct = characterize_case(case_name, enforce=True)
        direct["repetition"] = repetition
        lifted["repetition"] = repetition
        records.extend([direct, lifted])
    return records


def main() -> None:
    records = {
        "branch_status_audit": audit_branch_statuses(),
        "fixed_voltage_checks": fixed_voltage_checks(),
        "case9": [
            characterize_baseline("case9"),
            characterize_case("case9", enforce=False),
            characterize_lifted("case9", enforce=False),
            characterize_case("case9", enforce=True),
            characterize_lifted("case9", enforce=True),
        ],
        "case57": [
            characterize_baseline("case57"),
            characterize_case("case57", enforce=False),
            characterize_lifted("case57", enforce=False),
            characterize_case("case57", enforce=True),
            characterize_lifted("case57", enforce=True),
        ],
        "case57_repeated_structure": repeated_structure_comparison("case57"),
        "dense_structure": {
            "case9": [
                characterize_case("case9", enforce=True, sparse_pq=False),
                characterize_lifted("case9", enforce=True, sparse_pq=False),
            ],
            "case57": [
                characterize_case("case57", enforce=True, sparse_pq=False),
                characterize_lifted("case57", enforce=True, sparse_pq=False),
            ],
        },
        "case118": [
            characterize_baseline("case118"),
            characterize_case("case118", enforce=False),
            characterize_lifted("case118", enforce=False),
            characterize_lifted("case118", enforce=True),
        ],
        "all_cases_lifted_reporting": [
            characterize_lifted(case_name, enforce=False)
            for case_name in CASE_FACTORIES
        ],
        "all_cases_unused_direct_reporting": [
            characterize_case(case_name, enforce=False) for case_name in CASE_FACTORIES
        ],
        "all_cases_pre_m4_baseline": [
            characterize_baseline(case_name) for case_name in CASE_FACTORIES
        ],
        "all_cases_lifted_enforced": [
            characterize_lifted(case_name, enforce=True) for case_name in CASE_FACTORIES
        ],
        "sparsity": characterize_sparsity("case57", (0.0, 1e-12, 1e-6, 1e-3, 0.1, 1.0)),
        "multistep": [
            characterize_multistep(lifted=True, enforce=False),
            characterize_multistep(lifted=False),
            characterize_multistep(lifted=True),
        ],
    }
    RESULTS_PATH.write_text(json.dumps(records, indent=2) + "\n")
    print(json.dumps(records, indent=2))
    print(f"\nWrote {RESULTS_PATH}")


if __name__ == "__main__":
    main()
