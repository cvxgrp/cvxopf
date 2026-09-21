"""
Result extraction and comparison utilities.

Operates on OPFBuild objects after prob.solve() has been called.
Dispatches on build.formulation to return the appropriate result schema.

AC results keys:
    status, objective, Pg, Qg, Vm, Va_deg, p_net, q_net,
    branch_p_from, branch_q_from, branch_p_to, branch_q_to,
    branch_s_from, branch_s_to

DC (lossy_dc) results keys:
    status, objective, Pg, p_flows, p_net
    (Vm, Va_deg, Qg, q_net are absent — not modelled in DC formulation)

HVDC results keys (AC and lossy_dc, present when "n_hvdc" in build.data):
    p_hvdc_in, p_hvdc_out (signed nodal injections, MW), hvdc_loss (derived,
    = -(p_hvdc_in + p_hvdc_out), >= 0). Shapes (n_hvdc,) single / (T, n_hvdc)
    multi. Absent from singlenode_dc results (HVDC silently ignored there).

Singlenode DC (singlenode_dc) results keys:
    status, objective, Pg, p_net
    (p_flows, Vm, Va_deg, Qg, q_net absent — not modelled)
    (b, soc, storage_cost present when storage is not None)
    (p_nd, curtailment present when nondispatchable is not None)

Storage terminal-policy results (all formulations, when configured):
    storage_terminal_deviation (signed, MWh; negative means shortfall)
    storage_terminal_cost (scalar, soft terminal policies only)

First-class load results (when ``"nload"`` is present in build data):
    p_load, q_load, p_load_served (MW/MVAr device arrays)
    q_load_served (AC only)
When one or more loads are sheddable:
    p_load_shed, load_shed_fraction, p_load_shed_total,
    energy_not_served_by_load, energy_not_served, load_shedding_cost
    q_load_shed (AC only)
Fixed-load inputs and served values remain available without a primal solve.

The result schema is determined by the built model, not by solve success.
When no primal solution is available, configured array-valued and derived
quantities are ``None`` while scalar objective and cost quantities are NaN.
Callers should inspect ``status`` before consuming numerical values.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np

from cvxopf._temporal_assembly import ResultProjectionSpec
from cvxopf.hvdc import _loss_values
from cvxopf.nondispatchable import _curtailment_values
from cvxopf.problem import OPFBuild
from cvxopf.storage import _terminal_deviation_values


def _solved_expression_value(build: OPFBuild, name: str) -> float:
    """Return a scalar value from the exact expression used by the model."""
    if name not in build.expressions:
        if build.temporal_assembly == "vectorized":
            raise ValueError(f"missing required vectorized result expression {name!r}")
        raise KeyError(name)
    value = _solved_expression_values(build, name)
    if value is None:
        return float("nan")
    array = np.asarray(value)
    if array.shape != ():
        raise ValueError(
            f"modeled scalar expression {name!r} produced shape {array.shape}"
        )
    return float(array)


def _vectorized_projection(
    build: OPFBuild,
    source_kind: Literal["variable", "expression"],
    name: str,
) -> ResultProjectionSpec:
    """Return the trusted projection for one vectorized solved source."""
    if "T" not in build.data:
        raise ValueError("vectorized result extraction requires horizon metadata T")
    return build.result_projections.projection_for(source_kind, name)


def _project_vectorized_value(
    build: OPFBuild,
    source_kind: Literal["variable", "expression"],
    name: str,
    source_shape: tuple[int, ...],
    value: Any,
) -> np.ndarray | None:
    """Project one available time-last value under its frozen schema."""
    projection = _vectorized_projection(build, source_kind, name)
    horizon_steps = int(build.data["T"])
    expected = projection.internal_shape(horizon_steps)
    if source_shape != expected:
        raise ValueError(
            f"vectorized result {source_kind} {name!r} declares source shape "
            f"{source_shape}, but its projection requires {expected}"
        )
    return (
        None if value is None else projection.project(np.asarray(value), horizon_steps)
    )


def _solved_expression_values(
    build: OPFBuild, name: str
) -> np.ndarray | np.generic | float | None:
    """Evaluate a named single- or multi-step modeled expression."""
    expression = build.expressions.get(name)
    if expression is None:
        if build.temporal_assembly == "vectorized":
            raise ValueError(f"missing required vectorized result expression {name!r}")
        return None
    if isinstance(expression, list):
        values = [item.value for item in expression]
        return None if any(value is None for value in values) else np.array(values)
    if build.temporal_assembly == "vectorized":
        return _project_vectorized_value(
            build,
            "expression",
            name,
            tuple(int(dimension) for dimension in expression.shape),
            expression.value,
        )
    return expression.value


def _scaled_values(value: Any, scale: float) -> Any:
    """Scale an available scalar or array while preserving ``None``."""
    return None if value is None else value * scale


def _objective_value(build: OPFBuild) -> float:
    """Return the solver objective, or NaN when it is unavailable."""
    value = build.prob.value
    if value is None or not np.isfinite(value):
        return float("nan")
    return float(value)


def _initialize_results(build: OPFBuild) -> dict[str, Any]:
    """Initialize the public schema from the built model, before values."""
    core_fields = {
        "ac": (
            "Pg",
            "Qg",
            "Vm",
            "Va_deg",
            "p_net",
            "q_net",
            "branch_p_from",
            "branch_q_from",
            "branch_p_to",
            "branch_q_to",
            "branch_s_from",
            "branch_s_to",
        ),
        "lossy_dc": ("Pg", "p_flows", "p_net"),
        "singlenode_dc": ("Pg", "p_net"),
    }[build.formulation]
    results = {
        "status": build.prob.status,
        "objective": float("nan"),
        **{field: None for field in core_fields},
    }

    if "ns" in build.data:
        results["storage_device_ids"] = np.asarray(
            build.data["storage_device_ids"], dtype=object
        ).copy()
        results["storage_device_id_is_explicit"] = np.asarray(
            build.data["storage_device_id_is_explicit"], dtype=bool
        ).copy()
        results["b"] = None
        if "b_q" in build.variables:
            results["b_q"] = None
        results["soc"] = None
        results["storage_cost"] = float("nan")
        targets = build.data["storage_terminal_soc"]
        if np.any(np.isfinite(targets)):
            results["storage_terminal_deviation"] = None
        if "storage_terminal_cost" in build.expressions:
            results["storage_terminal_cost"] = float("nan")

    if "nnd" in build.data:
        results["p_nd"] = None
        if "q_nd" in build.variables:
            results["q_nd"] = None
        results["curtailment"] = None

    if "n_hvdc" in build.data:
        results["p_hvdc_in"] = None
        results["p_hvdc_out"] = None
        results["hvdc_loss"] = None

    if "nload" in build.data:
        results["p_load"] = None
        results["q_load"] = None
        results["p_load_served"] = None
        if build.formulation == "ac":
            results["q_load_served"] = None
        if int(build.data["nsheddable"]) > 0:
            results["p_load_shed"] = None
            if build.formulation == "ac":
                results["q_load_shed"] = None
            results["load_shed_fraction"] = None
            results["p_load_shed_total"] = None
            results["energy_not_served_by_load"] = None
            results["energy_not_served"] = None
            results["load_shedding_cost"] = float("nan")

    return results


def _variable_values(build: OPFBuild, name: str) -> Any:
    """Return one variable value or stack a multistep variable list."""
    if name not in build.variables:
        if build.temporal_assembly == "vectorized":
            raise ValueError(f"missing required vectorized result variable {name!r}")
        raise KeyError(name)
    variable = build.variables[name]
    if isinstance(variable, list):
        values = [item.value for item in variable]
        if any(value is None for value in values):
            return None
        return np.array(values)
    if build.temporal_assembly == "vectorized":
        return _project_vectorized_value(
            build,
            "variable",
            name,
            tuple(int(dimension) for dimension in variable.shape),
            variable.value,
        )
    return variable.value


def _add_storage_results(results: dict[str, Any], build: OPFBuild) -> None:
    """Add storage-owned variables and modeled cost to a result dictionary."""
    if "ns" not in build.data:
        return
    results["b"] = _variable_values(build, "b")
    if "b_q" in build.variables:
        results["b_q"] = _variable_values(build, "b_q")
    results["soc"] = _variable_values(build, "soc")
    results["storage_cost"] = _solved_expression_value(build, "storage_cost")
    targets = build.data["storage_terminal_soc"]
    if np.any(np.isfinite(targets)):
        if results["soc"] is None:
            results["storage_terminal_deviation"] = None
        else:
            terminal_soc = results["soc"][-1] if "T" in build.data else results["soc"]
            results["storage_terminal_deviation"] = _terminal_deviation_values(
                targets, terminal_soc
            )
    if "storage_terminal_cost" in build.expressions:
        results["storage_terminal_cost"] = _solved_expression_value(
            build, "storage_terminal_cost"
        )


def _add_nd_results(results: dict[str, Any], build: OPFBuild) -> None:
    """Add ND variables and device-owned curtailment values."""
    if "nnd" not in build.data:
        return
    results["p_nd"] = _variable_values(build, "p_nd")
    if "q_nd" in build.variables:
        results["q_nd"] = _variable_values(build, "q_nd")
    availability_key = "nd_available" if "T" in build.data else "nd_p_available"
    results["curtailment"] = (
        None
        if results["p_nd"] is None
        else _curtailment_values(build.data[availability_key], results["p_nd"])
    )


def _add_hvdc_results(results: dict[str, Any], build: OPFBuild) -> None:
    """Add HVDC terminal injections and device-owned loss values."""
    if "n_hvdc" not in build.data:
        return
    results["p_hvdc_in"] = _variable_values(build, "p_hvdc_in")
    results["p_hvdc_out"] = _variable_values(build, "p_hvdc_out")
    results["hvdc_loss"] = (
        None
        if (results["p_hvdc_in"] is None or results["p_hvdc_out"] is None)
        else _loss_values(  # type: ignore[no-untyped-call]
            results["p_hvdc_in"], results["p_hvdc_out"]
        )
    )


def _add_load_results(results: dict[str, Any], build: OPFBuild) -> None:
    """Add exogenous inputs and conditional served/shedding results."""
    if "nload" not in build.data:
        return
    results["p_load"] = _solved_expression_values(build, "p_load")
    results["q_load"] = _solved_expression_values(build, "q_load")
    if int(build.data["nsheddable"]) == 0:
        results["p_load_served"] = _solved_expression_values(build, "p_load_served")
        if build.formulation == "ac":
            results["q_load_served"] = _solved_expression_values(build, "q_load_served")
        return

    results["p_load_served"] = _solved_expression_values(build, "p_load_served")
    results["p_load_shed"] = _solved_expression_values(build, "p_load_shed")
    results["load_shed_fraction"] = _solved_expression_values(
        build, "load_shed_fraction"
    )
    results["p_load_shed_total"] = _solved_expression_values(build, "p_load_shed_total")
    if build.formulation == "ac":
        results["q_load_served"] = _solved_expression_values(build, "q_load_served")
        results["q_load_shed"] = _solved_expression_values(build, "q_load_shed")
    results["energy_not_served_by_load"] = _solved_expression_values(
        build, "energy_not_served_by_load"
    )
    results["energy_not_served"] = _solved_expression_values(build, "energy_not_served")
    results["load_shedding_cost"] = _solved_expression_value(
        build, "load_shedding_cost"
    )


def _add_device_results(results: dict[str, Any], build: OPFBuild) -> None:
    """Add every optional device's reported values."""
    _add_storage_results(results, build)
    _add_nd_results(results, build)
    _add_hvdc_results(results, build)
    _add_load_results(results, build)


def _add_ac_branch_results(results: dict[str, Any], build: OPFBuild) -> None:
    """Add signed AC branch-terminal powers and derived magnitudes."""
    base_mva = float(build.data["baseMVA"])
    signed = {
        "branch_p_from": _scaled_values(
            _solved_expression_values(build, "branch_p_from_pu"),
            base_mva,
        ),
        "branch_q_from": _scaled_values(
            _solved_expression_values(build, "branch_q_from_pu"),
            base_mva,
        ),
        "branch_p_to": _scaled_values(
            _solved_expression_values(build, "branch_p_to_pu"),
            base_mva,
        ),
        "branch_q_to": _scaled_values(
            _solved_expression_values(build, "branch_q_to_pu"),
            base_mva,
        ),
    }
    results.update(signed)
    if any(value is None for value in signed.values()):
        results["branch_s_from"] = None
        results["branch_s_to"] = None
        return
    results["branch_s_from"] = np.hypot(
        signed["branch_p_from"], signed["branch_q_from"]
    )
    results["branch_s_to"] = np.hypot(signed["branch_p_to"], signed["branch_q_to"])


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def extract_results(build: OPFBuild) -> dict[str, Any]:
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
            objective   float        Optimal interval cost (objective units)
            Pg          np.ndarray   (ng,)  Generator real output, MW
            Qg          np.ndarray   (ng,)  Generator reactive output, MVAr
            Vm          np.ndarray   (nb,)  Bus voltage magnitudes, p.u.
            Va_deg      np.ndarray   (nb,)  Bus voltage angles, degrees
            p_net       np.ndarray   (nb,)  Net real bus injection, MW
            q_net       np.ndarray   (nb,)  Net reactive bus injection, MVAr
            branch_p_from, branch_p_to
                         np.ndarray   (nl,)  Signed terminal real power, MW
            branch_q_from, branch_q_to
                         np.ndarray   (nl,)  Signed terminal reactive power,
                                            MVAr
            branch_s_from, branch_s_to
                         np.ndarray   (nl,)  Terminal apparent power, MVA

        AC multi-step: same keys; Pg, Qg are (T, ng); Vm, Va_deg, p_net,
        q_net are (T, nb), and branch fields are (T, nl).
        ``branch_p_*`` is in MW, ``branch_q_*`` is in MVAr, and
        ``branch_s_*`` is in MVA in both modes. objective is total integrated
        horizon cost.

        DC single-step keys:
            status      str          CVXPY solve status
            objective   float        Optimal interval cost (objective units)
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
            objective   float        Optimal interval cost (objective units)
            Pg          np.ndarray   (ng,)  Per-generator output, MW
            p_net       float        Net generation minus load, MW
                                     (near zero at optimum)

        Singlenode DC multi-step: Pg is (T, ng); p_net is (T,).

        First-class loads always add ``p_load``, ``q_load``, and
        ``p_load_served`` in MW/MVAr with shape ``(nload,)`` or
        ``(T, nload)``. AC also adds ``q_load_served``. When one or more
        loads are sheddable, ``p_load_shed`` and ``load_shed_fraction`` have
        shape ``(nsheddable,)`` or ``(T, nsheddable)``; AC also adds signed
        ``q_load_shed``. ``p_load_shed_total`` is scalar or ``(T,)``.
        ``energy_not_served_by_load`` is a horizon ``(nsheddable,)`` MWh
        vector; ``energy_not_served`` and ``load_shedding_cost`` are horizon
        scalars.

        Configured keys remain present when no primal solution is available.
        Exogenous load inputs and fixed served loads remain available.
        Storage identity and its explicitness mask likewise remain available.
        Array-valued primal and derived quantities are otherwise None; scalar
        objective and cost quantities are NaN. Inspect status first.

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


def compare_to_reference(
    results: dict[str, Any], reference: dict[str, Any]
) -> dict[str, Any]:
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

        cv = np.asarray(results[f], dtype=float)
        ref = np.asarray(reference[f], dtype=float)

        abs_diff = np.abs(cv - ref)
        denom = np.where(np.abs(ref) > 1e-8, np.abs(ref), 1.0)
        rel_diff = abs_diff / denom

        comparison[f] = dict(
            cvxopf=cv,
            reference=ref,
            abs_diff=abs_diff,
            rel_diff=rel_diff,
        )

    return comparison


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_ac_results(build: OPFBuild) -> dict[str, Any]:
    """Extract results for AC formulation (single-step or multi-step)."""
    var = build.variables
    data = build.data
    baseMVA = float(data["baseMVA"])
    prob = build.prob
    results = _initialize_results(build)

    multistep = "T" in data

    if not multistep:
        voltage = var["v"].value
        angle = var["theta"].value
        results.update(
            status=prob.status,
            objective=_objective_value(build),
            Pg=_scaled_values(var["Pg"].value, baseMVA),
            Qg=_scaled_values(var["Qg"].value, baseMVA),
            Vm=None if voltage is None else voltage.flatten(),
            Va_deg=(None if angle is None else np.rad2deg(angle.flatten())),
            p_net=_scaled_values(_solved_expression_values(build, "p_net"), baseMVA),
            q_net=_scaled_values(_solved_expression_values(build, "q_net"), baseMVA),
        )

        if build.temporal_assembly == "vectorized" or (
            voltage is not None and angle is not None
        ):
            _add_ac_branch_results(results, build)
        _add_device_results(results, build)
        return results

    Pg_values = _variable_values(build, "Pg")
    Qg_values = _variable_values(build, "Qg")
    voltage = _variable_values(build, "v")
    angle = _variable_values(build, "theta")
    public_voltage = (
        voltage
        if build.temporal_assembly == "vectorized" or voltage is None
        else np.squeeze(voltage, axis=-1)
    )
    public_angle = (
        angle
        if build.temporal_assembly == "vectorized" or angle is None
        else np.squeeze(angle, axis=-1)
    )
    results.update(
        status=prob.status,
        objective=_objective_value(build),
        Pg=_scaled_values(Pg_values, baseMVA),
        Qg=_scaled_values(Qg_values, baseMVA),
        Vm=public_voltage,
        Va_deg=(None if public_angle is None else np.rad2deg(public_angle)),
        p_net=_scaled_values(_solved_expression_values(build, "p_net"), baseMVA),
        q_net=_scaled_values(_solved_expression_values(build, "q_net"), baseMVA),
    )

    if build.temporal_assembly == "vectorized" or (
        voltage is not None and angle is not None
    ):
        _add_ac_branch_results(results, build)
    _add_device_results(results, build)
    return results


def _extract_dc_results(build: OPFBuild) -> dict[str, Any]:
    """
    Extract results for lossy DC formulation (single-step or multi-step).

    Pg is stored directly as a per-generator (ng,) variable. Nodal net
    injection is evaluated from the exact expression used in power balance.
    """
    var = build.variables
    data = build.data
    baseMVA = float(data["baseMVA"])
    prob = build.prob
    multistep = "T" in data
    results = _initialize_results(build)

    if not multistep:
        Pg_val = var["Pg"].value
        p_flows_val = var["p_flows"].value
        results.update(
            status=prob.status,
            objective=_objective_value(build),
            Pg=_scaled_values(Pg_val, baseMVA),
            p_flows=_scaled_values(p_flows_val, baseMVA),
            p_net=_scaled_values(_solved_expression_values(build, "p_net"), baseMVA),
        )

        _add_device_results(results, build)
        return results

    results.update(
        status=prob.status,
        objective=_objective_value(build),
        Pg=_scaled_values(_variable_values(build, "Pg"), baseMVA),
        p_flows=_scaled_values(_variable_values(build, "p_flows"), baseMVA),
        p_net=_scaled_values(_solved_expression_values(build, "p_net"), baseMVA),
    )

    _add_device_results(results, build)
    return results


def _extract_singlenode_dc_results(build: OPFBuild) -> dict[str, Any]:
    """
    Extract results for single-node DC formulation (single-step or multi-step).

    For single-node DC, Pg is (ng,) in single-step or (T, ng) in multi-step.
    p_net is a scalar float in single-step or (T,) array in multi-step.
    """
    var = build.variables
    data = build.data
    baseMVA = float(data["baseMVA"])
    prob = build.prob
    results = _initialize_results(build)

    multistep = "T" in data

    if not multistep:
        # Single-step extraction
        Pg_val = var["Pg"].value

        p_net = _solved_expression_values(build, "p_net")
        results.update(
            status=prob.status,
            objective=_objective_value(build),
            Pg=_scaled_values(Pg_val, baseMVA),
            p_net=(None if p_net is None else float(p_net * baseMVA)),
        )

        _add_device_results(results, build)
        return results

    results.update(
        status=prob.status,
        objective=_objective_value(build),
        Pg=_scaled_values(_variable_values(build, "Pg"), baseMVA),
        p_net=_scaled_values(_solved_expression_values(build, "p_net"), baseMVA),
    )

    _add_device_results(results, build)
    return results
