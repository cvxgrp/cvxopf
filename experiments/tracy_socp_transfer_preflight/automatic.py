"""Non-solving retrospective back-test of automatic loop discovery.

Run: uv run --extra dev python -m experiments.tracy_socp_transfer_preflight.automatic --backtest
All evidence is printed to stdout; historical experiment artifacts are read-only.
"""

import argparse
from collections import Counter
import json
import os
from pathlib import Path

import numpy as np

from experiments.tracy_socp_transfer_preflight import preflight as p
from experiments.tracy_socp_transfer_preflight import automatic_loops as automatic


def frozen_checkpoints():
    paths = [
        p.HERE / name
        for name in (
            "preflight.py",
            "test_preflight.py",
            "PROTOCOL.md",
            "REPORT.md",
            "EVIDENCE.json",
        )
    ]
    paths += [
        Path(p.loop.__file__).parent / name
        for name in (
            "analyze.py",
            "EVIDENCE.json",
            "EVIDENCE_ENDPOINT_FIX.json",
            "REPORT.md",
        )
    ]
    return {str(path.relative_to(p.ROOT)): p.transfer.sha(path) for path in paths}


def backtest(progress=None, *, max_length=4, max_boxes=64):
    with p.no_optimization():
        data = p.transfer.source_inventory()
        prepared = p.e3_prepared.verified_inputs()
        binding, before = p.historical_binding(data)
        frozen = frozen_checkpoints()
        report = dict(
            policy=dict(
                report_only=True,
                new_optimizer_calls=0,
                discovery="all simple cycles of length 3 through configured maximum",
                max_cycle_length=max_length,
                max_boxes_per_triggered_loop_hour=max_boxes,
                trigger="at least one positive necessary directed export bound",
                no_certificate_means="unresolved, not feasible",
                validation="retrospective consistency; not holdout prediction",
            ),
            sources=dict(
                scanner_sha256=p.transfer.sha(Path(automatic.__file__)),
                backtest_sha256=p.transfer.sha(Path(__file__)),
                protocol_sha256=p.transfer.sha(p.HERE / "AUTOMATIC_PROTOCOL.md"),
                transfer_binding_sha256=p.transfer.sha(
                    p.starts.ORIGIN / "binding.json"
                ),
                historical_inventory_sha256=p.transfer.object_hash(before),
                frozen_checkpoints=frozen,
            ),
            transfers=[],
            ac_controls=[],
        )
        saved = p.transfer.read(p.starts.ORIGIN / "report.json")
        for job in p.transfer.jobs(data):
            view, outcome = p.replay_transfer(binding, data, prepared, job["id"])
            build, kwargs, schedule, _, row, _ = view
            if saved["attempts"][job["id"] - 1]["compatible"] != outcome["compatible"]:
                raise ValueError("retained transfer eligibility differs")
            scanner = automatic.LoopScanner(
                kwargs, max_length=max_length, max_boxes=max_boxes
            )
            screen = scanner.scan_schedule(schedule, row["start"])
            if outcome["compatible"] and screen["verdict"] == "certified_incompatible":
                raise ValueError(
                    "automatic certificate contradicts audited retained witness"
                )
            report["transfers"].append(
                dict(
                    job=job,
                    historical_outcome=outcome,
                    source_schedule_sha256=row["schedule_sha256"],
                    mathematical_input_sha256=row["mathematical_input_sha256"],
                    **screen,
                )
            )
            if job["donor"] == "socp":
                reference = data["cases"][job["scenario"]]["ac"]
                control = scanner.scan_schedule(
                    np.asarray(reference["result"]["b"], float), row["start"]
                )
                if control["verdict"] == "certified_incompatible":
                    raise ValueError(
                        "automatic certificate contradicts accepted AC control"
                    )
                report["ac_controls"].append(
                    dict(
                        scenario=job["scenario"],
                        source=p.transfer.source_reference(data, reference),
                        **control,
                    )
                )
            if build.prob.status is not None and outcome["label"] == "timeout":
                raise ValueError("timeout graph unexpectedly solved during replay")
            if progress:
                exclusions = [
                    (h["hour"], r["cycle"])
                    for h in screen["hours"]
                    for r in h["loops"]
                    if r["status"] == "certified_incompatible"
                ]
                progress(
                    f"{job['id']}/18 {outcome['label']}: {screen['verdict']} {exclusions}"
                )
        if before != p.starts.origin_hashes(data) or frozen != frozen_checkpoints():
            raise ValueError("historical bytes changed during back-test")
        report["historical_bytes_unchanged"] = True
        rows = report["transfers"]
        report["summary"] = dict(
            retained_transfers=len(rows),
            historical_outcomes=dict(
                Counter(r["historical_outcome"]["label"] for r in rows)
            ),
            schedule_verdicts=dict(Counter(r["verdict"] for r in rows)),
            donor_hours=sum(len(r["hours"]) for r in rows),
            discovered_cycles_per_schedule=sorted(
                {r["discovered_cycles"] for r in rows}
            ),
            loop_hour_statuses=dict(
                Counter(
                    {
                        status: sum(
                            hour["loop_counts"].get(status, 0)
                            for row in rows
                            for hour in row["hours"]
                        )
                        for status in (
                            "certified_incompatible",
                            "unresolved_budget",
                            "unresolved_no_export_trigger",
                            "unsupported",
                        )
                    }
                )
            ),
            compatible_witness_contradictions=0,
            ac_controls=len(report["ac_controls"]),
            control_hours=sum(len(r["hours"]) for r in report["ac_controls"]),
            control_contradictions=0,
        )
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backtest", action="store_true", required=True)
    parser.add_argument("--max-cycle-length", type=int, default=4)
    parser.add_argument("--max-boxes", type=int, default=64)
    args = parser.parse_args()
    for name in p.original.THREAD_KEYS:
        os.environ[name] = "1"
    evidence = backtest(
        print, max_length=args.max_cycle_length, max_boxes=args.max_boxes
    )
    print("EVIDENCE_JSON", flush=True)
    print(json.dumps(evidence, separators=(",", ":"), allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
