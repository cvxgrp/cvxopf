"""Run and persist the frozen M17-S3 manual reference experiment."""

from __future__ import annotations

import argparse
from dataclasses import fields
from datetime import datetime, timezone
import gzip
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import subprocess
from typing import Mapping

import numpy as np
import pandas as pd

from experiments.hierarchical_battery_resilience.manual_runner import (
    ACAttemptRecord,
    EndpointStudyRecord,
    ExecutedIntervalRecord,
    FROZEN_ENDPOINT_CASES,
    OuterPlanRecord,
    SequentialRunRecord,
    run_endpoint_realization,
    run_sequential_execution,
)
from experiments.hierarchical_battery_resilience.scenario import (
    MANIFEST_PATH,
    load_frozen_scenario,
)


DEFAULT_OUTPUT = Path(
    "experiments/hierarchical_battery_resilience/results/s3_manual"
)
SEQUENTIAL_CASES = (
    ("frozen", "hard_equality"),
    ("frozen", "quadratic_soft"),
    ("replan_every_step", "hard_equality"),
    ("replan_every_step", "quadratic_soft"),
)
CONTEXT_FILE = "run_context.json"
ARTIFACT_SCHEMA_VERSION = 1
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRAJECTORY_SUMMARY_KEYS = {
    "generation_cost",
    "storage_cycling_cost",
    "renewable_curtailment_mwh",
    "active_loss_mwh",
    "maximum_voltage_violation_pu",
    "maximum_thermal_residual_mva",
    "maximum_normalized_squared_thermal_residual",
    "cumulative_absolute_signpost_deviation_mwh",
    "runtime_seconds",
}
EXECUTED_INTERVAL_KEYS = {
    "iteration",
    "controlling_attempt_id",
    "generation_cost",
    "storage_cycling_cost",
    "renewable_curtailment_mwh",
    "active_loss_mwh",
    "active_loss_crosscheck_mw_abs",
    "state_transition_residual_mwh_abs",
    "voltage_violation_pu",
    "thermal_residual_mva",
    "normalized_squared_thermal_residual",
}


def _jsonable(value):
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"S3 artifact cannot serialize {type(value).__name__}")


def _audit_payload(record) -> dict:
    return {
        field.name: _jsonable(getattr(record, field.name))
        for field in fields(record)
    }


def _outer_payload(record: OuterPlanRecord) -> dict:
    return {
        "outer_plan_id": record.outer_plan_id,
        "created_iteration": record.created_iteration,
        "global_interval_start": record.global_interval_start,
        "global_interval_stop": record.global_interval_stop,
        "local_boundary_indices": _jsonable(record.local_boundary_indices),
        "global_boundary_indices": _jsonable(record.global_boundary_indices),
        "storage_device_ids": list(record.storage_device_ids),
        "boundary_soc_mwh": _jsonable(record.boundary_soc_mwh),
        "results": _jsonable(record.results),
        "audit": _audit_payload(record.audit),
    }


def _attempt_payload(record: ACAttemptRecord) -> dict:
    excluded = {"build", "results", "audit"}
    payload = {
        field.name: _jsonable(getattr(record, field.name))
        for field in fields(record)
        if field.name not in excluded
    }
    payload["results"] = _jsonable(record.results)
    payload["audit"] = _audit_payload(record.audit)
    return payload


def _executed_payload(record: ExecutedIntervalRecord) -> dict:
    return {
        field.name: _jsonable(getattr(record, field.name))
        for field in fields(record)
    }


def _endpoint_payload(study: EndpointStudyRecord) -> dict:
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "study": "endpoint_realization",
        "completed": study.completed,
        "termination_reason": study.termination_reason,
        "outer_plan": _outer_payload(study.outer_plan),
        "realizations": [
            {
                "case": _audit_payload(record.case),
                "attempt": _attempt_payload(record.attempt),
                "diagnostic_attempt": (
                    None
                    if record.diagnostic_attempt is None
                    else _attempt_payload(record.diagnostic_attempt)
                ),
            }
            for record in study.realizations
        ],
    }


def _sequential_payload(study: SequentialRunRecord) -> dict:
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "study": "sequential_execution",
        "outer_policy": study.outer_policy,
        "inner_policy": study.inner_policy,
        "completed": study.completed,
        "termination_iteration": study.termination_iteration,
        "termination_reason": study.termination_reason,
        "completed_intervals": study.completed_intervals,
        "completion_fraction": study.completion_fraction,
        "realized_soc_mwh": _jsonable(study.realized_soc_mwh),
        "executed_b_mw": _jsonable(study.executed_b_mw),
        "trajectory_summary": _jsonable(study.trajectory_summary),
        "outer_plans": {
            plan_id: _outer_payload(record)
            for plan_id, record in study.outer_plans.items()
        },
        "ac_attempts": [
            _attempt_payload(record) for record in study.ac_attempts
        ],
        "executed_intervals": [
            _executed_payload(record) for record in study.executed_intervals
        ],
    }


def _write_gzip_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream, allow_nan=False, separators=(",", ":"))
    temporary.replace(path)


def _read_gzip_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, EOFError, json.JSONDecodeError):
        return None


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text)
    temporary.replace(path)


def _atomic_write_csv(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def _audit_is_structured(audit: object) -> bool:
    required = {
        "status",
        "outcome",
        "accepted_primal",
        "missing_or_nonfinite_fields",
        "residuals",
        "wall_time_seconds",
    }
    return isinstance(audit, dict) and required <= set(audit)


def _outer_is_structured(plan: object) -> bool:
    required = {
        "outer_plan_id",
        "created_iteration",
        "global_interval_start",
        "global_interval_stop",
        "local_boundary_indices",
        "global_boundary_indices",
        "storage_device_ids",
        "boundary_soc_mwh",
        "results",
        "audit",
    }
    return (
        isinstance(plan, dict)
        and required <= set(plan)
        and _audit_is_structured(plan["audit"])
    )


def _attempt_is_structured(attempt: object) -> bool:
    required = {
        "attempt_id",
        "attempt_kind",
        "iteration",
        "interval_start",
        "interval_stop",
        "outer_plan_id",
        "outer_local_boundary",
        "outer_global_boundary",
        "storage_device_ids",
        "window_diagnosis",
        "results",
        "audit",
    }
    return (
        isinstance(attempt, dict)
        and required <= set(attempt)
        and _audit_is_structured(attempt["audit"])
    )


def _valid_endpoint_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    required = {
        "artifact_schema_version",
        "study",
        "completed",
        "termination_reason",
        "outer_plan",
        "realizations",
    }
    if (
        not required <= set(payload)
        or payload["artifact_schema_version"] != ARTIFACT_SCHEMA_VERSION
        or payload["study"] != "endpoint_realization"
        or not isinstance(payload["completed"], bool)
        or not _outer_is_structured(payload["outer_plan"])
        or not isinstance(payload["realizations"], list)
    ):
        return False
    outer_accepted = payload["outer_plan"]["audit"]["accepted_primal"]
    expected_count = len(FROZEN_ENDPOINT_CASES) if outer_accepted else 0
    if len(payload["realizations"]) != expected_count:
        return False
    expected_cases = {
        (case.name, case.start, case.stop) for case in FROZEN_ENDPOINT_CASES
    }
    actual_cases = set()
    for record in payload["realizations"]:
        if not isinstance(record, dict) or not {
            "case",
            "attempt",
            "diagnostic_attempt",
        } <= set(record):
            return False
        case = record["case"]
        if not isinstance(case, dict) or not {"name", "start", "stop"} <= set(case):
            return False
        actual_cases.add((case["name"], case["start"], case["stop"]))
        if not _attempt_is_structured(record["attempt"]):
            return False
        diagnostic = record["diagnostic_attempt"]
        if diagnostic is not None and not _attempt_is_structured(diagnostic):
            return False
    attempts_accepted = all(
        record["attempt"]["audit"]["accepted_primal"]
        for record in payload["realizations"]
    )
    expected_completed = bool(outer_accepted and attempts_accepted)
    if payload["completed"] != expected_completed:
        return False
    if expected_completed != (payload["termination_reason"] is None):
        return False
    if not expected_completed and not isinstance(
        payload["termination_reason"], str
    ):
        return False
    if not outer_accepted:
        return payload["termination_reason"] == (
            f"outer_{payload['outer_plan']['audit']['outcome']}"
        )
    expected_reason = (
        None if expected_completed else "one_or_more_endpoint_attempts_failed"
    )
    return (
        actual_cases == expected_cases
        and payload["termination_reason"] == expected_reason
    )


def _valid_sequential_payload(
    payload: object,
    *,
    outer_policy: str,
    inner_policy: str,
    horizon_steps: int,
) -> bool:
    if not isinstance(payload, dict):
        return False
    required = {
        "artifact_schema_version",
        "study",
        "outer_policy",
        "inner_policy",
        "completed",
        "termination_iteration",
        "termination_reason",
        "completed_intervals",
        "completion_fraction",
        "realized_soc_mwh",
        "executed_b_mw",
        "trajectory_summary",
        "outer_plans",
        "ac_attempts",
        "executed_intervals",
    }
    if (
        not required <= set(payload)
        or payload["artifact_schema_version"] != ARTIFACT_SCHEMA_VERSION
        or payload["study"] != "sequential_execution"
        or payload["outer_policy"] != outer_policy
        or payload["inner_policy"] != inner_policy
        or not isinstance(payload["completed"], bool)
    ):
        return False
    completed = payload["completed_intervals"]
    if (
        isinstance(completed, bool)
        or not isinstance(completed, int)
        or not 0 <= completed <= horizon_steps
    ):
        return False
    if (
        not isinstance(payload["executed_intervals"], list)
        or len(payload["executed_intervals"]) != completed
    ):
        return False
    if not all(
        isinstance(interval, dict)
        and EXECUTED_INTERVAL_KEYS <= set(interval)
        for interval in payload["executed_intervals"]
    ):
        return False
    if (
        not isinstance(payload["executed_b_mw"], list)
        or len(payload["executed_b_mw"]) != completed
    ):
        return False
    if (
        not isinstance(payload["realized_soc_mwh"], list)
        or len(payload["realized_soc_mwh"]) != completed + 1
    ):
        return False
    completion_fraction = payload["completion_fraction"]
    if (
        isinstance(completion_fraction, bool)
        or not isinstance(completion_fraction, (int, float))
        or not np.isfinite(completion_fraction)
        or not np.isclose(completion_fraction, completed / horizon_steps)
    ):
        return False
    if payload["completed"] != (completed == horizon_steps):
        return False
    if not isinstance(payload["trajectory_summary"], dict) or not (
        TRAJECTORY_SUMMARY_KEYS <= set(payload["trajectory_summary"])
    ):
        return False
    if payload["completed"]:
        if payload["termination_iteration"] is not None:
            return False
        if payload["termination_reason"] is not None:
            return False
    elif payload["termination_iteration"] != completed:
        return False
    if not payload["completed"] and not isinstance(
        payload["termination_reason"], str
    ):
        return False
    plans = payload["outer_plans"]
    attempts = payload["ac_attempts"]
    if not isinstance(plans, dict) or not all(
        plan_id == plan.get("outer_plan_id") and _outer_is_structured(plan)
        for plan_id, plan in plans.items()
    ):
        return False
    if not isinstance(attempts, list) or not all(
        _attempt_is_structured(attempt) for attempt in attempts
    ):
        return False
    expected_plans = 1 if outer_policy == "frozen" else completed + (
        0 if payload["completed"] else 1
    )
    if len(plans) != expected_plans:
        return False
    controlling = [
        attempt for attempt in attempts if attempt["attempt_kind"] == "controlling"
    ]
    expected_controlling = completed + (
        0
        if payload["completed"]
        or payload["termination_reason"].startswith("outer_")
        else 1
    )
    return len(controlling) == expected_controlling


def _matches_prior_metadata(
    path: Path,
    prior_metadata: dict | None,
) -> bool:
    if prior_metadata is None:
        return True
    expected = prior_metadata.get("artifacts", {}).get(path.name)
    if expected is None:
        return False
    return (
        path.is_file()
        and path.stat().st_size == expected.get("bytes")
        and _sha256(path) == expected.get("sha256")
    )


def _reusable_payload(
    path: Path,
    *,
    prior_metadata: dict | None,
    validator,
) -> dict | None:
    if not _matches_prior_metadata(path, prior_metadata):
        return None
    payload = _read_gzip_json(path)
    if payload is None:
        return None
    try:
        return payload if validator(payload) else None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _summary_row(study: SequentialRunRecord) -> dict:
    return {
        "study": "sequential_execution",
        "outer_policy": study.outer_policy,
        "inner_policy": study.inner_policy,
        "completed": study.completed,
        "termination_iteration": study.termination_iteration,
        "termination_reason": study.termination_reason,
        "completed_intervals": study.completed_intervals,
        "completion_fraction": study.completion_fraction,
        **study.trajectory_summary,
    }


def _run_context(scenario) -> dict:
    experiment_root = Path(__file__).resolve().parent
    model_sources = list((REPOSITORY_ROOT / "src" / "cvxopf").rglob("*.py"))
    execution_sources = [
        experiment_root / "scenario.py",
        experiment_root / "manual_runner.py",
    ]
    artifact_sources = [
        experiment_root / "reproduce.py",
        experiment_root / "analysis.py",
    ]
    git_status = _git_output("status", "--porcelain", "--untracked-files=all")
    return {
        "scenario_name": scenario.manifest["scenario_name"],
        "scenario_manifest_sha256": _sha256(MANIFEST_PATH),
        "git_commit": _git_output("rev-parse", "HEAD"),
        "git_dirty": bool(git_status),
        "git_status_porcelain": git_status.splitlines(),
        "source_fingerprints": {
            "cvxopf_python_tree_sha256": _source_fingerprint(model_sources),
            "experiment_execution_sha256": _source_fingerprint(
                execution_sources
            ),
            "artifact_code_sha256": _source_fingerprint(artifact_sources),
            "files": {
                path.relative_to(REPOSITORY_ROOT).as_posix(): _sha256(path)
                for path in execution_sources + artifact_sources
            },
        },
        "python": platform.python_version(),
        "packages": {
            package: version(package)
            for package in ("cvxopf", "cvxpy", "numpy", "pandas")
        },
    }


def _prepare_run_context(
    output_path: Path,
    context: dict,
    *,
    resume: bool,
) -> None:
    context_path = output_path / CONTEXT_FILE
    existing = None
    if context_path.exists():
        existing = json.loads(context_path.read_text())
    elif resume and (output_path / "metadata.json").exists():
        metadata = json.loads((output_path / "metadata.json").read_text())
        existing = {key: metadata[key] for key in context}
    elif resume and any(output_path.glob("*.json.gz")):
        raise ValueError(
            "Cannot resume S3 artifacts without matching run context"
        )
    if resume and existing is not None and existing != context:
        raise ValueError("S3 resume context differs from existing artifacts")
    _atomic_write_text(context_path, json.dumps(context, indent=2) + "\n")


def reproduce(output_path: Path = DEFAULT_OUTPUT, *, resume: bool = False) -> None:
    """Run each frozen study separately and retain all extracted results."""
    output_path.mkdir(parents=True, exist_ok=True)
    scenario = load_frozen_scenario()
    context = _run_context(scenario)
    metadata_path = output_path / "metadata.json"
    prior_metadata = (
        json.loads(metadata_path.read_text())
        if resume and metadata_path.exists()
        else None
    )
    _prepare_run_context(output_path, context, resume=resume)
    artifacts = []
    summaries = []

    endpoint_path = output_path / "endpoint_realization.json.gz"
    endpoint_payload = (
        _reusable_payload(
            endpoint_path,
            prior_metadata=prior_metadata,
            validator=_valid_endpoint_payload,
        )
        if resume
        else None
    )
    if endpoint_payload is None:
        endpoint = run_endpoint_realization()
        _write_gzip_json(endpoint_path, _endpoint_payload(endpoint))
        del endpoint
    artifacts.append(endpoint_path)

    for outer_policy, inner_policy in SEQUENTIAL_CASES:
        path = output_path / f"{outer_policy}__{inner_policy}.json.gz"
        payload = (
            _reusable_payload(
                path,
                prior_metadata=prior_metadata,
                validator=lambda value: _valid_sequential_payload(
                    value,
                    outer_policy=outer_policy,
                    inner_policy=inner_policy,
                    horizon_steps=scenario.control.horizon_steps,
                ),
            )
            if resume
            else None
        )
        if payload is None:
            study = run_sequential_execution(outer_policy, inner_policy)
            summaries.append(_summary_row(study))
            _write_gzip_json(path, _sequential_payload(study))
            del study
        else:
            summaries.append(
                {
                    "study": payload["study"],
                    "outer_policy": payload["outer_policy"],
                    "inner_policy": payload["inner_policy"],
                    "completed": payload["completed"],
                    "termination_iteration": payload["termination_iteration"],
                    "termination_reason": payload["termination_reason"],
                    "completed_intervals": payload["completed_intervals"],
                    "completion_fraction": payload["completion_fraction"],
                    **payload["trajectory_summary"],
                }
            )
        artifacts.append(path)

    summary_path = output_path / "trajectory_summary.csv"
    _atomic_write_csv(summary_path, pd.DataFrame(summaries))
    artifacts.append(summary_path)
    metadata = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        **context,
        "artifacts": {
            path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
            for path in artifacts
        },
    }
    _atomic_write_text(
        metadata_path,
        json.dumps(metadata, indent=2) + "\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="reuse complete readable artifacts and rerun missing ones",
    )
    args = parser.parse_args()
    reproduce(args.output, resume=args.resume)


if __name__ == "__main__":
    main()
