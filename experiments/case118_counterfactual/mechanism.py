"""Phase-one fixed/free AC/DC study. Stops at the owner review checkpoint.

DC runs serially, followed by AC G/B chains on the existing 2+1 supervisor.
The original G arm is the fixed-battery F arm in this study's terminology.
No prescribed-transfer solves are launched by this entry point.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

from cvxopf import LayerSolveConfig
from cvxopf._hierarchical_solver import _software_versions
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    AttemptSpec,
    WindowKey,
)
from experiments.case118_annual_hierarchy.s5_speculative_process import (
    DirectCompletion,
    SubprocessBackend,
)
from experiments.case118_annual_hierarchy.s5_speculative_supervisor import MemoryPolicy
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json
from .data import ROOT, ToySource
from .dc import audit_dc
from .runner import (
    Study,
    execution_identity,
    execution_sources,
    reference,
    validate_protocol,
)
from .worker import jsonable, referenced_json


def worker_seconds(backend):
    now = time.monotonic()
    return sum(
        json.loads((c.directory / "lifecycle.json").read_text())["worker_wall_seconds"]
        if c.reaped
        else now - c.launched
        for c in backend.children.values()
    )


def selected_dc(inputs, policy, window, result, incumbent, tolerances):
    """Re-audit both choices under the destination B arm; preserve a cheaper F."""
    candidates = []
    for kind, ref in (("new", result), ("incumbent", incumbent)):
        if ref is None:
            continue
        payload = referenced_json(ref)
        if payload["window_identity"] != window.identity:
            raise ValueError("DC incumbent belongs to a different window")
        audit = audit_dc(
            inputs,
            policy,
            window,
            "B",
            payload["result"],
            tolerances,
            exception=payload["exception"],
            reported_loss_cost=payload["reported_loss_cost"],
        )
        if audit["accepted"]:
            candidates.append(
                {"selected_kind": kind, "selected": ref, "selected_audit": audit}
            )
    return (
        min(
            candidates,
            key=lambda x: (
                x["selected_audit"]["metrics"]["native_objective"],
                x["selected_kind"] != "incumbent",
            ),
        )
        if candidates
        else None
    )


class SerialDC:
    def __init__(self, study, output, *, arms=("F", "B")):
        if tuple(arms) not in (("F", "B"), ("F",)):
            raise ValueError("DC sequence must be F/B or fixed F")
        self.arms = tuple(arms)
        self.study, self.output = study, output
        self.records, self.results, self.jobs = {}, {}, {}
        self.backend = SubprocessBackend(
            output,
            cwd=ROOT,
            command=self.command,
            audit=self.audit,
            publish=lambda *args: None,
            advance=lambda *args: None,
            phase_prefix="dc",
        )

    def command(self, spec, directory):
        name, arm = self.jobs[spec.window]
        request = jsonable(
            {
                "invocation": asdict(spec),
                "window": asdict(self.study.windows[name]),
                "arm": arm,
                "dc_options": self.study.protocol["dc_options"],
                "tolerances": asdict(self.study.tolerances),
                "execution": self.study.execution,
            }
        )
        if name in self.study.battery_schedules:
            request["battery_schedule_mw"] = jsonable(
                self.study.battery_schedules[name]
            )
        atomic_immutable_json(directory / "request.json", request)
        return [
            sys.executable,
            "-m",
            "experiments.case118_counterfactual.dc",
            str(directory.resolve()),
        ]

    def audit(self, spec, directory):
        name, arm = self.jobs[spec.window]
        window = self.study.windows[name]
        request = json.loads((directory / "request.json").read_text())
        payload = json.loads((directory / "result.json").read_text())
        if (
            payload["request_sha256"] != object_sha256(request)
            or payload["window_identity"] != window.identity
            or payload["arm"] != arm
            or payload["invocation"] != asdict(spec)
        ):
            raise ValueError("DC result/request identity mismatch")
        audit = audit_dc(
            self.study.inputs,
            self.study.policy,
            window,
            arm,
            payload["result"],
            self.study.tolerances,
            exception=payload["exception"],
            reported_loss_cost=payload["reported_loss_cost"],
            battery_schedule_mw=self.study.battery_schedules.get(name),
        )
        if jsonable(audit) != payload["audit"]:
            raise ValueError("DC worker and coordinator audits disagree")
        self.results[spec] = (reference(directory / "result.json"), audit)
        return DirectCompletion(spec, "accepted" if audit["accepted"] else "rejected")

    def run(self, *, started):
        protocol = self.study.protocol
        try:
            for name, window in self.study.windows.items():
                records = self.records[name] = []
                for arm in self.arms:
                    key = WindowKey(f"{name}-{arm}", window.start)
                    self.jobs[key] = (name, arm)
                    spec = AttemptSpec(key, 0, 0)
                    if len(self.backend.children) >= protocol["max_dc_attempts"]:
                        raise RuntimeError("DC attempt budget exhausted")
                    self.backend.launch(spec)
                    self.backend.event("launched", spec, time.monotonic())
                    child = self.backend.children[spec.attempt_id]
                    while True:
                        now = time.monotonic()
                        if (
                            now - started > protocol["study_wall_seconds"]
                            or now - child.launched > protocol["worker_wall_seconds"]
                            or worker_seconds(self.backend)
                            > protocol["total_worker_seconds"]
                        ):
                            raise RuntimeError("DC phase resource budget exhausted")
                        if MemoryPolicy().hard_crossing(self.backend.memory()):
                            raise RuntimeError("DC phase hard RSS limit crossed")
                        done = self.backend.poll_audited(spec)
                        if done is not None:
                            self.backend.reap(spec)
                            self.backend.event("audited_return", spec, now)
                            break
                        time.sleep(0.2)
                    candidate = self.results.get(spec)
                    choice = None
                    if (
                        arm == "F"
                        and candidate is not None
                        and candidate[1]["accepted"]
                    ):
                        choice = {
                            "selected_kind": "new",
                            "selected": candidate[0],
                            "selected_audit": candidate[1],
                        }
                    elif arm == "B":
                        choice = selected_dc(
                            self.study.inputs,
                            self.study.policy,
                            window,
                            None if candidate is None else candidate[0],
                            records[0]["selected"],
                            self.study.tolerances,
                        )
                    record = {
                        "arm": arm,
                        "new_outcome": done.outcome,
                        **(
                            choice
                            or {
                                "selected_kind": None,
                                "selected": None,
                                "selected_audit": None,
                            }
                        ),
                    }
                    records.append(record)
                    atomic_immutable_json(
                        self.output / name / f"{arm}.json", jsonable(record)
                    )
                    if done.outcome != "accepted":
                        raise RuntimeError(
                            f"unresolved DC {name}/{arm}; no automatic retry"
                        )
        finally:
            for child in self.backend.children.values():
                if not child.reaped:
                    self.backend.cancel_and_reap(child.spec, "dc_phase_stop")
            self.backend.event("dc_phase_ended", None, time.monotonic())


def phase_comparisons(dc, ac):
    out = {}
    for name, records in dc.items():
        values = {}
        for formulation, stages, fixed_name in (
            ("dc", records, "F"),
            ("ac", ac.get(name, {}).get("stages", []), "G"),
        ):
            selected = {
                s["arm"]: s["selected_audit"]["metrics"]
                for s in stages
                if s["selected_audit"] is not None
            }
            if fixed_name in selected and "B" in selected:
                f, b = selected[fixed_name], selected["B"]
                objective = "native_objective" if formulation == "dc" else "common_cost"
                values[formulation] = {
                    "fixed": f,
                    "free": b,
                    "native_objective_improvement": f[objective] - b[objective],
                    "device_cost_improvement": f["common_cost"] - b["common_cost"],
                    "throughput_increase_mwh": b["throughput_mwh"]
                    - f["throughput_mwh"],
                }
            else:
                values[formulation] = None
        out[name] = values
    return out


def retain_source_snapshot(output, expected):
    """Retain exact reviewed worktree bytes without staging or committing."""
    sources = execution_sources()
    digest = hashlib.sha256()
    for name, content in sources.items():
        digest.update(name.encode() + b"\0" + content)
    if (
        digest.hexdigest() != expected["python_source_sha256"]
        or execution_identity() != expected
    ):
        raise ValueError("execution source changed while preparing snapshot")
    other = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "tests", "plans"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    payload = {
        "execution": expected,
        "runtime_files": {
            name: content.decode("utf-8") for name, content in sources.items()
        },
        "git_status": subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True
        ),
        "tracked_diff": subprocess.check_output(
            ["git", "diff", "HEAD", "--binary"], cwd=ROOT, text=True
        ),
        "untracked_review_files": {
            name: (ROOT / name).read_bytes().decode("utf-8")
            for name in other
            if name.endswith((".py", ".md"))
        },
    }
    atomic_immutable_json(output / "source-snapshot.json", payload)
    return reference(output / "source-snapshot.json")


def run_phase(source, protocol, output, *, snapshot_worktree=False):
    """One phase with a durable cumulative budget ledger for later step 3."""
    study = Study(source, protocol, output / "ac", arms=("G", "B"))
    serial = SerialDC(study, output / "dc")
    output.mkdir(parents=True, exist_ok=False)
    snapshot = (
        retain_source_snapshot(output, study.execution) if snapshot_worktree else None
    )
    atomic_immutable_json(
        output / "study.json",
        jsonable(
            {
                "phase": "fixed_free",
                "execution": study.execution,
                "source_snapshot": snapshot,
                "software_versions": _software_versions(),
                "protocol": protocol,
                "protocol_sha256": object_sha256(protocol),
                "windows": {k: asdict(v) for k, v in study.windows.items()},
                "arm_labels": {"ac_G": "fixed F", "ac_B": "free B"},
            }
        ),
    )
    started = time.monotonic()
    reason, ac = None, None
    try:
        serial.run(started=started)
        remaining = dict(protocol)
        remaining["study_wall_seconds"] -= time.monotonic() - started
        remaining["total_worker_seconds"] -= worker_seconds(serial.backend)
        if min(remaining["study_wall_seconds"], remaining["total_worker_seconds"]) <= 0:
            raise RuntimeError("study budget exhausted before AC phase")
        study.protocol = remaining
        ac = study.run()
        if not ac["complete"]:
            reason = ac["stop_reason"]
    except BaseException as exc:
        reason = f"{type(exc).__name__}: {exc}"
    finally:
        # Study.run handles its own cancellation; DC.run does likewise.
        summary = {
            "phase": "fixed_free",
            "complete": reason is None,
            "stop_reason": reason,
            "next": "owner_result_review_before_prescribed_transfers",
            "budget_consumed": {
                "active_wall_seconds": time.monotonic() - started,
                "total_worker_seconds": worker_seconds(serial.backend)
                + worker_seconds(study.backend),
                "dc_attempts": len(serial.backend.children),
                "ac_attempts": len(study.backend.children),
            },
            "dc_records": serial.records,
            "ac_summary": ac,
            "comparisons": phase_comparisons(
                serial.records, {} if ac is None else ac["windows"]
            ),
        }
        atomic_immutable_json(output / "summary.json", jsonable(summary))
    if execution_identity() != study.execution:
        raise RuntimeError("source changed during phase; results require disposition")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--snapshot-reviewed-worktree",
        action="store_true",
        help="retain reviewed uncommitted source instead of requiring a commit",
    )
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text())
    validate_protocol(protocol)
    LayerSolveConfig("CLARABEL", protocol["dc_options"])
    if protocol["phase"] != "fixed_free" or protocol["max_dc_attempts"] != 12:
        raise ValueError("this runner supports only the reviewed phase-one contract")
    if len(protocol["windows"]) != 3 or any(
        w["steps"] != 3 for w in protocol["windows"]
    ):
        raise ValueError("phase one requires the three selected three-hour windows")
    if (
        subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True
        ).strip()
        and not args.snapshot_reviewed_worktree
    ):
        raise ValueError("numerical launch requires a clean committed implementation")

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = run_phase(
            ToySource(),
            protocol,
            args.output.resolve(),
            snapshot_worktree=args.snapshot_reviewed_worktree,
        )
    finally:
        signal.signal(signal.SIGTERM, previous)
    if not result["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
