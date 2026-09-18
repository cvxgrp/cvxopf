"""Focused gates for the final M14b component-box qualification slice."""

from pathlib import Path

import numpy as np
import pytest

from experiments.m14_time_vectorization.m14b_component_bounds import (
    AUDIT_TOLERANCE,
    CandidateBox,
    ComponentProbeBuild,
    GATE_PAIRS,
    HORIZON,
    PAIR_ABSOLUTE_TOLERANCE,
    _binding_probe,
    build_probe,
    compare_pair,
    pair_decisions,
    run_all,
    run_arm,
)
from experiments.m14_time_vectorization.run_m14b_component_bounds import (
    build_result,
    write_immutable,
)


@pytest.mark.parametrize(("formulation", "gate"), GATE_PAIRS)
def test_shared_harness_uses_identical_fixture_solver_and_backend(formulation, gate):
    explicit = run_arm(formulation, gate, "explicit")
    leaf = run_arm(formulation, gate, "leaf")
    pair = compare_pair(explicit, leaf)

    assert explicit["fixture_sha256"] == leaf["fixture_sha256"]
    assert explicit["solver"] == leaf["solver"] == "CLARABEL"
    assert explicit["canonical_structure"]["backend"] == "SCIPY"
    assert leaf["canonical_structure"]["backend"] == "SCIPY"
    assert explicit["classification"] == leaf["classification"] == "accepted"
    for arm in (explicit, leaf):
        assert arm["source_structure"]["problem_is_dcp"] is True
        assert arm["source_structure"]["objective_is_dcp"] is True
        assert arm["source_structure"]["all_constraints_dcp"] is True
        assert {"objective_reconstruction_abs", "p_net_reconstruction_abs"} <= set(
            arm["residuals"]
        )
    assert {"Pg", "p_net"} <= set(explicit["public_results"])
    assert ("p_flows" in explicit["public_results"]) is (formulation == "lossy_dc")
    assert max(explicit["residuals"].values()) <= AUDIT_TOLERANCE
    assert max(leaf["residuals"].values()) <= AUDIT_TOLERANCE
    assert (
        explicit["source_structure"]["explicit_inequality_objects"]
        > leaf["source_structure"]["explicit_inequality_objects"]
    )
    assert pair["fixture_fingerprints_match"] is True
    assert pair["both_accepted"] is True
    assert pair["result_schemas_match"] is True
    assert pair["cost_schemas_match"] is True
    assert pair["probe_only_objective_term_schemas_match"] is True
    assert pair["binding_probes_passed"] is True
    assert pair["objective_absolute_residual"] <= PAIR_ABSOLUTE_TOLERANCE
    assert pair["equivalent"] is True


@pytest.mark.parametrize(("formulation", "gate"), GATE_PAIRS)
def test_binding_probes_reach_every_declared_face(formulation, gate):
    for encoding in ("explicit", "leaf"):
        arm = run_arm(formulation, gate, encoding)
        for probe in arm["binding_probes"]:
            assert probe["accepted"] is True
            assert probe["maximum_face_residual"] <= AUDIT_TOLERANCE
            assert (
                probe["lower_face_coordinates"]
                + probe["upper_face_coordinates"]
                + probe["fixed_coordinates"]
                > 0
            )


@pytest.mark.parametrize("formulation", ("lossy_dc", "singlenode_dc"))
def test_storage_gate_covers_recurrence_faces_and_three_terminal_policies(
    formulation,
):
    build = build_probe(formulation, "storage", "leaf")
    arm = run_arm(formulation, "storage", "leaf")

    assert [box.family for box in build.candidate_boxes] == [
        "storage_real_power",
        "storage_soc",
    ]
    assert build.variables["b"].shape == (3, HORIZON)
    assert build.variables["soc"].shape == (3, HORIZON + 1)
    assert arm["device_ids"] == ["equality", "shortfall", "soft"]
    assert arm["public_results"]["soc"] and len(arm["public_results"]["soc"]) == HORIZON
    assert len(arm["public_results"]["storage_terminal_deviation"]) == 3
    assert set(arm["component_costs"]) == {
        "storage_cost",
        "storage_terminal_cost",
    }
    assert set(arm["residuals"]) >= {
        "storage_initial_abs",
        "storage_recurrence_abs",
        "equality_terminal_abs",
        "shortfall_terminal_abs",
        "storage_terminal_cost_abs",
    }


@pytest.mark.parametrize("formulation", ("lossy_dc", "singlenode_dc"))
def test_nondispatchable_gate_covers_zero_availability_and_rating_limits(
    formulation,
):
    build = build_probe(formulation, "nondispatchable", "leaf")
    upper = build.candidate_boxes[0].upper
    availability = np.asarray(build.inputs["availability"], dtype=float)
    rating = np.asarray(build.inputs["rating"], dtype=float)[:, None]
    arm = run_arm(formulation, "nondispatchable", "leaf")

    assert np.count_nonzero(upper == 0.0) > 0
    assert np.count_nonzero(availability < rating) > 0
    assert np.count_nonzero(availability > rating) > 0
    assert arm["public_results"]["curtailment"]
    assert arm["component_costs"] == {}
    assert set(arm["probe_only_objective_terms"]) == {
        "nondispatchable_preference_probe_only"
    }
    assert set(arm["residuals"]) >= {
        "availability_abs",
        "rating_abs",
        "curtailment_nonnegative_abs",
        "nondispatchable_preference_probe_only_abs",
    }


@pytest.mark.parametrize("formulation", ("lossy_dc", "singlenode_dc"))
def test_load_gate_covers_ineligible_width_served_load_and_cost(formulation):
    build = build_probe(formulation, "load_shedding", "leaf")
    arm = run_arm(formulation, "load_shedding", "leaf")

    assert np.count_nonzero(build.candidate_boxes[0].upper == 0.0) > 0
    assert arm["public_results"]["p_load"]
    assert arm["public_results"]["q_load"]
    assert arm["public_results"]["p_load_shed"]
    assert arm["public_results"]["p_load_shed_total"]
    assert arm["public_results"]["p_load_served"]
    assert arm["public_results"]["energy_not_served_by_load"]
    assert arm["public_results"]["energy_not_served"] >= 0.0
    assert set(arm["component_costs"]) == {"load_shedding_cost"}
    assert set(arm["residuals"]) >= {
        "eligibility_abs",
        "ineligible_fraction_abs",
        "q_load_reconstruction_abs",
        "served_reconstruction_abs",
        "shed_total_reconstruction_abs",
        "energy_not_served_by_load_abs",
        "energy_not_served_abs",
        "shedding_cost_abs",
    }


def test_hvdc_gate_covers_every_frozen_direction_and_loss_branch():
    build = build_probe("lossy_dc", "hvdc", "leaf")
    arm = run_arm("lossy_dc", "hvdc", "leaf")

    assert np.asarray(build.inputs["direction"], dtype=object).tolist() == [
        "positive",
        "negative",
        "straddling",
        "positive",
        "positive",
    ]
    assert (
        np.count_nonzero(
            build.candidate_boxes[0].lower == build.candidate_boxes[0].upper
        )
        == HORIZON
    )
    assert arm["public_results"]["p_hvdc_in"]
    assert arm["public_results"]["p_hvdc_out"]
    assert arm["public_results"]["hvdc_loss"]
    assert set(arm["residuals"]) >= {
        "hvdc_coupling_abs",
        "hvdc_loss_abs",
        "hvdc_loss_nonnegative_abs",
        "hvdc_cost_abs",
    }


def test_consolidated_matrix_emits_nine_local_decisions_without_cross_inference():
    result = run_all()

    assert result["cross_formulation_inference_permitted"] is False
    assert len(result["pairs"]) == 7
    assert len(result["decisions"]) == 9
    assert all(pair["equivalent"] for pair in result["pairs"])
    assert all(
        decision
        == {
            "leaf_bounds_qualified": True,
            "selected_representation": "leaf",
            "reason": "paired model and binding probe passed",
        }
        for decision in result["decisions"].values()
    )


def test_failed_pair_selects_explicit_without_blocking_other_gates():
    explicit = run_arm("lossy_dc", "nondispatchable", "explicit")
    leaf = run_arm("lossy_dc", "nondispatchable", "leaf")
    leaf["accepted"] = False
    leaf["classification"] = "unusable_primal"
    pair = compare_pair(explicit, leaf)

    assert pair["equivalent"] is False
    assert pair_decisions(pair) == {
        "nondispatchable_real_power": {
            "leaf_bounds_qualified": False,
            "selected_representation": "explicit",
            "reason": "paired gate regressed; retain explicit inequalities",
        }
    }


def test_real_leaf_solver_exception_is_retained_and_selects_explicit(monkeypatch):
    original_solve = ComponentProbeBuild.solve

    def fail_leaf(build: ComponentProbeBuild) -> None:
        if build.encoding == "leaf":
            raise RuntimeError("synthetic solver failure")
        original_solve(build)

    monkeypatch.setattr(ComponentProbeBuild, "solve", fail_leaf)
    explicit = run_arm("lossy_dc", "nondispatchable", "explicit")
    leaf = run_arm("lossy_dc", "nondispatchable", "leaf")
    pair = compare_pair(explicit, leaf)

    assert explicit["classification"] == "accepted"
    assert leaf["classification"] == "solver_failure"
    assert leaf["exception"] == "RuntimeError: synthetic solver failure"
    assert leaf["solver"] is None
    assert leaf["solver_iterations"] is None
    assert leaf["solve_time"] is None
    assert pair_decisions(pair)["nondispatchable_real_power"] == {
        "leaf_bounds_qualified": False,
        "selected_representation": "explicit",
        "reason": "paired gate regressed; retain explicit inequalities",
    }


def test_binding_probe_retains_solver_exception_without_statistics(monkeypatch):
    def fail_solve(*_args, **_kwargs):
        raise RuntimeError("synthetic binding failure")

    monkeypatch.setattr("cvxpy.Problem.solve", fail_solve)
    probe = _binding_probe(
        CandidateBox("x", "test_box", np.array([0.0]), np.array([1.0])),
        "leaf",
    )

    assert probe["accepted"] is False
    assert probe["exception"] == "RuntimeError: synthetic binding failure"
    assert probe["solver"] is None


def test_authoritative_runner_rejects_dirty_tree_and_result_is_immutable(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(
        "experiments.m14_time_vectorization.run_m14b_component_bounds.execution_context",
        lambda: {"worktree_clean": False},
    )
    with pytest.raises(RuntimeError, match="clean worktree"):
        build_result()

    path = tmp_path / "result.json"
    write_immutable(path, {"value": 1})
    with pytest.raises(FileExistsError):
        write_immutable(path, {"value": 2})
