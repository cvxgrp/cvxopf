"""Original-unit acceptance and paired conclusions; no optimizer calls."""

import math

from .contract import (
    ANGLE_TOL_DEG, MW_TOL, V_TOL, audit_link, generator_cost, require, selected_links,
)


def cost_agrees(value, expected):
    return (isinstance(value, (int, float)) and math.isfinite(value)
            and abs(value - expected) <= 1e-4 + 1e-6 * abs(expected))


def assess(case, raw, evaluations, *, supervised=True):
    """Require both actual model evaluations, independent cost, and source success."""
    point = raw["point"]
    if point is None:
        failures = ["no usable primal"]
        if not supervised:
            failures.append("supervisor exit and resource status are unknown")
        return {"accepted": False, "classification": "no_primal" if supervised else "supervisor_lost",
                "failures": failures}
    require(set(evaluations) == {"cvxopf", "pypower"}, "two model evaluations are required")
    costs = [generator_cost(row, p) for row, p in zip(case["gencost"], point["pg_mw"], strict=True)]
    common_cost = sum(costs)
    failures = []
    if not supervised:
        failures.append("supervisor exit and resource status are unknown")
    if not raw["native_success"]:
        failures.append("native solver did not converge")
    if not cost_agrees(raw["objective"], common_cost):
        failures.append("native objective differs from the independent engineering-unit cost")
    if raw["solver"] == "cvxopf" and raw["native_success"]:
        if not cost_agrees(raw["api_objective"], common_cost):
            failures.append("CVXPY objective differs from independent cost")
    require(raw["native_checks"], "missing actual native-primal feasibility checks")
    for family, row in raw["native_checks"].items():
        value = row["signed"]
        if not math.isfinite(value) or abs(value) > row["tolerance"]:
            failures.append(f"native {family} {row['id']}: {value:.8g} {row['units']}")
    for side, evaluated in evaluations.items():
        if not cost_agrees(evaluated["objective"], common_cost):
            failures.append(f"{side} objective differs from independent cost")
        worst = evaluated["worst"]
        required = {"real_balance", "reactive_balance", "voltage_box", "angle_box", "reference_angle",
                    "generator_p_box", "generator_q_box", "capability"}
        if point["links"]:
            required |= {"link_from_box", "link_proportional" if side == "cvxopf" else "link_coupling"}
        if side == "pypower":
            required.add("thermal_box")
        require(set(worst) == required, f"{side} missing or unexpected original-unit audit families")
        for family, entry in worst.items():
            signed, units = entry["signed"], entry["units"]
            tol = {"degrees": ANGLE_TOL_DEG, "p.u.": V_TOL, "MW": MW_TOL, "MVAr": MW_TOL,
                   "MVA": MW_TOL, "MW/MVAr projection": MW_TOL}[units]
            if not math.isfinite(signed) or abs(signed) > tol:
                failures.append(f"{side} {family} {entry['id']}: {signed:.8g} {units}")
    for (row, dc), link in zip(selected_links(case).items(), point["links"], strict=True):
        try:
            audit_link(row, dc, link)
        except ValueError as exc:
            failures.append(f"link source row {row}: {exc}")
    if raw["solver"] == "pypower":
        q = raw["dummy_q_mvar"]
        require(len(q) == 2 * len(point["links"]), "missing converter dummy Q")
        for terminal, value in enumerate(q):
            if abs(value) > MW_TOL:
                failures.append(f"PYPOWER dummy terminal {terminal} Q: {value:.8g} MVAr")
    return {
        "accepted": not failures,
        "classification": ("supervisor_lost" if not supervised else "nonconverged"
                           if not raw["native_success"] else "rejected" if failures else "accepted"),
        "failures": failures, "common_objective": common_cost,
        "generator_costs": costs, "evaluations": evaluations,
    }


def pair_summary(arms, threshold):
    if set(arms) != {"cvxopf", "pypower"} or not all(a["audit"]["accepted"] for a in arms.values()):
        return {"verdict": "not_comparable", "reason": "both native returns must pass both model audits"}
    cvx, pyp = (arms[key] for key in ("cvxopf", "pypower"))
    delta = cvx["audit"]["common_objective"] - pyp["audit"]["common_objective"]
    a, b = cvx["raw"]["point"], pyp["raw"]["point"]
    return {
        "verdict": ("costs_agree_within_threshold" if abs(delta) <= threshold
                    else "cvxopf_lower_observed_feasible_cost" if delta < 0
                    else "pypower_lower_observed_feasible_cost"),
        "cvxopf_minus_pypower_cost": delta, "material_cost_threshold": threshold,
        "max_abs_pg_difference_mw": max(abs(x-y) for x, y in zip(a["pg_mw"], b["pg_mw"], strict=True)),
        "max_abs_qg_difference_mvar": max(abs(x-y) for x, y in zip(a["qg_mvar"], b["qg_mvar"], strict=True)),
        "links": {side: arm["raw"]["point"]["links"] for side, arm in arms.items()},
        "total_converter_loss_mw": {
            side: sum(link["physical_loss_mw"] for link in arm["raw"]["point"]["links"])
            for side, arm in arms.items()
        },
        "interpretation": "Two observed dispatches, not global optima or an isolated solver-speed comparison.",
    }
