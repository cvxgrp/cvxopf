"""No solves: the equilibration experiment changes settings, not the model."""

import numpy as np
import pytest

from experiments.socp_conditioning.diagnostic import (
    EQUILIBRATION,
    build_variant,
    smoke_inputs,
    retained_equilibration_rejection,
)


@pytest.mark.parametrize("variant", EQUILIBRATION)
def test_identical_canonical_problem(variant):
    baseline, _, _ = build_variant(smoke_inputs(), "cones")
    candidate, _, divisor = build_variant(smoke_inputs(), variant)
    before, _, _ = baseline.prob.get_problem_data("CLARABEL", canon_backend="SCIPY")
    after, _, _ = candidate.prob.get_problem_data("CLARABEL", canon_backend="SCIPY")
    assert divisor == 1
    assert str(before["dims"]) == str(after["dims"])
    for name in ("A", "P"):
        assert not (before[name] - after[name]).nnz
    for name in ("b", "c"):
        np.testing.assert_array_equal(before[name], after[name])


def test_only_equilibration_options_are_changed():
    assert tuple(EQUILIBRATION) == ("equil_default", "equil_off", "equil_narrow")
    for settings in EQUILIBRATION.values():
        assert set(settings) == {
            "equilibrate_enable",
            "equilibrate_min_scaling",
            "equilibrate_max_scaling",
        }
    assert EQUILIBRATION["equil_off"]["equilibrate_enable"] is False
    assert EQUILIBRATION["equil_narrow"]["equilibrate_min_scaling"] == 0.01
    assert EQUILIBRATION["equil_narrow"]["equilibrate_max_scaling"] == 100


def test_infeasible_no_primal_is_retained_without_success_fields():
    record = dict(
        exception=None,
        audit=None,
        native_solution=dict(status="DualInfeasible"),
        canonical_archive=dict(sha256="retained"),
    )
    assert retained_equilibration_rejection(record, "equil_off")
    assert not retained_equilibration_rejection(record, "cones")
    record["native_solution"] = None
    assert not retained_equilibration_rejection(record, "equil_off")
