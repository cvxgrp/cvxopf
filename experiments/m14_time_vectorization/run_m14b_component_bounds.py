"""Run and retain the M14b formulation/component box qualification matrix."""

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

from experiments.m14_time_vectorization.m14b_component_bounds import (
    AUDIT_TOLERANCE,
    CLARABEL_SOLVE_OPTIONS,
    DELTA_HOURS,
    GATE_PAIRS,
    HORIZON,
    PAIR_ABSOLUTE_TOLERANCE,
    BoundEncoding,
    Formulation,
    GateName,
    compare_pair,
    pair_decisions,
    run_arm,
)


ROOT = Path(__file__).resolve().parents[2]
RESULT_PATH = (
    ROOT / "experiments/m14_time_vectorization/M14B_COMPONENT_BOX_RESULTS.json"
)


def _git(*arguments: str) -> str | None:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
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
    """Return source, machine, and solver provenance for one process."""
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "worktree_clean": _git("status", "--porcelain") == "",
        "source_fingerprint": _source_fingerprint(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "packages": {
            name: _package_version(name)
            for name in ("cvxpy", "clarabel", "numpy", "scipy")
        },
    }


def _run_worker(
    formulation: Formulation,
    gate: GateName,
    encoding: BoundEncoding,
    output: Path,
    expected_context: dict[str, Any],
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "experiments.m14_time_vectorization.run_m14b_component_bounds",
        "--worker",
        "--formulation",
        formulation,
        "--gate",
        gate,
        "--encoding",
        encoding,
        "--output",
        str(output),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"M14b component-box worker failed for "
            f"{formulation}/{gate}/{encoding}: {completed.stderr[-2000:]}"
        )
    payload = cast(dict[str, Any], json.loads(output.read_text()))
    context = cast(dict[str, Any], payload.pop("execution_context"))
    for name in ("git_commit", "source_fingerprint"):
        if context.get(name) != expected_context.get(name):
            raise RuntimeError(f"M14b component-box worker {name} mismatch")
    if context.get("worktree_clean") is not True:
        raise RuntimeError("M14b component-box worker observed a dirty worktree")
    return payload


def build_result() -> dict[str, Any]:
    """Execute every arm in a fresh process under stable source provenance."""
    before = execution_context()
    if before["worktree_clean"] is not True:
        raise RuntimeError(
            "authoritative M14b component-box execution requires a clean worktree"
        )
    pairs = []
    decisions: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="cvxopf-m14b-box-") as temporary:
        directory = Path(temporary)
        for formulation, gate in GATE_PAIRS:
            arms = {
                encoding: _run_worker(
                    formulation,
                    gate,
                    encoding,
                    directory / f"{formulation}-{gate}-{encoding}.json",
                    before,
                )
                for encoding in ("explicit", "leaf")
            }
            pair = compare_pair(arms["explicit"], arms["leaf"])
            pairs.append(pair)
            for family, decision in pair_decisions(pair).items():
                decisions[f"{formulation}/{family}"] = decision
    after = execution_context()
    if (
        after["git_commit"] != before["git_commit"]
        or after["source_fingerprint"] != before["source_fingerprint"]
    ):
        raise RuntimeError("M14b component-box execution source changed")
    if after["worktree_clean"] is not True:
        raise RuntimeError("M14b component-box worktree changed during execution")
    return {
        "schema_version": 1,
        "stage": "M14b_component_box_qualification",
        "horizon": HORIZON,
        "delta_hours": DELTA_HOURS,
        "audit_tolerance": AUDIT_TOLERANCE,
        "pair_absolute_tolerance": PAIR_ABSOLUTE_TOLERANCE,
        "solver": "CLARABEL",
        "solver_options": dict(CLARABEL_SOLVE_OPTIONS),
        "canonicalization_backend": "SCIPY",
        "fresh_process_per_arm": True,
        "cross_formulation_inference_permitted": False,
        "pairs": pairs,
        "decisions": decisions,
        "execution_context": after,
    }


def write_immutable(path: Path, value: object) -> None:
    """Publish one JSON result without permitting replacement."""
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
    parser.add_argument("--formulation", choices=("lossy_dc", "singlenode_dc"))
    parser.add_argument(
        "--gate",
        choices=("storage", "nondispatchable", "load_shedding", "hvdc"),
    )
    parser.add_argument("--encoding", choices=("explicit", "leaf"))
    arguments = parser.parse_args()
    if arguments.worker:
        if (
            arguments.formulation is None
            or arguments.gate is None
            or arguments.encoding is None
        ):
            parser.error("worker mode requires formulation, gate, and encoding")
        result = run_arm(
            arguments.formulation,
            arguments.gate,
            arguments.encoding,
        )
        write_immutable(
            arguments.output.resolve(),
            {**result, "execution_context": execution_context()},
        )
        return
    result = build_result()
    write_immutable(arguments.output.resolve(), result)
    print(json.dumps(result["decisions"], sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
