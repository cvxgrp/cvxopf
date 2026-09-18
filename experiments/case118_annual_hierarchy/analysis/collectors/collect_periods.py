"""Local period-latency P95 from retained checkpoints and process receipts."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict, Counter, deque
import csv, hashlib, json, os
os.environ.setdefault('MPLCONFIGDIR','/private/tmp/cvxopf-completion-mpl')
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FuncFormatter

ROOT=Path(os.environ['S5_REPO_ROOT'])
RUN=ROOT/'experiments/case118_annual_hierarchy/results/s4b_annual_ac'
OUT=Path(os.environ['S5_PLOT_OUT'])
if (OUT/'summary.json').exists(): raise SystemExit('Use a fresh output directory')
started=datetime.now(timezone.utc);refs={};excluded=[];rows=[]
def read(p):
    raw=p.read_bytes();refs[str(p.relative_to(ROOT))]=hashlib.sha256(raw).hexdigest()
    return json.loads(raw)
cps={p.parent.name:read(p) for p in sorted(RUN.glob('shard-*/checkpoint.json'))}
interventions=[read(p).get('intervention_window') for pattern in
               ('operator-intervention-*.json', 'operator-recovery-*.json')
               for p in RUN.glob(pattern)]

# Completed speculative race latency: first actual contender launch to last reap.
# Retain only races with a checkpoint_advanced event and matching checkpoint entry.
groups=defaultdict(list); advances={}; launches=Counter()
for wave in sorted(RUN.glob('speculative-wave-*')):
    prog=wave/'supervisor-progress.json'
    if not prog.exists():continue
    d=read(prog);anchor=d['clock_anchor']
    for e in d['events']:
        if e['kind'] in {'launched', 'replacement_launched'}:
            w=e['invocation']['window']
            launches[(wave.name,w['shard_id'].replace('s4b-',''),w['iteration'])]+=1
        if e['kind']!='checkpoint_advanced':continue
        w=e['invocation']['window'];key=(w['shard_id'].replace('s4b-',''),w['iteration'])
        assert key not in advances,key
        advances[key]=(wave.name,datetime.fromisoformat(anchor['utc'])+timedelta(seconds=e['monotonic_seconds']-anchor['monotonic_seconds']))
    for p in sorted(wave.glob('s4b-shard-*/ac-*/lifecycle.json')):
        life=read(p);w=life['invocation']['window'];key=(w['shard_id'].replace('s4b-',''),w['iteration'])
        groups[key].append((wave.name,life))

for shard,cp in cps.items():
    directory=RUN/shard
    assert len(cp['windows'])==cp['completed_intervals']
    supers=defaultdict(list)
    for p in directory.glob('window-supervision-*.json'):
        d=read(p);supers[d['iteration']].append((p,d))
    for entry in cp['windows']:
        it=entry['iteration'];key=(shard,it)
        if entry in interventions:
            excluded.append([shard,it,'operator insertion']);continue
        if 'speculative' in entry['relative_path']:
            assert key in advances,key
            wave,utc=advances[key]; gg=groups[key]
            if len({w for w,l in gg})>1:
                excluded.append([shard,it,'cross-invocation retry']);continue
            lifes=[l for w,l in gg if w==wave]
            if not lifes or not all(l['reaped'] for l in lifes):
                excluded.append([shard,it,'cleanup not yet complete in live snapshot']);continue
            # A just-published checkpoint may precede loser cleanup. Defer it
            # until every launched contender has a finalized lifecycle.
            if len(lifes) != launches[(wave,shard,it)]:
                excluded.append([shard,it,'cleanup not yet complete in live snapshot']);continue
            seconds=max(l['reaped_monotonic'] for l in lifes)-min(l['launched_monotonic'] for l in lifes)
            regime='speculative';basis='UTC-anchored advancement event'
        else:
            ss=supers[it]
            if len(ss)!=1 or ss[0][1].get('lifecycle_attempt',0):
                excluded.append([shard,it,'operator insertion or interrupted/retried legacy period']);continue
            p,sup=ss[0]
            if sup['classification'] not in {'completed','timeout'}:
                excluded.append([shard,it,'unsuccessful legacy invocation']);continue
            ready=directory/f'window-ready-{it:06d}.json'
            assert read(ready)==entry,(shard,it,'ready entry mismatch')
            recovery_seconds=0
            if sup['classification']=='timeout':
                rec=read(directory/f'window-recovery-{it:06d}.json')
                assert rec['returncode']==0 and rec['timeout_supervision_sha256']==refs[str(p.relative_to(ROOT))]
                recovery_seconds=rec['wall_seconds']
            else:assert sup['returncode']==0
            seconds=sup['orchestration_wall_seconds']+recovery_seconds
            utc=datetime.fromtimestamp((directory/entry['relative_path']).stat().st_mtime,timezone.utc)
            regime='sequential';basis='accepted archive mtime proxy'
        assert np.isfinite(seconds) and seconds>0
        rows.append(dict(shard=shard,iteration=it,utc=utc.isoformat(),minutes=seconds/60,regime=regime,time_basis=basis))
rows.sort(key=lambda r:r['utc'])
assert len({(r['shard'],r['iteration']) for r in rows})==len(rows)
snapshot=datetime.now(timezone.utc)
times=np.array([datetime.fromisoformat(r['utc']).timestamp() for r in rows])
values=np.array([r['minutes'] for r in rows])
# Fixed five-minute output grid, trailing two hours, minimum 20 completed periods.
grid=np.arange(np.floor(times[0]/300)*300,np.floor(snapshot.timestamp()/300)*300+1,300)
curve=[]
for t in grid:
    lo=np.searchsorted(times,t-7200,side='right');hi=np.searchsorted(times,t,side='right')
    n=int(hi-lo)
    p95=float(np.quantile(values[lo:hi],.95,method='linear')) if n>=20 else float('nan')
    curve.append(dict(utc=datetime.fromtimestamp(t,timezone.utc).isoformat(),n=n,p95_minutes=p95))

tz=ZoneInfo('America/Los_Angeles')
policy=datetime.fromisoformat('2026-09-14T21:32:06.367944+00:00')
reboot=datetime.fromtimestamp(1789425801.047126,timezone.utc)
fan=datetime(2026,9,14,16,30,tzinfo=tz)
plt.rcParams.update({'font.size':11,'axes.spines.top':False,'axes.spines.right':False})
fig,(ax,counts)=plt.subplots(2,1,figsize=(13,7.5),sharex=True,gridspec_kw={'height_ratios':[4,1],'hspace':.08})
x=[datetime.fromisoformat(r['utc']) for r in curve];y=[r['p95_minutes'] for r in curve]
ax.plot(x,y,color='#0072B2',lw=2.1)
counts.plot(x,[r['n'] for r in curve],color='#666666',lw=1.1)
for a in (ax,counts):
    a.axvline(policy,color='#D55E00',ls='--',lw=1.3)
    a.axvline(reboot,color='#009E73',ls=':',lw=1.5)
    a.axvline(fan,color='#CC79A7',ls='-.',lw=1.5)
    a.grid(axis='y',alpha=.2)
ax.set_yscale('log')
ax.yaxis.set_major_locator(FixedLocator([1,2,3,5,10,20,30,60,100]))
ax.yaxis.set_major_formatter(FuncFormatter(lambda x,pos:f'{x:g}'))
ax.set_ylabel('Local 95th percentile · period minutes (log scale)')
counts.set_ylabel('Periods\nin window',fontsize=10)
counts.set_ylim(bottom=0)
counts.set_xlim(snapshot-timedelta(hours=24),snapshot+timedelta(minutes=5))
visible=[r['p95_minutes'] for r in curve if datetime.fromisoformat(r['utc'])>=snapshot-timedelta(hours=24) and np.isfinite(r['p95_minutes'])]
ax.set_ylim(min(visible)*.8,max(visible)*1.3)
counts.xaxis.set_major_locator(mdates.HourLocator(interval=3,tz=tz))
counts.xaxis.set_major_formatter(mdates.DateFormatter('%b %d\n%H:%M',tz=tz))
counts.set_xlabel('Wall-clock time · PDT')
fig.suptitle('Period calculation time · local 95th percentile',x=.085,ha='left',y=.985,fontsize=18,weight='bold')
fig.text(.085,.932,'Trailing 2 hours of completed periods · minimum 20 observations · pooled across shards',fontsize=11)
handles=[Line2D([],[],color='#D55E00',ls='--',label='Policy launch · Sep 14, 14:32'),
         Line2D([],[],color='#009E73',ls=':',label='Reboot · Sep 14, 15:43'),
         Line2D([],[],color='#CC79A7',ls='-.',label='Fan · Sep 14, ~16:30 (operator-reported)')]
fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.53,.91),ncol=3,frameon=False,fontsize=10)
fig.text(.085,.04,'Period latency includes primary + recovery path; parallel contenders count once in elapsed time, not summed compute.\n'
         'Restarted/interrupted periods and operator insertions excluded; in-flight periods are not yet observed.\n'
         'Legacy: orchestration + recovery elapsed; speculative: first launch to last reap. Legacy wall time uses archive-mtime proxies.',fontsize=9,color='#555555')
fig.text(.98,.985,f'Snapshot {snapshot.astimezone(tz):%b %d · %H:%M PDT}',ha='right',va='top',fontsize=10,color='#555555')
fig.subplots_adjust(left=.085,right=.98,top=.80,bottom=.22)
for ext in ['png','pdf']:fig.savefig(OUT/f'period_p95_last24h.{ext}',dpi=180)
for name,data in [('periods.csv',rows),('rolling_p95.csv',curve)]:
    with (OUT/name).open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
summary=dict(snapshot_started_utc=started.isoformat(),snapshot_finished_utc=snapshot.isoformat(),
             window_hours=2,minimum_observations=20,grid_minutes=5,quantile_method='numpy linear',
             included=len(rows),excluded=excluded,latest_estimate=curve[-1],input_sha256=refs,
             notes=['Trailing completed-period cohort, not active-period or calendar-weighted latency.',
                    'No estimate of still-running periods; long unresolved periods enter only after completion.',
                    'The smoother retains pre-intervention observations for two hours; its change is not instantaneous event timing.',
                    'Legacy orchestration and speculative launch-to-reap boundaries differ slightly; parent-only finalization excluded from speculative.',
                    'No causality claim; input difficulty and policy/host changes are not isolated.'])
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps({k:v for k,v in summary.items() if k!='input_sha256'},indent=2))
