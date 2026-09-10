"""Focused M14a.1 formulation-separated leaf-bound qualification tests."""

from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from experiments.m14_time_vectorization.m14a1_bounds import (
    BOUND_PROFILES,
    FORMULATIONS,
    HORIZON,
    build_qualification,
    compare_pair,
    run_all,
    run_qualification,
)
from experiments.m14_time_vectorization.run_m14a1 import write_immutable


@pytest.mark.parametrize("formulation", FORMULATIONS)
@pytest.mark.parametrize("profile", BOUND_PROFILES)
def test_leaf_and_explicit_models_have_identical_time_last_variables(
    formulation, profile
):
    explicit = build_qualification(formulation, "explicit", profile)
    leaf = build_qualification(formulation, "leaf", profile)

    assert {name: value.shape for name, value in explicit.variables.items()} == {
        name: value.shape for name, value in leaf.variables.items()
    }
    assert all(value.shape[-1] == HORIZON for value in leaf.variables.values())
    assert len(explicit.problem.constraints) > len(leaf.problem.constraints)


def test_static_bounds_are_broadcast_views_and_dynamic_bounds_are_materialized():
    static = build_qualification("ac", "leaf", "static")
    dynamic = build_qualification("ac", "leaf", "time_varying")

    assert np.asarray(static.inputs["Pg_lower"]).flags.owndata is False
    assert np.asarray(static.inputs["Pg_upper"]).flags.owndata is False
    assert np.asarray(dynamic.inputs["Pg_lower"]).flags.owndata is True
    assert np.asarray(dynamic.inputs["Pg_upper"]).flags.owndata is True


@pytest.mark.parametrize("formulation", FORMULATIONS)
@pytest.mark.parametrize("profile", BOUND_PROFILES)
def test_each_formulation_passes_its_own_paired_solver_gate(formulation, profile):
    explicit = run_qualification(formulation, "explicit", profile)
    leaf = run_qualification(formulation, "leaf", profile)
    pair = compare_pair(explicit, leaf)

    assert pair["both_accepted"] is True
    assert pair["equivalent"] is True
    assert pair["binding_probes_passed"] is True
    assert all(
        probe["accepted"]
        and probe["solver"] == "CLARABEL"
        and probe["canonicalization_backend"] == "SCIPY"
        and probe["lower_face_coordinates"] > 0
        and probe["upper_face_coordinates"] > 0
        for arm in (explicit, leaf)
        for probe in arm["binding_probes"]
    )
    assert explicit["source_structure"]["explicit_inequality_objects"] > 0
    assert leaf["source_structure"]["explicit_inequality_objects"] == 0
    if formulation == "ac":
        assert explicit["canonical_structure"] is None
        assert leaf["canonical_structure"] is None
        assert explicit["solver"] == "IPOPT"
        assert leaf["solver"] == "IPOPT"
    else:
        assert explicit["canonical_structure"]["backend"] == "SCIPY"
        assert leaf["canonical_structure"]["backend"] == "SCIPY"


def test_consolidated_decisions_are_formulation_local():
    result = run_all()

    assert result["cross_formulation_inference_permitted"] is False
    assert set(result["decisions"]) == set(FORMULATIONS)
    assert len(result["pairs"]) == 6
    assert result["decisions"]["lossy_dc"]["leaf_bounds_qualified"] is True
    assert result["decisions"]["singlenode_dc"]["leaf_bounds_qualified"] is True
    assert result["decisions"]["ac"]["isolated_leaf_compatibility_passed"] is True
    assert result["decisions"]["ac"]["leaf_bounds_qualified"] is False
    assert result["decisions"]["ac"]["selected_representation"] == "explicit"


def test_ac_audit_reports_independently_reconstructed_physical_channels():
    result = run_qualification("ac", "explicit", "static")

    assert "equality_abs" not in result["residuals"]
    assert set(result["residuals"]) >= {
        "active_balance_abs",
        "reactive_balance_abs",
        "reference_angle_abs",
    }


def test_nongated_ac_coordinates_are_retained_without_forcing_uniqueness():
    explicit = run_qualification("ac", "explicit", "static")
    leaf = deepcopy(run_qualification("ac", "leaf", "static"))
    leaf["values"]["Qg"][0][0] += 1e-3

    pair = compare_pair(explicit, leaf)

    assert pair["value_absolute_residuals"]["Qg"] >= 9e-4
    assert pair["gated_value_names"] == ["Pg"]
    assert pair["equivalent"] is True


def test_immutable_result_writer_refuses_replacement(tmp_path: Path):
    path = tmp_path / "result.json"
    write_immutable(path, {"value": 1})
    with pytest.raises(FileExistsError):
        write_immutable(path, {"value": 2})
