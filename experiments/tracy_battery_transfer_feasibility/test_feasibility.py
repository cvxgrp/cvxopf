"""Non-solving objective ablation, evidence replay and stop-rule checks."""
from dataclasses import fields

import cvxpy as cp
import numpy as np
import pytest

from experiments.numerical_preparation.audit import serializable
from experiments.tracy_battery_transfer_starts.test_starts import setup
from experiments.tracy_battery_transfer_feasibility import model as m, run as r


@pytest.fixture(autouse=True)
def no_optimizer(monkeypatch):
    monkeypatch.setattr(cp.Problem,'solve',lambda *_a,**_k:pytest.fail('test must not optimize'))


def test_zero_objective_retains_coordinates_pins_and_physics():
    physical,kwargs,start=setup()
    objective=physical.prob.objective
    ids=[c.id for c in physical.prob.constraints]
    solver,bindings=m.zero_objective(physical)
    assert physical.prob.objective is objective and physical.prob.status is None
    assert [c.id for c in solver.prob.constraints]==ids
    assert solver.prob.objective.value==0
    assert len(bindings)==2
    assert solver.numerical_preparation.normalize_device_limits
    assert solver.numerical_preparation.exact_fixed_boxes
    assert not solver.numerical_preparation.cost_coordinates
    assert solver.prob.constraints[-1].violation().max()==0
    d,inv,mapping,captured=m.canonical(solver)
    assert d['problem'].objective.value==0
    assert len(mapping.fixed)>0
    assert len(captured['complete_x0'])==len(d['x0'])
    from experiments.case118_tracy_2021 import e3_prepared
    economic=e3_prepared.prepared_start(physical,1)
    assert len(economic['complete_x0'])-len(d['x0'])==physical.variables['b'].size
    # Exercise the installed constant-objective derivative interface without
    # constructing an IPOPT Problem or entering an optimizer.
    from cvxpy.reductions.solvers.nlp_solvers.nlp_solver import Oracles
    oracle=Oracles(d['_bounds'].new_problem,verbose=False,use_hessian=True)
    assert oracle.objective(d['x0'])==0
    np.testing.assert_array_equal(oracle.gradient(d['x0']),np.zeros_like(d['x0']))
    for binding in bindings:
        np.testing.assert_array_equal(binding.leaf.value/binding.scale,binding.term.variable.value)


def test_actual_handoff_reconstruction_and_rejected_point_is_not_accepted(monkeypatch):
    physical,kwargs,start=setup()
    solver,bindings=m.zero_objective(physical)
    d,inv,mp,cap=m.canonical(solver)
    # Synthetic x0 point tests reconstruction only; never pretend it converged.
    from experiments.ac_cost_qualification.audit import canonical_objective
    x=np.asarray(cap['adjusted_x0'])
    canonical_objective(d,inv,x)
    g=np.concatenate([np.asarray(c.args[0].value).ravel(order='F') for c in d['problem'].constraints])
    native=dict(x=x[mp.free],g=g[mp.kept],status=2,obj_val=0.)
    ev=serializable(dict(native=native,coordinates={f.name:getattr(mp,f.name) for f in fields(mp)
        if not f.name.startswith('_')},variable_scale=np.ones(mp.free.size),row_scale=np.ones(mp.kept.size),
        assigned_x0=cap['complete_x0'],adjusted_x0=cap['adjusted_x0'],reduced_x0=cap['reduced_x0'],
        start_layout=[(v.id,list(v.shape),inv[-1].var_offsets[v.id],inv[-1].var_offsets[v.id]+v.size)
                      for v in d['problem'].variables()]))
    # Canonical replay starts from the original named assignments.
    from cvxopf._hierarchical_solver import _assign_start
    _assign_start(physical,start)
    for b in bindings:b.leaf.save_value(b.physical_start*b.scale)
    monkeypatch.setattr(m,'hotspot',lambda *_a:{'test_fixture':'case9'})
    result=m.assess((physical,solver,bindings,kwargs,start['b'].T,None,None,
        dict(original_terminal_soc_mwh=[100.])),ev,cap)
    assert not result['passed']
    assert result['diagnostic_objective']==0
    assert physical.prob.status is None
    assert result['result']['status']=='feasibility_diagnostic_point'
    assert result['reconstructed_native_g_error']<1e-10
    bad=dict(cap,reduced_x0=[1.])
    with pytest.raises(ValueError,match='canonical start'):
        m.assess((physical,solver,bindings,kwargs,None,None,None,{}),ev,bad)


def binding():
    return dict(rows=[dict(job=dict(donor=donor),trial=dict(id=i+1))
        for i,donor in enumerate(('socp','socp','lossy_dc','lossy_dc','singlenode_dc','singlenode_dc'))])


def test_success_first_and_failure_stop():
    b=binding()
    assert r.next_trial(b,[])==1
    assert r.next_trial(b,[dict(trial_id=1,classification='native_rejection',feasible=False)])==2
    assert r.next_trial(b,[dict(trial_id=1,classification='wall_limit',feasible=False)])==2
    assert r.next_trial(b,[dict(trial_id=1,classification='feasible',feasible=True)])==3
    for disposition in ('unfinished','audit_failure','infrastructure_failure','rss_limit',
                        'interrupted','supervisor_failure'):
        assert r.next_trial(b,[dict(trial_id=1,classification=disposition,feasible=False)]) is None


def test_native_zero_is_not_enough():
    assert r.classify(dict(exception=None,native=dict(status=0),checks=dict(passed=False)))=='audit_failure'
    assert r.classify(dict(exception=None,native=dict(status=2),checks=dict(passed=True)))=='native_rejection'
    assert r.classify(dict(exception='failure'))=='infrastructure_failure'


def status_fixture(tmp_path,monkeypatch,*,supervised=True,resources=True,elapsed=1.):
    root=tmp_path/'run';root.mkdir();directory=root/'call-001';directory.mkdir()
    b=dict(binding(),limits=m.LIMITS)
    r.q.atomic_immutable_json(root/'binding.json',b)
    r.q.atomic_immutable_json(root/'protocol.json',dict(protocol=m.LIMITS))
    request=dict(call_id=1,trial_id=1,wall_seconds=600.,protocol=r.q.reference(root/'protocol.json',root))
    checks=dict(passed=True,physics=dict(passed=True),pins=dict(passed=True),hotspots={})
    native=dict(status=0);ev=dict(native=native);capture=dict(iteration=0);row=dict(test='row')
    view=(None,None,None,None,None,{}, {},row)
    record=dict(iteration=1,request=request,row=row,native=native,preparation_evidence=ev,checks=checks,
        exception=None,optimizer_calls=1,classification='feasible',binding_sha256=r.q.digest(root/'binding.json'))
    for name,value in [('request.json',request),('raw-start.json',dict(raw_start={})),
            ('start.json',dict(assigned_start={})),('x0.json.gz',capture),
            ('native.json.gz',dict(iteration=1,data=native)),('preparation.json.gz',dict(iteration=1,data=ev)),('result.json.gz',record)]:
        (r.q.atomic_gzip_json if name.endswith('.gz') else r.q.atomic_immutable_json)(directory/name,value)
    r.q.atomic_immutable_json(directory/'completion.json',dict(classification='feasible',
        artifacts={name:r.q.digest(directory/name) for name in r.ARTIFACTS}))
    if supervised:
        r.q.atomic_immutable_json(directory/'supervision.json',dict(classification='exited',returncode=0,
            wall_seconds=elapsed,peak_sampled_rss_mib=100.,samples=1))
    monkeypatch.setattr(r,'validate_context',lambda *_a:None)
    monkeypatch.setattr(r.transfer,'source_inventory',lambda:None)
    monkeypatch.setattr(r.e3_prepared,'verified_inputs',lambda:None)
    monkeypatch.setattr(r,'verify_view',lambda *_a:(view,capture))
    monkeypatch.setattr(r.m,'assess',lambda *_a:checks)
    monkeypatch.setattr(r.q,'resource_evidence',lambda *_a:resources)
    return root,directory


@pytest.mark.parametrize('supervised,resources,elapsed,accepted,disposition',[
    (True,True,1.,True,'feasible'),(False,True,1.,False,'unfinished'),
    (True,False,1.,False,'infrastructure_failure'),(True,True,601.,False,'wall_limit')])
def test_status_requires_supervision_resources_and_wall_budget(tmp_path,monkeypatch,
        supervised,resources,elapsed,accepted,disposition):
    root,_=status_fixture(tmp_path,monkeypatch,supervised=supervised,resources=resources,elapsed=elapsed)
    progress=r.status(root)
    assert progress['feasible_cases']==int(accepted)
    assert progress['attempts'][0]['classification']==disposition
    if accepted:
        assert progress['next_trial']==3
        assert progress['trials'][1]['disposition']=='not_needed_after_feasibility'


def test_status_refuses_changed_manifest_bytes(tmp_path,monkeypatch):
    root,directory=status_fixture(tmp_path,monkeypatch)
    (directory/'start.json').write_text('{}')
    with pytest.raises(ValueError,match='artifact/hash'):
        r.status(root)


def test_status_refuses_audit_disagreement(tmp_path,monkeypatch):
    root,_=status_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(r.m,'assess',lambda *_a:dict(passed=False))
    with pytest.raises(ValueError,match='independent physical audit'):
        r.status(root)
