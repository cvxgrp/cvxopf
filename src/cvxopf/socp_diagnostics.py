"""Explicit numerical audits and forest voltage recovery for SOCP results.

These operations never solve, repair, or change a build. Physics is evaluated
from numeric primal values and the original admittances, not the builder's
affine maps or CVXPY constraint residuals. Diagnostic arrays are time-first,
including a length-one time axis for a single-step build. A feasible relaxation
or a recoverable product matrix is not a controller acceptance decision.
"""

from dataclasses import asdict, dataclass

import numpy as np

from cvxopf.generator import voltage_setpoints
from cvxopf.network import make_branch_admittance, make_ybus_matpower, VMIN, VMAX
from cvxopf.results import extract_results


@dataclass(frozen=True)
class SOCPAuditTolerances:
    """Absolute audit thresholds, distinct from exact-recovery thresholds.

    Power includes P/Q balance and apparent-power capability (MW/MVAr/MVA);
    energy is MWh, squared voltage and cone violation are per-unit squared.
    Rank tightness uses normalized determinant gaps; product error is p.u.^2.
    No dual certificate or global-optimality tolerance is implied.
    """

    power: float = 1e-4
    energy: float = 1e-5
    voltage_squared: float = 1e-6
    cone: float = 1e-6
    fraction: float = 1e-7
    rank_relative: float = 1e-5
    product: float = 1e-5
    cycle_angle: float = 1e-5
    phase_floor: float = 1e-12
    normalization_floor: float = 1e-12

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and strictly positive")


def _inputs(build, results):
    if build.formulation != "socp":
        raise ValueError("SOCP diagnostics require formulation='socp'")
    return build.data["_socp_audit_inputs"], extract_results(build) if results is None else results


def _value(build, results, key, count):
    value = results.get(key)
    shape = (build.data["T"], count) if "T" in build.data else (count,)
    if value is None:
        raise ValueError(f"missing primal value: {key}")
    array = np.asarray(value, dtype=float)
    if array.shape != shape or not np.all(np.isfinite(array)):
        raise ValueError(f"invalid shape or nonfinite primal value: {key}; expected {shape}")
    return array.reshape(-1, count) if count else np.empty((build.data.get("T", 1), 0))


def _summary(values, units, tolerance, identities=None):
    """Nonnegative violations with an explicit interval/identity worst location."""
    values = np.maximum(np.asarray(values, dtype=float), 0)
    if values.ndim == 1:
        values = values[None, :]
    worst = None
    maximum = 0.0
    if values.size:
        t, k = np.unravel_index(np.argmax(values), values.shape)
        maximum = float(values[t, k])
        worst = {"interval": int(t), "index": int(k),
                 "identity": int(k) if identities is None else identities[k]}
    return {"maximum": maximum, "by_interval": np.max(values, axis=1, initial=0),
            "worst": worst, "units": units, "tolerance": tolerance,
            "passed": None if tolerance is None else bool(np.all(np.isfinite(values)) and maximum <= tolerance)}


def _network_power(case, pairs, w, products, voltage=None):
    """Reconstruct numeric physics, deliberately independent of PowerMap."""
    adm = make_branch_admittance(case)
    ybus = make_ybus_matpower(case, branch_admittance=adm)
    if voltage is not None:
        nodal = voltage * np.conj(voltage @ ybus.T)
        vf, vt = voltage[:, adm.from_bus], voltage[:, adm.to_bus]
        sf = vf * np.conj(adm.yff * vf + adm.yft * vt)
        st = vt * np.conj(adm.ytf * vf + adm.ytt * vt)
    else:
        lookup = {tuple(pair): k for k, pair in enumerate(pairs)}

        def product(i, j):
            if i == j:
                return w[:, i]
            z = products[:, lookup[min(i, j), max(i, j)]]
            return z if i < j else z.conj()

        nodal = np.zeros(w.shape, dtype=complex)
        for i, j in zip(*np.nonzero(ybus), strict=True):
            nodal[:, i] += np.conj(ybus[i, j]) * product(i, j)
        sf = np.zeros((len(w), len(adm.from_bus)), dtype=complex)
        st = np.zeros_like(sf)
        for k in np.flatnonzero(adm.status):
            i, j = adm.from_bus[k], adm.to_bus[k]
            z = product(i, j)
            sf[:, k] = np.conj(adm.yff[k]) * w[:, i] + np.conj(adm.yft[k]) * z
            st[:, k] = np.conj(adm.ytt[k]) * w[:, j] + np.conj(adm.ytf[k]) * z.conj()
    base = case["baseMVA"]
    return nodal * base, sf * base, st * base, adm


def _device_audit(build, results, inputs, tol):
    """Numeric device checks; no CVXPY objects or modeled residual reads."""
    a, units = inputs["arrays"], inputs["units"]
    nb, T = build.data["nb"], build.data.get("T", 1)
    residuals = {}
    injection = np.zeros((T, nb), dtype=complex)

    def get(key, count):
        return _value(build, results, key, count)

    def check(key, violation, unit="MW", threshold=tol.power):
        residuals[key] = _summary(violation, unit, threshold)

    def box(key, value, lower, upper, unit="MW", threshold=tol.power):
        check(key, np.maximum(lower - value, value - upper), unit, threshold)

    generators = units.get("generator", ())
    pg, qg = get("Pg", len(generators)), get("Qg", len(generators))
    base = inputs["case"]["baseMVA"]
    box("generator_p", pg, a["Pgmin"] * base, a["Pgmax"] * base)
    box("generator_q", qg, a["Qgmin"] * base, a["Qgmax"] * base, "MVAr")
    injection += (pg + 1j * qg) @ a["Cg"].T

    p, q = a["_load_p_mw_by_step"].copy(), a["_load_q_mvar_by_step"].copy()
    indices = a["sheddable_load_indices"]
    if len(indices):
        fraction = get("load_shed_fraction", len(indices))
        eligible = p[:, indices] > 0
        box("shedding_fraction", fraction, 0, a["load_max_shed_fraction"][indices] * eligible,
            "fraction", tol.fraction)
        p[:, indices] -= np.maximum(p[:, indices], 0) * fraction
        q[:, indices] -= q[:, indices] * eligible * fraction
    injection -= (p + 1j * q) @ a["Cload"].T

    storage = units.get("storage", ())
    if storage:
        b, bq, soc = (get(key, len(storage)) for key in ("b", "b_q", "soc"))
        check("storage_capability", np.hypot(b, bq) - a["storage_apparent_power_rating"], "MVA")
        box("storage_energy", soc, 0, a["storage_capacity"], "MWh", tol.energy)
        previous = np.vstack([a["storage_initial_soc"], soc[:-1]])
        check("storage_transition", np.abs(soc - previous + build.data["delta"] * b), "MWh", tol.energy)
        target_violation = np.zeros((1, len(storage)))
        for k, unit in enumerate(storage):
            if unit.terminal_constraint == "equality":
                target_violation[0, k] = abs(soc[-1, k] - unit.terminal_soc)
            elif unit.terminal_constraint == "shortfall":
                target_violation[0, k] = max(unit.terminal_soc - soc[-1, k], 0)
        check("storage_terminal", target_violation, "MWh", tol.energy)
        residuals["storage_terminal"]["worst"]["interval"] = T - 1
        injection += (b + 1j * bq) @ a["Cs"].T

    nd = units.get("nondispatchable", ())
    if nd:
        pnd, qnd = get("p_nd", len(nd)), get("q_nd", len(nd))
        box("nondispatchable_p", pnd, 0, a["nd_available_mw"])
        check("nondispatchable_capability", np.hypot(pnd, qnd) - a["nd_apparent_power_rating"], "MVA")
        injection += (pnd + 1j * qnd) @ a["Cnd"].T

    links = units.get("hvdc", ())
    if links:
        pin, pout = get("p_hvdc_in", len(links)), get("p_hvdc_out", len(links))
        low, high = a["hvdc_p_min_mw"], a["hvdc_p_max_mw"]
        box("hvdc_box", pin, low, high)
        # Independent arithmetic, including the existing lossless fallback for
        # boxes straddling zero; diagnostics must not emit another warning.
        loss = np.array([link.loss_percent / 100 for link in links])
        coefficient = np.where(low >= 0, -1 / (1 - loss), np.where(high <= 0, -(1 - loss), -1))
        check("hvdc_coupling", np.abs(pout - coefficient * pin))
        injection += pin @ a["Ch_from"].T + pout @ a["Ch_to"].T
    return injection, residuals


def _audit(build, results, inputs, tol, voltage=None):
    report = {"solver_status": results.get("status"), "available": False,
              "feasible": None, "complete": not inputs["has_extra_coupling"],
              "tolerances": asdict(tol), "residuals": {}, "edge_gaps": None,
              "reason": None}
    stats = build.prob.solver_stats
    report["solver_statistics"] = None if stats is None else {
        "solver_name": stats.solver_name, "solve_time": stats.solve_time,
        "num_iters": stats.num_iters, "extra_stats": stats.extra_stats,
    }
    if results.get("status") in {"infeasible", "infeasible_inaccurate", "unbounded", "unbounded_inaccurate"}:
        report["reason"] = "No usable primal point for this solver termination."
        return report
    pairs = np.asarray(build.data["voltage_product_pairs"])
    try:
        w = _value(build, results, "w", build.data["nb"])
        z = _value(build, results, "W_re", len(pairs)) + 1j * _value(build, results, "W_im", len(pairs))
        injection, residuals = _device_audit(build, results, inputs, tol)
    except (ValueError, TypeError) as error:
        report["reason"] = str(error)
        return report
    nodal, sf, st, adm = _network_power(inputs["case"], pairs, w, z, voltage)
    residuals["p_balance"] = _summary(abs(nodal.real - injection.real), "MW", tol.power)
    residuals["q_balance"] = _summary(abs(nodal.imag - injection.imag), "MVAr", tol.power)
    bus = inputs["case"]["bus"]
    residuals["voltage_bounds"] = _summary(
        np.maximum(bus[:, VMIN]**2 - w, w - bus[:, VMAX]**2), "p.u.^2", tol.voltage_squared)
    if build.data["enforce_vset"]:
        mapping = build.data["ext_to_int"] or {i: i for i in range(len(bus))}
        selected = voltage_setpoints(inputs["units"]["generator"], mapping,
                                     (build.data["ref"], *build.data["pv"]))
        errors = np.column_stack([abs(w[:, i] - value**2) for i, value in selected.items()]) if selected else np.empty((len(w), 0))
        residuals["voltage_setpoints"] = _summary(errors, "p.u.^2", tol.voltage_squared, list(selected))
    if build.data["enforce_branch_limits"]:
        rows = np.flatnonzero(adm.status & np.isfinite(adm.rate_a_mva) & (adm.rate_a_mva > 0))
        for side, power in (("from", sf), ("to", st)):
            residuals[f"branch_{side}"] = _summary(abs(power[:, rows]) - adm.rate_a_mva[rows], "MVA", tol.power, rows.tolist())
    if voltage is None:
        i, j = pairs.T
        product = w[:, i] * w[:, j]
        gap = product - abs(z)**2
        denominator = np.maximum(tol.normalization_floor, np.maximum(abs(product), abs(z)**2))
        normalized = gap / denominator
        cone_violation = np.sqrt((w[:, i] - w[:, j])**2 + 4 * abs(z)**2) - w[:, i] - w[:, j]
        residuals["edge_cones"] = _summary(cone_violation, "p.u.^2", tol.cone, pairs.tolist())
        report["edge_gaps"] = {
            "signed": gap, "absolute": abs(gap), "normalized_signed": normalized,
            "normalization_floor": tol.normalization_floor, "units": "p.u.^4",
            "absolute_summary": _summary(abs(gap), "p.u.^4", None, pairs.tolist()),
            "relative_summary": _summary(abs(normalized), "dimensionless", tol.rank_relative, pairs.tolist()),
        }
    passed = all(item["passed"] for item in residuals.values())
    report.update(available=True, residuals=residuals,
                  feasible=passed if report["complete"] else (False if not passed else None),
                  checked_constraints_feasible=passed,
                  reason=None if report["complete"] else "Caller-supplied coupling constraints have no independent numeric audit.",
                  nodal_power_mva=nodal, branch_from_mva=sf, branch_to_mva=st)
    return report


def audit_socp_relaxation(build, results=None, *, tolerances=None):
    """Audit a SOCP primal point independently; no lower-bound certificate.

    ``results`` may be an extracted or explicitly supplied public-shape primal
    dictionary. Missing/nonfinite values return ``available=False``. Solver
    status is reported separately; even ``optimal_inaccurate`` is numerically
    audited. Extra caller coupling makes full feasibility unavailable.
    """
    inputs, results = _inputs(build, results)
    return _audit(build, results, inputs, tolerances or SOCPAuditTolerances())


def recover_socp_voltage(build, results=None, *, tolerances=None):
    """Recover a deterministic forest candidate and independently audit AC.

    Returns public-shape candidate voltages and the unchanged public-shape device
    primal in ``candidate``. Exact product recovery and AC feasibility are
    separate outcomes. There is no power-flow adjustment or optimization.
    """
    inputs, results = _inputs(build, results)
    tol = tolerances or SOCPAuditTolerances()
    audit = _audit(build, results, inputs, tol)
    report = {"solver_status": results.get("status"), "available": False,
              "exact_product_recovery": None, "ac_feasible": None,
              "candidate": None, "ac_audit": None, "tolerances": asdict(tol),
              "reason": audit["reason"]}
    if not audit["available"]:
        return report
    w = _value(build, results, "w", build.data["nb"])
    pairs = np.asarray(build.data["voltage_product_pairs"])
    z = _value(build, results, "W_re", len(pairs)) + 1j * _value(build, results, "W_im", len(pairs))
    if np.any(w < 0) or np.any(abs(z) <= tol.phase_floor):
        report["reason"] = "Negative squared voltage or undefined near-zero edge phase; no voltage fabricated."
        return report
    adjacency = [[] for _ in range(w.shape[1])]
    for edge, (i, j) in enumerate(pairs):
        adjacency[i].append((j, edge, 1))
        adjacency[j].append((i, edge, -1))
    theta = np.zeros_like(w)
    seen, tree, roots = set(), set(), []
    for root in [build.data["ref"], *range(w.shape[1])]:
        if root in seen:
            continue
        roots.append(int(root))
        seen.add(root)
        queue = [root]
        for i in queue:
            for j, edge, sign in adjacency[i]:
                if j not in seen:
                    theta[:, j] = theta[:, i] - sign * np.angle(z[:, edge])
                    seen.add(j)
                    tree.add(edge)
                    queue.append(j)
    i, j = pairs.T
    angle_error = np.angle(np.exp(1j * (theta[:, i] - theta[:, j] - np.angle(z))))
    non_tree = np.array([k for k in range(len(pairs)) if k not in tree], dtype=int)
    voltage = np.sqrt(w) * np.exp(1j * theta)
    product_error = abs(voltage[:, i] * voltage[:, j].conj() - z)
    cycle = _summary(abs(angle_error[:, non_tree]), "radians", tol.cycle_angle, pairs[non_tree].tolist())
    products = _summary(product_error, "p.u.^2", tol.product, pairs.tolist())
    tight = audit["edge_gaps"]["relative_summary"]["passed"]
    ac = _audit(build, results, inputs, tol, voltage)
    device_keys = ("Pg", "Qg", "b", "b_q", "soc", "p_nd", "q_nd", "p_hvdc_in", "p_hvdc_out", "load_shed_fraction")
    candidate = {key: np.asarray(results[key]).copy() for key in device_keys if results.get(key) is not None}
    # Voltage shape follows the public single/multistep convention as well.
    candidate.update(Vm=np.sqrt(w) if "T" in build.data else np.sqrt(w)[0],
                     Va_deg=np.rad2deg(theta) if "T" in build.data else np.rad2deg(theta)[0])
    report.update(available=True, candidate=candidate, ac_audit=ac, ac_feasible=ac["feasible"],
                  exact_product_recovery=bool(tight and cycle["passed"] and products["passed"]),
                  edge_tight=bool(tight), roots=roots, tree_edges=sorted(tree),
                  non_tree_edges=non_tree, cycle_angles=angle_error[:, non_tree],
                  cycle_summary=cycle, product_errors=product_error, product_summary=products,
                  reason="Forest candidate only; no dispatch repair or control-action acceptance.")
    return report
