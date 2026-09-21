"""Prescribed battery energy shifts, using the existing AC/DC models and workers."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import signal
import subprocess
import time

import numpy as np

from cvxopf import LayerSolveConfig
from cvxopf._hierarchical_solver import _software_versions
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json
from .data import ROOT, ToySource, restore_window
from .dc import audit_dc
from .mechanism import SerialDC, retain_source_snapshot, worker_seconds
from .model import ComparisonTolerances, audit_result
from .retained_files import retained_path, verify_recorded_source
from .runner import Study, execution_identity, reference, validate_protocol
from .worker import jsonable, referenced_json


def read_saved(ref):
    """Resolve relocation without weakening the original content-hash check."""
    canonical = dict(ref, path=str(retained_path(ref["path"]).resolve()))
    return referenced_json(canonical), canonical


def prescribed_schedule(inputs, policy, window, weights, energy):
    weights = np.asarray(weights, dtype=float)
    if (
        weights.shape != (len(inputs.storage),)
        or not np.isfinite(weights).all()
        or np.any(weights < 0)
        or abs(weights.sum() - 1) > 1e-12
        or isinstance(energy, bool)
        or not np.isfinite(energy)
        or energy <= 0
        or window.stop - window.start != 3
    ):
        raise ValueError("invalid three-hour transfer or storage weights")
    b = window.battery_mw.copy()
    b[0] -= energy * weights / inputs.delta
    b[2] += energy * weights / inputs.delta
    soc = window.soc_mwh[0] - inputs.delta * np.cumsum(b, axis=0)
    if (
        np.any(np.abs(b) > np.array([s.apparent_power_rating for s in inputs.storage]))
        or np.any(soc < 0)
        or np.any(soc > np.array([s.capacity for s in inputs.storage]))
        or np.max(np.abs(soc[-1] - window.soc_mwh[-1]))
        > policy.tolerances.terminal_soc_mwh_abs
    ):
        raise ValueError(
            "prescribed transfer violates storage power, state or endpoint"
        )
    return b, soc


def remaining_budget(protocol, consumed):
    result = dict(protocol)
    for setting, metric in (
        ("study_wall_seconds", "active_wall_seconds"),
        ("total_worker_seconds", "total_worker_seconds"),
        ("max_attempts", "ac_attempts"),
        ("max_dc_attempts", "dc_attempts"),
    ):
        value = consumed[metric]
        if isinstance(value, bool) or not np.isfinite(value) or value < 0:
            raise ValueError("invalid previous resource consumption")
        if metric.endswith("attempts") and not isinstance(value, int):
            raise ValueError("attempt consumption must be integral")
        result[setting] -= value
        if result[setting] <= 0:
            raise ValueError(f"previous phase exhausted {setting}")
    if min(result["max_attempts"], result["max_dc_attempts"]) < 6:
        raise ValueError("remaining budget cannot cover the six comparisons per model")
    return result


def verify_previous_work(directory, summary):
    counts, seconds = {}, 0.0
    for formulation in ("ac", "dc"):
        receipts = list((directory / formulation).glob("*/*/lifecycle.json"))
        counts[formulation + "_attempts"] = len(receipts)
        for path in receipts:
            receipt = json.loads(path.read_text())
            if not receipt["reaped"]:
                raise ValueError("previous worker has no completed lifecycle")
            seconds += receipt["worker_wall_seconds"]
            for ref in receipt["artifacts"].values():
                artifact = path.parent / ref["relative_path"]
                if (
                    artifact.stat().st_size != ref["bytes"]
                    or reference(artifact)["sha256"] != ref["sha256"]
                ):
                    raise ValueError("previous lifecycle artifact changed")
    consumed = summary["budget_consumed"]
    if (
        any(consumed[k] != v for k, v in counts.items())
        or abs(seconds - consumed["total_worker_seconds"]) > 1e-8
    ):
        raise ValueError("previous budget differs from retained worker lifecycles")


@dataclass
class Transfers:
    settings: dict
    schedules: dict
    baselines: dict
    descriptions: dict
    consumed: dict


def prepare(source, protocol):
    if protocol["phase"] != "prescribed_transfers" or protocol["transfer_mwh"] != [
        1,
        5,
    ]:
        raise ValueError("this study requires the selected 1 and 5 MWh transfers")
    old, study_ref = read_saved(protocol["previous_study"])
    summary, summary_ref = read_saved(protocol["previous_summary"])
    directory = Path(study_ref["path"]).parent
    if (
        Path(summary_ref["path"]).parent != directory
        or not summary["complete"]
        or summary["phase"] != "fixed_free"
    ):
        raise ValueError("step 3 requires the completed fixed/free comparison")
    if object_sha256(old["protocol"]) != old["protocol_sha256"]:
        raise ValueError("previous protocol hash mismatch")
    verify_recorded_source(old["execution"])
    verify_previous_work(directory, summary)
    settings = remaining_budget(old["protocol"], summary["budget_consumed"])
    plans, _ = read_saved(protocol["historical_plans"])
    checks, _ = read_saved(protocol["transfer_prechecks"])
    inputs, policy = source.fixture.inputs, source.fixture.policy
    tolerances = ComparisonTolerances(**settings["tolerances"])
    selected = settings["windows"]
    if [x["start"] for x in selected] != [2944, 3400, 4000] or any(
        x["steps"] != 3 for x in selected
    ):
        raise ValueError("only the three owner-selected windows are in scope")
    schedules, baselines, descriptions, expanded = {}, {}, {}, []
    for spec in selected:
        name = spec["id"]
        window = source.window(spec["start"], spec["steps"])
        if restore_window(old["windows"][name]).identity != window.identity:
            raise ValueError("previous comparison differs from authoritative DC inputs")
        historical = next(p for p in plans if p["iteration"] == window.start)
        precheck = next(p for p in checks if p["start"] == window.start)
        ids = list(inputs.storage_device_ids)
        if historical["battery_ids"] != ids or precheck["storage_ids"] != ids:
            raise ValueError("transfer weights have different storage identities")
        b = np.asarray(historical["ac_b_mw"])
        quantities = np.maximum(0, np.minimum(-b[0], b[2])) * inputs.delta
        weights = np.asarray(precheck["weights"])
        if quantities.sum() <= 0 or not np.allclose(
            weights, quantities / quantities.sum(), rtol=0, atol=1e-14
        ):
            raise ValueError(
                "transfer weights differ from the retained historical direction"
            )
        fixed = {}
        for formulation, arm in (("ac", "G"), ("dc", "F")):
            record_path = directory / formulation / name / f"{arm}.json"
            record = json.loads(record_path.read_text())
            payload, ref = read_saved(record["selected"])
            if payload["window_identity"] != window.identity or payload["arm"] != arm:
                raise ValueError("baseline belongs to a different window or comparison")
            kwargs = (
                {"reported_common_cost": payload["common_cost_expression"]}
                if formulation == "ac"
                else {"reported_loss_cost": payload["reported_loss_cost"]}
            )
            audit = (audit_result if formulation == "ac" else audit_dc)(
                inputs,
                policy,
                window,
                arm,
                payload["result"],
                tolerances,
                exception=payload["exception"],
                **kwargs,
            )
            if not audit["accepted"] or jsonable(audit) != record["selected_audit"]:
                raise ValueError("retained fixed baseline failed independent re-audit")
            fixed[formulation] = {
                "record": reference(record_path),
                "result": ref,
                "metrics": audit["metrics"],
            }
        _, context_ref = read_saved(spec["context"])
        for energy in protocol["transfer_mwh"]:
            job = f"{name}-{energy}mwh"
            schedule, soc = prescribed_schedule(inputs, policy, window, weights, energy)
            expected = next(
                x for x in precheck["checks"] if x["transfer_mwh"] == energy
            )
            if not np.allclose(
                schedule, expected["b_mw"], rtol=0, atol=1e-12
            ) or not np.allclose(soc, expected["soc_mwh"], rtol=0, atol=1e-10):
                raise ValueError(
                    "prescribed schedule differs from pre-outcome precheck"
                )
            schedules[job], baselines[job] = schedule, fixed
            descriptions[job] = {
                "window": name,
                "transfer_mwh": energy,
                "weights": weights,
                "soc_mwh": soc,
                "historical_archive_sha256": historical["archive_sha256"],
            }
            expanded.append(dict(spec, id=job, context=context_ref))
    settings["windows"] = expanded
    settings["phase"] = "prescribed_transfers"
    settings["scope"] = (
        "Six fixed-schedule DC solves followed by six AC comparisons; retained F solutions supply baselines and AC starts only."
    )
    validate_protocol(settings)
    LayerSolveConfig("CLARABEL", settings["dc_options"])
    return Transfers(
        settings, schedules, baselines, descriptions, summary["budget_consumed"]
    )


def comparisons(prepared, dc, ac):
    results = {}
    for name, description in prepared.descriptions.items():
        energy = description["transfer_mwh"]
        result = {"window": description["window"], "transfer_mwh": energy}
        for formulation, stages in (
            ("dc", dc.get(name, [])),
            ("ac", ac.get(name, {}).get("stages", [])),
        ):
            current = next(
                (
                    s["selected_audit"]["metrics"]
                    for s in stages
                    if s["selected_audit"] is not None
                ),
                None,
            )
            if current is None:
                result[formulation] = None
                continue
            fixed = prepared.baselines[name][formulation]["metrics"]
            objective = "common_cost" if formulation == "ac" else "native_objective"
            changes = {
                key: current[key] - fixed[key]
                for key in (
                    "generation_cost",
                    "storage_cost",
                    "common_cost",
                    "throughput_mwh",
                    "branch_loss_mwh" if formulation == "ac" else "dc_loss_proxy_cost",
                )
            }
            result[formulation] = {
                "fixed": fixed,
                "prescribed": current,
                "changes": changes,
                "native_objective_change": current[objective] - fixed[objective],
                "native_objective_change_per_mwh": (
                    current[objective] - fixed[objective]
                )
                / energy,
                "device_cost_change_per_mwh": changes["common_cost"] / energy,
            }
        results[name] = result
    return results


def run(source, protocol, output, *, snapshot_worktree=False):
    prepared = prepare(source, protocol)
    study = Study(
        source,
        prepared.settings,
        output / "ac",
        arms=("G",),
        battery_schedules=prepared.schedules,
        initial_sources={
            name: values["ac"]["result"] for name, values in prepared.baselines.items()
        },
    )
    serial = SerialDC(study, output / "dc", arms=("F",))
    output.mkdir(parents=True, exist_ok=False)
    snapshot = (
        retain_source_snapshot(output, study.execution) if snapshot_worktree else None
    )
    atomic_immutable_json(
        output / "study.json",
        jsonable(
            {
                "phase": "prescribed_transfers",
                "protocol": protocol,
                "protocol_sha256": object_sha256(protocol),
                "execution": study.execution,
                "source_snapshot": snapshot,
                "software_versions": _software_versions(),
                "effective_protocol": prepared.settings,
                "previous_budget_consumed": prepared.consumed,
                "windows": {k: asdict(v) for k, v in study.windows.items()},
                "battery_schedules_mw": prepared.schedules,
                "transfers": prepared.descriptions,
                "baselines": prepared.baselines,
            }
        ),
    )
    started, reason, ac = time.monotonic(), None, None
    try:
        serial.run(started=started)
        study.protocol = dict(prepared.settings)
        study.protocol["study_wall_seconds"] -= time.monotonic() - started
        study.protocol["total_worker_seconds"] -= worker_seconds(serial.backend)
        if (
            min(
                study.protocol["study_wall_seconds"],
                study.protocol["total_worker_seconds"],
            )
            <= 0
        ):
            raise RuntimeError("remaining study budget exhausted before AC phase")
        ac = study.run()
        if not ac["complete"]:
            reason = ac["stop_reason"]
    except BaseException as exc:
        reason = f"{type(exc).__name__}: {exc}"
    finally:
        used = {
            "active_wall_seconds": time.monotonic() - started,
            "total_worker_seconds": worker_seconds(serial.backend)
            + worker_seconds(study.backend),
            "dc_attempts": len(serial.backend.children),
            "ac_attempts": len(study.backend.children),
        }
        summary = {
            "phase": "prescribed_transfers",
            "complete": reason is None,
            "stop_reason": reason,
            "phase_budget_consumed": used,
            "budget_consumed": {k: v + prepared.consumed[k] for k, v in used.items()},
            "dc_records": serial.records,
            "ac_summary": ac,
            "comparisons": comparisons(
                prepared, serial.records, {} if ac is None else ac["windows"]
            ),
        }
        atomic_immutable_json(output / "summary.json", jsonable(summary))
    if execution_identity() != study.execution:
        raise RuntimeError(
            "source changed during execution; results require disposition"
        )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot-reviewed-worktree", action="store_true")
    args = parser.parse_args()
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
        summary = run(
            ToySource(),
            json.loads(args.protocol.read_text()),
            args.output.resolve(),
            snapshot_worktree=args.snapshot_reviewed_worktree,
        )
    finally:
        signal.signal(signal.SIGTERM, previous)
    if not summary["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
