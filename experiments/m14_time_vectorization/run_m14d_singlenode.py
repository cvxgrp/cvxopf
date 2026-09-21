"""Initial Case9/Tracy single-node vectorization timing and numerical comparison.

Run from the repository root with:
    uv run --extra dev python -m experiments.m14_time_vectorization.run_m14d_singlenode
Each representation/horizon runs once in a fresh process. This is an initial
performance observation, not a statistical speedup claim or solver qualification.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

import cvxpy as cp
import numpy as np

from cvxopf import build_opf_multistep, extract_results
from cvxopf.characterization import characterize_source_graph
from cvxopf.testcases import case9
from experiments.battery_terminal.devices import (
    make_dispatchable_generators, make_nondispatchable_units, make_storage,
)
from experiments.battery_terminal.scenario import (
    ScenarioConfig, generate_scenario, read_source_data, select_complete_window,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'experiments/battery_terminal/data/9q9wtp_gen_and_load.csv'
SOURCE_SHA256 = '45e11f061d736741b18334aea0e9525c355c1a13068c291c1db6ed2e614b1b6f'
START = '2021-12-22 00:00:00-08:00'
END = '2021-12-28 23:00:00-08:00'
HORIZONS = (3, 24, 168)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_inputs(horizon):
    if sha(SOURCE) != SOURCE_SHA256:
        raise ValueError('Tracy source differs from the selected source record')
    scenario = generate_scenario(select_complete_window(read_source_data(SOURCE), START, END))
    # One fleet sized from the full selected week, held fixed across prefixes.
    renewable = make_nondispatchable_units([scenario.df_nd])
    generators = make_dispatchable_generators()
    battery = make_storage(terminal_soc=500, terminal_constraint='equality')
    kwargs = dict(df_P=scenario.df_P.iloc[:horizon], T=horizon,
        formulation='singlenode_dc', delta=1,
        storage=[battery], generators=generators, nondispatchable=renewable,
        df_nd=scenario.df_nd.iloc[:horizon])
    return scenario, kwargs


def run_one(horizon, assembly):
    scenario, kwargs = prepare_inputs(horizon)
    renewable, generators = kwargs['nondispatchable'], kwargs['generators']
    start = time.perf_counter()
    build = build_opf_multistep(case9(), temporal_assembly=assembly, **kwargs)
    built = time.perf_counter()
    build.prob.get_problem_data(cp.CLARABEL, canon_backend=build.canonicalization_backend)
    canonicalized = time.perf_counter()
    build.solve()
    solved = time.perf_counter()
    result = extract_results(build)
    extracted = time.perf_counter()
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if result['status'] != cp.OPTIMAL:
        raise RuntimeError(f'{horizon}/{assembly}: {result["status"]}')
    pg, b, soc, nd = [np.asarray(result[k]) for k in ('Pg', 'b', 'soc', 'p_nd')]
    load = scenario.df_P.iloc[:horizon].to_numpy().sum(axis=1)
    availability = scenario.df_nd.iloc[:horizon].to_numpy()
    rating = np.array([unit.apparent_power_rating for unit in renewable])
    pmin = np.array([unit.p_min_mw for unit in generators])
    pmax = np.array([unit.p_max_mw for unit in generators])
    generation_cost = sum(np.sum(unit.cost_coeffs[0] + unit.cost_coeffs[1]*pg[:, i]
        + unit.cost_coeffs[2]*pg[:, i]**2) for i, unit in enumerate(generators))
    cycling_cost = .01 * np.abs(b).sum()
    audits = {
        'balance_max_abs_mw': float(np.max(np.abs(pg.sum(axis=1)+b.sum(axis=1)+nd.sum(axis=1)-load))),
        'soc_recurrence_max_abs_mwh': float(np.max(np.abs(np.diff(np.vstack([[500], soc]), axis=0)+b))),
        'terminal_soc_error_mwh': float(abs(soc[-1, 0]-500)),
        'generator_bound_violation_mw': float(max(0, np.max(pmin-pg), np.max(pg-pmax))),
        'storage_power_violation_mw': float(max(0, np.max(np.abs(b))-150)),
        'storage_energy_violation_mwh': float(max(0, -soc.min(), soc.max()-1000)),
        'renewable_bound_violation_mw': float(max(0, -nd.min(), np.max(nd-np.minimum(availability, rating)))),
        'objective_reconstruction_abs': float(abs(result['objective']-generation_cost-cycling_cost)),
    }
    if not all(np.isfinite(value) and value < 1e-4 for value in audits.values()):
        raise RuntimeError(f'Physical/cost audit failed: {audits}')
    graph = asdict(characterize_source_graph(build))
    return {
        'horizon': horizon, 'assembly': assembly, 'backend': build.canonicalization_backend,
        'seconds': {'construction': built-start, 'canonicalization': canonicalized-built,
            'solve_wall_after_canonicalization': solved-canonicalized,
            'solver_reported': build.prob.solver_stats.solve_time,
            'extraction': extracted-solved, 'total': extracted-start},
        'peak_process_rss_mib': peak_rss/(1024**2 if sys.platform == 'darwin' else 1024),
        'graph': {key: graph[key] for key in ('variable_object_count', 'constraint_object_count',
            'parameter_object_count', 'scalar_variables', 'scalar_equalities', 'scalar_inequalities')},
        'status': result['status'], 'objective': result['objective'],
        'generation_cost': float(generation_cost), 'cycling_cost': float(cycling_cost),
        'curtailed_energy_mwh': float(np.sum(availability-nd)), 'audits': audits,
        'trajectory': {key: np.asarray(result[key]).tolist() for key in ('Pg', 'b', 'soc', 'p_nd')},
    }


def probe_alternative_optima(horizon, pair):
    """Evaluate both solutions and their midpoint in both actual CVXPY graphs.

    The strictly convex generation cost fixes dispatch, but linear throughput
    and free renewable curtailment can leave battery/renewable schedules nonunique.
    This is a numerical flat-direction check, not a uniqueness certificate.
    """
    _, kwargs = prepare_inputs(horizon)
    trajectories = {
        row['assembly']: {key: np.asarray(value) for key, value in row['trajectory'].items()}
        for row in pair
    }
    trajectories['midpoint'] = {key: (trajectories['stepwise'][key]+trajectories['vectorized'][key])/2
                              for key in trajectories['stepwise']}
    observations = []
    for assembly in ('stepwise', 'vectorized'):
        build = build_opf_multistep(case9(), temporal_assembly=assembly, **kwargs)
        for label, trajectory in trajectories.items():
            projection_changes = []
            for key, array in trajectory.items():
                internal = array / build.data['baseMVA'] if key == 'Pg' else array
                if assembly == 'vectorized':
                    internal = np.vstack([[500.], internal]) if key == 'soc' else internal
                    variable = build.variables[key]
                    projected = variable.project(internal.T)
                    projection_changes.append(float(np.max(np.abs(projected-internal.T))))
                    variable.value = projected
                else:
                    for variable, value in zip(build.variables[key], internal, strict=True):
                        variable.value = value
            violation = max(float(np.max(constraint.violation())) for constraint in build.prob.constraints)
            objective = float(build.prob.objective.value)
            gap = abs(objective-pair[0]['objective']) / abs(pair[0]['objective'])
            projection_change = max(projection_changes, default=0.)
            if violation > 1e-4 or gap > 1e-7 or projection_change > 1e-6:
                raise RuntimeError(f'Alternative-schedule probe failed: {assembly}/{label}')
            observations.append(dict(graph=assembly, trajectory=label,
                max_constraint_violation=violation, objective=objective,
                objective_relative_gap=gap, bound_projection_max_change=projection_change))
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', choices=['stepwise', 'vectorized'])
    parser.add_argument('--horizon', type=int, choices=HORIZONS)
    parser.add_argument('--output', type=Path,
        default=ROOT/'experiments/m14_time_vectorization/M14D_SINGLENODE_RESULTS.json')
    args = parser.parse_args()
    if args.worker:
        if args.horizon is None:
            parser.error('--worker requires --horizon')
        print(json.dumps(run_one(args.horizon, args.worker)))
        return
    rows, comparisons = [], []
    for horizon in HORIZONS:
        pair = []
        for assembly in ('stepwise', 'vectorized'):
            completed = subprocess.run([sys.executable, '-m',
                'experiments.m14_time_vectorization.run_m14d_singlenode',
                '--worker', assembly, '--horizon', str(horizon)],
                cwd=ROOT, capture_output=True, text=True, check=True, timeout=180)
            row = json.loads(completed.stdout)
            pair.append(row)
            print(f'T={horizon} {assembly}: {row["seconds"]["total"]:.3f}s, '
                  f'objective={row["objective"]:.6f}', flush=True)
        step, vector = pair
        comparison = {'horizon': horizon,
            'objective_relative_difference': abs(step['objective']-vector['objective'])/abs(step['objective']),
            'trajectory_differences': {}}
        for key in step['trajectory']:
            a, b = np.array(step['trajectory'][key]), np.array(vector['trajectory'][key])
            # Individual zero-cost renewable allocations need not be unique.
            comparison['trajectory_differences'][key] = {
                'max_absolute': float(np.max(np.abs(a-b))),
                'max_absolute_over_stepwise_peak': float(np.max(np.abs(a-b))/max(1., np.max(np.abs(a)))),
                'aggregate_max_absolute': float(np.max(np.abs(a.sum(axis=1)-b.sum(axis=1)))),
            }
        comparison['alternative_schedule_probe'] = probe_alternative_optima(horizon, pair)
        comparisons.append(comparison)
        for row in pair:
            del row['trajectory']
            rows.append(row)
    source_paths = sorted((ROOT/'src/cvxopf').glob('*.py')) + [
        Path(__file__), ROOT/'experiments/battery_terminal/scenario.py',
        ROOT/'experiments/battery_terminal/devices.py']
    report = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'single-node DC only; one fresh-process observation per representation and horizon',
        'timing': 'Total is construction through extraction, excluding imports and input preparation. '
                  'Canonicalization is timed explicitly before build.solve; solve wall includes cached '
                  'compilation/interface overhead, solver_reported is solver-native. Peak RSS includes '
                  'imports and input preparation through extraction. Solver uses CLARABEL defaults.',
        'source_sha256': SOURCE_SHA256, 'start': START, 'end': END,
        'scenario_config': asdict(ScenarioConfig()),
        'fleet': 'Prior Case9 generator costs and limits; 150 MW/1000 MWh battery, '
                 '500 MWh initial and hard terminal SoC, aging weight .01; '
                 'renewable ratings 1.1 times full-week site maxima; delta=1 hour.',
        'versions': {package: importlib.metadata.version(package) for package in ('cvxpy', 'numpy', 'pandas', 'clarabel')},
        'python': platform.python_version(), 'platform': platform.platform(),
        'source_hashes': {str(path.relative_to(ROOT)): sha(path) for path in source_paths},
        'runs': rows, 'comparisons': comparisons,
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
