"""Original-unit audits and matched comparisons; no optimizer calls."""

from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path

import numpy as np
from scipy import sparse

from cvxopf import audit_socp_relaxation
from experiments.case118_tracy_2021 import e3
from experiments.case118_tracy_2021.stage_b import audit_result
from experiments.case118_tracy_2021.stage_d import read
from experiments.case118_tracy_2021.stage_d_physics import audit_reactive_channels
from experiments.socp_conditioning.artifact_locations import resolve_artifact
from tests.socp_matched import digest as input_digest
from .fixture import ROOT, mathematical_inputs, structural_inputs


def serializable(value):
    """Preserve nonfinite diagnostic values explicitly, never pass them as zero."""
    if isinstance(value, Mapping):
        return {str(k): serializable(v) for k, v in value.items()}
    if isinstance(value, np.ndarray):
        return serializable(value.tolist())
    if isinstance(value, np.generic):
        return serializable(value.item())
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    if isinstance(value, float) and not np.isfinite(value):
        return {"nonfinite": str(value)}
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    return value


def evidence_record(evidence):
    if evidence is None:
        return None
    values = {f.name: getattr(evidence, f.name) for f in fields(evidence)
              if f.name != "coordinates"}
    values["coordinates"] = {f.name: getattr(evidence.coordinates, f.name)
                             for f in fields(evidence.coordinates)
                             if not f.name.startswith("_")}
    return serializable(values)


def single_step_result(result):
    """Time-first projection for the common audit, without changing any values."""
    result = dict(result)
    names = ("Pg", "Qg", "b", "b_q", "soc", "p_nd", "q_nd", "curtailment", "p_net", "q_net",
             "p_load", "q_load", "p_load_served", "q_load_served", "p_load_shed", "q_load_shed",
             "load_shed_fraction", "p_load_shed_total", "p_flows", "Vm", "Va_deg",
             "branch_p_from", "branch_p_to", "branch_q_from", "branch_q_to",
             "branch_s_from", "branch_s_to")
    for name in names:
        if result.get(name) is not None:
            value = np.asarray(result[name])
            result[name] = value.reshape((1,) + value.shape)
    return result


def physical_audit(build, result, kwargs, named):
    relaxation = None
    if kwargs["formulation"] == "socp":
        relaxation = audit_socp_relaxation(build, result, tolerances=e3.RELAXATION_TOLERANCES)

        def network(a, kw, injection, check, box):
            if not relaxation["available"] or not relaxation["complete"]:
                check("lifted_audit_available", float("inf"), 0)
                return
            for name, item in relaxation["residuals"].items():
                check("socp_" + name, item["maximum"], item["tolerance"])
            nodal = relaxation["nodal_power_mva"]
            for name, expected in (("p_net", nodal.real), ("q_net", nodal.imag)):
                check(name + "_lifted_reporting", float(np.max(abs(a[name] - expected))), 1e-4)
            audit_reactive_channels(a, kw, check, box,
                                    relaxation["branch_from_mva"], relaxation["branch_to_mva"])

        common = audit_result(result, kwargs, named, network_audit=network,
                              total_cost_relative_tolerance=1e-6)
    else:
        common = audit_result(result, kwargs, named)
    return common, relaxation


def transformation_check(call, kwargs, evidence, captured_start=None):
    if call.treatment == "baseline":
        return dict(passed=evidence is None, expected_fixed=0, actual_fixed=0)
    if evidence is None:
        return dict(passed=False, reason="preparation evidence unavailable")
    expected = structural_inputs(kwargs)["expected_fixed_count"] if "prepared" in call.treatment or call.treatment == "combined_ac" else 0
    mapping = evidence["coordinates"]
    fixed, free = np.array(mapping["fixed"], int), np.setdiff1d(np.arange(mapping["full_size"]), mapping["fixed"])
    dropped = np.array(mapping["dropped"], int)
    kept = np.setdiff1d(np.arange(mapping["row_count"]), dropped)
    D, R = np.array(evidence["variable_scale"]), np.array(evidence["row_scale"])
    good = (fixed.size == expected == dropped.size and free.size > 0
            and len(np.unique(fixed)) == fixed.size and len(np.unique(dropped)) == dropped.size
            and np.all((fixed >= 0) & (fixed < mapping["full_size"]))
            and np.all((dropped >= 0) & (dropped < mapping["row_count"]))
            and D.shape == free.shape and R.shape == kept.shape
            and np.isfinite(D).all() and np.isfinite(R).all()
            and np.all((D >= 1e-6) & (D <= 1e6)) and np.all((R >= 1e-6) & (R <= 1e6))
            and evidence["checks"].get("restoration_available") is True)
    if call.formulation == "ac":
        assigned, adjusted, reduced = (np.array(evidence[n], float) for n in
                                      ("assigned_x0", "adjusted_x0", "reduced_x0"))
        restored = adjusted.copy()
        if reduced.shape == free.shape:
            restored[free] = reduced
        else:
            good = False
        good = good and np.array_equal(restored, adjusted) and np.array_equal(assigned[free], adjusted[free])
        good = good and np.array_equal(adjusted[fixed], mapping["values"]) and np.all(D == 1) and np.all(R == 1)
        if captured_start is None:
            good = False
        else:
            captured = np.asarray(captured_start["complete_x0"], float)
            layout = [(list(v["shape"]), v["start"], v["stop"]) for v in captured_start["layout"]]
            prepared_layout = [(v[1], v[2], v[3]) for v in evidence["start_layout"]]
            good = good and layout == prepared_layout and np.array_equal(assigned, captured)
    return dict(passed=bool(good), expected_fixed=expected, actual_fixed=int(fixed.size))


def audit_record(call, kwargs, build, record, captured_start=None):
    common, relaxation = physical_audit(build, record["result"], kwargs, record["named_costs"])
    native = record.get("native", {})
    full = native.get("status") == (0 if call.formulation == "ac" else "Solved")
    structure = transformation_check(call, kwargs, record.get("preparation_evidence"), captured_start)
    accepted = record.get("exception") is None and full and common["passed"] and structure["passed"]
    return dict(accepted=bool(accepted), native_full_convergence=bool(full),
                common=common, relaxation=relaxation, transformation=structure)


def pair_check(baseline, prepared):
    if any(not r["audit"]["accepted"] or not r.get("supervised_accepted", True)
           for r in (baseline, prepared)):
        return dict(passed=False, available=False, reason="both independently accepted outcomes required")
    a, b = baseline["audit"]["common"], prepared["audit"]["common"]
    cost_a, cost_b = sum(a["costs"].values()), sum(b["costs"].values())
    objective_limit = 1e-4 + 1e-6 * max(abs(cost_a), abs(cost_b))
    cost_difference = abs(cost_a - cost_b)
    ens_difference = abs(a["metrics"]["energy_not_served_mwh"] - b["metrics"]["energy_not_served_mwh"])
    deltas = {}
    for key in ("Pg", "Qg", "b", "b_q", "soc", "p_nd", "q_nd", "load_shed_fraction", "p_load_served"):
        if key in baseline["result"] and key in prepared["result"]:
            x, y = (np.asarray(r["result"][key], float) for r in (baseline, prepared))
            if x.shape != y.shape or not np.isfinite(x).all() or not np.isfinite(y).all():
                raise ValueError("unaligned pair trajectory")
            deltas[key] = float(np.max(abs(x - y), initial=0))
    starts_match = True
    if "physical_start" in baseline or "physical_start" in prepared:
        starts_match = baseline.get("physical_start") == prepared.get("physical_start")
    inputs_match = bool(baseline.get("mathematical_input_sha256")) and baseline.get("mathematical_input_sha256") == prepared.get("mathematical_input_sha256")
    return dict(passed=bool(cost_difference <= objective_limit and ens_difference <= 1e-4 and starts_match and inputs_match),
                available=True, physical_objectives=[cost_a, cost_b], objective_difference=cost_difference,
                objective_limit=objective_limit, ens_difference_mwh=ens_difference, ens_limit_mwh=1e-4,
                physical_starts_match=starts_match, inputs_match=inputs_match, maximum_trajectory_deltas=deltas,
                storage_endpoints=[np.asarray(r["boundary_soc_mwh"])[[0, -1]].tolist() for r in (baseline, prepared)],
                served_energy_mwh=[float(np.sum(r["result"]["p_load_served"])) for r in (baseline, prepared)])


def historical_control(call, kwargs, build):
    """Reaudit retained QDLDL controls without historical loader/solver execution."""
    if call.id == 5:
        path = resolve_artifact("experiments/case118_tracy_2021/results/e3/arm-010/attempt-000/result.json.gz")
        record = read(path)
        return dict(available=True, historical_classification=record["classification"],
                    pair_required=False, reference=str(path.relative_to(ROOT)))
    label = ("surplus", "deficit", "ramp_up", "ramp_down")[call.id - 1]
    folder = Path(f"experiments/socp_conditioning/results/four_conditions_001/{label}-qdldl")
    record = read(resolve_artifact(folder / "arm.json.gz"))
    supervision = read(resolve_artifact(folder / "supervision.json"))
    completion = read(resolve_artifact(folder / "completion.json"))
    for name, expected in supervision["artifacts"].items():
        if Path(name).name != name:
            raise ValueError("invalid historical artifact reference")
        from experiments.case118_tracy_2021.prepare import digest
        if digest(resolve_artifact(folder / name)) != expected:
            raise ValueError("historical manifest disagrees with retained archive")
    if completion["arm_sha256"] != supervision["artifacts"]["arm.json.gz"]:
        raise ValueError("historical completion/archive mismatch")
    if supervision["classification"] != "completed" or supervision["returncode"] != 0 or record["optimizer_calls"] != 1:
        raise ValueError("historical control lacks completed supervision")
    neutral = mathematical_inputs(kwargs)
    neutral["options"].pop("numerical_preparation")
    original = dict(case=neutral.pop("case"), kwargs={k: v for k, v in neutral.items() if k != "formulation"})
    if input_digest(original) != record["historical_reference"]["mathematical_input_sha256"]:
        raise ValueError("historical comparator input mismatch")
    with np.load(resolve_artifact(folder / "canonical.npz"), allow_pickle=False) as z:
        P = sparse.csc_array((z["P_data"], z["P_indices"], z["P_indptr"]), shape=z["P_shape"])
        c = z["c"].copy()
    with np.load(resolve_artifact(folder / "vectors.npz"), allow_pickle=False) as z:
        x = z["full_x"].copy()
    if x.shape != c.shape or not np.isfinite(x).all():
        raise ValueError("invalid retained canonical primal")
    aliases = {"Pg": "Pg", "Qg": "Qg", "w": "w", "W_re": "W_re", "W_im": "W_im",
               "b": "b", "b_q": "b_q", "p_nd": "p_nd", "q_nd": "q_nd",
               "load_shed_fraction": "load_shed_fraction", "soc": "soc"}
    layouts = {v["name"]: v for v in record["canonical_archive"]["variables"]}
    for name, public in aliases.items():
        item = layouts[name]
        value = x[item["offset"]:item["offset"] + int(np.prod(item["shape"]))].reshape(item["shape"], order="F").T
        if name in {"Pg", "Qg"}:
            value = value * kwargs["case"]["baseMVA"]
        if name == "soc":
            np.testing.assert_allclose(value[0], [s.initial_soc for s in kwargs["storage"]], rtol=0, atol=1e-10)
            value = value[1:]
        np.testing.assert_allclose(value, record["result"][public], rtol=1e-12, atol=1e-10,
                                   err_msg="retained canonical/public primal mismatch")
    constant = kwargs["T"] * kwargs["delta"] * sum(g.cost_coeffs[0] for g in kwargs["generators"])
    canonical = float(.5 * x @ (P @ x) + c @ x + constant)
    historical_result = dict(record["result"], objective=canonical)
    common, relaxation = physical_audit(build, historical_result, kwargs, record["named_costs"])
    accepted = record["native"]["status"] == "Solved" and common["passed"] and record.get("solver_exception") is None and record.get("exception") is None
    return dict(available=True, pair_required=True, reference=str(folder),
                mathematical_input_sha256=input_digest(mathematical_inputs(kwargs)),
                boundary_soc_mwh=np.vstack(([s.initial_soc for s in kwargs["storage"]], historical_result["soc"])),
                historical_classification="accepted" if record["accepted"] else "rejected",
                retrospective_eligibility=bool(accepted), result=historical_result,
                audit=dict(accepted=bool(accepted), common=common), relaxation=relaxation,
                original_canonical_discrepancy=record["objective_reconstruction_error"])
