"""Frozen deficit dispatch and offline stopping semantics, without solving."""

import pytest

from experiments.socp_conditioning import joint_deficit as runner
from experiments.socp_conditioning import joint_scaling as j
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import joint_stopping_analysis as a


def test_rule_source_is_unchanged():
    assert d.sha(j.__file__) == runner.FROZEN_RULE_SHA256


def test_deficit_worker_keeps_policy_and_rule(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(runner, "BASE_WORKER", lambda *args, **kw: calls.append(kw))
    runner.worker(tmp_path / "shedding_baseline", tmp_path, "shedding_baseline")
    assert calls[0]["case_name"] == "deficit"
    assert calls[0]["scaling_strategy"] is j.joint_scales
    verify = calls[0]["reference_validator"]
    assert verify(None, None, "input", False)["input_sha256"] == "input"
    with pytest.raises(ValueError, match="policy"):
        verify(None, None, "input", True)
    with pytest.raises(ValueError, match="pair"):
        runner.worker(tmp_path, tmp_path, "fixed_scaled")


def test_scaled_deficit_binds_baseline(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(runner, "BASE_WORKER", lambda *args, **kw: calls.append(kw))
    monkeypatch.setattr(runner.c, "verify_reference", lambda *args, **kw: kw)
    runner.worker(tmp_path / "shedding_scaled", tmp_path, "shedding_scaled")
    found = calls[0]["reference_validator"](None, None, "input", False)
    assert found["directory"] == tmp_path / "shedding_baseline"


@pytest.mark.parametrize(
    "abs_gap,rel_gap,passed",
    [(1e-8, 1e-8, False), (1e-11, 1e-8, True), (1e-8, 1e-11, True)],
)
def test_gap_gate_is_or_not_and(abs_gap, rel_gap, passed):
    settings = "\n".join(
        f" {k}: {v},"
        for k, v in dict(
            tol_gap_abs=1e-10,
            tol_gap_rel=1e-10,
            tol_feas=1e-10,
            reduced_tol_gap_abs=5e-5,
            reduced_tol_gap_rel=5e-5,
            reduced_tol_feas=1e-4,
        ).items()
    )
    record = {
        "native_info": {
            "effective_native_settings": settings,
            "native_info": dict(
                gap_abs=abs_gap, gap_rel=rel_gap, res_primal=1e-12, res_dual=1e-12
            ),
        }
    }
    log = " 33 +3.125e+00 +3.125e+00 4e-09 4e-12 1e-12 2e-14 1e-13 0.00e+00"
    result = a.stopping(record, log)
    assert result["full_visible_tests_pass"] == passed
    assert result["last_logged_step"] == 0
    assert result["underlying_pre_postprocess_error"].startswith("not retained")
