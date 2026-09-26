"""Render historical timing evidence into a fresh directory, without raw-study collection."""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/cvxopf-deck-mpl")
import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, FixedLocator, NullFormatter, PercentFormatter
import numpy as np

HERE = Path(__file__).resolve().parent
TZ = ZoneInfo("America/Los_Angeles")
RESTART = datetime.fromisoformat("2026-09-14T22:50:48.786872+00:00")
STORY_START = datetime(2026, 9, 14, 10, tzinfo=TZ)

def read_json(path):
    return json.loads(path.read_text())

def read_csv(path):
    with path.open() as f:
        return list(csv.DictReader(f))

def save(fig, path):
    fig.savefig(path.with_suffix(".pdf"))
    fig.savefig(path.with_suffix(".png"), dpi=160)
    plt.close(fig)

def render(snap, assets):
    assets.mkdir(parents=True, exist_ok=False)
    events = read_json(snap / "events.json")
    c = read_json(snap / "completion/summary.json")
    p = read_json(snap / "periods/summary.json")
    s = read_json(snap / "solves/summary.json")
    progress = read_json(snap / "data/progress_snapshot.json")
    completion = read_csv(snap / "completion/completion.csv")
    periods = read_csv(snap / "periods/periods.csv")
    solves = read_csv(snap / "solves/solve_times.csv")
    curve = read_csv(snap / "periods/rolling_p95.csv")
    end = datetime.fromisoformat(c["snapshot_finished_utc"])
    stamp = datetime.fromisoformat(progress["snapshot_finished_utc"]).astimezone(TZ)
    plt.rcParams.update({"font.size": 12, "axes.spines.top": False,
                         "axes.spines.right": False})

    def markers(ax):
        for e in events:
            ax.axvline(datetime.fromisoformat(e["utc"]), color=e["color"],
                       ls=e["linestyle"], lw=1.4)

    def time_axis(ax, left, right):
        ax.set_xlim(left, right)
        locator = mdates.AutoDateLocator(tz=TZ, minticks=5, maxticks=8)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d\n%H:%M", tz=TZ))
        ax.set_xlabel("Wall-clock time (PDT)")
        ax.grid(axis="y", alpha=.2)

    def event_legend(fig, y=.98):
        fig.legend(handles=[Line2D([], [], color=e["color"], ls=e["linestyle"],
                       label=e["label"]) for e in events],
                   loc="upper center", bbox_to_anchor=(.52, y), ncol=3,
                   frameon=False, fontsize=11)

    def foot(fig, text):
        fig.text(.09, .025, text, fontsize=9, color="#555555", va="bottom")

    times = [datetime.fromisoformat(r["utc"]) for r in completion]
    vals = [float(r["completion_percent"]) for r in completion]
    run_start = datetime.fromisoformat("2026-09-10T15:21:23.033532+00:00") - timedelta(seconds=57608.458472292055)
    for name, left in [("completion_full", run_start), ("completion_zoom", STORY_START)]:
        fig, ax = plt.subplots(figsize=(12, 5.3))
        ax.step([run_start] + times + [end], [0] + vals + [vals[-1]],
                where="post", color="#0072B2", lw=2)
        ax.annotate(f"{c['completed']:,} / 8,760 ({c['percent']:.2f}%)",
                    (end, vals[-1]), xytext=(-6, 10), textcoords="offset points",
                    ha="right", color="#0072B2", weight="bold")
        policy_time = datetime.fromisoformat(events[0]['utc'])
        fan_time = datetime.fromisoformat(events[2]['utc'])
        ax.axvspan(policy_time, fan_time, color='#E69F00', alpha=.23, linewidth=0)
        ax.text(.02, .96, 'Shaded band: policy change through fan addition\nSep 14, 14:32–~16:30 PDT (includes reboot)',
                transform=ax.transAxes, va='top', fontsize=10, color='#805500')
        time_axis(ax, left, end + timedelta(hours=1))
        ax.set_ylim(0 if name.endswith("full") else 35, 100 if name.endswith("full") else min(100, vals[-1]+5))
        ax.yaxis.set_major_formatter(PercentFormatter(100))
        ax.set_ylabel("Completion of 8,760 hourly intervals")
        foot(fig, f"Snapshot {end.astimezone(TZ):%b %d, %H:%M PDT}. Calendar time includes pauses. Legacy times use archive-mtime proxies.\n"
                  "Speculative advances use UTC/monotonic anchors. *Fan time is approximate and operator-reported.")
        fig.subplots_adjust(left=.09, right=.98, top=.84, bottom=.23)
        save(fig, assets / name)

    fig, ax = plt.subplots(figsize=(12, 5.6))
    # Historical source: match plot_notebook_solve_times in experiments/case118_annual_hierarchy/results/s5_analysis/s5_dashboard.py.
    colors = {"Primary":"#0072B2", "Target-free":"#D55E00",
              "Copied target-free":"#009E73", "Perturbed starts":"#222222"}
    role_markers = {"Primary":"o", "Target-free":"s",
                    "Copied target-free":"D", "Perturbed starts":"*"}
    role_sizes = {"Primary":10, "Target-free":22,
                  "Copied target-free":24, "Perturbed starts":48}
    for role, color in colors.items():
        for status in ["returned", "censored"]:
            rr = [r for r in solves if r["role"] == role and r["status"] == status]
            ax.scatter([datetime.fromisoformat(r["utc"]) for r in rr],
                       [float(r["minutes"]) for r in rr], color=color,
                       marker=role_markers[role] if status=="returned" else "x",
                       s=role_sizes[role] if status=="returned" else 30,
                       alpha=.7, linewidths=.8)
    markers(ax); event_legend(fig)
    ax.legend(handles=[Line2D([], [], color=color, marker=role_markers[role], ls="", label=role)
                       for role, color in colors.items()] +
                      [Line2D([], [], color='#555555', marker='x', ls='', label='Terminated (any role)')],
              frameon=False, ncol=5,
              loc="upper center", bbox_to_anchor=(.5, 1.16), fontsize=9)
    ax.set_yscale("log"); ax.set_ylim(.08, max(150, max(float(r['minutes']) for r in solves)*1.2))
    ax.yaxis.set_major_locator(FixedLocator([.1,.3,1,3,10,30,100]))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _:f"{x:g}"))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.set_ylabel("Solve-call minutes (log scale)")
    time_axis(ax, STORY_START, datetime.fromisoformat(s["snapshot_finished_utc"])+timedelta(minutes=15))
    foot(fig, f"Snapshot {datetime.fromisoformat(s['snapshot_finished_utc']).astimezone(TZ):%b %d, %H:%M PDT}. Role markers: returned calls, including failures. ×: terminated/censored calls; color identifies role.\n"
              "Canonicalization included, construction excluded. Legacy wall times use phase-file mtime proxies. *Fan: approximate.")
    fig.subplots_adjust(left=.09, right=.98, top=.76, bottom=.22)
    save(fig, assets / "solve_times")

    fig, (ax, count) = plt.subplots(2, 1, figsize=(12, 5.7), sharex=True,
                gridspec_kw={"height_ratios":[4,1], "hspace":.06})
    xx = [datetime.fromisoformat(r["utc"]) for r in curve]
    ax.plot(xx, [float(r["p95_minutes"]) for r in curve], color="#0072B2", lw=2)
    count.plot(xx, [int(r["n"]) for r in curve], color="#666666")
    for a in [ax, count]: markers(a); a.grid(axis="y", alpha=.2)
    event_legend(fig)
    visible_p95 = [float(r['p95_minutes']) for r in curve
                   if datetime.fromisoformat(r['utc']) >= STORY_START and np.isfinite(float(r['p95_minutes']))]
    ax.set_yscale("log"); ax.set_ylim(min(1,min(visible_p95)*.8),max(40,max(visible_p95)*1.2))
    ax.yaxis.set_major_locator(FixedLocator([1,2,3,5,10,20,30]))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x,_:f"{x:g}"))
    ax.yaxis.set_minor_formatter(NullFormatter()); ax.set_ylabel("Period P95 (minutes, log scale)")
    count.set_ylabel("Periods\nin window", fontsize=10); count.set_ylim(bottom=0)
    time_axis(count, STORY_START, datetime.fromisoformat(p["snapshot_finished_utc"])+timedelta(minutes=15))
    foot(fig, f"Snapshot {datetime.fromisoformat(p['snapshot_finished_utc']).astimezone(TZ):%b %d, %H:%M PDT}. Trailing two hours of completed periods; minimum 20; pooled shards.\n"
              "Parallel time counts once. Special retries/insertions excluded. Unfinished periods unobserved. *Fan: approximate.")
    fig.subplots_adjust(left=.10, right=.98, top=.85, bottom=.23)
    save(fig, assets / "period_p95")

    # Post-reboot distributions now include ALL subsequent waves, not merely
    # the first invocation used in the original 640-period figure.
    post = [r for r in periods if r["regime"]=="speculative" and datetime.fromisoformat(r["utc"])>=RESTART]
    races = {(r["shard"].replace("s4b-", ""),r["interval"]):r for r in progress["races"]}
    categories = {"Primary only":[], "Primary wins race":[], "Helper wins race":[]}
    for r in post:
        race = races.get((r["shard"], int(r["iteration"])))
        category = "Primary only" if race is None else ("Primary wins race" if race["winner"]==0 else "Helper wins race")
        categories[category].append(float(r["minutes"]))
    values = np.array([float(r["minutes"]) for r in post])
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    groups = [np.array(v) for v in categories.values()]
    palette = ["#0072B2", "#E69F00", "#CC79A7"]
    axes[0].hist(groups, bins=np.arange(0,np.ceil(values.max())+.51,.5), stacked=True,
                 color=palette, label=[f"{k} ({len(v):,})" for k,v in categories.items()])
    lo, hi = np.floor(np.log(values.min())/.25)*.25, np.ceil(np.log(values.max())/.25)*.25
    axes[1].hist([np.log(g) for g in groups], bins=np.arange(lo,hi+.26,.25), stacked=True, color=palette)
    axes[0].set(xlabel="Period minutes", ylabel="Completed periods", title="Time: 30-second bins")
    axes[1].set(xlabel="ln(period time / 1 minute)", title="Log-time: 0.25-wide bins")
    axes[0].legend(frameon=False, fontsize=10)
    axes[1].text(.97,.96, f"Median {np.median(values)*60:.1f} s\nP95 {np.quantile(values,.95):.2f} min\nMax {values.max():.2f} min",
                 transform=axes[1].transAxes, ha="right", va="top")
    for a in axes: a.grid(axis="y", alpha=.15); a.set_axisbelow(True)
    foot(fig, f"{len(values):,} ordinary completed periods after Sep 14, 15:50 PDT worker relaunch, across all later waves.\n"
              "First contender launch to final reap; concurrent time counts once. Interrupted/retried periods and unfinished work excluded.")
    fig.subplots_adjust(left=.08, right=.98, top=.9, bottom=.25, wspace=.24)
    save(fig, assets / "post_reboot_distribution")

    # Five-minute throughput bins retain zero-completion time and wave gaps.
    bins = int((end-RESTART).total_seconds()//300)
    edges = RESTART.timestamp()+np.arange(bins+1)*300
    shards = sorted({r["shard"] for r in completion if datetime.fromisoformat(r["utc"])>=RESTART})
    counts = [np.histogram([datetime.fromisoformat(r["utc"]).timestamp() for r in completion
               if r["shard"]==shard and RESTART<=datetime.fromisoformat(r["utc"])<datetime.fromtimestamp(edges[-1],timezone.utc)], bins=edges)[0]
              for shard in shards]
    total = np.sum(counts,axis=0); rates = total*12
    fig, axes = plt.subplots(1,2,figsize=(12,5), gridspec_kw={"width_ratios":[1,1.65]})
    hist = np.bincount(total)
    axes[0].bar(np.arange(len(hist))*12,hist,width=10.5,color="#0072B2")
    axes[0].axvline(rates.mean(),color="#D55E00",ls="--",label=f"Mean {rates.mean():.1f}/hour")
    axes[0].legend(frameon=False,fontsize=10)
    axes[0].set(xlabel="Accepted intervals/hour",ylabel="Five-minute bins")
    centers = [datetime.fromtimestamp(t,timezone.utc) for t in (edges[:-1]+edges[1:])/2]
    bottom = np.zeros(bins)
    for shard, cc, color in zip(shards,counts,["#0072B2","#E69F00","#009E73","#CC79A7","#56B4E9","#D55E00","#999999","#332288"]):
        axes[1].bar(centers,cc*12,bottom=bottom,width=280/86400,color=color,label=shard)
        bottom += cc*12
    axes[1].set_ylabel("Combined accepted intervals/hour")
    axes[1].legend(frameon=False,fontsize=8,ncol=2)
    time_axis(axes[1], RESTART, datetime.fromtimestamp(edges[-1],timezone.utc))
    foot(fig,f"{bins:,} complete five-minute bins, {total.sum():,} accepted intervals since Sep 14, 15:50 PDT. Zero bins included; last partial bin excluded.\n"
             "Rates are completions per bin times 12. Each physical interval counts once, including when a helper wins.")
    fig.subplots_adjust(left=.08,right=.98,top=.9,bottom=.25,wspace=.28)
    save(fig,assets/"parallel_throughput")

    return progress, p, stamp, {"post_count":len(values), "post_median_seconds":np.median(values)*60,
            "throughput_mean":rates.mean(), "throughput_bins":bins}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=HERE / "artifacts/final_snapshot")
    parser.add_argument("--output", type=Path, required=True, help="New directory for rendered figures")
    args = parser.parse_args()
    render(args.snapshot, args.output)


if __name__ == "__main__":
    main()
