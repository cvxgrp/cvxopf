from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import numpy as np

from cvxopf import OPFBuild
from experiments.case118_annual_hierarchy.audit import audit_probe
from experiments.case118_annual_hierarchy.p0_fixture import frozen_p0_policy
from experiments.case118_annual_hierarchy.s2_analysis import (
    _interval_metrics,
    _shifted_primary_statistics,
)
from experiments.case118_annual_hierarchy.s2_fixture import load_s2_fixture


def _synthetic_window():
    inputs = load_s2_fixture().inputs
    steps = 3
    storage = len(inputs.storage)
    generators = len(inputs.generators)
    loads = len(inputs.loads)
    nondispatchable = len(inputs.nondispatchable)
    buses = len(np.asarray(inputs.case["bus"]))
    branches = len(np.asarray(inputs.case["branch"]))
    initial = np.asarray([unit.initial_soc for unit in inputs.storage])
    p_load = np.zeros((steps, loads))
    q_load = np.zeros((steps, loads))
    result = {
        "status": "optimal",
        "objective": 0.0,
        "Pg": np.zeros((steps, generators)).tolist(),
        "Qg": np.zeros((steps, generators)).tolist(),
        "b": np.zeros((steps, storage)).tolist(),
        "b_q": np.zeros((steps, storage)).tolist(),
        "soc": np.tile(initial, (steps, 1)).tolist(),
        "branch_p_from": np.zeros((steps, branches)).tolist(),
        "branch_q_from": np.zeros((steps, branches)).tolist(),
        "branch_p_to": np.zeros((steps, branches)).tolist(),
        "branch_q_to": np.zeros((steps, branches)).tolist(),
        "p_net": np.zeros((steps, buses)).tolist(),
        "q_net": np.zeros((steps, buses)).tolist(),
        "p_nd": np.zeros((steps, nondispatchable)).tolist(),
        "q_nd": np.zeros((steps, nondispatchable)).tolist(),
        "curtailment": np.zeros((steps, nondispatchable)).tolist(),
        "Vm": np.ones((steps, buses)).tolist(),
        "Va_deg": np.zeros((steps, buses)).tolist(),
        "branch_s_from": np.zeros((steps, branches)).tolist(),
        "branch_s_to": np.zeros((steps, branches)).tolist(),
        "p_load": p_load.tolist(),
        "q_load": q_load.tolist(),
        "p_load_served": p_load.tolist(),
        "q_load_served": q_load.tolist(),
        "storage_device_ids": [unit.device_id for unit in inputs.storage],
        "storage_terminal_deviation": np.zeros(storage).tolist(),
    }
    window_storage = tuple(
        replace(
            unit,
            initial_soc=float(initial[index]),
            terminal_soc=float(initial[index]),
            terminal_constraint="equality",
            terminal_cost=None,
            terminal_weight=None,
        )
        for index, unit in enumerate(inputs.storage)
    )
    probe = audit_probe(
        inputs.case,
        cast(OPFBuild, SimpleNamespace(formulation="ac")),
        result,
        generators=inputs.generators,
        loads=inputs.loads,
        nondispatchable=inputs.nondispatchable,
        storage=window_storage,
        delta=inputs.delta,
        tolerances=frozen_p0_policy().tolerances,
    )
    attempt_id = "ac-000-00-primary_controlling"
    return inputs, {
        "iteration": 0,
        "initial_soc_mwh": initial.tolist(),
        "target_soc_mwh": initial.tolist(),
        "post_step_soc_mwh": initial.tolist(),
        "executed_interval": {"controlling_attempt_id": attempt_id},
        "attempts": [
            {
                "attempt_id": attempt_id,
                "ordinal": 0,
                "role": "primary_controlling",
                "slot_state": "executed",
                "result": result,
                "audit": {
                    "status": probe.status,
                    "accepted_primal": probe.accepted_primal,
                    "missing_or_nonfinite_fields": list(
                        probe.missing_or_nonfinite_fields
                    ),
                    "identity_error": probe.identity_error,
                    "residuals": dict(probe.residuals),
                    "wall_time_seconds": 12.5,
                },
            }
        ],
    }


def test_s2_interval_accounting_uses_only_the_executed_first_interval():
    inputs, window = _synthetic_window()

    metrics = _interval_metrics(window, inputs)

    assert metrics["iteration"] == 0
    assert metrics["controlling_ordinal"] == 0
    assert metrics["renewable_curtailment_mwh"] == 0.0
    assert metrics["active_loss_mwh"] == 0.0
    assert metrics["soc_recurrence_residual_mwh_abs"] == 0.0
    assert metrics["reported_soc_residual_mwh_abs"] == 0.0
    assert metrics["fixed_load_service_residual_mw_abs"] == 0.0
    assert metrics["storage_throughput_mwh"] == [0.0] * 4
    assert metrics["attempt_wall_time_seconds"] == 12.5
    assert metrics["executed_attempt_count"] == 1
    assert metrics["attempt_wall_time_by_role_seconds"] == {
        "primary_controlling": 12.5
    }
    assert metrics["controlling_audit_reconstructed_and_equal"] is True


def test_s2_accounting_detects_concealed_fixed_load_nonservice():
    inputs, window = _synthetic_window()
    changed = deepcopy(window)
    changed["attempts"][0]["result"]["p_load_served"][0][0] -= 1.0

    metrics = _interval_metrics(changed, inputs)

    assert metrics["fixed_load_service_residual_mw_abs"] == 1.0
    assert metrics["controlling_audit_reconstructed_and_equal"] is False


def test_s2_independent_audit_detects_archived_residual_drift():
    inputs, window = _synthetic_window()
    changed = deepcopy(window)
    changed["attempts"][0]["audit"]["residuals"][
        "ac_reactive_balance_pu_abs"
    ] = 1.0

    metrics = _interval_metrics(changed, inputs)

    assert metrics["controlling_audit_reconstructed_and_equal"] is False


def test_shifted_primary_fraction_excludes_the_first_flat_interval():
    statistics = _shifted_primary_statistics(
        [
            {"iteration": 0, "controlling_ordinal": 0},
            {"iteration": 1, "controlling_ordinal": 0},
            {"iteration": 2, "controlling_ordinal": 2},
        ]
    )

    assert statistics == {
        "first_flat_primary_accepted": True,
        "shifted_primary_opportunity_count": 2,
        "shifted_primary_success_count": 1,
        "shifted_primary_success_fraction": 0.5,
    }
