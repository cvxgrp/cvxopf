"""Prescribed-shift contracts: units, locks, source roles and cumulative budgets."""

from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import numpy as np
import pytest

from experiments.case118_counterfactual.dc import audit_dc, build_dc
from experiments.case118_counterfactual.mechanism import SerialDC
from experiments.case118_counterfactual.model import audit_result, build_arm
from experiments.case118_counterfactual.runner import Study, reference
from experiments.case118_counterfactual.transfers import (
    Transfers,
    comparisons,
    prescribed_schedule,
    remaining_budget,
    verify_previous_work,
)
from experiments.case118_annual_hierarchy.s5_speculative_policy import AttemptSpec
from tests.test_case118_battery_mechanism import dc_result
from tests.test_case118_counterfactual import TOL

pytest_plugins = ["tests.test_case118_counterfactual"]


def test_transfer_energy_signs_endpoints_and_nonunit_delta(matched):
    inputs, policy, _, window, _ = matched
    inputs = replace(inputs, delta=0.25)
    b, soc = prescribed_schedule(inputs, policy, window, [1], 5)
    np.testing.assert_allclose(b[:, 0], [-20, 0, 20])
    np.testing.assert_allclose(soc[:, 0], [505, 505, 500])
    np.testing.assert_array_equal(window.battery_mw, 0)
    assert inputs.delta * np.abs(b).sum() == 10
    for weights, energy in (
        ([0.9], 1),
        ([-1], 1),
        ([float("nan")], 1),
        ([1], -1),
        ([1], True),
        ([1], 1e6),
    ):
        with pytest.raises(ValueError):
            prescribed_schedule(inputs, policy, window, weights, energy)


def test_fixed_baseline_cannot_pass_transfer_lock_and_free_arms_reject_schedule(
    matched,
):
    inputs, policy, _, window, ac = matched
    b, _ = prescribed_schedule(inputs, policy, window, [1], 1)
    for formulation, result, audit, arm, extra in (
        ("ac", ac, audit_result, "G", {"reported_common_cost": 0}),
        ("dc", dc_result(matched), audit_dc, "F", {"reported_loss_cost": 0}),
    ):
        assert audit(inputs, policy, window, arm, result, TOL, **extra)["accepted"]
        shifted = audit(
            inputs, policy, window, arm, result, TOL, battery_schedule_mw=b, **extra
        )
        assert not shifted["accepted"], formulation
        assert shifted["residuals"]["battery_lock_mw_abs"] == 1
        with pytest.raises(ValueError):
            audit(
                inputs, policy, window, "B", result, TOL, battery_schedule_mw=b, **extra
            )
    for build, arm in ((build_arm, "G"), (build_dc, "F")):
        model = build(inputs, policy, window, arm, battery_schedule_mw=b)
        prob = model.build.prob if hasattr(model, "build") else model.prob
        lock = prob.constraints[-1]
        for variable in lock.variables():
            variable.value = np.zeros(variable.shape)
        assert np.max(lock.violation()) == 1
        with pytest.raises(ValueError):
            build(inputs, policy, window, "B", battery_schedule_mw=b)


def test_external_initialization_is_not_an_incumbent_and_schedule_reaches_workers(
    matched, tmp_path, monkeypatch
):
    inputs, policy, _, window, _ = matched
    provenance = {"outer_sha256": "outer", "manifest_sha256": "manifest"}
    window = replace(window, provenance=provenance)
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"input_sha256": window.input_sha256, **provenance}))
    source = SimpleNamespace(
        fixture=SimpleNamespace(inputs=inputs, policy=policy),
        window=lambda a, b: window,
    )
    protocol = {
        "windows": [
            {"id": "one", "start": 1, "steps": 3, "context": reference(context)}
        ],
        "tolerances": asdict(TOL),
        "max_attempts": 42,
        "ac_options": {},
        "dc_options": {},
    }
    schedule, _ = prescribed_schedule(inputs, policy, window, [1], 1)
    initial = {"path": "fixed-baseline.json", "sha256": "retained"}
    study = Study(
        source,
        protocol,
        tmp_path / "ac",
        arms=("G",),
        battery_schedules={"one": schedule},
        initial_sources={"one": initial},
    )
    monkeypatch.setattr(study.supervisor, "add_window", lambda *a, **k: None)
    study.admit("one")
    key = next(iter(study.jobs))
    assert study.incumbent(key) is None
    assert study.race(key, has_preceding=False).__class__.__name__ == "FixedBatteryRace"
    for order, slot in ((0, 0), (1, 6)):
        directory = tmp_path / f"ac-{slot}"
        directory.mkdir()
        study.command(AttemptSpec(key, order, slot), directory)
        request = json.loads((directory / "request.json").read_text())
        assert request["source"] == initial
        np.testing.assert_array_equal(request["battery_schedule_mw"], schedule)
    serial = SerialDC(study, tmp_path / "dc", arms=("F",))
    assert serial.arms == ("F",)
    serial.jobs[key] = ("one", "F")
    directory = tmp_path / "dc-request"
    directory.mkdir()
    serial.command(AttemptSpec(key, 0, 0), directory)
    request = json.loads((directory / "request.json").read_text())
    np.testing.assert_array_equal(request["battery_schedule_mw"], schedule)


def test_budget_deducts_both_models_and_does_not_reset_worker_limit():
    protocol = {
        "study_wall_seconds": 10800,
        "total_worker_seconds": 21600,
        "max_attempts": 48,
        "max_dc_attempts": 12,
        "worker_wall_seconds": 5400,
    }
    consumed = {
        "active_wall_seconds": 421.9025,
        "total_worker_seconds": 638.2417,
        "ac_attempts": 6,
        "dc_attempts": 6,
    }
    result = remaining_budget(protocol, consumed)
    assert result["study_wall_seconds"] == pytest.approx(10378.0975)
    assert result["total_worker_seconds"] == pytest.approx(20961.7583)
    assert (
        result["max_attempts"],
        result["max_dc_attempts"],
        result["worker_wall_seconds"],
    ) == (42, 6, 5400)
    for values in (
        dict(consumed, dc_attempts=7),
        dict(consumed, ac_attempts=43),
        dict(consumed, active_wall_seconds=10800),
        dict(consumed, dc_attempts=1.5),
    ):
        with pytest.raises(ValueError):
            remaining_budget(protocol, values)


def test_prior_budget_must_match_receipts(tmp_path):
    path = tmp_path / "dc" / "one" / "attempt"
    path.mkdir(parents=True)
    receipt = {"reaped": True, "worker_wall_seconds": 2.0, "artifacts": {}}
    (path / "lifecycle.json").write_text(json.dumps(receipt))
    summary = {
        "budget_consumed": {
            "dc_attempts": 1,
            "ac_attempts": 0,
            "total_worker_seconds": 2.0,
        }
    }
    verify_previous_work(tmp_path, summary)
    for key, value in [("dc_attempts", 0), ("total_worker_seconds", 1.0)]:
        bad = {"budget_consumed": dict(summary["budget_consumed"], **{key: value})}
        with pytest.raises(ValueError):
            verify_previous_work(tmp_path, bad)


def test_report_sign_and_energy_normalization_keep_dc_proxy_separate():
    def metrics(common, storage, proxy):
        return {
            "common_cost": common,
            "native_objective": common + proxy,
            "generation_cost": common - storage,
            "storage_cost": storage,
            "throughput_mwh": storage,
            "dc_loss_proxy_cost": proxy,
            "branch_loss_mwh": 0.0,
        }

    fixed = metrics(100, 0, 2)
    changed = metrics(98, 10, 5)
    prepared = Transfers(
        {},
        {},
        {"one": {f: {"metrics": fixed} for f in ("ac", "dc")}},
        {"one": {"window": "may03", "transfer_mwh": 5}},
        {},
    )
    stage = {"selected_audit": {"metrics": changed}}
    result = comparisons(prepared, {"one": [stage]}, {"one": {"stages": [stage]}})[
        "one"
    ]
    assert result["ac"]["native_objective_change_per_mwh"] == -0.4
    assert result["dc"]["native_objective_change_per_mwh"] == 0.2
    assert result["dc"]["device_cost_change_per_mwh"] == -0.4
    assert comparisons(prepared, {}, {})["one"]["ac"] is None


@pytest.mark.parametrize("dc_failure", [False, True])
def test_transfer_phase_handoff_and_cumulative_accounting(
    matched, tmp_path, monkeypatch, dc_failure
):
    """Exercise phase orchestration without rerunning any native optimizer."""
    from experiments.case118_counterfactual import transfers

    _, _, _, window, _ = matched
    names = [f"window-{j}" for j in range(6)]
    settings = {
        "study_wall_seconds": 100,
        "total_worker_seconds": 200,
        "max_attempts": 42,
        "max_dc_attempts": 6,
    }
    consumed = {
        "active_wall_seconds": 10.0,
        "total_worker_seconds": 20.0,
        "ac_attempts": 6,
        "dc_attempts": 6,
    }
    metrics = {
        k: 0.0
        for k in (
            "generation_cost",
            "storage_cost",
            "common_cost",
            "native_objective",
            "throughput_mwh",
            "branch_loss_mwh",
            "dc_loss_proxy_cost",
        )
    }
    baseline = {
        f: {"result": {"path": "fixed.json"}, "metrics": metrics} for f in ("ac", "dc")
    }
    prepared = Transfers(
        settings,
        {n: window.battery_mw for n in names},
        {n: baseline for n in names},
        {n: {"window": n, "transfer_mwh": 1} for n in names},
        consumed,
    )
    calls = []

    class FakeStudy:
        def __init__(
            self, source, protocol, output, *, arms, battery_schedules, initial_sources
        ):
            assert (
                arms == ("G",) and len(initial_sources) == len(battery_schedules) == 6
            )
            self.protocol, self.execution = protocol, {"commit": "test"}
            self.windows = {n: window for n in names}
            self.backend = SimpleNamespace(children={}, seconds=0.0)

        def run(self):
            calls.append("ac")
            assert not dc_failure
            assert 0 < self.protocol["study_wall_seconds"] < 100
            assert self.protocol["total_worker_seconds"] == 197
            assert self.protocol["max_attempts"] == 42
            self.backend.children = dict.fromkeys(names)
            self.backend.seconds = 9.0
            return {
                "complete": True,
                "stop_reason": None,
                "windows": {
                    n: {"stages": [{"selected_audit": {"metrics": metrics}}]}
                    for n in names
                },
            }

    class FakeDC:
        def __init__(self, study, output, *, arms):
            assert arms == ("F",)
            self.records = {}
            self.backend = SimpleNamespace(children={}, seconds=0.0)

        def run(self, *, started):
            calls.append("dc")
            self.backend.children = dict.fromkeys(names[:1] if dc_failure else names)
            self.backend.seconds = 3.0
            if dc_failure:
                raise RuntimeError("unresolved DC comparison")
            self.records = {
                n: [{"selected_audit": {"metrics": metrics}}] for n in names
            }

    monkeypatch.setattr(transfers, "prepare", lambda *a: prepared)
    monkeypatch.setattr(transfers, "Study", FakeStudy)
    monkeypatch.setattr(transfers, "SerialDC", FakeDC)
    monkeypatch.setattr(transfers, "worker_seconds", lambda backend: backend.seconds)
    monkeypatch.setattr(transfers, "execution_identity", lambda: {"commit": "test"})
    summary = transfers.run(None, {}, tmp_path / "run")
    assert calls == (["dc"] if dc_failure else ["dc", "ac"])
    assert summary["complete"] is (not dc_failure)
    assert summary["budget_consumed"]["total_worker_seconds"] == (
        23 if dc_failure else 32
    )
    assert summary["budget_consumed"]["dc_attempts"] == (7 if dc_failure else 12)
    assert summary["budget_consumed"]["ac_attempts"] == (6 if dc_failure else 12)
    assert summary["budget_consumed"]["active_wall_seconds"] >= 10
    assert (tmp_path / "run/summary.json").exists()
