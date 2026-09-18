from pathlib import Path
import json
from datetime import datetime, timedelta
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as md

p = Path(__file__).resolve().parent
sources = [
    p.parents[1] / "ac_dispatch_adjustments/joint-episode/context.json",
    p / "may22-context/context.json",
    p / "jun16-context/context.json",
]
plans = json.loads((p / "plans.json").read_text())
fig, axes = plt.subplots(3, 2, figsize=(14, 10), layout="constrained")
for row, (i, path) in enumerate(zip([2944, 3400, 4000], sources)):
    x = json.loads(path.read_text())
    records = x["records"]
    t = [datetime.fromisoformat(r["timestamp"]) for r in records]
    end = [v + timedelta(hours=1) for v in t]
    h = next(z for z in plans if z["iteration"] == i)
    start = datetime.fromisoformat(
        next(r["timestamp"] for r in records if r["iteration"] == i)
    )
    stop = start + timedelta(hours=3)
    for kind, label in [("dc", "DC"), ("ac_first_action", "Executed AC")]:
        axes[row, 0].step(
            t, [sum(r[kind]["b"]) for r in records], where="post", label=label
        )
        axes[row, 1].plot(end, [sum(r[kind]["soc"]) for r in records], label=label)
    tp = [start + timedelta(hours=j) for j in range(4)]
    b = np.array(h["ac_b_mw"]).sum(1)
    soc = [sum(h["initial_soc_mwh"])] + list(np.array(h["ac_soc_mwh"]).sum(1))
    axes[row, 0].step(
        tp,
        list(b) + [b[-1]],
        where="post",
        ls="--",
        color="black",
        label="Selected original plan",
    )
    axes[row, 1].plot(
        tp,
        soc,
        ls="--",
        marker="o",
        ms=3,
        color="black",
        label="Selected original plan",
    )
    axes[row, 0].set_ylabel("Fleet battery MW (+ discharge)")
    axes[row, 1].set_ylabel("Fleet SoC MWh")
    for ax in axes[row]:
        ax.axvspan(start, stop, alpha=0.15, color="gold")
        ax.grid(alpha=0.2)
        ax.xaxis.set_major_formatter(md.DateFormatter("%b %d\n%H:%M", tz=start.tzinfo))
        ax.legend(fontsize=8)
        ax.set_title(f"Window {i}: {start:%b %d, %H:%M} UTC")
fig.suptitle(
    "Owner-selected battery excursions: retained 48-hour context\nDashed lines are complete original plans; solid AC paths are executed actions with reoptimization"
)
fig.savefig(p / "selected-context.png", dpi=130)
