"""Report-only SOCP transfer preflight and retained-outcome back-test.

No solve, retry, repair, acceptance mutation or historical artifact write.
Generic point screens reuse production diagnostics. Schedule certificates use
the frozen, explicitly limited Tracy 71--72--24--70 template, not arbitrary
network infeasibility or an out-of-sample predictor.
"""

import argparse
from contextlib import contextmanager
from decimal import Decimal
import json
import os
from pathlib import Path

import cvxpy as cp
import numpy as np

from cvxopf import build_opf_multistep
from cvxopf.socp_diagnostics import (
    SOCPAuditTolerances,
    audit_socp_relaxation,
    recover_socp_voltage,
)
from experiments.case118_tracy_2021 import e3_plus, e3_prepared
from experiments.numerical_preparation.audit import serializable
from experiments.tracy_battery_transfer import model as transfer, run as original
from experiments.tracy_battery_transfer_loop import analyze as loop
from experiments.tracy_battery_transfer_slice import model as slices, run as slice_run
from experiments.tracy_battery_transfer_starts import model as starts

HERE = Path(__file__).resolve().parent
ROOT = transfer.ROOT
BOX_BUDGET = 64
TOL = SOCPAuditTolerances()
LOOP_EVIDENCE = Path(loop.__file__).parent / "EVIDENCE_ENDPOINT_FIX.json"
ANALYSIS_DIRECTORIES = tuple(
    f"experiments/{name}/"
    for name in (
        "tracy_socp_transfer_preflight",
        "tracy_battery_transfer_loop",
        "tracy_battery_transfer_slice",
        "tracy_battery_transfer_feasibility",
    )
)


@contextmanager
def no_optimization():
    """Defense in depth: all CVXPY solve entry points must fail in this scope."""
    prior = cp.Problem.solve
    cp.Problem.solve = loop.forbid_solves
    try:
        yield
    finally:
        cp.Problem.solve = prior


def short_cycles(pairs, max_length=4):
    """Deterministic simple undirected cycles, unique up to reversal/rotation."""
    adjacency = {}
    for a, b in np.asarray(pairs, dtype=int):
        adjacency.setdefault(int(a), set()).add(int(b))
        adjacency.setdefault(int(b), set()).add(int(a))
    found = set()
    for start in sorted(adjacency):

        def visit(path):
            for node in sorted(adjacency[path[-1]]):
                if node == start and len(path) >= 3:
                    forward = tuple(path)
                    reverse = (start, *reversed(path[1:]))
                    found.add(min(forward, reverse))
                elif node > start and node not in path and len(path) < max_length:
                    visit([*path, node])

        visit([start])
    return sorted(found)


def cycle_errors(products, pairs, cycles):
    products = np.asarray(products, complex)
    lookup = {tuple(map(int, pair)): k for k, pair in enumerate(pairs)}
    residuals = np.empty((len(products), len(cycles)))
    valid = np.ones(residuals.shape, dtype=bool)
    for j, cycle in enumerate(cycles):
        total = np.zeros(len(products))
        for a, b in zip(cycle, (*cycle[1:], cycle[0])):
            edge = lookup[tuple(sorted((a, b)))]
            total += (1 if a < b else -1) * np.angle(products[:, edge])
            valid[:, j] &= np.isfinite(products[:, edge]) & (
                abs(products[:, edge]) > TOL.phase_floor
            )
        residuals[:, j] = np.where(valid[:, j], np.angle(np.exp(1j * total)), np.nan)
    return residuals, valid


def constraint_failures_at_hour(audit, offset, horizon):
    failed = []
    for name, summary in audit.get("residuals", {}).items():
        if summary.get("passed") is not False:
            continue
        values = np.asarray(summary["by_interval"])
        if len(values) == horizon:
            value = values[offset]
        elif name == "storage_terminal":
            value = values[0] if offset == horizon - 1 else 0
        else:
            value = np.max(values, initial=0)
        if not np.isfinite(value) or value > summary["tolerance"]:
            failed.append(name)
    return failed


def point_screen(build, point, first_hour):
    """Non-mutating hourly screens; never equate failed recovery with bad pins."""
    before = build.prob.status
    audit = audit_socp_relaxation(build, point)
    recovery = recover_socp_voltage(build, point)
    assert build.prob.status == before
    pairs = np.asarray(point["voltage_product_pairs"], int)
    if not np.array_equal(pairs, build.data["voltage_product_pairs"]):
        raise ValueError("retained voltage-product topology differs")
    w = np.asarray(point["w"], float)
    z = np.asarray(point["W_re"], float) + 1j * np.asarray(point["W_im"], float)
    if (
        w.shape != (build.data["T"], build.data["nb"])
        or z.shape != (build.data["T"], len(pairs))
        or not np.isfinite(w).all()
        or not np.isfinite(z).all()
        or np.any(w <= 0)
    ):
        raise ValueError("unusable SOCP primal arrays")
    i, j = pairs.T
    product = w[:, i] * w[:, j]
    signed = (product - abs(z) ** 2) / np.maximum(
        TOL.normalization_floor, np.maximum(product, abs(z) ** 2)
    )
    cycles = short_cycles(pairs)
    errors, valid = cycle_errors(z, pairs, cycles)
    external = {v: k for k, v in build.data["ext_to_int"].items()}
    input_case = build.data["_socp_audit_inputs"]["case"]
    rates = np.asarray(input_case["branch"][:, 5], float)
    rated = (input_case["branch"][:, 10] > 0) & np.isfinite(rates) & (rates > 0)
    utilization = (
        np.maximum(
            abs(np.asarray(audit["branch_from_mva"])),
            abs(np.asarray(audit["branch_to_mva"])),
        )[:, rated]
        / rates[rated]
    )
    rated_rows = np.flatnonzero(rated)
    bus = input_case["bus"]
    basis = (
        np.asarray(recovery["cycle_summary"]["by_interval"])
        if recovery["available"]
        else None
    )
    hours = []
    for h in range(len(w)):
        rank = float(np.max(abs(signed[h]), initial=0))
        top = sorted(
            np.flatnonzero(valid[h]), key=lambda k: abs(errors[h, k]), reverse=True
        )[:3]
        maximum = float(np.max(abs(errors[h, valid[h]]), initial=0))
        row = (
            int(rated_rows[int(np.argmax(utilization[h]))]) if len(rated_rows) else None
        )
        failed = constraint_failures_at_hour(audit, h, len(w))
        warning = bool(
            rank > TOL.rank_relative
            or basis is None
            or (basis is not None and basis[h] > TOL.cycle_angle)
            or not valid[h].all()
            or maximum > TOL.cycle_angle
            or failed
            or not audit["complete"]
        )
        hours.append(
            dict(
                hour=first_hour + h,
                offset=h,
                point_warning=warning,
                relaxation_constraint_failures=failed,
                max_rank_relative=rank,
                max_positive_slack=float(signed[h].max()),
                non_tight_edges=int(np.sum(abs(signed[h]) > TOL.rank_relative)),
                basis_cycle_error_rad=None if basis is None else float(basis[h]),
                max_short_cycle_error_deg=float(np.rad2deg(maximum)),
                undefined_short_cycles=int(np.sum(~valid[h])),
                top_short_cycles=[
                    dict(
                        buses=[int(external[b]) for b in cycles[k]],
                        closure_deg=float(np.rad2deg(errors[h, k])),
                        phase_defined=bool(valid[h, k]),
                    )
                    for k in top
                ],
                max_branch_utilization=float(np.max(utilization[h], initial=0)),
                limiting_branch_row=row,
                voltage_bound_active_buses=int(
                    np.sum(
                        np.minimum(
                            abs(w[h] - bus[:, 12] ** 2), abs(w[h] - bus[:, 11] ** 2)
                        )
                        <= TOL.voltage_squared
                    )
                ),
            )
        )
    return dict(
        relaxation_available=audit["available"],
        relaxation_feasible=audit.get("feasible"),
        complete=audit["complete"],
        exact_product_recovery=recovery.get("exact_product_recovery", False),
        recovered_ac_physics_feasible=recovery.get("ac_feasible", False),
        recovery_reason=recovery.get("reason"),
        hours=hours,
        short_cycle_count=len(cycles),
        basis_cycle_count=len(recovery.get("non_tree_edges", [])),
        tolerances=serializable(TOL.__dict__),
    ), recovery


def local_template_applicable(kwargs):
    # Additional guards around a deliberately frozen Tracy proof: absent
    # ratings, a new load at 71 or duplicate batteries must not silently turn
    # this topology-specific bound into a generic certificate.
    if any(load.bus == 71 for load in kwargs["loads"]):
        return False
    if any(sum(s.bus == bus for s in kwargs["storage"]) != 1 for bus in (71, 72)):
        return False
    pairs = {(70, 71), (71, 72), (24, 72), (24, 70), (71, 73)}
    selected = [
        r for r in kwargs["case"]["branch"] if r[10] > 0 and tuple(r[:2]) in pairs
    ]
    return len(selected) == 5 and all(
        np.isfinite(r[5]) and r[5] > 0 and r[2] >= 0 and r[3] > 0 for r in selected
    )


def schedule_hour(kwargs, schedule, offset, targets, max_boxes=BOX_BUDGET):
    """No model construction: slice numeric inputs for a necessary-condition check."""
    hourly, _, _ = slices.slice_inputs(kwargs, schedule, offset, targets)
    if not local_template_applicable(hourly):
        return dict(
            offset=offset,
            status="template_not_applicable",
            reason="local asset/rating assumptions differ from frozen Tracy template",
        )
    try:
        first = loop.prove_loop(hourly, schedule[offset], max_boxes=1)
    except AssertionError:
        return dict(
            offset=offset,
            status="template_not_applicable",
            reason="frozen loop-template applicability guard failed",
        )
    p12 = float(Decimal(first["p71_to72_lower_mw"][0]))
    p24 = float(Decimal(first["p72_to24_lower_mw"][0]))
    result = dict(
        offset=offset,
        p71_to72_lower_mw=p12,
        p72_to24_lower_mw=p24,
        b71_mw=float(Decimal(first["b71_mw"][0])),
        b72_mw=float(Decimal(first["b72_mw"][0])),
    )
    if first["proved_exclusion"]:
        result.update(status="certified_incompatible", proof=first)
    elif p12 <= 0 or p24 <= 0:
        result.update(
            status="no_forward_export_trigger",
            reason="directional template has no positive necessary export on both legs",
        )
    else:
        bounded = (
            first
            if max_boxes == 1
            else loop.prove_loop(hourly, schedule[offset], max_boxes=max_boxes - 1)
        )
        result.update(
            status="certified_incompatible"
            if bounded["proved_exclusion"]
            else "unresolved_budget",
            tested_boxes=bounded["tested_boxes"]
            + (0 if max_boxes == 1 else first["tested_boxes"]),
            unresolved_boxes=bounded["unresolved_boxes"],
        )
        if bounded["proved_exclusion"]:
            result["proof"] = bounded
    return result


def screen_schedule(kwargs, schedule, first_hour, targets, *, max_boxes=BOX_BUDGET):
    """Reusable preflight from numeric inputs, without historical outcome labels.

    Absence of a template exclusion is unresolved, not feasibility. A
    separately audited recovered witness may be combined with these results.
    """
    schedule = np.asarray(schedule, float)
    if type(max_boxes) is not int or max_boxes < 1:
        raise ValueError("positive integer box budget required")
    if kwargs["formulation"] != "ac" or len(schedule) != kwargs["T"]:
        raise ValueError("schedule must match the full target AC horizon")
    _, precheck = transfer.schedule_check(
        schedule, kwargs["storage"], kwargs["delta"], targets
    )
    hours = [
        dict(
            hour=first_hour + h,
            **schedule_hour(kwargs, schedule, h, targets, max_boxes=max_boxes),
        )
        for h in range(kwargs["T"])
    ]
    return dict(
        verdict=classify_schedule(hours, False),
        hours=hours,
        battery_precheck=precheck,
        max_boxes_per_triggered_hour=max_boxes,
    )


def historical_context_matches(saved, current, ignored=()):
    from .historical_replay import historical_context_matches as compare

    return compare(saved, current, ignored)


def historical_binding(data):
    """Reuse historical manifest/resource checks; no pretending today's HEAD is old."""
    hashes = starts.origin_hashes(data)
    binding = original.q.read(starts.ORIGIN / "binding.json")
    current = original.context()
    # These three recovery-only scripts changed after the original transfer.
    # The transfer runner/model and all numerical production sources must match.
    ignored = {
        f"experiments/tracy_battery_transfer_starts/{name}.py"
        for name in ("run", "model", "test_starts")
    }
    if not historical_context_matches(binding["context"], current, ignored):
        raise ValueError("historical transfer mathematical source/environment changed")
    # Historical read-only checks survive committing this report. Every
    # mathematical source, protocol and historical evidence byte stays bound.
    slice_root = slices.HERE / "results/slice_002"
    saved = transfer.read(slice_root / "binding.json")
    if (
        not historical_context_matches(saved["context"], slice_run.context())
        or slice_run.historical_hashes() != saved["historical_hashes"]
        or transfer.read(slice_root / "protocol.json") != slice_run.protocol()
        or saved["limits"] != slices.LIMITS
    ):
        raise ValueError("historical slice source/environment/protocol changed")
    slice_run.check_predecessor_files(saved["predecessor"])
    if (
        transfer.sha(Path(loop.__file__))
        != transfer.read(LOOP_EVIDENCE)["analysis_sha256"]
    ):
        raise ValueError("frozen loop certificate implementation changed")
    return binding, hashes


def replay_transfer(binding, data, prepared, number):
    view = original.checked_construction(binding, data, prepared, number)
    build, kwargs, schedule, start, row, captured = view
    directory = starts.ORIGIN / f"call-{number:03d}"
    sup = original.q.read(directory / "supervision.json")
    if not original.q.resource_evidence(directory, sup):
        raise ValueError("unverified finalized supervision")
    if not (directory / "completion.json").exists():
        if sup["classification"] != "wall_limit":
            raise ValueError("unclassified incomplete record")
        return view, dict(
            label="timeout",
            compatible=False,
            supervision="wall_limit",
            independent_replay="no completed native point",
        )
    record = transfer.read(directory / "result.json.gz")
    if record["row"] != row or record["optimizer_calls"] != 1:
        raise ValueError("retained transfer identity differs from reconstructed inputs")
    for name, expected in [
        ("start.json", dict(assigned_start=serializable(start))),
        ("x0.json.gz", captured),
        ("native.json.gz", dict(iteration=number, data=record["native"])),
        (
            "preparation.json.gz",
            dict(iteration=number, data=record["preparation_evidence"]),
        ),
    ]:
        if transfer.read(directory / name) != expected:
            raise ValueError("retained native/start evidence differs")
    checks = transfer.assess(build, kwargs, schedule, start, row, record, captured)
    if checks != record["checks"]:
        raise ValueError("independent transfer audit differs")
    compatible = record["classification"] == "compatible" and checks["passed"]
    request = transfer.read(directory / "request.json")
    if compatible and not (
        sup["classification"] == "exited"
        and sup["returncode"] == 0
        and sup["wall_seconds"] <= request["wall_seconds"]
    ):
        raise ValueError("compatible record lacks eligible finalized supervision")
    return view, dict(
        label=record["classification"],
        compatible=compatible,
        supervision=sup["classification"],
        native_status=record["native"]["status"],
        independent_replay="passed" if compatible else "native rejection preserved",
        cycling_warning=checks.get("economics", {}).get("cycling_warning", False),
    )


def classify_schedule(hours, witness):
    if any(h["status"] == "certified_incompatible" for h in hours):
        if witness:
            raise ValueError("certificate contradicts recovered witness")
        return "certified_incompatible"
    return "audited_recovered_witness" if witness else "unresolved"


def backtest(progress=None):
    with no_optimization():
        data = transfer.source_inventory()
        prepared = e3_prepared.verified_inputs()
        binding, before = historical_binding(data)
        report = dict(
            policy=dict(
                report_only=True,
                new_optimizer_calls=0,
                local_template="71-72-24-70 with radial 71-73",
                max_boxes_per_triggered_hour=BOX_BUDGET,
                short_cycle_max_length=4,
                thresholds="unchanged SOCPAuditTolerances",
                validation_kind="retrospective, post-hoc template; not holdout prediction",
            ),
            sources=dict(
                analysis_sha256=transfer.sha(Path(__file__)),
                frozen_loop_sha256=transfer.sha(Path(loop.__file__)),
                frozen_loop_evidence_sha256=transfer.sha(LOOP_EVIDENCE),
                transfer_binding_sha256=transfer.sha(starts.ORIGIN / "binding.json"),
                historical_inventory_sha256=transfer.object_hash(before),
            ),
            socp_points=[],
            transfers=[],
            ac_controls=[],
        )
        screens = {}
        for label, case in data["cases"].items():
            source, ref = case["socp"], case["ac"]
            if source["mathematical_input_sha256"] != ref["mathematical_input_sha256"]:
                raise ValueError("SOCP source and target AC physical inputs differ")
            cohort = e3_plus if source["cohort"] == "prepared_plus" else e3_prepared
            kwargs = cohort.kwargs_for_arm(
                prepared,
                source["arm"],
                source["input_identities"]["storage"],
                assembly="hourly",
            )
            if (
                e3_prepared.physical_input_digest(kwargs)
                != source["mathematical_input_sha256"]
            ):
                raise ValueError("source SOCP input digest differs")
            build = build_opf_multistep(**kwargs)
            saved = transfer.read(transfer.source_path(data, source))
            screen, recovery = point_screen(
                build, saved["result"], source["arm"]["start"]
            )
            if screen["relaxation_feasible"] != saved["relaxation_audit"]["feasible"]:
                raise ValueError("source relaxation replay differs")
            screen.update(
                scenario=label,
                source=transfer.source_reference(data, source),
                historical_classification=saved["classification"],
            )
            report["socp_points"].append(screen)
            screens[label] = (screen, recovery)
            if progress:
                progress("screened " + label)
        saved_report = transfer.read(starts.ORIGIN / "report.json")
        for job in transfer.jobs(data):
            view, outcome = replay_transfer(binding, data, prepared, job["id"])
            build, kwargs, schedule, _, row, _ = view
            saved_row = saved_report["attempts"][job["id"] - 1]
            if saved_row["compatible"] != outcome["compatible"]:
                raise ValueError("replayed eligibility differs from retained report")
            case = data["cases"][job["scenario"]]
            targets = row["original_terminal_soc_mwh"]
            schedule_report = screen_schedule(kwargs, schedule, row["start"], targets)
            hours = schedule_report["hours"]
            screen, recovery = screens[job["scenario"]]
            witness = False
            if (
                job["donor"] == "socp"
                and recovery.get("ac_feasible") is True
                and recovery.get("ac_audit", {}).get("complete")
            ):
                witness = bool(
                    transfer.pin_audit(
                        recovery["candidate"], schedule, kwargs, targets
                    )["passed"]
                )
            verdict = classify_schedule(hours, witness)
            if outcome["compatible"] and verdict == "certified_incompatible":
                raise ValueError(
                    "local certificate contradicts audited historical AC witness"
                )
            report["transfers"].append(
                dict(
                    job=job,
                    source=row["donor"],
                    source_schedule_sha256=row["schedule_sha256"],
                    target_mathematical_input_sha256=row["mathematical_input_sha256"],
                    historical_outcome=outcome,
                    preflight_verdict=verdict,
                    battery_precheck=schedule_report["battery_precheck"],
                    socp_point_warning=any(h["point_warning"] for h in screen["hours"])
                    if job["donor"] == "socp"
                    else None,
                    hours=hours,
                )
            )
            if job["donor"] == "socp":
                control = np.asarray(case["ac"]["result"]["b"], float)
                control_hours = screen_schedule(kwargs, control, row["start"], targets)[
                    "hours"
                ]
                if any(h["status"] == "certified_incompatible" for h in control_hours):
                    raise ValueError(
                        "local certificate contradicts retained accepted AC control"
                    )
                report["ac_controls"].append(
                    dict(
                        scenario=job["scenario"],
                        certificate_count=0,
                        hours=control_hours,
                    )
                )
            if progress:
                progress(f"back-tested {job['id']}/18: {outcome['label']} -> {verdict}")
        after = starts.origin_hashes(data)
        if before != after:
            raise ValueError("historical artifacts changed during read-only back-test")
        report["historical_bytes_unchanged"] = True
        report["summary"] = summarize(report)
        return serializable(report)


def summarize(report):
    rows = report["transfers"]
    compatible = [r for r in rows if r["historical_outcome"]["compatible"]]
    other = [r for r in rows if not r["historical_outcome"]["compatible"]]
    socp = [r for r in rows if r["job"]["donor"] == "socp"]
    return dict(
        retained_transfers=len(rows),
        audited_compatible=len(compatible),
        native_rejections=sum(
            r["historical_outcome"]["label"] == "native_rejection" for r in rows
        ),
        timeouts=sum(r["historical_outcome"]["label"] == "timeout" for r in rows),
        certified_incompatible=sum(
            r["preflight_verdict"] == "certified_incompatible" for r in rows
        ),
        contradictions_among_compatible=sum(
            r["preflight_verdict"] == "certified_incompatible" for r in compatible
        ),
        contradictions_among_not_accepted=sum(
            r["preflight_verdict"] == "certified_incompatible" for r in other
        ),
        recovered_witnesses=sum(
            r["preflight_verdict"] == "audited_recovered_witness" for r in rows
        ),
        unresolved=sum(r["preflight_verdict"] == "unresolved" for r in rows),
        socp_point_warnings=sum(r["socp_point_warning"] is True for r in socp),
        socp_point_warnings_on_compatible=sum(
            r["socp_point_warning"] is True and r["historical_outcome"]["compatible"]
            for r in socp
        ),
        positive_ac_controls=len(report["ac_controls"]),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backtest", action="store_true", required=True)
    parser.parse_args()
    # Match immutable numerical-environment metadata for read-only replay.
    for name in original.THREAD_KEYS:
        os.environ[name] = "1"
    report = backtest(progress=lambda s: print(s, flush=True))
    print("EVIDENCE_JSON")
    print(json.dumps(report, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
