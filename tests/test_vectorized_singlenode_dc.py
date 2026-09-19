"""Public-contract and independent physical checks for single-node vectorization."""

import warnings

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import build_opf, build_opf_multistep, extract_results
from cvxopf.generator import DispatchableGenerator
from cvxopf.hvdc import HVDCLink
from cvxopf.load import Load
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.storage import StorageUnitIdeal
from cvxopf.testcases import case9, make_singlenode_case


def build_pair(T=3, case=None, **kwargs):
    case = case9() if case is None else case
    if 'loads' not in kwargs and 'df_P' not in kwargs:
        kwargs['df_P'] = pd.DataFrame(
            np.linspace(.8, 1.2, T)[:, None] * case['bus'][:, 2]
        )
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        return [build_opf_multistep(case, T=T, formulation='singlenode_dc',
                    temporal_assembly=assembly, **kwargs)
                for assembly in ('stepwise', 'vectorized')]


def solve(build):
    build.solve(tol_gap_abs=1e-10, tol_gap_rel=1e-10, tol_feas=1e-10)
    return extract_results(build)


def assert_results_equal(left, right):
    assert left.keys() == right.keys()
    for name, value in left.items():
        if value is None or isinstance(value, str):
            assert value == right[name], name
        elif np.asarray(value).dtype.kind in 'biufc':
            assert np.shape(value) == np.shape(right[name]), name
            np.testing.assert_allclose(value, right[name], atol=3e-5, rtol=1e-8,
                                       equal_nan=True, err_msg=name)
        else:
            np.testing.assert_array_equal(value, right[name], err_msg=name)


@pytest.mark.parametrize('T', [1, 3, 24])
def test_imported_loads_match_and_single_node_axis_is_preserved(T):
    step, vector = build_pair(T)
    actual = solve(vector)
    assert_results_equal(solve(step), actual)
    assert actual['Pg'].shape == (T, 3)
    assert actual['p_net'].shape == (T,)
    assert vector.expressions['p_net'].shape == (1, T)
    assert vector.variables['Pg'].shape == (3, T)
    assert vector.variables['Pg'].attributes['bounds'] is not None
    assert vector.canonicalization_backend == 'SCIPY'
    assert step.temporal_assembly == 'stepwise'
    assert vector.prob.is_dcp()
    assert not {'p_flows', 'Vm', 'Qg', 'b_q', 'q_nd'}.intersection(actual)
    np.testing.assert_allclose(actual['Pg'].sum(axis=1), actual['p_load'].sum(axis=1), atol=1e-6)
    np.testing.assert_allclose(actual['p_net'], 0, atol=1e-6)
    np.testing.assert_allclose(vector.data['Pd_series'], step.data['Pd_series'])


def test_t1_matches_single_step_with_devices_and_boundary_state():
    kwargs = dict(
        storage=[StorageUnitIdeal(bus=7, apparent_power_rating=30, capacity=80,
                                  initial_soc=40, terminal_soc=35,
                                  terminal_cost='quadratic', terminal_weight=.05)],
        nondispatchable=[NondispatchableUnit(bus=5, p_available=20, apparent_power_rating=25)],
    )
    single = build_opf(case9(), formulation='singlenode_dc', **kwargs)
    _, vector = build_pair(1, df_P=pd.DataFrame([case9()['bus'][:, 2]]), **kwargs)
    a, b = solve(single), solve(vector)
    for name in ('Pg', 'p_net', 'soc', 'b', 'p_nd', 'p_load', 'curtailment'):
        np.testing.assert_allclose(a[name], b[name][0], atol=3e-5)
    assert a['objective'] == pytest.approx(b['objective'], rel=1e-8)
    assert vector.variables['soc'].shape == (1, 2)
    np.testing.assert_allclose(vector.variables['soc'].value[:, 0], 40, atol=1e-6)
    np.testing.assert_allclose(b['soc'], vector.variables['soc'].value[:, 1:].T)


POLICIES = [({}, lambda e: 0),
    ({'terminal_constraint': 'equality'}, lambda e: 0),
    ({'terminal_constraint': 'shortfall'}, lambda e: 0),
    ({'terminal_cost': 'linear', 'terminal_weight': 25}, lambda e: 25 * abs(e-25)),
    ({'terminal_cost': 'quadratic', 'terminal_weight': .05}, lambda e: .05 * (e-25)**2),
    ({'terminal_cost': 'shortfall_linear', 'terminal_weight': 25}, lambda e: 25 * max(25-e, 0)),
    ({'terminal_cost': 'shortfall_quadratic', 'terminal_weight': .05}, lambda e: .05 * max(25-e, 0)**2)]


@pytest.mark.parametrize('policy,terminal_cost', POLICIES)
def test_components_terminal_policies_and_independent_costs(policy, terminal_cost):
    delta = .5
    kwargs = dict(delta=delta,
        loads=[Load(bus=5, p_load_mw=0, device_id='a', shedding_cost_per_mwh=1000,
                    max_shed_fraction=.8), Load(bus=7, p_load_mw=0, device_id='b')],
        # Reversed columns must align by device identity before collapsing buses.
        df_load_p=pd.DataFrame({'b': [80, 80, 80], 'a': [10, 180, 30]}),
        df_load_q=pd.DataFrame({'b': [8, 8, 8], 'a': [1, 18, 3]}),
        generators=[DispatchableGenerator(bus=3, p_max_mw=120, cost_coeffs=(7, 2, .1))],
        storage=[StorageUnitIdeal(bus=9, apparent_power_rating=20, capacity=50,
                    initial_soc=25, aging_weight=.01, device_id='battery',
                    terminal_soc=25 if policy else None, **policy)],
        nondispatchable=[NondispatchableUnit(bus=1, p_available=10,
                    apparent_power_rating=30, device_id='solar')],
        df_nd=pd.DataFrame({'solar': [40, 0, 20]}))
    step, vector = build_pair(**kwargs)
    result = solve(vector)
    assert result['status'] == 'optimal'
    assert_results_equal(solve(step), result)
    pg, power, soc, nd = [result[name] for name in ('Pg', 'b', 'soc', 'p_nd')]
    shed = result['p_load_shed']
    served = kwargs['df_load_p'][['a', 'b']].to_numpy(dtype=float, copy=True)
    served[:, 0] -= shed[:, 0]
    np.testing.assert_allclose(pg.sum(axis=1) + power.sum(axis=1) + nd.sum(axis=1),
                               served.sum(axis=1), atol=1e-6)
    np.testing.assert_allclose(np.diff(np.vstack([[25], soc]), axis=0), -delta*power, atol=1e-6)
    assert np.max(np.abs(power)) <= 20 + 1e-6
    assert soc.min() >= -1e-6 and soc.max() <= 50 + 1e-6
    assert np.all(nd[:, 0] <= np.minimum([40, 0, 20], 30) + 1e-6)
    assert shed[1, 0] > 0  # Exercise active shedding, not only a dormant channel.
    generation = delta*np.sum(7 + 2*pg + .1*pg**2)
    cycling = delta*.01*np.abs(power).sum()
    shedding = delta*1000*shed.sum()
    terminal = terminal_cost(soc[-1, 0])
    assert result['objective'] == pytest.approx(generation+cycling+shedding+terminal, abs=1e-5)
    for name, expected in [('generator_cost', generation), ('storage_cost', cycling),
                           ('load_shedding_cost', shedding)]:
        assert float(vector.expressions[name].value) == pytest.approx(expected, abs=1e-5)
    np.testing.assert_allclose(vector.data['Pd_series'], [.9, 2.6, 1.1])
    np.testing.assert_array_equal(vector.data['Cload'], [[1, 1]])
    assert vector.data['source_nb'] == 9 and vector.data['nb'] == 1
    for name in ('gen_bus', 'storage_bus', 'nd_bus'):
        assert np.all(vector.data[name] == 0)


def test_static_fallback_aggregates_all_loads_and_keeps_broadcast_provenance():
    kwargs = dict(loads=[Load(bus=5, p_load_mw=70, device_id='a'),
                         Load(bus=9, p_load_mw=110, device_id='b')],
                  nondispatchable=[NondispatchableUnit(bus=3, p_available=20,
                                                      apparent_power_rating=30)])
    step, vector = build_pair(T=3, **kwargs)
    assert_results_equal(solve(step), solve(vector))
    np.testing.assert_allclose(vector.data['Pd_series'], 1.8)
    assert vector.data['Pd_series'].strides == (0,)
    assert vector.data['load_p_temporal_class'] == 'static'
    assert vector.data['nd_available_temporal_class'] == 'static'
    assert vector.data['load_p_source_mw'].shape == (2,)
    assert vector.data['nd_available_source_mw'].shape == (1,)
    assert not vector.data['nd_available'].flags.owndata


@pytest.mark.parametrize('cost', [(7,), (7, 2), (7, 2, .1), 'pwl'])
def test_minimal_case_supported_costs_and_nonunit_delta(cost):
    generator = DispatchableGenerator(bus=1, p_max_mw=100,
        cost_coeffs=(7, 2, .1) if cost == 'pwl' else cost)
    if cost == 'pwl':
        generator.cost_type = 'piecewise_linear'
        generator.cost_coeffs = None
        generator.cost_points = [(0, 0), (50, 100), (100, 300)]
    case = make_singlenode_case(30, [generator])
    step, vector = build_pair(case=case, delta=.25)
    assert_results_equal(solve(step), solve(vector))


def test_no_loads_and_no_devices_preserve_empty_result_channels():
    case = case9()
    case['gen'][:, 9] = 0
    step, vector = build_pair(loads=[], case=case)
    assert_results_equal(solve(step), solve(vector))
    assert extract_results(vector)['p_net'].shape == (3,)
    assert extract_results(vector)['p_load'].shape == (3, 0)


def test_nonconsecutive_bus_ids_still_collapse_to_one_node():
    case = case9()
    case['bus'][:, 0] *= 10
    case['gen'][:, 0] *= 10
    case['branch'][:, :2] *= 10
    step, vector = build_pair(case=case, loads=[Load(bus=90, p_load_mw=100, device_id='load')])
    assert_results_equal(solve(step), solve(vector))
    assert vector.data['ext_to_int'][90] == 8
    assert np.all(vector.data['gen_bus'] == 0)


def test_hvdc_keeps_single_node_ignore_contract():
    link = HVDCLink(from_bus=1, to_bus=2, p_min_mw=0, p_max_mw=10)
    with warnings.catch_warnings(record=True) as caught:
        vector = build_opf_multistep(case9(), T=3, loads=[Load(bus=5, p_load_mw=100, device_id='load')],
            formulation='singlenode_dc', temporal_assembly='vectorized', hvdc=[link])
    assert not caught
    assert not any(name.startswith('p_hvdc') for name in vector.variables)
    assert solve(vector)['status'] == 'optimal'


def test_initialization_extra_constraints_and_source_graph_size():
    marker = cp.Variable()
    constraint = marker == 2
    _, short = build_pair(T=3, coupling_constraints=[constraint])
    _, long = build_pair(T=168, coupling_constraints=[constraint])
    assert short.prob.constraints[-1] is constraint
    assert len(short.prob.variables()) == len(long.prob.variables())
    assert len(short.prob.constraints) == len(long.prob.constraints)
    long.variables['Pg'].value = np.ones((3, 168))
    assert solve(long)['status'] == 'optimal'
    assert marker.value == pytest.approx(2)
    with pytest.raises(ValueError, match='SCIPY'):
        long.solve(canon_backend=cp.CPP_CANON_BACKEND)


@pytest.mark.parametrize('infeasible', [False, True])
def test_unavailable_primal_results_match(infeasible):
    kwargs = dict(loads=[Load(bus=5, p_load_mw=10000, device_id='load')]) if infeasible else {}
    step, vector = build_pair(**kwargs)
    if infeasible:
        step.solve()
        vector.solve()
    assert_results_equal(extract_results(step), extract_results(vector))
    assert extract_results(vector)['Pg'] is None
    assert extract_results(vector)['p_net'] is None
