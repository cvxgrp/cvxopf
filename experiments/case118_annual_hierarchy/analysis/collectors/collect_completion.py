"""Snapshot checkpoint-counted completion; never imports or runs study code."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import csv
import hashlib
import json
import os

os.environ.setdefault('MPLCONFIGDIR', '/private/tmp/cvxopf-completion-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import PercentFormatter

ROOT = Path(os.environ['S5_REPO_ROOT'])
RUN = ROOT / 'experiments/case118_annual_hierarchy/results/s4b_annual_ac'
OUT = Path(os.environ['S5_PLOT_OUT'])
if (OUT / 'summary.json').exists():
    raise SystemExit('Snapshot already exists; use a fresh directory.')
snapshot_start = datetime.now(timezone.utc)
snapshots = {}
refs = {}

def read(path):
    raw = path.read_bytes()
    refs[str(path.relative_to(ROOT))] = hashlib.sha256(raw).hexdigest()
    return json.loads(raw)

for cp in sorted(RUN.glob('shard-*/checkpoint.json')):
    snapshots[cp.parent.name] = read(cp)

events = {}
anchors = []
for wave in sorted(RUN.glob('speculative-wave-*')):
    p = wave / 'supervisor-progress.json'
    if not p.exists():
        continue
    d = read(p)
    anchor = d.get('clock_anchor')
    if anchor is None:
        continue
    utc = datetime.fromisoformat(anchor['utc'])
    for e in d['events']:
        if e['kind'] != 'checkpoint_advanced':
            continue
        inv = e['invocation']['window']
        key = (inv['shard_id'].replace('s4b-', ''), inv['iteration'])
        assert key not in events, key
        events[key] = utc + timedelta(seconds=e['monotonic_seconds'] - anchor['monotonic_seconds'])
    if any(e['kind'] == 'launched' for e in d['events']):
        anchors.append(utc)

rows = []
for shard, cp in snapshots.items():
    assert cp['completed_intervals'] == len(cp['windows'])
    assert [e['iteration'] for e in cp['windows']] == list(range(cp['interval']['start'], cp['next_global_iteration']))
    for entry in cp['windows']:
        p = RUN / shard / entry['relative_path']
        st = p.stat()
        assert st.st_size == entry['bytes']
        key = (shard, entry['iteration'])
        when = events.get(key)
        source = 'anchored_checkpoint_event' if when is not None else 'archive_mtime_proxy'
        if when is None:
            assert 'speculative' not in entry['relative_path'], key
            when = datetime.fromtimestamp(st.st_mtime, timezone.utc)
        rows.append(dict(shard=shard, iteration=entry['iteration'], utc=when.isoformat(),
                         time_source=source, archive=str(p.relative_to(ROOT)),
                         checkpoint_archive_sha256=entry['sha256'], archive_mtime_ns=st.st_mtime_ns))
rows.sort(key=lambda r: (r['utc'], r['shard'], r['iteration']))
assert len({(r['shard'], r['iteration']) for r in rows}) == len(rows)
for i, row in enumerate(rows, 1):
    row.update(completed_intervals=i, completion_percent=100*i/8760)
first_wave = read(RUN / 'supervision-wave-000-000.json')
start = datetime.fromisoformat(first_wave['created_utc']) - timedelta(seconds=first_wave['elapsed_critical_path_seconds'])
end = datetime.now(timezone.utc)
assert start <= datetime.fromisoformat(rows[0]['utc']) <= datetime.fromisoformat(rows[-1]['utc']) <= end
tz = ZoneInfo('America/Los_Angeles')
times = [start] + [datetime.fromisoformat(r['utc']) for r in rows] + [end]
values = [0] + [r['completion_percent'] for r in rows] + [rows[-1]['completion_percent']]
plt.rcParams.update({'font.size': 12, 'axes.spines.top': False, 'axes.spines.right': False})
fig, ax = plt.subplots(figsize=(12, 6.3))
ax.step(times, values, where='post', color='#0072B2', lw=2.2)
ax.scatter([end], [values[-1]], color='#0072B2', s=35, zorder=4)
ax.annotate(f"{len(rows):,} / 8,760   ({values[-1]:.2f}%)", (end, values[-1]),
            xytext=(-10, 14), textcoords='offset points', ha='right', weight='bold', color='#005580')
if anchors:
    cutover = min(anchors)
    ax.axvline(cutover, color='#D55E00', ls='--', lw=1.2)
    ax.text(cutover - timedelta(hours=1), 82, 'Speculative policy\nfirst worker launch',
            ha='right', va='top', color='#A84300', fontsize=10)
ax.set_ylim(0, 100)
ax.set_xlim(start - timedelta(hours=1), end + timedelta(hours=2))
ax.yaxis.set_major_formatter(PercentFormatter(100))
ax.set_ylabel('Overall completion · 8,760 hourly intervals')
ax.set_xlabel('Wall-clock time · America/Los_Angeles (PDT)')
ax.xaxis.set_major_locator(mdates.DayLocator(tz=tz))
ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d', tz=tz))
ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=[6,12,18], tz=tz))
ax.grid(axis='y', alpha=.2)
ax.set_title('Annual AC study completion', loc='left', fontsize=18, weight='bold', pad=14)
fig.text(.09, .045, 'Elapsed calendar time includes pauses and reboots. Counts come from retained shard checkpoints.\n'
         'Earlier timestamps: accepted-archive mtime proxy; speculative phase: UTC-anchored advancement events.', fontsize=9, color='#555555')
fig.text(.09, .985, f"Snapshot: {snapshot_start.astimezone(tz):%b %d, %Y · %H:%M PDT}", va='top', fontsize=10, color='#555555')
fig.subplots_adjust(left=.09, right=.97, bottom=.2, top=.89)
for suffix in ['png', 'pdf']:
    fig.savefig(OUT / f'completion_walltime.{suffix}', dpi=180)
with (OUT / 'completion.csv').open('w') as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
(OUT / 'checkpoint_snapshot.json').write_text(json.dumps(snapshots, sort_keys=True))
summary = dict(snapshot_started_utc=snapshot_start.isoformat(), snapshot_finished_utc=end.isoformat(),
               completed=len(rows), total=8760, percent=values[-1],
               timestamp_sources={s:sum(r['time_source']==s for r in rows) for s in {r['time_source'] for r in rows}},
               source_json_sha256=refs,
               caveats=['Checkpoint reads are a short non-simultaneous live snapshot.',
                        'Legacy archive mtime approximates completion; it precedes checkpoint publication slightly and is not a portable historical clock if files are copied without timestamp preservation.',
                        'New event timestamps are supervisor tick times associated with advancement, not nanosecond-precise checkpoint write times.',
                        'Initial zero timestamp is estimated from first wave creation UTC minus retained elapsed duration.',
                        'Failed attempts do not increase completion; accepted operator-inserted intervals do. No rate extrapolation or policy causal-effect claim.'])
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2))
print(json.dumps({k:v for k,v in summary.items() if k!='source_json_sha256'},indent=2))
