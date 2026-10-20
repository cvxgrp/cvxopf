"""Read-only retained back-test with parallel corridors and fixed transformers."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path

import numpy as np

from experiments.tracy_socp_transfer_preflight import preflight as p
from experiments.tracy_socp_transfer_preflight import automatic as checkpoint
from experiments.tracy_socp_transfer_preflight import multigraph_loops as scanner


def frozen_checkpoints():
    frozen = checkpoint.frozen_checkpoints()
    for name in (
        "automatic.py",
        "automatic_loops.py",
        "test_automatic.py",
        "AUTOMATIC_PROTOCOL.md",
        "AUTOMATIC_REPORT.md",
        "AUTOMATIC_EVIDENCE.json",
    ):
        path = p.HERE / name
        frozen[str(path.relative_to(p.ROOT))] = p.transfer.sha(path)
    return frozen


def backtest(progress=None, *, max_length=4, max_boxes=64):
    with p.no_optimization():
        data = p.transfer.source_inventory()
        prepared = p.e3_prepared.verified_inputs()
        binding, before = p.historical_binding(data)
        frozen = frozen_checkpoints()
        saved = p.transfer.read(p.starts.ORIGIN / "report.json")
        prior = p.transfer.read(p.HERE / "AUTOMATIC_EVIDENCE.json")
        report = dict(
            policy=dict(
                report_only=True,
                new_optimizer_calls=0,
                max_cycle_length=max_length,
                max_boxes_per_triggered_loop_hour=max_boxes,
                parallel_policy="all branches intersect one circular bus-angle interval; no equivalent line",
                transformer_policy="from voltage divided by fixed tap; signed phase added to electrical angle",
                trigger="geometry root checked for every loop, then positive necessary aggregate export",
                no_certificate_means="unresolved, not feasible",
                validation="retrospective consistency, not holdout prediction",
            ),
            sources=dict(
                scanner_sha256=p.transfer.sha(Path(scanner.__file__)),
                backtest_sha256=p.transfer.sha(Path(__file__)),
                protocol_sha256=p.transfer.sha(p.HERE / "MULTIGRAPH_PROTOCOL.md"),
                historical_inventory_sha256=p.transfer.object_hash(before),
                frozen_checkpoints=frozen,
            ),
            transfers=[],
            ac_controls=[],
        )
        for job in p.transfer.jobs(data):
            view, outcome = p.replay_transfer(binding, data, prepared, job["id"])
            _, kwargs, schedule, _, row, _ = view
            if saved["attempts"][job["id"] - 1]["compatible"] != outcome["compatible"]:
                raise ValueError("retained eligibility differs")
            engine = scanner.LoopScanner(
                kwargs, max_length=max_length, max_boxes=max_boxes
            )
            screened = engine.scan_schedule(schedule, row["start"])
            if (
                outcome["compatible"]
                and screened["verdict"] == "certified_incompatible"
            ):
                raise ValueError(
                    "certificate contradicts independently audited compatible witness"
                )
            prior_row = prior["transfers"][job["id"] - 1]
            old_unsupported = {
                tuple(prior_row["cycle_catalog"][r["cycle_index"]])
                for r in prior_row["unsupported_cycles"]
            }
            coverage = {
                cycle: next(
                    h["loop_status_codes"][engine.cycles.index(cycle)]
                    for h in screened["hours"]
                )
                for cycle in sorted(old_unsupported)
            }
            report["transfers"].append(
                dict(
                    job=job,
                    historical_outcome=outcome,
                    source_schedule_sha256=row["schedule_sha256"],
                    mathematical_input_sha256=row["mathematical_input_sha256"],
                    formerly_unsupported_coverage=[
                        dict(cycle=list(c), first_hour_status_code=v)
                        for c, v in coverage.items()
                    ],
                    **screened,
                )
            )
            if job["donor"] == "socp":
                ref = data["cases"][job["scenario"]]["ac"]
                control = engine.scan_schedule(
                    np.asarray(ref["result"]["b"], float), row["start"]
                )
                if control["verdict"] == "certified_incompatible":
                    raise ValueError("certificate contradicts accepted AC control")
                report["ac_controls"].append(
                    dict(
                        scenario=job["scenario"],
                        source=p.transfer.source_reference(data, ref),
                        **control,
                    )
                )
            if progress:
                exclusions = [
                    (h["hour"], loop["cycle"])
                    for h in screened["hours"]
                    for loop in h["loops"]
                    if loop["status"] == "certified_incompatible"
                ]
                progress(
                    f"{job['id']}/18 {outcome['label']}: {screened['verdict']} {exclusions}"
                )
        if before != p.starts.origin_hashes(data) or frozen != frozen_checkpoints():
            raise ValueError("historical artifacts or checkpoint bytes changed")
        report["historical_bytes_unchanged"] = True
        rows = report["transfers"]
        report["summary"] = dict(
            retained_transfers=len(rows),
            historical_outcomes=dict(
                Counter(r["historical_outcome"]["label"] for r in rows)
            ),
            schedule_verdicts=dict(Counter(r["verdict"] for r in rows)),
            donor_hours=sum(len(r["hours"]) for r in rows),
            simple_cycles_per_schedule=sorted({r["simple_cycles"] for r in rows}),
            parallel_corridors_per_schedule=sorted(
                {r["parallel_corridors"] for r in rows}
            ),
            loop_hour_statuses={
                status: sum(
                    h["loop_counts"].get(status, 0) for r in rows for h in r["hours"]
                )
                for status in (
                    "certified_incompatible",
                    "unresolved_budget",
                    "unresolved_no_export_trigger",
                    "unsupported",
                )
            },
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
    for key in p.original.THREAD_KEYS:
        os.environ[key] = "1"
    result = backtest(
        lambda s: print(s, flush=True),
        max_length=args.max_cycle_length,
        max_boxes=args.max_boxes,
    )
    print("EVIDENCE_JSON", flush=True)
    print(json.dumps(result, separators=(",", ":"), allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
