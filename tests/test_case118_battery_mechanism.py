"""Scientific phase-one contracts; no native Case118 study solves."""

from dataclasses import asdict, replace
import json
from types import SimpleNamespace
import sys

import numpy as np
import pytest

from tests.test_case118_counterfactual import TOL

from experiments.case118_counterfactual.dc import (
    audit_dc,
    build_dc,
    dc_metrics,
    execute_dc,
)
from experiments.case118_counterfactual.mechanism import phase_comparisons, selected_dc
from experiments.case118_counterfactual.runner import Study, reference
from experiments.case118_counterfactual.worker import jsonable

pytest_plugins = ["tests.test_case118_counterfactual"]


def dc_result(matched):
    inputs, _, _, _, original = matched
    out = {k: v.copy() if isinstance(v, np.ndarray) else v for k, v in original.items()}
    out["p_flows"] = np.zeros((3, len(inputs.case["branch"])))
    return out


def test_dc_audit_feasible_and_corruptions(matched):
    inputs, policy, _, window, _ = matched
    result = dc_result(matched)
    assert audit_dc(inputs, policy, window, "F", result, TOL, reported_loss_cost=0)[
        "accepted"
    ]
    for field in ("Pg", "b", "soc", "p_net", "p_flows", "p_load", "p_load_served"):
        altered = dict(result)
        altered[field] = result[field].copy()
        altered[field][0, 0] += 1
        assert not audit_dc(
            inputs, policy, window, "F", altered, TOL, reported_loss_cost=0
        )["accepted"], field
    for alteration in (
        {"objective": 1},
        {"storage_cost": 1},
        {"storage_device_ids": ["wrong"]},
        {"status": None},
        {"p_flows": np.zeros((2, 9))},
    ):
        assert not audit_dc(
            inputs,
            policy,
            window,
            "B",
            dict(result, **alteration),
            TOL,
            reported_loss_cost=0,
        )["accepted"]


def test_dc_loss_proxy_per_unit_and_nonunit_duration(matched):
    inputs, _, _, _, _ = matched
    inputs = replace(inputs, delta=0.25, options=replace(inputs.options, loss_weight=7))
    result = dc_result(matched)
    result["p_flows"][:] = 40
    metrics = dc_metrics(inputs, result)
    expected = 0.25 * 7 * 3 * np.sum(inputs.case["branch"][:, 2]) * (0.4**2)
    assert metrics["dc_loss_proxy_cost"] == pytest.approx(expected)
    assert metrics["native_objective"] == pytest.approx(
        metrics["common_cost"] + expected
    )


def test_dc_keeps_native_objective_and_only_fixed_arm_locks_battery(matched):
    inputs, policy, _, window, _ = matched
    fixed = build_dc(inputs, policy, window, "F")
    free = build_dc(inputs, policy, window, "B")
    assert fixed.prob.is_dcp() and free.prob.is_dcp()
    assert len(fixed.prob.constraints) == len(free.prob.constraints) + 1
    assert "dc_loss_cost" in fixed.expressions
    with pytest.raises(ValueError):
        build_dc(inputs, policy, window, "R1")


def test_dc_worker_real_build_extraction_with_analytic_solver(
    matched, monkeypatch, tmp_path
):
    from cvxopf import OPFBuild

    inputs, policy, _, window, _ = matched

    def analytic_solve(build, **kwargs):
        assert kwargs == {"solver": "CLARABEL", "nlp": False}
        for v in build.prob.variables():
            v.value = v.project(np.zeros(v.shape))
        for v in build.variables["soc"]:
            v.value = np.full(v.shape, 500.0)
        build.prob._status = "optimal"
        build.prob._value = float(build.prob.objective.expr.value)

    monkeypatch.setattr(OPFBuild, "solve", analytic_solve)
    for arm in ("F", "B"):
        directory = tmp_path / arm
        directory.mkdir()
        request = jsonable(
            {
                "window": asdict(window),
                "arm": arm,
                "invocation": {"test": arm},
                "dc_options": {},
                "tolerances": asdict(TOL),
            }
        )
        payload = execute_dc(directory, inputs, policy, request)
        assert payload["audit"]["accepted"], payload["audit"]
        assert payload["reported_loss_cost"] == 0
        assert [
            v["phase"]
            for v in json.loads((directory / "phase.json").read_text())["events"]
        ] == ["before_dc_build", "after_dc_build", "before_dc_solve", "after_dc_solve"]
        with pytest.raises(FileExistsError):
            execute_dc(directory, inputs, policy, request)


def test_dc_incumbent_reaudit_and_failed_candidate(matched, tmp_path):
    inputs, policy, _, window, _ = matched
    result = dc_result(matched)
    payload = {
        "window_identity": window.identity,
        "result": jsonable(result),
        "reported_loss_cost": 0,
        "exception": None,
    }
    path = tmp_path / "fixed.json"
    path.write_text(json.dumps(payload))
    ref = reference(path)
    selected = selected_dc(inputs, policy, window, None, ref, TOL)
    assert selected["selected_kind"] == "incumbent"
    assert selected["selected_audit"]["accepted"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(dict(payload, exception="failed")))
    assert (
        selected_dc(inputs, policy, window, reference(bad), ref, TOL)["selected"] == ref
    )
    bad.write_text(json.dumps(dict(payload, window_identity="other")))
    with pytest.raises(ValueError, match="different window"):
        selected_dc(inputs, policy, window, reference(bad), ref, TOL)


def test_ac_gb_chain_admission_and_original_default(matched, tmp_path, monkeypatch):
    inputs, policy, _, window, _ = matched
    context = tmp_path / "context.json"
    provenance = {"outer_sha256": "outer", "manifest_sha256": "manifest"}
    window = replace(window, provenance=provenance)
    context.write_text(json.dumps({"input_sha256": window.input_sha256, **provenance}))
    source = SimpleNamespace(
        fixture=SimpleNamespace(inputs=inputs, policy=policy),
        window=lambda start, steps: window,
    )
    protocol = {
        "windows": [
            {"id": "one", "start": 1, "steps": 3, "context": reference(context)}
        ],
        "tolerances": asdict(TOL),
    }
    study = Study(source, protocol, tmp_path / "run", arms=("G", "B"))
    monkeypatch.setattr(study.supervisor, "add_window", lambda *a, **k: None)
    study.admit("one")
    first = next(iter(study.jobs))
    assert study.jobs[first]["arm"] == "G"
    study.stages["one"].append({"selected": {"path": "first"}})
    study.admit("one")
    assert list(study.jobs.values())[-1]["arm"] == "B"
    assert list(study.jobs.values())[-1]["incumbent"] == {"path": "first"}
    assert Study(source, protocol, tmp_path / "original").arms == ("R1", "R2", "G", "B")


def test_comparison_keeps_native_dc_and_device_costs_distinct():
    def stage(arm, common, native, throughput):
        return {
            "arm": arm,
            "selected_audit": {
                "metrics": {
                    "common_cost": common,
                    "native_objective": native,
                    "throughput_mwh": throughput,
                }
            },
        }

    dc = {"one": [stage("F", 10, 15, 0), stage("B", 11, 13, 2)]}
    ac = {"one": {"stages": [stage("G", 12, 12, 0), stage("B", 10, 10, 2)]}}
    result = phase_comparisons(dc, ac)["one"]
    assert result["dc"]["native_objective_improvement"] == 2
    assert result["dc"]["device_cost_improvement"] == -1
    assert result["ac"]["native_objective_improvement"] == 2
    assert phase_comparisons(dc, {})["one"]["ac"] is None


def test_serial_dc_accepted_returns_and_phase_handoff(matched, tmp_path, monkeypatch):
    """Six real subprocess receipts, analytic DC results, no native optimizers."""
    from experiments.case118_counterfactual import mechanism
    from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
    from experiments.case118_annual_hierarchy.s5_speculative_supervisor import (
        MemorySample,
    )

    inputs, policy, _, window, _ = matched
    provenance = {"outer_sha256": "outer", "manifest_sha256": "manifest"}
    window = replace(window, provenance=provenance)
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"input_sha256": window.input_sha256, **provenance}))
    source = SimpleNamespace(
        fixture=SimpleNamespace(inputs=inputs, policy=policy),
        window=lambda start, steps: window,
    )
    protocol = {
        "windows": [
            {"id": name, "start": 1, "steps": 3, "context": reference(context)}
            for name in ("one", "two", "three")
        ],
        "tolerances": asdict(TOL),
        "dc_options": {},
        "max_dc_attempts": 12,
        "study_wall_seconds": 60,
        "worker_wall_seconds": 30,
        "total_worker_seconds": 120,
    }
    original = mechanism.SerialDC.command
    calls = []

    def analytic_command(self, spec, directory):
        original(self, spec, directory)
        request = json.loads((directory / "request.json").read_text())
        result = dc_result(matched)
        payload = jsonable(
            {
                "arm": request["arm"],
                "invocation": request["invocation"],
                "request_sha256": object_sha256(request),
                "window_identity": window.identity,
                "result": result,
                "exception": None,
                "reported_loss_cost": 0,
                "audit": audit_dc(
                    inputs,
                    policy,
                    window,
                    request["arm"],
                    result,
                    TOL,
                    reported_loss_cost=0,
                ),
            }
        )
        calls.append((spec.window.shard_id, request["arm"]))
        script = (
            "import json,time,pathlib\n"
            f"p=pathlib.Path({str(directory)!r})\n"
            f"inv=json.loads({json.dumps(request['invocation'])!r})\n"
            "events=[{'phase':v,'monotonic_seconds':time.monotonic()} for v in "
            "['before_dc_build','after_dc_build','before_dc_solve','after_dc_solve']]\n"
            "(p/'phase.json').write_text(json.dumps({'invocation':inv,'events':events}))\n"
            f"(p/'result.json').write_text({json.dumps(payload)!r})\n"
        )
        return [sys.executable, "-c", script]

    def ac_handoff(self):
        assert len(calls) == 6
        assert self.arms == ("G", "B")
        assert 0 < self.protocol["study_wall_seconds"] < 60
        assert 0 < self.protocol["total_worker_seconds"] < 120
        for name in ("one", "two", "three"):
            assert (tmp_path / "run" / "dc" / name / "B.json").exists()
        return {"complete": True, "stop_reason": None, "windows": {}}

    monkeypatch.setattr(mechanism.SerialDC, "command", analytic_command)
    monkeypatch.setattr(
        mechanism.SubprocessBackend, "memory", lambda self: MemorySample(100, (50,))
    )
    monkeypatch.setattr(mechanism.Study, "run", ac_handoff)
    summary = mechanism.run_phase(source, protocol, tmp_path / "run")
    assert summary["complete"]
    assert summary["budget_consumed"]["dc_attempts"] == 6
    assert summary["budget_consumed"]["total_worker_seconds"] > 0
    assert summary["next"] == "owner_result_review_before_prescribed_transfers"
    receipts = list((tmp_path / "run" / "dc").glob("*/dc-*/lifecycle.json"))
    assert len(receipts) == 6
    for path in receipts:
        receipt = json.loads(path.read_text())
        assert receipt["reaped"] and receipt["completion"]["outcome"] == "accepted"
        assert "complete_x0_retained" not in receipt["completion"]


def test_source_identity_includes_new_modules(tmp_path, monkeypatch):
    from experiments.case118_counterfactual import runner

    (tmp_path / "tracked.py").write_text("old\n")
    (tmp_path / "new.py").write_text("new\n")
    monkeypatch.setattr(runner, "ROOT", tmp_path)

    def output(argv, **kwargs):
        if argv[1] == "rev-parse":
            return "commit\n"
        assert "--others" in argv and "--exclude-standard" in argv
        return "tracked.py\nnew.py\n"

    monkeypatch.setattr(runner.subprocess, "check_output", output)
    before = runner.execution_identity()
    assert set(runner.execution_sources()) == {"new.py", "tracked.py"}
    (tmp_path / "new.py").write_text("changed\n")
    assert runner.execution_identity() != before


def test_snapshot_binds_exact_bytes_and_refuses_changed_source(tmp_path, monkeypatch):
    import hashlib
    from experiments.case118_counterfactual import mechanism

    sources = {"new.py": b"hello\r\n"}
    expected = {
        "commit": "base",
        "python_source_sha256": hashlib.sha256(b"new.py\0hello\r\n").hexdigest(),
    }
    monkeypatch.setattr(mechanism, "execution_sources", lambda: sources)
    monkeypatch.setattr(mechanism, "execution_identity", lambda: expected)
    monkeypatch.setattr(mechanism.subprocess, "check_output", lambda *a, **k: "")
    ref = mechanism.retain_source_snapshot(tmp_path, expected)
    saved = json.loads((tmp_path / "source-snapshot.json").read_text())
    assert saved["runtime_files"]["new.py"].encode() == sources["new.py"]
    assert ref == reference(tmp_path / "source-snapshot.json")
    with pytest.raises(ValueError, match="source changed"):
        mechanism.retain_source_snapshot(
            tmp_path, dict(expected, python_source_sha256="wrong")
        )
