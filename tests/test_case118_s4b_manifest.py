from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.case118_annual_hierarchy import s4b_manifest as manifest


EXPECTED_SELECTIONS = (
    (682, 0.05284339818428413, 1.6728369988691338, [True, True, False, False]),
    (1452, 0.07256307777486415, 1.1421819084435003, [True, True, False, False]),
    (2213, 0.03266179982329953, 0.43291619875962695, [False, False, False, True]),
    (2965, 0.12646061291406652, 0.029562181207357062, [False, True, False, False]),
    (3723, 0.009171329601460776, 0.8533111823692214, [False, False, False, True]),
    (4468, 0.09745365048860599, 0.8933853857907565, [False, False, False, True]),
    (5211, 0.08790595135029161, 0.8023393651143274, [False, False, False, True]),
    (5956, 0.025644231970695874, 0.8900996911979339, [False, False, False, True]),
    (6726, 0.07342851816243312, 0.9360393030864044, [False, False, False, True]),
    (7475, 0.07434818082424716, 1.995784954505918, [True, True, False, False]),
    (8187, 0.013469264062433484, 0.589176713004654, [False, False, False, True]),
)


def _tracked() -> dict[str, object]:
    return cast(dict[str, object], json.loads(manifest.S4B_MANIFEST_PATH.read_text()))


def _rehash(value: dict[str, object]) -> dict[str, object]:
    value["manifest_sha256"] = manifest.object_sha256(value["manifest"])
    return value


def test_tracked_manifest_has_frozen_noncircular_identity() -> None:
    envelope = manifest.load_verified_manifest()

    assert manifest.object_sha256(envelope) == manifest.EXPECTED_MANIFEST_SHA256
    assert set(envelope) == {"schema_version", "manifest_sha256", "manifest"}
    payload = envelope["manifest"]
    assert isinstance(payload, dict)
    assert "manifest_sha256" not in payload
    assert all("manifest_sha256" not in shard for shard in payload["shards"])


def test_rule_payload_freezes_binary64_characterization() -> None:
    rule = manifest.rule_payload()

    assert manifest.canonical_json(1e-6) == b"1e-06"
    assert manifest.canonical_json(0.001) == b"0.001"
    assert rule["participation"] == {
        "neighborhood_start_offset_steps": -3,
        "neighborhood_stop_offset_steps": 3,
        "neighborhood_stop_exclusive": True,
        "absolute_power_floor_mw": 1e-6,
        "power_rating_fraction": 0.001,
        "comparison": "greater_than_or_equal",
    }
    assert rule["selection_order"] == [
        "minimum_midpoint_deviation",
        "maximum_normalized_charging",
        "minimum_nominal_boundary_distance",
        "earliest_global_boundary",
    ]
    assert rule["authoritative_sources"] == {
        "s4_results_sha256": manifest.S4_RESULTS_SHA256,
        "outer_archive_sha256": manifest.S4_OUTER_ARCHIVE_SHA256,
        "outer_signpost_sha256": manifest.S4_SIGNPOST_SHA256,
    }


def test_every_candidate_and_selected_score_reconstructs() -> None:
    payload = manifest.load_verified_manifest()["manifest"]
    assert isinstance(payload, dict)
    rounds = payload["derivation_rounds"]

    assert payload["boundary_indices"] == list(manifest.EXPECTED_BOUNDARIES)
    assert payload["shard_lengths"] == list(manifest.EXPECTED_SHARD_LENGTHS)
    assert sum(len(item["candidates"]) for item in rounds) == 11 * 121
    for round_payload, expected in zip(rounds, EXPECTED_SELECTIONS, strict=True):
        selected = next(
            candidate
            for candidate in round_payload["candidates"]
            if candidate["global_boundary"] == round_payload["selected_boundary"]
        )
        boundary, deviation, charging, participating = expected
        assert round_payload["selected_boundary"] == boundary
        assert selected["midpoint_deviation"] == deviation
        assert selected["normalized_charging"] == charging
        assert selected["participating_devices"] == participating


def test_shards_retain_exact_aligned_state_and_global_seed_rule() -> None:
    payload = manifest.load_verified_manifest()["manifest"]
    assert isinstance(payload, dict)
    shards = payload["shards"]

    assert len(shards) == 12
    for ordinal, shard in enumerate(shards):
        assert (
            shard["storage"]["terminal_state"]
            == payload["boundary_states"][ordinal + 1]
        )
        assert shard["storage"]["initial_state"] == payload["boundary_states"][ordinal]
        recovery = shard["controller"]["recovery"]
        assert recovery["global_iteration_coordinates"] is True
        assert recovery["seed_base"] == 17_000_000
        assert recovery["seed_formula"] == (
            "seed_base+100*global_iteration+10*source_code+scale_index"
        )
        assert shard["identity"]["rule_sha256"] == payload["rule_sha256"]
    for left, right in zip(shards, shards[1:]):
        assert left["storage"]["terminal_state"] == right["storage"]["initial_state"]


@pytest.mark.skipif(
    not manifest.S4_OUTER_ARCHIVE_PATH.is_file(),
    reason="ignored authoritative S4 archive is unavailable",
)
def test_authoritative_archive_rederives_tracked_manifest_byte_exactly() -> None:
    derived = manifest.derive_manifest()

    assert derived == _tracked()
    assert manifest.canonical_json(derived) == manifest.S4B_MANIFEST_PATH.read_bytes()
    assert sha256(manifest.S4B_MANIFEST_PATH.read_bytes()).hexdigest() == (
        manifest.EXPECTED_MANIFEST_SHA256
    )


def test_no_eligible_candidate_blocks_derivation() -> None:
    boundary = np.tile(np.asarray([[1.0, 2.0, 3.0, 4.0]]), (8_761, 1))
    power = np.zeros((8_760, 4), dtype=float)

    with pytest.raises(ValueError, match="no eligible S4b shard boundary"):
        manifest.derive_boundary_rounds(
            boundary,
            power,
            np.ones(4, dtype=float) * 10.0,
            np.ones(4, dtype=float),
        )


def test_envelope_hash_corruption_is_rejected() -> None:
    corrupted = _tracked()
    cast(dict[str, Any], corrupted["manifest"])["execution_authorized"] = True

    with pytest.raises(ValueError, match="payload hash mismatch"):
        manifest.validate_manifest(corrupted)


def test_noncanonical_tracked_bytes_are_rejected(tmp_path: Path) -> None:
    path = tmp_path.joinpath("manifest.json")
    path.write_text(json.dumps(_tracked(), indent=2))

    with pytest.raises(ValueError, match="not canonical JSON"):
        manifest.load_verified_manifest(path)


@pytest.mark.parametrize(
    "field",
    [
        "boundary_soc_mwh",
        "preceding_storage_power_mw",
        "local_peak_absolute_power_mw",
    ],
)
def test_truncated_candidate_device_vectors_are_rejected(field: str) -> None:
    corrupted = deepcopy(_tracked())
    payload = cast(dict[str, Any], corrupted["manifest"])
    candidate = payload["derivation_rounds"][0]["candidates"][0]
    candidate[field] = candidate[field][:-1]
    _rehash(corrupted)

    with pytest.raises(ValueError, match="one finite value per storage device"):
        manifest.validate_manifest(corrupted)


def test_negative_candidate_peak_is_rejected() -> None:
    corrupted = deepcopy(_tracked())
    payload = cast(dict[str, Any], corrupted["manifest"])
    payload["derivation_rounds"][0]["candidates"][0]["local_peak_absolute_power_mw"][
        0
    ] = -1.0
    _rehash(corrupted)

    with pytest.raises(ValueError, match="peak power must be nonnegative"):
        manifest.validate_manifest(corrupted)


@pytest.mark.parametrize(
    "mutation, match",
    [
        (
            lambda payload: payload["global_provenance"].__setitem__(
                "outer_signpost_sha256", "0" * 64
            ),
            "global provenance mismatch",
        ),
        (
            lambda payload: payload["rule_payload"]["charging"].__setitem__(
                "minimum_normalized_statistic", 0.002
            ),
            "frozen contract mismatch",
        ),
        (
            lambda payload: payload["derivation_rounds"][0]["candidates"][
                0
            ].__setitem__("normalized_charging", 999.0),
            "candidate score does not reconstruct",
        ),
        (
            lambda payload: payload["derivation_rounds"][0].__setitem__(
                "selected_boundary", 683
            ),
            "selected boundary does not reproduce",
        ),
        (
            lambda payload: payload["shards"][1]["storage"]["initial_state"][
                "soc_mwh"
            ].__setitem__(0, -1.0),
            "boundary state identity mismatch",
        ),
    ],
)
def test_self_consistently_rehashed_semantic_corruption_is_rejected(
    mutation: object, match: str
) -> None:
    corrupted = deepcopy(_tracked())
    assert callable(mutation)
    mutation(corrupted["manifest"])
    _rehash(corrupted)

    with pytest.raises(ValueError, match=match):
        manifest.validate_manifest(corrupted)


def test_tracked_identity_rejects_an_otherwise_valid_envelope() -> None:
    copied = deepcopy(_tracked())
    cast(dict[str, Any], copied["manifest"])["classification"] = (
        "derived_manifest_execution_not_authorized"
    )
    _rehash(copied)

    with pytest.raises(ValueError, match="tracked manifest file identity mismatch"):
        manifest.validate_manifest(
            copied,
            expected_envelope_sha256="0" * 64,
        )
