"""Read-only phase-one verification: retained arrays, no builds or solves."""

from pathlib import Path
import hashlib
import json
import numpy as np
from experiments.case118_counterfactual.data import ToySource, restore_window
from experiments.case118_counterfactual.model import ComparisonTolerances, audit_result
from experiments.case118_counterfactual.dc import audit_dc
from experiments.case118_counterfactual.retained_files import (
    retained_path,
    verify_recorded_source,
)

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "experiments/case118_counterfactual/results/battery_operation_20260917"
study = json.loads((OUT / "study.json").read_text())
summary = json.loads((OUT / "summary.json").read_text())
assert summary["complete"] and summary["stop_reason"] is None
verify_recorded_source(study["execution"])
source = ToySource()
inputs, policy = source.fixture.inputs, source.fixture.policy
tols = ComparisonTolerances(**study["protocol"]["tolerances"])
rows = []
for spec in study["protocol"]["windows"]:
    name = spec["id"]
    ref = spec["context"]
    raw = retained_path(ref["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ref["sha256"]
    context = json.loads(raw)
    coeff = np.array([g["cost_coeffs"] for g in context["generators"]])
    aging = np.array([s["aging_weight"] for s in context["storage"]])
    window = restore_window(study["windows"][name])
    assert source.window(spec["start"], spec["steps"]).identity == window.identity
    for formulation, arms in [("dc", ["F", "B"]), ("ac", ["G", "B"])]:
        for arm in arms:
            record = json.loads((OUT / formulation / name / f"{arm}.json").read_text())
            ref = record["selected"]
            raw = retained_path(ref["path"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == ref["sha256"]
            payload = json.loads(raw)
            result = payload["result"]
            assert payload["exception"] is None
            kwargs = (
                {"reported_loss_cost": payload["reported_loss_cost"]}
                if formulation == "dc"
                else {"reported_common_cost": payload["common_cost_expression"]}
            )
            audit = (audit_dc if formulation == "dc" else audit_result)(
                inputs, policy, window, arm, result, tols, **kwargs
            )
            assert audit["accepted"] and audit == record["selected_audit"]
            if arm != "B":
                incumbent = (audit_dc if formulation == "dc" else audit_result)(
                    inputs, policy, window, "B", result, tols, **kwargs
                )
                assert incumbent["accepted"]
            pg, b, soc = map(lambda k: np.asarray(result[k]), ["Pg", "b", "soc"])
            generation = inputs.delta * float(
                (coeff[:, 0] + coeff[:, 1] * pg + coeff[:, 2] * pg**2).sum()
            )
            storage = inputs.delta * float((np.abs(b) * aging).sum())
            metrics = audit["metrics"]
            assert abs(generation - metrics["generation_cost"]) < 1e-8
            assert abs(storage - metrics["storage_cost"]) < 1e-8
            prior = np.vstack([window.soc_mwh[0], soc[:-1]])
            recurrence = float(np.max(np.abs(soc - prior + inputs.delta * b)))
            endpoint = float(np.max(np.abs(soc[-1] - window.soc_mwh[-1])))
            assert recurrence < 1e-4 and endpoint < 1e-3
            native = generation + storage
            if formulation == "dc":
                proxy = (
                    inputs.delta
                    * inputs.options.loss_weight
                    * float(
                        np.sum(
                            np.asarray(inputs.case["branch"])[:, 2]
                            * (np.asarray(result["p_flows"]) / inputs.case["baseMVA"])
                            ** 2
                        )
                    )
                )
                assert abs(proxy - metrics["dc_loss_proxy_cost"]) < 1e-8
                native += proxy
            assert abs(
                native - result["objective"]
            ) < tols.cost_abs + tols.cost_rel * abs(native)
            rows.append(
                {
                    "window": name,
                    "formulation": formulation,
                    "arm": "F" if arm == "G" else arm,
                    "generation_cost": generation,
                    "storage_cost": storage,
                    "native_objective": native,
                    "fleet_battery_mw": b.sum(axis=1).tolist(),
                    "throughput_mwh": inputs.delta * float(np.abs(b).sum()),
                    "recurrence_error_mwh": recurrence,
                    "terminal_error_mwh": endpoint,
                    "branch_loss_mwh": metrics.get("branch_loss_mwh"),
                    "accepted": True,
                }
            )
artifacts = 0
worker_seconds = 0
lifecycles = list(OUT.glob("*/*/*/lifecycle.json"))
for path in lifecycles:
    data = json.loads(path.read_text())
    assert (
        data["reaped"]
        and data["returncode"] == 0
        and data["completion"]["outcome"] == "accepted"
    )
    worker_seconds += data["worker_wall_seconds"]
    for ref in data["artifacts"].values():
        raw = (path.parent / ref["relative_path"]).read_bytes()
        assert (
            len(raw) == ref["bytes"]
            and hashlib.sha256(raw).hexdigest() == ref["sha256"]
        )
        artifacts += 1
assert len(lifecycles) == 12 and len(rows) == 12
assert abs(worker_seconds - summary["budget_consumed"]["total_worker_seconds"]) < 1e-8
peaks = []
for path in OUT.glob("*/supervisor-progress.json"):
    peaks.extend(json.loads(path.read_text())["memory_samples"])
print(
    json.dumps(
        {
            "accepted": len(rows),
            "lifecycles": len(lifecycles),
            "artifact_hashes_verified": artifacts,
            "budget_consumed": summary["budget_consumed"],
            "sampled_peak_aggregate_gib": max(x["aggregate_mib"] for x in peaks) / 1024,
            "sampled_peak_worker_gib": max(
                max(x["worker_trees_mib"], default=0) for x in peaks
            )
            / 1024,
            "rows": rows,
        },
        indent=2,
    )
)
