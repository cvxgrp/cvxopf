"""Initial Case9 Tracy comparisons of stepwise and vectorized DC/AC networks.

Run from the repository root using uv run --extra dev python -m
experiments.m14_time_vectorization.compare_network_vectorization.
Each pair progresses through 3, 24 and 168 hours, in fresh worker processes.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import resource
import subprocess
import sys
import time
from unittest.mock import patch

import cvxpy as cp
import numpy as np

from cvxopf import build_opf_multistep, extract_results
from cvxopf.characterization import characterize_source_graph
from cvxopf.network import make_branch_admittance, reindex_case_to_consecutive
from cvxopf.testcases import case9
from experiments.case118_annual_hierarchy.streaming_runner import complete_flat_start, assign_start
from experiments.m14_time_vectorization.run_m14d_singlenode import (
    ROOT, START, END, SOURCE_SHA256, prepare_inputs, sha,
)


def audit(build, result):
    """Reconstruct nodal currents/flows, component bounds, dynamics and costs."""
    d = build.data
    pg, power, soc, nd = [np.asarray(result[k]) for k in ('Pg', 'b', 'soc', 'p_nd')]
    p = pg @ d['Cg'].T + power @ d['Cs'].T + nd @ d['Cnd'].T - result['p_load'] @ d['Cload'].T
    violations = {
        'storage_recurrence_mwh': np.max(np.abs(np.diff(np.vstack([[500.], soc]), axis=0)+power)),
        'terminal_soc_mwh': abs(soc[-1, 0]-500),
        'storage_energy_mwh': max(0, -soc.min(), soc.max()-1000),
        'generator_p_mw': max(0, np.max(d['Pgmin']*100-pg), np.max(pg-d['Pgmax']*100)),
        'renewable_availability_mw': max(0, -nd.min(), np.max(nd-d['nd_available'])),
    }
    if build.formulation == 'ac':
        v = result['Vm'] * np.exp(1j*np.deg2rad(result['Va_deg']))
        actual = 100 * v * np.conj(v @ d['Ybus'].T)
        q = result['Qg'] @ d['Cg'].T + result['b_q'] @ d['Cs'].T + result['q_nd'] @ d['Cnd'].T - result['q_load'] @ d['Cload'].T
        c, _ = reindex_case_to_consecutive(case9())
        adm = make_branch_admittance(c)
        vf, vt = v[:, adm.from_bus], v[:, adm.to_bus]
        sf, st = 100*vf*np.conj(adm.yff*vf+adm.yft*vt), 100*vt*np.conj(adm.ytf*vf+adm.ytt*vt)
        violations.update(
            real_balance_mw=np.max(np.abs(p-actual.real)),
            reactive_balance_mvar=np.max(np.abs(q-actual.imag)),
            branch_reporting_mva=max(np.max(np.abs(sf-(result['branch_p_from']+1j*result['branch_q_from']))),
                                     np.max(np.abs(st-(result['branch_p_to']+1j*result['branch_q_to'])))),
            branch_limit_mva=max(0, np.max(np.abs(sf)-adm.rate_a_mva), np.max(np.abs(st)-adm.rate_a_mva)),
            voltage_pu=max(0, np.max(c['bus'][:,12]-result['Vm']), np.max(result['Vm']-c['bus'][:,11])),
            generator_q_mvar=max(0, np.max(d['Qgmin']*100-result['Qg']), np.max(result['Qg']-d['Qgmax']*100)),
            storage_rating_mva=max(0, np.max(np.hypot(power,result['b_q'])-150)),
            renewable_rating_mva=max(0, np.max(np.hypot(nd,result['q_nd'])-d['nd_apparent_power_rating'])),
        )
    else:
        violations.update(
            real_balance_mw=np.max(np.abs(p+result['p_flows'] @ d['A'].T)),
            branch_limit_mw=max(0, np.max(np.abs(result['p_flows'])-d['f_max']*100)),
            storage_rating_mw=max(0, np.max(np.abs(power))-150),
            renewable_rating_mw=max(0,np.max(nd-d['nd_apparent_power_rating'])),
        )
    generation = np.sum(150+5*pg[:,0]+.11*pg[:,0]**2)+np.sum(600+1.2*pg[:,1]+.085*pg[:,1]**2)+np.sum(335+pg[:,2]+.1225*pg[:,2]**2)
    cycling = .01*np.abs(power).sum()
    loss_cost = 0 if build.formulation == 'ac' else float(np.sum(d['r']*(result['p_flows']/100)**2)*d['loss_weight'])
    violations['objective_absolute'] = abs(result['objective']-generation-cycling-loss_cost)
    violations = {key: float(value) for key, value in violations.items()}
    return {'residuals': violations, 'accepted': all(np.isfinite(v) and v < 1e-4 for v in violations.values()),
            'generation_cost': float(generation), 'cycling_cost': float(cycling), 'loss_cost': loss_cost}


def run_one(formulation, mode, horizon):
    scenario, kwargs = prepare_inputs(horizon)
    kwargs['formulation'] = formulation
    if formulation == 'ac':
        kwargs['df_Q'] = scenario.df_Q.iloc[:horizon]
    times = {}
    started = time.perf_counter()
    build = build_opf_multistep(case9(), temporal_assembly=mode, **kwargs)
    times['construction'] = time.perf_counter()-started
    if formulation == 'ac':
        import cyipopt
        from cvxpy.reductions.solvers.solving_chain import SolvingChain
        from cvxpy.reductions.solvers.nlp_solvers.nlp_solver import Oracles
        init_started = time.perf_counter()
        assign_start(build, complete_flat_start(build))
        times['initialization'] = time.perf_counter()-init_started
        chain_apply, oracle_init = SolvingChain.apply, Oracles.__init__
        native = cyipopt.Problem
        def timed_apply(self, *args, **kwargs):
            start = time.perf_counter()
            result = chain_apply(self, *args, **kwargs)
            times['reductions'] = time.perf_counter()-start
            return result
        def timed_oracle(self, *args, **kwargs):
            start = time.perf_counter()
            oracle_init(self, *args, **kwargs)
            times['derivative_oracles'] = time.perf_counter()-start
        class TimedProblem(native):
            def solve(self, *args, **kwargs):
                start = time.perf_counter()
                result = super().solve(*args, **kwargs)
                times['solver'] = time.perf_counter()-start
                return result
        before = time.perf_counter()
        with patch.object(SolvingChain, 'apply', timed_apply), patch.object(Oracles, '__init__', timed_oracle), patch.object(cyipopt, 'Problem', TimedProblem):
            build.solve(max_iter=1000, max_cpu_time=150.0)
        solve_wall = time.perf_counter()-before
        times['canonicalization'] = times['reductions']+times['derivative_oracles']
        times['interface_overhead'] = solve_wall-times['canonicalization']-times['solver']
    else:
        times['initialization'] = 0.0
        before = time.perf_counter()
        build.prob.get_problem_data(cp.CLARABEL, canon_backend=build.canonicalization_backend)
        times['canonicalization'] = time.perf_counter()-before
        before = time.perf_counter()
        build.solve()
        solve_wall = time.perf_counter()-before
        times['solver'] = float(build.prob.solver_stats.solve_time)
        times['interface_overhead'] = solve_wall-times['solver']
    before = time.perf_counter()
    result = extract_results(build)
    times['extraction'] = time.perf_counter()-before
    times['total'] = time.perf_counter()-started
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform == 'darwin' else 1024)
    if result['status'] != 'optimal':
        raise RuntimeError(f'{formulation}/{mode}/{horizon}: {result["status"]}')
    physical = audit(build, result)
    if not physical['accepted']:
        raise RuntimeError(f'Failed audit: {physical}')
    graph = asdict(characterize_source_graph(build))
    trajectory = {k: np.asarray(v).tolist() for k,v in result.items()
                  if isinstance(v,np.ndarray) and v.dtype.kind in 'fiu'}
    return dict(formulation=formulation, assembly=mode, horizon=horizon,
                backend=build.canonicalization_backend, seconds=times,
                peak_process_rss_mib=peak, status=result['status'], objective=result['objective'],
                audit=physical, graph={k:graph[k] for k in ('variable_object_count','constraint_object_count',
                    'parameter_object_count','scalar_variables','scalar_equalities','scalar_inequalities')},
                trajectory=trajectory)


def execution_metadata():
    """Capture settings and environment once, before launching a fresh ladder."""
    return dict(
        source_sha256=SOURCE_SHA256, start=START, end=END, horizons=[3,24,168],
        initialization='Case118 complete_flat_start + assign_start, using variable.value; no shifted start needed for independent full-horizon comparisons.',
        solver_options={'ac':{'max_iter':1000,'max_cpu_time':150.0},'lossy_dc':'CLARABEL defaults'},
        worker_wall_limit_seconds=180,
        versions={p:importlib.metadata.version(p) for p in ('cvxpy','numpy','pandas','cyipopt','clarabel')},
        metadata_capture={'timing':'before_execution', 'created_utc':datetime.now(timezone.utc).isoformat()},
    )


def collect(raw, output):
    """Retain completed measurements and censored outcomes without rerunning."""
    rows, comparisons = [], []
    for horizon in (3, 24, 168):
        for formulation in ('lossy_dc', 'ac'):
            pair = []
            for assembly in ('stepwise', 'vectorized'):
                path = raw/f'{formulation}_{assembly}_{horizon}.json'
                if path.exists():
                    row = json.loads(path.read_text())
                    pair.append(row)
                    rows.append({key:value for key,value in row.items() if key != 'trajectory'})
            complete = len(pair) == 2 and all(row['status'] == 'optimal' for row in pair)
            fields = {}
            relative = None
            if complete:
                a, b = pair
                relative = abs(a['objective']-b['objective'])/abs(a['objective'])
                for key in a['trajectory'].keys() & b['trajectory'].keys():
                    av, bv = np.asarray(a['trajectory'][key]), np.asarray(b['trajectory'][key])
                    if av.size and av.shape == bv.shape:
                        maximum = float(np.max(np.abs(bv-av)))
                        fields[key] = {'max_absolute':maximum,
                            'over_stepwise_peak':maximum/max(1.,float(np.max(np.abs(av))))}
            comparisons.append(dict(formulation=formulation,horizon=horizon,
                paired_numerical_comparison_available=complete,
                objective_relative_difference=relative,trajectory_differences=fields))
    source_hashes = json.loads((raw/'execution_source_hashes.json').read_text())
    metadata = json.loads((raw/'execution_metadata.json').read_text())
    output.write_text(json.dumps(dict(created_utc=datetime.now(timezone.utc).isoformat(),
        **metadata,
        scope='One fresh-process observation per formulation/assembly/horizon. Prefix ladder of selected week; timeouts are censored outcomes.',
        timing='AC canonicalization = DNLP reduction chain + derivative oracle construction; solver = cyipopt.solve wall including callbacks. DC canonicalization = get_problem_data; solver = solver-reported native time. Interface overhead is disjoint remainder of solve wall. Total includes construction, initialization, canonicalization, solver, overhead, extraction; excludes input preparation/imports/audits. RSS includes imports through extraction.',
        source_hashes=source_hashes,collector_sha256=sha(Path(__file__)),
        raw_artifact_hashes={p.name:sha(p) for p in sorted(raw.glob('*.json'))},
        runs=rows,comparisons=comparisons),indent=2,allow_nan=False)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', choices=['ac','lossy_dc'])
    parser.add_argument('--assembly', choices=['stepwise','vectorized'])
    parser.add_argument('--horizon', type=int, choices=[3,24,168])
    parser.add_argument('--collect', action='store_true', help='Summarize retained local outcomes without solving')
    parser.add_argument('--output', type=Path, default=ROOT/'experiments/m14_time_vectorization/AC_DC_COMPARISON_RESULTS.json')
    args = parser.parse_args()
    if args.worker:
        if args.assembly is None or args.horizon is None:
            parser.error('--worker requires --assembly and --horizon')
        print(json.dumps(run_one(args.worker,args.assembly,args.horizon),allow_nan=False))
        return
    raw = ROOT/'experiments/m14_time_vectorization/results/network_vectorization'
    raw.mkdir(parents=True,exist_ok=True)
    if args.collect:
        collect(raw, args.output)
        return
    # Keep the exact executed runner separate from later collection/report edits.
    paths = list((ROOT/'src/cvxopf').glob('*.py')) + [Path(__file__),
        ROOT/'experiments/m14_time_vectorization/run_m14d_singlenode.py',
        ROOT/'experiments/battery_terminal/scenario.py',ROOT/'experiments/battery_terminal/devices.py',
        ROOT/'experiments/case118_annual_hierarchy/streaming_runner.py']
    if any(raw.glob('*_3.json')):
        raise FileExistsError('Retained comparison outcomes exist; use --collect or move the output directory before rerunning')
    (raw/'execution_source_hashes.json').write_text(json.dumps({str(p.relative_to(ROOT)):sha(p) for p in paths},indent=2)+'\n')
    (raw/'execution_metadata.json').write_text(json.dumps(execution_metadata(),indent=2)+'\n')
    (raw/'runner_at_initial_execution.py').write_bytes(Path(__file__).read_bytes())
    for horizon in (3,24,168):
        all_accepted = True
        for formulation in ('lossy_dc','ac'):
            for assembly in ('stepwise','vectorized'):
                path = raw/f'{formulation}_{assembly}_{horizon}.json'
                try:
                    child = subprocess.run([sys.executable,'-m',__spec__.name,'--worker',formulation,
                        '--assembly',assembly,'--horizon',str(horizon)],capture_output=True,text=True,timeout=180,cwd=ROOT)
                    (path.with_suffix('.log')).write_text(child.stderr)
                    if child.returncode:
                        row = dict(formulation=formulation,assembly=assembly,horizon=horizon,
                                   status='worker_error',returncode=child.returncode)
                    else:
                        row = json.loads(child.stdout)
                except subprocess.TimeoutExpired as exc:
                    row = dict(formulation=formulation,assembly=assembly,horizon=horizon,
                               status='wall_timeout',wall_limit_seconds=180,solver_status=None)
                    path.with_suffix('.log').write_text(str(exc))
                path.write_text(json.dumps(row,allow_nan=False)+'\n')
                all_accepted &= row['status'] == 'optimal'
                collect(raw,args.output)
                print(f'{formulation} {assembly} T={horizon}: {row["status"]}',flush=True)
        if not all_accepted:
            break


if __name__ == '__main__':
    main()
