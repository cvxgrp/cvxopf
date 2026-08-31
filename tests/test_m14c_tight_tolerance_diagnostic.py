from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, cast

import numpy as np
import pytest

from cvxpy.reductions.solvers.conic_solvers.clarabel_conif import CLARABEL
from experiments.m14_time_vectorization import (
    m14c_tight_tolerance_diagnostic as diagnostic,
)


def test_diagnostic_freezes_tight_clarabel_options_and_source_scope() -> None:
    assert diagnostic.TIGHT_CLARABEL_OPTIONS == {
        "tol_gap_abs": 1e-10,
        "tol_gap_rel": 1e-10,
        "tol_feas": 1e-10,
    }
    relative = {
        path.relative_to(diagnostic.ROOT).as_posix()
        for path in diagnostic._source_paths()
    }
    assert {
        "experiments/m14_time_vectorization/M14C_TIGHT_TOLERANCE_DIAGNOSTIC.md",
        "experiments/m14_time_vectorization/m14c_tight_tolerance_diagnostic.py",
        "src/cvxopf/dc_problem.py",
    } <= relative
    assert len(diagnostic.diagnostic_source_fingerprint()) == 64


def test_clarabel_capture_retains_gap_and_residuals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution = SimpleNamespace(
        status="Solved",
        iterations=7,
        solve_time=0.5,
        obj_val=10.0,
        obj_val_dual=9.999999,
        r_prim=2e-11,
        r_dual=3e-11,
    )

    def solve(self: object, *args: object, **kwargs: object) -> object:
        del self, args, kwargs
        return solution

    monkeypatch.setattr(CLARABEL, "solve_via_data", solve)
    captured: dict[str, object] = {}
    _, wrapper = diagnostic._capture_clarabel_solution(captured)
    assert wrapper(object()) is solution
    assert captured["primal_objective"] == 10.0
    assert captured["dual_objective"] == 9.999999
    assert float(cast(float, captured["absolute_gap"])) == pytest.approx(1e-6)
    assert float(cast(float, captured["relative_gap"])) == pytest.approx(1e-7)
    assert captured["primal_residual"] == 2e-11
    assert captured["dual_residual"] == 3e-11


def test_full_bounds_audit_covers_every_present_component_family() -> None:
    build = SimpleNamespace(
        data={
            "baseMVA": 100.0,
            "Pgmin": np.array([0.0]),
            "Pgmax": np.array([1.0]),
            "f_max": np.array([1.0]),
            "storage_apparent_power_rating": np.array([10.0]),
            "storage_capacity": np.array([20.0]),
            "storage_initial_soc": np.array([10.0]),
            "storage_delta": 1.0,
            "storage_terminal_soc": np.array([10.0]),
            "sheddable_load_indices": np.array([], dtype=int),
            "load_max_shed_fraction": np.array([0.0]),
            "nd_available": np.array([[5.0], [5.0]]),
            "nd_apparent_power_rating": np.array([5.0]),
        }
    )
    result: Mapping[str, object] = {
        "Pg": np.array([[50.0], [50.0]]),
        "p_flows": np.zeros((2, 1)),
        "b": np.array([[1.0], [-1.0]]),
        "soc": np.array([[9.0], [10.0]]),
        "p_load": np.array([[40.0], [40.0]]),
        "p_load_served": np.array([[40.0], [40.0]]),
        "p_nd": np.array([[5.0], [4.0]]),
        "curtailment": np.array([[0.0], [1.0]]),
    }
    audit = diagnostic.full_bounds_audit(cast(object, build), result)
    assert audit["accepted"] is True
    residuals = cast(Mapping[str, float], audit["residuals"])
    assert residuals["maximum_abs"] == 0.0
    assert {
        "generator_active_bound_mw_abs",
        "branch_flow_bound_mw_abs",
        "storage_power_bound_mw_abs",
        "storage_energy_bound_mwh_abs",
        "load_shed_fraction_bound_abs",
        "nondispatchable_active_bound_mw_abs",
        "nondispatchable_rating_bound_mw_abs",
    } <= residuals.keys()

    build.data["nd_apparent_power_rating"] = np.array([4.0])
    violated = diagnostic.full_bounds_audit(cast(object, build), result)
    violated_residuals = cast(Mapping[str, float], violated["residuals"])
    assert violated["accepted"] is False
    assert violated_residuals["nondispatchable_rating_bound_mw_abs"] == 1.0


def test_diagnostic_comparison_retains_tight_solver_and_bounds_evidence() -> None:
    result = {
        "objective": 10.0,
        "Pg": [[1.0]],
        "b": [[2.0]],
        "soc": [[3.0]],
        "p_net": [[4.0]],
        "p_flows": [[5.0]],
    }
    payload = {
        "result": result,
        "objective_accounting": {
            "objective": 10.0,
            "components": {"generation_cost": 9.0, "dc_loss_cost": 1.0},
        },
        "solver_statistics": {"clarabel": {"relative_gap": 1e-11}},
        "bounds_audit": {"accepted": True, "residuals": {"maximum_abs": 0.0}},
    }
    comparison = diagnostic._comparison(payload, payload)
    assert comparison["objective_absolute_difference"] == 0.0
    assert comparison["p_flows_coordinate_comparison"] == ("residual_gated_nonunique")
    assert comparison["stepwise_clarabel"] == {"relative_gap": 1e-11}
    assert comparison["stepwise_bounds_audit"] == payload["bounds_audit"]


def test_accepted_arm_requires_native_clarabel_solved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = {"horizon_steps": 24, "temporal_assembly": "stepwise"}
    monkeypatch.setattr(
        diagnostic, "diagnostic_context", lambda horizon, assembly: context
    )
    clarabel = {
        "status": "AlmostSolved",
        "primal_objective": 1.0,
        "dual_objective": 1.0,
        "absolute_gap": 0.0,
        "relative_gap": 0.0,
        "primal_residual": 0.0,
        "dual_residual": 0.0,
    }
    payload = {
        "schema_version": diagnostic.SCHEMA_VERSION,
        "classification": "accepted",
        "exception": None,
        "context": context,
        "end_context": context,
        "context_matches": True,
        "audit": {"accepted_primal": True},
        "bounds_audit": {"accepted": True},
        "solver_statistics": {"solver_name": "CLARABEL", "clarabel": clarabel},
    }
    assert not diagnostic._accepted_arm(payload, 24, "stepwise")
    clarabel["status"] = "Solved"
    assert diagnostic._accepted_arm(payload, 24, "stepwise")


def test_worker_retains_solver_failure_without_primal_reconstruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "arm"
    directory.mkdir()
    context = {"horizon_steps": 24, "temporal_assembly": "stepwise"}
    monkeypatch.setattr(
        diagnostic, "diagnostic_context", lambda horizon, assembly: context
    )

    class FailedBuild:
        temporal_assembly = "stepwise"
        canonicalization_backend = "CPP"
        prob = SimpleNamespace(solver_stats=None)

        def solve(self, **kwargs: object) -> None:
            del kwargs
            raise RuntimeError("synthetic solver failure")

    monkeypatch.setattr(
        diagnostic.streaming_runner,
        "build_window",
        lambda *args, **kwargs: FailedBuild(),
    )
    monkeypatch.setattr(
        diagnostic,
        "extract_results",
        lambda build: {
            "status": "solver_error",
            "objective": np.nan,
            "Pg": None,
            "p_flows": None,
            "p_net": None,
            "b": None,
            "soc": None,
            "p_load": None,
            "p_load_served": None,
        },
    )
    monkeypatch.setattr(
        diagnostic,
        "audit_probe",
        lambda *args, **kwargs: SimpleNamespace(
            accepted_primal=False,
            status="solver_error",
            residuals={},
            missing_or_nonfinite_fields=("objective",),
            identity_error=None,
        ),
    )
    monkeypatch.setattr(diagnostic, "process_rss_bytes", lambda: 1)

    assert diagnostic._worker(directory, 24, "stepwise") == 1
    payload = cast(
        Mapping[str, object], json.loads((directory / "arm-result.json").read_text())
    )
    assert payload["classification"] == "solver_failure"
    assert "synthetic solver failure" in str(payload["exception"])
    assert (
        cast(Mapping[str, object], payload["bounds_audit"])["classification"]
        == "unavailable_primal"
    )


def test_diagnostic_refuses_dirty_source_before_creating_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "diagnostic"
    monkeypatch.setattr(diagnostic, "_git", lambda *args: "dirty")
    with pytest.raises(ValueError, match="clean committed"):
        diagnostic.run_diagnostic(directory)
    assert not directory.exists()
