"""Five/six-bus report-only back-tests; unchanged multigraph certificate engine.

Print losslessly compacted evidence, never write existing experiment artifacts.
One invocation per maximum cycle length preserves separate checkpoints.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
from itertools import groupby
import json
import os
from pathlib import Path
import time

from experiments.tracy_socp_transfer_preflight import multigraph as m


def utc():
    return datetime.now(timezone.utc).isoformat()


def existing_inventory():
    return {
        str(path.relative_to(m.p.ROOT)): m.p.transfer.sha(path)
        for prefix in m.p.ANALYSIS_DIRECTORIES
        for path in sorted((m.p.ROOT / prefix).rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def encode_status(codes):
    return [[code, len(list(group))] for code, group in groupby(codes)]


def decode_status(runs):
    return "".join(code * count for code, count in runs)


def compact(report):
    """Remove identical topology duplicates; RLE codes preserve every result."""
    rows = report["transfers"] + report["ac_controls"]
    names = ("cycle_catalog", "branch_groups", "loop_status_legend")
    topology = {name: rows[0][name] for name in names}
    counts = {
        str(size): sum(len(c) == size for c in topology["cycle_catalog"])
        for size in range(2, report["policy"]["max_cycle_length"] + 1)
    }
    report["topology"] = dict(topology, checks_by_length=counts)
    legend = {code: name for name, code in topology["loop_status_legend"].items()}
    report["summary"]["statuses_by_length"] = {}
    for cohort, group in (
        ("transfers", report["transfers"]),
        ("ac_controls", report["ac_controls"]),
    ):
        by_size = {
            str(size): Counter()
            for size in range(2, report["policy"]["max_cycle_length"] + 1)
        }
        for row in group:
            for name in names:
                if row.pop(name) != topology[name]:
                    raise ValueError("retained topology differs; cannot share catalog")
            for hour in row["hours"]:
                codes = hour.pop("loop_status_codes")
                runs = encode_status(codes)
                if decode_status(runs) != codes or len(codes) != len(
                    topology["cycle_catalog"]
                ):
                    raise ValueError("status compression lost evidence")
                hour["loop_status_runs"] = runs
                for code, cycle in zip(codes, topology["cycle_catalog"]):
                    by_size[str(len(cycle))][legend[code]] += 1
        report["summary"]["statuses_by_length"][cohort] = {
            size: dict(c) for size, c in by_size.items()
        }
    report["encoding"] = (
        "one shared cycle catalog; lossless [status code, repeat count] runs per hour"
    )
    return report


def backtest(max_length):
    if max_length not in (5, 6):
        raise ValueError("this checkpoint accepts only maximum lengths five or six")
    before = existing_inventory()
    began, started = time.monotonic(), utc()
    report = m.backtest(
        lambda s: print(s, flush=True), max_length=max_length, max_boxes=64
    )
    if before != {path: m.p.transfer.sha(m.p.ROOT / path) for path in before}:
        raise ValueError(
            "an existing analysis/result file changed during the back-test"
        )
    report["sources"].update(
        long_loop_driver_sha256=m.p.transfer.sha(Path(__file__)),
        preflight_reader_sha256=m.p.transfer.sha(Path(m.p.__file__)),
        historical_reader_sha256=m.p.transfer.sha(m.p.HERE / "historical_replay.py"),
        replay_source_changes_sha256=m.p.transfer.sha(
            m.p.HERE / "REPLAY_SOURCE_CHANGES.json"
        ),
    )
    report["existing_files_verified"] = before
    report["existing_results_unchanged"] = True
    report["execution"] = dict(
        started_utc=started,
        finished_utc=utc(),
        elapsed_seconds=time.monotonic() - began,
    )
    prior = m.p.transfer.read(m.p.HERE / "MULTIGRAPH_EVIDENCE.json")
    previous = {
        (row["job"]["id"], hour["hour"], tuple(loop["cycle"]))
        for row in prior["transfers"]
        for hour in row["hours"]
        for loop in hour["loops"]
        if loop["status"] == "certified_incompatible"
    }
    found = {
        (row["job"]["id"], hour["hour"], tuple(loop["cycle"]))
        for row in report["transfers"]
        for hour in row["hours"]
        for loop in hour["loops"]
        if loop["status"] == "certified_incompatible"
    }
    if not previous <= found:
        raise ValueError("expanded search lost a previously reproduced certificate")
    report["summary"]["incremental_certificates_vs_length4"] = [
        dict(job=job, hour=hour, cycle=list(cycle))
        for job, hour, cycle in sorted(found - previous)
    ]
    return compact(report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cycle-length", type=int, choices=(5, 6), required=True)
    args = parser.parse_args()
    for key in m.p.original.THREAD_KEYS:
        os.environ[key] = "1"
    report = backtest(args.max_cycle_length)
    print("EVIDENCE_JSON", flush=True)
    print(json.dumps(report, separators=(",", ":"), allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
