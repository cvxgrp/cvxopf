"""Refresh read-only completion evidence and shade the intervention interval."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import runpy

ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo('America/Los_Angeles')
OUT = Path(os.environ.get('S5_COMPLETION_OUT', ROOT / 'experiments/case118_annual_hierarchy/results/reproductions' / ('s5_completion_band_' + datetime.now(TZ).strftime('%Y%m%dT%H%M%S'))))
OUT.mkdir(parents=True, exist_ok=False)
os.environ['S5_REPO_ROOT'] = str(ROOT)
os.environ['S5_PLOT_OUT'] = str(OUT)
state = runpy.run_path(str(Path(__file__).with_name('collect_completion.py')))
ax, fig = state['ax'], state['fig']
for label in list(ax.texts):
    if label.get_text().startswith('Speculative policy'):
        label.remove()
for line in list(ax.lines):
    x = line.get_xdata()
    if len(x) == 2 and x[0] == x[1]:
        line.remove()
policy = datetime.fromisoformat('2026-09-14T14:32:06.367944-07:00')
fan = datetime(2026, 9, 14, 16, 30, tzinfo=TZ)
ax.axvspan(policy, fan, color='#E69F00', alpha=0.23, linewidth=0, zorder=0)
ax.annotate('Policy change → fan added\nSep 14 · 14:32–~16:30 PDT\n(reboot occurred within this band)',
            xy=(policy + (fan-policy)/2, 78), xytext=(-22, 30),
            textcoords='offset points', ha='right', va='bottom', fontsize=10,
            color='#805500', arrowprops={'arrowstyle':'-', 'color':'#A87500'})
for ext in ['png', 'pdf']:
    fig.savefig(OUT / f'completion_walltime_band.{ext}', dpi=180)
(OUT / 'band_metadata.json').write_text(json.dumps({
    'start': policy.isoformat(), 'start_basis': 'retained first successful speculative worker launch',
    'end': fan.isoformat(), 'end_basis': 'operator-reported original fan addition; approximate',
    'reboot_within_band': '2026-09-14T15:43:21-07:00',
    'note': 'This groups the interventions; it does not isolate their causal effects.'
}, indent=2) + '\n')
print('PLOT_DIRECTORY=' + str(OUT))
