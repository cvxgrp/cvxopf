"""Frozen authority, schedule, and provenance for Case118 S5 execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import subprocess
from typing import Mapping, Sequence, cast

from experiments.case118_annual_hierarchy.run_s0 import ROOT, _software_versions
from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.s4b_manifest import (
    EXPECTED_MANIFEST_SHA256,
    load_verified_manifest,
    object_sha256,
)
from experiments.case118_annual_hierarchy.streaming_schema import sha256_path


SCHEMA_VERSION = 1
EXPERIMENT_DIR = ROOT / "experiments/case118_annual_hierarchy"
MANIFEST_USE_AUTHORITY_PATH = EXPERIMENT_DIR / "S5_EXECUTION_AUTHORITY.json"
NUMERICAL_AUTHORITY_FILENAME = "S5_NUMERICAL_EXECUTION_AUTHORITY.json"
DEFAULT_NUMERICAL_AUTHORITY_PATH = EXPERIMENT_DIR / NUMERICAL_AUTHORITY_FILENAME
S4B_RESULTS_PATH = EXPERIMENT_DIR / "S4B_RESULTS.json"
S4B_RESULTS_SHA256 = "45dd2e81b8732bd7729ad02edf86c56d021e3d2f6ed5fa934053efc59b09f2ce"
S4B_ANALYSIS_SHA256 = "9c8d1608ffcf5fb084ffbb3cfa95dcd88d35b73374d765be7e0e8e56a80ac0d3"
MANIFEST_USE_AUTHORITY_SHA256 = (
    "64c3a511886c91d80c2a1c50b06fcdcce4108421e3bdb0eb855a78868d961338"
)
QUALIFICATION_RESULT_COMMIT = "ff321d1bd49e2de556f27967a27f78fbb4ac0c60"
QUALIFICATION_EXECUTION_COMMIT = "cc539d880bea04a75a287e7d534507d588852b05"
QUALIFICATION_REGISTRY_SHA256 = (
    "e9e695346df50af4d663454f9f750ec7b2c81d4ee1b6bbcb0e4c65f2884d7a3a"
)
MAXIMUM_CONCURRENCY = 2
PER_WORKER_RSS_LIMIT_MIB = 16_384.0
AGGREGATE_RSS_LIMIT_MIB = 24_576.0
ANNUAL_SHARD_IDS = tuple(f"s4b-shard-{index:03d}" for index in range(12))
ANNUAL_WAVES = tuple(
    (ANNUAL_SHARD_IDS[index], ANNUAL_SHARD_IDS[index + 1])
    for index in range(0, len(ANNUAL_SHARD_IDS), 2)
)

SOURCE_FILES = (
    "experiments/case118_annual_hierarchy/FIVE_MINUTE_TIMEOUT_POLICY.md",
    "experiments/case118_annual_hierarchy/S4B_PROTOCOL.md",
    "experiments/case118_annual_hierarchy/S4B_SHARD_MANIFEST.json",
    "experiments/case118_annual_hierarchy/S5_EXECUTION_AUTHORITY.json",
    "experiments/case118_annual_hierarchy/S5_PROTOCOL.md",
    "experiments/case118_annual_hierarchy/S5_INTERVAL_2448_INTERVENTION.md",
    "experiments/case118_annual_hierarchy/audit.py",
    "experiments/case118_annual_hierarchy/p0_fixture.py",
    "experiments/case118_annual_hierarchy/run_s0.py",
    "experiments/case118_annual_hierarchy/run_s4b.py",
    "experiments/case118_annual_hierarchy/run_s5.py",
    "experiments/case118_annual_hierarchy/s2_analysis.py",
    "experiments/case118_annual_hierarchy/s4_fixture.py",
    "experiments/case118_annual_hierarchy/s4b_execution.py",
    "experiments/case118_annual_hierarchy/s4b_manifest.py",
    "experiments/case118_annual_hierarchy/s5_execution.py",
    "experiments/case118_annual_hierarchy/s5_operator_intervention.py",
    "experiments/case118_annual_hierarchy/s5_repaired_window.py",
    "experiments/case118_annual_hierarchy/s5_retry_transition.py",
    "experiments/case118_annual_hierarchy/s5_source_transition.py",
    "experiments/case118_annual_hierarchy/S5_SPECULATIVE_RECOVERY_PLAN.md",
    "experiments/case118_annual_hierarchy/S5_COMPLETED_PREFIX_ANCHOR.json",
    "experiments/case118_annual_hierarchy/s5_speculative_archive.py",
    "experiments/case118_annual_hierarchy/s5_speculative_attempt.py",
    "experiments/case118_annual_hierarchy/s5_speculative_continuation.py",
    "experiments/case118_annual_hierarchy/s5_speculative_policy.py",
    "experiments/case118_annual_hierarchy/s5_speculative_process.py",
    "experiments/case118_annual_hierarchy/s5_speculative_runtime.py",
    "experiments/case118_annual_hierarchy/s5_speculative_supervisor.py",
    "experiments/case118_annual_hierarchy/s5_speculative_transaction.py",
    "experiments/case118_annual_hierarchy/s5_speculative_window.py",
    "experiments/case118_annual_hierarchy/s5_speculative_worker.py",
    "experiments/case118_annual_hierarchy/s5_prefix_anchor.py",
    "experiments/case118_annual_hierarchy/streaming_archive.py",
    "experiments/case118_annual_hierarchy/streaming_runner.py",
    "experiments/case118_annual_hierarchy/streaming_schema.py",
)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return cast(Mapping[str, object], value)


def source_paths() -> tuple[Path, ...]:
    """Return every tracked source imported by an S5 worker or supervisor."""
    paths = [ROOT / name for name in SOURCE_FILES]
    paths.extend((ROOT / "src/cvxopf").rglob("*.py"))
    result = tuple(sorted(set(paths)))
    missing = [
        path.relative_to(ROOT).as_posix() for path in result if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(f"S5 source registry is incomplete: {missing}")
    return result


def source_fingerprint() -> str:
    """Hash the complete S5 execution source registry."""
    digest = hashlib.sha256()
    for path in source_paths():
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def execution_context() -> Mapping[str, object]:
    """Capture the exact S5 execution environment before mutation."""
    fixture = load_s4_fixture()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    registry = annual_registry()
    return {
        "git_commit": commit,
        "git_clean": not bool(status.strip()),
        "source_fingerprint": source_fingerprint(),
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "annual_registry_sha256": registry["registry_sha256"],
        "s4b_results_sha256": sha256_path(S4B_RESULTS_PATH),
        "scenario_sha256": fixture.scenario_hash,
        "policy_sha256": fixture.policy_sha256,
        "solve_config_sha256": fixture.solve_config_sha256,
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "software_versions": dict(_software_versions()),
    }


def execution_identity_unchanged(
    start: Mapping[str, object], end: Mapping[str, object]
) -> bool:
    """Ignore incidental Git-state changes while scientific bytes stay fixed.

    Entry still binds a clean reviewed commit. An unrelated file or a commit
    containing only non-execution material can change ``git_clean`` or HEAD
    during a long solve. The child retains the original bound commit and
    records the observed drift, but never accepts changed executable-source,
    scientific-input, solver, or environment fingerprints.
    """
    incidental = {"git_clean", "git_commit"}
    return {key: value for key, value in start.items() if key not in incidental} == {
        key: value for key, value in end.items() if key not in incidental
    }


def load_manifest_use_authority(
    path: Path = MANIFEST_USE_AUTHORITY_PATH,
) -> Mapping[str, object]:
    """Validate the reviewed S4b advancement decision without enabling solves."""
    if sha256_path(path) != MANIFEST_USE_AUTHORITY_SHA256:
        raise ValueError("S5 manifest-use authority hash mismatch")
    value = _mapping(json.loads(path.read_text()), "S5 manifest-use authority")
    results = _mapping(json.loads(S4B_RESULTS_PATH.read_text()), "S4b result")
    expected = {
        "schema_version": SCHEMA_VERSION,
        "classification": "reviewed_s5_annual_manifest_use_authorized",
        "execution_scope": "annual_manifest_s5_planning_and_implementation",
        "annual_manifest_use_authorized": True,
        "s5_numerical_execution_authorized": False,
        "authorization_parent_commit": QUALIFICATION_RESULT_COMMIT,
        "qualification_result_commit": QUALIFICATION_RESULT_COMMIT,
        "qualification_execution_commit": QUALIFICATION_EXECUTION_COMMIT,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "qualification_registry_sha256": QUALIFICATION_REGISTRY_SHA256,
        "s4b_results_sha256": S4B_RESULTS_SHA256,
        "s4b_analysis_sha256": S4B_ANALYSIS_SHA256,
        "s4b_execution_source_fingerprint": (
            "050648b7ff027fb49c8ae692f1dc9797788017d977345700a3fee138ee70d91d"
        ),
    }
    if (
        value != expected
        or sha256_path(S4B_RESULTS_PATH) != S4B_RESULTS_SHA256
        or results.get("analysis_sha256") != S4B_ANALYSIS_SHA256
        or results.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or results.get("accepted_for_s5") is not True
        or results.get("execution_complete") is not True
    ):
        raise ValueError(
            "S5 manifest-use authority does not bind accepted S4b evidence"
        )
    return value


def load_numerical_authority(
    path: Path,
    *,
    expected_execution_commit: str,
    expected_source_fingerprint: str,
) -> Mapping[str, object]:
    """Require separately reviewed numerical authority before S5 mutation."""
    load_manifest_use_authority()
    if not path.is_file():
        raise ValueError("S5 numerical execution remains unauthorized")
    value = _mapping(json.loads(path.read_text()), "S5 numerical authority")
    registry = annual_registry()
    expected = {
        "schema_version": SCHEMA_VERSION,
        "classification": "reviewed_s5_numerical_execution_authorized",
        "execution_scope": "annual_8760h_hierarchical_ac",
        "annual_execution_authorized": True,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "annual_registry_sha256": registry["registry_sha256"],
        "manifest_use_authority_sha256": MANIFEST_USE_AUTHORITY_SHA256,
        "s4b_results_sha256": S4B_RESULTS_SHA256,
        "s4b_analysis_sha256": S4B_ANALYSIS_SHA256,
        "maximum_concurrency": MAXIMUM_CONCURRENCY,
        "per_worker_current_rss_mib": PER_WORKER_RSS_LIMIT_MIB,
        "aggregate_current_rss_mib": AGGREGATE_RSS_LIMIT_MIB,
        "execution_commit": expected_execution_commit,
        "source_fingerprint": expected_source_fingerprint,
    }
    transition_hash = value.get("source_version_contract_sha256")
    if value.get("recovery_policy") is not None:
        from experiments.case118_annual_hierarchy.s5_speculative_policy import (
            POLICY_NAME,
        )

        if value["recovery_policy"] != POLICY_NAME or transition_hash is None:
            raise ValueError("speculative execution needs reviewed policy continuation")
        expected["recovery_policy"] = POLICY_NAME
        expected["maximum_solver_processes"] = 3
        revision = value.get("recovery_policy_revision")
        if revision is not None:
            if revision != 2:
                raise ValueError("speculative execution policy revision mismatch")
            expected["recovery_policy_revision"] = 2
    if transition_hash is not None:
        if (
            not isinstance(transition_hash, str)
            or len(transition_hash) != 64
            or any(c not in "0123456789abcdef" for c in transition_hash)
        ):
            raise ValueError("S5 source-version authority needs a contract hash")
        expected["source_version_contract_sha256"] = transition_hash
    if value != expected:
        raise ValueError("S5 numerical authority does not match the frozen run")
    return value


def annual_registry() -> Mapping[str, object]:
    """Return the immutable annual shard registry after exact identity checks."""
    envelope = load_verified_manifest()
    manifest = _mapping(envelope["manifest"], "S4b annual manifest")
    shards = cast(Sequence[Mapping[str, object]], manifest["shards"])
    ids = tuple(str(item["shard_id"]) for item in shards)
    if ids != ANNUAL_SHARD_IDS or manifest.get("boundary_indices") != [
        0,
        682,
        1452,
        2213,
        2965,
        3723,
        4468,
        5211,
        5956,
        6726,
        7475,
        8187,
        8760,
    ]:
        raise ValueError("S5 annual shard registry differs from reviewed manifest")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "classification": "frozen_s5_annual_registry",
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "horizon_steps": 8_760,
        "maximum_concurrency": MAXIMUM_CONCURRENCY,
        "waves": [list(item) for item in ANNUAL_WAVES],
        "shard_ids": list(ids),
    }
    return {**payload, "registry_sha256": object_sha256(payload)}


def wave_index_for_request(shard_ids: Sequence[str]) -> int:
    """Require one complete wave or a reviewed subset of exactly one wave."""
    request = tuple(shard_ids)
    if (
        not request
        or len(request) > MAXIMUM_CONCURRENCY
        or len(set(request)) != len(request)
    ):
        raise ValueError("S5 wave request requires one or two unique shards")
    matches = [
        index for index, wave in enumerate(ANNUAL_WAVES) if set(request) <= set(wave)
    ]
    if len(matches) != 1:
        raise ValueError("S5 shard request crosses the frozen wave schedule")
    return matches[0]


__all__ = [
    "AGGREGATE_RSS_LIMIT_MIB",
    "ANNUAL_SHARD_IDS",
    "ANNUAL_WAVES",
    "DEFAULT_NUMERICAL_AUTHORITY_PATH",
    "MAXIMUM_CONCURRENCY",
    "NUMERICAL_AUTHORITY_FILENAME",
    "PER_WORKER_RSS_LIMIT_MIB",
    "SCHEMA_VERSION",
    "S4B_RESULTS_SHA256",
    "SOURCE_FILES",
    "annual_registry",
    "execution_context",
    "load_manifest_use_authority",
    "load_numerical_authority",
    "source_fingerprint",
    "source_paths",
    "wave_index_for_request",
]
