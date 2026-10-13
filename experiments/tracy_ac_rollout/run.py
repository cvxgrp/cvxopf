"""Prepare/status are non-solving. --run requires separate reviewed authority."""
# ruff: noqa: E402 -- freeze native thread limits before numerical imports.
import argparse
from contextlib import contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path

THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
if __name__ == "__main__":
    for key in THREAD_KEYS:
        os.environ[key] = "1"

import cvxpy as cp
from cvxopf._prepared_rollout import starting_values
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json
from experiments.case118_annual_hierarchy.s5_speculative_policy import POLICY_NAME
from experiments.case118_annual_hierarchy.s5_speculative_transaction import pending_winner
from experiments.case118_tracy_2021 import e3_prepared, e3_plus
from experiments.case118_tracy_2021.stage_d import read
from experiments.numerical_preparation import run_qualification as qualification
from experiments.numerical_preparation.audit import serializable
from . import model, audit, runtime, worker


@contextmanager
def no_optimization():
    prior = cp.Problem.solve

    def forbidden(*args, **kwargs):
        raise RuntimeError("rollout preflight/status must not invoke an optimizer")

    cp.Problem.solve = forbidden
    try:
        yield
    finally:
        cp.Problem.solve = prior


def context():
    value = qualification.context()
    # Execution dependencies, not an incidental git ls-files inventory. New
    # tests/commits must not invalidate historical read-only replay commands.
    paths = list((model.ROOT / "src/cvxopf").rglob("*.py"))
    paths += list((model.ROOT / "experiments").glob("*/*.py"))
    paths += [model.ROOT / name for name in ("tests/socp_matched.py", "tests/socp_reference_cases.py", "uv.lock", "pyproject.toml")]
    paths += list(model.HERE.glob("*.md"))
    value["sources"] = {str(p.relative_to(model.ROOT)): e3_prepared.digest(p) for p in sorted(set(paths))}
    return value


def validate_context(expected, current, *, execution):
    if execution:
        if not expected["clean"] or expected != current:
            raise ValueError("execution requires its exact frozen clean commit/context")
    else:
        # Historical comparison tolerates only git bookkeeping changes and
        # newly added unrelated experiment sources; bound bytes remain required.
        before = {k: v for k, v in expected.items() if k not in {"commit", "clean", "sources"}}
        after = {k: v for k, v in current.items() if k not in {"commit", "clean", "sources"}}
        if before != after or any(current["sources"].get(k) != v for k, v in expected["sources"].items()):
            raise ValueError("historical rollout sources/environment changed")


def verify_authority(binding, authority, binding_sha):
    expected = dict(schema=1, experiment="tracy_ac_rollout", execution_authorized=True,
        binding_sha256=binding_sha, execution_commit=binding["context"]["commit"],
        primary_workers=2, helper_workers=1, maximum_solver_processes=3,
        recovery_policy=POLICY_NAME, recovery_revision=2,
        budgets=binding["budgets"])
    if authority != expected or binding["context"]["clean"] is not True:
        raise ValueError("missing/mismatched independently reviewed E4 launch authority")


def validate_output(root):
    root = root.resolve()
    allowed = (model.HERE / "results").resolve()
    if not root.is_relative_to(allowed) or root == allowed:
        raise ValueError("output must be a new named directory under tracy_ac_rollout/results")


def verify_binding(root, *, execution=False):
    binding = read(root / "binding.json")
    validate_context(binding["context"], context(), execution=execution)
    runtime.ResourceBudgets(**binding["budgets"])
    manifest, data, prepared = model.inventory()
    if binding["manifest"] != manifest:
        raise ValueError("retained cohort/source/physical inputs changed")
    if execution:
        verify_authority(binding, read(root / "authority.json"), e3_prepared.digest(root / "binding.json"))
    return binding, data, prepared


def preflight(*, canonical=True):
    """Construct all first windows; prepare IPOPT x0 but never enter a solver."""
    with no_optimization():
        manifest, data, prepared = model.inventory()
        rows = []
        for row in manifest["arms"]:
            cursor = model.cursor_for(row)
            build, _, snapshot, request = model.construct(row, cursor, data, prepared)
            raw, assigned = starting_values(build, snapshot, request, source_slot=0)
            captured = e3_prepared.prepared_start(build, 0) if canonical else None
            rows.append(dict(arm_id=row["arm_id"], source=row["source"], scenario=row["scenario"],
                window=serializable(asdict(request)), start_sha256=model.transfer.object_hash(assigned),
                canonical_start_sha256=None if captured is None else model.transfer.object_hash(captured)))
        return dict(manifest=manifest, first_windows=rows, optimizer_calls=0,
            study_execution_authorized=False, primary_workers=2, shared_helpers=1, maximum_solver_processes=3)


def status(root):
    """Independently replay supervised candidates and accepted-only window chain."""
    with no_optimization():
        binding, data, prepared = verify_binding(root)
        fixed, _ = model.retained_transfers()
        arms = []
        effort, attempts = 0., []
        directories = sorted((root / "attempts").glob("*/ac-*-spec-*"))
        for directory in directories:
            req = read(directory / "request.json")
            spec = worker.invocation(req["invocation"])
            finalized = (directory / "supervision.json").is_file()
            row = next(x for x in binding["manifest"]["arms"] if x["arm_id"] == req["arm_id"])
            label = "unfinished"
            if finalized:
                supervision = read(directory / "supervision.json")
                lifecycle = read(directory / "lifecycle.json")
                if (supervision["lifecycle_sha256"] != e3_prepared.digest(directory / "lifecycle.json")
                        or supervision["resources_sha256"] != e3_prepared.digest(directory / "resources.json")
                        or not supervision["reaped"] or lifecycle["invocation"] != asdict(spec)):
                    raise ValueError("finalized supervision/resource evidence differs")
                if supervision.get("resource_log_sha256") is not None and supervision["resource_log_sha256"] != e3_prepared.digest(directory / "resources.jsonl"):
                    raise ValueError("resource sampling log changed")
                resource = read(directory / "resources.json")
                if resource["samples"]:
                    if resource["peak_rss_mib"] != max(x["rss_mib"] for x in resource["samples"]):
                        raise ValueError("resource peak/sample disagreement")
                effort += supervision["worker_wall_seconds"]
                label = supervision["reason"]
                if label == "returned" and supervision["returncode"] == 0:
                    record, _, _ = runtime.checked_record(directory, spec, row, data, prepared, supervised=True)
                    label = ("accepted" if record["classification"] == "accepted" else
                             "native_rejection" if record["native"].get("status") != 0 else "audit_failure")
            attempts.append(dict(invocation=asdict(spec), finalized=finalized, classification=label))
        for row in binding["manifest"]["arms"]:
            directory = root / "arms" / row["arm_id"]
            cursor, executed = model.cursor_for(row), []
            pending = None
            if (directory / "checkpoint.json").exists():
                cp = read(directory / "checkpoint.json")
                if cp["completed_intervals"] != len(cp["windows"]) or cp["next_global_iteration"] != len(cp["windows"]):
                    raise ValueError("checkpoint coverage/count mismatch")
                for entry in cp["windows"]:
                    path = directory / entry["relative_path"]
                    if Path(entry["relative_path"]).name != entry["relative_path"] or path.stat().st_size != entry["bytes"] or e3_prepared.digest(path) != entry["sha256"]:
                        raise ValueError("executed window archive/hash mismatch")
                    saved = read(path)
                    finalized = read(directory / f'finalization-{entry["iteration"]:03d}.json')
                    if finalized["window"] != entry:
                        raise ValueError("window finalization/index disagreement")
                    selected_supervision = worker.referenced(root, saved["selected_supervision"])
                    if not selected_supervision["reaped"]:
                        raise ValueError("selected winner is not finalized")
                    if set(finalized["candidate_supervision"]) != set(saved["candidate_directories"]):
                        raise ValueError("contender cleanup inventory differs")
                    for ref in finalized["candidate_supervision"].values():
                        if not worker.referenced(root, ref)["reaped"]:
                            raise ValueError("contender cleanup is unfinished")
                    selected_path = root / saved["selected_record"]["path"]
                    record = worker.referenced(root, saved["selected_record"])
                    spec = worker.invocation(record["invocation"])
                    if finalized["winner"] != asdict(spec):
                        raise ValueError("finalized winner selection differs")
                    if (spec.window.iteration != cursor.completed_intervals or saved["plan_id"] != row["plan"]["plan_id"]
                            or saved["executed_interval"]["controlling_attempt_id"] != spec.attempt_id
                            or saved["preceding_controlling_attempt_id"] != cursor.preceding_controlling_attempt_id):
                        raise ValueError("executed controlling chain changed")
                    record, kwargs, request = runtime.checked_record(selected_path.parent, spec, row, data, prepared, supervised=True)
                    cursor, action = audit.first_action(record, cursor, request, kwargs, spec.attempt_id)
                    if saved["executed_interval"] != action or saved["post_step_soc_mwh"] != cursor.realized_soc_mwh.tolist():
                        raise ValueError("executed action accounting/state differs")
                    executed.append(action["result"])
                if (cp["realized_soc_mwh"] != cursor.realized_soc_mwh.tolist()
                        or cp["preceding_controlling_attempt_id"] != cursor.preceding_controlling_attempt_id
                        or cp["complete"] != (cursor.completed_intervals == 24)):
                    raise ValueError("checkpoint differs from independently replayed trajectory")
                pending = pending_winner(directory)
            reference = data["cases"][row["scenario"]]["ac"]
            original = model.original_kwargs(reference, prepared)
            complete = cursor.completed_intervals == 24
            trajectory = audit.trajectory(executed, original, complete=complete)
            comparisons = None
            fixed_comparison = None
            if complete:
                totals = trajectory["audit"]
                costs, ref_costs = totals["costs"], reference["audit"]["costs"]
                delta = sum(costs.values()) - sum(ref_costs.values())
                comparisons = dict(physical_cost_delta=delta,
                    physical_cost_relative_delta=None if abs(sum(ref_costs.values())) < 1e-8 else delta / abs(sum(ref_costs.values())),
                    component_cost_deltas={k: costs[k] - ref_costs[k] for k in costs},
                    ens_delta_mwh=totals["metrics"]["energy_not_served_mwh"] - reference["audit"]["metrics"]["energy_not_served_mwh"])
                if row["source"] != "ac_reference":
                    retained = next(x for x in fixed if x["scenario"] == row["scenario"] and x["donor"] == row["source"])
                    fixed_comparison = dict(evidence_kind="retained_original_fixed_transfer",
                        retained_classification=retained["retained_classification"], available=retained["retained_compatible"])
                    if retained["retained_compatible"]:
                        fixed_comparison.update(physical_cost_delta=sum(costs.values()) - sum(retained["named_costs"].values()),
                            ens_delta_mwh=totals["metrics"]["energy_not_served_mwh"] - retained["result"]["energy_not_served"])
                if reference["cohort"] == "prepared_plus":
                    witness = e3_plus.fixture_reference()["ac"]["stress"]["modified"]["minimum_ens_mwh"]
                    witness_passed = totals["metrics"]["energy_not_served_mwh"] + e3_plus.WITNESS_ALLOWANCE_MWH >= witness
                    trajectory["forced_shedding_witness"] = dict(minimum_ens_mwh=witness, passed=witness_passed)
                    trajectory["audit"]["passed"] = trajectory["audit"]["passed"] and witness_passed
            trajectory.pop("result", None)  # full actions remain in immutable window archives
            if comparisons is not None:
                comparisons["independent_trajectory_passed"] = trajectory["audit"]["passed"]
            if fixed_comparison is not None:
                fixed_comparison["independent_trajectory_passed"] = trajectory["audit"]["passed"]
            arms.append(dict(arm_id=row["arm_id"], scenario=row["scenario"], source=row["source"],
                source_classification=row["plan"]["source_classification"], qualification_flags=row["plan"]["qualification_flags"],
                completed_intervals=cursor.completed_intervals, first_unresolved=None if complete else cursor.completed_intervals,
                first_unresolved_global=None if complete else row["plan"]["global_start"] + cursor.completed_intervals,
                pending_durable_winner=None if pending is None else asdict(pending),
                trajectory=trajectory, full_ac_comparison=comparisons, fixed_transfer_comparison=fixed_comparison,
                outcome=read(directory / "outcome.json") if (directory / "outcome.json").exists() else None))
        return dict(arms=arms, completed_arms=sum(a["completed_intervals"] == 24 for a in arms),
            independently_audited_complete_arms=sum(a["completed_intervals"] == 24 and a["trajectory"]["audit"]["passed"] for a in arms),
            attempts=attempts, finalized_worker_seconds=effort, active_partial_attempts=sum(not a["finalized"] for a in attempts),
            outcome=read(root / "outcome.json") if (root / "outcome.json").exists() else None, optimizer_calls=0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--worker", type=Path)
    parser.add_argument("--output", type=Path, default=model.HERE / "results/rollout_001")
    parser.add_argument("--budgets", type=Path, help="reviewed explicit run-wide resource ceilings")
    parser.add_argument("--authority", type=Path, help="separately reviewed E4 execution authority")
    args = parser.parse_args(argv)
    root = args.output.resolve()
    if args.preflight:
        print(json.dumps(preflight(), indent=2, allow_nan=False))
        return
    if args.status:
        print(json.dumps(serializable(status(root)), indent=2, allow_nan=False))
        return
    validate_output(root)
    if args.prepare:
        if args.budgets is None:
            parser.error("--prepare requires --budgets; no compute budget is inferred")
        budgets = runtime.ResourceBudgets(**read(args.budgets))
        checked = preflight()
        binding = dict(schema=1, context=context(), manifest=checked["manifest"],
            first_windows=checked["first_windows"], budgets=asdict(budgets))
        if not binding["context"]["clean"]:
            raise ValueError("prepare a binding only after owner review/commit")
        root.mkdir(parents=True, exist_ok=False)
        atomic_immutable_json(root / "binding.json", binding)
        print(f"Prepared {len(binding['manifest']['arms'])} arms; no execution authorized.")
    elif args.worker is not None:
        directory = args.worker.resolve()
        if not directory.is_relative_to(root / "attempts"):
            raise ValueError("worker directory escapes this run")
        worker.run(root, directory)
    else:
        if args.authority is None:
            parser.error("--run requires separately reviewed --authority")
        if (root / "attempts").exists() or (root / "arms").exists() or (root / "outcome.json").exists():
            raise ValueError("run already attempted; no automatic resume/retry or overwrite")
        binding, data, prepared = verify_binding(root)
        authority = read(args.authority)
        verify_authority(binding, authority, e3_prepared.digest(root / "binding.json"))
        validate_context(binding["context"], context(), execution=True)
        monitoring = runtime.telemetry()  # permissions/telemetry BEFORE a child launch
        atomic_immutable_json(root / "authority.json", authority)
        atomic_immutable_json(root / "monitoring.json", monitoring)
        runtime.execute(root, binding, data, prepared)
        print(json.dumps(serializable(status(root)), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
