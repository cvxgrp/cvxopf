from pathlib import Path
import json
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta

p = Path(__file__).resolve().parent
x = json.loads((p / "joint-episode/context.json").read_text())
rows = x["records"]
t = [datetime.fromisoformat(r["timestamp"]) for r in rows]
end = [v + timedelta(hours=1) for v in t]


def a(kind, name):
    return np.array([r[kind][name] for r in rows])


pg, gd = a("ac_first_action", "Pg"), a("dc", "Pg")
b, bd = a("ac_first_action", "b"), a("dc", "b")
soc, sd = a("ac_first_action", "soc"), a("dc", "soc")
v = a("ac_first_action", "Vm")
dg = pg - gd
net = dg.sum(1)
l1 = abs(dg).sum(1)
opp = (l1 - abs(net)) / 2
branch = np.array(x["branches"])
rated = (branch[:, 10] == 1) & (branch[:, 5] > 0)
u = (
    np.maximum(
        a("ac_first_action", "branch_s_from"), a("ac_first_action", "branch_s_to")
    )[:, rated]
    / branch[rated, 5]
)
ud = abs(a("dc", "p_flows"))[:, rated] / branch[rated, 5]
lo = a("ac_first_action", "p_load").sum(1)
fig, ax = plt.subplots(6, 1, figsize=(12, 14), sharex=True, layout="constrained")
ax[0].plot(t, lo, label="Gross load")
ax[0].plot(t, gd.sum(1), label="DC generation")
ax[0].plot(t, pg.sum(1), label="Executed AC generation")
ax[0].set_ylabel("MW")
for y, label in [
    (net, "Net generator change"),
    (l1, "Total absolute change"),
    (opp, "Opposing change"),
]:
    ax[1].plot(t, y, label=label)
ax[1].set_ylabel("MW")
ax[2].step(t, bd.sum(1), where="post", label="DC fleet battery power")
ax[2].step(t, b.sum(1), where="post", label="Executed AC fleet battery power")
ax[2].axhline(0, color="gray", lw=0.6)
ax[2].set_ylabel("MW (+ discharge)")
ax[3].plot(end, sd.sum(1), label="DC end-hour SoC")
ax[3].plot(end, soc.sum(1), label="Executed AC end-hour SoC")
ax[3].set_ylabel("MWh")
ax[4].plot(t, ud.max(1), label="DC maximum MW/rating")
ax[4].plot(t, u.max(1), label="AC maximum terminal MVA/rating")
ax[4].axhline(1, color="gray", lw=0.6)
ax[4].set_ylabel("Loading ratio")
ax[4].set_ylim(0.98, 1.01)
ax[4].ticklabel_format(axis="y", style="plain", useOffset=False)
ax[5].plot(t, v.min(1), label="Minimum voltage")
ax[5].plot(t, v.max(1), label="Maximum voltage")
ax[5].set_ylabel("pu")
first = t[0] + timedelta(hours=2940 - x["start"])
last = first + timedelta(hours=3)
for axis in ax:
    axis.axvspan(
        first, last, color="#d69435", alpha=0.18, label="Selected comparison window"
    )
    axis.legend(loc="upper left", fontsize=8, ncol=2)
    axis.grid(alpha=0.2)
ax[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %d %H:%M", tz=t[0].tzinfo))
ax[-1].set_xlabel(
    "Synthetic study calendar, UTC; executed AC first actions are historical context"
)
fig.suptitle(
    "Joint generator/battery episode • 48-hour context\nAC dispatch adjustments: May 3, 12:00–15:00 UTC (indices 2940–2942)"
)
fig.savefig(p / "context.png", dpi=130)
sel = np.array([2940 <= r["iteration"] < 2943 for r in rows])
gen = np.array([[*g["cost_coeffs"]] for g in x["generators"]])
cap = np.array([s["capacity"] for s in x["storage"]])
summary = {
    "context_interval": [x["start"], x["stop"]],
    "comparison_interval": [2940, 2943],
    "comparison_timestamps": [first.isoformat(), last.isoformat()],
    "context_shards": sorted(set(r["shard"] for r in rows)),
    "context_interventions": [
        r["iteration"] for r in rows if r["operator_intervention"]
    ],
    "comparison_historical_generator_net_mwh": float(net[sel].sum()),
    "comparison_historical_generator_l1_mwh": float(l1[sel].sum()),
    "comparison_historical_opposing_mwh": float(opp[sel].sum()),
    "comparison_historical_battery_change_l1_mwh": float(abs(b[sel] - bd[sel]).sum()),
    "comparison_dc_generation_cost": float(
        (gd[sel] * gen[:, 1] + gd[sel] ** 2 * gen[:, 2] + gen[:, 0]).sum()
    ),
    "comparison_dc_battery_throughput_mwh": float(abs(bd[sel]).sum()),
    "comparison_dc_initial_soc_mwh": rows[2940 - x["start"]]["dc_initial_soc_mwh"],
    "comparison_dc_terminal_soc_mwh": sd[2942 - x["start"]].tolist(),
    "comparison_historical_ac_initial_soc_mwh": rows[2940 - x["start"]][
        "initial_soc_mwh"
    ],
    "comparison_historical_ac_terminal_soc_mwh": soc[2942 - x["start"]].tolist(),
    "comparison_ac_peak_utilization": float(u[sel].max()),
    "comparison_dc_peak_utilization": float(ud[sel].max()),
    "comparison_ac_voltage_min": float(v[sel].min()),
    "comparison_ac_voltage_max": float(v[sel].max()),
    "capacity_mwh": cap.tolist(),
}
(p / "context-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
