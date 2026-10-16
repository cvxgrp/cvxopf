"""E4 non-solving contracts, prepared handoff, race integration and replay.

Every test blocks native solver invocation. Retained results are fixtures, not
new optimization results. Synthetic audit successes are explicitly mocked.
"""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import cvxpy as cp
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
import numpy as np
import pandas as pd
import pytest

from cvxopf import OPFOptions, NondispatchableUnit
from cvxopf._ac_rollout import RetainedEnergyPlan, RolloutCursor, request_window, advance_cursor
from cvxopf._prepared_rollout import physical_snapshot, window_kwargs, starting_values, bound_shedding_start, physical_solution
from experiments.case118_tracy_2021 import e3_prepared
from experiments.case118_tracy_2021.stage_d import read
from experiments.case118_annual_hierarchy.s5_speculative_policy import WindowKey, AttemptSpec, Completion
from experiments.case118_annual_hierarchy.s5_speculative_transaction import advance_from_winner
from experiments.case118_annual_hierarchy.streaming_schema import atomic_json
from experiments.tracy_ac_rollout import model, audit, run, runtime, worker
from tests.test_hierarchical_solver import _inputs
from tests.test_case118_s5_speculative_supervisor import FakeBackend

PROBLEM_SOLVE = cp.Problem.solve


@pytest.fixture(autouse=True)
def no_native_solve(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("test must not launch an optimizer")
    import clarabel
    import cyipopt
    monkeypatch.setattr(clarabel, "DefaultSolver", forbidden)
    monkeypatch.setattr(cyipopt, "Problem", forbidden)
    monkeypatch.setattr(cp.Problem, "solve", forbidden)
    monkeypatch.setattr(IPOPT, "solve_via_data", forbidden)


def energy_plan(T=5, ids=("z", "a")):
    return RetainedEnergyPlan("p", "socp", "1" * 64, "2" * 40, "accepted", "accepted",
        "3" * 64, (), "4" * 64, 2439, T, .5, ids,
        np.tile([500., 120.][:len(ids)], (T + 1, 1)))


def small_kwargs(T=5):
    source = _inputs(horizon_steps=T)
    loads = [replace(x, shedding_cost_per_mwh=1000., max_shed_fraction=.25) for x in source.loads]
    return dict(case=source.case, T=T, formulation="ac", temporal_assembly="vectorized",
        automatic_sparse_dispatch=False, options=OPFOptions(numerical_preparation=e3_prepared.policy("ac", "component_first")),
        delta=source.delta, generators=source.generators, loads=loads, storage=source.storage,
        nondispatchable=[NondispatchableUnit(5, 10., 20., device_id="nd")],
        df_load_p=source.df_load_p, df_load_q=source.df_load_q,
        df_nd=pd.DataFrame({"nd": [10.] * T}))


def small_request(k, T=5):
    return SimpleNamespace(storage_device_ids=("battery-7",), local_start=k, local_stop=min(k + 3, T),
        initial_soc_mwh=np.array([500.]), target_soc_mwh=np.array([500.]))


def test_owned_energy_alignment_and_exact_final_obligation():
    source = np.tile([500., 120.], (6, 1))
    plan = replace(energy_plan(), boundary_soc_mwh=source)
    source[1] = 999
    assert plan.boundary_soc_mwh[1, 0] == 500
    with pytest.raises(ValueError):
        plan.boundary_soc_mwh[1, 0] = 0
    with pytest.raises(ValueError):
        plan.boundary_soc_mwh.flags.writeable = True
    aligned = plan.aligned(("a", "z"), [300, 1000], [120, 500], [120, 500],
                          box_tolerance=1e-4, endpoint_tolerance=.001)
    np.testing.assert_array_equal(aligned.boundary_soc_mwh[0], [120, 500])
    perturbed_endpoint = aligned.boundary_soc_mwh.copy()
    perturbed_endpoint[-1] += .0001
    aligned = replace(aligned, boundary_soc_mwh=perturbed_endpoint)
    cursor = RolloutCursor(3, ("a", "z"), [115., 505.], "arm/ac-2")
    request = request_window(aligned, cursor, [120., 500.], arm_id="arm")
    assert (request.local_start, request.local_stop, request.global_start, request.global_stop) == (3, 5, 2442, 2444)
    np.testing.assert_array_equal(request.initial_soc_mwh, [115., 505.])
    np.testing.assert_array_equal(request.target_soc_mwh, [120., 500.])
    assert not np.array_equal(request.target_soc_mwh, request.source_endpoint_soc_mwh)
    assert request.target_kind == "original_final_obligation"


@pytest.mark.parametrize("ids", [("z", "z"), ("z",), ("z", "a", "x")])
def test_source_identity_mismatch_rejected(ids):
    with pytest.raises(ValueError):
        energy_plan().aligned(ids, [1000] * len(ids), [500] * len(ids), [500] * len(ids),
                              box_tolerance=1e-4, endpoint_tolerance=.001)


@pytest.mark.parametrize("change", [dict(delta_hours=0), dict(global_start=True), dict(interval_count=0),
                                    dict(source_use_disposition="accept_inaccurate"), dict(boundary_soc_mwh=[[np.nan]])])
def test_bad_plan_structure_rejected(change):
    with pytest.raises(ValueError):
        replace(energy_plan(), **change)


def test_all_24_targets_and_only_accepted_first_actions_advance():
    plan = energy_plan(24)
    cursor = RolloutCursor(0, plan.storage_device_ids, [500., 120.])
    horizons = []
    for k in range(24):
        request = request_window(plan, cursor, [500., 120.], arm_id="arm")
        horizons.append(request.local_stop - request.local_start)
        assert request.local_start == k
        unchanged = cursor.realized_soc_mwh.copy()
        args = dict(attempt_id=f"a-{k}", first_b_mw=[0, 0], solved_next_soc_mwh=unchanged,
                    delta_hours=.5, recurrence_tolerance=1e-4)
        for accepted, hard in ((False, True), (True, False)):
            with pytest.raises(ValueError, match="unaccepted"):
                advance_cursor(cursor, request, accepted=accepted, hard_target=hard, **args)
        cursor = advance_cursor(cursor, request, accepted=True, hard_target=True, **args)
        with pytest.raises(ValueError):
            advance_cursor(cursor, request, accepted=True, hard_target=True, **args)
    assert horizons == [3] * 22 + [2, 1]
    with pytest.raises(ValueError, match="coverage"):
        request_window(plan, cursor, [500., 120.], arm_id="arm")


def test_recurrence_mismatch_never_advances():
    plan = energy_plan()
    cursor = RolloutCursor(0, plan.storage_device_ids, [500., 120.])
    req = request_window(plan, cursor, [500., 120.], arm_id="arm")
    with pytest.raises(ValueError, match="disagrees"):
        advance_cursor(cursor, req, attempt_id="bad", accepted=True, hard_target=True,
            first_b_mw=[1., 0.], solved_next_soc_mwh=[500., 120.], delta_hours=.5, recurrence_tolerance=1e-4)
    assert cursor.completed_intervals == 0


@pytest.mark.parametrize("k", [0, 1, 3, 4])
def test_window_storage_replacement_and_tail_without_power_pins(k):
    kwargs = small_kwargs()
    snapshot = physical_snapshot(kwargs)
    req = small_request(k)
    build, actual = window_kwargs(snapshot, req)
    assert build.temporal_assembly == "vectorized" and build.data["T"] == min(3, 5 - k)
    assert actual["storage"][0].terminal_constraint == "equality"
    assert actual["storage"][0].terminal_soc == 500
    np.testing.assert_array_equal(actual["df_load_p"], kwargs["df_load_p"].iloc[k:min(k + 3, 5)])
    assert build.variables["b"].shape == (1, min(3, 5 - k))
    free, free_kwargs = window_kwargs(snapshot, req, hard_target=False)
    assert free_kwargs["storage"][0].terminal_soc is None
    assert free_kwargs["storage"][0].terminal_constraint is None
    assert build.variables.keys() == free.variables.keys()
    assert len(build.prob.constraints) > len(free.prob.constraints)
    kwargs["df_load_p"].iloc[0, 0] = 999
    assert snapshot.df_load_p.iloc[0, 0] != 999


@pytest.mark.parametrize("mutate", [lambda k: k.update(coupling_constraints=[]), lambda k: k.update(hvdc=[]),
    lambda k: k.update(storage=[]), lambda k: k.update(temporal_assembly="stepwise"),
    lambda k: k.update(loads=[replace(x, shedding_cost_per_mwh=None) for x in k["loads"]]),
    lambda k: k.update(options=OPFOptions(enforce_branch_limits=False)),
    lambda k: k.update(df_load_p=k["df_load_p"].iloc[:, ::-1])])
def test_private_capability_fails_before_construction(mutate):
    kwargs = small_kwargs()
    mutate(kwargs)
    with pytest.raises(ValueError):
        physical_snapshot(kwargs)


def test_public_hierarchy_guards_still_reject_preparation_and_shedding():
    inputs = _inputs()
    with pytest.raises(ValueError, match="prepared hierarchical"):
        replace(inputs, options=OPFOptions(numerical_preparation=e3_prepared.policy("ac", "component_first")))
    with pytest.raises(ValueError, match="sheddable"):
        replace(inputs, loads=tuple(replace(x, shedding_cost_per_mwh=1000.) for x in inputs.loads))


@pytest.mark.parametrize("T", [3, 2, 1])
def test_shifted_fraction_caps_and_new_cost_weights(T):
    kwargs = small_kwargs()
    snapshot = physical_snapshot(kwargs)
    source, _ = window_kwargs(snapshot, small_request(0))
    _, assigned = starting_values(source, snapshot, small_request(0), source_slot=0)
    assigned["load_shed_fraction"][:] = .9
    from cvxopf import _hierarchical_solver as shared
    shared._assign_start(source, assigned)
    logical = physical_solution(source)
    req = small_request(0)
    req.local_stop = T
    snapshot = replace(snapshot, df_load_p=snapshot.df_load_p * 2)
    destination, _ = window_kwargs(snapshot, req)
    raw, shifted = starting_values(destination, snapshot, req, source_slot=0, preceding=logical)
    assert np.max(shifted["load_shed_fraction"]) <= .25
    assert np.max(raw["load_shed_fraction"]) == .9
    np.testing.assert_array_equal(shifted["soc"][:, 0], req.initial_soc_mwh)
    captured = e3_prepared.prepared_start(destination, 0)
    maps = [m for m in captured["cost_maps"] if "shedd" in m["kind"]]
    assert len(maps) == 1
    scale = np.asarray(maps[0]["scale"])
    p = destination.expressions["p_load"].value
    np.testing.assert_array_equal(scale, np.where(p > 0, 1000. * p, 1.))


def test_subset_fraction_mapping_with_zero_negative_demand_and_reactive_signs():
    kwargs = small_kwargs(3)
    kwargs["loads"] = [replace(x, shedding_cost_per_mwh=None) if i % 2 == 0 else x for i, x in enumerate(kwargs["loads"])]
    kwargs["df_load_p"].iloc[:, 1] = [10., 0., -10.]
    kwargs["df_load_q"].iloc[:, 1] = [-5., 0., 5.]
    from cvxopf import build_opf_multistep
    build = build_opf_multistep(**kwargs)  # reusable start helper supports broader loads; E4 admission does not
    from cvxopf import _hierarchical_solver as shared
    values = shared._complete_start(build)
    values["load_shed_fraction"][:] = .8
    adjusted = bound_shedding_start(build, values)
    assert adjusted["load_shed_fraction"].shape[0] != len(kwargs["loads"])
    np.testing.assert_array_equal(adjusted["load_shed_fraction"][0], [.25, 0., 0.])
    np.testing.assert_array_equal(build.expressions["q_load"].value[1], [-5., 0., 5.])


def test_target_free_copy_and_perturbations_keep_initial_soc_fixed():
    snapshot = physical_snapshot(small_kwargs())
    request = small_request(0)
    free, _ = window_kwargs(snapshot, request, hard_target=False)
    starting_values(free, snapshot, request, source_slot=1)
    logical = physical_solution(free)
    hard, _ = window_kwargs(snapshot, request)
    for slot in (2, 3, 6):
        raw, assigned = starting_values(hard, snapshot, request, source_slot=slot,
            scale=1e-3, seed=17000011, target_free=logical, preceding=logical)
        np.testing.assert_array_equal(assigned["soc"][:, 0], [500.])
        np.testing.assert_array_equal(raw["soc"][:, 0], [500.])
    with pytest.raises(ValueError, match="target-free"):
        starting_values(hard, snapshot, request, source_slot=2)
    with pytest.raises(ValueError, match="preceding"):
        starting_values(hard, snapshot, request, source_slot=6)


def retained_inventory(required_paths):
    """Skip unavailable owner inputs, never broken published evidence."""
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        pytest.skip("owner retained Tracy inputs unavailable: " + ", ".join(missing))
    return model.inventory()


@pytest.fixture(scope="module")
def retained():
    from experiments.case118_tracy_2021.prepare import SOURCE

    return retained_inventory((
        SOURCE,
        model.e3_plus.FIXTURE_PATH,
        model.ROOT / "experiments/case118_tracy_2021/results/e3_prepared/binding.json",
        model.ROOT / "experiments/case118_tracy_2021/results/e3_plus/binding.json",
        model.ROOT / "experiments/tracy_battery_transfer/results/transfer_001/binding.json",
    ))


@pytest.mark.parametrize("missing_index", range(5))
def test_retained_fixture_skips_missing_admission_input(tmp_path, monkeypatch, missing_index):
    required = [tmp_path / f"input-{i}" for i in range(5)]
    for index, path in enumerate(required):
        if index != missing_index:
            path.write_text("present")
    monkeypatch.setattr(model, "inventory", lambda: pytest.fail("missing input must skip before admission"))
    with pytest.raises(pytest.skip.Exception, match=str(required[missing_index])):
        retained_inventory(required)


@pytest.mark.parametrize("damage", ("malformed_json", "hash_mismatch", "missing_nested_file"))
def test_retained_fixture_does_not_skip_corrupt_present_evidence(tmp_path, monkeypatch, damage):
    from experiments.case118_tracy_2021 import e3_results

    directory = tmp_path / "e3_prepared"
    directory.mkdir()
    binding = directory / "binding.json"
    binding.write_text("not JSON")
    required = (binding, *(tmp_path / f"input-{i}" for i in range(4)))
    for path in required[1:]:
        path.write_text("present")
    monkeypatch.setattr(model.transfer, "load_scenarios", lambda: e3_results.load_scenarios(tmp_path))
    if damage == "malformed_json":
        error, message = json.JSONDecodeError, "Expecting value"
    else:
        binding.write_text(json.dumps(dict(context=dict(
            cohort="prepared", stage_a_manifest_sha256="0" * 64))))
        if damage == "missing_nested_file":
            error, message = FileNotFoundError, "progress.json"
        else:
            (directory / "progress.json").write_text(json.dumps(dict(
                disposed_arms=20, accepted_arms=20, finished=True,
                active_attempt=None, arms=[{}] * 20)))
            (tmp_path / "stage_a").mkdir()
            (tmp_path / "stage_a/manifest.json").write_text("{}")
            monkeypatch.setattr(e3_results, "HERE", tmp_path)
            error, message = ValueError, "Stage A manifest mismatch"
    # Exercise the real inventory/reader chain; no owner artifacts are changed.
    with pytest.raises(error, match=message):
        retained_inventory(required)


def test_real_24_arm_cohort_preserves_flags_and_physical_matches(retained):
    manifest, data, prepared = retained
    assert len(manifest["arms"]) == 24
    assert len({row["scenario"] for row in manifest["arms"]}) == 6
    assert len([row for row in manifest["arms"] if row["plan"]["source_use_disposition"] == "owner_approved_qualified"]) == 3
    for row in manifest["arms"]:
        assert model.plan_for(row).boundary_soc_mwh.shape == (25, 27)
        assert model.cursor_for(row).completed_intervals == 0
    assert model.CONFIG.primary_workers == 2 and model.CONFIG.helper_workers == 1
    assert len(manifest["fixed_transfer_outcomes"]) == 18
    assert sum(x["retained_compatible"] for x in manifest["fixed_transfer_outcomes"]) == 15


def test_non_solving_24_hour_mock_rollout_and_executed_only_accounting(retained):
    manifest, data, prepared = retained
    row = next(row for row in manifest["arms"] if row["source"] == "ac_reference")
    original = model.original_kwargs(data["cases"][row["scenario"]]["ac"], prepared)
    retained_result = data["cases"][row["scenario"]]["ac"]["result"]
    cursor = model.cursor_for(row)
    actions = []
    for k in range(24):
        _, kwargs, _, request = model.construct(row, cursor, data, prepared)
        window_result = {name: np.asarray(retained_result[name])[k:request.local_stop] for name in audit.TIME_FIELDS}
        # Synthetic accepted executor output using already retained physical
        # points. This is an integration fixture, not a new native acceptance.
        record = dict(classification="accepted", independent_audit=dict(passed=True), hard_target=True,
                      result=window_result)
        cursor, action = audit.first_action(record, cursor, request, kwargs, f"fixture-{k}")
        actions.append(action["result"])
        if k == 3:
            prefix = audit.trajectory(actions, original, complete=False)
            assert prefix["completed_intervals"] == 4 and not prefix["complete"]
    complete = audit.trajectory(actions, original, complete=True)
    reference = data["cases"][row["scenario"]]["ac"]["audit"]
    assert complete["audit"]["costs"] == pytest.approx(reference["costs"])
    assert complete["audit"]["metrics"]["energy_not_served_mwh"] == pytest.approx(reference["metrics"]["energy_not_served_mwh"])
    assert cursor.completed_intervals == 24


def test_historical_commit_only_change_allowed_but_execution_rejected():
    old = dict(commit="a" * 40, clean=True, sources={"x.py": "b" * 64}, packages={"cvxpy": "x"})
    new = dict(old, commit="c" * 40)
    run.validate_context(old, new, execution=False)
    with pytest.raises(ValueError, match="exact frozen"):
        run.validate_context(old, new, execution=True)
    with pytest.raises(ValueError, match="sources/environment"):
        run.validate_context(old, dict(new, sources={"x.py": "d" * 64}), execution=False)


def test_explicit_budget_and_authority_gates(tmp_path):
    with pytest.raises(ValueError):
        runtime.ResourceBudgets(True, 1, 1, 1)
    with pytest.raises(ValueError):
        runtime.ResourceBudgets(2, float("inf"), 1, 1)
    binding = dict(context=dict(clean=True, commit="a" * 40), budgets=asdict(runtime.ResourceBudgets(10, 100, 20, 20)))
    with pytest.raises(ValueError, match="authority"):
        run.verify_authority(binding, {}, "b" * 64)
    with pytest.raises(ValueError, match="new named"):
        run.validate_output(tmp_path)
    with pytest.raises(SystemExit):
        run.main(["--run", "--output", str(model.HERE / "results/not_launched")])
    assert not (model.HERE / "results/not_launched").exists()


@pytest.mark.parametrize("sample", ["not-json", '{"temp":{}}',
    '{"temp":{"cpu_temp_avg":0,"gpu_temp_avg":45}}',
    '{"temp":{"cpu_temp_avg":NaN,"gpu_temp_avg":45}}'])
def test_thermal_failure_is_advisory_not_a_process_stop(monkeypatch, sample):
    monkeypatch.setattr(runtime, "process_observations", lambda: ())
    monkeypatch.setattr(runtime.subprocess, "check_output",
        lambda argv, **kwargs: sample if argv[0] == "macmon" else "AC Power")
    value = runtime.telemetry()
    assert value["thermal"] is None and value["thermal_status"] == "unavailable"
    assert "advisory" in value["alerts"][0]
    assert value["battery"] == "AC Power"


def test_process_monitoring_is_still_required(monkeypatch):
    def denied():
        raise PermissionError("ps unavailable")
    monkeypatch.setattr(runtime, "process_observations", denied)
    with pytest.raises(PermissionError):
        runtime.telemetry()


def test_thermal_command_failure_and_battery_failure_are_advisory(monkeypatch):
    monkeypatch.setattr(runtime, "process_observations", lambda: ())
    def missing(*args, **kwargs):
        raise FileNotFoundError("telemetry command unavailable")
    monkeypatch.setattr(runtime.subprocess, "check_output", missing)
    value = runtime.telemetry()
    assert value["thermal"] is None and value["battery"] is None
    assert len(value["alerts"]) == 2


def test_continuation_restores_controllers_and_consumed_budget_without_launch(tmp_path):
    from experiments.tracy_ac_rollout import continuation
    spec = AttemptSpec(WindowKey("arm-000-component_first", 0), 0, 0)
    directory = tmp_path / "attempts/arm-000-component_first/ac-000000-spec-00"
    atomic_json(directory / "record.json", dict(invocation=asdict(spec)))
    selected = dict(path=str((directory / "record.json").relative_to(tmp_path)), sha256="a" * 64)
    atomic_json(tmp_path / "arms/arm-000/window.json", dict(selected_record=selected))
    binding = dict(initial_checkpoints={"arm-000":dict(windows=[dict(relative_path="window.json")])},
                   inherited_launches=43, inherited_worker_seconds=1398.5)
    backend = runtime.RolloutBackend(tmp_path, runtime.ResourceBudgets(43, 172800., 300., 300.),
        command=lambda *args: pytest.fail("must not launch"), audit=lambda *args: None,
        publish=lambda *args: None, advance=lambda *args: None)
    continuation.restore_backend(tmp_path, backend, binding)
    assert backend.total_launches() == 43
    assert backend.effort() == 1398.5
    assert backend.children[spec.attempt_id].reaped
    assert backend.children[spec.attempt_id].completion.outcome == "accepted"
    from experiments.case118_annual_hierarchy.s5_speculative_supervisor import ResourceStop
    with pytest.raises(ResourceStop, match="launch ceiling"):
        backend.launch(AttemptSpec(WindowKey("arm-000-component_first", 1), 0, 0))


def test_operational_historical_context_does_not_exempt_numerical_sources():
    old = dict(clean=True, commit="a", sources={
        "experiments/tracy_ac_rollout/runtime.py":"old", "src/cvxopf/storage.py":"fixed"}, env="same")
    new = dict(old, commit="b", sources={
        "experiments/tracy_ac_rollout/runtime.py":"reviewed-new", "src/cvxopf/storage.py":"fixed"})
    run.validate_context(old, new, execution=False)
    with pytest.raises(ValueError):
        run.validate_context(old, new, execution=True)
    new["sources"]["src/cvxopf/storage.py"] = "changed"
    with pytest.raises(ValueError):
        run.validate_context(old, new, execution=False)


def test_continuation_preserves_and_checks_immutable_predecessor(tmp_path):
    from experiments.tracy_ac_rollout import continuation
    atomic_json(tmp_path / "binding.json", dict(original=True))
    atomic_json(tmp_path / "outcome.json", dict(classification="stopped"))
    atomic_json(tmp_path / "arms/arm-000/checkpoint.json", dict(completed_intervals=1))
    before = continuation.retained_files(tmp_path)
    assert "arms/arm-000/checkpoint.json" not in before
    receipt = dict(retained_files=before, run_binding_sha256=e3_prepared.digest(tmp_path / "binding.json"))
    atomic_json(tmp_path / "arms/arm-000/checkpoint.json", dict(completed_intervals=2))
    continuation.verify_retained(tmp_path, receipt)
    atomic_json(tmp_path / "outcome.json", dict(classification="changed"))
    with pytest.raises(ValueError, match="predecessor evidence changed"):
        continuation.verify_retained(tmp_path, receipt)


def test_experiment_exhaustion_hook_preserves_peer_primary(tmp_path, monkeypatch):
    backend = FakeBackend(tmp_path)
    def launch(attempt):
        backend.running[attempt.attempt_id] = attempt
        backend.starts[attempt.attempt_id] = backend.now
    monkeypatch.setattr(backend, "launch", launch)
    supervisor = runtime.RolloutSupervisor(backend)
    first, peer = WindowKey("arm-000-component_first", 0), WindowKey("arm-001-component_first", 0)
    supervisor.add_window(first, has_preceding=False, now=0)
    supervisor.add_window(peer, has_preceding=False, now=0)
    race = supervisor.races[first]
    # Synthetic terminal state with primary already reaped, no winner.
    completion = Completion(race.primary, "rejected", True)
    race.complete_batch([completion])
    backend.reap(race.primary)
    supervisor.active.pop(race.primary.attempt_id)
    race._next_order, race._replay_considered = 9, True
    race._final_started, race._final_next_index = True, 8
    assert race.exhausted
    assert supervisor.tick(1) == (first,)
    assert not supervisor.stopped
    assert supervisor.races[peer].primary.attempt_id in supervisor.active
    assert not any(kind in {"lost_race", "supervisor_stopped"} for kind, _ in backend.log)


@pytest.mark.parametrize("T", [3, 2, 1])
@pytest.mark.parametrize("phase", ["component_first", "hourly"])
def test_production_prepared_boundary_intercept_no_optimizer_and_unusable_cleanup(monkeypatch, T, phase):
    snapshot = physical_snapshot(small_kwargs())
    request = small_request(0)
    request.local_stop = T
    build, _ = window_kwargs(snapshot, request, preparation=e3_prepared.policy("ac", phase))
    starting_values(build, snapshot, request, source_slot=0)
    captured = e3_prepared.prepared_start(build, 0)
    seen = []
    def fake_native(self, data, *args, **kwargs):
        seen.append(data["x0"].copy())
        return dict(status=-1, x=data["x0"].copy(), obj_val=1., num_iters=0)
    # Exercise the real prepared graph and verified handoff, intercepting before
    # IPOPT instantiation/optimization. There is no native solver delegation.
    monkeypatch.setattr(cp.Problem, "solve", PROBLEM_SOLVE)
    monkeypatch.setattr(IPOPT, "solve_via_data", fake_native)
    build.solve(solver="IPOPT", **dict(model.CONFIG.ac.options))
    assert len(seen) == 1
    e3_prepared.verify_prepared_start(build.preparation_evidence, captured)
    np.testing.assert_array_equal(seen[0], build.preparation_evidence.reduced_x0)
    assert all(variable.value is None for variable in build.prob.variables())
    assert not build.preparation_evidence.checks["restoration_available"]
    assert len(captured["cost_maps"]) == 2


@pytest.mark.parametrize("native_status", [-1, 0])
@pytest.mark.parametrize("continued", [False, True])
def test_supervised_worker_archives_acceptance_or_rejection_and_replays_without_solving(tmp_path, monkeypatch, native_status, continued):
    import os
    root = tmp_path
    spec = AttemptSpec(WindowKey("arm-000-component_first", 0), 0, 0)
    base = root / "continuations/continuation_001" if continued else root
    directory = base / "attempts/arm-000-component_first/ac-000000-spec-00"
    directory.mkdir(parents=True)
    from tests.test_tracy_stage_d import fixture
    from cvxopf import _hierarchical_solver as shared
    kwargs, original, _, _ = fixture(3)
    kwargs["options"] = OPFOptions(numerical_preparation=e3_prepared.policy("ac", "component_first"))
    kwargs["automatic_sparse_dispatch"] = False
    assigned_fixture = shared._solution_values(original)
    row = dict(arm_id="arm-000", plan=asdict(replace(energy_plan(T=3, ids=("battery",)),
        boundary_soc_mwh=np.full((4, 1), 5.))), initial_soc_mwh=[5.], final_soc_mwh=[5.])
    cursor = model.cursor_for(row)
    request = model.window_for(row, cursor)
    snapshot = physical_snapshot(kwargs)
    def construct(*args, **kwargs):
        build, kw = window_kwargs(snapshot, request)
        return build, kw, snapshot, request
    monkeypatch.setattr(model, "construct", construct)
    def analytic_start(build, *args, **kwargs):
        # Explicit test-only analytic primal, not the study's primary start.
        shared._assign_start(build, assigned_fixture)
        return assigned_fixture, assigned_fixture
    monkeypatch.setattr(worker, "starting_values", analytic_start)
    monkeypatch.setattr(runtime, "starting_values", analytic_start)
    build, _, _, _ = construct()
    _, assigned = analytic_start(build)
    captured = e3_prepared.prepared_start(build, 0)
    binding = dict(manifest=dict(arms=[row]), first_windows=[dict(arm_id="arm-000",
        window=model.serializable(asdict(request)), start_sha256=model.transfer.object_hash(assigned),
        canonical_start_sha256=model.transfer.object_hash(captured))])
    atomic_json(root / "binding.json", model.serializable(binding))
    cp_state = dict(completed_intervals=0, storage_device_ids=["battery"], realized_soc_mwh=[5.],
                    preceding_controlling_attempt_id=None)
    req = dict(invocation=asdict(spec), arm_id="arm-000", phase="component_first", checkpoint=cp_state,
               sources=dict(preceding=None, target_free=None, replay=None), binding_sha256=e3_prepared.digest(root / "binding.json"))
    if continued:
        atomic_json(base / "authority.json", dict(test_only=True))
        req.update(continuation_generation=base.name,
                   continuation_authority_sha256=e3_prepared.digest(base / "authority.json"))
    atomic_json(directory / "request.json", req)
    atomic_json(directory / "launch.json", dict(pid=os.getpid(), invocation=asdict(spec)))
    atomic_json(directory / "monitoring.json", dict(test_only=True))
    monkeypatch.setattr(run, "verify_binding", lambda *a, **k: (binding, None, None))
    monkeypatch.setattr(cp.Problem, "solve", PROBLEM_SOLVE)
    def fake_native(self, data, *args, solver_cache=None, **kwargs):
        point = data["x0"].copy()
        oracle = solver_cache["oracles"]
        return dict(status=native_status, status_msg=b"synthetic analytic native return", x=point,
            obj_val=oracle.objective(point), num_iters=0, mult_g=np.zeros(len(data["cl"])),
            mult_x_L=np.zeros(len(point)), mult_x_U=np.zeros(len(point)))
    monkeypatch.setattr(IPOPT, "solve_via_data", fake_native)
    worker.run(root, directory)
    record, _, _ = runtime.checked_record(directory, spec, row, None, None, supervised=False)
    assert record["native"]["status"] == native_status
    assert (record["classification"] == "accepted") == (native_status == 0)
    assert record["independent_audit"]["passed"] == (native_status == 0)
    if native_status == -1:
        assert record["result"] is None and record["logical_solution"] is None
    assert [x["phase"] for x in read(directory / "phase.json")["events"]] == [
        "before_ac_build", "after_ac_build", "before_ac_solve", "after_ac_solve"]
    with pytest.raises(FileNotFoundError):
        runtime.checked_record(directory, spec, row, None, None, supervised=True)
    resources = dict(samples=[dict(monotonic_seconds=1., rss_mib=10.)], peak_rss_mib=10.)
    atomic_json(directory / "resources.json", resources)
    atomic_json(directory / "supervision.json", dict(reason="returned", reaped=True, returncode=0, ended_utc="test-only",
        resources_sha256=e3_prepared.digest(directory / "resources.json"),
        completion_sha256=e3_prepared.digest(directory / "completion.json")))
    replayed, _, _ = runtime.checked_record(directory, spec, row, None, None, supervised=True)
    assert replayed == record
    if native_status == 0:
        next_cursor, action = audit.first_action(record, cursor, request, kwargs, spec.attempt_id)
        assert next_cursor.completed_intervals == 1
        assert action["audit"]["metrics"]["energy_not_served_mwh"] == pytest.approx(1.)
        assert record["audit"]["metrics"]["energy_not_served_mwh"] == pytest.approx(3.)


@pytest.mark.parametrize("failed_arm", [None, "arm-000"])
@pytest.mark.parametrize("resume_prefix", [0, 2])
def test_synthetic_24_arm_two_lane_schedule_uses_no_subprocesses(tmp_path, monkeypatch, failed_arm, resume_prefix):
    from cvxopf._ac_rollout import RolloutCursor
    launches, advanced, stopped = [], [], []
    rows = [dict(arm_id=f"arm-{i:03d}", plan=dict(plan_id=f"p-{i}", storage_device_ids=["b"]),
                 initial_soc_mwh=[1.], final_soc_mwh=[1.]) for i in range(24)]
    binding = dict(manifest=dict(arms=rows), budgets=asdict(runtime.ResourceBudgets(2000, 1e6, 100, 100)))
    atomic_json(tmp_path / "binding.json", binding)
    atomic_json(tmp_path / "synthetic/supervision.json", dict(reaped=True, test_only=True))
    class SimulatedBackend(FakeBackend):
        def __init__(self, root, budgets, **callbacks):
            super().__init__(root)
            self.children = {}
            self.budgets = budgets
            self.finalized_effort = 0.
            self.callbacks = callbacks
        def launch(self, spec):
            launches.append(spec)
            self.running[spec.attempt_id] = spec
            self.starts[spec.attempt_id] = 0.
            self.children[spec.attempt_id] = SimpleNamespace(directory=tmp_path / "synthetic")
            failed = spec.window.shard_id.startswith(f"{failed_arm}-") and spec.window.iteration == 3
            rejected = failed and spec.hard_target
            self.ready[spec.attempt_id] = Completion(spec, "rejected" if rejected else "accepted", True,
                .1 if rejected else None, 1. if rejected else None)
            assert len(self.running) <= 3
        def effort(self):
            return 0.
        def publish_winner(self, spec, attempts):
            self.callbacks["publish"](spec, attempts, {spec.attempt_id: tmp_path / "synthetic"})
        def advance_checkpoint(self, spec):
            assert not any(x.window == spec.window for x in self.running.values())
            self.callbacks["advance"](spec, {spec.attempt_id: tmp_path / "synthetic"})
            advanced.append(spec)
        def cancel_and_reap(self, spec, reason):
            stopped.append(spec)
            super().cancel_and_reap(spec, reason)
    monkeypatch.setattr(runtime, "RolloutBackend", SimulatedBackend)
    monkeypatch.setattr(runtime, "telemetry", lambda: dict(test_only=True))
    monkeypatch.setattr(runtime.time, "sleep", lambda *args: None)
    monkeypatch.setattr(model, "cursor_for", lambda row, cp: RolloutCursor(cp["completed_intervals"], ("b",),
        cp["realized_soc_mwh"], cp["preceding_controlling_attempt_id"]))
    def checked(directory, spec, row, *args, **kwargs):
        request = SimpleNamespace(local_start=spec.window.iteration, local_stop=min(spec.window.iteration + 3, 24),
            global_start=spec.window.iteration, global_stop=min(spec.window.iteration + 3, 24),
            storage_device_ids=("b",), initial_soc_mwh=np.array([1.]), target_soc_mwh=np.array([1.]))
        # publish serializes a dataclass: use the actual request contract.
        from cvxopf._ac_rollout import ACWindowRequest
        request = ACWindowRequest(row["arm_id"], row["plan"]["plan_id"], request.local_start, request.local_stop,
            request.global_start, request.global_stop, ("b",), [1.], [1.], [1.], "retained_boundary")
        return dict(request=dict(phase="component_first")), dict(delta=1.), request
    monkeypatch.setattr(runtime, "checked_record", checked)
    def action(record, cursor, request, kwargs, attempt_id):
        next_cursor = advance_cursor(cursor, request, attempt_id=attempt_id, accepted=True, hard_target=True,
            first_b_mw=[0.], solved_next_soc_mwh=[1.], delta_hours=1., recurrence_tolerance=1e-4)
        return next_cursor, dict(controlling_attempt_id=attempt_id, test_only_synthetic=True)
    monkeypatch.setattr(audit, "first_action", action)
    monkeypatch.setattr(runtime, "reference", lambda *args: dict(path="synthetic", sha256="a" * 64))
    receipt = None
    if resume_prefix:
        checkpoints = {}
        for i, row in enumerate(rows):
            count = resume_prefix if i < 2 else 0
            entries = []
            previous = None
            for k in range(count):
                spec = AttemptSpec(WindowKey(f'{row["arm_id"]}-component_first', k), 0, 0)
                record_path = tmp_path / "retained" / f"{i}-{k}.json"
                atomic_json(record_path, dict(invocation=asdict(spec)))
                window_path = tmp_path / "arms" / row["arm_id"] / f"retained-{k}.json"
                atomic_json(window_path, dict(selected_record=dict(path=str(record_path.relative_to(tmp_path)))))
                entries.append(dict(iteration=k, relative_path=window_path.name,
                    bytes=window_path.stat().st_size, sha256=e3_prepared.digest(window_path)))
                previous = spec.attempt_id
            cp = dict(shard_id=row["arm_id"], plan_id=row["plan"]["plan_id"],
                binding_sha256=e3_prepared.digest(tmp_path / "binding.json"), interval=dict(start=0, stop=24),
                completed_intervals=count, next_global_iteration=count, complete=False, windows=entries,
                storage_device_ids=["b"], realized_soc_mwh=[1.], preceding_controlling_attempt_id=previous)
            atomic_json(tmp_path / "arms" / row["arm_id"] / "checkpoint.json", cp)
            checkpoints[row["arm_id"]] = cp
        receipt = dict(generation="continuation_001", initial_checkpoints=checkpoints,
            inherited_launches=2 * resume_prefix, inherited_worker_seconds=10.)
    runtime.execute(tmp_path, binding, None, None, continuation=receipt)
    assert len(advanced) == (576 if failed_arm is None else 555) - 2 * resume_prefix
    assert all(spec.window.iteration >= resume_prefix for spec in launches
               if spec.window.shard_id.startswith(("arm-000-", "arm-001-")))
    assert len(launches) >= len(advanced)
    assert not stopped
    for row in rows:
        cp = read(tmp_path / "arms" / row["arm_id"] / "checkpoint.json")
        expected = 3 if row["arm_id"] == failed_arm else 24
        assert cp["complete"] == (expected == 24) and cp["completed_intervals"] == expected
        assert len(cp["windows"]) == expected
    if failed_arm is not None:
        outcome = read(tmp_path / "arms/arm-000/outcome.json")
        assert outcome["classification"] == "numerically_unresolved" and outcome["first_unresolved"] == 3
        assert any(spec.window.shard_id.endswith("-hourly") for spec in launches)
    from experiments.case118_annual_hierarchy.streaming_schema import WindowIndexEntry
    assert not advance_from_winner(tmp_path / "arms/arm-000", WindowIndexEntry(
        **read(tmp_path / "arms/arm-000/checkpoint.json")["windows"][-1]))


def test_solved_state_tolerance_cannot_hide_invalid_reconstructed_first_action(monkeypatch):
    from tests.test_tracy_stage_d import fixture
    from cvxopf._ac_rollout import ACWindowRequest
    kwargs, _, result, named = fixture(3)
    kwargs["storage"] = [replace(kwargs["storage"][0], initial_soc=10., terminal_soc=10., aging_weight=.01)]
    result = model.serializable(result)
    result["soc"] = [[10.], [10.], [10.]]
    result["b"] = [[-4e-5], [4e-5], [0.]]
    result["storage_cost"] = 8e-7
    result["objective"] += 8e-7
    common = audit.audit_result(result, kwargs, dict(named, storage_cost=8e-7))
    assert common["passed"], common  # native coordinate passes existing engineering tolerance
    request = ACWindowRequest("arm", "plan", 0, 3, 0, 3, ("battery",), [10.], [10.], [10.], "original_final_obligation")
    record = dict(hard_target=True, result=result, logical_solution={}, window_request=model.serializable(asdict(request)),
        request=dict(checkpoint=dict(completed_intervals=0, storage_device_ids=["battery"], realized_soc_mwh=[10.],
                                    preceding_controlling_attempt_id=None)))
    monkeypatch.setattr(e3_prepared, "assess", lambda *a, **k: dict(passed=True))
    monkeypatch.setattr(audit, "verify_logical", lambda *a, **k: None)
    checked = audit.assess(record, kwargs, {}, {})
    assert not checked["passed"] and not checked["first_action"]["passed"]
    assert "executed-only physical audit failed" in checked["first_action"]["reason"]
