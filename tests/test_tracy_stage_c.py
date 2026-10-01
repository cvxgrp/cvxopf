"""Annual runner seams, without canonicalization or numerical execution."""

from dataclasses import asdict
import gzip
import json
from types import SimpleNamespace

import numpy as np
import pytest

from experiments.case118_tracy_2021 import run_stage_b as shared
from experiments.case118_tracy_2021 import run_stage_c as annual
from experiments.case118_tracy_2021.prepare import SOURCE
from experiments.case118_tracy_2021.stage_b import (
    Arm,
    inputs_for_arm,
    study_spec,
    verified_inputs,
)
from tests.test_tracy_stage_b import fixture


def test_annual_spec_exact_pair_and_unchanged_stage_b():
    spec = annual.study_spec()
    assert spec["arms"] == [
        asdict(Arm("Full year", 0, 8760, 1 / 3, 0.01, f))
        for f in ("singlenode_dc", "lossy_dc")
    ]
    assert spec["annual_execution"] and not spec["ac_execution"]
    assert spec["limits"] == dict(
        wall_seconds=14400.0, rss_mib=16384.0, poll_seconds=1.0
    )
    assert spec["solver_options"] == study_spec()["solver_options"]
    assert spec["tolerances"] == study_spec()["tolerances"]
    assert len(study_spec()["arms"]) == 72
    assert not study_spec()["annual_execution"]
    assert study_spec()["limits"]["wall_seconds"] == 1800


def test_annual_owner_inputs_match_full_year_and_devices():
    if not SOURCE.exists():
        pytest.skip("owner Tracy CSV unavailable")
    p = verified_inputs()
    pair = [inputs_for_arm(p, Arm(**a)) for a in annual.study_spec()["arms"]]
    for k in pair:
        assert k["T"] == 8760 and k["temporal_assembly"] == "vectorized"
        assert len(k["generators"]) == 54 and len(k["storage"]) == 27
        assert len(k["loads"]) == 99 and len(k["nondispatchable"]) == 119
        assert all(
            s.initial_soc == s.terminal_soc == s.capacity / 2 for s in k["storage"]
        )
        assert all(s.aging_weight == 0.01 for s in k["storage"])
        assert all(d.shedding_cost_per_mwh == 20763.594 for d in k["loads"])
        for g in k["generators"]:
            if g.p_max_mw:
                assert g.cost_coeffs[2] * g.p_max_mw == pytest.approx(
                    g.cost_coeffs[1] / 3
                )
    for key in ("df_load_p", "df_load_q", "df_nd"):
        np.testing.assert_array_equal(pair[0][key], pair[1][key])
    for key in ("generators", "storage", "loads", "nondispatchable"):
        assert pair[0][key] == pair[1][key]


def test_annual_budget_acknowledgement_precedes_output(tmp_path):
    with pytest.raises(ValueError, match="approval"):
        annual.run(tmp_path / "annual", "not-a-commit")
    assert not (tmp_path / "annual").exists()


@pytest.mark.parametrize("clean,commit", [(False, "abc"), (True, "wrong")])
def test_annual_dirty_or_wrong_commit_precedes_output(
    tmp_path, monkeypatch, clean, commit
):
    monkeypatch.setattr(
        shared, "context", lambda protocol: dict(clean=clean, commit="abc")
    )
    with pytest.raises(ValueError, match="clean tree"):
        annual.run(tmp_path / "annual", commit, approve_resource_budget=True)
    assert not (tmp_path / "annual").exists()


@pytest.mark.parametrize("fail_first", [False, True])
def test_annual_parent_order_limits_and_stop(tmp_path, monkeypatch, fail_first):
    ctx = dict(clean=True, commit="abc")
    monkeypatch.setattr(shared, "context", lambda protocol: ctx)
    monkeypatch.setattr(shared, "verified_inputs", lambda: None)
    monkeypatch.setattr(shared, "_child_rss_mib", lambda pid: 10.0)
    events = []

    def supervise(command, directory, limits):
        events.append(("worker", directory.name))
        assert command[2] == annual.STUDY.module
        assert limits == annual.LIMITS
        return dict(classification="exited", returncode=0)

    def reconstruct(directory, number, manifest, p):
        events.append(("audit", f"arm-{number:03d}"))
        assert manifest["study"] == annual.study_spec()
        if fail_first:
            raise ValueError("rejected")
        return dict(number=number)

    monkeypatch.setattr(shared, "supervise", supervise)
    monkeypatch.setattr(shared, "reconstruct_arm", reconstruct)
    outcome = annual.run(tmp_path / "annual", "abc", approve_resource_budget=True)
    assert outcome["annual_execution"] is True
    assert outcome["classification"] == ("stopped" if fail_first else "complete")
    assert events == [
        (event, f"arm-{n:03d}")
        for n in range(1 if fail_first else 2)
        for event in ("worker", "audit")
    ]
    assert len(outcome["reconstruction_seconds"]) == (1 if fail_first else 2)


@pytest.mark.parametrize("failure", [False, True])
def test_annual_worker_and_analyzer_use_real_shared_audit(
    tmp_path, monkeypatch, failure
):
    ctx = dict(
        clean=True,
        commit="synthetic",
        stage_a_manifest_sha256="input",
        source_sha256="csv",
    )
    shared.atomic_json(
        tmp_path / "binding.json", dict(context=ctx, study=annual.study_spec())
    )
    monkeypatch.setattr(shared, "context", lambda protocol: ctx)
    monkeypatch.setattr(shared, "verified_inputs", lambda: None)
    for n, arm in enumerate(annual.study_spec()["arms"]):
        kwargs, result, named = fixture(arm["formulation"])
        directory = tmp_path / f"arm-{n:03d}"
        directory.mkdir()

        def solve(**options):
            assert options["max_iter"] == 5000
            assert not options["warm_start"]
            if failure:
                raise RuntimeError("synthetic failure")

        build = SimpleNamespace(
            solve=solve,
            expressions={k: SimpleNamespace(value=v) for k, v in named.items()},
            prob=SimpleNamespace(
                _solver_cache={},
                compilation_time=0.1,
                solver_stats=SimpleNamespace(
                    solver_name="CLARABEL", num_iters=3, solve_time=0.2, setup_time=None
                ),
            ),
        )
        monkeypatch.setattr(
            shared, "inputs_for_arm", lambda p, a: fixture(a.formulation)[0]
        )
        monkeypatch.setattr(shared, "build_opf_multistep", lambda **kw: build)
        monkeypatch.setattr(shared, "extract_results", lambda b: result)
        assert shared.worker(tmp_path, n, study=annual.STUDY) == int(failure)
        with gzip.open(directory / "result.json.gz", "rt") as stream:
            payload = json.load(stream)
        assert payload["arm"] == arm
        if not failure:
            assert np.asarray(payload["boundary_soc_mwh"]).shape == (3, 1)
            assert payload["identities"]["storage"] == ["battery"]
        else:
            assert "synthetic failure" in payload["exception"]
        shared.atomic_json(
            directory / "supervision.json",
            dict(
                classification="exited",
                returncode=int(failure),
                samples=1,
                wall_seconds=100.0,
                peak_sampled_rss_mib=100.0,
            ),
        )
    assert not annual.analyze(tmp_path)["complete"]  # Not yet finalized.
    shared.atomic_json(tmp_path / "study-result.json", dict(classification="complete"))
    analysis = annual.analyze(tmp_path)
    assert analysis["complete"] is (not failure)
    assert analysis["annual_execution"]
    assert len(analysis["accepted"]) == (0 if failure else 2)


def test_stage_c_protocol_identity_is_separate(tmp_path, monkeypatch):
    # Context hashes the source, but this test only needs protocol identity;
    # never depend on the Git-ignored owner CSV for this clone-ready check.
    source = tmp_path / "synthetic-source.csv"
    source.write_text("hour,load_mw\n0,1\n", encoding="utf-8")
    monkeypatch.setattr(shared, "SOURCE", source)

    annual_context = annual.STUDY.context()
    shared_context = shared.context()
    assert (
        annual_context["source_sha256"]
        == shared_context["source_sha256"]
        == shared.digest(source)
    )
    assert annual_context["protocol_sha256"] != shared_context["protocol_sha256"]


def test_wrong_stage_binding_rejected_before_model(tmp_path, monkeypatch):
    shared.atomic_json(tmp_path / "binding.json", dict(context={}, study=study_spec()))
    (tmp_path / "arm-000").mkdir()

    def unexpected_build(**kwargs):
        pytest.fail("wrong stage must not construct a model")

    monkeypatch.setattr(shared, "build_opf_multistep", unexpected_build)
    assert shared.worker(tmp_path, 0, study=annual.STUDY) == 1
    with gzip.open(tmp_path / "arm-000/result.json.gz", "rt") as stream:
        payload = json.load(stream)
    assert "specification differs" in payload["exception"]
    with pytest.raises(ValueError, match="specification differs"):
        annual.analyze(tmp_path)
