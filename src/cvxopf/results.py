"""
Result extraction and comparison utilities.

Operates on OPFBuild objects after prob.solve() has been called.
"""

from __future__ import annotations

import json
import numpy as np

from cvxopf.problem import OPFBuild


def extract_results(build: OPFBuild) -> dict:
    """
    Extract and scale solver results from a solved OPFBuild.

    Detects single-step vs multi-step builds by inspecting whether
    build.variables['Pg'] is a list or a single variable.

    Parameters
    ----------
    build : OPFBuild
        A solved OPFBuild (build.prob.solve() has been called).

    Returns
    -------
    results : dict
        Single-step keys:
            status      str       CVXPY solve status
            objective   float     Optimal cost ($/hr)
            Pg          ndarray   (ng,)  Generator real output, MW
            Qg          ndarray   (ng,)  Generator reactive output, MVAr
            Vm          ndarray   (nb,)  Bus voltage magnitudes, p.u.
            Va_deg      ndarray   (nb,)  Bus voltage angles, degrees
            p_net       ndarray   (nb,)  Net real bus injection, MW
            q_net       ndarray   (nb,)  Net reactive bus injection, MVAr

        Multi-step: same keys; Pg, Qg are (T, ng) and Vm, Va_deg, p_net,
        q_net are (T, nb). objective is total cost across all steps.
    """
    var     = build.variables
    data    = build.data
    baseMVA = float(data["baseMVA"])
    prob    = build.prob

    multistep = isinstance(var["Pg"], list)

    if not multistep:
        Pg_val    = var["Pg"].value
        Qg_val    = var["Qg"].value
        v_val     = var["v"].value.flatten()
        theta_val = var["theta"].value.flatten()
        p_val     = var["p"].value
        q_val     = var["q"].value

        return dict(
            status    = prob.status,
            objective = float(prob.value),
            Pg        = Pg_val * baseMVA,
            Qg        = Qg_val * baseMVA,
            Vm        = v_val,
            Va_deg    = np.rad2deg(theta_val),
            p_net     = p_val * baseMVA,
            q_net     = q_val * baseMVA,
        )
    else:
        T = data["T"]
        Pg_rows    = []
        Qg_rows    = []
        Vm_rows    = []
        Va_rows    = []
        p_rows     = []
        q_rows     = []

        for t in range(T):
            Pg_rows.append(var["Pg"][t].value)
            Qg_rows.append(var["Qg"][t].value)
            Vm_rows.append(var["v"][t].value.flatten())
            Va_rows.append(var["theta"][t].value.flatten())
            p_rows.append(var["p"][t].value)
            q_rows.append(var["q"][t].value)

        return dict(
            status    = prob.status,
            objective = float(prob.value),
            Pg        = np.array(Pg_rows) * baseMVA,
            Qg        = np.array(Qg_rows) * baseMVA,
            Vm        = np.array(Vm_rows),
            Va_deg    = np.rad2deg(np.array(Va_rows)),
            p_net     = np.array(p_rows) * baseMVA,
            q_net     = np.array(q_rows) * baseMVA,
        )


def compare_to_reference(results: dict, reference: dict) -> dict:
    """
    Compute structured differences between cvxopf results and a pypower
    reference fixture dict.

    Parameters
    ----------
    results : dict
        Output of extract_results() for a single-step solve.
    reference : dict
        Loaded from a fixture JSON file produced by
        scripts/generate_pypower_fixtures.py. Expected keys match results.

    Returns
    -------
    comparison : dict
        For each comparable field, a sub-dict with:
            cvxopf      ndarray or float   cvxopf value
            reference   ndarray or float   pypower reference value
            abs_diff    ndarray or float   absolute difference
            rel_diff    ndarray or float   relative difference (where meaningful)
    """
    fields = ["objective", "Pg", "Qg", "Vm", "Va_deg"]
    comparison = {}

    for f in fields:
        if f not in results or f not in reference:
            continue
        cv  = np.asarray(results[f],   dtype=float)
        ref = np.asarray(reference[f], dtype=float)

        abs_diff = np.abs(cv - ref)
        denom    = np.where(np.abs(ref) > 1e-8, np.abs(ref), 1.0)
        rel_diff = abs_diff / denom

        comparison[f] = dict(
            cvxopf    = cv,
            reference = ref,
            abs_diff  = abs_diff,
            rel_diff  = rel_diff,
        )

    return comparison
