"""Isolated legacy scaling measurements for the M14a baseline."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any, cast

import cvxpy as cp

from cvxopf.characterization import (
    characterize_convex_canonicalization,
    characterize_source_graph,
)
from cvxopf.results import extract_results
from experiments.m14_time_vectorization.baseline import (
    BaselineCase,
    audit_result,
    build_baseline_fixture,
    json_result,
    result_schema,
)


FORMULATIONS = ("ac", "lossy_dc", "singlenode_dc")
ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_fingerprint() -> str:
    paths = sorted((ROOT / "src" / "cvxopf").rglob("*.py")) + sorted(
        (ROOT / "experiments" / "m14_time_vectorization").rglob("*.py")
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git(*arguments: str) -> str | None:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _execution_context() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "worktree_clean": status == "" if status is not None else None,
        "source_fingerprint": _source_fingerprint(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "packages": {
            name: _package_version(name)
            for name in ("cvxpy", "clarabel", "cyipopt", "numpy", "scipy")
        },
    }


def _peak_rss_bytes() -> int:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(raw if sys.platform == "darwin" else raw * 1024)


def _strict_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def measure_point(
    formulation: str,
    horizon: int,
    case_name: BaselineCase,
    *,
    observer: Callable[[str, dict[str, Any]], None] | None = None,
    expected_commit: str | None = None,
    expected_source_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Measure one point; callers isolate points in separate processes."""
    context_before = _execution_context()
    if expected_commit is not None and context_before["git_commit"] != expected_commit:
        raise RuntimeError("worker Git commit does not match parent context")
    if (
        expected_source_fingerprint is not None
        and context_before["source_fingerprint"] != expected_source_fingerprint
    ):
        raise RuntimeError("worker source fingerprint does not match parent context")
    started = time.perf_counter()
    fixture = build_baseline_fixture(formulation, horizon=horizon, case_name=case_name)
    construction_seconds = time.perf_counter() - started
    build = fixture.build
    rss_after_construction = _peak_rss_bytes()
    source = characterize_source_graph(build)
    if observer is not None:
        observer(
            "construction",
            {
                "seconds": construction_seconds,
                "peak_rss_bytes": rss_after_construction,
                "source_structure": asdict(source),
            },
        )

    canonical = None
    canonicalization_seconds = None
    rss_after_canonicalization = None
    if build.is_convex:
        started = time.perf_counter()
        canonical = characterize_convex_canonicalization(build, backend="CPP")
        canonicalization_seconds = time.perf_counter() - started
        rss_after_canonicalization = _peak_rss_bytes()
        if observer is not None:
            observer(
                "canonicalization",
                {
                    "seconds": canonicalization_seconds,
                    "peak_rss_bytes": rss_after_canonicalization,
                    "canonical_structure": asdict(canonical),
                },
            )

    started = time.perf_counter()
    if build.is_convex:
        build.solve(canon_backend=cp.CPP_CANON_BACKEND)
    else:
        build.solve()
    solve_seconds = time.perf_counter() - started
    rss_after_solve = _peak_rss_bytes()
    if observer is not None:
        observer(
            "solve",
            {
                "seconds": solve_seconds,
                "peak_rss_bytes": rss_after_solve,
                "status": build.prob.status,
            },
        )

    started = time.perf_counter()
    result = extract_results(build)
    extraction_seconds = time.perf_counter() - started
    rss_after_extraction = _peak_rss_bytes()
    result_payload = json_result(result)
    residuals = audit_result(fixture, result)
    result_bytes = _strict_json_bytes(result_payload)
    if observer is not None:
        observer(
            "extraction",
            {
                "seconds": extraction_seconds,
                "peak_rss_bytes": rss_after_extraction,
                "result_schema": result_schema(result),
                "residuals": residuals,
                "serialized_result_bytes": len(result_bytes),
            },
        )
    context_after = _execution_context()
    if (
        context_after["git_commit"] != context_before["git_commit"]
        or context_after["source_fingerprint"] != context_before["source_fingerprint"]
    ):
        raise RuntimeError("execution source changed during worker measurement")

    return {
        "schema_version": 1,
        "execution_context": context_after,
        "case": case_name,
        "formulation": formulation,
        "temporal_assembly": build.temporal_assembly,
        "canonicalization_backend": ("CPP" if build.is_convex else "DNLP_IPOPT"),
        "horizon": horizon,
        "timing_seconds": {
            "construction": construction_seconds,
            "canonicalization": canonicalization_seconds,
            "solve_after_explicit_canonicalization": solve_seconds,
            "extraction": extraction_seconds,
        },
        "peak_rss_bytes": {
            "after_construction": rss_after_construction,
            "after_canonicalization": rss_after_canonicalization,
            "after_solve": rss_after_solve,
            "after_extraction": rss_after_extraction,
        },
        "source_structure": asdict(source),
        "canonical_structure": None if canonical is None else asdict(canonical),
        "result_schema": result_schema(result),
        "residuals": residuals,
        "result": result_payload,
        "serialized_result_bytes": len(result_bytes),
        "status": result["status"],
    }


def _write_immutable(path: Path, payload: object) -> None:
    _write_immutable_bytes(path, _strict_json_bytes(payload))


def _write_immutable_bytes(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o644)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _append_phase(path: Path, phase: str, payload: dict[str, Any]) -> None:
    line = _strict_json_bytes({"phase": phase, **payload})
    with path.open("ab") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def _artifact_identity(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _validate_worker_artifact(
    path: Path,
    *,
    formulation: str,
    horizon: int,
    case_name: str,
    parent_context: dict[str, Any],
) -> dict[str, Any]:
    payload = cast(dict[str, Any], json.loads(path.read_text()))
    if payload["formulation"] != formulation:
        raise ValueError("worker artifact formulation mismatch")
    if int(payload["horizon"]) != horizon:
        raise ValueError("worker artifact horizon mismatch")
    if payload["case"] != case_name:
        raise ValueError("worker artifact case mismatch")
    context = payload["execution_context"]
    for key in ("git_commit", "source_fingerprint"):
        if context[key] != parent_context[key]:
            raise ValueError(f"worker artifact {key} mismatch")
    return payload


def _validate_phase_journal(path: Path, *, convex: bool) -> None:
    records = [json.loads(line) for line in path.read_text().splitlines()]
    expected = ["construction"]
    if convex:
        expected.append("canonicalization")
    expected.extend(("solve", "extraction"))
    if [record.get("phase") for record in records] != expected:
        raise ValueError("worker phase journal is incomplete or out of order")


def _run_parent(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    parent_context = _execution_context()
    if not parent_context["git_commit"] or not parent_context["source_fingerprint"]:
        raise RuntimeError("parent execution provenance is unavailable")
    if not args.horizons or any(horizon <= 0 for horizon in args.horizons):
        raise ValueError("horizons must contain positive integers")
    if args.timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    output.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    for formulation in args.formulations:
        for horizon in args.horizons:
            destination = output / f"{formulation}-{horizon:05d}.json"
            command = [
                sys.executable,
                "-m",
                "experiments.m14_time_vectorization.run_m14a",
                "--worker",
                "--formulation",
                formulation,
                "--horizon",
                str(horizon),
                "--case",
                args.case,
                "--output",
                str(destination),
                "--expected-commit",
                str(parent_context["git_commit"]),
                "--expected-source-fingerprint",
                str(parent_context["source_fingerprint"]),
            ]
            classification = "completed"
            try:
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    timeout=args.timeout_seconds,
                )
                returncode = completed.returncode
                stdout = completed.stdout
                stderr = completed.stderr
                if returncode != 0:
                    classification = "worker_failure"
            except subprocess.TimeoutExpired as error:
                returncode = None
                classification = "wall_time_limit"
                stdout = error.stdout or b""
                stderr = error.stderr or b""
            log_path = destination.with_suffix(".log")
            _write_immutable_bytes(
                log_path,
                b"--- stdout ---\n" + stdout + b"\n--- stderr ---\n" + stderr,
            )
            artifact = None
            if classification == "completed":
                if not destination.exists():
                    classification = "artifact_missing"
                else:
                    try:
                        payload = _validate_worker_artifact(
                            destination,
                            formulation=formulation,
                            horizon=horizon,
                            case_name=args.case,
                            parent_context=parent_context,
                        )
                        phase_path = destination.with_suffix(".phases.jsonl")
                        _validate_phase_journal(phase_path, convex=formulation != "ac")
                    except (
                        FileNotFoundError,
                        KeyError,
                        TypeError,
                        ValueError,
                        json.JSONDecodeError,
                    ):
                        classification = "artifact_invalid"
                    else:
                        artifact = _artifact_identity(destination)
                        if payload["status"] not in {
                            cp.OPTIMAL,
                            cp.OPTIMAL_INACCURATE,
                        }:
                            classification = "solve_not_accepted"
            evidence = {"log": _artifact_identity(log_path)}
            phase_path = destination.with_suffix(".phases.jsonl")
            if phase_path.exists():
                evidence["phases"] = _artifact_identity(phase_path)
            if destination.exists() and artifact is None:
                evidence["unvalidated_result"] = _artifact_identity(destination)
            records.append(
                {
                    "formulation": formulation,
                    "horizon": horizon,
                    "classification": classification,
                    "returncode": returncode,
                    "artifact": artifact,
                    "evidence": evidence,
                }
            )
            if classification != "completed":
                break
    _write_immutable(
        output / "manifest.json",
        {
            "schema_version": 1,
            "execution_context": parent_context,
            "timeout_seconds_per_point": args.timeout_seconds,
            "records": records,
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--formulation", choices=FORMULATIONS)
    parser.add_argument("--horizon", type=int)
    parser.add_argument("--case", choices=("case9", "case118"), default="case9")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--formulations", nargs="+", choices=FORMULATIONS, default=FORMULATIONS
    )
    parser.add_argument("--horizons", nargs="+", type=int, default=(1, 2, 4, 8))
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-source-fingerprint")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.worker:
        if args.formulation is None or args.horizon is None:
            raise SystemExit("worker requires --formulation and --horizon")
        phase_path = args.output.resolve().with_suffix(".phases.jsonl")
        _write_immutable_bytes(phase_path, b"")
        record = measure_point(
            args.formulation,
            args.horizon,
            args.case,
            observer=lambda phase, payload: _append_phase(phase_path, phase, payload),
            expected_commit=args.expected_commit,
            expected_source_fingerprint=args.expected_source_fingerprint,
        )
        _write_immutable(args.output.resolve(), record)
    else:
        _run_parent(args)


if __name__ == "__main__":
    main()
