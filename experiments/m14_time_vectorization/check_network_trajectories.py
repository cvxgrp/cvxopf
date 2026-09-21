"""Evaluate saved paired trajectories in both network representations.

This does not solve, average AC solutions, or claim global optimality. It checks
whether observed trajectory differences change the implemented feasible set.
Run after compare_network_vectorization, using the same production sources.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from cvxopf import build_opf_multistep
from cvxopf.testcases import case9
from experiments.m14_time_vectorization.run_m14d_singlenode import ROOT, prepare_inputs, sha


def assign_trajectory(build, trajectory):
    d = build.data
    fields = {key: np.asarray(value) for key, value in trajectory.items()}
    internal = dict(fields)
    internal['Pg'] = fields['Pg']/100
    if build.formulation == 'ac':
        internal.update(Qg=fields['Qg']/100, theta=np.deg2rad(fields['Va_deg']),
                        v=fields['Vm'], p=fields['p_net']/100, q=fields['q_net']/100)
        voltage = fields['Vm'] * np.exp(1j*np.deg2rad(fields['Va_deg']))
        pq = voltage[:,d['rows']] * np.conj(d['Ybus'][d['rows'],d['cols']]*voltage[:,d['cols']])
        internal.update(P_vec=pq.real, Q_vec=pq.imag)
    else:
        internal['p_flows'] = fields['p_flows']/100
    changes = []
    def assign(variable, value):
        value = value.reshape(variable.shape)
        projected = variable.project(value)
        changes.append(float(np.max(np.abs(projected-value))))
        variable.value = projected
    for name, variable in build.variables.items():
        values = internal[name]
        if isinstance(variable, list):
            for t, leaf in enumerate(variable):
                assign(leaf, values[t])
        else:
            values = np.vstack([[500.], values]) if name == 'soc' else values
            assign(variable, values.T)
    if build.formulation == 'ac':
        for end in ('from','to'):
            for channel in ('p','q'):
                source = f'branch_{channel}_{end}'
                expr = build.expressions[source+'_pu']
                values = fields[source]/100
                if isinstance(expr, list):
                    for t, leaf in enumerate(expr):
                        assign(leaf, values[t])
                else:
                    assign(expr, values.T)
    assert all(var.value is not None for var in build.prob.variables())
    return max(changes)


def main():
    record_path = ROOT/'experiments/m14_time_vectorization/AC_DC_COMPARISON_RESULTS.json'
    record = json.loads(record_path.read_text())
    for path, expected in record['source_hashes'].items():
        source = (ROOT/'outputs/network_vectorization/runner_at_initial_execution.py'
                  if path.endswith('/compare_network_vectorization.py') else ROOT/path)
        assert sha(source) == expected, path
    observations = []
    for comparison in record['comparisons']:
        formulation, horizon = comparison['formulation'], comparison['horizon']
        scenario, kwargs = prepare_inputs(horizon)
        kwargs['formulation'] = formulation
        if formulation == 'ac':
            kwargs['df_Q'] = scenario.df_Q.iloc[:horizon]
        for mode in ('stepwise','vectorized'):
            build = build_opf_multistep(case9(), temporal_assembly=mode, **kwargs)
            for source in ('stepwise','vectorized'):
                filename = f'{formulation}_{source}_{horizon}.json'
                path = ROOT/'outputs/network_vectorization'/filename
                assert sha(path) == record['raw_artifact_hashes'][filename]
                raw = json.loads(path.read_text())
                if raw['status'] != 'optimal':
                    continue
                projection = assign_trajectory(build, raw['trajectory'])
                residual = max(float(np.max(con.violation())) for con in build.prob.constraints)
                objective = float(build.prob.objective.value)
                relative = abs(objective-raw['objective'])/abs(raw['objective'])
                if projection > 1e-6 or residual > 1e-4 or relative > 1e-7:
                    raise RuntimeError((formulation,horizon,mode,source,projection,residual,relative))
                observations.append(dict(formulation=formulation,horizon=horizon,graph=mode,
                    source=source,bound_projection_max=projection,constraint_violation_max=residual,
                    objective=objective,source_objective_relative_error=relative))
    destination = Path(__file__).with_name('NETWORK_TRAJECTORY_CHECKS.json')
    destination.write_text(json.dumps(dict(comparison_record_sha256=sha(record_path),
        checker_sha256=sha(Path(__file__)),observations=observations),indent=2)+'\n')
    print(f'{len(observations)} actual-graph trajectory checks passed')


if __name__ == '__main__':
    main()
