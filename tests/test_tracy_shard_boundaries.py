"""Independent selection reconstruction; no AC construction or solves."""

import json

import numpy as np
import pytest

from experiments.case118_annual_hierarchy.s4b_manifest import (
    canonical_json,
    object_sha256,
)
from experiments.case118_tracy_2021.annual_results import HERE, load_results
from experiments.case118_tracy_2021.shard_boundaries import derive


def test_retained_manifest_continuity_and_selection():
    path = HERE / "SHARD_BOUNDARIES.json"
    envelope = json.loads(path.read_bytes())
    assert path.read_bytes() == canonical_json(envelope)
    p = envelope["manifest"]
    assert object_sha256(p) == envelope["manifest_sha256"]
    assert object_sha256(p["rule"]) == p["rule_sha256"]
    assert len(p["storage_device_ids"]) == len(set(p["storage_device_ids"])) == 27
    assert p["ac_execution_authorized"] is False
    shards = p["shards"]
    assert shards[0]["start"] == 0 and shards[-1]["stop"] == 8760
    assert sum(s["hours"] for s in shards) == 8760
    for j, s in enumerate(shards):
        assert s["hours"] == s["stop"] - s["start"]
        assert len(s["initial_soc_mwh"]) == len(s["terminal_soc_mwh"]) == 27
        if j:
            assert shards[j - 1]["stop"] == s["start"]
            assert shards[j - 1]["terminal_soc_mwh"] == s["initial_soc_mwh"]
        assert s["initial_soc_mwh"] == p["boundaries"][j]["soc_mwh"]
        assert s["terminal_soc_mwh"] == p["boundaries"][j + 1]["soc_mwh"]
        assert 0 < s["hours"] <= 792
        if j < len(shards) - 1:
            assert s["hours"] >= 672
    for r in p["rounds"]:
        candidates = r["candidates"]
        assert [c["global_boundary"] for c in candidates] == list(
            range(
                r["previous_boundary"] + 672,
                min(r["previous_boundary"] + 792, 8759) + 1,
            )
        )
        winner = min(
            (c for c in candidates if c["eligible"]),
            key=lambda c: (
                c["midpoint_deviation"],
                -c["normalized_charging"],
                abs(c["global_boundary"] - (r["previous_boundary"] + 730)),
                c["global_boundary"],
            ),
        )
        assert winner["global_boundary"] == r["selected_boundary"]


def test_full_annual_source_rederivation_and_independent_scores():
    if not (HERE / "results/stage_c/analysis.json").exists():
        pytest.skip("owner annual results unavailable")
    data = load_results()
    envelope = derive(data)
    assert canonical_json(envelope) == (HERE / "SHARD_BOUNDARIES.json").read_bytes()
    soc = data["runs"]["lossy_dc"]["boundary_soc_mwh"]
    power = data["runs"]["lossy_dc"]["result"]["b"]
    energy = data["tables"]["batteries"].capacity_mwh.to_numpy()
    rating = data["tables"]["batteries"].active_power_limit_mw.to_numpy()
    for r in envelope["manifest"]["rounds"]:
        for c in r["candidates"]:
            t = c["global_boundary"]
            active = np.any(
                abs(power[max(0, t - 3) : min(8760, t + 3)])
                >= np.maximum(1e-6, 0.001 * rating),
                axis=0,
            )
            charge = sum(max(-power[t - 1, i] / rating[i], 0) for i in range(27))
            deviation = (
                max(abs(soc[t, i] / energy[i] - 0.5) for i in np.flatnonzero(active))
                if active.any()
                else None
            )
            assert c["participating_devices"] == active.tolist()
            assert c["normalized_charging"] == pytest.approx(charge)
            assert (
                c["midpoint_deviation"] == pytest.approx(deviation)
                if deviation is not None
                else c["midpoint_deviation"] is None
            )
            assert c["eligible"] == bool(active.any() and charge >= 0.001)
    for b in envelope["manifest"]["boundaries"]:
        np.testing.assert_array_equal(b["soc_mwh"], soc[b["index"]])
