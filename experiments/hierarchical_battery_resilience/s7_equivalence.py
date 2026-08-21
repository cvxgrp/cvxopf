"""Run and audit M17-S7 public-API equivalence against frozen references."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping
from importlib.metadata import PackageNotFoundError, version

import numpy as np

from cvxopf import (
    HierarchicalAcceptanceTolerances,
    HierarchicalInputs,
    HierarchicalPolicy,
    HierarchicalResult,
    solve_hierarchical_opf,
)

from .scenario import MANIFEST_PATH, load_frozen_scenario


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = Path(__file__).resolve().parent
RESULT_ROOT = EXPERIMENT_ROOT / "results"
S3_DIRECTORY = RESULT_ROOT / "s3_authoritative_0cd65b1"
S3B_DIRECTORY = RESULT_ROOT / "s3b_causal_recovery"
S3_METADATA = EXPERIMENT_ROOT / "S3_RESULTS_METADATA.json"
S3B_METADATA = EXPERIMENT_ROOT / "S3B_RESULTS_METADATA.json"
DEFAULT_OUTPUT = RESULT_ROOT / "s7_public_equivalence"
NUMERIC_ATOL = 1e-6

CaseName = Literal[
    "frozen__hard_equality",
    "frozen__quadratic_soft",
    "replan_every_step__hard_equality",
    "replan_every_step__quadratic_soft",
    "replan_every_step__hard_equality__shifted_with_recovery",
]

S3_CASES: tuple[CaseName, ...] = (
    "frozen__hard_equality",
    "frozen__quadratic_soft",
    "replan_every_step__hard_equality",
    "replan_every_step__quadratic_soft",
)
S3B_CASE: CaseName = "replan_every_step__hard_equality__shifted_with_recovery"
ALL_CASES = (*S3_CASES, S3B_CASE)


@dataclass(frozen=True)
class Comparison:
    """One named equivalence check and its maximum absolute difference."""

    name: str
    passed: bool
    maximum_absolute_difference: float | None = None
    detail: str | None = None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"artifact root must be an object: {path}")
    return value


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _git(*arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def verify_reference_integrity() -> dict[str, Any]:
    """Verify every frozen artifact used by S7 against tracked metadata."""
    s3 = json.loads(S3_METADATA.read_text())
    s3b = json.loads(S3B_METADATA.read_text())
    checks: dict[str, Any] = {}
    for name, specification in s3["artifacts"].items():
        path = S3_DIRECTORY / name
        actual = {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        expected = {
            "bytes": specification["bytes"],
            "sha256": specification["sha256"],
        }
        if actual != expected:
            raise ValueError(f"S3 artifact integrity failure for {name}: {actual}")
        checks[f"s3/{name}"] = actual
    for name, specification in s3b["artifacts"].items():
        path = S3B_DIRECTORY / name
        actual = {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        expected = {
            "bytes": specification["bytes"],
            "sha256": specification["sha256"],
        }
        if actual != expected:
            raise ValueError(f"S3b artifact integrity failure for {name}: {actual}")
        checks[f"s3b/{name}"] = actual
    manifest_hash = _sha256(MANIFEST_PATH)
    for label, expected in (
        ("S3", s3["scenario"]["manifest_sha256"]),
        ("S3b", s3b["source_fingerprints"]["scenario_manifest_sha256"]),
    ):
        if manifest_hash != expected:
            raise ValueError(f"{label} scenario manifest hash differs")
    checks["scenario_manifest_sha256"] = manifest_hash
    return checks


def _inputs() -> HierarchicalInputs:
    scenario = load_frozen_scenario()
    return HierarchicalInputs(
        case=scenario.case,
        horizon_steps=scenario.control.horizon_steps,
        delta=scenario.control.delta_hours,
        generators=scenario.generators,
        loads=scenario.loads,
        storage=scenario.storage,
        df_load_p=scenario.df_load_p,
        df_load_q=scenario.df_load_q,
        nondispatchable=scenario.nondispatchable,
        df_nd=scenario.df_nd,
        hvdc=scenario.hvdc,
        options=scenario.options,
    )


def _policy(case_name: CaseName) -> HierarchicalPolicy:
    scenario = load_frozen_scenario()
    outer, terminal, *suffix = case_name.split("__")
    initialization = "shifted_with_recovery" if suffix else "flat_only"
    return HierarchicalPolicy(
        ac_window_steps=scenario.control.nominal_ac_window_steps,
        outer_policy=outer,  # type: ignore[arg-type]
        inner_terminal_policy=terminal,  # type: ignore[arg-type]
        initialization_policy=initialization,  # type: ignore[arg-type]
        quadratic_soft_weight=(
            scenario.control.quadratic_soft_weight
            if terminal == "quadratic_soft"
            else None
        ),
        tolerances=HierarchicalAcceptanceTolerances(
            **dict(scenario.control.acceptance_tolerances)
        ),
    )


def run_public_case(case_name: CaseName) -> HierarchicalResult:
    """Execute one frozen S7 case exclusively through the public M17 API."""
    if case_name not in ALL_CASES:
        raise ValueError(f"unknown S7 case {case_name!r}")
    return solve_hierarchical_opf(_inputs(), _policy(case_name))


def _array_comparison(name: str, actual: object, expected: object) -> Comparison:
    if actual is None or expected is None:
        return _exact(f"{name}.presence", actual is None, expected is None)
    left = np.asarray(actual, dtype=float)
    right = np.asarray(expected, dtype=float)
    if left.shape != right.shape:
        return Comparison(name, False, detail=f"shape {left.shape} != {right.shape}")
    difference = float(np.max(np.abs(left - right))) if left.size else 0.0
    return Comparison(name, difference <= NUMERIC_ATOL, difference)


def _exact(name: str, actual: object, expected: object) -> Comparison:
    return Comparison(
        name,
        actual == expected,
        detail=None if actual == expected else f"{actual!r} != {expected!r}",
    )


def _structural_value(value: object) -> object:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {
            str(key): _structural_value(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_structural_value(item) for item in value]
    return value


def _is_unavailable_scalar(value: object) -> bool:
    if value is None:
        return True
    array = np.asarray(value)
    if array.ndim != 0:
        return False
    try:
        return bool(np.isnan(float(array)))
    except (TypeError, ValueError):
        return False


def _outcome_class(value: object) -> object:
    return "accepted" if value == "accepted_soft" else value


def _reference(case_name: CaseName) -> dict[str, Any]:
    if case_name == S3B_CASE:
        return _read_gzip_json(S3B_DIRECTORY / "causal_recovery.json.gz")
    return _read_gzip_json(S3_DIRECTORY / f"{case_name}.json.gz")


def _termination_class(reason: object, *, completed: bool) -> str | None:
    if completed:
        return None
    text = "" if reason is None else str(reason)
    if text.startswith("outer_"):
        return f"outer:{text.removeprefix('outer_')}"
    if text.startswith("ac_recovery_exhausted:"):
        return f"ac:{text.removeprefix('ac_recovery_exhausted:')}"
    return f"ac:{text}"


def _controlling_s3_attempts(reference: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        attempt
        for attempt in reference["ac_attempts"]
        if attempt["attempt_kind"] == "controlling"
    ]


def _expected_supplied_action(expected: Mapping[str, Any]) -> bool:
    if "supplied_executed_action" in expected:
        return bool(expected["supplied_executed_action"])
    audit = expected.get("audit")
    return bool(audit is not None and audit.get("accepted_primal", False))


def _common_result_checks(
    prefix: str,
    actual: Mapping[str, object] | None,
    expected: Mapping[str, object] | None,
) -> list[Comparison]:
    if actual is None or expected is None:
        return [_exact(f"{prefix}.result_presence", actual is None, expected is None)]
    checks: list[Comparison] = [
        Comparison(
            f"{prefix}.result_key.{name}",
            name in actual,
            detail=None if name in actual else "missing from public result",
        )
        for name in sorted(expected)
    ]
    for name in sorted(set(actual) & set(expected)):
        left, right = actual[name], expected[name]
        if _is_unavailable_scalar(left) or _is_unavailable_scalar(right):
            checks.append(
                _exact(
                    f"{prefix}.result.{name}.unavailable",
                    _is_unavailable_scalar(left),
                    _is_unavailable_scalar(right),
                )
            )
            continue
        try:
            left_array = np.asarray(left, dtype=float)
            right_array = np.asarray(right, dtype=float)
        except (TypeError, ValueError):
            checks.append(
                _exact(
                    f"{prefix}.result.{name}",
                    _structural_value(left),
                    _structural_value(right),
                )
            )
            continue
        checks.append(
            _array_comparison(f"{prefix}.result.{name}", left_array, right_array)
        )
    return checks


def compare_public_to_reference(
    case_name: CaseName, result: HierarchicalResult
) -> dict[str, Any]:
    """Compare one public run with its immutable S3 or S3b reference."""
    reference = _reference(case_name)
    checks = [
        _exact("completed", result.completed, reference["completed"]),
        _exact(
            "completed_intervals",
            result.completed_intervals,
            reference["completed_intervals"],
        ),
        _exact(
            "termination_iteration",
            result.termination_iteration,
            reference["termination_iteration"],
        ),
        _exact(
            "termination_class",
            _termination_class(result.termination_reason, completed=result.completed),
            _termination_class(
                reference["termination_reason"], completed=reference["completed"]
            ),
        ),
        _array_comparison(
            "realized_soc_mwh", result.realized_soc_mwh, reference["realized_soc_mwh"]
        ),
        _array_comparison(
            "executed_b_mw", result.executed_b_mw, reference["executed_b_mw"]
        ),
    ]
    expected_plans = reference["outer_plans"]
    checks.append(_exact("outer_plan_count", len(result.outer_plans), len(expected_plans)))
    for plan_id, plan in result.outer_plans.items():
        expected = expected_plans.get(plan_id)
        if expected is None:
            checks.append(Comparison(f"outer.{plan_id}", False, detail="missing"))
            continue
        prefix = f"outer.{plan_id}"
        checks.extend((
            _exact(f"{prefix}.created_iteration", plan.created_iteration, expected["created_iteration"]),
            _array_comparison(f"{prefix}.local_boundaries", plan.local_boundary_indices, expected["local_boundary_indices"]),
            _array_comparison(f"{prefix}.global_boundaries", plan.global_boundary_indices, expected["global_boundary_indices"]),
            _array_comparison(f"{prefix}.soc", plan.boundary_soc_mwh, expected["boundary_soc_mwh"]),
            _exact(f"{prefix}.status", plan.audit.status, expected["audit"]["status"]),
            _exact(f"{prefix}.outcome", plan.audit.outcome, expected["audit"]["outcome"]),
        ))
        checks.extend(
            _common_result_checks(prefix, plan.result, expected.get("results"))
        )

    if case_name == S3B_CASE:
        expected_attempts = reference["attempts"]
    else:
        expected_attempts = _controlling_s3_attempts(reference)
    checks.append(
        _exact("controlling_or_slot_count", len(result.ac_attempts), len(expected_attempts))
    )
    actual_attempt_locations = {
        attempt.attempt_id: (attempt.iteration, attempt.ordinal)
        for attempt in result.ac_attempts
    }
    expected_attempt_locations = {
        attempt["attempt_id"]: (
            attempt["iteration"],
            attempt.get("slot", {}).get("ordinal", 0),
        )
        for attempt in expected_attempts
    }
    for index, (attempt, expected) in enumerate(
        zip(result.ac_attempts, expected_attempts, strict=False)
    ):
        prefix = f"attempt.{index:03d}"
        expected_slot = expected.get("slot", {})
        expected_source_location = (
            None
            if expected.get("source_attempt_id") is None
            else expected_attempt_locations.get(expected["source_attempt_id"])
        )
        actual_source_location = (
            None
            if attempt.source_attempt_id is None
            else actual_attempt_locations.get(attempt.source_attempt_id)
        )
        expected_supplied_action = _expected_supplied_action(expected)
        checks.extend((
            _exact(f"{prefix}.iteration", attempt.iteration, expected["iteration"]),
            _exact(f"{prefix}.ordinal", attempt.ordinal, expected_slot.get("ordinal", 0)),
            _exact(f"{prefix}.role", attempt.role, expected_slot.get("role", "primary_controlling")),
            _exact(f"{prefix}.transformation", attempt.transformation, expected_slot.get("transformation", "flat")),
            _exact(f"{prefix}.scale", attempt.scale, expected_slot.get("scale")),
            _exact(f"{prefix}.seed", attempt.seed, expected_slot.get("seed")),
            _exact(f"{prefix}.source_location", actual_source_location, expected_source_location),
            _exact(f"{prefix}.slot_state", attempt.slot_state, expected.get("slot_state", "executed")),
            _exact(
                f"{prefix}.supplied_action",
                attempt.supplied_executed_action,
                expected_supplied_action,
            ),
            _exact(f"{prefix}.status", None if attempt.audit is None else attempt.audit.status, None if expected.get("audit") is None else expected["audit"]["status"]),
            _exact(
                f"{prefix}.outcome_class",
                _outcome_class(
                    None if attempt.audit is None else attempt.audit.outcome
                ),
                _outcome_class(
                    None
                    if expected.get("audit") is None
                    else expected["audit"]["outcome"]
                ),
            ),
            _array_comparison(f"{prefix}.initial_soc", list(attempt.initial_soc_mwh.values()), list(expected["initial_soc_mwh"].values())),
            _array_comparison(f"{prefix}.target_soc", list(attempt.target_soc_mwh.values()), list(expected["target_soc_mwh"].values())),
        ))
        expected_results = expected.get("results")
        checks.extend(_common_result_checks(prefix, attempt.result, expected_results))

    expected_executed = reference["executed_intervals"]
    checks.append(
        _exact(
            "executed_interval_count",
            len(result.executed_intervals),
            len(expected_executed),
        )
    )
    for index, (record, expected) in enumerate(
        zip(result.executed_intervals, expected_executed, strict=False)
    ):
        prefix = f"executed.{index:03d}"
        checks.append(_exact(f"{prefix}.iteration", record.iteration, expected["iteration"]))
        for name in (
            "generation_cost",
            "storage_cycling_cost",
            "renewable_curtailment_mwh",
            "active_loss_mwh",
            "active_loss_crosscheck_mw_abs",
            "state_transition_residual_mwh_abs",
            "voltage_violation_pu",
            "thermal_residual_mva",
            "normalized_squared_thermal_residual",
        ):
            checks.append(
                _array_comparison(
                    f"{prefix}.{name}", getattr(record, name), expected[name]
                )
            )

    summary_names = set(result.trajectory_summary) - {"runtime_seconds"}
    for name in sorted(summary_names):
        checks.append(
            _array_comparison(
                f"summary.{name}",
                result.trajectory_summary[name],
                reference["trajectory_summary"][name],
            )
        )
    failures = [item for item in checks if not item.passed]
    numeric_differences = [
        item.maximum_absolute_difference
        for item in checks
        if item.maximum_absolute_difference is not None
    ]
    return {
        "case": case_name,
        "passed": not failures,
        "numeric_atol": NUMERIC_ATOL,
        "checks": [item.__dict__ for item in checks],
        "failure_count": len(failures),
        "maximum_absolute_difference": max(numeric_differences, default=0.0),
        "manual_only_diagnostic_attempts": (
            0
            if case_name == S3B_CASE
            else len(reference["ac_attempts"]) - len(expected_attempts)
        ),
        "public_run_summary": {
            "completed": result.completed,
            "completed_intervals": result.completed_intervals,
            "termination_iteration": result.termination_iteration,
            "termination_reason": result.termination_reason,
            "outer_plan_count": len(result.outer_plans),
            "attempt_count": len(result.ac_attempts),
            "executed_interval_count": len(result.executed_intervals),
            "trajectory_summary": dict(result.trajectory_summary),
        },
    }


def _environment() -> dict[str, str]:
    values = {"python": platform.python_version()}
    for package in (
        "cvxopf",
        "cvxpy",
        "numpy",
        "pandas",
        "cyipopt",
        "clarabel",
    ):
        try:
            values[package] = version(package)
        except PackageNotFoundError:
            values[package] = "unknown"
    return values


def execute_to_directory(output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    """Execute all S7 cases from one clean source checkpoint."""
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"S7 output directory is not fresh: {output}")
    status = _git("status", "--porcelain")
    if status:
        raise ValueError("S7 execution requires a clean Git worktree")
    integrity = verify_reference_integrity()
    commit = _git("rev-parse", "HEAD")
    source_hash = _sha256(Path(__file__))
    comparisons = []
    for case_name in ALL_CASES:
        comparisons.append(compare_public_to_reference(case_name, run_public_case(case_name)))
    if _git("status", "--porcelain") or _git("rev-parse", "HEAD") != commit:
        raise RuntimeError("S7 execution source changed during the run")
    if _sha256(Path(__file__)) != source_hash:
        raise RuntimeError("S7 runner changed during the run")
    payload = {
        "schema_version": 1,
        "study": "s7_public_equivalence",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "execution_source_commit": commit,
        "execution_source_clean": True,
        "runner_sha256": source_hash,
        "cvxopf_python_tree_sha256": _source_fingerprint(
            list((ROOT / "src/cvxopf").rglob("*.py"))
        ),
        "environment": _environment(),
        "reference_integrity": integrity,
        "all_passed": all(item["passed"] for item in comparisons),
        "comparisons": comparisons,
    }
    _atomic_json(output / "equivalence.json", payload)
    return payload


if __name__ == "__main__":
    outcome = execute_to_directory()
    print(json.dumps({
        "all_passed": outcome["all_passed"],
        "cases": [
            {"case": item["case"], "failure_count": item["failure_count"]}
            for item in outcome["comparisons"]
        ],
    }, indent=2))
