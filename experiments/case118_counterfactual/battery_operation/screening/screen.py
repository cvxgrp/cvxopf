"""Read-only onset screen; inspect selected original three-hour plans, no OPF solves."""

from pathlib import Path
import gzip
import hashlib
import json
import numpy as np
import pandas as pd
from experiments.case118_counterfactual.data import ToySource, ROOT

out = Path(__file__).resolve().parent
src = ToySource()
p = (
    ROOT
    / "experiments/case118_annual_hierarchy/analysis/artifacts/final_snapshot/dispatch/intervals.csv"
)
x = pd.read_csv(p)
mask = (
    (x.soc_initial_l1_mwh <= 0.001)
    & (x.battery_l1_mw >= 10)
    & (~x.operator_intervention)
)
candidates = x[mask].copy()
rows = []
plans = []
checks = {}
for _, r in candidates.iterrows():
    i = int(r.iteration)
    directory = (
        ROOT / "experiments/case118_annual_hierarchy/results/s4b_annual_ac" / r.shard
    )
    if r.shard not in checks:
        cp = directory / "checkpoint.json"
        raw = cp.read_bytes()
        c = json.loads(raw)
        assert c["outer_plan_sha256"] == src.window(i, 1).provenance["outer_sha256"]
        assert c["manifest_sha256"] == src.window(i, 1).provenance["manifest_sha256"]
        checks[r.shard] = {
            "path": str(cp),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "entries": {e["iteration"]: e for e in c["windows"]},
        }
    e = checks[r.shard]["entries"][i]
    path = directory / e["relative_path"]
    raw = path.read_bytes()
    assert (
        len(raw) == e["bytes"]
        and hashlib.sha256(raw).hexdigest() == e["sha256"] == r.archive_sha256
    )
    w = json.loads(gzip.decompress(raw))
    selected = [a for a in w["attempts"] if a["supplied_executed_action"]]
    assert len(selected) == 1
    a = selected[0]
    assert a["audit"]["accepted_primal"]
    assert (
        a["attempt_id"] == w["executed_interval"]["controlling_attempt_id"]
        and w["iteration"] == i
    )
    assert w["storage_device_ids"] == list(src.fixture.inputs.storage_device_ids)
    b = np.array(a["result"]["b"])
    soc = np.array(a["result"]["soc"])
    s0 = np.array(w["initial_soc_mwh"])
    target = np.array(w["target_soc_mwh"])
    h = len(b)
    delta = w["delta_hours"]
    assert (
        delta == 1
        and a["global_interval_start"] == i
        and a["global_interval_stop"] == i + h
    )
    assert np.max(abs(np.diff(np.vstack([s0, soc]), axis=0) + delta * b)) < 1e-4
    assert np.max(abs(soc[-1] - target)) < 1e-3
    sdc = src.soc[i : i + h + 1]
    bdc = src.battery[i : i + h]
    assert np.max(abs(target - sdc[-1])) < 1e-3
    initial = float(abs(s0 - sdc[0]).sum())
    assert abs(initial - r.soc_initial_l1_mwh) < 1e-8
    departure = float(abs(b[0] - bdc[0]).sum())
    assert abs(departure - r.battery_l1_mw) < 1e-8
    ordinal = src.boundaries.index(max(j for j in src.boundaries if j <= i))
    no_reset = i > src.boundaries[ordinal] and i + h <= src.boundaries[ordinal + 1]
    throughput = float(delta * abs(b).sum())
    required = float(abs(target - s0).sum())
    dc_tp = float(delta * abs(bdc).sum())
    dc_end = float(abs(sdc[-1] - sdc[0]).sum())
    clean = bool(
        h == 3
        and no_reset
        and required <= 0.001
        and dc_tp <= 0.001
        and throughput - required >= 20
    )
    row = {
        "iteration": i,
        "timestamp": r.timestamp,
        "shard": r.shard,
        "initial_soc_l1_mwh": initial,
        "first_battery_departure_mw": departure,
        "hours": h,
        "no_reset": no_reset,
        "endpoint_movement_l1_mwh": required,
        "dc_endpoint_movement_l1_mwh": dc_end,
        "dc_throughput_mwh": dc_tp,
        "ac_throughput_mwh": throughput,
        "excess_throughput_mwh": throughput - required,
        "planned_charge_mwh": float(-np.minimum(b, 0).sum()),
        "planned_discharge_mwh": float(np.maximum(b, 0).sum()),
        "max_planned_soc_excursion_l1_mwh": float(abs(soc - s0).sum(1).max()),
        "first_fleet_power_mw": float(b[0].sum()),
        "second_fleet_power_mw": float(b[1].sum()) if h >= 2 else None,
        "third_fleet_power_mw": float(b[2].sum()) if h >= 3 else None,
        "clean_round_trip": clean,
        "archive_path": str(path),
        "archive_sha256": e["sha256"],
        "controller": a["attempt_id"],
    }
    rows.append(row)
    plans.append(
        {
            "iteration": i,
            "archive_sha256": e["sha256"],
            "battery_ids": w["storage_device_ids"],
            "initial_soc_mwh": s0.tolist(),
            "target_soc_mwh": target.tolist(),
            "ac_b_mw": b.tolist(),
            "ac_soc_mwh": soc.tolist(),
            "dc_b_mw": bdc.tolist(),
            "dc_soc_mwh": sdc.tolist(),
            "accepted_controller": a["attempt_id"],
        }
    )
f = pd.DataFrame(rows).sort_values("first_battery_departure_mw", ascending=False)
f.to_csv(out / "screened-windows.csv", index=False)
(out / "plans.json").write_text(json.dumps(plans, indent=2) + "\n")
summary = {
    "source_csv": str(p),
    "source_csv_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
    "input_sha256": src.outer["input_fingerprint"],
    "checks": [{k: v for k, v in c.items() if k != "entries"} for c in checks.values()],
    "criteria": {
        "initial_soc_l1_max_mwh": 0.001,
        "first_battery_departure_min_mw": 10,
        "no_operator_intervention": True,
        "clean_round_trip_extra": {
            "hours": 3,
            "no_shard_reset": True,
            "endpoint_movement_l1_max_mwh": 0.001,
            "dc_throughput_max_mwh": 0.001,
            "excess_ac_throughput_min_mwh": 20,
        },
    },
    "total_intervals": len(x),
    "preliminary_candidates": len(f),
    "verified_clean_round_trip_windows": int(f.clean_round_trip.sum()),
    "clean_monthly_counts": f[f.clean_round_trip]
    .groupby(f.timestamp.str[:7])
    .size()
    .to_dict(),
    "note": "Counts are windows, not independent episodes. Original optimized plans, not stitched executed actions. Selected-window primal records verified; no dual/economic causality conclusion and no new solves.",
}
(out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps({k: v for k, v in summary.items() if k != "checks"}, indent=2))
cols = [
    "iteration",
    "timestamp",
    "initial_soc_l1_mwh",
    "endpoint_movement_l1_mwh",
    "first_fleet_power_mw",
    "second_fleet_power_mw",
    "third_fleet_power_mw",
    "ac_throughput_mwh",
    "dc_throughput_mwh",
    "clean_round_trip",
]
print(f[f.clean_round_trip][cols].head(8).to_string(index=False))
print("May3:")
print(f[f.iteration == 2944][cols].to_string(index=False))
