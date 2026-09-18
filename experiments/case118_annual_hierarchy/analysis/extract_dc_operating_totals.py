"""Extract fleet totals from the accepted toy DC archive, without any solves."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEFAULT_OUTER = ROOT / "experiments/case118_annual_hierarchy/results/s4_annual_outer_rated_attempt_005/outer-plan.json.gz"
DEFAULT_REPORT = HERE / "artifacts/final_snapshot/dispatch/report.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract(outer_path, report_path, destination):
    report = json.loads(report_path.read_text())
    outer_hash = sha(outer_path)
    if outer_hash != report["outer_sha256"]:
        raise ValueError("DC archive does not match the saved AC/DC correction source")
    with gzip.open(outer_path, "rt") as stream:
        outer = json.load(stream)
    n, ids = report["horizon"], report["storage_ids"]
    if (outer["storage_device_ids"] != ids or outer["delta_hours"] != report["delta_hours"]
            or outer["horizon_steps"] != n):
        raise ValueError("DC identity, timestep, or horizon mismatch")
    pg = np.asarray(outer["result"]["Pg"], dtype=float)
    power = np.asarray(outer["result"]["b"], dtype=float)
    soc = np.asarray(outer["boundary_soc_mwh"], dtype=float)
    if (pg.shape != (n, len(report["generator_buses"])) or power.shape != (n, len(ids))
            or soc.shape != (n + 1, len(ids))
            or not all(np.isfinite(a).all() for a in (pg, power, soc))):
        raise ValueError("Unexpected shape or nonfinite DC trajectory")
    np.testing.assert_array_equal(outer["global_boundary_indices"], np.arange(n + 1))
    np.testing.assert_array_equal(soc[1:], outer["result"]["soc"])
    np.testing.assert_allclose(soc[1:], soc[:-1] - report["delta_hours"] * power, atol=2e-4, rtol=0)
    frame = pd.DataFrame({
        "iteration": np.arange(n),
        "dc_generation_mw": pg.sum(axis=1),
        "dc_battery_power_mw": power.sum(axis=1),
        "dc_soc_end_mwh": soc[1:].sum(axis=1),
    })
    destination.mkdir(parents=True, exist_ok=False)
    csv_path = destination / "dc_operating_totals.csv"
    frame.to_csv(csv_path, index=False)
    (destination / "provenance.json").write_text(json.dumps({
        "scenario": "Completed analytical Case118 toy study, synthetic 2025 UTC",
        "outer_archive": str(outer_path), "outer_sha256": outer_hash,
        "dispatch_report_sha256": sha(report_path), "manifest_sha256": report["manifest_sha256"],
        "fixture_hashes": report["fixture_hashes"], "storage_ids": ids,
        "generator_order": report["generator_order"], "generator_buses": report["generator_buses"],
        "delta_hours": report["delta_hours"],
        "rows": n, "csv_sha256": sha(csv_path), "extractor_sha256": sha(Path(__file__)),
        "definitions": {
            "generation": "Sum of dispatchable Pg in MW; excludes renewable generation",
            "battery_power": "Fleet sum in MW; positive discharge, negative charge",
            "soc": "Fleet sum at end of each hour in MWh, plotted on that hour's start row",
        },
    }, indent=2) + "\n")
    print(csv_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outer", type=Path, default=DEFAULT_OUTER)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path, required=True, help="New directory for compact totals")
    args = parser.parse_args()
    extract(args.outer, args.report, args.output)
