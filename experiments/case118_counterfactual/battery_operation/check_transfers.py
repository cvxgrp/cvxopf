"""Verify prescribed battery transfers from retained arrays; no optimization."""

from pathlib import Path
import hashlib
import json
import numpy as np
from experiments.case118_counterfactual.data import ToySource, restore_window
from experiments.case118_counterfactual.model import ComparisonTolerances, audit_result
from experiments.case118_counterfactual.dc import audit_dc
from experiments.case118_counterfactual.retained_files import verify_recorded_source
from experiments.case118_counterfactual.transfers import prepare, comparisons
from experiments.case118_counterfactual.worker import jsonable

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "experiments/case118_counterfactual/results/battery_transfers_20260917"
study = json.loads((OUT / "study.json").read_text())
summary = json.loads((OUT / "summary.json").read_text())
assert summary["complete"] and summary["stop_reason"] is None
verify_recorded_source(study["execution"])
source = ToySource()
prepared = prepare(source, study["protocol"])
inputs, policy = source.fixture.inputs, source.fixture.policy
tols = ComparisonTolerances(**study["effective_protocol"]["tolerances"])
assert (
    jsonable(
        comparisons(prepared, summary["dc_records"], summary["ac_summary"]["windows"])
    )
    == summary["comparisons"]
)
rows = []
for spec in study["effective_protocol"]["windows"]:
    name = spec["id"]
    window = restore_window(study["windows"][name])
    assert source.window(spec["start"], spec["steps"]).identity == window.identity
    schedule = np.asarray(study["battery_schedules_mw"][name])
    assert np.array_equal(schedule, prepared.schedules[name])
    description = study["transfers"][name]
    energy = description["transfer_mwh"]
    delta_b = schedule - window.battery_mw
    assert np.allclose(
        inputs.delta * delta_b.sum(axis=1), [-energy, 0, energy], rtol=0, atol=1e-12
    )
    context = json.loads(Path(spec["context"]["path"]).read_text())
    coeff = np.asarray([g["cost_coeffs"] for g in context["generators"]])
    aging = np.asarray([s["aging_weight"] for s in context["storage"]])
    for formulation, arm in [("ac", "G"), ("dc", "F")]:
        record = json.loads((OUT / formulation / name / f"{arm}.json").read_text())
        ref = record["selected"]
        raw = Path(ref["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == ref["sha256"]
        payload = json.loads(raw)
        result = payload["result"]
        assert (
            payload["window_identity"] == window.identity
            and payload["exception"] is None
        )
        kwargs = (
            {"reported_common_cost": payload["common_cost_expression"]}
            if formulation == "ac"
            else {"reported_loss_cost": payload["reported_loss_cost"]}
        )
        audit = (audit_result if formulation == "ac" else audit_dc)(
            inputs,
            policy,
            window,
            arm,
            result,
            tols,
            battery_schedule_mw=schedule,
            **kwargs,
        )
        assert audit["accepted"] and jsonable(audit) == record["selected_audit"]
        pg, b, soc = (np.asarray(result[k]) for k in ["Pg", "b", "soc"])
        generation = inputs.delta * float(
            (coeff[:, 0] + coeff[:, 1] * pg + coeff[:, 2] * pg**2).sum()
        )
        storage = inputs.delta * float((np.abs(b) * aging).sum())
        native = generation + storage
        proxy = None
        if formulation == "dc":
            proxy = (
                inputs.delta
                * inputs.options.loss_weight
                * float(
                    (
                        np.asarray(inputs.case["branch"])[:, 2]
                        * (np.asarray(result["p_flows"]) / inputs.case["baseMVA"]) ** 2
                    ).sum()
                )
            )
            native += proxy
        assert abs(generation - audit["metrics"]["generation_cost"]) < 1e-8
        assert abs(storage - audit["metrics"]["storage_cost"]) < 1e-8
        assert abs(native - result["objective"]) < tols.cost_abs + tols.cost_rel * abs(
            native
        )
        assert np.max(np.abs(b - schedule)) < tols.lock_mw_abs
        assert (
            np.max(
                np.abs(soc - (window.soc_mwh[0] - inputs.delta * np.cumsum(b, axis=0)))
            )
            < 1e-4
        )
        assert np.max(np.abs(soc[-1] - window.soc_mwh[-1])) < 1e-3
        rows.append(
            dict(
                window=name,
                formulation=formulation,
                energy_mwh=energy,
                status=result["status"],
                generation_cost=generation,
                storage_cost=storage,
                native_objective=native,
                dc_loss_proxy_cost=proxy,
                throughput_mwh=inputs.delta * float(np.abs(b).sum()),
                fleet_battery_mw=b.sum(axis=1).tolist(),
                max_schedule_error_mw=float(np.max(np.abs(b - schedule))),
                terminal_error_mwh=float(np.max(np.abs(soc[-1] - window.soc_mwh[-1]))),
            )
        )
artifacts = 0
seconds = 0
counts = {"ac_attempts": 0, "dc_attempts": 0}
canceled = 0
for form in ["ac", "dc"]:
    for path in (OUT / form).glob("*/*/lifecycle.json"):
        receipt = json.loads(path.read_text())
        counts[form + "_attempts"] += 1
        assert receipt["reaped"]
        if receipt["completion"] is None:
            assert receipt["returncode"] == -15
            canceled += 1
        else:
            assert (
                receipt["returncode"] == 0
                and receipt["completion"]["outcome"] == "accepted"
            )
        seconds += receipt["worker_wall_seconds"]
        for ref in receipt["artifacts"].values():
            raw = (path.parent / ref["relative_path"]).read_bytes()
            assert (
                len(raw) == ref["bytes"]
                and hashlib.sha256(raw).hexdigest() == ref["sha256"]
            )
            artifacts += 1
used = summary["phase_budget_consumed"]
assert all(used[k] == v for k, v in counts.items())
assert abs(seconds - used["total_worker_seconds"]) < 1e-8
assert all(
    abs(summary["budget_consumed"][k] - used[k] - prepared.consumed[k]) < 1e-8
    for k in used
)
assert len(rows) == 12
assert counts == {"ac_attempts": 7, "dc_attempts": 6} and canceled == 1
peaks = [
    x
    for path in OUT.glob("*/supervisor-progress.json")
    for x in json.loads(path.read_text())["memory_samples"]
]
print(
    json.dumps(
        dict(
            accepted=len(rows),
            canceled=canceled,
            artifact_hashes_verified=artifacts,
            attempts=counts,
            phase_budget_consumed=used,
            cumulative_budget_consumed=summary["budget_consumed"],
            sampled_peak_aggregate_gib=max(x["aggregate_mib"] for x in peaks) / 1024,
            sampled_peak_worker_gib=max(
                max(x["worker_trees_mib"], default=0) for x in peaks
            )
            / 1024,
            rows=rows,
            comparisons=summary["comparisons"],
        ),
        indent=2,
    )
)
