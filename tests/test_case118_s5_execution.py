from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Mapping, cast

import pytest

from experiments.case118_annual_hierarchy import (
    run_s4b,
    run_s5,
    s4b_execution,
    s5_analysis,
)
from experiments.case118_annual_hierarchy import s5_execution as s5
from experiments.case118_annual_hierarchy.s4b_manifest import (
    EXPECTED_MANIFEST_SHA256,
    object_sha256,
)


def _authority(commit: str = "a" * 40, source: str = "b" * 64) -> dict[str, object]:
    return {
        "schema_version": 1,
        "classification": "reviewed_s5_numerical_execution_authorized",
        "execution_scope": "annual_8760h_hierarchical_ac",
        "annual_execution_authorized": True,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "annual_registry_sha256": s5.annual_registry()["registry_sha256"],
        "manifest_use_authority_sha256": s5.MANIFEST_USE_AUTHORITY_SHA256,
        "s4b_results_sha256": s5.S4B_RESULTS_SHA256,
        "s4b_analysis_sha256": s5.S4B_ANALYSIS_SHA256,
        "maximum_concurrency": 2,
        "per_worker_current_rss_mib": 16_384.0,
        "aggregate_current_rss_mib": 24_576.0,
        "execution_commit": commit,
        "source_fingerprint": source,
    }


def _context() -> dict[str, object]:
    return {
        "git_commit": "a" * 40,
        "git_clean": True,
        "source_fingerprint": "b" * 64,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "annual_registry_sha256": s5.annual_registry()["registry_sha256"],
    }


def _summary(shard_id: str) -> dict[str, object]:
    """Production summary schema, with deterministic synthetic audited values."""
    _, shard = s4b_execution.shard_entry(shard_id)
    interval = cast(Mapping[str, int], shard["interval"])
    storage = cast(Mapping[str, object], shard["storage"])
    count = interval["stop"] - interval["start"]
    value: dict[str, object] = {
        "schema_version": 1,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "execution_registry_sha256": s5.annual_registry()["registry_sha256"],
        "shard_id": shard_id,
        "interval": interval,
        "initial_state": storage["initial_state"],
        "terminal_state": storage["terminal_state"],
        "classification": "accepted",
        "execution_complete": True,
        "completed_intervals": count,
        "coverage_fraction": 1.0,
        "checkpoint_sha256": "1" * 64,
        "window_chain_sha256": "2" * 64,
        "execution_source_fingerprint": _context()["source_fingerprint"],
        "outer_plan_sha256": "3" * 64,
        "timeout_count": 0,
        "recovery_window_count": 0,
        "shifted_primary_opportunities": count - 1,
        "shifted_primary_successes": count - 1,
        "shifted_primary_success_fraction": 1.0,
        "all_independent_audits_agree": True,
        "timing": {"total_window_path_seconds": float(count)},
    }
    for name in (
        "storage_throughput_mwh",
        "cumulative_absolute_signpost_deviation_mwh",
        "terminal_deviation_mwh",
        "generation_cost",
        "storage_cycling_cost",
        "active_losses_mwh",
        "renewable_curtailment_mwh",
        "maximum_voltage_violation_pu",
        "maximum_thermal_violation_mva",
    ):
        value[name] = 0.0
    return {**value, "summary_sha256": object_sha256(value)}


def _worker(shard_id: str) -> dict[str, object]:
    return {
        **_summary(shard_id),
        "execution_context": _context(),
        "execution_mode": "annual",
        "worker_pid": 123,
        "window_supervision": [],
        "completed_child_cpu_seconds": 1.0,
    }


def test_s5_manifest_authority_is_valid_but_numerical_authority_is_absent() -> None:
    assert s5.load_manifest_use_authority()["annual_manifest_use_authorized"] is True
    assert (
        s5.load_manifest_use_authority()["s5_numerical_execution_authorized"] is False
    )
    assert not s5.DEFAULT_NUMERICAL_AUTHORITY_PATH.exists()
    with pytest.raises(ValueError, match="numerical execution remains unauthorized"):
        s5.load_numerical_authority(
            s5.DEFAULT_NUMERICAL_AUTHORITY_PATH,
            expected_execution_commit="a" * 40,
            expected_source_fingerprint="b" * 64,
        )


def test_numerical_authority_is_exact(tmp_path: Path) -> None:
    path = tmp_path / "authority.json"
    payload = _authority()
    path.write_text(json.dumps(payload))
    assert (
        s5.load_numerical_authority(
            path,
            expected_execution_commit="a" * 40,
            expected_source_fingerprint="b" * 64,
        )
        == payload
    )

    payload["maximum_concurrency"] = 1
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="does not match"):
        s5.load_numerical_authority(
            path,
            expected_execution_commit="a" * 40,
            expected_source_fingerprint="b" * 64,
        )


def test_annual_registry_freezes_six_adjacent_two_worker_waves() -> None:
    registry = s5.annual_registry()
    assert registry["waves"] == [list(item) for item in s5.ANNUAL_WAVES]
    assert registry["shard_ids"] == list(s5.ANNUAL_SHARD_IDS)
    assert registry["horizon_steps"] == 8_760
    assert registry["registry_sha256"] == object_sha256(
        {key: value for key, value in registry.items() if key != "registry_sha256"}
    )
    assert s5.wave_index_for_request(s5.ANNUAL_WAVES[2]) == 2
    assert s5.wave_index_for_request((s5.ANNUAL_WAVES[2][1],)) == 2
    with pytest.raises(ValueError, match="crosses"):
        s5.wave_index_for_request((s5.ANNUAL_WAVES[0][0], s5.ANNUAL_WAVES[1][0]))


def test_run_s4b_annual_scope_uses_s5_authority_and_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker: dict[str, object] = {}
    monkeypatch.setattr(s5, "execution_context", lambda: _context())
    monkeypatch.setattr(
        s5,
        "load_numerical_authority",
        lambda path, **kwargs: marker.update(path=path, **kwargs) or _authority(),
    )
    context = run_s4b._scope_context(run_s4b.ANNUAL_SCOPE)
    authority = run_s4b._scope_authority(
        run_s4b.ANNUAL_SCOPE,
        tmp_path / "authority.json",
        expected_commit="a" * 40,
        expected_source_fingerprint="b" * 64,
    )
    _, shard = run_s4b._scope_shard_entry(
        run_s4b.ANNUAL_SCOPE, "s4b-shard-011", object()
    )
    assert context == _context()
    assert authority == _authority()
    assert marker["expected_execution_commit"] == "a" * 40
    assert cast(Mapping[str, object], shard["interval"])["stop"] == 8_760


def test_annual_checkpoint_uses_annual_registry_and_mode() -> None:
    _, shard = s4b_execution.shard_entry("s4b-shard-000")
    initial = cast(
        Mapping[str, object],
        cast(Mapping[str, object], shard["storage"])["initial_state"],
    )["soc_mwh"]
    registry_sha256 = str(s5.annual_registry()["registry_sha256"])
    checkpoint = s4b_execution.shard_checkpoint_payload(
        shard=shard,
        execution_source_fingerprint="a" * 64,
        outer_plan_sha256="b" * 64,
        execution_mode="annual",
        realized_soc_mwh=cast(list[float], initial),
        preceding_controlling_attempt_id=None,
        windows=(),
        execution_registry_sha256=registry_sha256,
        allowed_execution_modes=("annual",),
    )
    assert checkpoint["execution_registry_sha256"] == registry_sha256
    assert "qualification_registry_sha256" not in checkpoint
    assert (
        s4b_execution.validate_shard_checkpoint(
            checkpoint,
            shard=shard,
            expected_execution_registry_sha256=registry_sha256,
            allowed_execution_modes=("annual",),
        )
        == checkpoint
    )


def _resource_sample(
    shard_ids: tuple[str, ...], rss: float = 100.0
) -> dict[str, object]:
    roots = {item: 100 + index for index, item in enumerate(shard_ids)}
    per_worker = {
        str(pid): {
            "process_identities": [[pid, f"time-{pid}"]],
            "rss_mib": rss,
            "cpu_seconds": float(index + 1),
        }
        for index, pid in enumerate(roots.values())
    }
    return {
        "elapsed_seconds": 1.0,
        "active_shards": list(shard_ids),
        "supervisor_current_rss_mib": 20.0,
        "supervisor_cpu_seconds": 0.2,
        "per_worker": per_worker,
        "aggregate_process_identities": [
            [pid, f"time-{pid}"] for pid in roots.values()
        ],
        "aggregate_rss_mib": rss * len(shard_ids),
        "aggregate_cpu_seconds": sum(
            cast(float, item["cpu_seconds"]) for item in per_worker.values()
        ),
    }


def _supervision(
    classification: str = "accepted",
    *,
    shard_ids: tuple[str, ...] = s5.ANNUAL_WAVES[0],
    rss: float = 100.0,
) -> dict[str, object]:
    roots = {item: 100 + index for index, item in enumerate(shard_ids)}
    sample = _resource_sample(shard_ids, rss=rss)
    return {
        "schema_version": 1,
        "classification": classification,
        "wave_index": s5.wave_index_for_request(shard_ids),
        "frozen_wave": list(s5.ANNUAL_WAVES[s5.wave_index_for_request(shard_ids)]),
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "authority": _authority(),
        "execution_context": _context(),
        "requested_shards": list(shard_ids),
        "requested_concurrency": len(shard_ids),
        "maximum_observed_concurrency": len(shard_ids),
        "returncodes": {item: 0 for item in shard_ids},
        "worker_root_pids": roots,
        "resource_triggers": [],
        "resource_samples": [sample],
        "peak_worker_rss_mib": {item: rss for item in shard_ids},
        "peak_aggregate_rss_mib": rss * len(shard_ids),
        "elapsed_critical_path_seconds": 2.0,
        "artifact_error": None,
        "supervisor_exception": None,
        "supervisor_exception_kind": None,
        "worker_results": {item: _worker(item) for item in shard_ids},
        "worker_logs": {
            item: {"path": f"{item}.log", "sha256": "c" * 64} for item in shard_ids
        },
    }


def test_analysis_reconstructs_accepted_and_resource_supervision() -> None:
    accepted = _supervision()
    assert s5_analysis.validate_supervision(accepted) == accepted

    limited = _supervision("resource_limit", rss=16_385.0)
    limited["resource_triggers"] = [
        {
            "kind": "per_worker_rss_limit",
            "shard_id": item,
            "rss_mib": 16_385.0,
        }
        for item in s5.ANNUAL_WAVES[0]
    ] + [{"kind": "aggregate_rss_limit", "rss_mib": 32_770.0}]
    limited["returncodes"] = {item: -15 for item in s5.ANNUAL_WAVES[0]}
    limited["worker_results"] = {}
    assert (
        s5_analysis.validate_supervision(limited)["classification"] == "resource_limit"
    )


def test_supervision_rejects_wrong_trigger_priority() -> None:
    record = _supervision("worker_process_failure", rss=13_000.0)
    record["resource_triggers"] = [{"kind": "aggregate_rss_limit", "rss_mib": 26_000.0}]
    record["returncodes"] = {item: -15 for item in s5.ANNUAL_WAVES[0]}
    record["worker_results"] = {}
    with pytest.raises(ValueError, match="does not reconstruct"):
        s5_analysis.validate_supervision(record)


def test_run_annual_uses_exact_wave_order_and_merges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(run_s5, "execution_context", lambda: _context())
    monkeypatch.setattr(
        run_s5, "load_numerical_authority", lambda *_a, **_k: _authority()
    )
    monkeypatch.setattr(run_s5, "_outer", lambda: object())
    monkeypatch.setattr(
        run_s5, "audit_shard", lambda _d, **kw: _summary(kw["shard"]["shard_id"])
    )
    calls: list[tuple[str, ...]] = []

    def supervisor(
        shard_ids: tuple[str, ...], **kwargs: object
    ) -> Mapping[str, object]:
        calls.append(tuple(shard_ids))
        output_root = cast(Path, kwargs["output_root"])
        for shard_id in shard_ids:
            directory = output_root / f"shard-{int(shard_id[-3:]):03d}"
            directory.mkdir(parents=True)
            (directory / "shard-result.json").write_text(json.dumps(_worker(shard_id)))
        wave_index = len(calls) - 1
        record = output_root / f"supervision-wave-{wave_index:03d}-000.json"
        record.write_text(
            json.dumps({"wave_index": wave_index, "classification": "accepted"})
        )
        return {
            "classification": "accepted",
            "record_path": record.name,
            "record_sha256": run_s5.sha256_path(record),
        }

    result = run_s5.run_annual(
        authority_path=tmp_path / "authority.json",
        output_root=tmp_path / "annual",
        supervisor=supervisor,
    )
    assert calls == list(s5.ANNUAL_WAVES)
    assert result["classification"] == "accepted"
    assert result["completed_shards"] == list(s5.ANNUAL_SHARD_IDS)
    assert json.loads((tmp_path / "annual/progress.json").read_text()) == result


def test_run_annual_stops_before_later_wave_after_abnormal_supervision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(run_s5, "execution_context", lambda: _context())
    monkeypatch.setattr(
        run_s5, "load_numerical_authority", lambda *_a, **_k: _authority()
    )
    monkeypatch.setattr(run_s5, "_outer", lambda: object())
    calls: list[tuple[str, ...]] = []

    def supervisor(
        shard_ids: tuple[str, ...], **kwargs: object
    ) -> Mapping[str, object]:
        calls.append(tuple(shard_ids))
        root = cast(Path, kwargs["output_root"])
        record = root / "supervision-wave-000-000.json"
        record.write_text(
            json.dumps({"wave_index": 0, "classification": "resource_limit"})
        )
        return {
            "classification": "resource_limit",
            "record_path": record.name,
            "record_sha256": run_s5.sha256_path(record),
        }

    result = run_s5.run_annual(
        authority_path=tmp_path / "authority.json",
        output_root=tmp_path / "annual",
        supervisor=supervisor,
    )
    assert calls == [s5.ANNUAL_WAVES[0]]
    assert result["classification"] == "partial"
    assert result["next_wave"] == 0
    assert not (tmp_path / "annual/run-result.json").exists()


def test_numerical_authority_failure_precedes_output_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(run_s5, "execution_context", lambda: _context())
    output = tmp_path / "annual"
    with pytest.raises(ValueError, match="numerical execution remains unauthorized"):
        run_s5.run_annual(
            authority_path=tmp_path / "missing-authority.json",
            output_root=output,
        )
    assert not output.exists()


def test_root_interruption_reconciles_published_supervision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(run_s5, "execution_context", lambda: _context())
    monkeypatch.setattr(
        run_s5, "load_numerical_authority", lambda *_a, **_k: _authority()
    )
    monkeypatch.setattr(run_s5, "_outer", lambda: object())
    output = tmp_path / "annual"

    def interrupted(
        shard_ids: tuple[str, ...], **kwargs: object
    ) -> Mapping[str, object]:
        record = _supervision("supervisor_interrupted")
        record["supervisor_exception"] = "KeyboardInterrupt"
        record["supervisor_exception_kind"] = "interruption"
        path = output / "supervision-wave-000-000.json"
        path.write_text(json.dumps(record))
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_s5.run_annual(
            authority_path=tmp_path / "authority.json",
            output_root=output,
            supervisor=interrupted,
        )
    progress = json.loads((output / "progress.json").read_text())
    assert progress["classification"] == "supervisor_interrupted"
    assert progress["supervision_records"] == [
        {
            "path": "supervision-wave-000-000.json",
            "sha256": run_s5.sha256_path(output / "supervision-wave-000-000.json"),
            "wave_index": 0,
            "classification": "supervisor_interrupted",
        }
    ]
    assert len(progress["root_outcomes"]) == 1
    root_outcome = output / progress["root_outcomes"][0]["path"]
    assert (
        json.loads(root_outcome.read_text())["classification"]
        == "supervisor_interrupted"
    )


def test_reviewed_continuation_accepts_stale_running_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(run_s5, "execution_context", lambda: _context())
    monkeypatch.setattr(
        run_s5, "load_numerical_authority", lambda *_a, **_k: _authority()
    )
    monkeypatch.setattr(run_s5, "_outer", lambda: object())
    monkeypatch.setattr(
        run_s5, "audit_shard", lambda _d, **kw: _summary(kw["shard"]["shard_id"])
    )
    output = tmp_path / "annual"
    output.mkdir()
    (output / "run-context.json").write_text(json.dumps(_context()))
    (output / "progress.json").write_text(
        json.dumps(
            run_s5._progress_payload(
                context=_context(),
                authority=_authority(),
                classification="running",
                next_wave=0,
                completed_shards=(),
                supervision_records=(),
                reviewed_continuations=(),
                root_outcomes=(),
            )
        )
    )

    def supervisor(
        shard_ids: tuple[str, ...], **kwargs: object
    ) -> Mapping[str, object]:
        root = cast(Path, kwargs["output_root"])
        for shard_id in shard_ids:
            directory = root / f"shard-{int(shard_id[-3:]):03d}"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "shard-result.json").write_text(json.dumps(_worker(shard_id)))
        wave_index = s5.wave_index_for_request(shard_ids)
        path = root / f"supervision-wave-{wave_index:03d}-000.json"
        path.write_text(
            json.dumps({"wave_index": wave_index, "classification": "accepted"})
        )
        return {
            "classification": "accepted",
            "record_path": path.name,
            "record_sha256": run_s5.sha256_path(path),
        }

    result = run_s5.run_annual(
        authority_path=tmp_path / "authority.json",
        output_root=output,
        reviewed_continue=True,
        supervisor=supervisor,
    )
    assert result["classification"] == "accepted"
    assert len(result["reviewed_continuations"]) == 1


def test_partial_analysis_cannot_be_promoted(tmp_path: Path) -> None:
    base = {
        "schema_version": 1,
        "classification": "partial",
        "execution_complete": False,
        "accepted_for_s6": False,
    }
    value = {**base, "analysis_sha256": object_sha256(base)}
    with pytest.raises(ValueError, match="cannot be promoted"):
        s5_analysis.promote_completed(tmp_path / "S5_RESULTS.json", value)
    assert not (tmp_path / "S5_RESULTS.json").exists()


def test_complete_analysis_reconstructs_all_shards_waves_and_merge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "annual"
    output.mkdir()
    workers: dict[str, dict[str, object]] = {}
    for shard_id in s5.ANNUAL_SHARD_IDS:
        worker = {
            "shard_id": shard_id,
            "classification": "accepted",
            "execution_complete": True,
            "all_independent_audits_agree": True,
            "execution_context": _context(),
            "execution_source_fingerprint": _context()["source_fingerprint"],
            "execution_mode": "annual",
            "checkpoint_sha256": "1" * 64,
            "window_chain_sha256": "2" * 64,
            "ok": True,
        }
        directory = output / f"shard-{int(shard_id[-3:]):03d}"
        directory.mkdir()
        (directory / "shard-result.json").write_text(json.dumps(worker))
        workers[shard_id] = worker
    supervision_registry: list[dict[str, object]] = []
    for wave_index, wave in enumerate(s5.ANNUAL_WAVES):
        record = _supervision(shard_ids=wave)
        record["wave_index"] = wave_index
        record["frozen_wave"] = list(wave)
        record["worker_results"] = {item: workers[item] for item in wave}
        logs: dict[str, object] = {}
        for shard_id in wave:
            path = output / f"{shard_id}.log"
            path.write_text("accepted\n")
            logs[shard_id] = {
                "path": path.name,
                "sha256": run_s5.sha256_path(path),
            }
        record["worker_logs"] = logs
        path = output / f"supervision-wave-{wave_index:03d}-000.json"
        path.write_text(json.dumps(record))
        supervision_registry.append(
            {
                "path": path.name,
                "sha256": run_s5.sha256_path(path),
                "wave_index": wave_index,
                "classification": "accepted",
            }
        )
    merged = {
        "classification": "accepted_annual_partition",
        "execution_complete": True,
        "all_independent_audits_agree": True,
    }
    (output / "merged-result.json").write_text(json.dumps(merged))
    progress = {
        "schema_version": 1,
        "classification": "accepted",
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "execution_context": _context(),
        "authority": _authority(),
        "next_wave": 6,
        "completed_shards": list(s5.ANNUAL_SHARD_IDS),
        "supervision_records": supervision_registry,
        "reviewed_continuations": [],
        "root_outcomes": [],
        "merged_result": {
            "path": "merged-result.json",
            "sha256": run_s5.sha256_path(output / "merged-result.json"),
        },
    }
    (output / "progress.json").write_text(json.dumps(progress))
    (output / "run-result.json").write_text(json.dumps(progress))
    (output / "run-context.json").write_text(json.dumps(_context()))
    monkeypatch.setattr(
        s5_analysis, "load_numerical_authority", lambda *_a, **_k: _authority()
    )
    monkeypatch.setattr(s5_analysis, "_outer", lambda: object())
    monkeypatch.setattr(
        s5_analysis,
        "audit_shard",
        lambda directory, **_kwargs: json.loads(
            (directory / "shard-result.json").read_text()
        ),
    )
    monkeypatch.setattr(
        s5_analysis, "verify_shard_artifacts", lambda *_a, **_k: ({}, ())
    )
    monkeypatch.setattr(
        s5_analysis, "merge_shard_summaries", lambda _values, **_kwargs: merged
    )
    monkeypatch.setattr(s5_analysis, "analysis_context", lambda: {"git_clean": True})

    result = s5_analysis.analyze_s5(output, authority_path=tmp_path / "authority.json")
    assert result["execution_complete"] is True
    assert result["accepted_for_s6"] is True
    assert result["completed_shards"] == list(s5.ANNUAL_SHARD_IDS)


def test_complete_promotion_is_immutable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = {
        "schema_version": 1,
        "classification": "accepted",
        "execution_complete": True,
        "accepted_for_s6": True,
    }
    result = {**base, "analysis_sha256": object_sha256(base)}
    monkeypatch.setattr(s5_analysis, "analyze_s5", lambda *_a, **_k: result)
    path = tmp_path / "S5_RESULTS.json"
    s5_analysis.promote_completed(path, result)
    assert json.loads(path.read_text()) == result
    changed_base = {**base, "extra": True}
    changed = {**changed_base, "analysis_sha256": object_sha256(changed_base)}
    with pytest.raises(FileExistsError):
        s5_analysis.promote_completed(path, changed)


def test_default_output_is_ignored_and_default_authority_remains_absent() -> None:
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(run_s5.DEFAULT_OUTPUT_ROOT)],
        cwd=run_s5.ROOT,
        check=False,
    )
    assert ignored.returncode == 0
    assert not s5.DEFAULT_NUMERICAL_AUTHORITY_PATH.exists()


@pytest.mark.parametrize("rss", [13_000.0, 17_000.0])
def test_over_limit_samples_require_resource_triggers(rss: float) -> None:
    with pytest.raises(ValueError, match="triggers do not reconstruct"):
        s5_analysis.validate_supervision(_supervision(rss=rss))


@pytest.mark.parametrize("invalid", ["missing", "rejected", "provenance"])
def test_continuation_audits_prior_wave_before_writing_or_launching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid: str
) -> None:
    monkeypatch.setattr(run_s5, "execution_context", _context)
    monkeypatch.setattr(
        run_s5, "load_numerical_authority", lambda *_a, **_k: _authority()
    )
    monkeypatch.setattr(run_s5, "_outer", lambda: object())
    monkeypatch.setattr(
        run_s5,
        "audit_shard",
        lambda _d, **kw: {
            **_summary(kw["shard"]["shard_id"]),
            **({"classification": "rejected"} if invalid == "rejected" else {}),
        },
    )
    output = tmp_path / "annual"
    output.mkdir()
    progress = run_s5._progress_payload(
        context=_context(),
        authority=_authority(),
        classification="partial",
        next_wave=1,
        completed_shards=s5.ANNUAL_WAVES[0],
        supervision_records=(),
        reviewed_continuations=(),
        root_outcomes=(),
    )
    progress_path = output / "progress.json"
    progress_path.write_text(json.dumps(progress))
    before = progress_path.read_bytes()
    if invalid != "missing":
        for shard_id in s5.ANNUAL_WAVES[0]:
            directory = output / f"shard-{int(shard_id[-3:]):03d}"
            directory.mkdir()
            worker = _worker(shard_id)
            if invalid == "provenance":
                worker["execution_context"] = {**_context(), "git_commit": "f" * 40}
            (directory / "shard-result.json").write_text(json.dumps(worker))
    with pytest.raises(ValueError, match="prefix|audit or provenance"):
        run_s5.run_annual(
            output_root=output,
            reviewed_continue=True,
            supervisor=lambda *_a, **_k: pytest.fail("launched beyond invalid prefix"),
        )
    assert progress_path.read_bytes() == before
    assert not list(output.glob("reviewed-continuation-*.json"))


@pytest.mark.parametrize("outcome", ["unpublished", "nonzero_exit", "published"])
def test_continuation_requires_completed_peer_supervision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str
) -> None:
    monkeypatch.setattr(run_s5, "execution_context", _context)
    monkeypatch.setattr(
        run_s5, "load_numerical_authority", lambda *_a, **_k: _authority()
    )
    monkeypatch.setattr(run_s5, "_outer", lambda: object())
    monkeypatch.setattr(
        run_s5, "audit_shard", lambda _d, **kw: _summary(kw["shard"]["shard_id"])
    )
    output = tmp_path / "annual"
    output.mkdir()
    shard_id = s5.ANNUAL_WAVES[0][0]
    directory = output / "shard-000"
    directory.mkdir()
    result_path = directory / "shard-result.json"
    result_path.write_text(json.dumps(_worker(shard_id)))
    progress_path = output / "progress.json"
    progress_path.write_text(
        json.dumps(
            run_s5._progress_payload(
                context=_context(),
                authority=_authority(),
                classification="running",
                next_wave=0,
                completed_shards=(),
                supervision_records=(),
                reviewed_continuations=(),
                root_outcomes=(),
            )
        )
    )
    before_progress = progress_path.read_bytes()
    before_result = result_path.read_bytes()
    if outcome != "unpublished":
        record = _supervision(shard_ids=(shard_id,))
        if outcome == "nonzero_exit":
            record["classification"] = "worker_process_failure"
            record["returncodes"] = {shard_id: 1}
            record["worker_results"] = {}
        log_path = output / f"{shard_id}.log"
        log_path.write_text("worker exited\n")
        record["worker_logs"] = {
            shard_id: {
                "path": log_path.name,
                "sha256": run_s5.sha256_path(log_path),
            }
        }
        (output / "supervision-wave-000-000.json").write_text(json.dumps(record))

    def stop_at_next_worker(
        shard_ids: tuple[str, ...], **_kw: object
    ) -> Mapping[str, object]:
        assert shard_ids == (s5.ANNUAL_WAVES[0][1],)
        assert (output / "reviewed-continuation-000.json").is_file()
        raise KeyboardInterrupt

    if outcome == "published":
        # Discover a durable supervision record even if progress never registered it.
        with pytest.raises(KeyboardInterrupt):
            run_s5.run_annual(
                output_root=output,
                reviewed_continue=True,
                supervisor=stop_at_next_worker,
            )
    else:
        with pytest.raises(
            ValueError, match="no bound zero-exit supervision outcome"
        ) as error:
            run_s5.run_annual(
                output_root=output,
                reviewed_continue=True,
                supervisor=lambda *_a, **_k: pytest.fail(
                    "launched with an unbound completed peer"
                ),
            )
        assert "audit-only reconciliation" in str(error.value)
        assert progress_path.read_bytes() == before_progress
        assert not list(output.glob("reviewed-continuation-*.json"))
        assert not (output / "run-result.json").exists()
    assert result_path.read_bytes() == before_result


def test_mixed_wave_retains_successful_peer_then_completes_and_merges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise real root, wave, merger and analyzer with only solves simulated."""
    for module in (run_s5, s5_analysis):
        monkeypatch.setattr(
            module, "load_numerical_authority", lambda *_a, **_k: _authority()
        )
        monkeypatch.setattr(module, "_outer", lambda: object())
        monkeypatch.setattr(
            module, "audit_shard", lambda _d, **kw: _summary(kw["shard"]["shard_id"])
        )
    monkeypatch.setattr(run_s5, "execution_context", _context)
    monkeypatch.setattr(s5_analysis, "analysis_context", lambda: {"git_clean": True})
    monkeypatch.setattr(
        s5_analysis, "verify_shard_artifacts", lambda *_a, **_k: ({}, ())
    )
    output = tmp_path / "annual"
    launched: list[str] = []
    failed_once = False

    class Process:
        def __init__(self, pid: int, code: int) -> None:
            self.pid = pid
            self.code = code
            self.done = False

        def poll(self) -> int | None:
            return self.code if self.done else None

        def wait(self) -> int:
            assert self.done
            return self.code

    def supervisor(
        shard_ids: tuple[str, ...], **kwargs: object
    ) -> Mapping[str, object]:
        processes: list[Process] = []

        def popen(command: list[str], **_kwargs: object) -> subprocess.Popen[bytes]:
            nonlocal failed_once
            shard_id = command[command.index("--shard-id") + 1]
            directory = Path(command[command.index("--directory") + 1])
            launched.append(shard_id)
            fail = shard_id == s5.ANNUAL_WAVES[0][1] and not failed_once
            failed_once |= fail
            if not fail:
                directory.mkdir(parents=True)
                (directory / "shard-result.json").write_text(
                    json.dumps(_worker(shard_id))
                )
            process = Process(20_000 + len(launched), 1 if fail else 0)
            processes.append(process)
            return cast(subprocess.Popen[bytes], process)

        def observations() -> list[run_s4b.ProcessObservation]:
            return [run_s4b.ProcessObservation(os.getpid(), 1, "parent", 20.0, 1.0)] + [
                run_s4b.ProcessObservation(p.pid, os.getpid(), str(p.pid), 100.0, 1.0)
                for p in processes
            ]

        def finish(_seconds: float) -> None:
            for process in processes:
                process.done = True

        return run_s5.supervise_wave(
            shard_ids,
            output_root=cast(Path, kwargs["output_root"]),
            reviewed_resume=bool(kwargs["reviewed_resume"]),
            popen=popen,
            observation_reader=observations,
            sleep=finish,
        )

    partial = run_s5.run_annual(output_root=output, supervisor=supervisor)
    assert partial["classification"] == "partial"
    first_path = output / "supervision-wave-000-000.json"
    first = json.loads(first_path.read_text())
    assert first["classification"] == "worker_process_failure"
    assert list(first["worker_results"]) == [s5.ANNUAL_WAVES[0][0]]
    assert s5_analysis.analyze_s5(output)["classification"] == "partial"
    completed = run_s5.run_annual(
        output_root=output,
        supervisor=supervisor,
        reviewed_continue=True,
    )
    assert completed["classification"] == "accepted"
    assert launched.count(s5.ANNUAL_WAVES[0][0]) == 1
    assert launched.count(s5.ANNUAL_WAVES[0][1]) == 2
    merged = json.loads((output / "merged-result.json").read_text())
    assert merged["completed_intervals"] == 8_760
    assert merged["shard_summary_sha256"] == [
        _summary(item)["summary_sha256"] for item in s5.ANNUAL_SHARD_IDS
    ]
    result = s5_analysis.analyze_s5(output)
    assert result["execution_complete"] is True
    assert result["accepted_for_s6"] is True
    s5_analysis.promote_completed(
        tmp_path / "S5_RESULTS.json", result, output_root=output
    )

    # Internally consistent but mixed wave authority must fail against the root.
    first["authority"]["per_worker_current_rss_mib"] = 32_768.0
    first_path.write_text(json.dumps(first))
    with pytest.raises(ValueError, match="differs from root"):
        s5_analysis.analyze_s5(output)
