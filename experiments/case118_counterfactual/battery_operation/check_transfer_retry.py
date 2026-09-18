"""Read-only verification of the owner-requested June16 5MWh AC retry."""

from pathlib import Path
import hashlib
import io
import json
import subprocess
import tarfile
import numpy as np
from experiments.case118_counterfactual.data import ToySource, restore_window
from experiments.case118_counterfactual.model import ComparisonTolerances, audit_result
from experiments.case118_counterfactual.worker import referenced_json, jsonable

ROOT = Path(__file__).resolve().parents[3]
OUT = (
    ROOT
    / "experiments/case118_counterfactual/results/battery_transfer_jun16_retry_20260917"
)
plan = json.loads((OUT / "diagnostic-plan.json").read_text())
summary = json.loads((OUT / "summary.json").read_text())
assert (
    summary["clean"]
    and summary["execution_unchanged"]
    and summary["stop_reason"] is None
)
assert (
    hashlib.sha256(Path(plan["script"]["path"]).read_bytes()).hexdigest()
    == plan["script"]["sha256"]
)
snapshot = referenced_json(plan["source_snapshot"])
digest = hashlib.sha256()
for name, content in sorted(snapshot["runtime_files"].items()):
    digest.update(name.encode() + b"\0" + content.encode())
assert digest.hexdigest() == plan["execution"]["python_source_sha256"]
assert snapshot["execution"] == plan["execution"]
raw = subprocess.check_output(
    ["git", "archive", plan["execution"]["commit"], "src", "experiments"], cwd=ROOT
)
with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
    for member in archive.getmembers():
        if member.isfile() and member.name.endswith(".py"):
            assert (
                snapshot["runtime_files"][member.name].encode()
                == archive.extractfile(member).read()
            )
source = ToySource()
inputs, policy = source.fixture.inputs, source.fixture.policy
window = restore_window(plan["window"])
assert source.window(4000, 3).identity == window.identity
old = referenced_json(plan["previous_result"])
result = referenced_json(summary["result"])
request = json.loads(
    (Path(summary["result"]["path"]).parent / "request.json").read_text()
)
old_request = json.loads(
    (Path(plan["previous_result"]["path"]).parent / "request.json").read_text()
)
for key in [
    "window",
    "arm",
    "repair_budget_mwh",
    "source",
    "tolerances",
    "ac_options",
    "battery_schedule_mw",
]:
    assert request[key] == old_request[key]
assert (
    request["invocation"] == plan["invocation"]
    and request["invocation"]["source_slot"] == 6
)
assert result["result"]["status"] == "optimal"
audit = audit_result(
    inputs,
    policy,
    window,
    "G",
    result["result"],
    ComparisonTolerances(**plan["tolerances"]),
    battery_schedule_mw=plan["battery_schedule_mw"],
    reported_common_cost=result["common_cost_expression"],
    exception=result["exception"],
)
assert audit["accepted"] and jsonable(audit) == summary["audit"] == result["audit"]
fixed = referenced_json(plan["baseline"]["result"])
fa = audit_result(
    inputs,
    policy,
    window,
    "G",
    fixed["result"],
    ComparisonTolerances(**plan["tolerances"]),
    reported_common_cost=fixed["common_cost_expression"],
    exception=fixed["exception"],
)
assert fa["accepted"]
for key, value in summary["changes_from_fixed"].items():
    assert abs(audit["metrics"][key] - fa["metrics"][key] - value) < 1e-8
assert (
    abs(
        audit["metrics"]["common_cost"]
        - old["audit"]["metrics"]["common_cost"]
        - summary["cost_change_from_previous_attempt"]
    )
    < 1e-8
)
b = np.asarray(result["result"]["b"])
soc = np.asarray(result["result"]["soc"])
assert np.allclose(
    inputs.delta * (b - window.battery_mw).sum(axis=1), [-5, 0, 5], rtol=0, atol=1e-8
)
assert (
    np.max(np.abs(soc - (window.soc_mwh[0] - inputs.delta * np.cumsum(b, axis=0))))
    < 1e-4
)
assert np.max(np.abs(soc[-1] - window.soc_mwh[-1])) < 1e-3
receipts = list(OUT.glob("ac/*/*/lifecycle.json"))
assert len(receipts) == 1
receipt = json.loads(receipts[0].read_text())
assert (
    receipt["reaped"]
    and receipt["returncode"] == 0
    and receipt["completion"]["outcome"] == "accepted"
)
for ref in receipt["artifacts"].values():
    data = (receipts[0].parent / ref["relative_path"]).read_bytes()
    assert (
        len(data) == ref["bytes"] and hashlib.sha256(data).hexdigest() == ref["sha256"]
    )
assert (
    abs(
        receipt["worker_wall_seconds"]
        - summary["phase_budget_consumed"]["total_worker_seconds"]
    )
    < 1e-8
)
assert all(
    abs(plan["previous_cumulative_budget"][k] + v - summary["budget_consumed"][k])
    < 1e-8
    for k, v in summary["phase_budget_consumed"].items()
)
progress = json.loads((OUT / "ac/supervisor-progress.json").read_text())
print(
    json.dumps(
        {
            "accepted": True,
            "status": "optimal",
            "artifact_hashes_verified": len(receipt["artifacts"]),
            "committed_runtime_unchanged": True,
            "changes_from_fixed": summary["changes_from_fixed"],
            "cost_change_from_previous_attempt": summary[
                "cost_change_from_previous_attempt"
            ],
            "phase_budget_consumed": summary["phase_budget_consumed"],
            "cumulative_budget_consumed": summary["budget_consumed"],
            "peak_sampled_worker_gib": max(
                max(x["worker_trees_mib"], default=0)
                for x in progress["memory_samples"]
            )
            / 1024,
            "peak_sampled_aggregate_gib": max(
                x["aggregate_mib"] for x in progress["memory_samples"]
            )
            / 1024,
        },
        indent=2,
    )
)
