"""Report-only forced five-/six-bus subdivision on retained schedules.

Reuse the frozen multigraph mathematics, but replace no-export dispositions
with the same bounded proof search. No source checkpoint or solve is rewritten.
"""

from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
import json
import os
import multiprocessing
from pathlib import Path
import time

import numpy as np

from experiments.tracy_socp_transfer_preflight import long_loops as long

m, p = long.m, long.m.p


class LoopScanner(m.scanner.LoopScanner):
    """Force only long loops; all branch bounds and proof arithmetic inherited."""

    def __init__(self, kwargs, *, max_boxes=64, progress=None):
        super().__init__(kwargs, max_length=6, max_boxes=max_boxes)
        self.cycles = [c for c in self.cycles if len(c) in (5, 6)]
        self.cuts = {c: self.cuts[c] for c in self.cycles}
        self.progress = progress
        self.proof_reuse = {}
        self.actual_box_visits = self.reused_searches = 0
        # Per-instance, bounded exact memoization; never round cache keys.
        self.cached_angle = lru_cache(maxsize=32768)(super().branch_angle)

    def branch_angle(self, index, a, b, ubox, vbox, forward, reverse):
        # Only the lower endpoint enters branch_angle; provenance stays in prove.
        forward = None if forward is None else (forward[0],)
        reverse = None if reverse is None else (reverse[0],)
        interval, detail = self.cached_angle(index, a, b, ubox, vbox, forward, reverse)
        # prove may add a conflict reason; do not mutate a memoized dictionary.
        return interval, dict(detail)

    def prove(self, cycle, bounds, *, max_boxes=None):
        budget = self.max_boxes if max_boxes is None else max_boxes
        key = (
            cycle,
            budget,
            tuple(
                (edge, None if bounds[edge] is None else bounds[edge][0])
                for edge in sorted(bounds)
            ),
        )
        if key in self.proof_reuse:
            self.reused_searches += 1
            return dict(self.proof_reuse[key])
        result = super().prove(cycle, bounds, max_boxes=max_boxes)
        self.actual_box_visits += result["tested_boxes"]
        # Only inconclusive counters are reused; certificates retain current cuts.
        if result["status"] == "unresolved_budget":
            self.proof_reuse[key] = dict(result)
        return result

    def scan_hour(self, pins, offset=0):
        self.hour_bounds = {}
        result = super().scan_hour(pins, offset)
        pending = [
            r for r in result["loops"] if r["status"] == "unresolved_no_export_trigger"
        ]
        if pending:
            for record in pending:
                cycle = tuple(record["cycle"])
                bounds = self.hour_bounds[cycle]
                record.update(self.prove(cycle, bounds), export_trigger_bypassed=True)
        result["loop_counts"] = dict(Counter(r["status"] for r in result["loops"]))
        result["status"] = (
            "certified_incompatible"
            if result["loop_counts"].get("certified_incompatible", 0)
            else "unresolved"
        )
        if self.progress:
            self.progress(
                f"hour offset {offset + 1}/{self.kwargs['T']}: {result['loop_counts']}"
            )
        return result

    def export_bounds(self, cycle, values, losses):
        bounds = super().export_bounds(cycle, values, losses)
        # scan_hour already computed the exact dispatch-dependent cuts once.
        if hasattr(self, "hour_bounds"):
            self.hour_bounds[cycle] = bounds
        return bounds

    def scan_schedule(self, schedule, first_hour=0):
        result = super().scan_schedule(schedule, first_hour)
        result["computation"] = dict(
            actual_box_visits=self.actual_box_visits,
            exact_reused_searches=self.reused_searches,
            logical_box_visits=sum(
                loop["tested_boxes"]
                for hour in result["hours"]
                for loop in hour["loops"]
            ),
        )
        return result

    def clear_caches(self):
        self.cached_angle.cache_clear()
        self.proof_reuse.clear()
        # Frozen decorators key by instance; release completed engines.
        self.arc.cache_clear()
        self.thermal_upper.cache_clear()
        self.geometric_root.cache_clear()


def compact_budgets(report):
    """Losslessly group identical inconclusive counters by cycle indexes."""
    for row in report["transfers"] + report["ac_controls"]:
        for hour in row["hours"]:
            groups, certificates = {}, []
            for loop in hour["loops"]:
                if loop["status"] != "unresolved_budget":
                    certificates.append(loop)
                    continue
                detail = {
                    k: v for k, v in loop.items() if k not in ("cycle", "cycle_index")
                }
                key = json.dumps(detail, sort_keys=True)
                groups.setdefault(key, dict(**detail, cycle_indexes=[]))[
                    "cycle_indexes"
                ].append(loop["cycle_index"])
            hour["loops"] = certificates
            hour["budget_groups"] = list(groups.values())
    report["encoding"] += "; identical budget records grouped by explicit cycle indexes"
    return report


def scan_pair(number, kwargs, schedule, first_hour, control_pins):
    """Independent process: numeric interval analysis only, guarded against OPF."""
    with p.no_optimization():

        def progress(message):
            print(f"transfer {number}: {message}", flush=True)

        engine = LoopScanner(kwargs, progress=progress)
        screened = engine.scan_schedule(schedule, first_hour)
        engine.clear_caches()
        control = None
        if control_pins is not None:
            print(f"transfer {number}: AC control", flush=True)
            engine = LoopScanner(kwargs, progress=progress)
            control = engine.scan_schedule(control_pins, first_hour)
            engine.clear_caches()
        return screened, control


def backtest(progress=print):
    before = long.existing_inventory()
    began, started = time.monotonic(), long.utc()
    baseline = p.transfer.read(p.HERE / "LONG_LOOPS_6_EVIDENCE.json")
    with p.no_optimization():
        data = p.transfer.source_inventory()
        prepared = p.e3_prepared.verified_inputs()
        binding, historical = p.historical_binding(data)
        report = dict(
            policy=dict(
                report_only=True,
                new_optimizer_calls=0,
                max_cycle_length=6,
                forced_cycle_lengths=[5, 6],
                max_boxes_per_triggered_loop_hour=64,
                trigger="bypassed for all supported five-/six-bus loop-hours",
                mathematics="unchanged multigraph prove; exact memoization only",
                physical_inputs="unchanged; no OPF, new initialization, or altered pins",
                no_certificate_means="unresolved, not feasible",
                validation="retrospective consistency, not holdout prediction",
            ),
            sources=dict(
                driver_sha256=p.transfer.sha(Path(__file__)),
                scanner_sha256=p.transfer.sha(Path(m.scanner.__file__)),
                long_loop_driver_sha256=p.transfer.sha(Path(long.__file__)),
                protocol_sha256=p.transfer.sha(
                    p.HERE / "FORCED_SUBDIVISION_PROTOCOL.md"
                ),
                baseline_sha256=p.transfer.sha(p.HERE / "LONG_LOOPS_6_EVIDENCE.json"),
                historical_inventory_sha256=p.transfer.object_hash(historical),
            ),
            transfers=[],
            ac_controls=[],
        )
        with ProcessPoolExecutor(
            max_workers=4, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            pending = {}
            for job in p.transfer.jobs(data):
                view, outcome = p.replay_transfer(binding, data, prepared, job["id"])
                _, kwargs, schedule, _, row, _ = view
                prior = baseline["transfers"][job["id"] - 1]
                if outcome != prior["historical_outcome"]:
                    raise ValueError(
                        "independent historical replay differs from retained baseline"
                    )
                ref = (
                    data["cases"][job["scenario"]]["ac"]
                    if job["donor"] == "socp"
                    else None
                )
                control_pins = (
                    None if ref is None else np.asarray(ref["result"]["b"], float)
                )
                future = pool.submit(
                    scan_pair, job["id"], kwargs, schedule, row["start"], control_pins
                )
                pending[future] = (job, outcome, row, ref)
                if progress:
                    progress(f"queued verified transfer {job['id']}/18 {job['donor']}")
            for future in as_completed(pending):
                job, outcome, row, ref = pending[future]
                screened, control = future.result()
                if (
                    outcome["compatible"]
                    and screened["verdict"] == "certified_incompatible"
                ):
                    raise ValueError(
                        "certificate contradicts compatible retained transfer"
                    )
                report["transfers"].append(
                    dict(
                        job=job,
                        historical_outcome=outcome,
                        source_schedule_sha256=row["schedule_sha256"],
                        mathematical_input_sha256=row["mathematical_input_sha256"],
                        **screened,
                    )
                )
                if control is not None:
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
                    progress(
                        f"completed transfer {job['id']}/18: {screened['verdict']}"
                    )
            report["transfers"].sort(key=lambda r: r["job"]["id"])
            order = {r["job"]["scenario"]: i for i, r in enumerate(report["transfers"])}
            report["ac_controls"].sort(key=lambda r: order[r["scenario"]])
        if historical != p.starts.origin_hashes(data):
            raise ValueError("historical artifacts changed")
    if before != {path: p.transfer.sha(p.ROOT / path) for path in before}:
        raise ValueError("existing source or study artifact changed during scan")
    report.update(
        existing_files_verified=before,
        existing_results_unchanged=True,
        historical_bytes_unchanged=True,
        execution=dict(
            started_utc=started,
            finished_utc=long.utc(),
            elapsed_seconds=time.monotonic() - began,
            analysis_processes=4,
        ),
    )
    report["summary"] = dict(
        retained_transfers=len(report["transfers"]),
        ac_controls=len(report["ac_controls"]),
        donor_hours=sum(len(r["hours"]) for r in report["transfers"]),
        control_hours=sum(len(r["hours"]) for r in report["ac_controls"]),
        historical_outcomes=dict(
            Counter(r["historical_outcome"]["label"] for r in report["transfers"])
        ),
        compatible_witness_contradictions=0,
        control_contradictions=0,
    )
    return compact_budgets(long.compact(report))


def main():
    for key in p.original.THREAD_KEYS:
        os.environ[key] = "1"
    report = backtest(lambda s: print(s, flush=True))
    print("EVIDENCE_JSON", flush=True)
    print(json.dumps(report, separators=(",", ":"), allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
