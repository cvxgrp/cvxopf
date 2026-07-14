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

from cvxopf.problem import OPFBuild


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
                                            extracted from p_gen at gen_bus
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

    multistep = isinstance(var["Pg"], list)

    if not multistep:
        results = dict(
            status    = prob.status,
            objective = float(prob.value),
            Pg        = var["Pg"].value * baseMVA,
            Qg        = var["Qg"].value * baseMVA,
            Vm        = var["v"].value.flatten(),
            Va_deg    = np.rad2deg(var["theta"].value.flatten()),
            p_net     = var["p"].value * baseMVA,
            q_net     = var["q"].value * baseMVA,
        )
        
        # Add storage results if present
        if "ns" in data:
            results["b"] = var["b"].value
            results["b_q"] = var["b_q"].value
            results["soc"] = var["soc"].value
            results["storage_cost"] = float(
                np.sum(data["storage_aging_weight"] * np.abs(results["b"]))
            )
        
        # Add nondispatchable results if present
        if "nnd" in data:
            results["p_nd"] = var["p_nd"].value
            results["q_nd"] = var["q_nd"].value
            # Curtailment = available - actual production
            if "nd_p_available" in data:  # single-step
                results["curtailment"] = data["nd_p_available"] - results["p_nd"]
            else:  # multistep - this shouldn't happen in single-step path
                results["curtailment"] = data["nd_available"][0, :] - results["p_nd"] # pragma: no cover
        
        # Add HVDC results if present
        if "n_hvdc" in data:
            results["p_hvdc_in"]  = var["p_hvdc_in"].value
            results["p_hvdc_out"] = var["p_hvdc_out"].value
            # Total loss = sending - receiving magnitude; under Convention B
            # (pure proportional loss) this is exactly p_in + p_out, >= 0.
            results["hvdc_loss"] = results["p_hvdc_in"] + results["p_hvdc_out"]
        
        return results

    T       = data["T"]
    Pg_rows = []
    Qg_rows = []
    Vm_rows = []
    Va_rows = []
    p_rows  = []
    q_rows  = []
    b_rows  = []
    b_q_rows = []
    soc_rows = []
    p_nd_rows = []
    q_nd_rows = []
    p_hvdc_in_rows  = []
    p_hvdc_out_rows = []

    for t in range(T):
        Pg_rows.append(var["Pg"][t].value)
        Qg_rows.append(var["Qg"][t].value)
        Vm_rows.append(var["v"][t].value.flatten())
        Va_rows.append(var["theta"][t].value.flatten())
        p_rows.append(var["p"][t].value)
        q_rows.append(var["q"][t].value)
        
        # Extract storage results if present
        if "ns" in data:
            b_rows.append(var["b"][t].value)
            b_q_rows.append(var["b_q"][t].value)
            soc_rows.append(var["soc"][t].value)
        
        # Extract nondispatchable results if present
        if "nnd" in data:
            p_nd_rows.append(var["p_nd"][t].value)
            q_nd_rows.append(var["q_nd"][t].value)
        
        # Extract HVDC results if present
        if "n_hvdc" in data:
            p_hvdc_in_rows.append(var["p_hvdc_in"][t].value)
            p_hvdc_out_rows.append(var["p_hvdc_out"][t].value)

    results = dict(
        status    = prob.status,
        objective = float(prob.value),
        Pg        = np.array(Pg_rows) * baseMVA,
        Qg        = np.array(Qg_rows) * baseMVA,
        Vm        = np.array(Vm_rows),
        Va_deg    = np.rad2deg(np.array(Va_rows)),
        p_net     = np.array(p_rows) * baseMVA,
        q_net     = np.array(q_rows) * baseMVA,
    )
    
    # Add storage results if present
    if "ns" in data:
        results["b"] = np.array(b_rows)
        results["b_q"] = np.array(b_q_rows)
        results["soc"] = np.array(soc_rows)
        results["storage_cost"] = float(
            np.sum(data["storage_aging_weight"] * np.abs(results["b"]))
        )
    
    # Add nondispatchable results if present
    if "nnd" in data:
        results["p_nd"] = np.array(p_nd_rows)
        results["q_nd"] = np.array(q_nd_rows)
        # Curtailment = available - actual production
        if "nd_available" in data:  # multistep
            results["curtailment"] = data["nd_available"] - results["p_nd"]
        else:  # single-step - this shouldn't happen in multistep path
            results["curtailment"] = data["nd_p_available"] - results["p_nd"]  # pragma: no cover
    
    # Add HVDC results if present
    if "n_hvdc" in data:
        results["p_hvdc_in"]  = np.array(p_hvdc_in_rows)
        results["p_hvdc_out"] = np.array(p_hvdc_out_rows)
        # Total loss = p_in + p_out (Convention B, pure proportional loss), >= 0.
        results["hvdc_loss"] = results["p_hvdc_in"] + results["p_hvdc_out"]
    
    return results


def _extract_dc_results(build: OPFBuild) -> dict:
    """
    Extract results for lossy DC formulation (single-step or multi-step).

    Pg is extracted from p_gen at gen_bus indices, giving a (ng,) array
    that aligns with the AC convention.
    """
    var     = build.variables
    data    = build.data
    baseMVA = float(data["baseMVA"])
    prob    = build.prob
    gen_bus = data["gen_bus"]

    multistep = isinstance(var["p_gen"], list)

    if not multistep:
        p_gen_val   = var["p_gen"].value
        p_flows_val = var["p_flows"].value
        Pd          = data["Pd"]

        # Guard: solver may return None values if problem is infeasible
        if p_gen_val is None or p_flows_val is None:
            return dict(
                status    = prob.status,
                objective = float("nan"),
                Pg        = None,
                p_flows   = None,
                p_net     = None,
            )

        results = dict(
            status    = prob.status,
            objective = float(prob.value),
            Pg        = p_gen_val[gen_bus] * baseMVA,
            p_flows   = p_flows_val * baseMVA,
            p_net     = (p_gen_val - Pd) * baseMVA,
        )
        
        # Add storage results if present
        if "ns" in data:
            results["b"] = var["b"].value
            results["soc"] = var["soc"].value
            results["storage_cost"] = float(
                np.sum(data["storage_aging_weight"] * np.abs(results["b"]))
            )
        
        # Add nondispatchable results if present
        if "nnd" in data:
            results["p_nd"] = var["p_nd"].value
            # Curtailment = available - actual production
            if "nd_p_available" in data:  # single-step
                results["curtailment"] = data["nd_p_available"] - results["p_nd"]
            else:  # multistep - this shouldn't happen in single-step path
                results["curtailment"] = data["nd_available"][0, :] - results["p_nd"] # pragma: no cover
        
        # Add HVDC results if present
        if "n_hvdc" in data:
            results["p_hvdc_in"]  = var["p_hvdc_in"].value
            results["p_hvdc_out"] = var["p_hvdc_out"].value
            # Total loss = p_in + p_out (Convention B, pure proportional loss), >= 0.
            results["hvdc_loss"] = results["p_hvdc_in"] + results["p_hvdc_out"]
        
        return results

    T            = data["T"]
    Pd_series    = data["Pd_series"]
    Pg_rows      = []
    p_flows_rows = []
    p_net_rows   = []
    b_rows       = []
    soc_rows     = []
    p_nd_rows    = []
    p_hvdc_in_rows  = []
    p_hvdc_out_rows = []

    for t in range(T):
        p_gen_t   = var["p_gen"][t].value
        p_flows_t = var["p_flows"][t].value
        if p_gen_t is None or p_flows_t is None:
            return dict(
                status    = prob.status,
                objective = float("nan"),
                Pg        = None,
                p_flows   = None,
                p_net     = None,
            )
        Pg_rows.append(p_gen_t[gen_bus])
        p_flows_rows.append(p_flows_t)
        p_net_rows.append(p_gen_t - Pd_series[t])
        
        # Extract storage results if present
        if "ns" in data:
            b_rows.append(var["b"][t].value)
            soc_rows.append(var["soc"][t].value)
        
        # Extract nondispatchable results if present
        if "nnd" in data:
            p_nd_rows.append(var["p_nd"][t].value)
        
        # Extract HVDC results if present
        if "n_hvdc" in data:
            p_hvdc_in_rows.append(var["p_hvdc_in"][t].value)
            p_hvdc_out_rows.append(var["p_hvdc_out"][t].value)

    results = dict(
        status    = prob.status,
        objective = float(prob.value),
        Pg        = np.array(Pg_rows) * baseMVA,
        p_flows   = np.array(p_flows_rows) * baseMVA,
        p_net     = np.array(p_net_rows) * baseMVA,
    )
    
    # Add storage results if present
    if "ns" in data:
        results["b"] = np.array(b_rows)
        results["soc"] = np.array(soc_rows)
        results["storage_cost"] = float(
            np.sum(data["storage_aging_weight"] * np.abs(results["b"]))
        )
    
    # Add nondispatchable results if present
    if "nnd" in data:
        results["p_nd"] = np.array(p_nd_rows)
        # Curtailment = available - actual production
        if "nd_available" in data:  # multistep
            results["curtailment"] = data["nd_available"] - results["p_nd"]
        else:  # single-step - this shouldn't happen in multistep path
            results["curtailment"] = data["nd_p_available"] - results["p_nd"]  # pragma: no cover
    
    # Add HVDC results if present
    if "n_hvdc" in data:
        results["p_hvdc_in"]  = np.array(p_hvdc_in_rows)
        results["p_hvdc_out"] = np.array(p_hvdc_out_rows)
        # Total loss = p_in + p_out (Convention B, pure proportional loss), >= 0.
        results["hvdc_loss"] = results["p_hvdc_in"] + results["p_hvdc_out"]
    
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

    # Detect single-step vs multi-step by checking if Pg is a list
    multistep = isinstance(var["Pg"], list)

    if not multistep:
        # Single-step extraction
        Pg_val = var["Pg"].value

        # Guard: solver may return None values if problem is infeasible
        if Pg_val is None:
            return dict(
                status    = prob.status,
                objective = float("nan"),
                Pg        = None,
                p_net     = None,
            )

        results = dict(
            status    = prob.status,
            objective = float(prob.value),
            Pg        = Pg_val * baseMVA,          # (ng,) MW
            p_net     = float(np.sum(Pg_val) * baseMVA - data["Pd_total"] * baseMVA),  # scalar MW
        )

        # Add storage results if present
        if "ns" in data:
            results["b"] = var["b"].value
            results["soc"] = var["soc"].value
            results["storage_cost"] = float(
                np.sum(data["storage_aging_weight"] * np.abs(results["b"]))
            )

        # Add nondispatchable results if present
        if "nnd" in data:
            results["p_nd"] = var["p_nd"].value
            # Curtailment = available - actual production
            results["curtailment"] = data["nd_p_available"] - results["p_nd"]

        return results

    # Multi-step extraction
    T = data["T"]
    Pg_rows = []
    b_rows = []
    soc_rows = []
    p_nd_rows = []

    for t in range(T):
        Pg_val = var["Pg"][t].value
        if Pg_val is None:
            return dict(
                status=prob.status,
                objective=float("nan"),
                Pg=None,
                p_net=None,
            )
        Pg_rows.append(Pg_val)
        if "ns" in data:
            b_rows.append(var["b"][t].value)
            soc_rows.append(var["soc"][t].value)
        if "nnd" in data:
            p_nd_rows.append(var["p_nd"][t].value)

    Pd_series = data["Pd_series"]  # shape (T,)

    results = dict(
        status    = prob.status,
        objective = float(prob.value),
        Pg        = np.array(Pg_rows) * baseMVA,  # (T, ng)
        p_net     = (np.array([np.sum(r) for r in Pg_rows]) - Pd_series) * baseMVA,  # (T,)
    )

    # Add storage results if present
    if "ns" in data:
        results["b"] = np.array(b_rows)      # (T, ns)
        results["soc"] = np.array(soc_rows)  # (T, ns)
        results["storage_cost"] = float(
            np.sum(data["storage_aging_weight"] * np.abs(results["b"]))
        )

    # Add nondispatchable results if present
    if "nnd" in data:
        results["p_nd"] = np.array(p_nd_rows)  # (T, nnd)
        if "nd_available" in data:
            results["curtailment"] = data["nd_available"] - results["p_nd"]
        else:
            results["curtailment"] = data["nd_p_available"] - results["p_nd"]

    return results