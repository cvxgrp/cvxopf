"""Portable, non-solving schedule transfer, replay and supervision checks."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import cvxpy as cp
import numpy as np
import pytest

from cvxopf import build_opf_multistep
from cvxopf._hierarchical_solver import _assign_start
from cvxopf._numerical_preparation import PreparationEvidence
from experiments.ac_cost_qualification.audit import canonical_objective
from experiments.case118_tracy_2021 import e3_prepared
from experiments.case118_tracy_2021.e3_results import HERE as TRACY
from experiments.numerical_preparation import fixture
from experiments.numerical_preparation.audit import evidence_record, serializable
from experiments.m11_socp.e2_v2_audit import ac_canonical
from experiments.tracy_battery_transfer import model as m, run as r


@pytest.fixture(autouse=True)
def no_optimizer(monkeypatch):
    monkeypatch.setattr(
        cp.Problem, "solve", lambda *_a, **_k: pytest.fail("optimizer not authorized")
    )


def kwargs(delta=1.0):
    values = fixture.case9_kwargs(3, "ac")
    values["delta"] = delta
    values["options"] = replace(
        values["options"],
        numerical_preparation=e3_prepared.policy("ac", "component_first"),
    )
    return values


def test_declared_matrix_and_approved_limits():
    data = dict(cases={f"case{i}": {} for i in range(6)})
    jobs = m.jobs(data)
    assert len(jobs) == m.LIMITS["max_launches"] == 18
    assert [job["id"] for job in jobs] == list(range(1, 19))
    assert all(sum(j["donor"] == d for j in jobs) == 6 for d in m.DONORS)
    assert m.LIMITS["wall_seconds"] == 3600
    assert m.LIMITS["rss_mib"] == 16384
    assert m.LIMITS["total_worker_seconds"] == 64800
    with pytest.raises(ValueError, match="six"):
        m.jobs(dict(cases={}))


@pytest.mark.parametrize("delta", [1.0, 0.5])
def test_all_power_pins_keep_initial_and_dynamics_but_remove_terminal_api(delta):
    original = kwargs(delta)
    schedule = np.array([[3.0], [-2.0], [1.0]]) / delta
    built, transfer, boundaries, checks = m.transfer_build(original, schedule)
    assert checks["passed"]
    assert original["storage"][0].terminal_soc == 18.0
    assert original["storage"][0].terminal_constraint == "equality"
    assert transfer["storage"][0].terminal_soc is None
    assert transfer["storage"][0].terminal_constraint is None
    assert transfer["storage"][0].initial_soc == 20.0
    for field in ("aging_weight", "capacity", "apparent_power_rating", "device_id"):
        assert getattr(original["storage"][0], field) == getattr(
            transfer["storage"][0], field
        )
    unpinned = build_opf_multistep(**transfer)
    assert len(built.prob.constraints) == len(unpinned.prob.constraints) + 1
    assert built.prob.constraints[-1].shape == (1, 3)
    assert built.prob.constraints[-1].is_dcp()
    built.variables["b"].save_value(schedule.T)
    built.variables["soc"].save_value(boundaries.T)
    assert np.max(built.prob.constraints[-1].violation()) == 0
    np.testing.assert_array_equal(boundaries[:, 0], [20.0, 17.0, 19.0, 18.0])
    result = dict(b=schedule, soc=boundaries[1:])
    audit = m.pin_audit(result, schedule, transfer, [18.0])
    assert audit["passed"] and audit["maximum_power_difference_mw"] == 0.0
    result["b"] = schedule.copy()
    result["b"][-1, 0] += 0.01
    assert not m.pin_audit(result, schedule, transfer, [18.0])["passed"]


def test_source_float_values_remain_unrepaired():
    values = kwargs()
    source = np.array([[3.0], [-2.0], [1.0 + 2e-8]])
    before = source.copy()
    built, transfer, boundaries, checks = m.transfer_build(values, source)
    np.testing.assert_array_equal(source, before)
    assert checks["endpoint_difference_mwh"] == pytest.approx(2e-8)
    built.variables["b"].save_value(source.T)
    assert np.max(built.prob.constraints[-1].violation()) == 0.0
    assert m.pin_audit(dict(b=source, soc=boundaries[1:]), source, transfer, [18.0])[
        "passed"
    ]
    np.testing.assert_array_equal(values["df_load_p"], transfer["df_load_p"])


@pytest.mark.parametrize(
    "schedule",
    [
        np.array([[11.0], [-10.0], [1.0]]),
        np.array([[0.0], [0.0], [0.0]]),
        np.array([[np.nan], [0.0], [2.0]]),
        np.zeros((3, 2)),
    ],
)
def test_invalid_source_schedule_is_not_clipped_or_repaired(schedule):
    with pytest.raises(ValueError):
        m.transfer_build(kwargs(), schedule)


def test_source_terminal_cost_or_non_ac_build_cannot_silently_change():
    values = kwargs()
    values["storage"] = [
        replace(
            values["storage"][0],
            terminal_constraint=None,
            terminal_cost="linear",
            terminal_weight=1.0,
        )
    ]
    with pytest.raises(ValueError, match="without terminal costs"):
        m.transfer_build(values, np.array([[3.0], [-2.0], [1.0]]))
    with pytest.raises(ValueError, match="vectorized AC"):
        m.transfer_build(
            dict(kwargs(), formulation="socp"), np.array([[3.0], [-2.0], [1.0]])
        )


def test_comparison_preserves_negative_cost_changes_and_gross_throughput():
    baseline = dict(
        Pg=[[2.0], [3.0]],
        p_load_served=[[5.0], [5.0]],
        p_load_shed=[[0.0], [0.0]],
        curtailment=[[1.0], [1.0]],
        p_net=[[0.1], [0.2]],
        b=[[1.0, -1.0], [-2.0, 2.0]],
    )
    transfer = dict(baseline, Pg=[[1.0], [3.0]])
    comparison = m.comparison(
        transfer,
        dict(generator_cost=3.0, storage_cost=0.1),
        dict(result=baseline, named_costs=dict(generator_cost=4.0, storage_cost=0.2)),
    )
    assert comparison["transfer"]["battery_throughput_mwh"] == 6.0
    assert comparison["transfer_minus_unrestricted_ac"][
        "physical_cost"
    ] == pytest.approx(-1.1)
    assert (
        comparison["transfer_minus_unrestricted_ac"]["conventional_generation_mwh"]
        == -1.0
    )


def test_canonical_pin_survives_cost_coordinate_transformation():
    source = np.array([[3.0], [-2.0], [1.0]])
    build, _, _, _ = m.transfer_build(kwargs(), source)
    captured = e3_prepared.prepared_start(build, 1)
    _, solver, bindings, _, _, _, _ = ac_canonical(build)
    cycling = next(binding for binding in bindings if binding.term.kind == "cycling")
    cycling.leaf.save_value(cycling.scale * source.T)
    assert np.max(solver.prob.constraints[-1].violation()) == 0.0
    cycling.leaf.save_value(cycling.scale * (source.T + 0.1))
    assert np.max(solver.prob.constraints[-1].violation()) > 0.0
    assert captured["cost_maps"] and captured["iteration"] == 1


def test_successful_native_replay_restores_pins_and_detects_tampering(monkeypatch):
    schedule = np.array([[3.0], [-2.0], [1.0]])
    build, transfer, boundaries, _ = m.transfer_build(kwargs(), schedule)
    start = r.q.physical_start(build, transfer)
    start["b"], start["soc"] = schedule.T, boundaries.T
    _assign_start(build, start)
    captured = e3_prepared.prepared_start(build, 1)
    _, _, bindings, _, data, inverse, mapping = ac_canonical(build)
    adjusted = data["x0"].copy()
    adjusted[mapping.fixed] = mapping.values
    value, _ = canonical_objective(data, inverse, adjusted)
    native = serializable(dict(status=0, obj_val=value, x=adjusted[mapping.free]))
    evidence = evidence_record(
        PreparationEvidence(
            mapping,
            native,
            dict(
                objective_assembly="component_first",
                cost_coordinate_maps=[
                    dict(
                        kind=b.term.kind,
                        scale=b.scale,
                        physical_start=b.physical_start,
                        solver_variable_id=b.leaf.id,
                    )
                    for b in bindings
                ],
            ),
            np.ones(mapping.free.size),
            np.ones(mapping.kept.size),
            assigned_x0=data["x0"],
            adjusted_x0=adjusted,
            reduced_x0=adjusted[mapping.free],
            start_layout=tuple(
                (
                    v.id,
                    v.shape,
                    inverse[-1].var_offsets[v.id],
                    inverse[-1].var_offsets[v.id] + v.size,
                )
                for v in data["problem"].variables()
            ),
        )
    )
    published, _, _ = e3_prepared.restore_ac(
        build, evidence, dict(assigned_start=serializable(start)), captured
    )
    named = {
        k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")
    }
    record = dict(
        native=native,
        preparation_evidence=evidence,
        result=published,
        named_costs=named,
    )
    # The synthetic canonical point is not a feasibility certificate. Mock only
    # physics; keep native slicing, publication, economics and all pins live.
    monkeypatch.setattr(
        e3_prepared,
        "audit",
        lambda build, *_: (
            dict(
                passed=True,
                costs={
                    k: float(v.value)
                    for k, v in build.expressions.items()
                    if k.endswith("_cost")
                },
            ),
            None,
        ),
    )
    row = dict(original_terminal_soc_mwh=[18.0], ac_reference=dict(cohort="prepared"))
    fresh = m.transfer_build(kwargs(), schedule)[0]
    checks = m.assess(fresh, transfer, schedule, start, row, record, captured)
    assert checks["passed"] and checks["pins"]["passed"]
    assert checks["canonical_reconstruction"] == 0
    corrupt = deepcopy(record)
    corrupt["result"]["b"][-1][0] += 0.01
    with pytest.raises(ValueError):
        m.assess(fresh, transfer, schedule, start, row, corrupt, captured)
    corrupt = deepcopy(record)
    corrupt["preparation_evidence"]["adjusted_x0"][0] += 1
    with pytest.raises(ValueError, match="reduced start"):
        m.assess(fresh, transfer, schedule, start, row, corrupt, captured)


def journal(monkeypatch, root):
    data = dict(cases={f"case{i}": {} for i in range(6)})
    binding = dict(rows=[dict(job=job) for job in m.jobs(data)])
    r.q.atomic_immutable_json(root / "binding.json", binding)
    r.q.atomic_immutable_json(root / "protocol.json", r.protocol())
    monkeypatch.setattr(r, "verify_binding", lambda _: (binding, data))
    monkeypatch.setattr(e3_prepared, "verified_inputs", lambda: None)
    directory = root / "call-001"
    directory.mkdir()
    req = r.request(root, 1, 3600.0)
    r.q.atomic_immutable_json(directory / "request.json", req)
    return binding, data, directory, req


def test_unfinished_archives_are_never_compatible(monkeypatch, tmp_path):
    _, _, directory, _ = journal(monkeypatch, tmp_path)
    for name in (
        "result.json.gz",
        "native.json.gz",
        "preparation.json.gz",
        "completion.json",
    ):
        (directory / name).touch()
    progress = r.status(tmp_path)
    assert (
        progress["launches"] == 1
        and progress["finalized"] == progress["compatible"] == 0
    )
    assert progress["result_archives"] == progress["completion_manifests"] == 1
    assert progress["attempts"][0]["classification"] == "unfinished"
    (tmp_path / "call-002").mkdir()
    with pytest.raises(ValueError, match="advance unfinished"):
        r.status(tmp_path)


def test_finalized_native_rejection_and_tampered_manifest(monkeypatch, tmp_path):
    binding, _, directory, req = journal(monkeypatch, tmp_path)
    monkeypatch.setattr(r.q, "resource_evidence", lambda *_: True)
    r.q.atomic_immutable_json(
        directory / "supervision.json",
        dict(
            classification="exited",
            returncode=0,
            wall_seconds=10.0,
            peak_sampled_rss_mib=1.0,
        ),
    )
    record = dict(
        iteration=1,
        call_id=1,
        request=req,
        optimizer_calls=1,
        exception=None,
        binding_sha256=r.q.digest(tmp_path / "binding.json"),
        row=binding["rows"][0],
        native=dict(status=2),
        preparation_evidence=dict(native=dict(status=2)),
        checks=dict(passed=False, reason="native numerical rejection", native_status=2),
    )
    record["classification"] = r.classify(record)
    values = {name: {} for name in r.ARTIFACTS}
    values.update(
        {
            "request.json": req,
            "result.json.gz": record,
            "native.json.gz": dict(iteration=1, data=record["native"]),
            "preparation.json.gz": dict(
                iteration=1, data=record["preparation_evidence"]
            ),
            "x0.json.gz": dict(iteration=1),
            "start.json": dict(assigned_start={}),
        }
    )
    for name, value in values.items():
        if name == "request.json":
            continue
        (r.q.atomic_gzip_json if name.endswith(".gz") else r.q.atomic_immutable_json)(
            directory / name, value
        )
    r.q.atomic_immutable_json(
        directory / "completion.json",
        dict(
            classification=record["classification"],
            artifacts={name: r.q.digest(directory / name) for name in r.ARTIFACTS},
        ),
    )
    monkeypatch.setattr(
        r,
        "checked_construction",
        lambda *_: (None, {}, [], {}, binding["rows"][0], dict(iteration=1)),
    )
    progress = r.status(tmp_path)
    assert progress["finalized"] == progress["native_rejections"] == 1
    assert progress["compatible"] == 0
    (directory / "monitoring.json").write_text(json.dumps(dict(changed=True)))
    with pytest.raises(ValueError, match="manifest/archive"):
        r.status(tmp_path)


@pytest.mark.parametrize("wall", [True, float("nan"), -1.0, 3601.0, 100.0])
def test_attempt_requests_respect_per_attempt_and_remaining_budgets(tmp_path, wall):
    r.q.atomic_immutable_json(tmp_path / "protocol.json", r.protocol())
    req = r.request(tmp_path, 1, wall)
    with pytest.raises(ValueError, match="budget"):
        r.validate_request(tmp_path, 1, req, remaining=99.0)


def test_dirty_source_cannot_create_execution_artifacts(monkeypatch, tmp_path):
    root = tmp_path / "unlaunched"
    monkeypatch.setattr(
        r, "frozen_binding", lambda: dict(context=dict(clean=False, commit="a" * 40))
    )
    monkeypatch.setattr(
        r, "monitoring", lambda: pytest.fail("launch preflight reached")
    )
    with pytest.raises(ValueError, match="clean full commit"):
        r.run(root, "a" * 40)
    assert not root.exists()


def test_worker_exception_is_bound_and_archived_without_optimization(
    monkeypatch, tmp_path
):
    _, _, directory, req = journal(monkeypatch, tmp_path)
    r.q.atomic_immutable_json(directory / "launch.json", dict(pid=r.os.getpid()))

    def fail(_):
        raise ValueError("source changed")

    monkeypatch.setattr(r, "verify_binding", fail)
    r.worker(tmp_path, 1)
    record = r.q.read(directory / "result.json.gz")
    assert record["request"] == req and record["binding_sha256"] == r.q.digest(
        tmp_path / "binding.json"
    )
    assert record["classification"] == "exception" and record["optimizer_calls"] == 0
    assert r.q.read(directory / "completion.json")["classification"] == "exception"


@pytest.mark.parametrize("raises", [False, True])
def test_worker_retains_native_rejection_archives(monkeypatch, tmp_path, raises):
    binding, _, directory, _ = journal(monkeypatch, tmp_path)
    r.q.atomic_immutable_json(directory / "launch.json", dict(pid=r.os.getpid()))
    r.q.atomic_immutable_json(directory / "monitoring.json", dict(valid=True))
    called = []

    def solve(**options):
        called.append(options)
        if raises:
            raise cp.error.SolverError("synthetic native rejection")

    build = SimpleNamespace(
        solve=solve, preparation_evidence=SimpleNamespace(native=dict(status=2))
    )
    monkeypatch.setattr(
        r,
        "checked_construction",
        lambda *_: (build, {}, [], {}, binding["rows"][0], dict(iteration=1)),
    )
    monkeypatch.setattr(
        r, "evidence_record", lambda evidence: dict(native=evidence.native)
    )
    monkeypatch.setattr(e3_prepared, "verify_prepared_start", lambda *_: None)
    r.worker(tmp_path, 1)
    record = r.q.read(directory / "result.json.gz")
    assert called == [e3_prepared.DEFAULT_PROTOCOL["ac_options"]]
    assert record["iteration"] == record["optimizer_calls"] == 1
    assert record["classification"] == "native_rejection"
    assert record["exception"] is None
    assert r.q.read(directory / "native.json.gz") == dict(
        iteration=1, data=record["native"]
    )
    assert r.q.read(directory / "preparation.json.gz") == dict(
        iteration=1, data=record["preparation_evidence"]
    )
    completion = r.q.read(directory / "completion.json")
    assert set(completion["artifacts"]) == set(r.ARTIFACTS)
    assert all(
        r.q.digest(directory / name) == digest
        for name, digest in completion["artifacts"].items()
    )


@pytest.mark.parametrize(
    "outcome, expected, launches",
    [
        ("native_rejection", "matrix_complete", 18),
        ("audit_failure", "matrix_complete", 18),
        ("wall_limit", "matrix_complete", 18),
        ("exception", "worker_exception", 1),
        ("rss_limit", "supervision_failure", 1),
        ("budget_exhausted", "budget_exhausted", 1),
    ],
)
def test_runner_consumes_cases_without_retry_and_stops_on_infrastructure(
    monkeypatch, tmp_path, outcome, expected, launches
):
    root = tmp_path / "fresh"
    binding = dict(
        context=dict(
            clean=True,
            commit="a" * 40,
            thread_environment={key: "1" for key in r.THREAD_KEYS},
        )
    )
    monkeypatch.setattr(r, "frozen_binding", lambda: binding)
    monkeypatch.setattr(r, "monitoring", lambda: dict(valid=True))
    calls = []

    def progress(_):
        return dict(
            launches=len(calls),
            finalized=len(calls),
            remaining_worker_seconds=(
                0
                if calls and outcome == "budget_exhausted"
                else m.LIMITS["total_worker_seconds"]
            ),
            attempts=[dict(worker_classification=outcome) for _ in calls],
        )

    def supervise(argv, directory, execution_root, req):
        calls.append(req["call_id"])
        assert argv[-1] == str(execution_root) and directory.exists()
        assert req["wall_seconds"] == m.LIMITS["wall_seconds"]
        return dict(
            classification=outcome
            if outcome in {"wall_limit", "rss_limit"}
            else "exited",
            returncode=0,
        )

    monkeypatch.setattr(r, "status", progress)
    monkeypatch.setattr(r.q, "supervise", supervise)
    result = r.run(root, "a" * 40)
    assert result["outcome"] == expected
    assert calls == list(range(1, launches + 1))
    assert r.q.read(root / "invocation-finish.json")["outcome"] == expected


def test_real_supervisor_accepts_transfer_protocol_without_optimizer(
    monkeypatch, tmp_path
):
    from experiments.case118_tracy_2021 import run_e3 as supervision

    _, _, directory, req = journal(monkeypatch, tmp_path)

    class Process:
        pid = 12345

        def __init__(self):
            self.polls = 0

        def poll(self):
            self.polls += 1
            return None if self.polls == 1 else 0

        def wait(self):
            return 0

    process = Process()
    monkeypatch.setattr(supervision.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(supervision.time, "sleep", lambda _: None)
    result = supervision.supervise(
        ["synthetic-worker"], directory, tmp_path, req, rss_reader=lambda _: 4.0
    )
    assert result["classification"] == "exited" and result["returncode"] == 0
    assert result["samples"] == 1 and result["peak_sampled_rss_mib"] == 4.0
    assert r.q.resource_evidence(directory, result)


def test_retained_six_scenario_schedule_inventory():
    if not (TRACY / "results/e3_plus/progress.json").exists():
        pytest.skip("optional owner-retained E3 and E3+ evidence unavailable")
    data = m.source_inventory()
    assert len(m.jobs(data)) == 18
    storage_table = data["tables"]["batteries"]
    storage = [
        SimpleNamespace(
            initial_soc=0.0,
            apparent_power_rating=row.apparent_power_rating_mva,
            capacity=row.capacity_mwh,
        )
        for row in storage_table.itertuples()
    ]
    for job in m.jobs(data):
        record = data["cases"][job["scenario"]][job["donor"]]
        devices = [
            SimpleNamespace(
                initial_soc=initial,
                apparent_power_rating=s.apparent_power_rating,
                capacity=s.capacity,
            )
            for initial, s in zip(
                record["arm"]["initial_soc_mwh"], storage, strict=True
            )
        ]
        _, checks = m.schedule_check(
            record["result"]["b"], devices, 1.0, record["arm"]["terminal_soc_mwh"]
        )
        assert checks["passed"]
        assert checks["endpoint_difference_mwh"] < 3e-8
