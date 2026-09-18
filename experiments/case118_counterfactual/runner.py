"""Explicitly configured 2+1 matched-window execution, using the S5 supervisor.

This CLI is for a separately authorized numerical launch, after code and the
episode/window protocol are reviewed and committed. Importing it runs nothing.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

import numpy as np

from cvxopf import LayerSolveConfig
from cvxopf._hierarchical_solver import _software_versions
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.s5_speculative_attempt import (
    load_retained_start,
)
from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    AttemptSpec,
    Completion,
    WindowKey,
    WindowRace,
    normalized_violation,
)
from experiments.case118_annual_hierarchy.s5_speculative_process import (
    SubprocessBackend,
)
from experiments.case118_annual_hierarchy.s5_speculative_supervisor import (
    SpeculativeSupervisor,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    sha256_path,
)
from .data import ROOT, ToySource
from .model import ARMS, ComparisonTolerances, audit_result, cost_and_changes
from .worker import jsonable


class FixedBatteryRace(WindowRace):
    """Approved adaptation: explicit center perturbations, no redundant TF solve.

    Slots 6/7/8 retain the historical scales, seeds, 300s assistance, secondary
    replay, and final persistence. Here their center is explicitly the primary
    start/prior-arm incumbent, NEVER a preceding-hour controller. TF and all
    dependent slots are explicitly inapplicable, not silently missing starts.
    """

    def __init__(self, window, *, has_preceding=False):
        super().__init__(window, has_preceding=True)

    def next_helper(self):
        if self._next_order == 4:
            for order in range(4, 9):
                self.unavailable[order] = (
                    "fixed_battery_target_free_and_dependents_inapplicable"
                )
            self._next_order = 9
            self._target_free_retry_considered = True
        return super().next_helper()

    def next_final(self):
        if (
            self.winner is not None
            or self._next_order <= 8
            or not self._replay_considered
        ):
            return None
        secondary = next((s for s in self.launched.values() if s.order == 9), None)
        if not (
            self.primary.attempt_id in self.completed
            or secondary is None
            or secondary.attempt_id in self.completed
        ):
            return None
        self._final_started = True
        while self._final_next_index < 3:
            index = self._final_next_index
            self._final_next_index += 1
            slot = 6 + index
            if secondary is not None and slot == secondary.source_slot:
                self.unavailable[11 + index] = "already_exercised_by_secondary"
                continue
            predecessors = [
                x
                for x in self.completed.values()
                if x.attempt.source_slot == slot
                and x.complete_x0_retained
                and x.outcome != "accepted"
            ]
            replay = (
                max(predecessors, key=lambda x: x.attempt.order).attempt.attempt_id
                if predecessors
                else None
            )
            spec = AttemptSpec(self.window, 11 + index, slot, replay)
            self.launched[spec.attempt_id] = spec
            return spec
        for order in range(14, 19):
            self.unavailable[order] = (
                "fixed_battery_target_free_and_dependents_inapplicable"
            )
        self._final_next_index = 8
        return None


def execution_sources():
    """Include new nonignored experiment modules before their first commit."""
    names = subprocess.check_output(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "src",
            "experiments",
        ],
        cwd=ROOT,
        text=True,
    ).splitlines()
    return {
        name: (ROOT / name).read_bytes()
        for name in sorted(set(names))
        if name.endswith(".py")
    }


def execution_identity():
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    digest = hashlib.sha256()
    for name, content in execution_sources().items():
        digest.update(name.encode() + b"\0" + content)
    return {"commit": commit, "python_source_sha256": digest.hexdigest()}


def reference(path):
    return {"path": str(path.resolve()), "sha256": sha256_path(path)}


def reassess(inputs, policy, window, arm, payload, tolerances, budget):
    """Evaluate a retained physical candidate under a destination arm's rules."""
    result = dict(payload["result"])
    metrics = cost_and_changes(inputs, window, result)
    result["objective"] = (
        metrics["departure_mwh"] if arm == "R1" else metrics["common_cost"]
    )
    return audit_result(
        inputs,
        policy,
        window,
        arm,
        result,
        tolerances,
        repair_budget_mwh=budget,
        reported_common_cost=payload["common_cost_expression"],
        exception=payload["exception"],
    )


def choose_candidate(new, incumbent):
    """Both arguments are destination-audited (reference, audit) pairs."""
    available = [
        (kind, x)
        for kind, x in (("new", new), ("incumbent", incumbent))
        if x is not None and x[1]["accepted"]
    ]
    if not available:
        return None
    # Ties preserve the incumbent. Report exact changes, not a post-hoc threshold.
    return min(
        available,
        key=lambda item: (item[1][1]["metrics"]["common_cost"], item[0] != "incumbent"),
    )


def comparisons(stages):
    selected = {
        x["arm"]: x["selected_audit"]["metrics"]
        for x in stages
        if x["selected_audit"] is not None
    }
    gains = {}
    for first, last in (("R2", "G"), ("G", "B"), ("R2", "B")):
        if first not in selected or last not in selected:
            gains[f"{first}_minus_{last}"] = None
            continue
        a, b = selected[first], selected[last]
        difference = a["common_cost"] - b["common_cost"]
        baseline = selected.get("R2", {}).get("common_cost")
        gains[f"{first}_minus_{last}"] = {
            "common_cost": difference,
            "generation_cost": a["generation_cost"] - b["generation_cost"],
            "storage_cost": a["storage_cost"] - b["storage_cost"],
            "throughput_mwh": a["throughput_mwh"] - b["throughput_mwh"],
            "percent_of_R2": 100 * difference / baseline if baseline else None,
            "per_served_mwh": difference / a["served_load_mwh"]
            if a["served_load_mwh"]
            else None,
        }
    return gains


def validate_protocol(protocol):
    if protocol["fixed_battery_initialization"] != "explicit_start_perturbations":
        raise ValueError(
            "protocol must bind the owner-approved fixed-battery adaptation"
        )
    ComparisonTolerances(**protocol["tolerances"])
    LayerSolveConfig("IPOPT", protocol["ac_options"])
    for key in (
        "epsilon_repair_mwh",
        "study_wall_seconds",
        "worker_wall_seconds",
        "total_worker_seconds",
        "max_attempts",
    ):
        value = protocol[key]
        if (
            isinstance(value, bool)
            or not np.isfinite(value)
            or value < 0
            or (key != "epsilon_repair_mwh" and value == 0)
        ):
            raise ValueError(f"invalid protocol {key}")
    if not isinstance(protocol["max_attempts"], int):
        raise ValueError("max_attempts must be an integer")
    if not protocol["windows"]:
        raise ValueError("protocol must select windows after episode review")
    ids = [x["id"] for x in protocol["windows"]]
    if len(ids) != len(set(ids)) or any(
        not x
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for c in x
        )
        for x in ids
    ):
        raise ValueError("window IDs must be unique safe path components")
    for w in protocol["windows"]:
        if not w["selection_reason"] or not w["context"]:
            raise ValueError(
                "every solve window requires context and selection rationale"
            )
        path = Path(w["context"]["path"])
        if sha256_path(path) != w["context"]["sha256"]:
            raise ValueError("selected episode context hash mismatch")
        context = json.loads(path.read_text())
        if (
            not context["start"]
            <= w["start"]
            < w["start"] + w["steps"]
            <= context["stop"]
        ):
            raise ValueError("solve window must lie inside reviewed episode context")


class Study:
    def __init__(
        self,
        source,
        protocol,
        output,
        *,
        arms=ARMS,
        battery_schedules=None,
        initial_sources=None,
    ):
        if tuple(arms) not in (ARMS, ("G", "B"), ("G",)):
            raise ValueError("supported chains are R1/R2/G/B, G/B or fixed G")
        self.battery_schedules = battery_schedules or {}
        self.initial_sources = initial_sources or {}
        if (self.battery_schedules or self.initial_sources) and tuple(arms) != ("G",):
            raise ValueError("prescribed schedules and external starts require fixed G")
        self.arms = tuple(arms)
        self.source, self.protocol, self.output = source, protocol, output
        self.inputs, self.policy = source.fixture.inputs, source.fixture.policy
        self.tolerances = ComparisonTolerances(**protocol["tolerances"])
        self.execution = execution_identity()
        self.windows = {
            x["id"]: source.window(x["start"], x["steps"]) for x in protocol["windows"]
        }
        if (set(self.battery_schedules) | set(self.initial_sources)) - set(
            self.windows
        ):
            raise ValueError("schedule or initialization names an unknown window")
        for selected in protocol["windows"]:
            context = json.loads(Path(selected["context"]["path"]).read_text())
            window = self.windows[selected["id"]]
            if (
                context["input_sha256"] != window.input_sha256
                or context["outer_sha256"] != window.provenance["outer_sha256"]
                or context["manifest_sha256"] != window.provenance["manifest_sha256"]
            ):
                raise ValueError(
                    "episode context belongs to a different source fixture"
                )
        self.stages = {name: [] for name in self.windows}
        self.jobs, self.candidates, self.pending = {}, {}, {}
        self.backend = SubprocessBackend(
            output,
            cwd=ROOT,
            command=self.command,
            audit=self.audit,
            publish=self.publish,
            advance=self.advance,
        )
        self.supervisor = SpeculativeSupervisor(self.backend, race_factory=self.race)

    def race(self, key, *, has_preceding):
        cls = WindowRace if self.jobs[key]["arm"] == "B" else FixedBatteryRace
        return cls(key, has_preceding=False)

    def admit(self, name):
        stage_index = len(self.stages[name])
        arm = self.arms[stage_index]
        prior = self.stages[name][-1] if stage_index else None
        r1 = self.stages[name][0] if stage_index else None
        budget = None
        if arm == "R2":
            if r1["selected_audit"] is None:
                raise ValueError("R2 requires accepted R1 repair witness")
            budget = (
                r1["selected_audit"]["metrics"]["departure_mwh"]
                + self.protocol["epsilon_repair_mwh"]
            )
        key = WindowKey(f"{name}-{arm}", self.windows[name].start)
        self.jobs[key] = {
            "name": name,
            "arm": arm,
            "budget": budget,
            "incumbent": None if prior is None else prior["selected"],
        }
        self.supervisor.add_window(key, has_preceding=False, now=time.monotonic())

    def command(self, spec, directory):
        if len(self.backend.children) > self.protocol["max_attempts"]:
            raise RuntimeError("study attempt budget exhausted")
        job = self.jobs[spec.window]
        source_ref = self.initial_sources.get(job["name"], job["incumbent"])
        if spec.source_slot in (2, 3, 4, 5):
            possible = [
                (s, ref)
                for s, (ref, audit) in self.candidates.items()
                if s.window == spec.window and not s.hard_target and audit["accepted"]
            ]
            if not possible:
                raise ValueError("target-free recovery source unavailable")
            source_ref = max(possible, key=lambda item: item[0].order)[1]
        replay = replay_request = None
        if spec.replay_of is not None:
            previous = self.backend.children[spec.replay_of].directory
            replay = reference(previous / "start.json")
            replay_request = reference(previous / "request.json")
            # Replay uses the original source, even if a later TF solve returned.
            source_ref = json.loads((previous / "request.json").read_text())["source"]
        request = jsonable(
            {
                "invocation": asdict(spec),
                "window": asdict(self.windows[job["name"]]),
                "arm": job["arm"],
                "repair_budget_mwh": job["budget"],
                "source": source_ref,
                "replay": replay,
                "replay_request": replay_request,
                "tolerances": asdict(self.tolerances),
                "ac_options": self.protocol["ac_options"],
                "execution": self.execution,
                "initialization_policy": "B_target_free"
                if job["arm"] == "B"
                else "explicit_start_perturbations",
            }
        )
        if job["name"] in self.battery_schedules:
            request["battery_schedule_mw"] = jsonable(
                self.battery_schedules[job["name"]]
            )
        atomic_immutable_json(directory / "request.json", request)
        return [
            sys.executable,
            "-m",
            "experiments.case118_counterfactual.worker",
            str(directory.resolve()),
        ]

    def audit(self, spec, directory):
        payload = json.loads((directory / "result.json").read_text())
        request = json.loads((directory / "request.json").read_text())
        job, window = (
            self.jobs[spec.window],
            self.windows[self.jobs[spec.window]["name"]],
        )
        start = load_retained_start(directory / "start.json")
        if (
            payload["request_sha256"] != object_sha256(request)
            or start.request_sha256 != object_sha256(request)
            or payload["invocation"] != asdict(spec)
            or start.invocation != spec
            or payload["window_identity"] != window.identity
            or payload["arm"] != job["arm"]
            or payload["start_sha256"] != sha256_path(directory / "start.json")
        ):
            raise ValueError("candidate request/start/model binding mismatch")
        audit = audit_result(
            self.inputs,
            self.policy,
            window,
            job["arm"],
            payload["result"],
            self.tolerances,
            repair_budget_mwh=job["budget"],
            hard_target=spec.hard_target,
            exception=payload["exception"],
            reported_common_cost=payload["common_cost_expression"],
            battery_schedule_mw=self.battery_schedules.get(job["name"]),
        )
        if jsonable(audit) != payload["audit"]:
            raise ValueError("worker and independent coordinator audits disagree")
        if audit["accepted"]:
            values = payload["solution_values"]
            if set(values) != set(start.assigned) or any(
                np.asarray(values[k]).shape != start.assigned[k].shape
                or not np.isfinite(np.asarray(values[k], dtype=float)).all()
                for k in values
            ):
                raise ValueError(
                    "accepted candidate lacks a complete transferable solution"
                )
        self.candidates[spec] = (reference(directory / "result.json"), audit)
        score = (
            None
            if audit["accepted"]
            else normalized_violation(audit["residuals"], audit["limits"])
            if audit["limits"]
            else None
        )
        return Completion(
            spec,
            "accepted" if audit["accepted"] else "rejected",
            True,
            score,
            payload["result"].get("objective"),
        )

    def incumbent(self, key):
        job = self.jobs[key]
        ref = job["incumbent"]
        if ref is None:
            return None
        from .worker import referenced_json

        payload = referenced_json(ref)
        audit = reassess(
            self.inputs,
            self.policy,
            self.windows[job["name"]],
            job["arm"],
            payload,
            self.tolerances,
            job["budget"],
        )
        if not audit["accepted"]:
            raise ValueError("nested-arm incumbent failed destination audit")
        return ref, audit

    def publish(self, winner, attempts, directories):
        new = self.candidates[winner]
        # R1 is a repair search; subsequent arms are common-cost comparisons.
        choice = (
            ("new", new)
            if self.jobs[winner.window]["arm"] == "R1"
            else choose_candidate(new, self.incumbent(winner.window))
        )
        self.pending[winner.window] = self.record(
            winner.window, choice, winner.attempt_id
        )

    def record(self, key, choice, new_attempt):
        job = self.jobs[key]
        record = {
            "arm": job["arm"],
            "repair_budget_mwh": job["budget"],
            "new_attempt": new_attempt,
            "selected_kind": None if choice is None else choice[0],
            "selected": None if choice is None else choice[1][0],
            "selected_audit": None if choice is None else choice[1][1],
        }
        atomic_immutable_json(
            self.output / job["name"] / f"{job['arm']}.json", jsonable(record)
        )
        return record

    def advance(self, winner, directories):
        self.stages[self.jobs[winner.window]["name"]].append(
            self.pending.pop(winner.window)
        )

    def run(self):
        started = time.monotonic()
        pending_names = iter(self.windows)
        active = set()
        reason = None
        try:
            for _ in range(min(2, len(self.windows))):
                name = next(pending_names)
                active.add(name)
                self.admit(name)
            while active:
                now = time.monotonic()
                elapsed = [
                    (
                        (c.process is not None and c.process.poll() is None),
                        now - c.launched
                        if not c.reaped
                        else json.loads((c.directory / "lifecycle.json").read_text())[
                            "worker_wall_seconds"
                        ],
                    )
                    for c in self.backend.children.values()
                ]
                if (
                    now - started > self.protocol["study_wall_seconds"]
                    or any(
                        live and seconds > self.protocol["worker_wall_seconds"]
                        for live, seconds in elapsed
                    )
                    or sum(seconds for _, seconds in elapsed)
                    > self.protocol["total_worker_seconds"]
                ):
                    raise RuntimeError("declared study/worker/work budget exhausted")
                for key in self.supervisor.tick(now):
                    name = self.jobs[key]["name"]
                    if len(self.stages[name]) < len(self.arms):
                        self.admit(name)
                    else:
                        active.remove(name)
                        name = next(pending_names, None)
                        if name is not None:
                            active.add(name)
                            self.admit(name)
                time.sleep(0.2)
        except BaseException as exc:
            reason = f"{type(exc).__name__}: {exc}"
            self.supervisor.stop("counterfactual_study_stop", now=time.monotonic())
            # Never erase an already accepted feasible incumbent after a failed
            # or interrupted improvement search. No dependent work auto-starts.
            for key in set(self.jobs) - self.supervisor.finished:
                choice = choose_candidate(None, self.incumbent(key))
                job = self.jobs[key]
                if key in self.pending:
                    self.stages[job["name"]].append(self.pending.pop(key))
                elif not (self.output / job["name"] / f"{job['arm']}.json").exists():
                    self.stages[job["name"]].append(self.record(key, choice, None))
        finally:
            summary = {
                "complete": reason is None,
                "stop_reason": reason,
                "wall_seconds": time.monotonic() - started,
                "attempt_count": len(self.backend.children),
                "stage_count": sum(len(x) for x in self.stages.values()),
                "windows": {
                    name: {"stages": stages, "comparisons": comparisons(stages)}
                    for name, stages in self.stages.items()
                },
            }
            atomic_immutable_json(self.output / "summary.json", jsonable(summary))
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text())
    validate_protocol(protocol)
    if subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True
    ).strip():
        raise ValueError("numerical launch requires a clean committed implementation")
    source = ToySource()
    study = Study(source, protocol, args.output.resolve())
    args.output.mkdir(parents=True, exist_ok=False)
    atomic_immutable_json(
        args.output / "study.json",
        jsonable(
            {
                "protocol": protocol,
                "protocol_sha256": sha256_path(args.protocol),
                "execution": study.execution,
                "python": sys.version,
                "software_versions": {
                    **_software_versions(),
                    "scipy": version("scipy"),
                },
                "platform": platform.platform(),
                "windows": {name: asdict(w) for name, w in study.windows.items()},
            }
        ),
    )

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = study.run()
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    if execution_identity() != study.execution:
        raise RuntimeError("source changed during study; results require disposition")
    if not result["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
