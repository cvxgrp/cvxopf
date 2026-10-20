"""Non-solving convex algebra, accounting and supervised lifecycle tests."""

from dataclasses import asdict, replace
from types import SimpleNamespace

import clarabel
import numpy as np
import pytest

from experiments.convex_cost_qualification import fixture as f, model as m, audit as a, run as r, analyze as analysis
from experiments.numerical_preparation import fixture as old, run_qualification as q
from experiments.numerical_preparation.audit import serializable


@pytest.fixture(autouse=True)
def forbid_optimizer(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unit tests must not launch numerical optimizers")
    monkeypatch.setattr(clarabel, "DefaultSolver", forbidden)


def small_arm(form="socp", scaled=True, prepared=False):
    return next(v for v in f.arms() if v.dataset == "case9" and v.formulation == form
                and v.cost_coordinates == scaled and v.prepared == prepared)


@pytest.mark.parametrize("form", f.FORMS)
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("scaled", [False, True])
@pytest.mark.parametrize("delta", [.5, 1., 2.])
def test_exact_coordinate_algebra_and_canonical_rebuild(form, prepared, scaled, delta):
    arm = small_arm(form, scaled, prepared)
    _, kwargs, _ = f.kwargs_for_arm(arm)
    kwargs["delta"] = delta
    from cvxopf import build_opf_multistep
    view = m.transform(build_opf_multistep(**kwargs), delta, scaled)
    point = {v.name(): np.zeros(v.shape) for v in view.physical.prob.variables()}
    point["b"] = np.array([[-2., 0., 3.]])
    point["load_shed_fraction"] = np.arange(9).reshape(3, 3)/20
    assert m.equivalence(view, point)["passed"]
    assert view.solver.prob.is_dcp()
    view.restore()
    for variable in view.physical.prob.variables():
        np.testing.assert_allclose(variable.value, point[variable.name()], atol=1e-13)
    left = m.canonical(view.solver)
    _, _, other, _, _ = f.construct(arm)
    # Same mathematical graph, fresh generated CVXPY identities.
    if delta == 1:
        assert left.signature == m.canonical(other.solver).signature
    if prepared:
        assert len(left.signature["fixed"]) == old.structural_inputs(kwargs)["expected_fixed_count"]


def test_matrix_balances_preparation_coordinates_and_forced_horizons():
    arms = f.arms()
    assert [v.id for v in arms] == list(range(1, 97))
    assert f.LIMITS["max_launches"] == len(arms)
    assert f.LIMITS["total_worker_seconds"] == len(arms)*180
    for form in f.FORMS:
        rows = [v for v in arms if v.formulation == form]
        assert len(rows) == 32
        assert {v.T for v in rows if v.forced_shedding} == {3, 6, 24}
        for group in {v.group for v in rows}:
            assert {(v.prepared, v.cost_coordinates) for v in rows if v.group == group} == {
                (False, False), (False, True), (True, False), (True, True)}


@pytest.mark.parametrize("form", f.FORMS)
@pytest.mark.parametrize("T", [3, 6, 24])
def test_stress_witness_input_only_and_same_ac_recipe(monkeypatch, form, T):
    base = old.case9_kwargs(3, form)
    for name in ("df_load_p", "df_load_q", "df_nd"):
        base[name] = base[name].iloc[np.arange(T)%3].reset_index(drop=True)
    base["T"] = T
    monkeypatch.setattr(old, "kwargs_for_call", lambda *_: dict(base))
    arm = replace(small_arm(form), dataset="tracy", T=T, start=1165, forced_shedding=True)
    _, kwargs, stress = f.kwargs_for_arm(arm)
    assert min(stress["modified"]["minimum_shed_mw"]) > 0
    for name in ("df_load_p", "df_load_q"):
        np.testing.assert_array_equal(kwargs[name], base[name]*stress["demand_multiplier"])
    for name in ("storage", "generators", "loads", "nondispatchable", "df_nd", "case", "options"):
        assert kwargs[name] is base[name]
    assert stress == f.kwargs_for_arm(replace(arm, prepared=True, cost_coordinates=False))[2]


def synthetic_native(view, problem, gap=0.):
    # Not a feasible dispatch: isolates independent accounting/projection algebra.
    soc = view.physical.variables["soc"]
    point = soc.value.copy()
    point[:, 0] = view.physical.data["storage_initial_soc"]
    soc.save_value(point)
    pg = view.physical.variables["Pg"]
    lower, upper = pg.get_bounds()
    pg.save_value(np.clip(pg.value, lower, upper))
    x = np.zeros(len(problem.data["c"]))
    for item in problem.layout:
        if item["is_original_variable"]:
            variable = next(v for v in view.solver.prob.variables() if v.name() == item["name"])
            x[item["start"]:item["stop"]] = variable.value.ravel(order="F")
        elif tuple(item["shape"]) == view.physical.variables["b"].shape and np.all(problem.data["c"][item["start"]:item["stop"]] > 0):
            actual = abs(view.leaves["b"].value) if view.scales else abs(view.physical.variables["b"].value)
            values = actual.ravel(order="F")+gap/problem.data["c"][item["start"]:item["stop"]]
            x[item["start"]:item["stop"]] = values
    x[problem.mapping.fixed] = problem.mapping.values
    # CVXPY lifts each affine generator MW expression into a quadratic
    # coordinate. Assign its defining equality, not a synthetic zero cost.
    for item in problem.layout:
        if not item["is_original_variable"] and np.any(problem.data["P"].diagonal()[item["start"]:item["stop"]]):
            for column in range(item["start"], item["stop"]):
                rows = np.flatnonzero(problem.data["A"][:problem.data["dims"].zero, column].toarray().ravel())
                assert len(rows) == 1
                row = rows[0]
                vector = problem.data["A"][[row], :].toarray().ravel()
                x[column] = (problem.data["b"][row]-vector@x)/vector[column]
    objective = float(.5*x@(problem.data["P"]@x)+problem.data["c"]@x)-problem.offset
    return serializable(dict(status="Solved", x=x[problem.mapping.free]/problem.D, s=np.zeros(len(problem.R)),
        z=np.zeros(len(problem.R)), obj_val=objective, obj_val_dual=objective,
        iterations=1, solve_time=.01, r_prim=0., r_dual=0.))


def mock_physics(monkeypatch):
    def check(build, result, kwargs, named):
        pg = np.asarray(result["Pg"])
        costs = dict(generator_cost=kwargs["delta"]*sum(np.sum(g.cost_coeffs[0]+g.cost_coeffs[1]*pg[:, i]
            +g.cost_coeffs[2]*pg[:, i]**2) for i, g in enumerate(kwargs["generators"])),
            storage_cost=kwargs["delta"]*np.sum(abs(np.asarray(result["b"]))*[s.aging_weight for s in kwargs["storage"]]),
            load_shedding_cost=kwargs["delta"]*np.sum(np.asarray(result["p_load_shed"])*[u.shedding_cost_per_mwh for u in kwargs["loads"]]))
        if kwargs["formulation"] == "lossy_dc":
            from cvxopf.network import BR_R
            flow = np.asarray(result["p_flows"])/kwargs["case"]["baseMVA"]
            costs["dc_loss_cost"] = kwargs["delta"]*kwargs["options"].loss_weight*np.sum(flow**2*kwargs["case"]["branch"][:, BR_R])
        return dict(passed=True, costs=serializable(costs), residuals={}), None
    monkeypatch.setattr(a, "physical_audit", check)


@pytest.mark.parametrize("form", f.FORMS)
@pytest.mark.parametrize("scaled", [False, True])
def test_cycling_warning_does_not_reject_directly_or_through_total(monkeypatch, form, scaled):
    _, kwargs, view, stress, _ = f.construct(small_arm(form, scaled))
    problem = m.canonical(view.solver)
    mock_physics(monkeypatch)
    native = synthetic_native(view, problem, gap=1.)
    checks = a.assess(view, kwargs, stress, native, None, problem.signature)
    assert checks["coordinate_checks"]["passed"]
    assert checks["passed"]
    assert checks["economics"]["cycling_gap_warning"]
    assert not checks["economics"]["total"]["passed"]
    assert checks["economics"]["total_excluding_cycling_gap"]["passed"]
    assert checks["economics"]["cycling_slack_sum"] == pytest.approx(3.)


def test_cancelling_slack_still_warns(monkeypatch):
    _, kwargs, view, stress, _ = f.construct(small_arm())
    problem = m.canonical(view.solver)
    mock_physics(monkeypatch)
    native = synthetic_native(view, problem)
    item = next(v for v in problem.layout if not v["is_original_variable"] and
                np.all(problem.data["c"][v["start"]:v["stop"]] > 0))
    native["x"][item["start"]] += .001
    native["x"][item["start"]+1] -= .001
    checks = a.assess(view, kwargs, stress, native, None, problem.signature)
    assert checks["passed"] and checks["economics"]["cycling_gap_warning"]
    assert checks["economics"]["components"]["storage_cost"]["passed"]


def test_unexplained_native_objective_error_is_hard_failure(monkeypatch):
    _, kwargs, view, stress, _ = f.construct(small_arm())
    problem = m.canonical(view.solver)
    mock_physics(monkeypatch)
    native = synthetic_native(view, problem)
    native["obj_val"] += .1
    checks = a.assess(view, kwargs, stress, native, None, problem.signature)
    assert not checks["passed"] and not checks["economics"]["passed"]


def test_almost_solved_is_not_accepted(monkeypatch):
    _, kwargs, view, stress, _ = f.construct(small_arm())
    problem = m.canonical(view.solver)
    mock_physics(monkeypatch)
    native = synthetic_native(view, problem)
    native["status"] = "AlmostSolved"
    assert not a.assess(view, kwargs, stress, native, None, problem.signature)["passed"]


def test_changed_canonical_signature_rejected(monkeypatch):
    _, kwargs, view, stress, _ = f.construct(small_arm())
    problem = m.canonical(view.solver)
    mock_physics(monkeypatch)
    with pytest.raises(ValueError, match="signature"):
        a.assess(view, kwargs, stress, synthetic_native(view, problem), None, problem.signature | dict(full_size=0))


def setup_status(monkeypatch, root):
    binding = dict(context={}, rows=[dict(arm=asdict(arm), group=arm.group) for arm in f.arms()])
    monkeypatch.setattr(r, "verify_binding", lambda _: binding)
    q.atomic_immutable_json(root / "protocol.json", r.protocol())
    return binding


def test_unfinished_attempt_cannot_accept_or_advance(monkeypatch, tmp_path):
    setup_status(monkeypatch, tmp_path)
    folder = tmp_path / "call-001"
    folder.mkdir()
    q.atomic_immutable_json(folder / "completion.json", dict(classification="accepted"))
    status = r.status(tmp_path)
    assert status["accepted"] == status["disposed"] == status["completed_archives"] == 0
    (tmp_path / "call-002").mkdir()
    with pytest.raises(ValueError, match="unsupervised"):
        r.status(tmp_path)


def timed_out(monkeypatch, root):
    setup_status(monkeypatch, root)
    folder = root / "call-001"
    folder.mkdir()
    q.atomic_immutable_json(folder / "request.json", r.request(root, 1, 180.))
    q.atomic_immutable_json(folder / "supervision.json", dict(classification="wall_limit", wall_seconds=180.,
        returncode=-15, peak_sampled_rss_mib=100.))
    monkeypatch.setattr(q, "resource_evidence", lambda *_: True)
    return folder


def test_timeout_counts_as_disposed_not_accepted(monkeypatch, tmp_path):
    timed_out(monkeypatch, tmp_path)
    status = r.status(tmp_path)
    assert status["disposed"] == 1 and status["accepted"] == status["completed_archives"] == 0


def test_partial_publication_is_preserved_not_accepted(monkeypatch, tmp_path):
    folder = timed_out(monkeypatch, tmp_path)
    q.atomic_gzip_json(folder / "result.json.gz", dict(iteration=1, classification="accepted"))
    status = r.status(tmp_path)
    assert status["retained_result_archives"] == 1 and status["accepted"] == 0
    assert status["attempts"][0]["archive_state"] == "incomplete_publication"


def test_manifest_missing_archive_is_inconsistent(monkeypatch, tmp_path):
    folder = timed_out(monkeypatch, tmp_path)
    q.atomic_immutable_json(folder / "completion.json", dict(classification="accepted"))
    with pytest.raises(ValueError, match="disagreement"):
        r.status(tmp_path)


def test_clean_commit_required_before_creating_execution_root(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "frozen_binding", lambda: dict(context=dict(clean=False, commit="a"*40)))
    root = tmp_path / "new"
    with pytest.raises(ValueError, match="clean"):
        r.run(root, "a"*40)
    assert not root.exists()


def test_warning_does_not_fail_qualification_but_missing_coverage_does():
    arm = small_arm()
    checks = dict(common=dict(costs=dict(generator_cost=100.)), economics=dict(cycling_gap_warning=True))
    attempt = dict(arm=asdict(arm), classification="exited", accepted=True, checks=checks)
    assert analysis.disposition([arm], [attempt]) == "qualified_for_declared_matrix"
    checks["common"]["costs"]["generator_cost"] = 0.
    assert analysis.disposition([arm], [attempt]) == "incomplete_economic_coverage"
    assert analysis.disposition([arm], []) == "incomplete"


def test_stock_observer_restored_and_no_calls_on_fingerprint_mismatch(monkeypatch):
    _, _, view, _, _ = f.construct(small_arm())
    problem = m.canonical(view.solver)
    original = m.CLARABEL.solve_via_data
    bad = dict(problem.delivered, c=problem.delivered["c"]+1.)
    monkeypatch.setattr(view.solver, "solve", lambda **_: m.CLARABEL().solve_via_data(bad, False, False, {}))
    with pytest.raises(ValueError, match="differs"):
        m.solve_observed(view, problem, {}, lambda _: None)
    assert m.CLARABEL.solve_via_data is original


@pytest.mark.parametrize("form", f.FORMS)
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("scaled", [False, True])
def test_real_build_solve_boundary_inverse_and_warning_replay(monkeypatch, form, prepared, scaled):
    _, kwargs, view, stress, _ = f.construct(small_arm(form, scaled, prepared))
    problem = m.canonical(view.solver)
    native = synthetic_native(view, problem, gap=1.)
    seen, captured = [], []

    def stock(adapter, data, warm_start, verbose, solver_opts, solver_cache=None):
        seen.append((m.fingerprint(data), warm_start, solver_opts))
        assert len(seen) == 1
        return SimpleNamespace(**native)

    monkeypatch.setattr(m.CLARABEL, "solve_via_data", stock)
    evidence = m.solve_observed(view, problem, old.solver_options(form), captured.append)
    assert m.CLARABEL.solve_via_data is stock
    assert seen == [(problem.signature["delivered_sha256"], False, old.solver_options(form))]
    assert captured == [native]
    mock_physics(monkeypatch)
    checks = a.assess(view, kwargs, stress, native, evidence, problem.signature)
    assert checks["passed"] and checks["economics"]["cycling_gap_warning"]
    assert bool(evidence) == prepared


@pytest.mark.parametrize("component", ["generator_cost", "load_shedding_cost", "dc_loss_cost"])
def test_noncycling_cost_error_remains_hard(monkeypatch, component):
    _, kwargs, view, stress, _ = f.construct(small_arm("lossy_dc"))
    problem = m.canonical(view.solver)
    native = synthetic_native(view, problem, gap=1.)
    mock_physics(monkeypatch)
    physics = a.physical_audit

    def changed(*args):
        common, relaxation = physics(*args)
        common["costs"][component] += 10.
        return common, relaxation

    monkeypatch.setattr(a, "physical_audit", changed)
    checks = a.assess(view, kwargs, stress, native, None, problem.signature)
    assert not checks["passed"]
    assert not checks["economics"]["components"][component]["passed"]


def test_forced_shedding_witness_is_hard(monkeypatch):
    _, kwargs, view, stress, _ = f.construct(small_arm())
    problem = m.canonical(view.solver)
    native = synthetic_native(view, problem)
    mock_physics(monkeypatch)
    stress = stress | dict(forced_shedding=True, modified=stress["modified"] | dict(
        minimum_ens_mwh=1e9, minimum_shed_mw=[1e9]*3))
    checks = a.assess(view, kwargs, stress, native, None, problem.signature)
    assert not checks["passed"] and not checks["forced_shedding"]["passed"]


def test_analysis_requires_terminal_invocation_and_cannot_overwrite(tmp_path):
    with pytest.raises(FileNotFoundError):
        analysis.analyze(tmp_path)
    q.atomic_immutable_json(tmp_path / "invocation-finish.json", dict(outcome="operator_stop"))
    q.atomic_immutable_json(tmp_path / "analysis.json", {})
    with pytest.raises(FileExistsError):
        analysis.analyze(tmp_path)


def archived_worker(monkeypatch, root, native_status="Solved"):
    arm = replace(small_arm(), id=1)
    row = f.row_binding(arm)
    binding = dict(context={}, rows=[row])
    monkeypatch.setattr(r, "verify_binding", lambda _: binding)
    q.atomic_immutable_json(root / "protocol.json", r.protocol())
    folder = root / "call-001"
    folder.mkdir()
    q.atomic_immutable_json(folder / "request.json", r.request(root, 1, 180.))
    q.atomic_immutable_json(folder / "launch.json", dict(pid=r.os.getpid()))
    _, _, view, _, problem = r.checked_construction(row)
    native = synthetic_native(view, problem, gap=1.) | dict(status=native_status)
    monkeypatch.setattr(m.CLARABEL, "solve_via_data", lambda *_args, **_kwargs: SimpleNamespace(**native))
    mock_physics(monkeypatch)
    r.worker(root, 1)
    q.atomic_immutable_json(folder / "supervision.json", dict(classification="exited", wall_seconds=1.,
        returncode=0, peak_sampled_rss_mib=100.))
    monkeypatch.setattr(q, "resource_evidence", lambda *_: True)
    return folder


def test_worker_archive_manifest_replay_and_resource_gate(monkeypatch, tmp_path):
    folder = archived_worker(monkeypatch, tmp_path)
    status = r.status(tmp_path)
    assert status["accepted"] == status["disposed"] == status["completed_archives"] == 1
    assert status["accepted_with_cycling_warnings"] == 1
    manifest = q.read(folder / "completion.json")
    assert set(manifest["artifacts"]) == {"request.json", "native.json.gz", "result.json.gz"}
    monkeypatch.setattr(q, "resource_evidence", lambda *_: False)
    assert r.status(tmp_path)["accepted"] == 0
    q.atomic_json(folder / "completion.json", manifest | dict(artifacts=manifest["artifacts"] | {"result.json.gz": "0"*64}))
    with pytest.raises(ValueError, match="hash mismatch"):
        r.status(tmp_path)


def test_native_rejection_retained_and_not_accepted(monkeypatch, tmp_path):
    folder = archived_worker(monkeypatch, tmp_path, "MaxIterations")
    progress = r.status(tmp_path)
    assert progress["disposed"] == progress["completed_archives"] == 1
    assert progress["accepted"] == 0
    record = q.read(folder / "result.json.gz")
    assert record["classification"] == "rejected" and record["exception"] is None
    assert record["native"]["status"] == "MaxIterations"
    assert "checks" not in record


def test_terminal_analysis_keeps_warning_candidate_and_full_axes(monkeypatch, tmp_path):
    archived_worker(monkeypatch, tmp_path)
    q.atomic_immutable_json(tmp_path / "invocation-finish.json", dict(outcome="operator_stop"))
    value = analysis.analyze(tmp_path, make_plots=False)
    assert value["progress"]["accepted_with_cycling_warnings"] == 1
    comparison = next(iter(value["comparisons"].values()))
    assert comparison["axes"]["hours"] == [0, 1, 2]
    assert comparison["axes"]["boundary_hours"] == [0, 1, 2, 3]
    assert np.shape(comparison["boundary_soc_mwh"]["baseline_cost"]) == (4, 1)
    assert value["treatment_dispositions"]["socp"]["baseline_cost"] == "incomplete"


@pytest.mark.parametrize("invalid", [0., -1., float("nan"), float("inf")])
def test_noninvertible_cost_scale_rejected(invalid):
    from cvxopf import build_opf_multistep
    _, kwargs, _ = f.kwargs_for_arm(small_arm())
    build = build_opf_multistep(**kwargs)
    with pytest.raises(ValueError, match="invertible"):
        m.transform(build, invalid, True)


@pytest.mark.parametrize("field,value", [("r_prim", float("nan")), ("r_dual", {"nonfinite": "inf"}),
                                        ("iterations", True), ("solve_time", -1.)])
def test_native_diagnostics_cannot_be_hidden_by_cycling_warning(monkeypatch, field, value):
    _, kwargs, view, stress, _ = f.construct(small_arm())
    problem = m.canonical(view.solver)
    mock_physics(monkeypatch)
    native = synthetic_native(view, problem, gap=1.) | {field: value}
    checks = a.assess(view, kwargs, stress, native, None, problem.signature)
    assert not checks["passed"] and not checks["native_diagnostics_passed"]


@pytest.mark.parametrize("form", f.FORMS)
def test_fleet_plot_includes_soc_boundaries_and_does_not_invent_dc_reactive(monkeypatch, tmp_path, form):
    _, kwargs, view, stress, _ = f.construct(small_arm(form))
    problem = m.canonical(view.solver)
    mock_physics(monkeypatch)
    checks = a.assess(view, kwargs, stress, synthetic_native(view, problem), None, problem.signature)
    candidate = dict(result=checks["result"], economics=checks["economics"], accepted=True)
    import matplotlib.pyplot as plt
    original = plt.Axes.plot
    clocks = []

    def plot(axis, x, y, *args, **kwargs):
        clocks.append(list(x))
        return original(axis, x, y, *args, **kwargs)

    monkeypatch.setattr(plt.Axes, "plot", plot)
    artifact = analysis.plot_group(tmp_path, 1, small_arm(form), {"candidate": candidate}, kwargs)
    assert [0, 1, 2, 3] in clocks
    assert len(clocks) == (6 if form == "socp" else 5)
    assert q.digest(tmp_path / artifact["path"]) == artifact["sha256"]
    with pytest.raises(FileExistsError):
        analysis.plot_group(tmp_path, 1, small_arm(form), {"candidate": candidate}, kwargs)
