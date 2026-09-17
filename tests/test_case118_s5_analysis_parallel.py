"""Real spawn-pool tests with synthetic audits; no annual reconstruction/solves."""
import json
import os
from pathlib import Path
import time

import pytest

from experiments.case118_annual_hierarchy import s5_analysis
from experiments.case118_annual_hierarchy.s4b_manifest import canonical_json


def _synthetic_audit(directory, **kwargs):
    """Small deterministic stand-in for costly physics, used in both modes."""
    payload = json.loads((directory / "sample.json").read_text())
    time.sleep(payload["delay"])
    if payload.get("fail"):
        raise ValueError("synthetic shard is corrupt")
    assert kwargs["allowed_execution_modes"] == ("annual",)
    assert kwargs["outer"] == "test outer"
    with (directory / "calls.txt").open("a") as stream:
        stream.write(f"{os.getpid()}\n")
    return {"shard_id": kwargs["shard"]["shard_id"], "sum": sum(payload["values"])}


def _initialize_synthetic_auditor():
    # This function is imported in each actual spawned child; no fork inheritance.
    s5_analysis._SHARD_AUDIT_OUTER = "test outer"
    s5_analysis.audit_shard = _synthetic_audit


def _jobs(tmp_path):
    jobs = []
    for i in range(4):
        directory = tmp_path / f"shard-{i}"
        directory.mkdir()
        (directory / "sample.json").write_text(json.dumps({
            "values": [i, -i, i+1], "delay": .25 if i == 0 else .02,
        }))
        jobs.append((directory, {"shard_id": f"shard-{i}"}))
    return jobs


def test_spawned_audits_equal_serial_in_fixed_order(monkeypatch, tmp_path):
    jobs = _jobs(tmp_path)
    monkeypatch.setattr(s5_analysis, "audit_shard", _synthetic_audit)
    monkeypatch.setattr(s5_analysis, "_initialize_shard_auditor", _initialize_synthetic_auditor)
    serial = s5_analysis._audit_shards(jobs, outer="test outer", workers=1)
    parallel = s5_analysis._audit_shards(jobs, outer="test outer", workers=2)
    assert canonical_json(parallel) == canonical_json(serial)
    assert [item["shard_id"] for item in parallel] == [job[1]["shard_id"] for job in jobs]
    child_pids = set()
    for directory, _ in jobs:
        calls = (directory / "calls.txt").read_text().splitlines()
        assert len(calls) == 2  # Exactly one audit in each mode, not repeated checks.
        assert int(calls[0]) == os.getpid()
        assert int(calls[1]) != os.getpid()
        child_pids.add(calls[1])
    assert len(child_pids) == 2


def test_parallel_failure_prevents_promotion(monkeypatch, tmp_path):
    jobs = _jobs(tmp_path)
    (jobs[1][0] / "sample.json").write_text(json.dumps({"delay": 0, "fail": True}))
    monkeypatch.setattr(s5_analysis, "_initialize_shard_auditor", _initialize_synthetic_auditor)

    def analysis(*args, **kwargs):
        s5_analysis._audit_shards(jobs, outer="test outer", workers=kwargs["workers"])
        pytest.fail("a failed shard must not produce an annual result")

    monkeypatch.setattr(s5_analysis, "analyze_s5", analysis)
    destination = tmp_path / "S5_RESULTS.json"
    with pytest.raises(ValueError, match="synthetic shard is corrupt"):
        s5_analysis.promote_completed(destination, workers=2)
    assert not destination.exists()


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_invalid_worker_count_rejected_before_reading_archives(tmp_path, workers):
    with pytest.raises(ValueError, match="positive integer"):
        s5_analysis.analyze_s5(tmp_path / "absent", workers=workers)
