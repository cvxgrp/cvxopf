"""Independent audit and timing checks for the network comparison runner."""

from types import SimpleNamespace
import json

import numpy as np
import pandas as pd
import pytest

from cvxopf import build_opf_multistep, extract_results
from cvxopf.testcases import case9
from experiments.battery_terminal.devices import make_dispatchable_generators, make_storage, make_nondispatchable_units
from experiments.m14_time_vectorization import compare_network_vectorization as comparison


def inputs(horizon):
    case = case9()
    p = pd.DataFrame(np.broadcast_to(case['bus'][:, 2], (horizon, 9)))
    q = pd.DataFrame(np.broadcast_to(case['bus'][:, 3], (horizon, 9)))
    nd = pd.DataFrame({'utility_solar_bus_1': np.full(horizon, 20.)})
    return SimpleNamespace(df_P=p, df_Q=q, df_nd=nd), dict(df_P=p, T=horizon,
        formulation='lossy_dc', storage=[make_storage(terminal_soc=500, terminal_constraint='equality')],
        generators=make_dispatchable_generators(), nondispatchable=make_nondispatchable_units([nd]), df_nd=nd)


def test_dc_audit_uses_declared_incidence_orientation():
    _, kwargs = inputs(3)
    build = build_opf_multistep(case9(), **kwargs)
    build.solve()
    result = extract_results(build)
    assert comparison.audit(build, result)['accepted']
    # Reversing a nonzero actual dispatch must fail the independent nodal check.
    result['p_flows'] = -result['p_flows']
    rejected = comparison.audit(build, result)
    assert not rejected['accepted']
    assert rejected['residuals']['real_balance_mw'] > 1


@pytest.mark.parametrize('formulation', ['lossy_dc', 'ac'])
def test_worker_phase_accounting_and_physical_audit(formulation, monkeypatch):
    monkeypatch.setattr(comparison, 'prepare_inputs', inputs)
    result = comparison.run_one(formulation, 'vectorized', 3)
    assert result['audit']['accepted']
    times = result['seconds']
    accounted = sum(times[k] for k in ('construction', 'initialization', 'canonicalization',
                                       'solver', 'interface_overhead', 'extraction'))
    assert times['total'] >= accounted
    assert times['total']-accounted < .05
    assert all(v >= 0 for v in times.values())
    if formulation == 'ac':
        assert times['canonicalization'] == times['reductions'] + times['derivative_oracles']
        assert result['backend'] == 'DNLP_IPOPT'


def test_recollection_preserves_execution_context(tmp_path, monkeypatch):
    retained = {'source_sha256': 'executed-source', 'start': 'executed-start',
        'end': 'executed-end', 'versions': {'cvxpy': 'executed-version'},
        'solver_options': {'ac': {'max_iter': 1000}}, 'worker_wall_limit_seconds': 180}
    (tmp_path/'execution_metadata.json').write_text(json.dumps(retained))
    (tmp_path/'execution_source_hashes.json').write_text('{}')
    (tmp_path/'ac_stepwise_168.json').write_text(json.dumps({
        'formulation':'ac', 'assembly':'stepwise', 'horizon':168, 'status':'wall_timeout'}))
    monkeypatch.setattr(comparison, 'START', 'unrelated-new-window')
    monkeypatch.setattr(comparison, 'SOURCE_SHA256', 'new-source')
    monkeypatch.setattr(comparison.importlib.metadata, 'version', lambda _: 'new-version')
    output = tmp_path.parent/(tmp_path.name+'-collected.json')
    comparison.collect(tmp_path, output)
    result = json.loads(output.read_text())
    for key, value in retained.items():
        assert result[key] == value
    assert result['runs'][0]['status'] == 'wall_timeout'
    assert not any(c['paired_numerical_comparison_available'] for c in result['comparisons'])
