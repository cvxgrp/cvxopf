"""Explicit hours and neighborhoods for report-only five-/six-bus escalation.

Preview by default; --run enables bounded interval checks, never OPF solves.
The entire physical input remains intact: only hours and loop work are filtered.
"""

import argparse
from collections import Counter
import json
from numbers import Integral
import os
from pathlib import Path
import time

import numpy as np

from experiments.tracy_socp_transfer_preflight import forced_subdivision as f

p = f.p
BOX_CAP = 64


def integers(values, label):
    values = tuple(values)
    if any(
        isinstance(v, (bool, np.bool_)) or not isinstance(v, Integral) for v in values
    ):
        raise ValueError(f"{label} must contain integers, not booleans or fractions")
    return sorted(set(map(int, values)))


def select_segment(kwargs, *, first_hour, hours, buses=(), branch_rows=(), hops=0):
    """Select complete long cycles touching an active-topology neighborhood.

    Branch IDs are zero-based original MATPOWER row positions, not positions
    in an active-only table. They seed both endpoints. Neighborhood distance
    counts active undirected corridors, regardless of taps or parallel members.
    """
    first_hour = integers([first_hour], "first_hour")[0]
    horizon = integers([kwargs["T"]], "T")[0]
    hops = integers([hops], "hops")[0]
    hours, buses, branch_rows = (
        integers(values, label)
        for values, label in (
            (hours, "hours"),
            (buses, "buses"),
            (branch_rows, "branch_rows"),
        )
    )
    if first_hour < 0 or horizon < 1 or hops < 0:
        raise ValueError("nonnegative first hour/hops and positive horizon required")
    if not hours or any(not first_hour <= h < first_hour + horizon for h in hours):
        raise ValueError(
            "explicit nonempty global hours must lie within the source horizon"
        )
    if not buses and not branch_rows:
        raise ValueError("explicit focus buses or original branch rows required")
    case = kwargs["case"]
    adjacency = {int(row[0]): set() for row in case["bus"]}
    if any(bus not in adjacency for bus in buses):
        raise ValueError("unknown external focus bus")
    seeds = set(buses)
    for index in branch_rows:
        if not 0 <= index < len(case["branch"]):
            raise ValueError("focus branch row is outside the original branch table")
        row = case["branch"][index]
        if row[10] <= 0:
            raise ValueError("focus branch row is inactive")
        seeds.update(map(int, row[:2]))
    pairs = set()
    for row in case["branch"]:
        if row[10] > 0:
            a, b = map(int, row[:2])
            if a not in adjacency or b not in adjacency:
                raise ValueError("active branch endpoint missing from bus table")
            adjacency[a].add(b)
            adjacency[b].add(a)
            pairs.add(tuple(sorted((a, b))))
    neighborhood, frontier = set(seeds), set(seeds)
    for _ in range(hops):
        frontier = {
            other for bus in frontier for other in adjacency[bus]
        } - neighborhood
        if not frontier:
            break
        neighborhood.update(frontier)
    candidates = [
        c for c in p.short_cycles(sorted(pairs), max_length=6) if len(c) in (5, 6)
    ]
    selected = [list(c) for c in candidates if neighborhood.intersection(c)]
    return dict(
        source_horizon_hours=[first_hour, first_hour + horizon],
        requested_hours=hours,
        hour_offsets=[h - first_hour for h in hours],
        focus_buses=buses,
        focus_branch_rows=branch_rows,
        branch_row_indexing="zero-based original MATPOWER rows; active rows only",
        seed_buses=sorted(seeds),
        hops=hops,
        neighborhood_buses=sorted(neighborhood),
        loop_selection_rule="complete five-/six-bus cycle touches at least one neighborhood bus",
        candidate_cycles=len(candidates),
        selected_cycles=selected,
        omitted_cycles=len(candidates) - len(selected),
        omitted_hours=horizon - len(hours),
        selected_loop_hours=len(hours) * len(selected),
        dispatch_box_visit_cap=BOX_CAP * len(hours) * len(selected),
        geometry_root_check_cap=len(selected),
    )


def check_segment(
    kwargs,
    schedule,
    *,
    first_hour,
    hours,
    buses=(),
    branch_rows=(),
    hops=0,
    reason,
    run=False,
):
    """Non-solving numeric API; preview returns no certificate or checked hours.

    Keep schedule and all time series at their original full-horizon shapes.
    Global-hour selection controls iteration, not physical construction/SoC.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("nonempty escalation reason required")
    if type(run) is not bool:
        raise ValueError("run must be an explicit boolean")
    scope = select_segment(
        kwargs,
        first_hour=first_hour,
        hours=hours,
        buses=buses,
        branch_rows=branch_rows,
        hops=hops,
    )
    schedule = np.asarray(schedule, float)
    if (
        schedule.shape != (kwargs["T"], len(kwargs.get("storage") or []))
        or not np.isfinite(schedule).all()
    ):
        raise ValueError("finite full-horizon battery pins aligned to storage required")
    report = dict(
        policy=dict(
            report_only=True,
            new_optimizer_calls=0,
            cycle_lengths=[5, 6],
            max_boxes_per_loop_hour=BOX_CAP,
            trigger="bypassed only for selected loop-hours",
            physical_inputs="full network, original frames and pins unchanged",
            no_certificate_means="unresolved, not feasible",
        ),
        reason=reason.strip(),
        selection=scope,
        source_schedule_sha256=p.transfer.object_hash(schedule),
        mode="run" if run else "preview",
        verdict="unresolved",
        hours=[],
        computation=dict(
            actual_box_visits=0, exact_reused_searches=0, logical_box_visits=0
        ),
        summary=dict(checked_hours=0, checked_loop_hours=0, loop_statuses={}),
        disposition="not_run_preview" if not run else "no_loops_selected",
    )
    if not run or not scope["selected_cycles"]:
        return report
    with p.no_optimization():
        engine = f.LoopScanner(kwargs, max_boxes=BOX_CAP)
        try:
            engine.cycles = [tuple(c) for c in scope["selected_cycles"]]
            engine.cuts = {c: engine.cuts[c] for c in engine.cycles}
            for hour, offset in zip(scope["requested_hours"], scope["hour_offsets"]):
                report["hours"].append(
                    dict(
                        hour=hour,
                        source_offset=offset,
                        **engine.scan_hour(schedule[offset], offset),
                    )
                )
            statuses = Counter(
                loop["status"] for hour in report["hours"] for loop in hour["loops"]
            )
            report.update(
                disposition="completed_selected_checks",
                verdict="certified_incompatible"
                if statuses["certified_incompatible"]
                else "unresolved",
                computation=dict(
                    actual_box_visits=engine.actual_box_visits,
                    exact_reused_searches=engine.reused_searches,
                    logical_box_visits=sum(
                        loop.get("tested_boxes", 0)
                        for hour in report["hours"]
                        for loop in hour["loops"]
                    ),
                ),
                summary=dict(
                    checked_hours=len(report["hours"]),
                    checked_loop_hours=sum(statuses.values()),
                    loop_statuses=dict(statuses),
                ),
            )
        finally:
            engine.clear_caches()
    return report


def retained_segment(number, *, schedule_kind="donor", **selection):
    """Replay one chosen historical transfer, never a whole-cohort back-test."""
    if integers([number], "job")[0] not in range(1, 19):
        raise ValueError("retained job must be in [1, 18]")
    if schedule_kind not in ("donor", "ac-control"):
        raise ValueError("schedule_kind must be donor or ac-control")
    before = f.long.existing_inventory()
    began, started = time.monotonic(), f.long.utc()
    with p.no_optimization():
        data = p.transfer.source_inventory()
        prepared = p.e3_prepared.verified_inputs()
        binding, historical = p.historical_binding(data)
        view, outcome = p.replay_transfer(binding, data, prepared, number)
        _, kwargs, pins, _, row, _ = view
        job = next(job for job in p.transfer.jobs(data) if job["id"] == number)
        source = None
        if schedule_kind == "ac-control":
            ref = data["cases"][job["scenario"]]["ac"]
            pins = np.asarray(ref["result"]["b"], float)
            source = p.transfer.source_reference(data, ref)
        report = check_segment(kwargs, pins, first_hour=row["start"], **selection)
        if (outcome["compatible"] or schedule_kind == "ac-control") and report[
            "verdict"
        ] == "certified_incompatible":
            raise ValueError(
                "selected certificate contradicts retained accepted witness"
            )
        report["retained"] = dict(
            job=job,
            schedule_kind=schedule_kind,
            historical_donor_outcome=outcome,
            ac_control_source=source,
            mathematical_input_sha256=row["mathematical_input_sha256"],
        )
        if historical != p.starts.origin_hashes(data):
            raise ValueError("historical artifacts changed during selected analysis")
    if before != {path: p.transfer.sha(p.ROOT / path) for path in before}:
        raise ValueError(
            "existing source or study artifact changed during selected analysis"
        )
    report.update(
        sources=dict(
            selector_sha256=p.transfer.sha(Path(__file__)),
            forced_scanner_sha256=p.transfer.sha(Path(f.__file__)),
            multigraph_scanner_sha256=p.transfer.sha(Path(f.m.scanner.__file__)),
            protocol_sha256=p.transfer.sha(p.HERE / "SEGMENT_PROTOCOL.md"),
            historical_inventory_sha256=p.transfer.object_hash(historical),
        ),
        existing_files_verified=before,
        existing_results_unchanged=True,
        historical_bytes_unchanged=True,
        execution=dict(
            started_utc=started,
            finished_utc=f.long.utc(),
            elapsed_seconds=time.monotonic() - began,
        ),
    )
    return report


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--job", type=int, choices=range(1, 19), required=True)
    result.add_argument("--schedule", choices=("donor", "ac-control"), default="donor")
    result.add_argument(
        "--hours",
        type=int,
        nargs="+",
        required=True,
        help="exact global input-hour indexes",
    )
    result.add_argument(
        "--buses", type=int, nargs="+", default=[], help="external MATPOWER bus IDs"
    )
    result.add_argument(
        "--branch-rows",
        type=int,
        nargs="+",
        default=[],
        help="zero-based original active branch rows; seed both endpoints",
    )
    result.add_argument(
        "--hops",
        type=int,
        default=0,
        help="active-corridor distance from focus seeds (default: 0)",
    )
    result.add_argument(
        "--reason", required=True, help="evidence motivating explicit escalation"
    )
    result.add_argument(
        "--run",
        action="store_true",
        help="execute selected interval checks; otherwise preview only",
    )
    return result


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if not args.buses and not args.branch_rows:
        cli.error("specify --buses or --branch-rows (or both)")
    if args.hops < 0 or not args.reason.strip():
        cli.error("nonnegative --hops and nonempty --reason required")
    for key in p.original.THREAD_KEYS:
        os.environ[key] = "1"
    try:
        report = retained_segment(
            args.job,
            schedule_kind=args.schedule,
            hours=args.hours,
            buses=args.buses,
            branch_rows=args.branch_rows,
            hops=args.hops,
            reason=args.reason,
            run=args.run,
        )
    except ValueError as exc:
        cli.error(str(exc))
    print(json.dumps(report, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
