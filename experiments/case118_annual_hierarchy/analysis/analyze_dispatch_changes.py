"""Preliminary executed AC versus frozen DC schedule differences; no solves."""
import csv
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def finite(value, shape):
    a = np.asarray(value, dtype=float)
    assert a.shape == shape and np.isfinite(a).all(), (a.shape, shape)
    return a


def metrics(pg_ac, pg_dc, b_ac, b_dc, initial_ac, initial_dc, post_ac, post_dc, delta):
    dg, db = pg_ac-pg_dc, b_ac-b_dc
    # Signed state difference evolves from the inherited state, not from zero.
    np.testing.assert_allclose(post_ac-post_dc, initial_ac-initial_dc-delta*db, atol=2e-4, rtol=0)
    g = float(np.abs(dg).sum())
    return dict(generator_l1_mw=g, battery_l1_mw=float(np.abs(db).sum()),
                soc_end_l1_mwh=float(np.abs(post_ac-post_dc).sum()),
                soc_initial_l1_mwh=float(np.abs(initial_ac-initial_dc).sum()),
                generator_net_change_mw=float(dg.sum()),
                generator_counterdirection_mw=float((g-abs(dg.sum()))/2),
                battery_net_change_mw=float(db.sum()))


def main():
    from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
    from experiments.case118_annual_hierarchy.s4b_manifest import (
        load_authoritative_outer, load_verified_manifest,
        S4_OUTER_ARCHIVE_SHA256, EXPECTED_MANIFEST_SHA256,
    )
    # Small algebra/units checks independent of the archived data.
    z = np.zeros(2)
    m = metrics(np.array([3., -3.]), z, np.array([2., -1.]), z, z, z,
                np.array([-1., .5]), z, .5)
    assert m['generator_l1_mw']==6 and m['generator_counterdirection_mw']==3
    assert m['battery_l1_mw']==3 and m['soc_end_l1_mwh']==1.5
    m = metrics(z,z,z,z,np.ones(2),z,np.ones(2),z,1.)
    assert m['battery_l1_mw']==0 and m['soc_end_l1_mwh']==2
    fixture=load_s4_fixture()
    outer, soc_dc, b_dc=load_authoritative_outer()
    manifest=load_verified_manifest()['manifest']
    ids=list(fixture.storage_device_ids); ns=len(ids); ng=len(fixture.inputs.case['gen'])
    n=fixture.inputs.horizon_steps; delta=fixture.inputs.delta
    pg_dc=finite(outer['result']['Pg'],(n,ng))
    load=finite(outer['result']['p_load'],fixture.inputs.df_load_p.shape)
    np.testing.assert_array_equal(load,fixture.inputs.df_load_p.to_numpy())
    capacity=sum(u.capacity for u in fixture.inputs.storage)
    rating=sum(u.apparent_power_rating for u in fixture.inputs.storage)
    run=ROOT/'experiments/case118_annual_hierarchy/results/s4b_annual_ac'
    snapshots=[(p,p.read_bytes()) for p in sorted(run.glob('shard-*/checkpoint.json'))]
    snap=datetime.now(timezone.utc)
    dest=Path(os.environ.get('S5_DISPATCH_OUT', ROOT/'outputs/s5_analysis'/f'dispatch_changes_{snap:%Y%m%dT%H%M%SZ}'))
    rows=[]; checkpoint_refs=[]; seen=set(); interventions=[]
    boundaries=manifest['boundary_indices']
    for path, raw in snapshots:
        cp=json.loads(raw);ordinal=cp['ordinal'];start,stop=boundaries[ordinal:ordinal+2]
        assert cp['outer_plan_sha256']==S4_OUTER_ARCHIVE_SHA256
        assert cp['manifest_sha256']==EXPECTED_MANIFEST_SHA256
        assert cp['storage_device_ids']==ids
        assert cp['interval']==dict(start=start,stop=stop,half_open=True)
        assert [e['iteration'] for e in cp['windows']]==list(range(start,start+cp['completed_intervals']))
        checkpoint_refs.append(dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),completed=cp['completed_intervals']))
        previous=None
        for entry in cp['windows']:
            raw_w=(path.parent/entry['relative_path']).read_bytes()
            assert len(raw_w)==entry['bytes'] and hashlib.sha256(raw_w).hexdigest()==entry['sha256']
            w=json.loads(gzip.decompress(raw_w));i=entry['iteration'];T=min(fixture.policy.ac_window_steps,stop-i)
            assert i not in seen and w['iteration']==i and w['interval_start']==i and w['interval_stop']==i+T
            seen.add(i)
            assert w['delta_hours']==delta and w['storage_device_ids']==ids
            controls=[a for a in w['attempts'] if a['supplied_executed_action']]
            assert len(controls)==1
            c=controls[0];res=c['result']
            assert c['audit']['accepted_primal'] is True and c['slot_state']=='executed'
            assert c['attempt_id']==w['executed_interval']['controlling_attempt_id'] and c['role']!='target_free'
            assert res['storage_device_ids']==ids and w['result_dimensions']['generators']==ng
            pg=finite(res['Pg'],(T,ng))[0];b=finite(res['b'],(T,ns))[0]
            ini=finite(w['initial_soc_mwh'],(ns,));post=finite(w['post_step_soc_mwh'],(ns,))
            np.testing.assert_allclose(b,w['executed_interval']['b_mw'],atol=1e-8,rtol=0)
            np.testing.assert_allclose(post,finite(res['soc'],(T,ns))[0],atol=1e-6,rtol=0)
            np.testing.assert_allclose(post,ini-delta*b,atol=1e-4,rtol=0)
            np.testing.assert_allclose(w['target_soc_mwh'],soc_dc[i+T],atol=1e-6,rtol=0)
            np.testing.assert_allclose(ini,soc_dc[start] if previous is None else previous,atol=1e-4,rtol=0)
            np.testing.assert_allclose(finite(res['p_load'],(T,load.shape[1]))[0],load[i],atol=1e-8,rtol=0)
            previous=post
            row=dict(iteration=i,timestamp=str(fixture.inputs.df_load_p.index[i]),shard=path.parent.name,
                     **metrics(pg,pg_dc[i],b,b_dc[i],ini,soc_dc[i],post,soc_dc[i+1],delta))
            row.update(generator_l1_pct_load=row['generator_l1_mw']/float(load[i].sum())*100,
                       battery_l1_pct_fleet_rating=row['battery_l1_mw']/rating*100,
                       soc_end_l1_pct_fleet_capacity=row['soc_end_l1_mwh']/capacity*100,
                       controller_id=c['attempt_id'],archive_sha256=entry['sha256'],
                       policy=w.get('policy','legacy'),operator_intervention=w.get('operator_intervention') is not None)
            if row['operator_intervention']:interventions.append(i)
            for j,device in enumerate(ids):
                row[f'{device}.delta_b_mw']=float(b[j]-b_dc[i,j])
                row[f'{device}.delta_soc_end_mwh']=float(post[j]-soc_dc[i+1,j])
            rows.append(row)
            if len(rows)%1000==0:print(f'Verified/extracted {len(rows)} intervals',flush=True)
        print(f'{path.parent.name}: {len(cp["windows"])} accepted actions',flush=True)
    rows.sort(key=lambda r:r['iteration'])
    keys=['generator_l1_mw','battery_l1_mw','soc_end_l1_mwh',
          'generator_l1_pct_load','battery_l1_pct_fleet_rating','soc_end_l1_pct_fleet_capacity',
          'generator_counterdirection_mw','soc_initial_l1_mwh']
    summary={}
    for key in keys:
        values=np.array([r[key] for r in rows])
        summary[key]=dict(mean=float(values.mean()),median=float(np.median(values)),p90=float(np.quantile(values,.9)),
                          p95=float(np.quantile(values,.95)),maximum=float(values.max()))
    summary['pearson_generator_vs_battery']=float(np.corrcoef([r['generator_l1_mw'] for r in rows],[r['battery_l1_mw'] for r in rows])[0,1])
    info=dict(snapshot_utc=snap.isoformat(),completed=len(rows),horizon=n,summary=summary,
              outer_sha256=S4_OUTER_ARCHIVE_SHA256,manifest_sha256=EXPECTED_MANIFEST_SHA256,
              fixture_hashes=fixture.hashes,checkpoints=checkpoint_refs,storage_ids=ids,
              generator_order='Frozen Case118 generator-table row order; extraction scales Pg to MW for both formulations.',
              generator_buses=np.asarray(fixture.inputs.case['gen'])[:,0].astype(int).tolist(),
              fleet_rating_mw=rating,fleet_capacity_mwh=capacity,delta_hours=delta,
              operator_intervention_intervals=interventions,
              analysis_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              analysis_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              limitations=['Descriptive differences selected by this controller; not minimum feasibility repair or causal AC-only effect.',
                           'Annual DC versus receding-horizon AC also changes horizon, realized initial state, and numerical solution path.',
                           'Generator L1 includes net loss balancing and opposing redispatch; opposing changes count both legs.',
                           'End SoC L1 includes inherited trajectory divergence; battery power L1 measures current-action difference.',
                           'Operator interventions and recovery-policy changes included and labeled; no full independent AC reaudit.',
                           'Only executed first actions are compared, not unexecuted predictions; synthetic timestamps label interval starts.'])
    dest.mkdir(parents=True,exist_ok=False)
    (dest/'report.json').write_text(json.dumps(info,indent=2,allow_nan=False))
    with (dest/'intervals.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print(json.dumps(dict(output=str(dest),completed=len(rows),summary=summary),indent=2))


if __name__=='__main__':main()
