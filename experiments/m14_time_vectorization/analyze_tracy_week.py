"""Assemble the selected Tracy week, five solutions, and one failed AC mode.

Run ``uv run --extra notebook python -m experiments.m14_time_vectorization.
analyze_tracy_week --run-lossy-dc`` once to capture fresh stepwise/vectorized
lossy-DC results. Omit the flag to rebuild the analysis without solving.
The notebook reads the generated bundle and never launches an optimization.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'outputs/tracy_week_comparison'
HERE = Path(__file__).parent
LABELS = {
    'singlenode_dc_stepwise': 'Single-node · stepwise',
    'singlenode_dc_vectorized': 'Single-node · vectorized',
    'lossy_dc_stepwise': 'Lossy DC · stepwise',
    'lossy_dc_vectorized': 'Lossy DC · vectorized',
    'ac_vectorized': 'AC · vectorized',
}
COLORS = dict(zip(LABELS, ['#85b7de', '#2666a3', '#e9ac83', '#d46a32', '#268c82'], strict=True))
RESOURCE_GROUPS = {'DG solar': 'dist_solar_', 'Utility solar': 'utility_solar_', 'Utility wind': 'wind_'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def capture_lossy_dc():
    """Two fresh workers, using the original network runner and matched inputs."""
    from experiments.m14_time_vectorization.compare_network_vectorization import execution_metadata
    destination = OUT / 'lossy_dc_run'
    destination.mkdir(parents=True, exist_ok=False)
    runner = HERE / 'compare_network_vectorization.py'
    paths = list((ROOT/'src/cvxopf').glob('*.py')) + [runner, HERE/'run_m14d_singlenode.py',
        ROOT/'experiments/battery_terminal/scenario.py', ROOT/'experiments/battery_terminal/devices.py']
    metadata = execution_metadata()
    metadata.update(horizons=[168], formulations=['lossy_dc'],
                    solver_options={'lossy_dc': 'CLARABEL defaults'},
                    source_hashes={str(p.relative_to(ROOT)): sha(p) for p in paths})
    write(destination/'execution_metadata.json', metadata)
    for mode in ('stepwise', 'vectorized'):
        worker = subprocess.run([sys.executable, '-m',
            'experiments.m14_time_vectorization.compare_network_vectorization',
            '--worker', 'lossy_dc', '--assembly', mode, '--horizon', '168'],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        (destination/f'{mode}.log').write_text(worker.stderr)
        worker.check_returncode()
        row = json.loads(worker.stdout)
        write(destination/f'{mode}.json', row)
        print(f'Lossy DC {mode}: {row["status"]}, {row["seconds"]["total"]:.3f} s, objective {row["objective"]:.6f}', flush=True)


def series(bundle, key, quantity, channel='Total'):
    """Return hours, values, units; SoC is indexed at all 169 boundaries."""
    row = bundle['solutions'][key]
    t = {k: np.asarray(v) for k, v in row['trajectory'].items()}
    inputs = bundle['inputs']
    availability = np.asarray(inputs['renewable_availability_mw'])
    ids = inputs['renewable_ids']
    units = 'MW'
    if quantity == 'Battery power':
        values = t['b'].sum(axis=1)
    elif quantity == 'State of charge':
        values = np.r_[inputs['storage_initial_soc_mwh'], t['soc'].sum(axis=1)]
        units = 'MWh'
    elif quantity == 'Generation':
        values = t['Pg'].sum(axis=1) if channel == 'Total' else t['Pg'][:, inputs['generator_ids'].index(channel)]
    elif quantity in ('Renewable output', 'Curtailment'):
        array = t['p_nd'] if quantity == 'Renewable output' else availability-t['p_nd']
        if channel == 'Total':
            values = array.sum(axis=1)
        elif channel in RESOURCE_GROUPS:
            values = array[:, [i for i, name in enumerate(ids) if name.startswith(RESOURCE_GROUPS[channel])]].sum(axis=1)
        else:
            values = array[:, ids.index(channel)]
    elif quantity == 'Supply minus load':
        values = t['Pg'].sum(axis=1)+t['b'].sum(axis=1)+t['p_nd'].sum(axis=1)-np.asarray(inputs['load_mw'])
    else:
        raise ValueError(quantity)
    return np.arange(len(values)), values, units


def summarize(bundle):
    rows = []
    for key, solution in bundle['solutions'].items():
        _, generation, _ = series(bundle, key, 'Generation')
        _, curtailment, _ = series(bundle, key, 'Curtailment')
        _, battery, _ = series(bundle, key, 'Battery power')
        _, loss, _ = series(bundle, key, 'Supply minus load')
        cost = solution['costs']
        rows.append(dict(mode=LABELS[key], status=solution['status'], objective=solution['objective'],
                         generation_cost=cost['generation'], cycling_cost=cost['cycling'],
                         dc_loss_penalty=cost['dc_loss_penalty'],
                         generation_mwh=float(generation.sum()), curtailed_mwh=float(curtailment.sum()),
                         battery_throughput_mwh=float(np.abs(battery).sum()),
                         ac_real_losses_mwh=float(loss.sum()) if solution['formulation']=='ac' else None,
                         total_seconds=solution['seconds']['total'], peak_rss_mib=solution['peak_process_rss_mib'],
                         wall_limit_seconds=None))
    rows.append(dict(mode='AC · stepwise', status='wall_timeout (two attempts)', objective=None,
                     total_seconds=None, peak_rss_mib=None, wall_limit_seconds=1800))
    return rows


def assemble():
    """Validate provenance and shared inputs before writing the inspectable bundle."""
    from cvxopf import build_opf_multistep
    from cvxopf.testcases import case9
    from experiments.m14_time_vectorization.run_m14d_singlenode import prepare_inputs, SOURCE_SHA256, START, END
    from experiments.m14_time_vectorization.check_network_trajectories import assign_trajectory
    from experiments.m14_time_vectorization.compare_network_vectorization import audit

    scenario, kwargs = prepare_inputs(168)
    network_record = read(HERE/'AC_DC_COMPARISON_RESULTS.json')
    for name, expected in network_record['source_hashes'].items():
        source = ROOT/'outputs/network_vectorization/runner_at_initial_execution.py' if name.endswith('/compare_network_vectorization.py') else ROOT/name
        if sha(source) != expected:
            raise ValueError(f'AC source mismatch: {name}')
    single_path = ROOT/'outputs/singlenode_vectorization/trajectory_capture.json'
    single = read(single_path)
    single_record = read(HERE/'M14D_SINGLENODE_RESULTS.json')
    assert sha(HERE/'M14D_SINGLENODE_RESULTS.json') == single['original_record_sha256']
    for name in ('experiments/battery_terminal/scenario.py', 'experiments/battery_terminal/devices.py',
                 'experiments/m14_time_vectorization/run_m14d_singlenode.py'):
        assert sha(ROOT/name) == single['source_hashes'][name], name
    assert network_record['source_sha256'] == single_record['source_sha256'] == SOURCE_SHA256
    assert network_record['start'] == single_record['start'] == START
    assert network_record['end'] == single_record['end'] == END
    with np.load(ROOT/'outputs/singlenode_vectorization/inputs.npz') as saved:
        np.testing.assert_array_equal(saved['load'], scenario.df_P.sum(axis=1).to_numpy())
        np.testing.assert_array_equal(saved['renewable_availability'], scenario.df_nd.to_numpy())
        np.testing.assert_array_equal(saved['renewable_ids'], scenario.df_nd.columns)
    fresh = OUT/'lossy_dc_run'
    metadata = read(fresh/'execution_metadata.json')
    for name, expected in metadata['source_hashes'].items():
        assert sha(ROOT/name) == expected, name
    assert metadata['source_sha256'] == SOURCE_SHA256 and metadata['start'] == START and metadata['end'] == END
    inputs = dict(start=START, end=END, delta_hours=1, load_mw=scenario.df_P.sum(axis=1).tolist(),
                  load_by_bus_mw=scenario.df_P.to_numpy().tolist(), reactive_load_by_bus_mvar=scenario.df_Q.to_numpy().tolist(),
                  renewable_availability_mw=scenario.df_nd.to_numpy().tolist(), renewable_ids=scenario.df_nd.columns.tolist(),
                  generator_ids=[f'Generator {i+1} · bus {g.bus}' for i,g in enumerate(kwargs['generators'])],
                  generators=[asdict(g) for g in kwargs['generators']], storage=asdict(kwargs['storage'][0]),
                  renewable_units=[asdict(g) for g in kwargs['nondispatchable']],
                  storage_initial_soc_mwh=500., branch_ids=[f'{int(b[0])} → {int(b[1])}' for b in case9()['branch']])
    solutions = {}
    artifacts = [single_path, HERE/'M14D_SINGLENODE_RESULTS.json', HERE/'AC_DC_COMPARISON_RESULTS.json',
                 HERE/'AC_STEPWISE_RETRY_RESULTS.json', fresh/'execution_metadata.json',
                 ROOT/'outputs/singlenode_vectorization/inputs.npz']
    for raw in single['runs']:
        key = 'singlenode_dc_'+raw['assembly']
        solutions[key] = dict(raw, formulation='singlenode_dc', provenance='Retained single-node trajectory capture; timings from that capture',
                             costs=dict(generation=raw['generation_cost'], cycling=raw['cycling_cost'], dc_loss_penalty=0.))
    for mode in ('stepwise', 'vectorized'):
        path = fresh/f'{mode}.json'
        raw = read(path)
        solutions['lossy_dc_'+mode] = dict(raw, provenance='Fresh lossy-DC run for this comparison',
            costs=dict(generation=raw['audit']['generation_cost'], cycling=raw['audit']['cycling_cost'], dc_loss_penalty=raw['audit']['loss_cost']))
        artifacts.append(path)
    ac_path = ROOT/'outputs/network_vectorization/ac_vectorized_168.json'
    assert sha(ac_path) == network_record['raw_artifact_hashes'][ac_path.name]
    ac = read(ac_path)
    solutions['ac_vectorized'] = dict(ac, provenance='Retained original vectorized AC solve',
        costs=dict(generation=ac['audit']['generation_cost'], cycling=ac['audit']['cycling_cost'], dc_loss_penalty=0.))
    artifacts.append(ac_path)
    # Reconstruct each trajectory in its own current mathematical graph. Do not
    # compare AC feasibility against a DC model: those are different problems.
    checks = []
    for key, row in solutions.items():
        assert row['horizon']==168 and row['status']=='optimal'
        config = dict(kwargs, formulation=row['formulation'])
        if row['formulation']=='ac':
            config['df_Q'] = scenario.df_Q
        build = build_opf_multistep(case9(), temporal_assembly=row['assembly'], **config)
        if row['formulation']=='singlenode_dc':
            projections = []
            for name, values in row['trajectory'].items():
                array = np.asarray(values)/(100 if name=='Pg' else 1)
                variable = build.variables[name]
                if isinstance(variable, list):
                    for t, leaf in enumerate(variable):
                        value = leaf.project(array[t])
                        projections.append(float(np.max(np.abs(value-array[t]))))
                        leaf.value = value
                else:
                    array = np.vstack([[500.], array]) if name=='soc' else array
                    value = variable.project(array.T)
                    projections.append(float(np.max(np.abs(value-array.T))))
                    variable.value = value
            projection = max(projections)
        else:
            projection = assign_trajectory(build, row['trajectory'])
            assert projection < 1e-6
            physical = audit(build, {**{k: np.asarray(v) for k,v in row['trajectory'].items()}, 'objective': row['objective']})
            assert physical['accepted'], physical
        violation = max(float(np.max(c.violation())) for c in build.prob.constraints)
        relative = abs(float(build.prob.objective.value)-row['objective'])/abs(row['objective'])
        assert projection < 1e-6 and violation < 1e-4 and relative < 1e-7, (key, projection, violation, relative)
        checks.append(dict(mode=key, bound_projection_max=projection, constraint_violation_max=violation, objective_relative_error=relative))
    failures = [r for r in network_record['runs'] if r['formulation']=='ac' and r['assembly']=='stepwise' and r['horizon']==168]
    failures.append(read(HERE/'AC_STEPWISE_RETRY_RESULTS.json')['retry'])
    bundle = dict(created_utc=datetime.now(timezone.utc).isoformat(), inputs=inputs, solutions=solutions,
                  failed_mode=dict(label='AC · stepwise', attempts=failures), checks=checks,
                  provenance=dict(source_sha256=SOURCE_SHA256, fresh_execution=metadata,
                    artifacts={str(p.relative_to(ROOT)): sha(p) for p in artifacts}),
                  timing_note='Separate single runs on different dates; no controlled speedup estimate. Single-node times are from its trajectory capture, not the earlier timing-only report.')
    bundle['summary'] = summarize(bundle)
    write(OUT/'comparison.json', bundle)
    pd.DataFrame(bundle['summary']).to_csv(OUT/'summary.csv', index=False)
    write(HERE/'TRACY_WEEK_COMPARISON_RESULTS.json', {k:v for k,v in bundle.items() if k not in ('inputs','solutions')}
          | dict(bundle_sha256=sha(OUT/'comparison.json')))
    print(pd.DataFrame(bundle['summary'])[['mode','objective','curtailed_mwh','total_seconds']].to_string(index=False))
    return bundle


def input_figure(bundle, window):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    inputs = bundle['inputs']
    load = np.asarray(inputs['load_mw'])
    avail = np.asarray(inputs['renewable_availability_mw'])
    hours = np.arange(168)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, subplot_titles=['Load and net load', 'Available renewables · DG solar at bottom'])
    fig.add_trace(go.Scatter(x=hours, y=load, name='Load', line_color='#303b4a'), row=1,col=1)
    fig.add_trace(go.Scatter(x=hours,y=load-avail.sum(axis=1),name='Net load',line_color='#2666a3',fill='tonexty',fillcolor='rgba(38,102,163,0.5)'),row=1,col=1)
    for (label,prefix),color in zip(RESOURCE_GROUPS.items(), ['#cb7043','#e8ac32','#389d9b'], strict=True):
        values = avail[:,[i for i,name in enumerate(inputs['renewable_ids']) if name.startswith(prefix)]].sum(axis=1)
        fig.add_trace(go.Scatter(x=hours,y=values,name=label,stackgroup='renewables',line_color=color),row=2,col=1)
    fig.update_yaxes(title_text='MW')
    fig.update_xaxes(range=list(window))
    fig.update_xaxes(title_text='Hour since Dec 22, 2021 00:00 · fixed UTC−08:00',row=2,col=1)
    fig.update_layout(height=550, template='plotly_white',legend=dict(orientation='h',y=-.17),margin=dict(t=45,b=90))
    return fig


def comparison_figure(bundle, selected, baseline, quantity, channel, window):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    hours, base, unit = series(bundle, baseline, quantity, channel)
    fig = make_subplots(rows=2,cols=1,shared_xaxes=True,subplot_titles=[quantity+' · '+channel, 'Δ = selected − '+LABELS[baseline]])
    for key in selected:
        x, y, _ = series(bundle, key, quantity, channel)
        style = dict(color=COLORS[key],dash='dash' if key.endswith('stepwise') else 'solid')
        fig.add_trace(go.Scatter(x=x,y=y,name=LABELS[key],legendgroup=key,line=style),row=1,col=1)
        fig.add_trace(go.Scatter(x=x,y=y-base,name=LABELS[key],legendgroup=key,line=style,showlegend=False),row=2,col=1)
    if baseline not in selected:
        fig.add_trace(go.Scatter(x=hours,y=base,name=LABELS[baseline]+' (baseline)',line=dict(color=COLORS[baseline],dash='dot')),row=1,col=1)
    fig.add_hline(y=0,line_width=1,line_color='#aaa',row=2,col=1)
    fig.update_yaxes(title_text=unit)
    fig.update_xaxes(range=list(window))
    fig.update_xaxes(title_text='Hour since Dec 22 · SoC uses interval boundaries',row=2,col=1)
    fig.update_layout(height=530,template='plotly_white',legend=dict(orientation='h',y=-.2),margin=dict(t=45,b=110),hovermode='x unified')
    return fig


def difference_table(bundle, selected, baseline, quantity, channel, window):
    x, base, unit = series(bundle, baseline, quantity, channel)
    # Power is over [start,end); boundary states include both endpoints.
    mask = (x>=window[0]) & ((x<=window[1]) if quantity=='State of charge' else (x<window[1]))
    rows = []
    for key in selected:
        _, values, _ = series(bundle,key,quantity,channel)
        delta = values[mask]-base[mask]
        rows.append(dict(mode=LABELS[key], unit=unit, max_absolute_delta=float(np.abs(delta).max()),
                         mean_delta=float(delta.mean()), rms_delta=float(np.sqrt(np.mean(delta**2))),
                         energy_delta_mwh=float(delta.sum()) if unit=='MW' else None))
    return pd.DataFrame(rows)


def network_figure(bundle, channel, index, window):
    """Compare oriented real branch power, or inspect AC-only voltage/Q fields."""
    import plotly.graph_objects as go
    fig = go.Figure()
    for key, row in bundle['solutions'].items():
        trajectory = row['trajectory']
        if channel=='Branch real power':
            field = 'branch_p_from' if row['formulation']=='ac' else 'p_flows'
        else:
            field = {'Voltage magnitude':'Vm','Generator reactive power':'Qg',
                     'Renewable reactive power':'q_nd','Battery reactive power':'b_q'}[channel]
        if field in trajectory:
            fig.add_trace(go.Scatter(x=np.arange(168),y=np.asarray(trajectory[field])[:,index],name=LABELS[key],
                                    line=dict(color=COLORS[key],dash='dash' if key.endswith('stepwise') else 'solid')))
    unit = 'pu' if channel=='Voltage magnitude' else ('MW' if channel=='Branch real power' else 'MVAr')
    fig.update_layout(height=380,template='plotly_white',title=channel,hovermode='x unified',
                      yaxis_title=unit,xaxis_title='Hour since Dec 22',xaxis_range=list(window),legend=dict(orientation='h',y=-.2))
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-lossy-dc', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    if args.run_lossy_dc:
        capture_lossy_dc()
    assemble()


if __name__ == '__main__':
    main()
