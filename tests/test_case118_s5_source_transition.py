"""Cross-version provenance/lifecycle tests; no numerical execution."""

import hashlib
import json
import shutil

import pytest

from experiments.case118_annual_hierarchy import (
    run_s4b,
    run_s5,
    s4b_execution,
    s5_analysis,
)
from experiments.case118_annual_hierarchy import s5_source_transition as transition
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from tests.test_case118_s5_execution import _authority, _context, _summary, _supervision


@pytest.fixture
def retained(tmp_path, monkeypatch):
    root = tmp_path / "run"
    root.mkdir()
    old = _context()
    new = {**old, "git_commit": "d" * 40, "source_fingerprint": "e" * 64}
    old_authority = _authority()

    def write(name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        raw = path.read_bytes()
        return {
            "path": path.relative_to(tmp_path).as_posix(),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    authority_ref = write("old-authority.json", old_authority)
    wave = _supervision("supervisor_interrupted")
    wave.update(
        worker_results={},
        returncodes={s: 1 for s in wave["requested_shards"]},
        supervisor_exception="KeyboardInterrupt",
        supervisor_exception_kind="interruption",
    )
    for shard, log in wave["worker_logs"].items():
        ref = write(log["path"], {"log": shard})
        log["sha256"] = ref["sha256"]
    wave_ref = write("supervision-wave-000-000.json", wave)
    wave_entry = {
        "path": "supervision-wave-000-000.json",
        "sha256": wave_ref["sha256"],
        "wave_index": 0,
        "classification": "supervisor_interrupted",
    }
    progress = run_s5._progress_payload(
        context=old,
        authority=old_authority,
        classification="supervisor_interrupted",
        next_wave=0,
        completed_shards=(),
        supervision_records=(wave_entry,),
        reviewed_continuations=(),
        root_outcomes=(),
    )
    root_refs = [write("run-context.json", old), write("progress.json", progress)]
    root_refs.append(wave_ref)
    shards = []
    checkpoints = []
    for i in range(2):
        checkpoint = {
            "windows": [{"iteration": i * 682, "sha256": "f" * 64}],
            "completed_intervals": 1,
            "execution_source_fingerprint": old["source_fingerprint"],
            "shard_id": f"s4b-shard-{i:03d}",
        }
        checkpoints.append(checkpoint)
        ref = write(f"shard-{i:03d}/checkpoint.json", checkpoint)
        shards.append({"shard_id": checkpoint["shard_id"], "checkpoint": ref})
    template = {
        "schema_version": 1,
        "classification": "proposed",
        "launch_authorized": False,
        "review_status": "pending",
        "prior_execution": {
            "context": old,
            "numerical_authority": {**authority_ref, "payload": old_authority},
        },
        "continuation_execution": {},
        "trusted_stopping_point": {
            "root_evidence": root_refs,
            "shards": shards,
            "completed_intervals": 2,
        },
    }
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps(template))
    monkeypatch.setattr(transition, "ROOT", tmp_path)
    monkeypatch.setattr(transition, "TEMPLATE_PATH", template_path)
    monkeypatch.setattr(
        transition,
        "TEMPLATE_SHA256",
        hashlib.sha256(template_path.read_bytes()).hexdigest(),
    )
    contract = {
        **template,
        "classification": "reviewed_s5_source_version_continuation",
        "review_status": "reviewed",
        "launch_authorized": True,
        "continuation_execution": {
            "context": new,
            "required_clean_execution_commit": new["git_commit"],
            "required_execution_source_fingerprint": new["source_fingerprint"],
            "commit_must_match_exactly": True,
            "descendant_commits_implicitly_allowed": False,
        },
    }
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract))
    authority = {
        **_authority(new["git_commit"], new["source_fingerprint"]),
        "source_version_contract_sha256": object_sha256(contract),
    }
    monkeypatch.setattr(run_s4b, "_outer", lambda: object())
    audits = []
    monkeypatch.setattr(
        s4b_execution,
        "verify_shard_artifacts",
        lambda directory, **kw: audits.append(directory),
    )
    return root, contract_path, contract, old, new, authority, checkpoints, audits


def test_transition_is_atomic_idempotent_and_preserves_literal_checkpoints(retained):
    root, path, contract, old, new, authority, checkpoints, audits = retained
    before = {p: p.read_bytes() for p in root.glob("shard-*/checkpoint.json")}
    record = transition.publish_transition(root, path, new, authority)
    assert len(audits) == 2
    assert before == {p: p.read_bytes() for p in before}
    assert transition.publish_transition(root, path, new, authority) == record
    assert len(audits) == 2
    assert transition.load_transition(root) == record
    for i, checkpoint in enumerate(checkpoints):
        directory = root / f"shard-{i:03d}"
        assert transition.verify_checkpoint_segment(directory, checkpoint, context=new)
        appended = {
            **checkpoint,
            "windows": checkpoint["windows"] + [{"iteration": 999}],
            "execution_source_fingerprint": new["source_fingerprint"],
        }
        assert transition.verify_checkpoint_segment(directory, appended, context=new)
        appended["windows"][0] = {"iteration": 5}
        with pytest.raises(ValueError, match="trusted window prefix"):
            transition.verify_checkpoint_segment(directory, appended, context=new)


@pytest.mark.parametrize(
    "mutation",
    ["commit", "fingerprint", "checkpoint", "authority", "unreviewed", "state"],
)
def test_transition_rejects_mismatch_before_publication(retained, mutation):
    root, path, contract, old, new, authority, checkpoints, audits = retained
    current = dict(new)
    if mutation == "commit":
        current["git_commit"] = "f" * 40
    if mutation == "fingerprint":
        current["source_fingerprint"] = "f" * 64
    if mutation == "checkpoint":
        (root / "shard-000/checkpoint.json").write_text("{}")
    if mutation == "authority":
        authority = {**authority, "maximum_concurrency": 3}
    if mutation == "unreviewed":
        contract["launch_authorized"] = False
        path.write_text(json.dumps(contract))
    if mutation == "state":
        contract["prior_execution"]["context"]["git_clean"] = False
        path.write_text(json.dumps(contract))
    with pytest.raises(ValueError):
        transition.publish_transition(root, path, current, authority)
    assert not (root / transition.RECORD_NAME).exists()


def test_publication_failure_keeps_old_boundary_retryable(retained, monkeypatch):
    root, path, contract, old, new, authority, checkpoints, audits = retained
    write = transition.atomic_immutable_json
    monkeypatch.setattr(
        transition,
        "atomic_immutable_json",
        lambda *args: (_ for _ in ()).throw(OSError("publication failed")),
    )
    before = (root / "progress.json").read_bytes()
    with pytest.raises(OSError, match="publication failed"):
        transition.publish_transition(root, path, new, authority)
    assert (root / "progress.json").read_bytes() == before
    monkeypatch.setattr(transition, "atomic_immutable_json", write)
    assert transition.publish_transition(root, path, new, authority)


def test_changed_successor_or_old_prefix_fingerprint_is_rejected(retained):
    root, path, contract, old, new, authority, checkpoints, audits = retained
    transition.publish_transition(root, path, new, authority)
    with pytest.raises(ValueError, match="worker context"):
        transition.verify_checkpoint_segment(
            root / "shard-000", checkpoints[0], context=old
        )
    altered = {
        **checkpoints[0],
        "execution_source_fingerprint": new["source_fingerprint"],
    }
    with pytest.raises(ValueError, match="stopping checkpoint"):
        transition.verify_checkpoint_segment(root / "shard-000", altered, context=new)
    altered = {**checkpoints[0], "windows": checkpoints[0]["windows"] + [{}]}
    with pytest.raises(ValueError, match="successor source"):
        transition.verify_checkpoint_segment(root / "shard-000", altered, context=new)


def test_root_publishes_transition_before_worker_and_analyzer_retains_both_segments(
    retained, monkeypatch
):
    root, path, contract, old, new, authority, checkpoints, audits = retained
    monkeypatch.setattr(run_s5, "execution_context", lambda: new)
    monkeypatch.setattr(run_s5, "_outer", lambda: object())
    authority_path = root.parent / "new-authority.json"
    authority_path.write_text(json.dumps(authority))
    started = []

    def supervisor(shard_ids, **kwargs):
        assert transition.load_transition(root) is not None
        assert (root / "reviewed-continuation-000.json").is_file()
        started.extend(shard_ids)
        wave = json.loads((root / "supervision-wave-000-000.json").read_text())
        wave.update(execution_context=new, authority=authority)
        target = root / "supervision-wave-000-001.json"
        target.write_text(json.dumps(wave))
        return {
            **wave,
            "record_path": target.name,
            "record_sha256": run_s5.sha256_path(target),
        }

    result = run_s5.run_annual(
        authority_path=authority_path,
        output_root=root,
        reviewed_continue=True,
        source_transition_path=path,
        supervisor=supervisor,
    )
    assert result["classification"] == "partial"
    assert len(started) == 2
    continuation = json.loads((root / "reviewed-continuation-000.json").read_text())
    assert continuation["execution_context"] == new
    assert continuation["reviewed_source"]["execution_context"] == old
    monkeypatch.setattr(s5_analysis, "_outer", lambda: object())
    monkeypatch.setattr(s5_analysis, "verify_shard_artifacts", lambda *a, **kw: None)
    analyzed = s5_analysis.analyze_s5(root, authority_path=authority_path)
    assert analyzed["execution_context"] == new
    assert analyzed["initial_execution_context"] == old
    assert analyzed["source_version_transition"]["contract"] == contract
    assert analyzed["resource_summary"]["total_supervisor_critical_path_seconds"] == 4.0
    assert analyzed["accepted_for_s6"] is False
    corrupt = json.loads((root / "supervision-wave-000-001.json").read_text())
    corrupt.update(execution_context=old, authority=_authority())
    corrupt["elapsed_critical_path_seconds"] = 3.0
    assert not transition.historical_provenance_matches(
        corrupt, transition.load_transition(root), new, authority, output_root=root
    )


def test_shard_worker_checks_transition_before_any_solve(retained, monkeypatch):
    root, path, contract, old, new, authority, checkpoints, audits = retained
    transition.publish_transition(root, path, new, authority)
    monkeypatch.setattr(
        run_s4b, "_scope_context", lambda scope: {**new, "source_fingerprint": "f" * 64}
    )
    monkeypatch.setattr(run_s4b, "_scope_authority", lambda *a, **kw: authority)
    with pytest.raises(ValueError, match="reviewed source transition"):
        run_s4b._run_shard_worker_body(
            root / "shard-000",
            shard_id="s4b-shard-000",
            authority_path=path,
            reviewed_resume=True,
            execution_mode="annual",
            execution_scope="annual",
        )


def test_tracked_proposal_remains_nonexecuting():
    with pytest.raises(ValueError, match="not reviewed and authorized"):
        transition.validate_contract(json.loads(transition.TEMPLATE_PATH.read_text()))


def test_child_refuses_successor_authority_without_published_transition(
    retained, monkeypatch
):
    root, path, contract, old, new, authority, checkpoints, audits = retained
    monkeypatch.setattr(run_s4b, "_scope_context", lambda scope: new)
    monkeypatch.setattr(run_s4b, "_scope_authority", lambda *a, **kw: authority)
    monkeypatch.setattr(
        run_s4b, "_outer", lambda: pytest.fail("must reject before model load")
    )
    with pytest.raises(ValueError, match="lacks its reviewed source transition"):
        run_s4b.execute_one_window_child(
            root / "shard-000",
            shard_id="s4b-shard-000",
            iteration=1,
            primary_timeout_seconds=None,
            authority_path=path,
            expected_commit=new["git_commit"],
            expected_source_fingerprint=new["source_fingerprint"],
            execution_scope="annual",
        )


def test_no_new_transition_record_when_full_prefix_audit_fails(retained, monkeypatch):
    root, path, contract, old, new, authority, checkpoints, audits = retained
    monkeypatch.setattr(
        s4b_execution,
        "verify_shard_artifacts",
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("state chain corrupt")),
    )
    with pytest.raises(ValueError, match="state chain corrupt"):
        transition.publish_transition(root, path, new, authority)
    assert not (root / transition.RECORD_NAME).exists()


@pytest.mark.parametrize("restored", [False, True])
def test_initial_pending_transition_can_be_analyzed_before_worker_start(
    retained, monkeypatch, restored
):
    root, path, contract, old, new, authority, checkpoints, audits = retained
    transition.publish_transition(root, path, new, authority)
    authority_path = root.parent / "new-authority.json"
    authority_path.write_text(json.dumps(authority))
    monkeypatch.setattr(s5_analysis, "_outer", lambda: object())
    monkeypatch.setattr(s5_analysis, "verify_shard_artifacts", lambda *a, **kw: None)
    if restored:
        original = root
        root = original.parent / "restored"
        shutil.copytree(original, root)
        original.rename(original.parent / "unavailable-original")
        assert not original.exists()
        assert transition.load_transition(root) is not None
    result = s5_analysis.analyze_s5(root, authority_path=authority_path)
    assert result["execution_complete"] is False
    assert result["resource_summary"]["total_supervisor_critical_path_seconds"] == 2.0
    assert result["initial_execution_context"] == old


@pytest.mark.parametrize("second_restart", [False, True])
def test_complete_synthetic_transition_merges_and_retains_original_wall_time(
    retained, monkeypatch, second_restart
):
    root, path, contract, old, new, authority, checkpoints, audits = retained
    monkeypatch.setattr(run_s5, "execution_context", lambda: new)
    monkeypatch.setattr(run_s5, "_outer", lambda: object())
    monkeypatch.setattr(s5_analysis, "_outer", lambda: object())
    monkeypatch.setattr(s5_analysis, "verify_shard_artifacts", lambda *a, **kw: None)
    authority_path = root.parent / "new-authority.json"
    authority_path.write_text(json.dumps(authority))

    def summary(shard_id):
        value = _summary(shard_id)
        value.pop("summary_sha256")
        value["execution_source_fingerprint"] = new["source_fingerprint"]
        return {**value, "summary_sha256": object_sha256(value)}

    monkeypatch.setattr(
        run_s5, "audit_shard", lambda _d, **kw: summary(kw["shard"]["shard_id"])
    )
    monkeypatch.setattr(
        s5_analysis, "audit_shard", lambda _d, **kw: summary(kw["shard"]["shard_id"])
    )

    requested_waves = []

    def supervisor(shard_ids, **kwargs):
        requested_waves.append(tuple(shard_ids))
        # A real root interruption after wave zero has completed leaves its
        # successor supervision alongside the frozen original wave record.
        if second_restart and len(requested_waves) == 2:
            raise KeyboardInterrupt("stop after successor shard completion")
        wave = _supervision(shard_ids=shard_ids)
        wave.update(execution_context=new, authority=authority)
        for shard_id in shard_ids:
            directory = root / f"shard-{int(shard_id[-3:]):03d}"
            directory.mkdir(exist_ok=True)
            worker = {
                **summary(shard_id),
                "execution_context": new,
                "execution_mode": "annual",
                "worker_pid": wave["worker_root_pids"][shard_id],
            }
            wave["worker_results"][shard_id] = worker
            (directory / "shard-result.json").write_text(json.dumps(worker))
            log_path = root / f"new-{shard_id}.log"
            log_path.write_text("synthetic completed worker")
            wave["worker_logs"][shard_id] = {
                "path": log_path.name,
                "sha256": run_s5.sha256_path(log_path),
            }
        index = wave["wave_index"]
        target = (
            root / f"supervision-wave-{index:03d}-{1 if index == 0 else 0:03d}.json"
        )
        target.write_text(json.dumps(wave))
        return {
            **wave,
            "record_path": target.name,
            "record_sha256": run_s5.sha256_path(target),
        }

    kwargs = dict(
        authority_path=authority_path,
        output_root=root,
        reviewed_continue=True,
        supervisor=supervisor,
    )
    if second_restart:
        with pytest.raises(KeyboardInterrupt, match="successor shard completion"):
            run_s5.run_annual(**kwargs, source_transition_path=path)
        assert json.loads((root / "progress.json").read_text())["next_wave"] == 1
        successor = root / "supervision-wave-000-001.json"
        hidden = root / "withheld-successor.json"
        successor.rename(hidden)
        try:
            with pytest.raises(ValueError, match="no bound zero-exit supervision"):
                run_s5._validate_completed_prefix(root, 1, new, authority)
        finally:
            hidden.rename(successor)
        run_s5._validate_completed_prefix(root, 1, new, authority)
        result = run_s5.run_annual(**kwargs)
        assert requested_waves.count(run_s5.ANNUAL_WAVES[0]) == 1
        assert (root / "reviewed-continuation-001.json").is_file()
    else:
        result = run_s5.run_annual(**kwargs, source_transition_path=path)
    assert result["classification"] == "accepted"
    analyzed = s5_analysis.analyze_s5(root, authority_path=authority_path)
    assert analyzed["accepted_for_s6"] is True
    assert (
        analyzed["resource_summary"]["total_supervisor_critical_path_seconds"] == 14.0
    )
    assert analyzed["initial_execution_context"] == old
    assert len(analyzed["wave_lifecycle"]) == 7
    assert json.loads((root / "run-context.json").read_text()) == old
    if second_restart:
        restored = root.parent / "restored-complete"
        shutil.copytree(root, restored)
        root.rename(root.parent / "unavailable-original")
        assert not root.exists()
        restored_analysis = s5_analysis.analyze_s5(
            restored, authority_path=authority_path
        )
        assert restored_analysis["accepted_for_s6"] is True
        assert restored_analysis["resource_summary"] == analyzed["resource_summary"]
        assert restored_analysis["wave_lifecycle"] == analyzed["wave_lifecycle"]
        assert restored_analysis["initial_execution_context"] == old


def test_nested_transition_exposes_each_explicit_execution_segment():
    first_context = {"git_commit": "a" * 40, "source_fingerprint": "b" * 64}
    second_context = {"git_commit": "c" * 40, "source_fingerprint": "d" * 64}
    third_context = {"git_commit": "e" * 40, "source_fingerprint": "f" * 64}
    first_authority = {"execution_commit": first_context["git_commit"]}
    second_authority = {"execution_commit": second_context["git_commit"]}
    third_authority = {"execution_commit": third_context["git_commit"]}
    base = {
        "contract": {
            "prior_execution": {"context": first_context},
            "continuation_execution": {"context": second_context},
        },
        "prior_authority": first_authority,
        "new_authority": second_authority,
    }
    latest = {
        "contract": {
            "prior_execution_context": second_context,
            "continuation_execution": {"context": third_context},
        },
        "prior_authority": second_authority,
        "new_authority": third_authority,
        "predecessor_transition": base,
    }
    assert transition.transition_context_authority_pairs(latest) == (
        (third_context, third_authority),
        (second_context, second_authority),
        (first_context, first_authority),
    )
