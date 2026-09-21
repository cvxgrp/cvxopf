"""Vectorized AC compatibility and independent electrical/temporal checks."""

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import build_opf, build_opf_multistep, extract_results, OPFOptions
from cvxopf.generator import DispatchableGenerator
from cvxopf.hvdc import HVDCLink
from cvxopf.load import Load
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.storage import StorageUnitIdeal
from cvxopf.testcases import case9
from cvxopf.network import make_branch_admittance, reindex_case_to_consecutive
from cvxopf._hierarchical_solver import _complete_start, _assign_start
from experiments.case118_annual_hierarchy.streaming_runner import complete_flat_start, assign_start


def pair(T=3, case=None, **kwargs):
    case = case9() if case is None else case
    if 'loads' not in kwargs:
        multiplier = np.linspace(.8, 1.1, T)[:, None]
        kwargs.setdefault('df_P', pd.DataFrame(multiplier * case['bus'][:, 2]))
        kwargs.setdefault('df_Q', pd.DataFrame(multiplier * case['bus'][:, 3]))
    return [build_opf_multistep(case, T=T, formulation='ac', temporal_assembly=mode, **kwargs)
            for mode in ('stepwise', 'vectorized')]


def solve(build):
    # Inherit the Case118 starting-point helper, including every lifted variable.
    assign_start(build, complete_flat_start(build))
    build.solve(max_iter=400)
    result = extract_results(build)
    assert result['status'] == cp.OPTIMAL
    return result


def audit(build, result, case=None):
    """Reconstruct currents from complex voltage, independently of lifted P/Q."""
    voltage = result['Vm'] * np.exp(1j * np.deg2rad(result['Va_deg']))
    injection = build.data['baseMVA'] * voltage * np.conj(voltage @ build.data['Ybus'].T)
    np.testing.assert_allclose(result['p_net'], injection.real, atol=2e-4)
    np.testing.assert_allclose(result['q_net'], injection.imag, atol=2e-4)
    reindexed, _ = reindex_case_to_consecutive(case9() if case is None else case)
    adm = make_branch_admittance(reindexed)
    vf, vt = voltage[:, adm.from_bus], voltage[:, adm.to_bus]
    for end, value, current in [('from', vf, adm.yff*vf + adm.yft*vt),
                                 ('to', vt, adm.ytf*vf + adm.ytt*vt)]:
        power = build.data['baseMVA'] * value * np.conj(current)
        np.testing.assert_allclose(result[f'branch_p_{end}'], power.real, atol=2e-4)
        np.testing.assert_allclose(result[f'branch_q_{end}'], power.imag, atol=2e-4)
    expected_p = result['Pg'] @ build.data['Cg'].T - result['p_load_served'] @ build.data['Cload'].T
    expected_q = result['Qg'] @ build.data['Cg'].T - result['q_load_served'] @ build.data['Cload'].T
    for key, incidence in [('b', 'Cs'), ('p_nd', 'Cnd')]:
        if key in result and result[key] is not None:
            reactive = 'b_q' if key == 'b' else 'q_nd'
            expected_p += result[key] @ build.data[incidence].T
            expected_q += result[reactive] @ build.data[incidence].T
    if 'p_hvdc_in' in result and result['p_hvdc_in'] is not None:
        expected_p += result['p_hvdc_in'] @ build.data['Ch_from'].T
        expected_p += result['p_hvdc_out'] @ build.data['Ch_to'].T
    np.testing.assert_allclose(expected_p, injection.real, atol=2e-4)
    np.testing.assert_allclose(expected_q, injection.imag, atol=2e-4)
    if 'b' in result:
        initial = build.data['storage_initial_soc']
        np.testing.assert_allclose(np.diff(np.vstack([initial, result['soc']]), axis=0),
                                   -build.data['storage_delta']*result['b'], atol=1e-5)
        assert np.max(result['b']**2 + result['b_q']**2 - build.data['storage_apparent_power_rating']**2) < 1e-3
    if 'p_nd' in result:
        assert np.max(result['p_nd']**2 + result['q_nd']**2 - build.data['nd_apparent_power_rating']**2) < 1e-3


@pytest.mark.parametrize('T', [1, 3])
@pytest.mark.parametrize('sparse', [True, False])
def test_sparse_and_dense_results_and_network_physics(T, sparse):
    step, vector = pair(T, options=OPFOptions(sparse_pq=sparse))
    a, b = solve(step), solve(vector)
    assert a.keys() == b.keys()
    assert a['objective'] == pytest.approx(b['objective'], rel=1e-7)
    for key in ('Pg', 'Qg', 'Vm', 'Va_deg', 'branch_p_from', 'branch_q_to'):
        np.testing.assert_allclose(a[key], b[key], atol=3e-4)
    audit(vector, b)
    assert vector.variables['v'].shape == (9, T)
    assert vector.variables['Pg'].attributes['bounds'] is None
    assert vector.variables['Qg'].attributes['bounds'] is None
    assert vector.variables['v'].attributes['bounds'][0].strides[-1] == 0
    assert vector.canonicalization_backend == 'DNLP_IPOPT'
    assert b['Vm'].shape == (T, 9)
    if not sparse:
        dense = vector.variables['P'].value.reshape(9, 9, T)
        np.testing.assert_allclose(dense.sum(axis=1).T * 100, b['p_net'], atol=1e-5)


def test_t1_single_step_parity():
    c = case9()
    single = build_opf(c)
    _, vector = pair(1, df_P=pd.DataFrame([c['bus'][:, 2]]), df_Q=pd.DataFrame([c['bus'][:, 3]]))
    a, b = solve(single), solve(vector)
    for key in ('Pg', 'Qg', 'Vm', 'Va_deg', 'p_net', 'q_net', 'branch_s_from'):
        np.testing.assert_allclose(a[key], b[key][0], atol=3e-4)
    assert a['objective'] == pytest.approx(b['objective'], rel=1e-7)


POLICIES = [{}, {'terminal_constraint': 'equality'}, {'terminal_constraint': 'shortfall'},
            {'terminal_cost': 'linear', 'terminal_weight': 25},
            {'terminal_cost': 'quadratic', 'terminal_weight': .1},
            {'terminal_cost': 'shortfall_linear', 'terminal_weight': 25},
            {'terminal_cost': 'shortfall_quadratic', 'terminal_weight': .1}]


@pytest.mark.parametrize('policy', POLICIES)
def test_devices_terminal_policy_shedding_identity_and_costs(policy):
    kwargs = dict(delta=.5,
        loads=[Load(bus=5, p_load_mw=0, device_id='a', shedding_cost_per_mwh=1000,
                    max_shed_fraction=.8), Load(bus=7, p_load_mw=0, device_id='b')],
        df_load_p=pd.DataFrame({'b': [50, 50, 50], 'a': [10, 180, 30]}),
        df_load_q=pd.DataFrame({'b': [5, 5, 5], 'a': [1, 18, 3]}),
        generators=[DispatchableGenerator(bus=3, p_max_mw=120, q_min_mvar=-200,
                    q_max_mvar=200, cost_coeffs=(7, 2, .1))],
        storage=[StorageUnitIdeal(bus=9, apparent_power_rating=20, capacity=50,
                    initial_soc=25, aging_weight=.01, device_id='battery',
                    terminal_soc=25 if policy else None, **policy)],
        nondispatchable=[NondispatchableUnit(bus=1, p_available=10,
                    apparent_power_rating=30, device_id='solar')],
        df_nd=pd.DataFrame({'solar': [40, 0, 20]}))
    step, vector = pair(**kwargs)
    a, b = solve(step), solve(vector)
    assert a.keys() == b.keys()
    assert b['objective'] == pytest.approx(a['objective'], rel=1e-5)
    for build, result in [(step, a), (vector, b)]:
        audit(build, result)
        assert result['p_load_shed'][1, 0] > 0
        np.testing.assert_allclose(result['q_load_shed'], .1*result['p_load_shed'], atol=1e-5)
        generation = .5 * np.sum(7 + 2*result['Pg'] + .1*result['Pg']**2)
        cycling = .5 * .01*np.abs(result['b']).sum()
        shedding = .5 * 1000*result['p_load_shed'].sum()
        e = result['soc'][-1, 0] - 25
        cost = policy.get('terminal_cost', '')
        deviation = max(-e, 0) if 'shortfall' in cost else abs(e)
        terminal = policy.get('terminal_weight', 0) * (deviation**2 if 'quadratic' in cost else deviation)
        assert result['objective'] == pytest.approx(generation+cycling+shedding+terminal, abs=.002)
        if policy.get('terminal_constraint') == 'equality':
            assert result['soc'][-1, 0] == pytest.approx(25, abs=1e-5)
        if policy.get('terminal_constraint') == 'shortfall':
            assert result['soc'][-1, 0] >= 25 - 1e-5
    for name in ('b', 'b_q', 'soc', 'p_nd', 'q_nd', 'load_shed_fraction'):
        assert vector.variables[name].attributes['bounds'] is None


@pytest.mark.parametrize('direction', [-1, 0, 1])
def test_hvdc_time_varying_bounds_and_signed_losses(direction):
    lower, upper = ([-10, -8, -6], [-2, -1, -3]) if direction < 0 else ([2, 1, 3], [10, 8, 6])
    if direction == 0:
        lower = [-10, 0, -6]
        upper = [10, 0, 6]
    link = HVDCLink(from_bus=1, to_bus=2, p_min_mw=-10, p_max_mw=10,
                    loss_percent=0 if direction == 0 else 3, device_id='link')
    step, vector = pair(hvdc=[link], df_hvdc_min=pd.DataFrame({'link': lower}),
                        df_hvdc_max=pd.DataFrame({'link': upper}))
    a, b = solve(step), solve(vector)
    assert a['objective'] == pytest.approx(b['objective'], rel=1e-6)
    audit(vector, b)
    np.testing.assert_allclose(b['p_hvdc_out'], -(0.97 if direction < 0 else 1/0.97 if direction > 0 else 1)*b['p_hvdc_in'], atol=1e-5)
    assert np.all(b['p_hvdc_in'] >= np.asarray(lower)[:, None]-1e-5)
    assert np.all(b['p_hvdc_in'] <= np.asarray(upper)[:, None]+1e-5)


def test_static_inputs_voltage_setpoints_and_hierarchy_initialization():
    step, vector = pair(loads=[Load(bus=5, p_load_mw=100, q_load_mvar=20, device_id='load')],
        nondispatchable=[NondispatchableUnit(bus=3, p_available=20, apparent_power_rating=30)],
        options=OPFOptions(enforce_vset=True))
    for build in (step, vector):
        _assign_start(build, _complete_start(build))
        assert all(v.value is not None for v in build.prob.variables())
    a, b = solve(step), solve(vector)
    audit(vector, b)
    assert a['objective'] == pytest.approx(b['objective'], rel=1e-6)
    np.testing.assert_allclose(b['Vm'][:, :3], 1, atol=1e-5)
    assert vector.data['Pd_series'].strides[0] == 0
    assert vector.data['Qd_series'].strides[0] == 0
    assert vector.data['nd_available'].strides[0] == 0


def test_disabled_flat_initialization_accepts_explicit_hierarchy_start():
    _, vector = pair(options=OPFOptions(init_flat=False))
    assert vector.variables['theta'].value is None
    assert vector.variables['v'].value is None
    _assign_start(vector, _complete_start(vector))
    assert all(variable.value is not None for variable in vector.prob.variables())
    vector.solve(max_iter=400)
    result = extract_results(vector)
    assert result['status'] == cp.OPTIMAL
    audit(vector, result)


def test_graph_object_counts_do_not_grow_with_horizon():
    _, short = pair(3)
    _, long = pair(168)
    assert len(short.prob.variables()) == len(long.prob.variables())
    assert len(short.prob.constraints) == len(long.prob.constraints)
    assert len(short.prob.parameters()) == len(long.prob.parameters())


@pytest.mark.parametrize('failure', ['unsolved', 'infeasible', 'solver_error'])
def test_unavailable_results_and_solver_failure(failure, monkeypatch):
    step, vector = pair(loads=[Load(bus=5, p_load_mw=10000 if failure == 'infeasible' else 100,
                                   device_id='load')])
    if failure == 'infeasible':
        for build in (step, vector):
            build.solve(max_iter=100)
            assert build.prob.status == cp.INFEASIBLE
    elif failure == 'solver_error':
        def fail(*args, **kwargs):
            raise cp.error.SolverError('controlled failure')
        monkeypatch.setattr(cp.Problem, 'solve', fail)
        for build in (step, vector):
            with pytest.raises(cp.error.SolverError):
                build.solve()
    a, b = extract_results(step), extract_results(vector)
    assert a.keys() == b.keys()
    assert a['Pg'] is None and b['Pg'] is None
    if failure == 'infeasible':
        assert a['Vm'] is None and b['Vm'] is None
    else:
        # Preserve the existing unsolved schema's retained flat voltage values.
        np.testing.assert_array_equal(a['Vm'], b['Vm'])
    assert a['branch_p_from'] is None and b['branch_p_from'] is None
