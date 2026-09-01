"""Independently reconstruct and promote the S4 annual outer result."""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from typing import Mapping, cast

import numpy as np

from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.run_s4 import (
    S4_SOURCE_FILES,
    s4_source_fingerprint,
)
from experiments.case118_annual_hierarchy.s4_fixture import (
    S4_EXECUTION_LIMITS,
    S4_OUTPUT_DIRECTORY,
    load_s4_fixture,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    load_verified_outer_plan_archive,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    sha256_path,
)


DEFAULT_DESTINATION = Path("experiments/case118_annual_hierarchy/S4_RESULTS.json")
EXPECTED_EQUIVALENCE_DIMENSIONS: Mapping[str, int] = {
    "scalar_variables": 6004,
    "scalar_equalities": 2936,
    "explicit_scalar_inequalities": 0,
    "other_scalar_constraints": 0,
    "constraint_objects": 7,
}
EXPECTED_EQUIVALENCE_INPUT_SHA256 = (
    "54c86688e700c0166c3d0eafdab46d24095b055cdef589c3e7c8f7bb50c01630"
)
ANALYSIS_SOURCE_FILES = (
    *S4_SOURCE_FILES,
    "experiments/case118_annual_hierarchy/s4_analysis.py",
)


def analysis_source_paths() -> tuple[Path, ...]:
    paths = [ROOT / name for name in ANALYSIS_SOURCE_FILES]
    paths.extend((ROOT / "src/cvxopf").rglob("*.py"))
    result = tuple(sorted(set(paths)))
    if any(not path.is_file() for path in result):
        raise FileNotFoundError("S4 analysis source registry is incomplete")
    return result


def analysis_source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in analysis_source_paths():
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def analysis_context() -> Mapping[str, object]:
    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    return {
        "git_commit": git("rev-parse", "HEAD"),
        "git_clean": git("status", "--porcelain") == "",
        "analysis_source_fingerprint": analysis_source_fingerprint(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
    }


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return cast(Mapping[str, object], value)


def _number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or not np.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _sha256(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _shape_size(value: object) -> int:
    if not isinstance(value, list) or any(
        not isinstance(item, int) or item < 0 for item in value
    ):
        raise ValueError("S4 structural shape is invalid")
    return int(np.prod(value, dtype=int)) if value else 1


def _archived_dimensions(path: Path) -> Mapping[str, int]:
    """Reconstruct scalar structural counts from the retained signature."""
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = _mapping(json.load(stream), "S4 outer archive")
    signature = _mapping(payload.get("structural_signature"), "S4 structure")
    variables = cast(list[object], signature.get("variables"))
    constraints = cast(list[object], signature.get("constraints"))
    scalar_variables = sum(
        _shape_size(_mapping(item, "S4 variable signature").get("shape"))
        for item in variables
    )
    counts = {"Equality": 0, "Inequality": 0, "other": 0}
    for item in constraints:
        if not isinstance(item, str) or "|shape=" not in item:
            raise ValueError("S4 constraint signature is malformed")
        kind, encoded_shape = item.split("|shape=", maxsplit=1)
        parsed = ast.literal_eval(encoded_shape)
        shape = [] if parsed == () else list(cast(tuple[int, ...], parsed))
        key = kind if kind in {"Equality", "Inequality"} else "other"
        counts[key] += _shape_size(shape)
    return {
        "scalar_variables": scalar_variables,
        "scalar_equalities": counts["Equality"],
        "explicit_scalar_inequalities": counts["Inequality"],
        "other_scalar_constraints": counts["other"],
        "constraint_objects": len(constraints),
    }


def analyze_s4(directory: Path = S4_OUTPUT_DIRECTORY) -> Mapping[str, object]:
    """Verify supervision and independently reconstruct the accepted outer plan."""
    directory = directory.expanduser().resolve()
    forbidden = (
        tuple(directory.rglob("checkpoint.json"))
        + tuple(directory.rglob("window-*.json.gz"))
        + tuple(directory.rglob("failed-window-*.json.gz"))
    )
    if forbidden:
        raise ValueError("S4 outer-only directory contains AC trajectory artifacts")
    if (directory / "active-worker.json").exists():
        raise ValueError("S4 worker is still marked active")
    context_path = directory / "execution-context.json"
    equivalence_path = directory / "outer-equivalence.json"
    worker_path = directory / "worker-result.json"
    supervision_path = directory / "supervision.json"
    outer_path = directory / "outer-plan.json.gz"
    required = (
        context_path,
        equivalence_path,
        worker_path,
        supervision_path,
        outer_path,
    )
    if any(not path.is_file() for path in required):
        raise ValueError("S4 execution artifact set is incomplete")
    context = _mapping(json.loads(context_path.read_text()), "S4 context")
    equivalence = _mapping(
        json.loads(equivalence_path.read_text()), "S4 outer equivalence"
    )
    worker = _mapping(json.loads(worker_path.read_text()), "S4 worker")
    supervision = _mapping(json.loads(supervision_path.read_text()), "S4 supervision")
    fixture = load_s4_fixture()
    required_equivalence = {
        "schema_version",
        "horizon_steps",
        "equivalent",
        "mismatches",
        "formulation",
        "temporal_assembly",
        "canonicalization_backend",
        "public_dimensions",
        "streaming_dimensions",
        "public_status",
        "streaming_status",
        "storage_device_ids",
        "global_boundary_indices_sha256",
        "public_summary",
        "streaming_summary",
        "public_boundary_sha256",
        "streaming_boundary_sha256",
        "public_residuals",
        "streaming_residuals",
        "audit_schema_projection",
        "public_fingerprints",
        "streaming_fingerprints",
        "fingerprints_match",
    }
    if set(equivalence) != required_equivalence:
        raise ValueError("S4 outer equivalence evidence schema mismatch")
    public_fingerprints = _mapping(
        equivalence["public_fingerprints"], "S4 public fingerprints"
    )
    streaming_fingerprints = _mapping(
        equivalence["streaming_fingerprints"], "S4 streaming fingerprints"
    )
    public_summary = _mapping(equivalence["public_summary"], "S4 public summary")
    streaming_summary = _mapping(
        equivalence["streaming_summary"], "S4 streaming summary"
    )
    projection = _mapping(equivalence["audit_schema_projection"], "S4 audit projection")
    public_residuals = dict(
        _mapping(equivalence["public_residuals"], "S4 public residuals")
    )
    streaming_residuals = dict(
        _mapping(equivalence["streaming_residuals"], "S4 streaming residuals")
    )
    projected_public_residuals = {**public_residuals, "branch_mw_abs": 0.0}
    fingerprint_keys = {
        "input_sha256",
        "policy_sha256",
        "solve_config_sha256",
        "result_sha256",
        "boundary_sha256",
        "structure_sha256",
    }
    fingerprints_valid = bool(
        set(public_fingerprints) == fingerprint_keys
        and set(streaming_fingerprints) == fingerprint_keys
        and all(
            _sha256(value, f"S4 equivalence {name}")
            for name, value in public_fingerprints.items()
        )
    )
    _sha256(
        equivalence.get("global_boundary_indices_sha256"),
        "S4 equivalence boundary-index digest",
    )
    if (
        equivalence.get("equivalent") is not True
        or equivalence.get("horizon_steps") != 24
        or equivalence.get("mismatches") != []
        or equivalence.get("temporal_assembly") != fixture.temporal_assembly
        or equivalence.get("canonicalization_backend")
        != fixture.canonicalization_backend
        or equivalence.get("public_dimensions")
        != equivalence.get("streaming_dimensions")
        or equivalence.get("public_dimensions") != EXPECTED_EQUIVALENCE_DIMENSIONS
        or equivalence.get("fingerprints_match") is not True
        or public_fingerprints != streaming_fingerprints
        or not fingerprints_valid
        or public_fingerprints.get("input_sha256") != EXPECTED_EQUIVALENCE_INPUT_SHA256
        or public_summary.get("result_sha256") != streaming_summary.get("result_sha256")
        or public_summary.get("result_sha256")
        != public_fingerprints.get("result_sha256")
        or streaming_summary.get("result_sha256")
        != streaming_fingerprints.get("result_sha256")
        or public_summary.get("result_schema") != streaming_summary.get("result_schema")
        or not np.isclose(
            _number(public_summary.get("objective"), "S4 public objective"),
            _number(streaming_summary.get("objective"), "S4 streaming objective"),
            rtol=0.0,
            atol=1e-9,
        )
        or equivalence.get("public_boundary_sha256")
        != equivalence.get("streaming_boundary_sha256")
        or equivalence.get("storage_device_ids") != list(fixture.storage_device_ids)
        or projected_public_residuals != streaming_residuals
        or public_fingerprints.get("policy_sha256") != fixture.policy_sha256
        or public_fingerprints.get("solve_config_sha256") != fixture.solve_config_sha256
        or projection
        != {
            "public_branch_mw_abs_present": False,
            "streaming_branch_mw_abs": 0.0,
            "projected_public_branch_mw_abs": 0.0,
        }
    ):
        raise ValueError("S4 outer equivalence evidence is not accepted")
    if (
        supervision.get("classification") != "accepted"
        or supervision.get("returncode") != 0
        or supervision.get("context_matches") is not True
        or supervision.get("start_context") != context
        or supervision.get("end_context") != context
        or supervision.get("worker_result") != worker
        or worker.get("classification") != "accepted"
        or worker.get("context_matches") is not True
        or worker.get("start_context") != context
        or worker.get("end_context") != context
        or _mapping(worker.get("outer_plan"), "S4 worker outer plan").get(
            "temporal_assembly"
        )
        != fixture.temporal_assembly
        or _mapping(worker.get("outer_plan"), "S4 worker outer plan").get(
            "canonicalization_backend"
        )
        != fixture.canonicalization_backend
    ):
        raise ValueError("S4 execution did not retain one accepted provenance chain")
    if context.get("source_fingerprint") != s4_source_fingerprint():
        raise ValueError("S4 tracked source fingerprint differs from execution")
    if (
        context.get("temporal_assembly") != fixture.temporal_assembly
        or context.get("canonicalization_backend") != fixture.canonicalization_backend
        or context.get("generator_quadratic_cost") != fixture.generator_quadratic_cost
        or context.get("generator_conditioning_evidence_sha256")
        != fixture.generator_conditioning_evidence_sha256
        or context.get("m14c_integration_checkpoint")
        != fixture.m14c_integration_checkpoint
        or context.get("m14c_source_commit") != fixture.m14c_source_commit
        or context.get("big_experiment_parent_commit")
        != fixture.big_experiment_parent_commit
        or context.get("m14c_merge_base_commit") != fixture.m14c_merge_base_commit
        or context.get("prefix_ladder_executed") is not True
        or context.get("annual_execution_authorized") is not True
        or context.get("m14c_representation_disposition_sha256")
        != fixture.m14c_representation_disposition_sha256
        or context.get("m14c_prefix_ladder_results_sha256")
        != fixture.m14c_prefix_ladder_results_sha256
        or context.get("m14c_integration_sha256") != fixture.m14c_integration_sha256
    ):
        raise ValueError("S4 execution integration provenance differs from fixture")
    if supervision.get("outer_plan_sha256") != sha256_path(outer_path):
        raise ValueError("S4 supervision outer-plan identity mismatch")
    policy = _mapping(supervision.get("resource_policy"), "S4 resource policy")
    expected_policy = {
        "rss_limit_mib": S4_EXECUTION_LIMITS.child_rss_mib,
        "worker_wall_seconds": S4_EXECUTION_LIMITS.worker_wall_seconds,
        "supervisor_wall_seconds": S4_EXECUTION_LIMITS.supervisor_wall_seconds,
        "poll_seconds": S4_EXECUTION_LIMITS.poll_seconds,
    }
    if policy != expected_policy:
        raise ValueError("S4 supervision resource policy mismatch")
    first_rss = _number(supervision["first_sampled_rss_mib"], "S4 first RSS")
    peak_rss = _number(supervision["peak_sampled_rss_mib"], "S4 peak RSS")
    if first_rss <= 0.0 or peak_rss <= 0.0:
        raise ValueError("S4 accepted execution lacks positive RSS evidence")
    if peak_rss > S4_EXECUTION_LIMITS.child_rss_mib:
        raise ValueError("S4 accepted execution exceeded its RSS limit")
    if _number(supervision["wall_time_seconds"], "S4 wall time") > (
        S4_EXECUTION_LIMITS.supervisor_wall_seconds
    ):
        raise ValueError("S4 accepted execution exceeded its wall limit")
    if _number(supervision["worker_wall_time_seconds"], "S4 worker wall time") > (
        S4_EXECUTION_LIMITS.worker_wall_seconds
    ):
        raise ValueError("S4 accepted worker exceeded its wall limit")
    outer = load_verified_outer_plan_archive(
        outer_path,
        inputs=fixture.inputs,
        policy=fixture.policy,
        expected_solve_config_sha256=fixture.solve_config_sha256,
        expected_source_fingerprint=str(context["source_fingerprint"]),
        expected_scenario_hash=fixture.scenario_hash,
    )
    if not outer.accepted_primal or outer.boundary_soc_mwh is None:
        raise ValueError("S4 annual outer plan is not independently accepted")
    if (
        outer.temporal_assembly != fixture.temporal_assembly
        or outer.canonicalization_backend != fixture.canonicalization_backend
    ):
        raise ValueError("S4 outer archive has the wrong temporal representation")
    dimensions = _mapping(worker.get("dimensions"), "S4 worker dimensions")
    if dimensions != _archived_dimensions(outer_path):
        raise ValueError("S4 structural counts do not reproduce from the archive")
    terminal_target = np.asarray(
        [unit.terminal_soc for unit in fixture.inputs.storage], dtype=float
    )
    final_soc = np.asarray(outer.boundary_soc_mwh[-1], dtype=float)
    terminal_residual = float(np.max(np.abs(final_soc - terminal_target)))
    samples = cast(list[object], worker.get("resource_samples"))
    phases = [
        str(_mapping(sample, "S4 resource sample")["phase"]) for sample in samples
    ]
    if phases != [
        "worker_start",
        "before_construction",
        "after_construction",
        "before_solve",
        "after_solve",
        "after_archive",
        "after_release",
    ]:
        raise ValueError("S4 worker phase registry is incomplete or out of order")
    return {
        "schema_version": 1,
        "execution_complete": True,
        "accepted_for_s4b": True,
        "classification": "accepted",
        "horizon_steps": fixture.inputs.horizon_steps,
        "delta_hours": fixture.inputs.delta,
        "temporal_assembly": outer.temporal_assembly,
        "canonicalization_backend": outer.canonicalization_backend,
        "generator_quadratic_cost": fixture.generator_quadratic_cost,
        "generator_conditioning_evidence_sha256": (
            fixture.generator_conditioning_evidence_sha256
        ),
        "m14c_integration_checkpoint": fixture.m14c_integration_checkpoint,
        "m14c_source_commit": fixture.m14c_source_commit,
        "big_experiment_parent_commit": fixture.big_experiment_parent_commit,
        "m14c_merge_base_commit": fixture.m14c_merge_base_commit,
        "prefix_ladder_executed": fixture.prefix_ladder_executed,
        "annual_execution_authorized": fixture.annual_execution_authorized,
        "m14c_representation_disposition_sha256": (
            fixture.m14c_representation_disposition_sha256
        ),
        "m14c_prefix_ladder_results_sha256": (
            fixture.m14c_prefix_ladder_results_sha256
        ),
        "m14c_integration_sha256": fixture.m14c_integration_sha256,
        "storage_device_ids": list(fixture.storage_device_ids),
        "objective": _number(outer.result.get("objective"), "S4 objective"),
        "terminal_soc_residual_mwh_abs": terminal_residual,
        "audit_residuals": dict(outer.audit.residuals),
        "dimensions": dimensions,
        "solve_wall_seconds": outer.wall_time_seconds,
        "worker_wall_seconds": supervision["worker_wall_time_seconds"],
        "supervisor_wall_seconds": supervision["wall_time_seconds"],
        "peak_supervisor_rss_mib": peak_rss,
        "resource_samples": samples,
        "execution_context": context,
        "analysis_context": analysis_context(),
        "outer_equivalence": equivalence,
        "artifacts": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)}
            for path in required
        },
    }


def promote_s4(result: Mapping[str, object], destination: Path) -> None:
    """Immutably publish only an independently accepted complete S4 result."""
    if (
        result.get("execution_complete") is not True
        or result.get("accepted_for_s4b") is not True
    ):
        raise ValueError("only a complete accepted S4 result may be promoted")
    current_analysis = analysis_context()
    if (
        result.get("analysis_context") != current_analysis
        or current_analysis.get("git_clean") is not True
    ):
        raise ValueError("S4 promotion requires its clean matching analyzer context")
    atomic_immutable_json(destination, result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=S4_OUTPUT_DIRECTORY)
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    arguments = parser.parse_args()
    result = analyze_s4(arguments.directory)
    if arguments.promote:
        promote_s4(result, arguments.destination)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
