"""Independent arithmetic check of retained AC dispatch comparisons; no model or solve imports."""

from pathlib import Path
import hashlib
import json
import numpy as np
from experiments.case118_counterfactual.retained_files import (
    retained_path,
    verify_recorded_source,
)

root = Path(__file__).resolve().parents[3]
prep = Path(__file__).resolve().parent
out = (
    root / "experiments/case118_counterfactual/results/ac_dispatch_adjustments_20260917"
)
study = json.loads((out / "study.json").read_text())
verify_recorded_source(study["execution"])
context = json.loads((prep / "joint-episode/context.json").read_text())
window = study["windows"]["joint-may03"]
coeff = np.array([g["cost_coeffs"] for g in context["generators"]])
aging = np.array([s["aging_weight"] for s in context["storage"]])
rows = []
for arm in ["R1", "R2", "G", "B"]:
    path = out / "joint-may03" / f"{arm}.json"
    assert path.exists(), path
    stage = json.loads(path.read_text())
    if stage["selected"] is None:
        rows.append({"arm": arm, "accepted": False})
        continue
    ref = stage["selected"]
    candidate = retained_path(ref["path"])
    assert hashlib.sha256(candidate.read_bytes()).hexdigest() == ref["sha256"]
    payload = json.loads(candidate.read_text())
    result = payload["result"]
    pg = np.array(result["Pg"])
    b = np.array(result["b"])
    # Frozen comparison has three one-hour intervals; coefficients ascending degree.
    generation = float((coeff[:, 0] + coeff[:, 1] * pg + coeff[:, 2] * pg**2).sum())
    storage = float((np.abs(b) * aging).sum())
    departure = float(np.abs(pg - window["pg_mw"]).sum())
    metrics = stage["selected_audit"]["metrics"]
    for key, value in [
        ("generation_cost", generation),
        ("storage_cost", storage),
        ("departure_mwh", departure),
    ]:
        assert abs(metrics[key] - value) < 1e-8, (arm, key, value, metrics[key])
    rows.append(
        {
            "arm": arm,
            "accepted": True,
            "generation_cost": generation,
            "storage_cost": storage,
            "common_cost": generation + storage,
            "departure_mwh": departure,
            "throughput_mwh": float(np.abs(b).sum()),
            "terminal_error_mwh": float(
                np.max(np.abs(np.array(result["soc"])[-1] - window["soc_mwh"][-1]))
            ),
            "battery_lock_error_mw": float(np.max(np.abs(b - window["battery_mw"]))),
            "selected_kind": stage["selected_kind"],
        }
    )
assert len(rows) == 4 and all(row["accepted"] for row in rows)
receipts = list(out.glob("*/*/lifecycle.json"))
assert len(receipts) == 5
for receipt in receipts:
    data = json.loads(receipt.read_text())
    assert data["reaped"]
    for ref in data["artifacts"].values():
        content = (receipt.parent / ref["relative_path"]).read_bytes()
        assert len(content) == ref["bytes"]
        assert hashlib.sha256(content).hexdigest() == ref["sha256"]
print(json.dumps(rows, indent=2))
