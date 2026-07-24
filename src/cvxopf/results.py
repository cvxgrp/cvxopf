"""
Result extraction and comparison utilities.

Operates on OPFBuild objects after prob.solve() has been called.
Dispatches on build.formulation to return the appropriate result schema.

AC results keys:
    status, objective, Pg, Qg, Vm, Va_deg, p_net, q_net

DC (lossy_dc) results keys:
    status, objective, Pg, p_flows, p_net
    (Vm, Va_deg, Qg, q_net are absent — not modelled in DC formulation)

HVDC results keys (AC and lossy_dc, present when "n_hvdc" in build.data):
    p_hvdc_in, p_hvdc_out (signed nodal injections, MW), hvdc_loss (derived,
    = p_hvdc_in + p_hvdc_out, >= 0). Shapes (n_hvdc,) single / (T, n_hvdc)
    multi. Absent from singlenode_dc results (HVDC silently ignored there).

Singlenode DC (singlenode_dc) results keys:
    status, objective, Pg, p_net
    (p_flows, Vm, Va_deg, Qg, q_net absent — not modelled)
    (b, soc, storage_cost present when storage is not None)
    (p_nd, curtailment present when nondispatchable is not None)
"""

from __future__ import annotations

import numpy as np

from cvxopf.hvdc import _loss_values
from cvxopf.nondispatchable import _curtailment_values
from cvxopf.problem import OPFBuild


def _solved_expression_value(build: OPFBuild, name: str) -> float:
    """Return a scalar value from the exact expression used by the model."""
    value = build.expressions[name].value
    return float(value) if value is not None else float("nan")


def _solved_expression_values(build: OPFBuild, name: str):
    """Evaluate a named single- or multi-step modeled expression."""
    expression = build.expressions[name]
    if isinstance(expression, list):
        values = [item.value for item in expression]
        return None if any(value is None for value in values) else np.array(values)
    return expression.value


def _empty_results(build: OPFBuild, *fields: str) -> dict:
    """Return the common result shape when no primal solution is available."""
    return {
        "status": build.prob.status,
        "objective": float("nan"),
        **{field: None for field in fields},
    }


def _variable_values(variable):
    """Return one variable value or stack a multistep variable list."""
    if isinstance(variable, list):
        return np.array([item.value for item in variable])
    return variable.value


def _add_storage_results(results: dict, build: OPFBuild) -> None:
    """Add storage-owned variables and modeled cost to a result dictionary."""
    if "ns" not in build.data:
        return
    results["b"] = _variable_values(build.variables["b"])
    if "b_q" in build.variables:
        results["b_q"] = _variable_values(build.variables["b_q"])
    results["soc"] = _variable_values(build.variables["soc"])
    results["storage_cost"] = _solved_expression_value(build, "storage_cost")


def _add_nd_results(results: dict, build: OPFBuild) -> None:
    """Add ND variables and device-owned curtailment values."""
    if "nnd" not in build.data:
        return
    results["p_nd"] = _variable_values(build.variables["p_nd"])
    if "q_nd" in build.variables:
        results["q_nd"] = _variable_values(build.variables["q_nd"])
    availability_key = (
        "nd_available" if "T" in build.data else "nd_p_available"
    )
    results["curtailment"] = _curtailment_values(
        build.data[availability_key], results["p_nd"]
    )


def _add_hvdc_results(results: dict, build: OPFBuild) -> None:
    """Add HVDC terminal injections and device-owned loss values."""
    if "n_hvdc" not in build.data:
        return
    results["p_hvdc_in"] = _variable_values(build.variables["p_hvdc_in"])
    results["p_hvdc_out"] = _variable_values(build.variables["p_hvdc_out"])
    results["hvdc_loss"] = _loss_values(
        results["p_hvdc_in"], results["p_hvdc_out"]
    )


def _add_device_results(results: dict, build: OPFBuild) -> None:
    """Add every optional device's reported values."""
    _add_storage_results(results, build)
    _add_nd_results(results, build)
    _add_hvdc_results(results, build)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def extract_results(build: OPFBuild) -> dict:
    """
    Extract and scale solver results from a solved OPFBuild.

    Dispatches on build.formulation. For multi-step builds, detects by
    inspecting whether variables contain lists.

    Parameters
    ----------
    build : OPFBuild
        A solved OPFBuild (build.solve() has been called).

    Returns
    -------
    results : dict
        AC single-step keys:
            status      str          CVXPY solve status
            objective   float        Optimal cost ($/hr)
            Pg          np.ndarray   (ng,)  Generator real output, MW
            Qg          np.ndarray   (ng,)  Generator reactive output, MVAr
            Vm          np.ndarray   (nb,)  Bus voltage magnitudes, p.u.
            Va_deg      np.ndarray   (nb,)  Bus voltage angles, degrees
            p_net       np.ndarray   (nb,)  Net real bus injection, MW
            q_net       np.ndarray   (nb,)  Net reactive bus injection, MVAr

        AC multi-step: same keys; Pg, Qg are (T, ng); Vm, Va_deg, p_net,
        q_net are (T, nb). objective is total cost across all steps.

        DC single-step keys:
            status      str          CVXPY solve status
            objective   float        Optimal cost ($/hr)
            Pg          np.ndarray   (ng,)  Per-generator output, MW
                                            stored per generator as Pg
            p_flows     np.ndarray   (nl,)  Branch real power flows, MW
            p_net       np.ndarray   (nb,)  Net real bus injection, MW

        DC multi-step: Pg is (T, ng); p_flows is (T, nl); p_net is (T, nb).

        Note: Vm, Va_deg, Qg, and q_net are absent from DC results.
        Code consuming results from either formulation should use
        results.get('Vm') rather than results['Vm'].

        Singlenode DC single-step keys:
            status      str          CVXPY solve status
            objective   float        Optimal cost ($/hr)
            Pg          np.ndarray   (ng,)  Per-generator output, MW
            p_net       float        Net generation minus load, MW
                                     (near zero at optimum)

        Singlenode DC multi-step: Pg is (T, ng); p_net is (T,).

    Raises
    ------
    ValueError
        If build.formulation is not one of 'ac', 'lossy_dc',
        'singlenode_dc'.
    """
    if build.formulation == "ac":
        return _extract_ac_results(build)
    elif build.formulation == "lossy_dc":
        return _extract_dc_results(build)
    elif build.formulation == "singlenode_dc":
        return _extract_singlenode_dc_results(build)
    else:
        raise ValueError(
            f"extract_results: unknown formulation '{build.formulation}'. "
            f"Supported: 'ac', 'lossy_dc', 'singlenode_dc'."
        )


def compare_to_reference(results: dict, reference: dict) -> dict:
    """
    Compute structured differences between cvxopf results and a reference
    fixture dict (typically from Pypower).

    Only fields present in both dicts are compared. Fields absent from
    either are silently skipped, so this function works for both AC and
    DC result dicts.

    Parameters
    ----------
    results : dict
        Output of extract_results() for a single-step solve.
    reference : dict
        Reference dict. For AC, loaded from a Pypower fixture JSON file.
        Expected keys: objective, Pg, Qg, Vm, Va_deg (AC) or
        objective, Pg, p_flows, p_net (DC).

    Returns
    -------
    comparison : dict
        For each comparable field, a sub-dict with:
            cvxopf      np.ndarray or float   cvxopf value
            reference   np.ndarray or float   reference value
            abs_diff    np.ndarray or float   |cvxopf - reference|
            rel_diff    np.ndarray or float   abs_diff / max(|reference|, 1e-8)
    """
    fields = ["objective", "Pg", "Qg", "Vm", "Va_deg", "p_flows", "p_net"]
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_ac_results(build: OPFBuild) -> dict:
    """Extract results for AC formulation (single-step or multi-step)."""
    var     = build.variables
    data    = build.data
    baseMVA = float(data["baseMVA"])
    prob    = build.prob

    multistep = "T" in data

    if not multistep:
        if any(
            variable.value is None
            for variable in (
                var["Pg"], var["Qg"], var["v"], var["theta"], var["p"], var["q"]
            )
        ):
            return _empty_results(
                build, "Pg", "Qg", "Vm", "Va_deg", "p_net", "q_net"
            )

        results = dict(
            status    = prob.status,
            objective = float(prob.value),
            Pg        = var["Pg"].value * baseMVA,
            Qg        = var["Qg"].value * baseMVA,
            Vm        = var["v"].value.flatten(),
            Va_deg    = np.rad2deg(var["theta"].value.flatten()),
            p_net     = _solved_expression_values(build, "p_net") * baseMVA,
            q_net     = _solved_expression_values(build, "q_net") * baseMVA,
        )
        
        _add_device_results(results, build)
        return results

    T       = data["T"]
    Pg_rows = []
    Qg_rows = []
    Vm_rows = []
    Va_rows = []
    for t in range(T):
        if any(
            var[name][t].value is None
            for name in ("Pg", "Qg", "v", "theta", "p", "q")
        ):
            return _empty_results(
                build, "Pg", "Qg", "Vm", "Va_deg", "p_net", "q_net"
            )
        Pg_rows.append(var["Pg"][t].value)
        Qg_rows.append(var["Qg"][t].value)
        Vm_rows.append(var["v"][t].value.flatten())
        Va_rows.append(var["theta"][t].value.flatten())
    results = dict(
        status    = prob.status,
        objective = float(prob.value),
        Pg        = np.array(Pg_rows) * baseMVA,
        Qg        = np.array(Qg_rows) * baseMVA,
        Vm        = np.array(Vm_rows),
        Va_deg    = np.rad2deg(np.array(Va_rows)),
        p_net     = _solved_expression_values(build, "p_net") * baseMVA,
        q_net     = _solved_expression_values(build, "q_net") * baseMVA,
    )
    
    _add_device_results(results, build)
    return results


def _extract_dc_results(build: OPFBuild) -> dict:
    """
    Extract results for lossy DC formulation (single-step or multi-step).

    Pg is stored directly as a per-generator (ng,) variable. Nodal net
    injection is evaluated from the exact expression used in power balance.
    """
    var     = build.variables
    data    = build.data
    baseMVA = float(data["baseMVA"])
    prob    = build.prob
    multistep = "T" in data

    if not multistep:
        Pg_val      = var["Pg"].value
        p_flows_val = var["p_flows"].value
        # Guard: solver may return None values if problem is infeasible
        if Pg_val is None or p_flows_val is None:
            return _empty_results(build, "Pg", "p_flows", "p_net")

        results = dict(
            status    = prob.status,
            objective = float(prob.value),
            Pg        = Pg_val * baseMVA,
            p_flows   = p_flows_val * baseMVA,
            p_net     = _solved_expression_values(build, "p_net") * baseMVA,
        )
        
        _add_device_results(results, build)
        return results

    T            = data["T"]
    Pg_rows      = []
    p_flows_rows = []
    for t in range(T):
        Pg_t      = var["Pg"][t].value
        p_flows_t = var["p_flows"][t].value
        if Pg_t is None or p_flows_t is None:
            return _empty_results(build, "Pg", "p_flows", "p_net")
        Pg_rows.append(Pg_t)
        p_flows_rows.append(p_flows_t)
    results = dict(
        status    = prob.status,
        objective = float(prob.value),
        Pg        = np.array(Pg_rows) * baseMVA,
        p_flows   = np.array(p_flows_rows) * baseMVA,
        p_net     = _solved_expression_values(build, "p_net") * baseMVA,
    )
    
    _add_device_results(results, build)
    return results


def _extract_singlenode_dc_results(build: OPFBuild) -> dict:
    """
    Extract results for single-node DC formulation (single-step or multi-step).

    For single-node DC, Pg is (ng,) in single-step or (T, ng) in multi-step.
    p_net is a scalar float in single-step or (T,) array in multi-step.
    """
    var     = build.variables
    data    = build.data
    baseMVA = float(data["baseMVA"])
    prob    = build.prob

    multistep = "T" in data

    if not multistep:
        # Single-step extraction
        Pg_val = var["Pg"].value

        # Guard: solver may return None values if problem is infeasible
        if Pg_val is None:
            return _empty_results(build, "Pg", "p_net")

        results = dict(
            status    = prob.status,
            objective = float(prob.value),
            Pg        = Pg_val * baseMVA,          # (ng,) MW
            p_net     = float(
                _solved_expression_values(build, "p_net") * baseMVA
            ),
        )

        _add_device_results(results, build)
        return results

    # Multi-step extraction
    T = data["T"]
    Pg_rows = []
    for t in range(T):
        Pg_val = var["Pg"][t].value
        if Pg_val is None:
            return _empty_results(build, "Pg", "p_net")
        Pg_rows.append(Pg_val)

    results = dict(
        status    = prob.status,
        objective = float(prob.value),
        Pg        = np.array(Pg_rows) * baseMVA,  # (T, ng)
        p_net     = _solved_expression_values(build, "p_net") * baseMVA,
    )

    _add_device_results(results, build)
    return results
