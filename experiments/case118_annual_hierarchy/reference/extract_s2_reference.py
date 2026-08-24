"""Generate or verify the tracked S2 first-64 comparison reference."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence, cast

from experiments.case118_annual_hierarchy.p0_fixture import (
    frozen_p0_policy,
    policy_sha256,
)
from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.run_s2 import s2_source_fingerprint
from experiments.case118_annual_hierarchy.s2_fixture import (
    S2_HORIZON_STEPS,
    load_s2_fixture,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    load_verified_outer_plan_archive,
    outer_boundaries,
    residual_tolerances,
    result_dimensions,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    WindowIndexEntry,
    load_verified_checkpoint,
    sha256_path,
)
from experiments.case118_annual_hierarchy.streaming_runner import StreamingOuterPlan

EXPERIMENT_DIR = ROOT / "experiments/case118_annual_hierarchy"
REFERENCE_DIR = EXPERIMENT_DIR / "reference"
REFERENCE_PATH = REFERENCE_DIR / "s2_first64_reference.json"
SIDECAR_PATH = REFERENCE_DIR / "s2_first64_reference.sha256"
TRACKED_OUTER_PATH = REFERENCE_DIR / "outer-plan.json.gz"
S2_TRAJECTORY_DIR = EXPERIMENT_DIR / "results/s2_week_rated/trajectory"
S2_CHECKPOINT_PATH = S2_TRAJECTORY_DIR / "checkpoint.json"
S2_OUTER_PATH = S2_TRAJECTORY_DIR / "outer-plan.json.gz"
S2_METADATA_PATH = EXPERIMENT_DIR / "S2_RESULTS_METADATA.json"

SCHEMA_VERSION = 1
PREFIX_INTERVALS = 64
ACTUAL_START_BOUNDARIES = (0, 16, 32, 48)
HISTORICAL_SOURCE_FINGERPRINT = (
    "b62be721077ee5b5a3c61c93211197abeaf4e1ab5dabc2bc7d76b86b3520f4fd"
)
EXPECTED_OUTER_SHA256 = (
    "b4ac7b18b6e913e96991d5bbe217b01462c81be5b55587cbad17b2ca589d0b90"
)
EXPECTED_OUTER_BYTES = 717_010
EXPECTED_FINAL_CHECKPOINT_SHA256 = (
    "7e34a0ab1f00db2bfb164d1f9d6765231fdce8cd1a0f91f66dbc8230df546685"
)
EXPECTED_REFERENCE_SHA256 = (
    "174f76ae6f9726c8eb8b1953faf1b6282fc56ef6fc8ad03143090c23828eb979"
)


def canonical_bytes(value: object) -> bytes:
    """Serialize reference data deterministically."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return f"{text}\n".encode()


def _value_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return cast(Mapping[str, object], value)


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a sequence")
    return cast(Sequence[object], value)


def _load_json(path: Path) -> Mapping[str, object]:
    return _mapping(json.loads(path.read_text()), path.name)


def _load_gzip_json(path: Path) -> Mapping[str, object]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return _mapping(json.load(stream), path.name)


def _controlling_attempt(window: Mapping[str, object]) -> Mapping[str, object]:
    executed = _mapping(window.get("executed_interval"), "executed interval")
    controlling_id = executed.get("controlling_attempt_id")
    matches = [
        _mapping(item, "attempt")
        for item in _sequence(window.get("attempts"), "attempts")
        if _mapping(item, "attempt").get("attempt_id") == controlling_id
    ]
    if len(matches) != 1:
        raise ValueError("window must contain exactly one controlling attempt")
    return matches[0]


def _verify_outer(path: Path) -> StreamingOuterPlan:
    if not path.is_file() or path.stat().st_size != EXPECTED_OUTER_BYTES:
        raise ValueError("reference outer-plan byte count mismatch")
    if sha256_path(path) != EXPECTED_OUTER_SHA256:
        raise ValueError("reference outer-plan SHA-256 mismatch")
    fixture = load_s2_fixture()
    return load_verified_outer_plan_archive(
        path,
        inputs=fixture.inputs,
        policy=frozen_p0_policy(),
        expected_solve_config_sha256=fixture.solve_config_sha256,
        expected_source_fingerprint=HISTORICAL_SOURCE_FINGERPRINT,
        expected_scenario_hash=fixture.scenario_hash,
        expected_artifact=WindowIndexEntry(
            iteration=-1,
            relative_path=path.name,
            bytes=EXPECTED_OUTER_BYTES,
            sha256=EXPECTED_OUTER_SHA256,
        ),
    )


def _validated_source() -> tuple[Mapping[str, object], list[Mapping[str, object]]]:
    metadata = _load_json(S2_METADATA_PATH)
    artifacts = _mapping(metadata.get("artifacts"), "S2 metadata artifacts")
    checkpoint_metadata = _mapping(
        artifacts.get("trajectory/checkpoint.json"), "S2 checkpoint metadata"
    )
    if checkpoint_metadata.get("sha256") != EXPECTED_FINAL_CHECKPOINT_SHA256:
        raise ValueError("tracked S2 metadata checkpoint hash mismatch")
    if sha256_path(S2_CHECKPOINT_PATH) != EXPECTED_FINAL_CHECKPOINT_SHA256:
        raise ValueError("S2 final checkpoint hash mismatch")
    if sha256_path(S2_OUTER_PATH) != EXPECTED_OUTER_SHA256:
        raise ValueError("S2 outer-plan hash mismatch")
    if S2_OUTER_PATH.read_bytes() != TRACKED_OUTER_PATH.read_bytes():
        raise ValueError("tracked and S2 outer plans are not byte-identical")
    if s2_source_fingerprint() != HISTORICAL_SOURCE_FINGERPRINT:
        raise ValueError("frozen S2 source fingerprint mismatch")

    fixture = load_s2_fixture()
    policy = frozen_p0_policy()
    outer = _verify_outer(TRACKED_OUTER_PATH)
    checkpoint = load_verified_checkpoint(
        S2_CHECKPOINT_PATH,
        expected_source_fingerprint=HISTORICAL_SOURCE_FINGERPRINT,
        expected_scenario_hash=fixture.scenario_hash,
        expected_outer_plan_sha256=EXPECTED_OUTER_SHA256,
        expected_policy_hash=policy_sha256(policy),
        expected_soc_tolerance_mwh=policy.tolerances.soc_recurrence_mwh_abs,
        expected_residual_tolerances=residual_tolerances(policy),
        expected_inner_terminal_policy=policy.inner_terminal_policy,
        expected_horizon_steps=S2_HORIZON_STEPS,
        expected_ac_window_steps=policy.ac_window_steps,
        expected_result_dimensions=result_dimensions(fixture.inputs),
        expected_delta_hours=fixture.inputs.delta,
        expected_outer_boundary_soc_mwh=outer_boundaries(outer),
    )
    entries = _sequence(checkpoint.get("windows"), "checkpoint windows")
    windows = [
        _load_gzip_json(
            S2_TRAJECTORY_DIR
            / str(_mapping(entry, "window entry").get("relative_path"))
        )
        for entry in entries[:PREFIX_INTERVALS]
    ]
    return checkpoint, windows


def _tier_a_record(
    window: Mapping[str, object], entry: Mapping[str, object]
) -> dict[str, object]:
    attempt = _controlling_attempt(window)
    executed = _mapping(window.get("executed_interval"), "executed interval")
    return {
        "iteration": window.get("iteration"),
        "source_window": dict(entry),
        "storage_device_ids": window.get("storage_device_ids"),
        "executed_b_mw": executed.get("b_mw"),
        "realized_soc_mwh": window.get("post_step_soc_mwh"),
        "controlling_attempt_id": attempt.get("attempt_id"),
        "controlling_ordinal": attempt.get("ordinal"),
        "transformation": attempt.get("transformation"),
        "source_kind": attempt.get("source_kind"),
        "source_attempt_id": attempt.get("source_attempt_id"),
    }


def _tier_b_record(window: Mapping[str, object]) -> dict[str, object]:
    attempt = _controlling_attempt(window)
    names = (
        "assigned_start",
        "solver_x0",
        "solver_x0_layout",
        "solver_evidence",
        "causal_source",
    )
    evidence = {name: attempt.get(name) for name in names}
    return {
        "boundary": window.get("iteration"),
        "attempt_id": attempt.get("attempt_id"),
        "storage_device_ids": window.get("storage_device_ids"),
        "initial_soc_mwh": window.get("initial_soc_mwh"),
        "evidence": evidence,
        "evidence_sha256": {
            name: _value_sha256(value) for name, value in evidence.items()
        },
        "structural_signature_sha256": _value_sha256(
            attempt.get("structural_signature")
        ),
    }


def build_reference() -> dict[str, object]:
    """Validate ignored S2 archives and return their tiered first-64 extract."""
    checkpoint, windows = _validated_source()
    entries = _sequence(checkpoint.get("windows"), "checkpoint windows")
    if len(windows) != PREFIX_INTERVALS:
        raise ValueError("S2 checkpoint lacks the required first-64 prefix")
    fixture = load_s2_fixture()
    tier_a = [
        _tier_a_record(window, _mapping(entry, "window entry"))
        for window, entry in zip(windows, entries[:PREFIX_INTERVALS], strict=True)
    ]
    structural_signature = _controlling_attempt(windows[0]).get("structural_signature")
    structural_sha256 = _value_sha256(structural_signature)
    if any(
        _value_sha256(
            _controlling_attempt(windows[boundary]).get("structural_signature")
        )
        != structural_sha256
        for boundary in ACTUAL_START_BOUNDARIES
    ):
        raise ValueError("retained structural signatures are not invariant")
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "historical_source_fingerprint": HISTORICAL_SOURCE_FINGERPRINT,
            "scenario_sha256": fixture.scenario_hash,
            "policy_sha256": fixture.policy_sha256,
            "solve_config_sha256": fixture.solve_config_sha256,
            "outer_plan_sha256": EXPECTED_OUTER_SHA256,
            "final_checkpoint_sha256": EXPECTED_FINAL_CHECKPOINT_SHA256,
            "prefix_intervals": PREFIX_INTERVALS,
        },
        "storage_device_ids": checkpoint.get("storage_device_ids"),
        "initial_soc_mwh": checkpoint.get("initial_soc_mwh"),
        "invariants": {
            "structural_signature": structural_signature,
            "structural_signature_sha256": structural_sha256,
        },
        "tier_a": tier_a,
        "tier_b": [
            _tier_b_record(windows[boundary]) for boundary in ACTUAL_START_BOUNDARIES
        ],
    }


def _verify_reference_schema(reference: Mapping[str, object]) -> None:
    if reference.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported S2 reference schema")
    source = _mapping(reference.get("source"), "reference source")
    expected_source = {
        "historical_source_fingerprint": HISTORICAL_SOURCE_FINGERPRINT,
        "outer_plan_sha256": EXPECTED_OUTER_SHA256,
        "final_checkpoint_sha256": EXPECTED_FINAL_CHECKPOINT_SHA256,
        "prefix_intervals": PREFIX_INTERVALS,
    }
    for name, expected in expected_source.items():
        if source.get(name) != expected:
            raise ValueError(f"S2 reference {name} mismatch")
    tier_a = _sequence(reference.get("tier_a"), "Tier A")
    if len(tier_a) != PREFIX_INTERVALS:
        raise ValueError("S2 reference Tier A length mismatch")
    for iteration, item in enumerate(tier_a):
        record = _mapping(item, "Tier A record")
        entry = _mapping(record.get("source_window"), "source window")
        digest = entry.get("sha256")
        if record.get("iteration") != iteration or entry.get("iteration") != iteration:
            raise ValueError("S2 reference Tier A is not contiguous")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("S2 reference source-window hash is malformed")
    invariants = _mapping(reference.get("invariants"), "reference invariants")
    structural_signature = invariants.get("structural_signature")
    structural_sha256 = invariants.get("structural_signature_sha256")
    if structural_sha256 != _value_sha256(structural_signature):
        raise ValueError("S2 reference structural-signature hash mismatch")
    tier_b = _sequence(reference.get("tier_b"), "Tier B")
    boundaries = tuple(
        _mapping(item, "Tier B record").get("boundary") for item in tier_b
    )
    if boundaries != ACTUAL_START_BOUNDARIES:
        raise ValueError("S2 reference Tier B boundaries mismatch")
    for item in tier_b:
        record = _mapping(item, "Tier B record")
        if record.get("structural_signature_sha256") != structural_sha256:
            raise ValueError("Tier B structural-signature reference mismatch")
        evidence = _mapping(record.get("evidence"), "Tier B evidence")
        hashes = _mapping(record.get("evidence_sha256"), "evidence hashes")
        if set(evidence) != set(hashes):
            raise ValueError("S2 reference evidence hash registry mismatch")
        for name, value in evidence.items():
            if hashes.get(name) != _value_sha256(value):
                raise ValueError("S2 reference evidence hash mismatch")


def verify_tracked_reference(*, regenerate: bool = False) -> Mapping[str, object]:
    """Verify tracked identity, optionally regenerating from ignored archives."""
    _verify_outer(TRACKED_OUTER_PATH)
    reference_bytes = REFERENCE_PATH.read_bytes()
    digest = hashlib.sha256(reference_bytes).hexdigest()
    if digest != EXPECTED_REFERENCE_SHA256:
        raise ValueError("S2 reference frozen SHA-256 mismatch")
    if SIDECAR_PATH.read_text() != f"{digest}  {REFERENCE_PATH.name}\n":
        raise ValueError("S2 reference sidecar mismatch")
    reference = _mapping(json.loads(reference_bytes), "S2 reference")
    _verify_reference_schema(reference)
    if regenerate and canonical_bytes(build_reference()) != reference_bytes:
        raise ValueError("regenerated S2 reference differs from tracked bytes")
    return reference


def generate_tracked_reference() -> None:
    """Write and fully re-verify the deterministic extract and sidecar."""
    reference_bytes = canonical_bytes(build_reference())
    REFERENCE_PATH.write_bytes(reference_bytes)
    digest = hashlib.sha256(reference_bytes).hexdigest()
    SIDECAR_PATH.write_text(f"{digest}  {REFERENCE_PATH.name}\n")
    verify_tracked_reference(regenerate=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--regenerate", action="store_true")
    args = parser.parse_args()
    if args.generate:
        generate_tracked_reference()
    else:
        verify_tracked_reference(regenerate=args.regenerate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
