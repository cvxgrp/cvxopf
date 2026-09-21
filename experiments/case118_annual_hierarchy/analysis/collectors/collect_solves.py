"""Read-only phase/lifecycle timing snapshot; no solver or model imports."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from collections import Counter, defaultdict
import csv
import hashlib
import json
import os
os.environ.setdefault('MPLCONFIGDIR','/private/tmp/cvxopf-completion-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FuncFormatter

ROOT=Path(os.environ['S5_REPO_ROOT'])
RUN=ROOT/'experiments/case118_annual_hierarchy/results/s4b_annual_ac'
OUT=Path(os.environ['S5_PLOT_OUT'])
if (OUT/'summary.json').exists(): raise SystemExit('Use a fresh snapshot directory')
stamp=datetime.now(timezone.utc)
refs={}; rows=[]; excluded=Counter()
def read(p):
    raw=p.read_bytes(); stat=p.stat()
    refs[str(p.relative_to(ROOT))]={'sha256':hashlib.sha256(raw).hexdigest(),'mtime_ns':stat.st_mtime_ns}
    return json.loads(raw),stat.st_mtime
def role(slot):
    return {0:'Primary',1:'Target-free',2:'Copied target-free'}.get(slot,'Perturbed starts')
def add(p,iteration,slot,start,end,utc,status,regime,basis):
    assert end>start,(p,iteration,slot)
    rows.append(dict(source=str(p.relative_to(ROOT)),iteration=iteration,slot=slot,
                     role=role(slot),minutes=(end-start)/60,utc=utc.isoformat(),
                     status=status,regime=regime,time_basis=basis))

# Bind each legacy phase file to its retained supervision/recovery classification.
legacy={}
for p in sorted(RUN.glob('shard-*/window-supervision-*.json'))+sorted(RUN.glob('shard-*/window-recovery-*.json')):
    d,_=read(p)
    if d.get('phase_record'): legacy[p.parent/d['phase_record']]=d
for p in sorted(RUN.glob('shard-*/window-phase-*.json')):
    d,mtime=read(p); events=d.get('events',[])
    if not events: continue
    final=max(e['monotonic_seconds'] for e in events)
    per=defaultdict(dict)
    for e in events: per[(e['iteration'],e['attempt_ordinal'])][e['phase']]=e['monotonic_seconds']
    for (iteration,slot),ph in per.items():
        start=ph.get('before_ac_solve')
        if start is None: continue
        end=ph.get('after_ac_solve')
        status='returned'
        if end is None:
            sup=legacy.get(p,{})
            if slot==0 and sup.get('classification')=='timeout':
                end=start+float(sup['primary_budget_seconds']);status='censored'
            else:
                excluded['legacy_unreturned_without_deadline']+=1;continue
        utc=datetime.fromtimestamp(mtime,timezone.utc)+timedelta(seconds=end-final)
        add(p,iteration,slot,start,end,utc,status,'sequential','phase_file_mtime_anchor_proxy')

# Every finalized speculative attempt, including losers, counts as solver effort.
# Incomplete attempts lack lifecycle.json and are deliberately not plotted.
for p in sorted(RUN.glob('speculative-wave-*/s4b-shard-*/ac-*/lifecycle.json')):
    d,_=read(p); ph={e['phase']:e['monotonic_seconds'] for e in d['phases']}
    start=ph.get('before_ac_solve')
    if start is None: excluded['speculative_no_solve']+=1;continue
    end=ph.get('after_ac_solve'); status='returned'
    if end is None:
        if d.get('reason') not in {'solve_budget','lost_race'}:
            excluded['speculative_interruption_or_other_stop']+=1;continue
        end=d['reaped_monotonic'];status='censored'
    a=d['clock_anchor'];utc=datetime.fromisoformat(a['utc'])+timedelta(seconds=end-a['monotonic_seconds'])
    inv=d['invocation'];slot=inv['source_slot']
    add(p,inv['window']['iteration'],slot,start,end,utc,status,'speculative','retained_utc_monotonic_anchor')
rows.sort(key=lambda r:r['utc'])
end_snapshot=datetime.now(timezone.utc)
assert all(datetime.fromisoformat(r['utc'])<=end_snapshot for r in rows)
tz=ZoneInfo('America/Los_Angeles')
policy=datetime.fromisoformat('2026-09-14T21:32:06.367944+00:00')
reboot=datetime.fromtimestamp(1789425801.047126,timezone.utc)
colors={'Primary':'#0072B2','Target-free':'#D55E00','Copied target-free':'#009E73','Perturbed starts':'#CC79A7'}
plt.rcParams.update({'font.size':11,'axes.spines.top':False,'axes.spines.right':False})
fig,ax=plt.subplots(figsize=(13,7))
axes=[ax]
for ax in axes:
    for name,color in colors.items():
        for status in ['returned','censored']:
            rr=[r for r in rows if r['role']==name and r['status']==status]
            ax.scatter([datetime.fromisoformat(r['utc']) for r in rr],[r['minutes'] for r in rr],
                       color=color,s=13 if status=='returned' else 28,
                       marker='.' if status=='returned' else '^',
                       alpha=.5 if status=='returned' else .8,edgecolors='none')
    ax.set_yscale('log')
    ax.yaxis.set_major_locator(FixedLocator([.1,.3,1,3,10,30,100,300]))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x,pos:f'{x:g}'))
    ax.set_ylim(.08,max(100,max(r['minutes'] for r in rows)*1.3))
    ax.axvline(policy,color='#D55E00',ls='--',lw=1.3)
    ax.axvline(reboot,color='#333333',ls=':',lw=1.5)
    ax.axvline(datetime(2026,9,14,16,30,tzinfo=tz),color='#AA4499',ls='-.',lw=1.5)
    ax.axhline(5,color='#777777',lw=.8,ls=':',alpha=.5)
    ax.set_ylabel('Solve-path minutes · logarithmic scale')
    ax.grid(axis='y',which='major',alpha=.18)
ax.set_xlim(end_snapshot-timedelta(hours=24),end_snapshot+timedelta(minutes=10))
ax.xaxis.set_major_locator(mdates.HourLocator(interval=3,tz=tz))
ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d\n%H:%M',tz=tz))
ax.set_xlabel('Wall-clock time at solve return / stop · PDT')
handles=[Line2D([],[],color=c,marker='o',ls='',markersize=5,label=n) for n,c in colors.items()]
handles += [Line2D([],[],color='#666666',marker='^',ls='',markersize=6,label='Stopped before return (censored)'),
            Line2D([],[],color='#D55E00',ls='--',label='Policy launch · Sep 14, 14:32'),
            Line2D([],[],color='#333333',ls=':',label='Computer reboot · Sep 14, 15:43'),
            Line2D([],[],color='#AA4499',ls='-.',label='External fan · Sep 14, ~16:30*')]
fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.52,.925),ncol=4,frameon=False,fontsize=10)
fig.suptitle('AC solve times · last 24 hours',x=.085,ha='left',fontsize=19,weight='bold',y=.985)
fig.text(.085,.035,'Dots: returned solves, including failed returns. Triangles: timeouts / lost races, not completed solve durations.\n'
         'Durations include canonicalization, exclude model construction. Legacy wall times use phase-file mtime proxies.\n'
         'Ongoing attempts, operator diagnostics, and unreturned non-timeout interruptions excluded; completed retries retained.\n'
         '*Fan time is operator-reported and approximate.',fontsize=9,color='#555555')
fig.text(.98,.985,f'Snapshot {stamp.astimezone(tz):%b %d · %H:%M PDT}',ha='right',va='top',fontsize=10,color='#555555')
fig.subplots_adjust(left=.085,right=.98,top=.80,bottom=.245)
for ext in ['png','pdf']:fig.savefig(OUT/f'solve_times_last24h.{ext}',dpi=180)
with (OUT/'solve_times.csv').open('w') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
summary={'snapshot_started_utc':stamp.isoformat(),'snapshot_finished_utc':end_snapshot.isoformat(),
         'counts':dict(Counter(f"{r['role']} / {r['status']}" for r in rows)),
         'excluded':dict(excluded),'file_references':refs,
         'caveats':['Live file reads are not a simultaneous snapshot.',
                    'Legacy phase mtime approximates the final event wall time; earlier events use monotonic offsets from it.',
                    'Speculative cancellation duration extends to reaping and can include small termination overhead.',
                    'Returned is not equivalent to accepted. No causal attribution or interval-difficulty matching.',
                    'Five-minute censoring differs before/after policy change; triangles are not ordinary completed observations.']}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps({k:v for k,v in summary.items() if k!='file_references'},indent=2))
