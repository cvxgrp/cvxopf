"""Forced fixture preservation and shared E3+ journal; no optimizer calls."""

from copy import deepcopy
import sys

import clarabel
import cyipopt
import numpy as np
import pytest

from experiments.case118_annual_hierarchy.streaming_schema import atomic_gzip_json, atomic_immutable_json
from experiments.case118_tracy_2021 import e3, e3_plus as plus, e3_prepared as prepared, run_e3 as runner
from experiments.case118_tracy_2021.stage_d import read
from tests.test_tracy_e3_runner import selection


@pytest.fixture(autouse=True)
def forbid_optimization(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("E3+ preparation tests must not optimize")
    monkeypatch.setattr(clarabel, "DefaultSolver", forbidden)
    monkeypatch.setattr(cyipopt, "Problem", forbidden)
    monkeypatch.setattr(prepared.starts.IPOPT, "solve_via_data", forbidden)


def setup(root, monkeypatch, *, ac_only=False):
    monkeypatch.setattr(plus, "verify_context", lambda expected: None)
    source = e3.make_study(selection())
    arms = [a for a in source["arms"] if a["start"] == 1165 and a["leg"] == "energy_neutral"]
    if ac_only:
        arms = [a for a in arms if a["formulation"] == "ac"]
    study = dict(source, cohort=plus.COHORT, schema=2, arms=[dict(a, id=i) for i, a in enumerate(arms)])
    current = dict(clean=True, commit="a" * 40,
                   thread_environment={k: "1" for k in runner.THREAD_KEYS})
    atomic_immutable_json(root / "binding.json", dict(context=current, study=study))
    runner.queue_protocol(root, deepcopy(plus.DEFAULT_PROTOCOL), "synthetic E3+ protocol")
    return study


def replay(root, *, partial=False):
    return runner.replay(root, lambda a, r, v: v["classification"] == "accepted", partial=partial)


def append(root, *, accepted=False, supervised=True):
    item = replay(root)["next"]
    arm, slot = item["arm"], item["slot"]
    parent = root / f"arm-{arm['id']:03d}"
    directory = parent / f"attempt-{len(list(parent.glob('attempt-*'))):03d}"
    request = plus.request_for(arm, slot, item["causal_source"], item["target_free_source"],
                              read(root / "control.json")["protocol"], 10.)
    atomic_immutable_json(directory / "request.json", request)
    if supervised:
        atomic_immutable_json(directory / "supervision.json", dict(classification="exited", returncode=0, wall_seconds=2.))
    if arm["formulation"] == "ac":
        atomic_immutable_json(directory / "start.json", dict(causal_start={}, assigned_start={}))
        atomic_gzip_json(directory / "x0.json.gz", dict(iteration=arm["id"]))
    for name in ("native", "preparation"):
        atomic_gzip_json(directory / f"{name}.json.gz", dict(iteration=arm["id"], data={}))
    classification = "accepted" if accepted else "rejected"
    atomic_gzip_json(directory / "result.json.gz", dict(iteration=arm["id"], classification=classification,
        attempt=str(directory.relative_to(root)), request=request,
        execution_context=read(root / "binding.json")["context"], native={}, preparation_evidence={}))
    atomic_immutable_json(directory / "completion.json", dict(classification=classification,
        artifacts={p.name: e3.digest(p) for p in directory.iterdir() if p.name != "supervision.json"}))
    return directory


def test_four_arm_journal_completion_and_independent_counts(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    assert runner.cohort_for(tmp_path) is plus
    for _ in range(4):
        append(tmp_path, accepted=True)
    status = replay(tmp_path)
    assert status["finished"] and status["accepted_arms"] == 4
    assert status["disposed_arms"] == 4 and status["unresolved_arms"] == 0
    assert status["launches"] == 4 and status["worker_seconds"] == 8
    assert all(status[key] == 4 for key in ("result_archives", "native_archives", "preparation_archives",
                                          "completion_manifests", "finalized_supervision"))


@pytest.mark.parametrize("accepted", [True, False])
def test_terminal_invocation_uses_four_arm_target(tmp_path, monkeypatch, accepted):
    setup(tmp_path, monkeypatch)
    while not replay(tmp_path)["finished"]:
        append(tmp_path, accepted=accepted)
    current = read(tmp_path / "binding.json")["context"]
    monkeypatch.setattr(plus, "context", lambda: current)
    monkeypatch.setattr(plus, "specification", lambda: read(tmp_path / "binding.json")["study"])
    monkeypatch.setattr(plus, "verified_inputs", lambda: None)
    monkeypatch.setattr(plus, "monitoring", lambda: {})
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 1.)
    monkeypatch.setattr(runner, "independent_auditor",
                        lambda *args: lambda a, r, v: v["classification"] == "accepted")
    monkeypatch.setattr(runner, "supervise", lambda *args: pytest.fail("terminal journal must not launch"))
    outcome = runner.run(tmp_path, current["commit"], plus=True, resume=True)
    expected = "complete" if accepted else "complete_with_unresolved_arms"
    assert outcome["classification"] == expected
    assert read(tmp_path / "invocations/invocation-000/finish.json")["outcome"] == expected


def test_plus_two_phase_recovery_has_no_external_sources(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, ac_only=True)
    for _ in range(6):
        append(tmp_path)
    assert replay(tmp_path)["next"]["slot"] == 10
    for _ in range(5):
        append(tmp_path)
    append(tmp_path, accepted=True)  # Hourly target-free is diagnostic only.
    status = replay(tmp_path)
    assert status["accepted_arms"] == 0 and status["next"]["slot"] == 16
    for key in ("causal_source", "target_free_source"):
        assert status["next"][key]["path"].startswith("arm-000/")
    append(tmp_path, accepted=True)
    assert replay(tmp_path)["accepted_arms"] == 1


def test_unsupervised_accepted_archive_is_not_a_result_or_seed(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    append(tmp_path, accepted=True, supervised=False)
    status = replay(tmp_path, partial=True)
    assert status["accepted_arms"] == 0 and status["next"] is None
    assert status["worker_seconds"] == 0 and status["finalized_supervision"] == 0
    assert status["result_archives"] == 1


def test_missing_native_archive_is_inconsistent(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    directory = append(tmp_path, accepted=True)
    (directory / "native.json.gz").unlink()
    with pytest.raises(FileNotFoundError):
        replay(tmp_path)


@pytest.mark.parametrize("value,passed", [(0., False), (9., False), (10. - 5e-5, True), (11., True), (float("nan"), False)])
def test_shedding_witness_is_a_required_check(monkeypatch, value, passed):
    monkeypatch.setattr(prepared, "audit", lambda *args: (
        dict(passed=True, costs={}, metrics={}, limits={}, residuals={}), None))
    monkeypatch.setattr(plus, "shortage", lambda kwargs: dict(minimum_ens_mwh=10., minimum_shed_mw=[1.] * 24))
    audit, _ = plus.audit(None, dict(energy_not_served=value), {}, {})
    assert audit["passed"] == passed
    assert audit["limits"]["forced_shedding_witness_mwh"] == 1e-4


def test_assess_and_replay_dispatch_use_forced_audit(monkeypatch):
    observed = {}
    def assessed(*args, **kwargs):
        observed.update(kwargs)
        return dict(passed=True)
    monkeypatch.setattr(prepared, "assess", assessed)
    assert plus.assess({}, "primary", {}, {})["passed"]
    assert observed["audit_fn"] is plus.audit
    monkeypatch.setattr(prepared, "independent_auditor", assessed)
    plus.independent_auditor(None, {})
    assert observed["cohort"] is plus


def test_output_isolated_from_retained_runs():
    for directory in (plus.HERE / "results/e3", plus.HERE / "results/e3_prepared", plus.FIXTURE_PATH.parent):
        for path in (directory, directory / "nested", directory.parent):
            with pytest.raises(ValueError, match="retained"):
                plus.validate_output(path)
    plus.validate_output(plus.HERE / "results/e3_plus")


def test_plus_preflight_does_not_create_artifacts(tmp_path, monkeypatch):
    current = dict(clean=True, commit="a" * 40, thread_environment={k: "1" for k in runner.THREAD_KEYS})
    monkeypatch.setattr(plus, "context", lambda: current)
    monkeypatch.setattr(plus, "specification", lambda: dict(cohort=plus.COHORT, arms=[]))
    monkeypatch.setattr(plus, "verified_inputs", lambda: None)
    monkeypatch.setattr(plus, "monitoring", lambda: {})
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 1.)
    output = tmp_path / "fresh"
    assert runner.run(output, "a" * 40, plus=True, preflight=True)["classification"] == "preflight_only"
    assert not output.exists()
    with pytest.raises(ValueError, match="one E3 cohort"):
        runner.run(output, "a" * 40, plus=True, prepared=True, preflight=True)


def test_cli_plus_protocol_and_exclusive_cohort_selection(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_e3", "--plus", "--show-protocol"])
    assert runner.main() == 0
    assert '"wall_seconds": 3600.0' in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["run_e3", "--plus", "--prepared", "--show-protocol"])
    with pytest.raises(SystemExit):
        runner.main()


def test_retained_fixture_reused_exactly_without_optimization():
    if not plus.FIXTURE_PATH.exists() or not (plus.HERE / "results/stage_c/analysis.json").exists():
        pytest.skip("owner retained Tracy inputs and historical fixture unavailable")
    study = plus.specification()
    assert len(study["arms"]) == 4 and study["cohort"] == plus.COHORT
    assert [a["formulation"] for a in study["arms"]] == list(e3.FORMULATIONS)
    assert study["fixture"]["stress"]["demand_multiplier"] == 7.5416905141946184
    inputs = plus.verified_inputs()
    for arm in study["arms"]:
        kwargs = plus.kwargs_for_arm(inputs, arm, study["storage_device_ids"],
                                     assembly="component_first" if arm["formulation"] == "ac" else "hourly")
        assert plus.shortage(kwargs)["minimum_ens_mwh"] == 439252.83068650117
        altered = dict(kwargs, df_load_p=kwargs["df_load_p"] * (1. + 1e-9))
        with pytest.raises(ValueError, match="physical fixture"):
            plus.validate_fixture(altered, "primary")
        if arm["formulation"] == "ac":
            free = plus.kwargs_for_arm(inputs, arm, study["storage_device_ids"], "target_free", assembly="hourly")
            assert all(s.terminal_soc is None and s.terminal_constraint is None for s in free["storage"])
    assert plus.DEFAULT_PROTOCOL["max_launches"] == 28
    assert plus.DEFAULT_PROTOCOL["wall_seconds"] == 3600.
    assert plus.DEFAULT_PROTOCOL["total_worker_seconds"] == 43200.
    np.testing.assert_array_equal(study["arms"][0]["initial_soc_mwh"], study["arms"][0]["terminal_soc_mwh"])
