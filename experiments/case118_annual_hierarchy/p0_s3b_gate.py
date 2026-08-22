"""Retrospective normalization gate for the authoritative M17 S3b recovery."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence, cast


ROOT = Path(__file__).resolve().parents[1] / "hierarchical_battery_resilience"
METADATA_PATH = ROOT / "S3B_RESULTS_METADATA.json"
ARTIFACT_PATH = ROOT / "results/s3b_causal_recovery/causal_recovery.json.gz"
FIXTURE_PATH = Path(__file__).with_name("S3B_COPIED_RECOVERY_NORMALIZED.json")
RECOVERY_ITERATION = 80
EXPECTED_NORMALIZED_FIXTURE_SHA256 = (
    "3c46f53027a48c34902c1223b261e41d98a5d4cdc03d302908307be03fd4a8cf"
)


@dataclass(frozen=True)
class S3BNormalizationReport:
    """Outcome of the tracked and optional authoritative normalization gates."""

    source_artifact_available: bool
    source_integrity_verified: bool
    derived_fixture_verified: bool
    tracked_fixture_digest_verified: bool
    recovery_iteration: int
    slot_count: int
    executed_ordinals: tuple[int, ...]
    controlling_ordinal: int
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _public_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return "ac-" + text.removeprefix("s3b-")


def _normalized_layout(value: object) -> object:
    if value is None:
        return None
    records = cast(Sequence[Mapping[str, object]], value)
    auxiliary = 0
    normalized: list[dict[str, object]] = []
    for raw in records:
        record = {
            key: item for key, item in raw.items() if key != "variable_id"
        }
        if not bool(record["is_original_variable"]):
            record["name"] = f"auxiliary_{auxiliary}"
            auxiliary += 1
        normalized.append(record)
    return normalized


def _result_schema(value: object) -> object:
    if value is None:
        return None
    result = cast(Mapping[str, object], value)
    schema: dict[str, object] = {}
    for name, item in sorted(result.items()):
        if item is None:
            schema[name] = None
        elif isinstance(item, list):
            shape: list[int] = []
            cursor: object = item
            while isinstance(cursor, list):
                shape.append(len(cursor))
                cursor = cursor[0] if cursor else None
            schema[name] = shape
        else:
            schema[name] = "scalar"
    return schema


def normalize_recovery_window(artifact: Mapping[str, object]) -> Mapping[str, object]:
    """Project the legacy S3b window into public/streaming attempt semantics."""
    attempts = [
        cast(Mapping[str, object], item)
        for item in cast(Sequence[object], artifact["attempts"])
        if cast(Mapping[str, object], item)["iteration"] == RECOVERY_ITERATION
    ]
    attempts.sort(
        key=lambda item: int(
            cast(int, cast(Mapping[str, object], item["slot"])["ordinal"])
        )
    )
    normalized: list[dict[str, object]] = []
    for attempt in attempts:
        slot = cast(Mapping[str, object], attempt["slot"])
        audit = cast(Mapping[str, object] | None, attempt["audit"])
        executed = attempt["slot_state"] == "executed"
        source_id = _public_id(attempt["source_attempt_id"]) if executed else None
        normalized.append(
            {
                "attempt_id": _public_id(attempt["attempt_id"]),
                "iteration": attempt["iteration"],
                "ordinal": slot["ordinal"],
                "role": slot["role"],
                "transformation": slot["transformation"],
                "scale": slot["scale"],
                "seed": slot["seed"],
                "slot_state": attempt["slot_state"],
                "source_kind": "attempt" if source_id is not None else None,
                "source_attempt_id": source_id,
                "interval_start": attempt["interval_start"],
                "interval_stop": attempt["interval_stop"],
                "initial_soc_mwh": attempt["initial_soc_mwh"],
                "target_soc_mwh": attempt["target_soc_mwh"],
                "solver_executed": attempt["solver_executed"],
                "x0_verified": attempt["x0_verified"],
                "model_x0_count": attempt["model_x0_count"],
                "auxiliary_x0_count": attempt["auxiliary_x0_count"],
                "solver_x0_sha256": _digest(attempt["solver_x0"]),
                "layout_sha256": _digest(
                    _normalized_layout(attempt["solver_x0_layout"])
                ),
                "raw_start_sha256": _digest(attempt["raw_starting_values"]),
                "assigned_start_sha256": _digest(attempt["starting_values"]),
                "object_identity_preserved": attempt["object_identity_preserved"],
                "audit": (
                    None
                    if audit is None
                    else {
                        key: audit[key]
                        for key in (
                            "status",
                            "outcome",
                            "accepted_primal",
                            "missing_or_nonfinite_fields",
                            "identity_error",
                            "residuals",
                            "exception",
                            "solver_num_iters",
                            "solver_setup_time_seconds",
                            "solver_solve_time_seconds",
                        )
                    }
                ),
                "result_schema": _result_schema(attempt["results"]),
                "results_sha256": _digest(attempt["results"]),
                "terminal_deviation_mwh": attempt["terminal_deviation_mwh"],
                "supplied_executed_action": attempt["supplied_executed_action"],
                "reason_class": (
                    "earlier_controller_accepted"
                    if attempt["slot_state"] == "not_needed_after_acceptance"
                    else attempt["reason"]
                ),
            }
        )
    return {
        "schema_version": 1,
        "source_artifact": {
            "bytes": 3_312_008,
            "sha256": "a6f8ce5c5e01325a5ef376df6855687ee342f1504c28cd614d76a7dcbead1beb",
        },
        "recovery_iteration": RECOVERY_ITERATION,
        "attempts": normalized,
    }


def _validate_semantics(fixture: Mapping[str, object]) -> list[str]:
    failures: list[str] = []
    attempts = cast(Sequence[Mapping[str, object]], fixture["attempts"])
    if len(attempts) != 9:
        failures.append("slot_count")
        return failures
    if tuple(item["ordinal"] for item in attempts) != tuple(range(9)):
        failures.append("ordinals")
    expected_roles = (
        "primary_controlling",
        "target_free",
        "copied_target_free",
        "perturbed_target_free",
        "perturbed_target_free",
        "perturbed_target_free",
        "perturbed_causal",
        "perturbed_causal",
        "perturbed_causal",
    )
    if tuple(item["role"] for item in attempts) != expected_roles:
        failures.append("roles")
    if tuple(item["slot_state"] for item in attempts) != (
        "executed",
        "executed",
        "executed",
        *("not_needed_after_acceptance",) * 6,
    ):
        failures.append("slot_states")
    if tuple(
        item["audit"]["outcome"]  # type: ignore[index]
        for item in attempts[:3]
    ) != ("unusable_primal", "accepted", "accepted"):
        failures.append("outcomes")
    if attempts[0]["source_attempt_id"] != "ac-079-00-primary_controlling":
        failures.append("primary_source")
    if attempts[1]["source_attempt_id"] != "ac-079-00-primary_controlling":
        failures.append("target_free_source")
    if attempts[2]["source_attempt_id"] != "ac-080-01-target_free":
        failures.append("copied_source")
    if [item["supplied_executed_action"] for item in attempts] != [
        False,
        False,
        True,
        *([False] * 6),
    ]:
        failures.append("action_selection")
    for item in attempts[:3]:
        if (
            item["solver_executed"] is not True
            or item["x0_verified"] is not True
            or item["model_x0_count"] != 745
            or item["auxiliary_x0_count"] != 185
            or item["object_identity_preserved"] is not True
        ):
            failures.append(f"solver_evidence_{item['ordinal']}")
    return failures


def validate_normalized_fixture(fixture: Mapping[str, object]) -> tuple[str, ...]:
    """Bind every tracked normalized field and validate its public semantics."""
    failures = _validate_semantics(fixture)
    if _digest(fixture) != EXPECTED_NORMALIZED_FIXTURE_SHA256:
        failures.append("tracked_fixture_digest")
    return tuple(failures)


def run_s3b_normalization_gate() -> S3BNormalizationReport:
    """Verify tracked derived evidence and, when present, its source artifact."""
    metadata = cast(Mapping[str, object], json.loads(METADATA_PATH.read_text()))
    fixture = cast(Mapping[str, object], json.loads(FIXTURE_PATH.read_text()))
    failures = list(validate_normalized_fixture(fixture))
    specification = cast(Mapping[str, object], metadata["artifacts"])
    source_spec = cast(Mapping[str, object], specification["causal_recovery.json.gz"])
    if fixture["source_artifact"] != source_spec:
        failures.append("tracked_source_provenance")
    source_available = ARTIFACT_PATH.is_file()
    source_verified = False
    if source_available:
        if (
            ARTIFACT_PATH.stat().st_size != source_spec["bytes"]
            or _sha256(ARTIFACT_PATH) != source_spec["sha256"]
        ):
            failures.append("source_integrity")
        else:
            source_verified = True
            with gzip.open(ARTIFACT_PATH, "rt", encoding="utf-8") as stream:
                artifact = cast(Mapping[str, object], json.load(stream))
            if normalize_recovery_window(artifact) != fixture:
                failures.append("derived_fixture_drift")
    attempts = cast(Sequence[Mapping[str, object]], fixture["attempts"])
    executed = tuple(
        int(cast(int, item["ordinal"]))
        for item in attempts
        if item["solver_executed"] is True
    )
    controlling = next(
        int(cast(int, item["ordinal"]))
        for item in attempts
        if item["supplied_executed_action"] is True
    )
    return S3BNormalizationReport(
        source_artifact_available=source_available,
        source_integrity_verified=source_verified,
        derived_fixture_verified=(
            source_verified and "derived_fixture_drift" not in failures
        ),
        tracked_fixture_digest_verified="tracked_fixture_digest" not in failures,
        recovery_iteration=int(cast(int, fixture["recovery_iteration"])),
        slot_count=len(attempts),
        executed_ordinals=executed,
        controlling_ordinal=controlling,
        failures=tuple(failures),
    )


__all__ = [
    "ARTIFACT_PATH",
    "EXPECTED_NORMALIZED_FIXTURE_SHA256",
    "FIXTURE_PATH",
    "S3BNormalizationReport",
    "normalize_recovery_window",
    "run_s3b_normalization_gate",
    "validate_normalized_fixture",
]
