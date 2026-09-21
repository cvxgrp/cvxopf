"""Derive and verify the immutable Case118 S4b annual shard manifest."""

from __future__ import annotations

import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping, Sequence, cast

import numpy as np

from experiments.case118_annual_hierarchy.p0_fixture import (
    P0_EXPECTED_POLICY_SHA256,
    P0_EXPECTED_SOLVE_CONFIG_SHA256,
)
from experiments.case118_annual_hierarchy.s4_fixture import (
    S4_CANONICALIZATION_BACKEND,
    S4_EXPECTED_HASHES,
    S4_OUTPUT_DIRECTORY,
    S4_TEMPORAL_ASSEMBLY,
    load_s4_fixture,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    ATTEMPT_ROLES,
    PERTURBATION_SCALES,
    atomic_immutable_json,
    sha256_path,
)


ROOT = Path(__file__).parents[2]
S4B_PROTOCOL_PATH = Path(__file__).with_name("S4B_PROTOCOL.md")
TIMEOUT_POLICY_PATH = Path(__file__).with_name("FIVE_MINUTE_TIMEOUT_POLICY.md")
S4_RESULTS_PATH = Path(__file__).with_name("S4_RESULTS.json")
S4_OUTER_ARCHIVE_PATH = ROOT / S4_OUTPUT_DIRECTORY / "outer-plan.json.gz"
S4B_MANIFEST_PATH = Path(__file__).with_name("S4B_SHARD_MANIFEST.json")

S4_RESULTS_SHA256 = "f8194ef39d18084f90d0d6216bd1a7ee85a889bf3699571fd9f7f2b3c3dc4947"
S4_OUTER_ARCHIVE_SHA256 = (
    "6e7d88e8eed39de4a0141b0fe3c8a146fd2ae298a3d3ddc2768ae57247e87031"
)
S4_SIGNPOST_SHA256 = "afc1bc32d4ea453e9ee0bf32c99003cd4952e84746f72ca1e574721a40e15e5a"
S4B_PROTOCOL_SHA256 = "e7180a99f44827813f85abcef6b65ce9090ea7e8961e776591493791ac01c733"
# The manifest retains the exact derivation-time file identity above. Live
# revalidation separately binds the scientific body so status-only updates do
# not invalidate the immutable derived artifact.
S4B_PROTOCOL_BODY_SHA256 = (
    "b6d53f375c612ea3bfd0d848b7d5587810f79b98eac5ce2f3d3dd7f8f91ef1e6"
)
TIMEOUT_POLICY_SHA256 = (
    "1cb5c6c469cda85fe8c8bcb0fd4c872aa6b5ce4454d782b9f63575b537d8eda2"
)
DERIVATION_PARENT_COMMIT = "70870fd4cc078d88a7932a29a6df9e9e7911b412"

HORIZON_STEPS = 8_760
NOMINAL_SHARD_STEPS = 730
MINIMUM_ORDINARY_SHARD_STEPS = 672
MAXIMUM_ORDINARY_SHARD_STEPS = 792
PARTICIPATION_RADIUS_STEPS = 3
PARTICIPATION_FLOOR_MW = 1e-6
PARTICIPATION_RATING_FRACTION = 0.001
MINIMUM_NORMALIZED_CHARGING = 0.001
MIDPOINT_SOC_FRACTION = 0.5
PRIMARY_ATTEMPT_BUDGET_SECONDS = 300.0
PER_WORKER_RSS_LIMIT_MIB = 16_384.0
TWO_WORKER_AGGREGATE_RSS_LIMIT_MIB = 24_576.0

STORAGE_DEVICE_IDS = (
    "storage_bus_41",
    "storage_bus_65",
    "storage_bus_89",
    "storage_bus_105",
)
EXPECTED_BOUNDARIES = (
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
)
EXPECTED_SHARD_LENGTHS = (682, 770, 761, 752, 758, 745, 743, 745, 770, 749, 712, 573)
S4_EXECUTION_COMMIT = "ab2375cddcb4823a47610123ae2b0d8cd8c8f33d"
S4_SOURCE_FINGERPRINT = (
    "e1019b73fb666fb6553ab9c6cf2249b6e25c2a4a15c40ca2e0ed41057e4c0c26"
)
S4_OUTER_ARCHIVE_BYTES = 34_406_540

# Filled after deterministic publication; this separately binds the tracked
# artifact against an internally self-consistent accidental rewrite.
EXPECTED_MANIFEST_SHA256 = (
    "6d37eff9a3922cf17303d30f66cf4d23417f6ef2cc9dfc6cbba2365d5ec8633a"
)


def canonical_json(value: object) -> bytes:
    """Return the exact canonical binary64 JSON encoding frozen by S4b."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def object_sha256(value: object) -> str:
    return sha256(canonical_json(value)).hexdigest()


def protocol_body_sha256(path: Path = S4B_PROTOCOL_PATH) -> str:
    """Hash the normative S4b contract independently of its status preamble."""
    text = path.read_text()
    marker = "## Frozen annual boundary rule"
    if marker not in text:
        raise ValueError("S4b protocol lacks its frozen scientific body")
    return sha256(text[text.index(marker) :].encode()).hexdigest()


def rule_payload() -> dict[str, object]:
    """Return the one canonical machine-readable S4b boundary rule."""
    return {
        "schema_version": 1,
        "horizon_steps": HORIZON_STEPS,
        "shard_lengths": {
            "nominal_steps": NOMINAL_SHARD_STEPS,
            "minimum_ordinary_steps": MINIMUM_ORDINARY_SHARD_STEPS,
            "maximum_ordinary_steps": MAXIMUM_ORDINARY_SHARD_STEPS,
        },
        "candidate_range": {
            "lower_offset_steps": MINIMUM_ORDINARY_SHARD_STEPS,
            "upper_offset_steps": MAXIMUM_ORDINARY_SHARD_STEPS,
            "upper_horizon_offset_steps": -1,
            "inclusive": True,
        },
        "participation": {
            "neighborhood_start_offset_steps": -PARTICIPATION_RADIUS_STEPS,
            "neighborhood_stop_offset_steps": PARTICIPATION_RADIUS_STEPS,
            "neighborhood_stop_exclusive": True,
            "absolute_power_floor_mw": PARTICIPATION_FLOOR_MW,
            "power_rating_fraction": PARTICIPATION_RATING_FRACTION,
            "comparison": "greater_than_or_equal",
        },
        "charging": {
            "source_interval_offset_steps": -1,
            "positive_storage_power_means": "discharging",
            "statistic": "sum(max(-b_mw/power_rating_mw,0))",
            "minimum_normalized_statistic": MINIMUM_NORMALIZED_CHARGING,
        },
        "eligibility": {
            "requires_participating_device": True,
            "requires_minimum_charging": True,
            "no_eligible_candidate": "fail_manifest_derivation",
        },
        "midpoint_score": {
            "soc_fraction": MIDPOINT_SOC_FRACTION,
            "statistic": "max(abs(soc_mwh/capacity_mwh-0.5))",
            "participating_devices_only": True,
        },
        "selection_order": [
            "minimum_midpoint_deviation",
            "maximum_normalized_charging",
            "minimum_nominal_boundary_distance",
            "earliest_global_boundary",
        ],
        "final_truncation": {
            "append_horizon_when_remaining_at_most_steps": (
                MAXIMUM_ORDINARY_SHARD_STEPS
            ),
            "minimum_length_not_required": True,
        },
        "authoritative_sources": {
            "s4_results_sha256": S4_RESULTS_SHA256,
            "outer_archive_sha256": S4_OUTER_ARCHIVE_SHA256,
            "outer_signpost_sha256": S4_SIGNPOST_SHA256,
        },
        "storage_device_ids": list(STORAGE_DEVICE_IDS),
    }


def _signpost_sha256(
    storage_ids: Sequence[str], indices: np.ndarray, boundary_soc_mwh: np.ndarray
) -> str:
    digest = sha256()
    digest.update(json.dumps(list(storage_ids), separators=(",", ":")).encode())
    for values in (indices, boundary_soc_mwh):
        array = np.ascontiguousarray(np.asarray(values), dtype="<f8")
        digest.update(f"shape={array.shape}|".encode())
        digest.update(array.tobytes(order="C"))
        digest.update(b"\0")
    return digest.hexdigest()


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return cast(Mapping[str, object], value)


def _finite_matrix(value: object, shape: tuple[int, int], label: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != shape or not np.all(np.isfinite(array)):
        raise ValueError(f"{label} shape or finiteness mismatch")
    return array


def _finite_device_vector(value: object, label: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != (len(STORAGE_DEVICE_IDS),) or not np.all(np.isfinite(array)):
        raise ValueError(f"{label} must be one finite value per storage device")
    return array


def load_authoritative_outer(
    archive_path: Path = S4_OUTER_ARCHIVE_PATH,
) -> tuple[Mapping[str, object], np.ndarray, np.ndarray]:
    """Verify the accepted S4 evidence and load its exact storage trajectory."""
    if sha256_path(S4_RESULTS_PATH) != S4_RESULTS_SHA256:
        raise ValueError("tracked S4 result hash mismatch")
    if protocol_body_sha256() != S4B_PROTOCOL_BODY_SHA256:
        raise ValueError("S4b scientific protocol body hash mismatch")
    if sha256_path(TIMEOUT_POLICY_PATH) != TIMEOUT_POLICY_SHA256:
        raise ValueError("five-minute timeout policy hash mismatch")
    compact = _mapping(json.loads(S4_RESULTS_PATH.read_text()), "S4 result")
    artifacts = _mapping(compact.get("artifacts"), "S4 artifact registry")
    outer_entry = _mapping(artifacts.get("outer-plan.json.gz"), "S4 outer entry")
    if (
        compact.get("accepted_for_s4b") is not True
        or compact.get("execution_complete") is not True
        or compact.get("classification") != "accepted"
        or compact.get("horizon_steps") != HORIZON_STEPS
        or outer_entry.get("sha256") != S4_OUTER_ARCHIVE_SHA256
    ):
        raise ValueError("tracked S4 result does not authorize manifest derivation")
    if (
        not archive_path.is_file()
        or sha256_path(archive_path) != S4_OUTER_ARCHIVE_SHA256
    ):
        raise ValueError("authoritative S4 outer archive hash mismatch")
    if archive_path.stat().st_size != outer_entry.get("bytes"):
        raise ValueError("authoritative S4 outer archive byte count mismatch")
    with gzip.open(archive_path, "rt", encoding="utf-8") as stream:
        outer = _mapping(json.load(stream), "S4 outer archive")
    result = _mapping(outer.get("result"), "S4 outer result")
    audit = _mapping(outer.get("audit"), "S4 outer audit")
    ids = tuple(cast(Sequence[str], outer.get("storage_device_ids")))
    if (
        outer.get("schema_version") != 1
        or outer.get("outer_plan_id") != "outer-000"
        or outer.get("horizon_steps") != HORIZON_STEPS
        or outer.get("delta_hours") != 1.0
        or ids != STORAGE_DEVICE_IDS
        or outer.get("input_fingerprint") != S4_EXPECTED_HASHES["input_fingerprint"]
        or outer.get("scenario_hash") != S4_EXPECTED_HASHES["scenario"]
        or outer.get("policy_sha256") != P0_EXPECTED_POLICY_SHA256
        or outer.get("solve_config_sha256") != P0_EXPECTED_SOLVE_CONFIG_SHA256
        or outer.get("temporal_assembly") != S4_TEMPORAL_ASSEMBLY
        or outer.get("canonicalization_backend") != S4_CANONICALIZATION_BACKEND
        or outer.get("signpost_sha256") != S4_SIGNPOST_SHA256
        or audit.get("accepted_primal") is not True
        or audit.get("status") not in {"optimal", "optimal_inaccurate"}
        or audit.get("exception") is not None
        or audit.get("identity_error") is not None
        or audit.get("missing_or_nonfinite_fields") != []
        or result.get("status") not in {"optimal", "optimal_inaccurate"}
        or tuple(cast(Sequence[str], result.get("storage_device_ids"))) != ids
    ):
        raise ValueError("authoritative S4 outer archive contract mismatch")
    indices = np.asarray(outer.get("global_boundary_indices"), dtype=int)
    boundary = _finite_matrix(
        outer.get("boundary_soc_mwh"), (HORIZON_STEPS + 1, len(ids)), "outer SoC"
    )
    storage_power = _finite_matrix(
        result.get("b"), (HORIZON_STEPS, len(ids)), "outer storage power"
    )
    result_soc = _finite_matrix(
        result.get("soc"), (HORIZON_STEPS, len(ids)), "outer result SoC"
    )
    if not np.array_equal(indices, np.arange(HORIZON_STEPS + 1)):
        raise ValueError("outer boundary index registry mismatch")
    if not np.array_equal(boundary[1:], result_soc):
        raise ValueError("outer result and boundary SoC mismatch")
    if _signpost_sha256(ids, indices, boundary) != S4_SIGNPOST_SHA256:
        raise ValueError("independently reconstructed signpost hash mismatch")
    return outer, boundary, storage_power


def _candidate_evidence(
    *,
    previous: int,
    boundary: int,
    boundary_soc_mwh: np.ndarray,
    storage_power_mw: np.ndarray,
    capacities_mwh: np.ndarray,
    power_ratings_mw: np.ndarray,
) -> dict[str, object]:
    neighborhood_start = max(0, boundary - PARTICIPATION_RADIUS_STEPS)
    neighborhood_stop = min(HORIZON_STEPS, boundary + PARTICIPATION_RADIUS_STEPS)
    peak = np.max(
        np.abs(storage_power_mw[neighborhood_start:neighborhood_stop]), axis=0
    )
    thresholds = np.maximum(
        PARTICIPATION_FLOOR_MW,
        PARTICIPATION_RATING_FRACTION * power_ratings_mw,
    )
    participating = peak >= thresholds
    preceding_power = storage_power_mw[boundary - 1]
    charging = float(np.sum(np.maximum(-preceding_power / power_ratings_mw, 0.0)))
    eligible = bool(np.any(participating) and charging >= MINIMUM_NORMALIZED_CHARGING)
    deviation = (
        float(
            np.max(
                np.abs(
                    boundary_soc_mwh[boundary, participating]
                    / capacities_mwh[participating]
                    - MIDPOINT_SOC_FRACTION
                )
            )
        )
        if np.any(participating)
        else None
    )
    return {
        "global_boundary": boundary,
        "neighborhood": [neighborhood_start, neighborhood_stop],
        "boundary_soc_mwh": boundary_soc_mwh[boundary].tolist(),
        "preceding_storage_power_mw": preceding_power.tolist(),
        "local_peak_absolute_power_mw": peak.tolist(),
        "participating_devices": participating.tolist(),
        "normalized_charging": charging,
        "midpoint_deviation": deviation,
        "nominal_boundary_distance_steps": abs(
            boundary - (previous + NOMINAL_SHARD_STEPS)
        ),
        "eligible": eligible,
    }


def derive_boundary_rounds(
    boundary_soc_mwh: np.ndarray,
    storage_power_mw: np.ndarray,
    capacities_mwh: np.ndarray,
    power_ratings_mw: np.ndarray,
) -> tuple[list[int], list[dict[str, object]]]:
    """Apply the frozen lexicographic rule and retain every candidate input."""
    if boundary_soc_mwh.shape != (HORIZON_STEPS + 1, len(STORAGE_DEVICE_IDS)):
        raise ValueError("boundary SoC has the wrong shape")
    if storage_power_mw.shape != (HORIZON_STEPS, len(STORAGE_DEVICE_IDS)):
        raise ValueError("storage power has the wrong shape")
    for values, label in (
        (boundary_soc_mwh, "boundary SoC"),
        (storage_power_mw, "storage power"),
        (capacities_mwh, "storage capacities"),
        (power_ratings_mw, "storage power ratings"),
    ):
        if not np.all(np.isfinite(values)):
            raise ValueError(f"{label} must be finite")
    if np.any(capacities_mwh <= 0.0) or np.any(power_ratings_mw <= 0.0):
        raise ValueError("storage ratings must be positive")
    boundaries = [0]
    rounds: list[dict[str, object]] = []
    previous = 0
    while HORIZON_STEPS - previous > MAXIMUM_ORDINARY_SHARD_STEPS:
        candidate_start = previous + MINIMUM_ORDINARY_SHARD_STEPS
        candidate_stop = min(previous + MAXIMUM_ORDINARY_SHARD_STEPS, HORIZON_STEPS - 1)
        candidates = [
            _candidate_evidence(
                previous=previous,
                boundary=boundary,
                boundary_soc_mwh=boundary_soc_mwh,
                storage_power_mw=storage_power_mw,
                capacities_mwh=capacities_mwh,
                power_ratings_mw=power_ratings_mw,
            )
            for boundary in range(candidate_start, candidate_stop + 1)
        ]
        eligible = [candidate for candidate in candidates if candidate["eligible"]]
        if not eligible:
            raise ValueError(
                f"no eligible S4b shard boundary after global boundary {previous}"
            )
        selected = min(
            eligible,
            key=lambda item: (
                cast(float, item["midpoint_deviation"]),
                -cast(float, item["normalized_charging"]),
                cast(int, item["nominal_boundary_distance_steps"]),
                cast(int, item["global_boundary"]),
            ),
        )
        selected_boundary = cast(int, selected["global_boundary"])
        rounds.append(
            {
                "round": len(rounds),
                "previous_boundary": previous,
                "candidate_range": [candidate_start, candidate_stop],
                "selected_boundary": selected_boundary,
                "candidates": candidates,
            }
        )
        boundaries.append(selected_boundary)
        previous = selected_boundary
    boundaries.append(HORIZON_STEPS)
    return boundaries, rounds


def _boundary_state(index: int, values: np.ndarray) -> dict[str, object]:
    state = {
        "global_boundary": index,
        "storage_device_ids": list(STORAGE_DEVICE_IDS),
        "soc_mwh": values.tolist(),
    }
    return {**state, "boundary_sha256": object_sha256(state)}


def derive_manifest(
    archive_path: Path = S4_OUTER_ARCHIVE_PATH,
) -> dict[str, object]:
    """Derive the complete noncircular annual manifest envelope."""
    outer, boundary_soc, storage_power = load_authoritative_outer(archive_path)
    fixture = load_s4_fixture()
    ids = tuple(fixture.storage_device_ids)
    if ids != STORAGE_DEVICE_IDS:
        raise ValueError("fixture storage identities differ from the boundary rule")
    capacities = np.asarray(
        [unit.capacity for unit in fixture.inputs.storage], dtype=float
    )
    ratings = np.asarray(
        [unit.apparent_power_rating for unit in fixture.inputs.storage], dtype=float
    )
    boundaries, rounds = derive_boundary_rounds(
        boundary_soc, storage_power, capacities, ratings
    )
    lengths = [stop - start for start, stop in zip(boundaries, boundaries[1:])]
    if (
        tuple(boundaries) != EXPECTED_BOUNDARIES
        or tuple(lengths) != EXPECTED_SHARD_LENGTHS
    ):
        raise ValueError("derived S4b boundary characterization mismatch")
    rule = rule_payload()
    rule_hash = object_sha256(rule)
    source_fingerprint = str(outer["source_fingerprint"])
    compact = _mapping(json.loads(S4_RESULTS_PATH.read_text()), "S4 result")
    compact_context = _mapping(compact.get("execution_context"), "S4 context")
    global_provenance = {
        "derivation_parent_commit": DERIVATION_PARENT_COMMIT,
        "s4_execution_commit": compact_context["git_commit"],
        "s4_results_sha256": S4_RESULTS_SHA256,
        "outer_archive_sha256": S4_OUTER_ARCHIVE_SHA256,
        "outer_archive_bytes": archive_path.stat().st_size,
        "outer_signpost_sha256": S4_SIGNPOST_SHA256,
        "s4b_protocol_sha256": S4B_PROTOCOL_SHA256,
        "five_minute_timeout_policy_sha256": TIMEOUT_POLICY_SHA256,
        "source_fingerprint": source_fingerprint,
        "scenario_sha256": fixture.scenario_hash,
        "input_fingerprint": fixture.hashes["input_fingerprint"],
        "network_sha256": fixture.hashes["case"],
        "load_p_sha256": fixture.hashes["load_p"],
        "load_q_sha256": fixture.hashes["load_q"],
        "nondispatchable_sha256": fixture.hashes["nondispatchable"],
        "policy_sha256": fixture.policy_sha256,
        "solve_config_sha256": fixture.solve_config_sha256,
        "temporal_assembly": fixture.temporal_assembly,
        "canonicalization_backend": fixture.canonicalization_backend,
    }
    recovery = {
        "attempt_roles": list(ATTEMPT_ROLES),
        "attempt_ordinals": list(range(len(ATTEMPT_ROLES))),
        "perturbation_scales": list(PERTURBATION_SCALES),
        "seed_base": fixture.policy.recovery.seed_base,
        "seed_formula": "seed_base+100*global_iteration+10*source_code+scale_index",
        "source_codes": {"target_free": 1, "causal": 2},
        "scale_indices": [1, 2, 3],
        "global_iteration_coordinates": True,
    }
    identity = {
        **global_provenance,
        "rule_sha256": rule_hash,
        "storage_device_ids": list(ids),
    }
    boundary_states = [
        _boundary_state(index, boundary_soc[index]) for index in boundaries
    ]
    shards: list[dict[str, object]] = []
    for ordinal, (start, stop) in enumerate(zip(boundaries, boundaries[1:])):
        start_state = _boundary_state(start, boundary_soc[start])
        stop_state = _boundary_state(stop, boundary_soc[stop])
        output_directory = (
            f"experiments/case118_annual_hierarchy/results/s4b_annual_ac/"
            f"shard-{ordinal:03d}"
        )
        shards.append(
            {
                "shard_id": f"s4b-shard-{ordinal:03d}",
                "ordinal": ordinal,
                "interval": {"start": start, "stop": stop, "half_open": True},
                "identity": identity,
                "storage": {
                    "device_ids": list(ids),
                    "power_ratings_mw": ratings.tolist(),
                    "capacities_mwh": capacities.tolist(),
                    "initial_state": start_state,
                    "terminal_state": stop_state,
                },
                "controller": {
                    "ac_window_steps": 3,
                    "stride_steps": 1,
                    "terminal_policy": "hard_equality",
                    "initialization_policy": "shifted_with_recovery",
                    "recovery": recovery,
                    "primary_attempt_budget_seconds": PRIMARY_ATTEMPT_BUDGET_SECONDS,
                },
                "locations": {
                    "output_directory": output_directory,
                    "checkpoint": f"{output_directory}/checkpoint.json",
                },
                "resource_limits": {
                    "per_worker_current_rss_mib": PER_WORKER_RSS_LIMIT_MIB,
                    "two_worker_aggregate_current_rss_mib": (
                        TWO_WORKER_AGGREGATE_RSS_LIMIT_MIB
                    ),
                },
                "predecessor_boundary_sha256": (
                    None if ordinal == 0 else start_state["boundary_sha256"]
                ),
                "successor_boundary_sha256": (
                    None
                    if ordinal == len(boundaries) - 2
                    else stop_state["boundary_sha256"]
                ),
            }
        )
    payload = {
        "schema_version": 1,
        "classification": "derived_manifest_execution_not_authorized",
        "execution_authorized": False,
        "rule_payload": rule,
        "rule_sha256": rule_hash,
        "global_provenance": global_provenance,
        "storage_device_ids": list(ids),
        "storage_power_ratings_mw": ratings.tolist(),
        "storage_capacities_mwh": capacities.tolist(),
        "boundary_indices": boundaries,
        "boundary_states": boundary_states,
        "shard_lengths": lengths,
        "derivation_rounds": rounds,
        "shards": shards,
    }
    return {
        "schema_version": 1,
        "manifest_sha256": object_sha256(payload),
        "manifest": payload,
    }


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def validate_manifest(
    value: object, *, expected_envelope_sha256: str | None = None
) -> dict[str, object]:
    """Independently reconstruct all manifest identities and boundary choices."""
    envelope = dict(_mapping(value, "S4b manifest envelope"))
    if envelope.get("schema_version") != 1 or set(envelope) != {
        "schema_version",
        "manifest_sha256",
        "manifest",
    }:
        raise ValueError("S4b manifest envelope schema mismatch")
    if (
        expected_envelope_sha256 is not None
        and object_sha256(envelope) != expected_envelope_sha256
    ):
        raise ValueError("S4b tracked manifest file identity mismatch")
    manifest = _mapping(envelope.get("manifest"), "S4b manifest payload")
    if envelope.get("manifest_sha256") != object_sha256(manifest):
        raise ValueError("S4b manifest payload hash mismatch")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("classification") != "derived_manifest_execution_not_authorized"
        or manifest.get("execution_authorized") is not False
        or manifest.get("rule_payload") != rule_payload()
        or manifest.get("rule_sha256") != object_sha256(rule_payload())
        or tuple(cast(Sequence[str], manifest.get("storage_device_ids")))
        != STORAGE_DEVICE_IDS
    ):
        raise ValueError("S4b manifest frozen contract mismatch")
    provenance = _mapping(manifest.get("global_provenance"), "global provenance")
    expected_provenance = {
        "derivation_parent_commit": DERIVATION_PARENT_COMMIT,
        "s4_execution_commit": S4_EXECUTION_COMMIT,
        "s4_results_sha256": S4_RESULTS_SHA256,
        "outer_archive_sha256": S4_OUTER_ARCHIVE_SHA256,
        "outer_archive_bytes": S4_OUTER_ARCHIVE_BYTES,
        "outer_signpost_sha256": S4_SIGNPOST_SHA256,
        "s4b_protocol_sha256": S4B_PROTOCOL_SHA256,
        "five_minute_timeout_policy_sha256": TIMEOUT_POLICY_SHA256,
        "scenario_sha256": S4_EXPECTED_HASHES["scenario"],
        "input_fingerprint": S4_EXPECTED_HASHES["input_fingerprint"],
        "network_sha256": S4_EXPECTED_HASHES["case"],
        "load_p_sha256": S4_EXPECTED_HASHES["load_p"],
        "load_q_sha256": S4_EXPECTED_HASHES["load_q"],
        "nondispatchable_sha256": S4_EXPECTED_HASHES["nondispatchable"],
        "policy_sha256": P0_EXPECTED_POLICY_SHA256,
        "solve_config_sha256": P0_EXPECTED_SOLVE_CONFIG_SHA256,
        "temporal_assembly": S4_TEMPORAL_ASSEMBLY,
        "canonicalization_backend": S4_CANONICALIZATION_BACKEND,
        "source_fingerprint": S4_SOURCE_FINGERPRINT,
    }
    if provenance != expected_provenance:
        raise ValueError("S4b manifest global provenance mismatch")
    capacities = np.asarray(manifest.get("storage_capacities_mwh"), dtype=float)
    ratings = np.asarray(manifest.get("storage_power_ratings_mw"), dtype=float)
    if (
        capacities.shape != (4,)
        or ratings.shape != (4,)
        or np.any(capacities <= 0)
        or np.any(ratings <= 0)
    ):
        raise ValueError("S4b storage rating registry mismatch")
    boundaries = list(cast(Sequence[int], manifest.get("boundary_indices")))
    lengths = list(cast(Sequence[int], manifest.get("shard_lengths")))
    if (
        tuple(boundaries) != EXPECTED_BOUNDARIES
        or tuple(lengths) != EXPECTED_SHARD_LENGTHS
    ):
        raise ValueError("S4b manifest boundary characterization mismatch")
    boundary_states = cast(
        Sequence[Mapping[str, object]], manifest.get("boundary_states")
    )
    if len(boundary_states) != len(boundaries):
        raise ValueError("S4b boundary-state registry length mismatch")
    rounds = cast(Sequence[Mapping[str, object]], manifest.get("derivation_rounds"))
    if len(rounds) != len(boundaries) - 2:
        raise ValueError("S4b derivation round count mismatch")
    for ordinal, round_payload in enumerate(rounds):
        previous = boundaries[ordinal]
        start = previous + MINIMUM_ORDINARY_SHARD_STEPS
        stop = min(previous + MAXIMUM_ORDINARY_SHARD_STEPS, HORIZON_STEPS - 1)
        if (
            round_payload.get("round") != ordinal
            or round_payload.get("previous_boundary") != previous
            or round_payload.get("candidate_range") != [start, stop]
        ):
            raise ValueError("S4b derivation round identity mismatch")
        candidates = cast(
            Sequence[Mapping[str, object]], round_payload.get("candidates")
        )
        if [candidate.get("global_boundary") for candidate in candidates] != list(
            range(start, stop + 1)
        ):
            raise ValueError("S4b candidate registry is incomplete or unordered")
        eligible: list[Mapping[str, object]] = []
        for candidate in candidates:
            boundary = cast(int, candidate["global_boundary"])
            soc = _finite_device_vector(
                candidate.get("boundary_soc_mwh"), "candidate boundary SoC"
            )
            preceding = _finite_device_vector(
                candidate.get("preceding_storage_power_mw"),
                "candidate preceding storage power",
            )
            peak = _finite_device_vector(
                candidate.get("local_peak_absolute_power_mw"),
                "candidate local peak power",
            )
            if np.any(peak < 0.0):
                raise ValueError("candidate local peak power must be nonnegative")
            participation = peak >= np.maximum(
                PARTICIPATION_FLOOR_MW, PARTICIPATION_RATING_FRACTION * ratings
            )
            charging = float(np.sum(np.maximum(-preceding / ratings, 0.0)))
            deviation = (
                float(
                    np.max(np.abs(soc[participation] / capacities[participation] - 0.5))
                )
                if np.any(participation)
                else None
            )
            eligible_flag = bool(
                np.any(participation) and charging >= MINIMUM_NORMALIZED_CHARGING
            )
            if (
                candidate.get("neighborhood")
                != [max(0, boundary - 3), min(HORIZON_STEPS, boundary + 3)]
                or candidate.get("participating_devices") != participation.tolist()
                or _number(candidate.get("normalized_charging"), "candidate charging")
                != charging
                or candidate.get("midpoint_deviation") != deviation
                or candidate.get("nominal_boundary_distance_steps")
                != abs(boundary - (previous + 730))
                or candidate.get("eligible") is not eligible_flag
            ):
                raise ValueError("S4b candidate score does not reconstruct")
            if eligible_flag:
                eligible.append(candidate)
        if not eligible:
            raise ValueError("S4b derivation round has no eligible candidate")
        selected = min(
            eligible,
            key=lambda item: (
                cast(float, item["midpoint_deviation"]),
                -cast(float, item["normalized_charging"]),
                cast(int, item["nominal_boundary_distance_steps"]),
                cast(int, item["global_boundary"]),
            ),
        )
        if round_payload.get("selected_boundary") != selected["global_boundary"]:
            raise ValueError("S4b selected boundary does not reproduce")
        selected_state = boundary_states[ordinal + 1]
        if selected_state.get("soc_mwh") != selected.get("boundary_soc_mwh"):
            raise ValueError("S4b selected candidate and boundary state differ")
    shards = cast(Sequence[Mapping[str, object]], manifest.get("shards"))
    if len(shards) != len(lengths):
        raise ValueError("S4b shard registry length mismatch")
    previous_terminal: Mapping[str, object] | None = None
    expected_identity = {
        **expected_provenance,
        "rule_sha256": object_sha256(rule_payload()),
        "storage_device_ids": list(STORAGE_DEVICE_IDS),
    }
    expected_recovery = {
        "attempt_roles": list(ATTEMPT_ROLES),
        "attempt_ordinals": list(range(len(ATTEMPT_ROLES))),
        "perturbation_scales": list(PERTURBATION_SCALES),
        "seed_base": 17_000_000,
        "seed_formula": "seed_base+100*global_iteration+10*source_code+scale_index",
        "source_codes": {"target_free": 1, "causal": 2},
        "scale_indices": [1, 2, 3],
        "global_iteration_coordinates": True,
    }
    for ordinal, shard in enumerate(shards):
        start, stop = boundaries[ordinal : ordinal + 2]
        interval = _mapping(shard.get("interval"), "shard interval")
        storage = _mapping(shard.get("storage"), "shard storage")
        initial = _mapping(storage.get("initial_state"), "shard initial state")
        terminal = _mapping(storage.get("terminal_state"), "shard terminal state")
        identity = _mapping(shard.get("identity"), "shard identity")
        controller = _mapping(shard.get("controller"), "shard controller")
        locations = _mapping(shard.get("locations"), "shard locations")
        resources = _mapping(shard.get("resource_limits"), "shard resources")
        recovery = _mapping(controller.get("recovery"), "shard recovery")
        registered_start = boundary_states[ordinal]
        registered_stop = boundary_states[ordinal + 1]
        for state, index in ((initial, start), (terminal, stop)):
            base = {
                "global_boundary": index,
                "storage_device_ids": list(STORAGE_DEVICE_IDS),
                "soc_mwh": state.get("soc_mwh"),
            }
            if (
                state.get("global_boundary") != index
                or state.get("storage_device_ids") != list(STORAGE_DEVICE_IDS)
                or np.asarray(state.get("soc_mwh"), dtype=float).shape != (4,)
                or state.get("boundary_sha256") != object_sha256(base)
            ):
                raise ValueError("S4b shard boundary state identity mismatch")
        expected_output = f"experiments/case118_annual_hierarchy/results/s4b_annual_ac/shard-{ordinal:03d}"
        if (
            shard.get("shard_id") != f"s4b-shard-{ordinal:03d}"
            or shard.get("ordinal") != ordinal
            or interval != {"start": start, "stop": stop, "half_open": True}
            or storage.get("device_ids") != list(STORAGE_DEVICE_IDS)
            or storage.get("power_ratings_mw") != ratings.tolist()
            or storage.get("capacities_mwh") != capacities.tolist()
            or controller.get("ac_window_steps") != 3
            or controller.get("stride_steps") != 1
            or controller.get("terminal_policy") != "hard_equality"
            or controller.get("initialization_policy") != "shifted_with_recovery"
            or controller.get("primary_attempt_budget_seconds") != 300.0
            or recovery != expected_recovery
            or locations.get("output_directory") != expected_output
            or locations.get("checkpoint") != f"{expected_output}/checkpoint.json"
            or resources.get("per_worker_current_rss_mib") != 16_384.0
            or resources.get("two_worker_aggregate_current_rss_mib") != 24_576.0
        ):
            raise ValueError("S4b shard execution identity mismatch")
        if identity != expected_identity:
            raise ValueError("S4b shard identity payload mismatch")
        if initial != registered_start or terminal != registered_stop:
            raise ValueError("S4b shard state differs from boundary registry")
        if previous_terminal is not None and initial != previous_terminal:
            raise ValueError("S4b adjacent boundary states are not byte-identical")
        if shard.get("predecessor_boundary_sha256") != (
            None if ordinal == 0 else initial["boundary_sha256"]
        ) or shard.get("successor_boundary_sha256") != (
            None if ordinal == len(shards) - 1 else terminal["boundary_sha256"]
        ):
            raise ValueError("S4b predecessor/successor identity mismatch")
        previous_terminal = terminal
    initial_soc = np.asarray(boundary_states[0].get("soc_mwh"), dtype=float)
    terminal_soc = np.asarray(boundary_states[-1].get("soc_mwh"), dtype=float)
    if (
        not np.array_equal(initial_soc, 0.5 * capacities)
        or np.max(np.abs(terminal_soc - 0.5 * capacities)) > 1e-3
    ):
        raise ValueError("S4b annual initial or terminal state mismatch")
    return envelope


def load_verified_manifest(path: Path = S4B_MANIFEST_PATH) -> dict[str, object]:
    encoded = path.read_bytes()
    envelope = json.loads(encoded)
    if encoded != canonical_json(envelope):
        raise ValueError("S4b tracked manifest is not canonical JSON")
    if sha256_path(path) != EXPECTED_MANIFEST_SHA256:
        raise ValueError("S4b tracked manifest file hash mismatch")
    return validate_manifest(
        envelope, expected_envelope_sha256=EXPECTED_MANIFEST_SHA256
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=S4B_MANIFEST_PATH)
    args = parser.parse_args()
    envelope = derive_manifest()
    validate_manifest(envelope, expected_envelope_sha256=EXPECTED_MANIFEST_SHA256)
    atomic_immutable_json(args.output, envelope)
    print(json.dumps({"path": str(args.output), "sha256": object_sha256(envelope)}))


if __name__ == "__main__":
    main()
