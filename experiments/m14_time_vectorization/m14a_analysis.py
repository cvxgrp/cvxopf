"""Independent reconstruction and compact promotion of an M14a legacy run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, cast

import cvxpy as cp
import numpy as np

from experiments.m14_time_vectorization.baseline import (
    audit_result_from_inputs,
    result_schema,
)
from experiments.m14_time_vectorization.run_m14a import (
    FROZEN_LADDERS,
    ROOT,
    _validate_phase_journal,
)


AUDIT_TOLERANCE = 1e-5
CLASSIFICATIONS = {
    "completed",
    "worker_failure",
    "wall_time_limit",
    "artifact_missing",
    "artifact_invalid",
    "solve_not_accepted",
}
ENVIRONMENT_FIELDS = ("platform", "machine", "python", "packages")
ANALYSIS_SOURCES = tuple(
    sorted((ROOT / "src" / "cvxopf").rglob("*.py"))
    + sorted((ROOT / "experiments" / "m14_time_vectorization").rglob("*.py"))
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _analysis_source_fingerprint() -> str:
    digest = hashlib.sha256()
    # Match run_m14a._source_fingerprint(): package sources first, followed by
    # experiment sources.  Re-sorting the combined tuple changes the digest.
    for path in ANALYSIS_SOURCES:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git(*arguments: str) -> str | None:
    completed = subprocess.run(
        ["git", *arguments], cwd=ROOT, check=False, capture_output=True, text=True
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _confined_artifact(directory: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("artifact path must be a nonempty relative string")
    candidate = Path(relative)
    if candidate.is_absolute() or candidate != Path(*candidate.parts):
        raise ValueError("artifact path must be normalized and relative")
    resolved = (directory / candidate).resolve()
    if resolved.parent != directory.resolve():
        raise ValueError("artifact path escapes the run directory")
    return resolved


def _verified_identity(directory: Path, identity: object) -> Path:
    if not isinstance(identity, dict):
        raise ValueError("artifact identity must be a mapping")
    path = _confined_artifact(directory, identity.get("path"))
    if not path.is_file():
        raise ValueError("retained artifact is missing")
    if path.stat().st_size != identity.get("bytes"):
        raise ValueError("retained artifact size mismatch")
    if _sha256(path) != identity.get("sha256"):
        raise ValueError("retained artifact hash mismatch")
    return path


def _expected_points(ladder: str) -> list[tuple[str, int, str]]:
    return list(FROZEN_LADDERS[ladder])


def _required_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _finite_nonnegative(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    numeric = float(value)
    if not np.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return numeric


def _validate_measurements(payload: dict[str, Any], formulation: str) -> None:
    timing = payload.get("timing_seconds")
    memory = payload.get("peak_rss_bytes")
    if not isinstance(timing, dict) or not isinstance(memory, dict):
        raise ValueError("worker timing or memory evidence is missing")
    for name in ("construction", "solve_after_explicit_canonicalization", "extraction"):
        _finite_nonnegative(timing.get(name), f"{name} timing")
    for name in ("after_construction", "after_solve", "after_extraction"):
        if _finite_nonnegative(memory.get(name), f"{name} RSS") <= 0:
            raise ValueError(f"{name} RSS must be positive")
    if formulation == "ac":
        if timing.get("canonicalization") is not None:
            raise ValueError("AC canonicalization timing must be solve-owned")
        if memory.get("after_canonicalization") is not None:
            raise ValueError("AC canonicalization RSS must be solve-owned")
        if payload.get("canonical_structure") is not None:
            raise ValueError("AC must not retain convex canonical structure")
    else:
        _finite_nonnegative(timing.get("canonicalization"), "canonicalization timing")
        if (
            _finite_nonnegative(
                memory.get("after_canonicalization"), "after_canonicalization RSS"
            )
            <= 0
        ):
            raise ValueError("after_canonicalization RSS must be positive")
        if not isinstance(payload.get("canonical_structure"), dict):
            raise ValueError("convex canonical structure is missing")
    if not isinstance(payload.get("source_structure"), dict):
        raise ValueError("source structure is missing")


def _validate_classification_record(record: dict[str, Any]) -> None:
    classification = record.get("classification")
    returncode = record.get("returncode")
    artifact = record.get("artifact")
    evidence = record.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("manifest evidence must be a mapping")
    unvalidated = evidence.get("unvalidated_result")
    if classification == "completed":
        valid = returncode == 0 and artifact is not None and unvalidated is None
    elif classification == "worker_failure":
        valid = (
            isinstance(returncode, int)
            and not isinstance(returncode, bool)
            and returncode != 0
            and artifact is None
        )
    elif classification == "wall_time_limit":
        valid = returncode is None and artifact is None
    elif classification == "artifact_missing":
        valid = returncode == 0 and artifact is None and unvalidated is None
    elif classification == "artifact_invalid":
        valid = returncode == 0 and artifact is None
    elif classification == "solve_not_accepted":
        valid = returncode == 0 and artifact is not None and unvalidated is None
    else:
        raise ValueError("unknown M14a worker classification")
    if not valid:
        raise ValueError("worker classification evidence is inconsistent")


def _serialized_schema_matches(
    serialized_result: dict[str, Any], retained_schema: object
) -> bool:
    """Compare schemas while allowing JSON to normalize object string arrays."""
    if not isinstance(retained_schema, dict):
        return False
    reconstructed = result_schema(serialized_result)
    if reconstructed.keys() != retained_schema.keys():
        return False
    for name, actual in reconstructed.items():
        expected = retained_schema[name]
        if not isinstance(expected, dict):
            return False
        if actual == expected:
            continue
        normalized_actual = dict(actual)
        normalized_expected = dict(expected)
        if {
            normalized_actual.get("dtype_kind"),
            normalized_expected.get("dtype_kind"),
        } <= {"O", "U"}:
            normalized_actual.pop("dtype_kind", None)
            normalized_expected.pop("dtype_kind", None)
        if normalized_actual != normalized_expected:
            return False
    return True


def analyze_run(directory: Path) -> dict[str, Any]:
    """Validate retained evidence and return a compact scientific record."""
    root = directory.resolve()
    manifest_path = root / "manifest.json"
    manifest = cast(dict[str, Any], json.loads(manifest_path.read_text()))
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported M14a manifest schema")
    if (
        _finite_nonnegative(
            manifest.get("timeout_seconds_per_point"), "per-point timeout"
        )
        <= 0
    ):
        raise ValueError("per-point timeout must be positive")
    ladder = manifest.get("frozen_ladder")
    if ladder not in FROZEN_LADDERS:
        raise ValueError("manifest does not identify a frozen M14a ladder")
    records = manifest.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("manifest records must be a nonempty list")
    context = manifest.get("execution_context")
    if not isinstance(context, dict):
        raise ValueError("manifest execution context is missing")

    expected = _expected_points(str(ladder))
    expected_index = 0
    stopped: set[str] = set()
    summaries: list[dict[str, Any]] = []
    complete = True
    provenance_clean = context.get("worktree_clean") is True
    dirty_worker_points: list[dict[str, Any]] = []
    for raw_record in records:
        if not isinstance(raw_record, dict):
            raise ValueError("manifest record must be a mapping")
        formulation = str(raw_record.get("formulation"))
        horizon = _required_int(raw_record.get("horizon"), "manifest horizon")
        case_name = str(raw_record.get("case"))
        while expected_index < len(expected) and expected[expected_index][0] in stopped:
            expected_index += 1
        if expected_index >= len(expected) or expected[expected_index] != (
            formulation,
            horizon,
            case_name,
        ):
            raise ValueError("manifest point order does not match frozen ladder")
        expected_index += 1
        classification = str(raw_record.get("classification"))
        if classification not in CLASSIFICATIONS:
            raise ValueError("unknown M14a worker classification")
        evidence = raw_record.get("evidence")
        if not isinstance(evidence, dict):
            raise ValueError("manifest evidence must be a mapping")
        _validate_classification_record(raw_record)
        log_path = _verified_identity(root, evidence.get("log"))
        phases_identity = evidence.get("phases")
        phase_path = (
            None
            if phases_identity is None
            else _verified_identity(root, phases_identity)
        )
        summary: dict[str, Any] = {
            "case": case_name,
            "formulation": formulation,
            "horizon": horizon,
            "classification": classification,
            "returncode": raw_record.get("returncode"),
            "log_sha256": _sha256(log_path),
            "phase_journal_sha256": (
                None if phase_path is None else _sha256(phase_path)
            ),
        }
        if classification != "completed":
            complete = False
            stopped.add(formulation)
            retained_result = None
            if raw_record.get("artifact") is not None:
                retained_result = _verified_identity(root, raw_record.get("artifact"))
            elif evidence.get("unvalidated_result") is not None:
                retained_result = _verified_identity(
                    root, evidence.get("unvalidated_result")
                )
            summary["artifact_sha256"] = (
                None if retained_result is None else _sha256(retained_result)
            )
            summaries.append(summary)
            continue
        artifact_path = _verified_identity(root, raw_record.get("artifact"))
        payload = cast(dict[str, Any], json.loads(artifact_path.read_text()))
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported M14a worker schema")
        payload_context = payload.get("execution_context")
        if not isinstance(payload_context, dict):
            raise ValueError("worker execution context is missing")
        for name in ("git_commit", "source_fingerprint", *ENVIRONMENT_FIELDS):
            if payload_context.get(name) != context.get(name):
                raise ValueError(f"worker {name} differs from manifest")
        provenance_clean = (
            provenance_clean and payload_context.get("worktree_clean") is True
        )
        if payload_context.get("worktree_clean") is not True:
            dirty_worker_points.append(
                {
                    "case": case_name,
                    "formulation": formulation,
                    "horizon": horizon,
                    "git_commit": payload_context.get("git_commit"),
                    "source_fingerprint": payload_context.get("source_fingerprint"),
                }
            )
        if (
            payload.get("case"),
            payload.get("formulation"),
            _required_int(payload.get("horizon"), "worker horizon"),
        ) != (case_name, formulation, horizon):
            raise ValueError("worker point identity differs from manifest")
        if payload.get("temporal_assembly") != "stepwise":
            raise ValueError("worker did not use legacy stepwise assembly")
        expected_backend = "DNLP_IPOPT" if formulation == "ac" else "CPP"
        if payload.get("canonicalization_backend") != expected_backend:
            raise ValueError("worker canonicalization backend mismatch")
        if phase_path is None:
            raise ValueError("completed worker phase journal is missing")
        _validate_phase_journal(phase_path, convex=formulation != "ac")
        if payload.get("status") not in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}:
            raise ValueError("completed worker does not contain an accepted solve")
        residuals = payload.get("residuals")
        if not isinstance(residuals, dict) or not residuals:
            raise ValueError("worker residual mapping is missing")
        residual_values = np.asarray(list(residuals.values()), dtype=float)
        if not np.isfinite(residual_values).all() or np.any(residual_values < 0):
            raise ValueError("worker residuals must be finite and nonnegative")
        maximum_residual = float(np.max(residual_values))
        if maximum_residual > AUDIT_TOLERANCE:
            raise ValueError("worker residual exceeds the M14a audit tolerance")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("worker result mapping is missing")
        retained_audit_inputs = payload.get("audit_inputs")
        if not isinstance(retained_audit_inputs, dict):
            raise ValueError("worker audit inputs are missing")
        reconstructed_residuals = audit_result_from_inputs(
            formulation, retained_audit_inputs, result
        )
        if reconstructed_residuals.keys() != residuals.keys() or any(
            not np.isclose(
                reconstructed_residuals[name],
                float(residuals[name]),
                rtol=0.0,
                atol=1e-12,
            )
            for name in reconstructed_residuals
        ):
            raise ValueError("worker residual mapping does not reconstruct exactly")
        schema = payload.get("result_schema")
        if not _serialized_schema_matches(result, schema):
            raise ValueError("worker result schema does not match retained result")
        _validate_measurements(payload, formulation)
        timing = cast(dict[str, Any], payload["timing_seconds"])
        memory = cast(dict[str, Any], payload["peak_rss_bytes"])
        serialized_result = (
            json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode()
        if payload.get("serialized_result_bytes") != len(serialized_result):
            raise ValueError("serialized result size mismatch")
        summary.update(
            {
                "artifact_sha256": _sha256(artifact_path),
                "artifact_bytes": artifact_path.stat().st_size,
                "source_structure": payload.get("source_structure"),
                "source_structure_sha256": _digest(payload.get("source_structure")),
                "canonical_structure": payload.get("canonical_structure"),
                "canonical_structure_sha256": _digest(
                    payload.get("canonical_structure")
                ),
                "result_schema_sha256": _digest(schema),
                "result_sha256": _digest(result),
                "scientific_scalars": {
                    name: value
                    for name, value in result.items()
                    if value is None
                    or (isinstance(value, (int, float)) and not isinstance(value, bool))
                },
                "audit_inputs_sha256": _digest(retained_audit_inputs),
                "maximum_residual": maximum_residual,
                "timing_seconds": timing,
                "peak_rss_bytes": memory,
                "serialized_result_bytes": payload.get("serialized_result_bytes"),
            }
        )
        summaries.append(summary)

    while expected_index < len(expected) and expected[expected_index][0] in stopped:
        expected_index += 1
    if expected_index != len(expected):
        complete = False
    accepted_as_ladder_record = complete and provenance_clean
    return {
        "schema_version": 1,
        "stage": "M14a_legacy_baseline",
        "frozen_ladder": ladder,
        "execution_complete": complete,
        "execution_provenance_clean": provenance_clean,
        "accepted_as_ladder_record": accepted_as_ladder_record,
        "dirty_worker_points": dirty_worker_points,
        "audit_tolerance": AUDIT_TOLERANCE,
        "execution_context": context,
        "analysis_context": {
            "git_commit": _git("rev-parse", "HEAD"),
            "worktree_clean": _git("status", "--porcelain") == "",
            "source_fingerprint": _analysis_source_fingerprint(),
        },
        "manifest": {
            "bytes": manifest_path.stat().st_size,
            "sha256": _sha256(manifest_path),
        },
        "points": summaries,
    }


def _validate_reviewed_worktree_exception(
    exception: object,
    *,
    contexts: list[dict[str, Any]],
    dirty_worker_points: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(exception, dict):
        raise ValueError("reviewed worktree exception must be a mapping")
    if exception.get("schema_version") != 1:
        raise ValueError("unsupported reviewed worktree exception schema")
    if exception.get("scope") != "non_execution_worktree_changes":
        raise ValueError("reviewed worktree exception scope is invalid")
    reason = exception.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reviewed worktree exception reason is missing")
    paths = exception.get("paths")
    if (
        not isinstance(paths, list)
        or not paths
        or not all(isinstance(path, str) and path for path in paths)
        or len(set(paths)) != len(paths)
    ):
        raise ValueError("reviewed worktree exception paths are invalid")
    normalized_paths: list[str] = []
    for raw_path in paths:
        candidate = Path(raw_path)
        if (
            candidate.is_absolute()
            or not candidate.parts
            or candidate == Path(".")
            or ".." in candidate.parts
            or candidate != Path(*candidate.parts)
        ):
            raise ValueError("reviewed worktree exception path is not normalized")
        normalized = candidate.as_posix()
        if normalized.startswith("src/cvxopf/") or normalized.startswith(
            "experiments/m14_time_vectorization/"
        ):
            raise ValueError("reviewed exception includes an execution-source path")
        normalized_paths.append(normalized)
    if not dirty_worker_points:
        raise ValueError("reviewed exception has no dirty worker to explain")
    if any(context.get("worktree_clean") is not True for context in contexts):
        raise ValueError("reviewed exception requires clean parent launch contexts")
    for name in ("execution_commit", "execution_source_fingerprint"):
        context_name = (
            "git_commit" if name == "execution_commit" else "source_fingerprint"
        )
        if exception.get(name) != contexts[0].get(context_name):
            raise ValueError(f"reviewed worktree exception {name} mismatch")
    return {
        **exception,
        "paths": sorted(normalized_paths),
        "dirty_worker_points": dirty_worker_points,
    }


def analyze_runs(
    directories: list[Path],
    *,
    reviewed_worktree_exception: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine the two frozen ladders into the M14a advancement record."""
    runs: dict[str, dict[str, Any]] = {}
    for directory in directories:
        run = analyze_run(directory)
        ladder = str(run["frozen_ladder"])
        if ladder in runs:
            raise ValueError("duplicate frozen M14a ladder")
        runs[ladder] = run
    if set(runs) != set(FROZEN_LADDERS):
        raise ValueError("M14a advancement requires every frozen ladder")
    contexts = [run["execution_context"] for run in runs.values()]
    for name in ("git_commit", "source_fingerprint", *ENVIRONMENT_FIELDS):
        if any(context.get(name) != contexts[0].get(name) for context in contexts[1:]):
            raise ValueError(f"M14a ladder {name} values do not match")
    dirty_worker_points = [
        point for run in runs.values() for point in run["dirty_worker_points"]
    ]
    execution_complete = all(run["execution_complete"] for run in runs.values())
    execution_provenance_accepted = all(
        run["execution_provenance_clean"] for run in runs.values()
    )
    reviewed_exception = None
    if reviewed_worktree_exception is not None:
        reviewed_exception = _validate_reviewed_worktree_exception(
            reviewed_worktree_exception,
            contexts=contexts,
            dirty_worker_points=dirty_worker_points,
        )
        execution_provenance_accepted = True
    analysis_context = {
        "git_commit": _git("rev-parse", "HEAD"),
        "worktree_clean": _git("status", "--porcelain") == "",
        "source_fingerprint": _analysis_source_fingerprint(),
    }
    analysis_provenance_accepted = (
        analysis_context["worktree_clean"] is True
        and isinstance(analysis_context["git_commit"], str)
        and bool(analysis_context["git_commit"])
        and len(str(analysis_context["source_fingerprint"])) == 64
    )
    return {
        "schema_version": 1,
        "stage": "M14a_legacy_baseline",
        "execution_complete": execution_complete,
        "accepted_for_m14b": execution_complete
        and execution_provenance_accepted
        and analysis_provenance_accepted,
        "execution_commit": contexts[0].get("git_commit"),
        "execution_source_fingerprint": contexts[0].get("source_fingerprint"),
        "analysis_context": analysis_context,
        "reviewed_worktree_exception": reviewed_exception,
        "ladders": runs,
    }


def _promote(path: Path, payload: dict[str, Any]) -> None:
    if not payload["execution_complete"]:
        raise ValueError("an incomplete M14a run cannot be promoted")
    if not payload["accepted_for_m14b"]:
        raise ValueError("an M14a run without clean provenance cannot be promoted")
    analysis_context = payload.get("analysis_context")
    if not isinstance(analysis_context, dict) or (
        analysis_context.get("worktree_clean") is not True
        or not isinstance(analysis_context.get("git_commit"), str)
        or not analysis_context.get("git_commit")
        or not isinstance(analysis_context.get("source_fingerprint"), str)
        or len(analysis_context["source_fingerprint"]) != 64
    ):
        raise ValueError("analysis provenance does not match the execution source")
    data = (
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directories", type=Path, nargs="+")
    parser.add_argument("--promote", type=Path)
    parser.add_argument("--reviewed-worktree-exception", type=Path)
    arguments = parser.parse_args()
    reviewed_exception = None
    if arguments.reviewed_worktree_exception is not None:
        reviewed_exception = cast(
            dict[str, Any],
            json.loads(arguments.reviewed_worktree_exception.read_text()),
        )
    result = analyze_runs(
        arguments.run_directories,
        reviewed_worktree_exception=reviewed_exception,
    )
    if arguments.promote is not None:
        _promote(arguments.promote, result)
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
