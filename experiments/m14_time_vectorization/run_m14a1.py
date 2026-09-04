"""Run and retain the isolated M14a.1 leaf-bound qualification matrix."""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from typing import Any, cast

from experiments.m14_time_vectorization.m14a1_bounds import (
    BOUND_PROFILES,
    FORMULATIONS,
    BoundEncoding,
    BoundProfile,
    Formulation,
    compare_pair,
    formulation_decision,
    run_qualification,
)


ROOT = Path(__file__).resolve().parents[2]
RESULT_PATH = ROOT / "experiments/m14_time_vectorization/M14A1_RESULTS.json"


def _git(*arguments: str) -> str | None:
    result = subprocess.run(
        ["git", *arguments], cwd=ROOT, check=False, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _source_fingerprint() -> str:
    paths = sorted((ROOT / "src/cvxopf").rglob("*.py")) + sorted(
        (ROOT / "experiments/m14_time_vectorization").rglob("*.py")
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def execution_context() -> dict[str, Any]:
    try:
        from cyipopt import IPOPT_VERSION
    except ImportError:
        ipopt = None
    else:
        ipopt = ".".join(str(item) for item in IPOPT_VERSION)
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "worktree_clean": _git("status", "--porcelain") == "",
        "source_fingerprint": _source_fingerprint(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "packages": {
            name: _package_version(name)
            for name in ("cvxpy", "clarabel", "cyipopt", "numpy", "scipy")
        }
        | {"ipopt": ipopt},
    }


def build_result() -> dict[str, Any]:
    """Execute the matrix under stable source provenance."""
    before = execution_context()
    if before["worktree_clean"] is not True:
        raise RuntimeError("authoritative M14a.1 execution requires a clean worktree")
    pairs = []
    decisions: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="cvxopf-m14a1-") as temporary:
        directory = Path(temporary)
        for formulation in FORMULATIONS:
            formulation_pairs = []
            for profile in BOUND_PROFILES:
                arms = {
                    encoding: _run_worker(
                        formulation,
                        encoding,
                        profile,
                        directory / f"{formulation}-{profile}-{encoding}.json",
                        before,
                    )
                    for encoding in ("explicit", "leaf")
                }
                pair = compare_pair(arms["explicit"], arms["leaf"])
                pairs.append(pair)
                formulation_pairs.append(pair)
            decisions[formulation] = formulation_decision(
                formulation, formulation_pairs
            )
    result = {
        "schema_version": 1,
        "stage": "M14a.1_leaf_bound_qualification",
        "horizon": 3,
        "audit_tolerance": 1e-6,
        "pair_absolute_tolerance": 2e-4,
        "fresh_process_per_arm": True,
        "cross_formulation_inference_permitted": False,
        "decisions": decisions,
        "pairs": pairs,
    }
    after = execution_context()
    if (
        after["git_commit"] != before["git_commit"]
        or after["source_fingerprint"] != before["source_fingerprint"]
    ):
        raise RuntimeError("M14a.1 execution source changed during the run")
    if after["worktree_clean"] is not True:
        raise RuntimeError("M14a.1 worktree changed during the run")
    return {**result, "execution_context": after}


def _run_worker(
    formulation: Formulation,
    encoding: BoundEncoding,
    profile: BoundProfile,
    output: Path,
    expected_context: dict[str, Any],
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "experiments.m14_time_vectorization.run_m14a1",
        "--worker",
        "--formulation",
        formulation,
        "--encoding",
        encoding,
        "--profile",
        profile,
        "--output",
        str(output),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, check=False, capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"M14a.1 worker failed for {formulation}/{profile}/{encoding}: "
            f"{completed.stderr[-2000:]}"
        )
    payload = cast(dict[str, Any], json.loads(output.read_text()))
    context = payload.pop("execution_context")
    for name in ("git_commit", "source_fingerprint"):
        if context.get(name) != expected_context.get(name):
            raise RuntimeError(f"M14a.1 worker {name} mismatch")
    if context.get("worktree_clean") is not True:
        raise RuntimeError("M14a.1 worker observed a dirty worktree")
    return payload


def write_immutable(path: Path, value: object) -> None:
    data = (
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=RESULT_PATH)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--formulation", choices=FORMULATIONS)
    parser.add_argument("--encoding", choices=("explicit", "leaf"))
    parser.add_argument("--profile", choices=BOUND_PROFILES)
    arguments = parser.parse_args()
    if arguments.worker:
        if (
            arguments.formulation is None
            or arguments.encoding is None
            or arguments.profile is None
        ):
            parser.error("worker mode requires formulation, encoding, and profile")
        worker_result = run_qualification(
            arguments.formulation, arguments.encoding, arguments.profile
        )
        write_immutable(
            arguments.output.resolve(),
            {**worker_result, "execution_context": execution_context()},
        )
        return
    result = build_result()
    write_immutable(arguments.output.resolve(), result)
    print(json.dumps(result["decisions"], sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
