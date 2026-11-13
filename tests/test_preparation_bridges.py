"""Production bridge/algebra tests, not the separately gated qualification matrix."""
from dataclasses import replace
from types import SimpleNamespace

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from cvxopf import NumericalPreparation, OPFOptions, build_opf, build_opf_multistep, gen_from_matpower, NondispatchableUnit, StorageUnitIdeal, StorageUnitLossy, Load, extract_results, audit_socp_relaxation
from cvxopf.testcases import case9
from cvxopf._numerical_preparation import FixedCoordinateMap
from cvxopf._convex_preparation import substitute, joint_scales, scaled_data, restore
from cvxopf._ac_preparation import ReducedOracles


def data(P=None):
    return dict(P=sparse.csc_array([[4., 2.], [2., 6.]] if P is None else P),
                A=sparse.csc_array([[1., 0.], [1., 1.], [-1., 0.]]),
                b=np.array([2., 5., 0.]), c=np.array([3., -4.]),
                dims=SimpleNamespace(zero=1, nonneg=2, soc=[], exp=0, psd=[], p3d=[], pnd=[]))


def test_substitution_cross_terms_constants_and_duals():
    full = data()
    mapping = FixedCoordinateMap(2, 3, np.array([0]), np.array([2.]), np.array([0]))
    reduced, offset = substitute(full, mapping)
    np.testing.assert_array_equal(reduced["c"], [0.])
    assert offset == 14
    y = np.array([1.])
    x = mapping.expand(y)
    assert 0.5 * x @ full["P"] @ x + full["c"] @ x == 0.5 * y @ reduced["P"] @ y + reduced["c"] @ y + offset
    D, R = joint_scales(reduced)
    scaled = scaled_data(reduced, D, R)
    z = np.array([3., 2.])
    slack = reduced["b"] - reduced["A"] @ y
    raw = SimpleNamespace(x=y / D, s=R * slack, z=z / R, obj_val=3., obj_val_dual=2.,
                          status="Solved", iterations=1, solve_time=.01)
    restored, checks = restore(full, mapping, raw, D, R, offset)
    assert restored.obj_val == 17 and restored.obj_val_dual == 16
    np.testing.assert_allclose(full["A"] @ restored.x + restored.s, full["b"])
    assert (full["P"] @ restored.x + full["c"] + full["A"].T @ restored.z)[0] == 0
    assert checks["objective_reconstruction"] == 0
    np.testing.assert_allclose(scaled["A"] @ raw.x + raw.s, scaled["b"])
    with pytest.raises(ValueError):
        mapping.values.flags.writeable = True


@pytest.mark.parametrize("fixed,values,rows", [([0, 0], [1, 1], [0, 1]), ([2], [1], [0]), ([0], [np.nan], [0]), ([0], [1], [3]), ([0, 1], [1, 1], [0, 1])])
def test_invalid_maps_fail(fixed, values, rows):
    with pytest.raises(ValueError):
        FixedCoordinateMap(2, 3, np.array(fixed), np.array(values), np.array(rows))


def test_identity_map_and_empty_constraint_scaling():
    mapping = FixedCoordinateMap(2, 3, np.array([], dtype=int), np.array([]), np.array([], dtype=int))
    reduced, offset = substitute(data(), mapping)
    assert offset == 0
    np.testing.assert_array_equal(mapping.expand(mapping.select(np.array([1., 2.]))), [1, 2])
    np.testing.assert_array_equal(reduced["A"].toarray(), data()["A"].toarray())
    empty = data()
    empty.update(A=sparse.csc_array((0, 2)), b=np.array([]), dims=SimpleNamespace(zero=0, nonneg=0, soc=[], exp=0, psd=[], p3d=[], pnd=[]))
    D, R = joint_scales(empty)
    assert D.shape == (2,) and R.size == 0


def test_only_defining_rows_removed_and_bad_data_rejected():
    mapping = FixedCoordinateMap(2, 3, np.array([0]), np.array([2.]), np.array([1]))
    with pytest.raises(ValueError, match="defining fixed box"):
        substitute(data(), mapping)
    mapping = FixedCoordinateMap(2, 3, np.array([], dtype=int), np.array([]), np.array([], dtype=int))
    for invalid in (data([[1, 2], [0, 1]]), dict(data(), c=np.array([np.inf, 1.])), dict(data(), dims=SimpleNamespace(exp=1, psd=[], p3d=[], pnd=[]))):
        with pytest.raises(ValueError):
            substitute(invalid, mapping)


def test_joint_scaling_soc_blocks_and_zero_maxima():
    d = data([[0, 0], [0, 0]])
    d.update(A=sparse.csc_array([[0., 0.], [1., 0.], [0., 100.]]), b=np.zeros(3), c=np.zeros(2),
             dims=SimpleNamespace(zero=0, nonneg=0, soc=[3], exp=0, psd=[], p3d=[], pnd=[]))
    D, R = joint_scales(d)
    assert np.all(R == R[0])
    assert np.all((D >= 1e-6) & (D <= 1e6))
    np.testing.assert_array_equal(joint_scales(dict(d, A=d["A"] * 0))[0], np.ones(2))


def _lossy_units():
    return [StorageUnitLossy(bus=5+k, apparent_power_rating=10.+5*k,
                            capacity=40.+10*k, initial_soc=20.+10*k,
                            charge_efficiency=.95-.1*k, discharge_efficiency=.9+.02*k,
                            self_discharge_per_month=.01) for k in range(2)]


def build(formulation, assembly="single", policy=None, with_storage=False, with_lossy=False, T=2):
    case = case9()
    gens = gen_from_matpower(case["gen"], case["gencost"])
    gens[0] = replace(gens[0], p_min_mw=50., p_max_mw=50.)
    kwargs = dict(formulation=formulation, generators=gens,
                  nondispatchable=[NondispatchableUnit(bus=5, p_available=0., apparent_power_rating=10., device_id="zero")],
                  options=OPFOptions(numerical_preparation=policy or NumericalPreparation(exact_fixed_boxes=True)))
    if with_storage:
        kwargs["storage"] = [StorageUnitIdeal(bus=5, apparent_power_rating=10., capacity=40., initial_soc=20.)]
    if with_lossy:
        kwargs["lossy_storage"] = _lossy_units()
    if assembly == "single":
        return build_opf(case, **kwargs)
    return build_opf_multistep(case, T=T, df_P=pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1))),
                               df_Q=pd.DataFrame(np.tile(case["bus"][:, 3], (T, 1))),
                               temporal_assembly=assembly, **kwargs)


@pytest.mark.parametrize("formulation", ["socp", "lossy_dc", "singlenode_dc"])
@pytest.mark.parametrize("assembly", ["single", "stepwise", "vectorized"])
@pytest.mark.parametrize("with_lossy,T", [(False, 2), (True, 1), (True, 2)])
def test_convex_bridge_matches_baseline(formulation, assembly, with_lossy, T):
    prepared = build(formulation, assembly, NumericalPreparation(exact_fixed_boxes=True, canonical_scaling="joint5", normalize_device_limits=formulation == "socp"), with_storage=with_lossy, with_lossy=with_lossy, T=T)
    baseline = build(formulation, assembly, NumericalPreparation(), with_storage=with_lossy, with_lossy=with_lossy, T=T)
    prepared.solve()
    baseline.solve()
    assert prepared.prob.status == baseline.prob.status == "optimal"
    assert prepared.prob.value == pytest.approx(baseline.prob.value, rel=1e-6)
    e = prepared.preparation_evidence
    assert e.coordinates.fixed.size == (2 if assembly == "single" else 2*T)
    assert e.checks["restoration_available"]
    assert e.checks["primal_residual"] < 1e-5
    assert e.checks["stationarity"] < 1e-3
    assert e.checks["objective_reconstruction"] < 1e-5
    assert prepared.variables["Pg"].id if assembly != "stepwise" else prepared.variables["Pg"][0].id
    if with_lossy:
        results = extract_results(prepared)
        assert np.isfinite(results["ls_cost"]) and results["ls_split"].max() <= 1e-3
        if formulation == "socp":
            audit = audit_socp_relaxation(prepared, results)
            assert audit["complete"] and audit["feasible"], audit["residuals"]


@pytest.mark.parametrize("formulation,options", [
    (f, opts) for f in ("ac", "socp") for opts in
    ({"solver": "SCS"}, {"warm_start": True}, {"best_of": 2}, {"accept_unknown": False})
] + [("ac", {"hessian_approximation": "limited-memory"}), ("ac", {"warm_start_init_point": "yes"})])
def test_preflight_and_stale_cleanup(monkeypatch, formulation, options):
    b = build(formulation)
    for v in b.prob.variables():
        v.save_value(np.ones(v.shape))
    with pytest.raises(ValueError):
        b.solve(**options)
    assert b.preparation_evidence is None
    assert b.prob.value is None
    assert all(v.value is None for v in b.prob.variables())


class PolynomialOracle:
    def objective(self, x):
        return x @ x
    def constraints(self, x):
        return np.array([x[0] - 2, x[0] * x[1], x[1] + x[2]])
    def gradient(self, x):
        return 2 * x
    def jacobianstructure(self):
        return np.array([0, 1, 1, 2, 2]), np.array([0, 0, 1, 1, 2])
    def jacobian(self, x):
        return np.array([1, x[1], x[0], 1, 1])
    def hessianstructure(self):
        return np.array([0, 1, 1, 2]), np.array([0, 0, 1, 2])
    def hessian(self, x, duals, obj_factor):
        return np.array([2 * obj_factor, duals[1], 2 * obj_factor, 2 * obj_factor])


def test_reduced_oracle_sparse_structure_and_finite_differences():
    mapping = FixedCoordinateMap(3, 3, np.array([0]), np.array([2.]), np.array([0]))
    oracle = ReducedOracles(PolynomialOracle(), mapping)
    y, eps = np.array([3., 4.]), 1e-5
    numerical_grad = np.array([(oracle.objective(y + eps * e) - oracle.objective(y - eps * e)) / (2 * eps) for e in np.eye(2)])
    np.testing.assert_allclose(oracle.gradient(y), numerical_grad)
    numerical_jac = np.column_stack([(oracle.constraints(y + eps * e) - oracle.constraints(y - eps * e)) / (2 * eps) for e in np.eye(2)])
    rows, cols = oracle.jacobianstructure()
    np.testing.assert_allclose(sparse.coo_array((oracle.jacobian(y), (rows, cols)), shape=(2, 2)).toarray(), numerical_jac)
    rows, cols = oracle.hessianstructure()
    assert list(zip(rows, cols)) == [(0, 0), (1, 1)]
    np.testing.assert_array_equal(oracle.hessian(y, np.array([5., 6.]), 3), [6, 6])


@pytest.mark.parametrize("assembly", ["single", "stepwise", "vectorized"])
@pytest.mark.parametrize("with_lossy,T", [(False, 2), (True, 1), (True, 2)])
def test_ac_bridge_verified_start_and_physical_restoration(assembly, with_lossy, T):
    b = build("ac", assembly, NumericalPreparation(exact_fixed_boxes=True, normalize_device_limits=True), with_storage=True, with_lossy=with_lossy, T=T)
    # An explicit fixed-coordinate mismatch must be recorded and adjusted,
    # while every free coordinate in the complete assigned start stays intact.
    pg = b.variables["Pg"] if assembly != "stepwise" else b.variables["Pg"][0]
    pg.save_value(np.zeros(pg.shape))
    assigned = {}
    if with_lossy:
        for name, seed in (("ls_p_ch", 1.), ("ls_p_dis", 2.), ("ls_b_q", 3.), ("ls_soc", 4.)):
            variables = b.variables[name] if assembly == "stepwise" else [b.variables[name]]
            for variable in variables:
                value = np.arange(variable.size, dtype=float).reshape(variable.shape, order="F") + seed
                variable.value = value
                if name == "ls_soc" and assembly == "vectorized":
                    value = value.copy()
                    value[:, 0] = [20., 30.]
                assigned[variable.id] = value.ravel(order="F")
    b.solve(max_iter=100)
    assert b.prob.status in ("optimal", "optimal_inaccurate")
    e = b.preparation_evidence
    assert e.native["status"] in (0, 1, 6)
    assert e.checks["restoration_available"]
    np.testing.assert_array_equal(e.assigned_x0[e.coordinates.free], e.adjusted_x0[e.coordinates.free])
    np.testing.assert_array_equal(e.adjusted_x0[e.coordinates.fixed], e.coordinates.values)
    np.testing.assert_array_equal(e.coordinates.expand(e.reduced_x0), e.adjusted_x0)
    assert e.checks["primal_residual"] < 1e-5
    assert e.checks["objective_reconstruction"] < 1e-6
    assert e.assigned_x0[e.coordinates.fixed[0]] != e.adjusted_x0[e.coordinates.fixed[0]]
    soc = [v for v in b.prob.variables() if v.name() == "soc"]
    if soc and assembly == "vectorized":
        start = next(item[2] for item in e.start_layout if item[0] == soc[0].id)
        assert e.assigned_x0[start] == e.adjusted_x0[start] == 20.
    for variable_id, expected in assigned.items():
        start = next(item[2] for item in e.start_layout if item[0] == variable_id)
        np.testing.assert_array_equal(e.assigned_x0[start:start+expected.size], expected)
    if with_lossy:
        results = extract_results(b)
        assert np.isfinite(results["ls_cost"]) and results["ls_split"].max() <= 1e-3
    assert all(v.value is None for c in b.prob.constraints for v in c.dual_variables)


@pytest.mark.parametrize("status", ["MaxIterations", "PrimalInfeasible", "NumericalError"])
@pytest.mark.parametrize("with_lossy", [False, True])
def test_convex_failure_never_publishes_scaled_vectors(monkeypatch, status, with_lossy):
    from cvxpy.reductions.solvers.conic_solvers.clarabel_conif import CLARABEL
    b = build("socp", with_lossy=with_lossy)
    b.solve()
    assert all(v.value is not None for v in b.prob.variables())
    def failed(self, data, *args, **kwargs):
        return SimpleNamespace(status=status, x=np.ones(len(data["c"])), s=np.ones(len(data["b"])),
                               z=np.ones(len(data["b"])), obj_val=1., obj_val_dual=0., iterations=1, solve_time=.01)
    monkeypatch.setattr(CLARABEL, "solve_via_data", failed)
    if status == "NumericalError":
        with pytest.raises(cp.error.SolverError):
            b.solve()
    else:
        b.solve()
    assert all(v.value is None for v in b.prob.variables())
    assert all(v.value is None for c in b.prob.constraints for v in c.dual_variables)
    assert b.preparation_evidence.native["status"] == status
    assert not b.preparation_evidence.checks["restoration_available"]
    if with_lossy:
        assert extract_results(b)["ls_p_ch"] is None


@pytest.mark.parametrize("with_lossy", [False, True])
def test_changed_parameter_recanonicalized_and_exception_cleanup(monkeypatch, with_lossy):
    from cvxpy.reductions.solvers.conic_solvers.clarabel_conif import CLARABEL
    b = build_opf(case9(), formulation="singlenode_dc", loads=[Load(bus=5, p_load_mw=100., device_id="demand")],
                  lossy_storage=_lossy_units() if with_lossy else None,
                  options=OPFOptions(numerical_preparation=NumericalPreparation(canonical_scaling="joint5")))
    b.solve()
    previous = b.preparation_evidence
    original = CLARABEL.solve_via_data
    seen = []
    def captured(self, data, *args, **kwargs):
        seen.append(data["b"].copy())
        return original(self, data, *args, **kwargs)
    monkeypatch.setattr(CLARABEL, "solve_via_data", captured)
    b.solve()
    first_rhs = seen[-1].copy()
    demand = next(p for p in b.prob.parameters() if p.name() == "load_inv_base_mva")
    demand.value = demand.value * 1.2
    b.solve()
    assert b.preparation_evidence is not previous
    assert not np.array_equal(seen[-1], first_rhs)
    def failed(*args, **kwargs):
        raise RuntimeError("injected native failure")
    monkeypatch.setattr(CLARABEL, "solve_via_data", failed)
    with pytest.raises(RuntimeError, match="injected native failure"):
        b.solve()
    assert b.preparation_evidence is None and b.prob.value is None
    assert all(v.value is None for v in b.prob.variables())


@pytest.mark.parametrize("formulation,policy", [
    ("ac", NumericalPreparation(normalize_device_limits=True)),
    ("socp", NumericalPreparation(normalize_device_limits=True)),
    ("socp", NumericalPreparation(canonical_scaling="joint5")),
    ("ac", NumericalPreparation(exact_fixed_boxes=True)),
    ("socp", NumericalPreparation(exact_fixed_boxes=True, canonical_scaling="joint5")),
])
@pytest.mark.parametrize("with_lossy", [False, True])
def test_no_fixed_coordinate_policies_are_identity_maps(formulation, policy, with_lossy):
    b = build_opf(case9(), formulation=formulation, options=OPFOptions(numerical_preparation=policy),
                  lossy_storage=_lossy_units() if with_lossy else None)
    b.solve()
    assert b.prob.status == "optimal"
    assert b.preparation_evidence.coordinates.fixed.size == 0
    assert b.preparation_evidence.coordinates.dropped.size == 0


@pytest.mark.parametrize("status", [2, -1, -2])
@pytest.mark.parametrize("with_lossy", [False, True])
def test_ac_failure_never_publishes_native_reduced_values(monkeypatch, status, with_lossy):
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    b = build("ac", with_lossy=with_lossy)
    b.solve()
    assert all(v.value is not None for v in b.prob.variables())
    def failed(self, data, *args, **kwargs):
        return dict(status=status, x=data["x0"].copy(), obj_val=1., num_iters=0)
    monkeypatch.setattr(IPOPT, "solve_via_data", failed)
    if status == -2:
        with pytest.raises(cp.error.SolverError):
            b.solve()
    else:
        b.solve()
        assert b.prob.status == IPOPT.STATUS_MAP[status]
    assert all(v.value is None for v in b.prob.variables())
    assert b.preparation_evidence.native["status"] == status
    assert not b.preparation_evidence.checks["restoration_available"]


@pytest.mark.parametrize("with_lossy", [False, True])
def test_ac_derivative_cache_is_ephemeral_and_native_start_is_exact(monkeypatch, with_lossy):
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    b = build("ac", "vectorized", with_storage=True, with_lossy=with_lossy)
    ids = tuple(v.id for v in b.prob.variables())
    original = IPOPT.solve_via_data
    captured = []
    def checked(self, data, warm, verbose, options, solver_cache=None):
        assert not warm
        assert set(solver_cache) == {"oracles"}
        captured.append(data["x0"].copy())
        return original(self, data, warm, verbose, options, solver_cache)
    monkeypatch.setattr(IPOPT, "solve_via_data", checked)
    b.solve()
    e = b.preparation_evidence
    np.testing.assert_array_equal(captured[0], e.reduced_x0)
    assert tuple(v.id for v in b.prob.variables()) == ids
    assert set(ids) <= {item[0] for item in e.start_layout}
    assert len(e.start_layout) > len(ids)  # storage absolute-cost auxiliaries
    with pytest.raises(ValueError):
        e.reduced_x0.flags.writeable = True
    with pytest.raises(TypeError):
        e.native["status"] = -1


@pytest.mark.parametrize("failure", ["exception", "nonfinite", "missing"])
def test_lossy_ac_failed_second_solve_clears_physical_results(monkeypatch, failure):
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    b = build("ac", "vectorized", with_storage=True, with_lossy=True)
    b.solve()
    assert extract_results(b)["ls_p_ch"] is not None

    def failed(self, data, *args, **kwargs):
        if failure == "exception":
            raise RuntimeError("injected native failure")
        result = dict(status=0, obj_val=1., num_iters=0)
        if failure == "nonfinite":
            result["x"] = np.full(data["x0"].size, np.nan)
        return result

    monkeypatch.setattr(IPOPT, "solve_via_data", failed)
    with pytest.raises(cp.error.SolverError, match={
        "exception": "injected native failure", "nonfinite": "nonfinite", "missing": "KeyError"
    }[failure]):
        b.solve()
    assert b.prob.value is None
    assert all(v.value is None for v in b.prob.variables())
    result = extract_results(b)
    for key in ("ls_p_ch", "ls_p_dis", "ls_b_q", "ls_soc", "ls_b", "ls_split"):
        assert result[key] is None
    assert np.isnan(result["ls_cost"])


@pytest.mark.parametrize("formulation", ["ac", "socp"])
def test_lossy_user_coupling_survives_exact_box_reduction(formulation):
    b = build(formulation, "vectorized", NumericalPreparation(
        exact_fixed_boxes=True, normalize_device_limits=True,
        canonical_scaling="none" if formulation == "ac" else "joint5",
    ), with_storage=True, with_lossy=True)
    # This is not a defining Pg/ND box and must remain alongside lossy dynamics.
    coupling = b.variables["ls_p_ch"][0, 0] == b.variables["ls_p_ch"][1, 0] + 1.
    b.prob = cp.Problem(b.prob.objective, [*b.prob.constraints, coupling])
    b.solve()
    assert b.prob.status == "optimal"
    assert np.max(coupling.violation()) < 1e-6
    assert b.preparation_evidence.coordinates.dropped.size == 4
    assert b.preparation_evidence.checks["primal_residual"] < 1e-5
    if formulation == "socp":
        assert coupling.dual_value is not None and np.isfinite(coupling.dual_value)
        assert b.preparation_evidence.checks["stationarity"] < 1e-3


def test_unrelated_user_equality_is_retained_and_duals_reconstructed():
    from cvxopf.problem import OPFBuild
    from cvxopf._numerical_preparation import emit_exact_box
    from cvxopf._temporal_assembly import PreparedBoxBounds, VariableBoxFamily
    x = cp.Variable(2)
    lower, upper = np.array([2., -10.]), np.array([2., 10.])
    lower.flags.writeable = upper.flags.writeable = False
    box = emit_exact_box(x, PreparedBoxBounds(lower, upper),
                         VariableBoxFamily.DISPATCHABLE_P, "explicit")
    user = x[0] + x[1] == 5
    prob = cp.Problem(cp.Minimize(cp.sum_squares(x) + 7), [*box.constraints, user])
    b = OPFBuild(prob, {"Pg": x}, {}, "singlenode_dc", True,
                 _numerical_preparation=NumericalPreparation(exact_fixed_boxes=True, canonical_scaling="joint5"),
                 _exact_boxes=box.exact_boxes)
    b.solve()
    np.testing.assert_allclose(x.value, [2, 3], atol=1e-8)
    assert b.prob.value == pytest.approx(20)
    assert b.preparation_evidence.coordinates.dropped.size == 1
    assert user.dual_value == pytest.approx(-6, abs=1e-6)
    assert box.constraints[0].dual_value[0] == pytest.approx(2, abs=1e-6)
