"""Non-solving equivalence, automatic canonicalization and archive gates."""

from dataclasses import replace
import os
from types import SimpleNamespace

import cvxpy as cp
import numpy as np
import pytest
from cvxpy.reductions.cvx_attr2constr import CvxAttr2Constr
from cvxpy.reductions.dnlp2smooth.dnlp2smooth import Dnlp2Smooth
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
from cvxpy.reductions.solvers.nlp_solvers.nlp_solver import Oracles
from cvxpy.reductions.solvers.solving_chain import SolvingChain

from cvxopf._numerical_preparation import resolve_fixed_map
from experiments.numerical_preparation import fixture as f, run_qualification as q
from experiments.numerical_preparation.audit import serializable
from experiments.ac_cost_coordinates import model as m, run as r
from experiments.ac_cost_coordinates.analyze import soc_boundaries


def fixture(delta=1., treatment="baseline"):
    call = replace(f.calls()[22], treatment=treatment)
    kwargs = f.case9_kwargs(3, "ac")
    kwargs["delta"] = delta
    kwargs["options"] = replace(kwargs["options"], numerical_preparation=f.POLICIES[treatment])
    build = f.build_for_call(call, kwargs)
    start = q.physical_start(build, kwargs)
    return call, kwargs, build, start


def canonical(build):
    solver = IPOPT()
    chain = SolvingChain(reductions=[CvxAttr2Constr(reduce_bounds=not solver.BOUNDED_VARIABLES),
                                    Dnlp2Smooth(), solver])
    return chain.apply(build.prob)


@pytest.mark.parametrize("delta", [.5, 1., 2.])
@pytest.mark.parametrize("treatment", ["baseline", "combined_ac"])
def test_exact_substitution_at_nonzero_signed_dispatch(delta, treatment):
    _, _, build, values = fixture(delta, treatment)
    view = m.transform(build, delta, True)
    values["b"] = np.array([[-3., 0., 4.]])
    values["load_shed_fraction"] = np.arange(9).reshape(3, 3)/20
    check = m.equivalence(view, values)
    assert check["passed"], check
    assert len(view.solver.prob.constraints) == len(build.prob.constraints)
    assert [c.id for c in view.solver.prob.constraints] == [c.id for c in build.prob.constraints]
    np.testing.assert_array_equal(view.leaves["b"].value, delta*.01*values["b"])
    restored = view.restore()
    actual = {v.name(): v.value for v in view.physical.prob.variables()}
    for name, expected in values.items():
        np.testing.assert_allclose(actual[name], expected, atol=1e-14)
    assert all(expression.is_dcp() for expression in restored.expressions.values()
               if hasattr(expression, "is_dcp") and expression.is_convex())


def test_only_automatic_epigraph_has_unit_cycling_gradient_and_fixed_map_survives():
    _, kwargs, build, _ = fixture(treatment="combined_ac")
    view = m.transform(build, kwargs["delta"], True)
    data, inverse = canonical(view.solver)
    mapping = resolve_fixed_map(view.solver._exact_boxes, inverse[:-1], inverse[-1],
                               data["_bounds"].problem.constraints, len(data["x0"]), len(data["cl"]))
    assert mapping.fixed.size == f.structural_inputs(kwargs)["expected_fixed_count"] == 5
    original_ids = {v.id for v in view.solver.prob.variables()}
    auxiliary = [v for v in data["problem"].variables()
                 if v.id not in original_ids and v.shape == build.variables["b"].shape]
    assert len(auxiliary) == 1
    oracles = Oracles(data["_bounds"].new_problem, verbose=False, use_hessian=True)
    oracles.objective(data["x0"])
    gradient = oracles.gradient(data["x0"])
    for v in [auxiliary[0], view.leaves["load_shed_fraction"]]:
        offset = inverse[-1].var_offsets[v.id]
        np.testing.assert_array_equal(gradient[offset:offset+v.size], np.ones(v.size))


@pytest.mark.parametrize("scale", [0., -1., float("nan"), float("inf")])
def test_noninvertible_scales_fail_before_solving(scale):
    _, _, build, _ = fixture()
    with pytest.raises(ValueError, match="invertible"):
        m.transform(build, scale, True)


def test_other_cost_or_assembly_fails_before_solving():
    _, _, build, _ = fixture()
    with pytest.raises(ValueError, match="vectorized AC"):
        m.transform(replace(build, temporal_assembly="scalar"), 1., True)
    with pytest.raises(ValueError, match="three frozen"):
        m.transform(replace(build, expressions=build.expressions | {"other_cost": cp.Constant(0.)}), 1., True)


@pytest.mark.parametrize("scaled", [False, True])
def test_retained_primal_round_trip_and_cost_slack_accounting(scaled):
    _, kwargs, build, start = fixture()
    view = m.transform(build, kwargs["delta"], scaled)
    start["b"] = np.array([[-2., 0., 3.]])
    m.equivalence(view, start)
    data, inverse = canonical(view.solver)
    original_ids = {v.id for v in view.solver.prob.variables()}
    layout, x = [], np.asarray(data["x0"]).copy()
    for variable in data["problem"].variables():
        a = inverse[-1].var_offsets[variable.id]
        layout.append(dict(name=variable.name(), shape=list(variable.shape), start=a,
                           stop=a+variable.size, is_original_variable=variable.id in original_ids))
        if variable.id not in original_ids and variable.shape == build.variables["b"].shape:
            value = abs(view.leaves["b"].value if scaled else start["b"]) + 2.
            x[a:a+variable.size] = value.ravel(order="F")
    excess = 6. if scaled else .06
    physical = float(sum(e.value for k, e in build.expressions.items() if k.endswith("_cost")))
    record = dict(native=dict(x=x, obj_val=physical+excess), preparation_evidence=None)
    fresh = fixture()[2]
    other = m.transform(fresh, kwargs["delta"], scaled)
    m.restore_archive(other, record, dict(layout=layout))
    actual = {v.name(): v.value for v in other.physical.prob.variables()}
    for name in start:
        np.testing.assert_allclose(actual[name], start[name], atol=1e-14)
    check = m.accounting(other, record["native"], None, dict(layout=layout), dict(costs=dict(total=physical)))
    assert check["cycling_epigraph_excess"] == pytest.approx(excess)
    assert check["epigraph_reconstruction_error"] < 1e-8


def setup_status(monkeypatch, tmp_path):
    binding = dict(context={}, limits=r.LIMITS, historical_pins={}, rows=[
        dict(historical_call=n, scaled=s) for n, s in m.ARMS])
    monkeypatch.setattr(r, "context", lambda: {})
    q.atomic_immutable_json(tmp_path/"binding.json", binding)
    q.atomic_immutable_json(tmp_path/"protocol.json", dict(protocol=r.LIMITS))


def test_unfinished_not_accepted_and_cannot_advance(monkeypatch, tmp_path):
    setup_status(monkeypatch, tmp_path)
    directory = tmp_path/"call-001"
    directory.mkdir()
    q.atomic_immutable_json(directory/"completion.json", dict(classification="accepted"))
    report = r.status(tmp_path)
    assert report["accepted"] == report["disposed"] == 0
    assert report["attempts"][0]["classification"] == "unfinished"
    (tmp_path/"call-002").mkdir()
    with pytest.raises(ValueError, match="unsupervised"):
        r.status(tmp_path)


def test_preflight_failure_never_creates_execution_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "frozen_binding", lambda: {})
    for name in r.THREAD_KEYS:
        monkeypatch.setenv(name, "1")
    def unavailable():
        raise RuntimeError("battery")
    monkeypatch.setattr(r, "monitoring", unavailable)
    with pytest.raises(RuntimeError, match="battery"):
        r.run(tmp_path/"absent")
    assert not (tmp_path/"absent").exists()


def test_bound_scope_and_current_tolerance():
    assert m.ARMS == ((24, False), (24, True), (25, False), (25, True))
    assert r.LIMITS == dict(max_launches=4, wall_seconds=180., rss_mib=16384.,
                           total_worker_seconds=720., poll_seconds=1.)
    # Serialization must not hide nonfinite scientific evidence as zero.
    assert serializable(np.nan) == {"nonfinite": "nan"}


def test_soc_plot_restores_initial_boundary_without_duplicating_end_state():
    kwargs = f.case9_kwargs(3, "ac")
    np.testing.assert_array_equal(soc_boundaries(dict(soc=[[19.], [18.], [18.]]), kwargs),
                                  [[20.], [19.], [18.], [18.]])
    with pytest.raises(ValueError, match="SoC shape"):
        soc_boundaries(dict(soc=[[20.], [19.], [18.], [18.]]), kwargs)


def test_worker_archives_start_and_failure_without_invoking_optimizer(monkeypatch, tmp_path):
    """Exercise the real gzip writer contract at the verified-start callback."""
    call, kwargs, build, start = fixture()
    view = m.transform(build, kwargs["delta"], False)
    frozen = serializable(f.call_binding(call, kwargs))
    binding = dict(context={}, rows=[dict(historical_call=24, scaled=False,
        frozen=frozen, physical_start=serializable(start), scales={})])
    q.atomic_immutable_json(tmp_path/"binding.json", binding)
    q.atomic_immutable_json(tmp_path/"protocol.json", dict(protocol=r.LIMITS))
    directory = tmp_path/"call-001"
    directory.mkdir()
    q.atomic_immutable_json(directory/"request.json", r.request(tmp_path, 1, 180.))
    q.atomic_immutable_json(directory/"launch.json", dict(pid=os.getpid()))
    monkeypatch.setattr(r, "context", lambda: {})
    monkeypatch.setattr(r, "construct", lambda *_: (call, kwargs, view, start))
    def no_optimization(*args, start_observer, native_observer, **options):
        start_observer(SimpleNamespace(complete_x0=[1., 2.], layout=[]))
        native_observer(dict(status=2, obj_val=10.))
        return SimpleNamespace(exception=None)
    monkeypatch.setattr(r, "_solve_ac_with_verified_x0", no_optimization)
    r.worker(tmp_path, 1)
    completion = q.read(directory/"completion.json")
    record = q.read(directory/"result.json.gz")
    assert record["classification"] == "rejected" and record["exception"] is None
    assert record["iteration"] == record["captured"]["iteration"] == 1
    assert record["captured"] == q.read(directory/"x0.json.gz")
    for name, sha in completion["artifacts"].items():
        assert q.digest(directory/name) == sha
