"""Objective-only AC diagnostic; production physics and archived runs untouched."""

from dataclasses import fields, replace
from pathlib import Path

import cvxpy as cp
import numpy as np
from cvxpy.reductions.cvx_attr2constr import CvxAttr2Constr
from cvxpy.reductions.dnlp2smooth.dnlp2smooth import Dnlp2Smooth
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
from cvxpy.reductions.solvers.solving_chain import SolvingChain

from cvxopf import extract_results
from cvxopf._cost_coordinates import transform_cost_coordinates
from cvxopf._cvxpy_dispatch import sparse_dispatch_policy
from cvxopf._numerical_preparation import resolve_fixed_map
from cvxopf.network import make_branch_admittance, make_ybus_matpower, reindex_case_to_consecutive
from experiments.ac_cost_qualification.audit import canonical_objective
from experiments.case118_tracy_2021 import e3_prepared
from experiments.numerical_preparation.audit import serializable
from experiments.numerical_preparation.tracy_variables import expand_primal
from experiments.tracy_battery_transfer import model as transfer
from experiments.tracy_battery_transfer_starts import model as starts

HERE = Path(__file__).resolve().parent
LIMITS = dict(max_launches=6, wall_seconds=600., rss_mib=16384.,
              total_worker_seconds=3600., poll_seconds=1.)


def trials(data):
    jobs = [job for job in transfer.jobs(data) if job['id'] in starts.CASE_IDS]
    return [dict(id=i+1, job=job, center=center, scale=None, seed=None)
            for i, (job, center) in enumerate((job, center) for job in jobs
                                              for center in ('matched_ac', 'flat'))]


def zero_objective(physical):
    """Keep cost-valued constraint coordinates; ablate only the objective."""
    economic, bindings = transform_cost_coordinates(physical)
    policy = replace(physical.numerical_preparation, cost_coordinates=False,
                     objective_assembly='hourly')
    solver = replace(economic, prob=cp.Problem(cp.Minimize(0), economic.prob.constraints),
                     _numerical_preparation=policy)
    if any(a is not b for a, b in zip(economic.prob.constraints,
                                    solver.prob.constraints, strict=True)):
        raise ValueError('objective ablation changed constraint identity')
    return solver, bindings


def construct(trial, data, prepared):
    if trial not in trials(data):
        raise ValueError('undeclared feasibility trial')
    physical, kwargs, schedule, matched, row = transfer.construct(trial['job'], data, prepared)
    raw, assigned = starts.alternative_start(physical, kwargs, matched, trial)
    solver, bindings = zero_objective(physical)
    row = dict(row, trial=trial, diagnostic_objective='constant_zero',
               raw_start_sha256=transfer.object_hash(raw),
               assigned_start_sha256=transfer.object_hash(assigned),
               coordinate_maps=serializable([dict(kind=b.term.kind, shape=b.leaf.shape,
                                                 scale=b.scale) for b in bindings]))
    return physical, solver, bindings, kwargs, schedule, raw, assigned, serializable(row)


def canonical(solver):
    native = IPOPT()
    with sparse_dispatch_policy(solver.automatic_sparse_dispatch):
        data, inverse = SolvingChain(reductions=[CvxAttr2Constr(
            reduce_bounds=not native.BOUNDED_VARIABLES), Dnlp2Smooth(), native]).apply(solver.prob)
    mapping = resolve_fixed_map(solver._exact_boxes, inverse[:-1], inverse[-1],
                               data['_bounds'].problem.constraints,
                               len(data['x0']), len(data['cl']))
    adjusted = data['x0'].copy()
    adjusted[mapping.fixed] = mapping.values
    captured = serializable(dict(iteration=0, complete_x0=data['x0'], adjusted_x0=adjusted,
        reduced_x0=adjusted[mapping.free], coordinates={f.name:getattr(mapping, f.name)
        for f in fields(mapping) if not f.name.startswith('_')},
        layout=[[list(v.shape), inverse[-1].var_offsets[v.id],
                 inverse[-1].var_offsets[v.id]+v.size] for v in data['problem'].variables()]))
    return data, inverse, mapping, captured


def hotspot(result, kwargs):
    """Independent voltage/admittance reconstruction, with external identities."""
    internal, _ = reindex_case_to_consecutive(kwargs['case'])
    adm = make_branch_admittance(internal)
    ybus = make_ybus_matpower(internal, branch_admittance=adm)
    bus = np.asarray(kwargs['case']['bus'][:,0], int)
    index = {int(ext):i for i, ext in enumerate(bus)}
    v = result['Vm'] * np.exp(1j*np.deg2rad(result['Va_deg']))
    network = v*np.conj(v@ybus.T)*kwargs['case']['baseMVA']
    injection = np.zeros_like(network)
    for devices, values in ((kwargs['generators'], result['Pg']+1j*result['Qg']),
            (kwargs['storage'], result['b']+1j*result['b_q']),
            (kwargs['nondispatchable'], result['p_nd']+1j*result['q_nd']),
            (kwargs['loads'], -result['p_load_served']-1j*result['q_load_served'])):
        for j, device in enumerate(devices):
            injection[:,index[device.bus]] += values[:,j]
    errors = injection-network
    def top(values):
        t, b = np.unravel_index(np.argmax(abs(values)), values.shape)
        return dict(hour=2439+int(t), bus=int(bus[b]), maximum=float(abs(values[t,b])))
    vf, vt = v[:,adm.from_bus], v[:,adm.to_bus]
    branches = {}
    for side, values in (('from', vf*np.conj(vf*adm.yff+vt*adm.yft)),
                         ('to', vt*np.conj(vf*adm.ytf+vt*adm.ytt))):
        magnitude = abs(values)*kwargs['case']['baseMVA']
        over = np.maximum(magnitude-adm.rate_a_mva, 0)
        t, branch = np.unravel_index(np.argmax(over), over.shape)
        branches[side] = dict(hour=2439+int(t), row_zero_based=int(branch),
            from_bus=int(bus[adm.from_bus[branch]]), to_bus=int(bus[adm.to_bus[branch]]),
            excess_mva=float(over[t,branch]), apparent_mva=float(magnitude[t,branch]),
            rating_mva=float(adm.rate_a_mva[branch]))
    return dict(p_balance_mw=top(errors.real), q_balance_mvar=top(errors.imag),
                branches=branches,
                bus73_hours_2452_2453=[dict(hour=2439+t,
                    p_error_mw=float(errors[t,index[73]].real),
                    voltage_pu=float(result['Vm'][t,index[73]])) for t in (13,14)])


def assess(view, evidence, captured):
    """Read-only native replay; never publish economic optimality."""
    physical, solver, bindings, kwargs, schedule, _, _, row = view
    data, inverse, mapping, fresh = canonical(solver)
    if fresh != captured:
        raise ValueError('fresh canonical start/map/layout mismatch')
    layout = [[s,a,b] for _,s,a,b in evidence['start_layout']]
    if (layout != captured['layout'] or evidence['coordinates'] != captured['coordinates']
            or evidence['assigned_x0'] != captured['complete_x0']
            or evidence['adjusted_x0'] != captured['adjusted_x0']
            or evidence['reduced_x0'] != captured['reduced_x0']
            or evidence['variable_scale'] != np.ones(mapping.free.size).tolist()
            or evidence['row_scale'] != np.ones(mapping.kept.size).tolist()):
        raise ValueError('actual handoff differs from prospectively retained x0')
    native = evidence['native']
    full = expand_primal(native, evidence)
    objective, _ = canonical_objective(data, inverse, full)
    if objective != 0 or native['obj_val'] != 0:
        raise ValueError('diagnostic objective is not constant zero')
    g = np.concatenate([np.asarray(c.args[0].value).ravel(order='F')
                        for c in data['problem'].constraints])
    g_error = float(np.max(abs(g[mapping.kept]-native['g']), initial=0))
    if not np.isfinite(g_error) or g_error > 1e-8:
        raise ValueError('reconstructed native constraints differ')
    for variable in solver.prob.variables():
        offset = inverse[-1].var_offsets[variable.id]
        variable.save_value(full[offset:offset+variable.size].reshape(variable.shape, order='F'))
    for binding in bindings:
        binding.term.variable.save_value(binding.leaf.value/binding.scale)
    # The economic Problem stays unsolved. This scalar is accounting, not a
    # solver objective, and status does not assert economic optimality.
    if physical.prob.status is not None:
        raise ValueError('economic problem must remain unsolved')
    result = extract_results(physical)
    result['status'] = 'feasibility_diagnostic_point'
    result['objective'] = float(physical.prob.objective.value)
    named = {key:float(value.value) for key,value in physical.expressions.items()
             if key.endswith('_cost')}
    common, _ = e3_prepared.audit(physical, result, kwargs, named)
    pins = transfer.pin_audit(result, schedule, kwargs, row['original_terminal_soc_mwh'])
    return serializable(dict(result=result, named_costs=named, physics=common, pins=pins,
        hotspots=hotspot(result, kwargs), reconstructed_native_g_error=g_error,
        diagnostic_objective=objective, economic_cost_not_optimized=result['objective'],
        passed=native['status']==0 and common['passed'] and pins['passed']))
