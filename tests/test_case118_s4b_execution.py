from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from experiments.case118_annual_hierarchy.p0_fixture import load_p0_fixture
from experiments.case118_annual_hierarchy import run_s4b
from experiments.case118_annual_hierarchy import s4b_analysis
from experiments.case118_annual_hierarchy import s4b_execution
from experiments.case118_annual_hierarchy.s4b_manifest import (
    EXPECTED_MANIFEST_SHA256,
    PRIMARY_ATTEMPT_BUDGET_SECONDS,
    load_verified_manifest,
    object_sha256,
)
from experiments.case118_annual_hierarchy.streaming_runner import (
    execute_streaming_window,
    snapshot_inputs,
    solve_frozen_outer,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    window_archive_payload,
)
from experiments.case118_annual_hierarchy.streaming_schema import WindowIndexEntry


def _first_shard() -> dict[str, object]:
    registry = cast(
        dict[str, Any], s4b_execution.qualification_registry(run_s4b._outer())
    )
    return cast(dict[str, object], registry["shards"][0])


def _summary(shard: dict[str, object]) -> dict[str, object]:
    interval = cast(dict[str, object], shard["interval"])
    payload: dict[str, object] = {
        "schema_version": 1,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "qualification_registry_sha256": (
            s4b_execution.EXPECTED_QUALIFICATION_REGISTRY_SHA256
        ),
        "shard_id": shard["shard_id"],
        "interval": interval,
        "classification": "accepted",
        "execution_complete": True,
        "completed_intervals": cast(int, interval["stop"])
        - cast(int, interval["start"]),
        "initial_state": cast(dict[str, Any], shard["storage"])["initial_state"],
        "terminal_state": cast(dict[str, Any], shard["storage"])["terminal_state"],
        "checkpoint_sha256": "1" * 64,
        "execution_source_fingerprint": "3" * 64,
        "outer_plan_sha256": "4" * 64,
        "window_chain_sha256": "2" * 64,
        "timeout_count": 0,
        "recovery_window_count": 0,
        "coverage_fraction": 1.0,
        "shifted_primary_success_fraction": 1.0,
        "shifted_primary_opportunities": 1,
        "shifted_primary_successes": 1,
        "storage_throughput_mwh": 5.0,
        "cumulative_absolute_signpost_deviation_mwh": 0.0,
        "terminal_deviation_mwh": 0.0,
        "generation_cost": 1.0,
        "storage_cycling_cost": 2.0,
        "active_losses_mwh": 3.0,
        "renewable_curtailment_mwh": 4.0,
        "maximum_voltage_violation_pu": 0.0,
        "maximum_thermal_violation_mva": 0.0,
        "all_independent_audits_agree": True,
        "timing": {
            "accepted_solver_wall_seconds": 1.0,
            "primary_solver_wall_seconds": 1.0,
            "target_free_solver_wall_seconds": 0.0,
            "copied_solver_wall_seconds": 0.0,
            "model_construction_wall_seconds": 0.1,
            "primary_orchestration_wall_seconds": 1.2,
            "recovery_wall_seconds": 0.0,
            "recovery_restart_overhead_seconds": 0.0,
            "total_window_path_seconds": 1.2,
        },
    }
    return {**payload, "summary_sha256": object_sha256(payload)}


def test_tracked_manifest_is_not_execution_authority(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="remains unauthorized"):
        s4b_execution.load_qualification_authority(
            tmp_path / "missing.json",
            expected_execution_commit="a" * 40,
            expected_source_fingerprint="b" * 64,
        )


def test_qualification_authority_is_exact_and_never_annual(tmp_path: Path) -> None:
    path = tmp_path / "authority.json"
    payload = {
        "schema_version": 1,
        "classification": "reviewed_s4b_qualification_execution_authorized",
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "qualification_registry_sha256": (
            s4b_execution.EXPECTED_QUALIFICATION_REGISTRY_SHA256
        ),
        "qualification_execution_authorized": True,
        "annual_execution_authorized": False,
        "execution_scope": "bounded_24h_qualification",
        "execution_commit": "a" * 40,
        "source_fingerprint": "b" * 64,
    }
    path.write_text(json.dumps(payload))
    assert (
        s4b_execution.load_qualification_authority(
            path,
            expected_execution_commit="a" * 40,
            expected_source_fingerprint="b" * 64,
        )
        == payload
    )

    payload["annual_execution_authorized"] = True
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="does not match"):
        s4b_execution.load_qualification_authority(
            path,
            expected_execution_commit="a" * 40,
            expected_source_fingerprint="b" * 64,
        )


def test_qualification_registry_is_bounded_and_rejects_annual_shards() -> None:
    outer = run_s4b._outer()
    registry = s4b_execution.qualification_registry(outer)
    shards = cast(list[dict[str, Any]], registry["shards"])
    assert [item["interval"] for item in shards] == [
        {"start": 0, "stop": 24, "half_open": True},
        {"start": 0, "stop": 12, "half_open": True},
        {"start": 12, "stop": 24, "half_open": True},
    ]
    assert registry["registry_sha256"] == (
        s4b_execution.EXPECTED_QUALIFICATION_REGISTRY_SHA256
    )
    with pytest.raises(ValueError, match="unknown S4b qualification shard"):
        s4b_execution.qualification_shard_entry("s4b-shard-000", outer)


def test_shard_checkpoint_uses_global_contiguous_coordinates() -> None:
    shard = _first_shard()
    initial = cast(
        dict[str, Any], cast(dict[str, Any], shard["storage"])["initial_state"]
    )["soc_mwh"]
    entry = WindowIndexEntry(0, "window-000000.json.gz", 10, "a" * 64)
    checkpoint = s4b_execution.shard_checkpoint_payload(
        shard=shard,
        execution_source_fingerprint="b" * 64,
        outer_plan_sha256="c" * 64,
        execution_mode="ordinary",
        realized_soc_mwh=initial,
        preceding_controlling_attempt_id="ac-000-00-primary_controlling",
        windows=(entry,),
    )

    assert checkpoint["completed_intervals"] == 1
    assert checkpoint["next_global_iteration"] == 1
    assert checkpoint["complete"] is False


def test_later_shard_checkpoint_rejects_local_iteration_numbering() -> None:
    payload = cast(
        dict[str, Any], s4b_execution.qualification_registry(run_s4b._outer())
    )
    shard = cast(dict[str, object], payload["shards"][2])
    initial = cast(
        dict[str, Any], cast(dict[str, Any], shard["storage"])["initial_state"]
    )["soc_mwh"]
    wrong = WindowIndexEntry(0, "window.json.gz", 10, "a" * 64)

    with pytest.raises(ValueError, match="window registry"):
        s4b_execution.shard_checkpoint_payload(
            shard=shard,
            execution_source_fingerprint="b" * 64,
            outer_plan_sha256="c" * 64,
            execution_mode="partitioned_fresh_sequential",
            realized_soc_mwh=initial,
            preceding_controlling_attempt_id="ac-682-00-primary_controlling",
            windows=(wrong,),
        )


def test_merge_is_order_independent_and_rejects_corruption() -> None:
    payload = cast(dict[str, Any], load_verified_manifest()["manifest"])
    summaries = [_summary(cast(dict[str, object], item)) for item in payload["shards"]]

    forward = s4b_execution.merge_shard_summaries(summaries)
    reverse = s4b_execution.merge_shard_summaries(list(reversed(summaries)))
    assert forward == reverse
    assert forward["completed_intervals"] == 8_760

    corrupted = deepcopy(summaries)
    corrupted[0]["completed_intervals"] -= 1
    corrupted[1]["completed_intervals"] += 1
    for item in corrupted[:2]:
        item["summary_sha256"] = object_sha256(
            {key: value for key, value in item.items() if key != "summary_sha256"}
        )
    with pytest.raises(ValueError, match="accepted merge evidence"):
        s4b_execution.merge_shard_summaries(corrupted)


def test_bounded_partition_merge_completes_at_24_not_8760() -> None:
    registry = cast(
        dict[str, Any], s4b_execution.qualification_registry(run_s4b._outer())
    )
    shards = cast(list[dict[str, object]], registry["shards"])[1:]
    merged = s4b_execution.merge_shard_summaries(
        [_summary(item) for item in shards], registry_shards=shards
    )
    assert merged["classification"] == "accepted_bounded_partition"
    assert merged["completed_intervals"] == 24
    assert merged["expected_horizon_steps"] == 24
    assert merged["execution_complete"] is True


def test_five_minute_timeout_enters_causal_recovery_and_advances_once() -> None:
    fixture = load_p0_fixture(6)
    snapshot = snapshot_inputs(fixture.inputs)
    outer = solve_frozen_outer(snapshot, fixture.policy, fixture.solve_config)

    window = execute_streaming_window(
        snapshot,
        fixture.policy,
        fixture.solve_config,
        outer,
        0,
        {"p0_storage_bus_7": 500.0},
        None,
        primary_timeout_seconds=PRIMARY_ATTEMPT_BUDGET_SECONDS,
    )

    assert [attempt.slot_state for attempt in window.attempts[:3]] == [
        "timeout",
        "executed",
        "executed",
    ]
    assert window.attempts[1].audit is not None
    assert window.attempts[1].audit.accepted_primal
    assert window.controlling_attempt is window.attempts[2]
    assert window.attempts[2].source_attempt_id == window.attempts[1].attempt_id
    assert all(
        attempt.slot_state == "not_needed_after_acceptance"
        for attempt in window.attempts[3:]
    )
    assert window.post_step_soc_mwh is not None
    archive = window_archive_payload(
        window,
        inputs=snapshot,
        policy=fixture.policy,
        outer=outer,
        preceding_controlling_attempt_id=None,
        primary_attempt_budget_seconds=PRIMARY_ATTEMPT_BUDGET_SECONDS,
    )
    assert cast(list[dict[str, Any]], archive["attempts"])[0]["slot_state"] == (
        "timeout"
    )


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 12345
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        assert self.returncode is not None
        return self.returncode


def test_primary_timeout_supervision_stops_and_joins_process(tmp_path: Path) -> None:
    phase_path = tmp_path / "window-phase-000000-primary.json"
    phase_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "events": [
                    {
                        "phase": "before_ac_solve",
                        "iteration": 0,
                        "attempt_ordinal": 0,
                        "monotonic_seconds": 10.0,
                    }
                ],
            }
        )
    )
    process = _FakeProcess()
    clock_values = iter((10.0, 310.0, 310.0, 310.0))
    terminated: list[tuple[int, float]] = []

    def terminate(fake: Any, grace: float) -> None:
        terminated.append((fake.pid, grace))
        fake.returncode = -15

    record = run_s4b.supervise_window_process(
        ["unused"],
        directory=tmp_path,
        iteration=0,
        clock=lambda: next(clock_values),
        sleep=lambda _value: None,
        popen=lambda *args, **kwargs: cast(Any, process),
        terminate_process=terminate,
    )

    assert record["classification"] == "timeout"
    assert record["primary_budget_seconds"] == 300.0
    assert record["primary_budget_consumed_seconds"] == 300.0
    assert terminated == [(12345, run_s4b.ATTEMPT_TERMINATION_GRACE_SECONDS)]
    assert process.returncode == -15


def test_process_tree_usage_deduplicates_descendants() -> None:
    rows = (
        run_s4b.ProcessObservation(10, 1, "root-a", 100.0, 2.0),
        run_s4b.ProcessObservation(20, 1, "root-b", 200.0, 3.0),
        run_s4b.ProcessObservation(11, 10, "child-a", 40.0, 4.0),
        run_s4b.ProcessObservation(12, 11, "grandchild-a", 10.0, 5.0),
        run_s4b.ProcessObservation(21, 20, "child-b", 50.0, 6.0),
    )

    usage = run_s4b.process_tree_usage((10, 20), rows)

    assert usage["aggregate_rss_mib"] == 400.0
    assert usage["aggregate_cpu_seconds"] == 20.0
    assert cast(dict[str, Any], usage["per_worker"])["10"]["rss_mib"] == 150.0
    assert cast(dict[str, Any], usage["per_worker"])["20"]["rss_mib"] == 250.0
    assert run_s4b._cpu_seconds("01:02.50") == 62.5
    assert run_s4b._cpu_seconds("1-02:03:04.25") == 93_784.25


def test_process_tree_usage_rejects_missing_live_root() -> None:
    with pytest.raises(ValueError, match="cannot sample"):
        run_s4b.process_tree_usage(
            (10,),
            (run_s4b.ProcessObservation(11, 10, "child", 1.0, 1.0),),
        )


def test_analyzer_reconstructs_concurrency_resources_and_provenance() -> None:
    worker_results = {
        "s4b-qualification-partition-a": {"shard_id": "a"},
        "s4b-qualification-partition-b": {"shard_id": "b"},
    }
    context = {
        "git_commit": "a" * 40,
        "git_clean": True,
        "source_fingerprint": "b" * 64,
    }
    record: dict[str, object] = {
        "schema_version": 1,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "classification": "accepted",
        "authority": {
            "execution_commit": "a" * 40,
            "source_fingerprint": "b" * 64,
        },
        "execution_context": context,
        "run_label": "partitioned_fresh_concurrent",
        "requested_shards": list(worker_results),
        "requested_concurrency": 2,
        "maximum_observed_concurrency": 2,
        "worker_root_pids": {
            "s4b-qualification-partition-a": 10,
            "s4b-qualification-partition-b": 20,
        },
        "worker_results": worker_results,
        "returncodes": {
            "s4b-qualification-partition-a": 0,
            "s4b-qualification-partition-b": 0,
        },
        "resource_triggers": [],
        "artifact_error": None,
        "supervisor_exception_kind": None,
        "supervisor_exception": None,
        "peak_worker_rss_mib": {
            "s4b-qualification-partition-a": 100.0,
            "s4b-qualification-partition-b": 200.0,
        },
        "peak_aggregate_rss_mib": 300.0,
        "resource_samples": [
            {
                "active_shards": list(worker_results),
                "supervisor_current_rss_mib": 10.0,
                "supervisor_cpu_seconds": 1.0,
                "per_worker": {
                    "10": {
                        "process_identities": [[10, "a"]],
                        "rss_mib": 100.0,
                        "cpu_seconds": 2.0,
                    },
                    "20": {
                        "process_identities": [[20, "b"]],
                        "rss_mib": 200.0,
                        "cpu_seconds": 3.0,
                    },
                },
                "aggregate_process_identities": [[10, "a"], [20, "b"]],
                "aggregate_rss_mib": 300.0,
                "aggregate_cpu_seconds": 5.0,
            }
        ],
    }
    assert s4b_analysis.validate_supervision(record) == record
    record["maximum_observed_concurrency"] = 1
    with pytest.raises(ValueError, match="concurrency does not reconstruct"):
        s4b_analysis.validate_supervision(record)


def test_complete_analysis_reconstructs_bounded_run_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shard_ids = {
        "ordinary": ("s4b-qualification-ordinary",),
        "partitioned_one_process": (
            "s4b-qualification-partition-a",
            "s4b-qualification-partition-b",
        ),
        "partitioned_fresh_sequential": (
            "s4b-qualification-partition-a",
            "s4b-qualification-partition-b",
        ),
        "partitioned_fresh_concurrent": (
            "s4b-qualification-partition-a",
            "s4b-qualification-partition-b",
        ),
    }
    interval_by_id = {
        "s4b-qualification-ordinary": {"start": 0, "stop": 24},
        "s4b-qualification-partition-a": {"start": 0, "stop": 12},
        "s4b-qualification-partition-b": {"start": 12, "stop": 24},
    }
    directories: list[Path] = []
    workers: dict[str, dict[str, object]] = {}
    for mode, ids in shard_ids.items():
        for shard_id in ids:
            directory = tmp_path / mode / shard_id
            directory.mkdir(parents=True)
            worker = {
                "shard_id": shard_id,
                "execution_mode": mode,
                "worker_pid": 100 + len(workers),
                "completed_child_cpu_seconds": 1.0,
                "execution_complete": True,
                "classification": "accepted",
                "all_independent_audits_agree": True,
                "checkpoint_sha256": "a" * 64,
                "window_chain_sha256": "b" * 64,
            }
            (directory / "shard-result.json").write_text(json.dumps(worker))
            workers[f"{mode}:{shard_id}"] = worker
            directories.append(directory)

    fake_shards = {
        shard_id: {"shard_id": shard_id, "interval": interval}
        for shard_id, interval in interval_by_id.items()
    }
    monkeypatch.setattr(s4b_analysis, "_outer", lambda: object())
    monkeypatch.setattr(
        s4b_analysis,
        "qualification_registry",
        lambda _outer: {"shards": list(fake_shards.values())},
    )
    monkeypatch.setattr(
        s4b_analysis,
        "qualification_shard_entry",
        lambda shard_id, _outer: ({}, fake_shards[shard_id]),
    )
    monkeypatch.setattr(
        s4b_analysis,
        "audit_shard",
        lambda directory, **_kwargs: {
            key: value
            for key, value in json.loads(
                (directory / "shard-result.json").read_text()
            ).items()
            if key
            not in {"execution_mode", "worker_pid", "completed_child_cpu_seconds"}
        },
    )

    def fake_archives(
        directory: Path, **_kwargs: object
    ) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
        worker = json.loads((directory / "shard-result.json").read_text())
        interval = interval_by_id[worker["shard_id"]]
        archives = tuple(
            {
                "attempts": [],
                "executed_interval": {"b_mw": [0.0]},
                "post_step_soc_mwh": [0.0],
                "interval_stop": (
                    min(index + 3, interval["stop"])
                    if worker["execution_mode"] != "ordinary"
                    else min(index + 3, 24)
                ),
            }
            for index in range(interval["start"], interval["stop"])
        )
        return {}, archives

    monkeypatch.setattr(s4b_analysis, "verify_shard_artifacts", fake_archives)
    monkeypatch.setattr(
        s4b_analysis,
        "merge_shard_summaries",
        lambda *_args, **_kwargs: {
            "completed_intervals": 24,
            "initial_state": {},
            "terminal_state": {},
            "all_independent_audits_agree": True,
        },
    )
    monkeypatch.setattr(s4b_analysis, "analysis_context", lambda: {"git_clean": True})

    supervision: list[dict[str, object]] = []
    for mode in (
        "ordinary",
        "partitioned_fresh_sequential",
        "partitioned_fresh_concurrent",
    ):
        groups = (
            [(item,) for item in shard_ids[mode]]
            if mode == "partitioned_fresh_sequential"
            else [shard_ids[mode]]
        )
        for group in groups:
            supervision.append(
                {
                    "classification": "accepted",
                    "run_label": mode,
                    "requested_shards": list(group),
                    "requested_concurrency": len(group),
                    "maximum_observed_concurrency": len(group),
                    "worker_root_pids": {
                        item: workers[f"{mode}:{item}"]["worker_pid"] for item in group
                    },
                    "worker_results": {
                        item: workers[f"{mode}:{item}"] for item in group
                    },
                }
            )
    monkeypatch.setattr(s4b_analysis, "validate_supervision", lambda value: value)
    supervision_paths = []
    for index, item in enumerate(supervision):
        path = tmp_path / f"supervision-{index}.json"
        path.write_text(json.dumps(item))
        supervision_paths.append(path)
    root_path = tmp_path / "one-process.json"
    one_workers = [
        workers[f"partitioned_one_process:{item}"]
        for item in shard_ids["partitioned_one_process"]
    ]
    one_workers[1]["worker_pid"] = one_workers[0]["worker_pid"]
    (directories[2] / "shard-result.json").write_text(json.dumps(one_workers[1]))
    root_path.write_text(
        json.dumps(
            {
                "classification": "accepted",
                "run_label": "partitioned_one_process",
                "worker_pid": one_workers[0]["worker_pid"],
                "worker_results": one_workers,
                "execution_context": {
                    "git_clean": True,
                    "git_commit": "a",
                    "source_fingerprint": "b",
                },
                "authority": {"execution_commit": "a", "source_fingerprint": "b"},
            }
        )
    )

    result = s4b_analysis.analyze_s4b(
        directories,
        supervision_paths=supervision_paths,
        run_result_paths=[root_path],
    )
    assert result["execution_complete"] is True
    assert result["run_evidence_matrix_complete"] is True
    assert result["accepted_for_s5"] is True
    assert (
        cast(dict[str, object], result["boundary_effect_characterization"])[
            "window_structures_differ"
        ]
        is True
    )


def test_partial_analysis_cannot_occupy_authoritative_destination(
    tmp_path: Path,
) -> None:
    base = {
        "schema_version": 1,
        "classification": "partial",
        "execution_complete": False,
        "accepted_for_s5": False,
    }
    result = {**base, "analysis_sha256": object_sha256(base)}

    with pytest.raises(ValueError, match="cannot be promoted"):
        s4b_analysis.promote_completed(tmp_path / "S4B_RESULTS.json", result)

    assert not (tmp_path / "S4B_RESULTS.json").exists()
