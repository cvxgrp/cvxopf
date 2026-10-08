"""COPT acceptance contract; no installation, license or solve needed."""

import json

from experiments.socp_conditioning.copt_check import accepted, portable_relaxation_audit


def test_acceptance_requires_physical_and_objective_agreement():
    record = dict(
        exception=None,
        native_optimal=True,
        audit={"passed": True},
        original_objective=3.0,
        native_vs_original_objective=1e-6,
    )
    assert accepted(record)
    for updates in (
        {"exception": "solver failure"},
        {"native_optimal": False},
        {"audit": None},
        {"audit": {"passed": False}},
        {"native_vs_original_objective": 0.2},
    ):
        assert not accepted(record | updates)


def test_native_model_handle_does_not_escape_into_json():
    audit = {"solver_statistics": {"extra_stats": object(), "solve_time": 1.5}}
    result = json.loads(json.dumps(portable_relaxation_audit(audit), allow_nan=False))
    assert result["solver_statistics"]["solve_time"] == 1.5
    assert result["solver_statistics"]["extra_stats"]["native_model_handle_omitted"]
