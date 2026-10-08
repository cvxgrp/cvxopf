"""No-solve E3 proposals from verified annual DC states; see E3_SELECTION_PLAN.md."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .annual_results import HERE, load_results, sha
from .select_stage_d import REGIMES


RULE = dict(
    horizon_hours=24, event_hours=6, target_fraction=0.5,
    score="equal-device, equal-endpoint RMS deviation from 0.5",
    tie_breaks=["maximum absolute device endpoint deviation", "event/window midpoint distance", "earliest global start"],
    fleet_weighting="capacity-weighted; descriptive only, not selection",
)


def rank_candidates(soc, capacity, net, anchor):
    """All containing windows; SoC has N+1 boundaries for N hourly inputs."""
    soc, capacity, net = (np.asarray(a, dtype=float) for a in (soc, capacity, net))
    if capacity.ndim != 1 or not len(capacity) or not np.isfinite(capacity).all() or np.any(capacity <= 0):
        raise ValueError("capacity must be a finite positive device vector")
    if net.ndim != 1 or len(net) < 24 or not np.isfinite(net).all():
        raise ValueError("net load must contain at least 24 finite hourly inputs")
    if soc.shape != (len(net)+1, len(capacity)) or not np.isfinite(soc).all():
        raise ValueError("SoC must contain N+1 finite, device-aligned boundaries")
    if np.any(soc < -1e-5) or np.any(soc > capacity+1e-5):
        raise ValueError("annual SoC violates device energy bounds")
    if isinstance(anchor, bool) or not isinstance(anchor, (int, np.integer)) or not 0 <= anchor <= len(net)-6:
        raise ValueError("anchor must identify a complete six-hour input event")
    candidates = []
    for start in range(max(0, anchor-18), min(anchor, len(net)-24)+1):
        stop = start+24
        endpoints = soc[[start, stop]]/capacity
        deviation = endpoints-.5
        fleet = soc[[start, stop]].sum(axis=1)/capacity.sum()
        values = net[start:stop]
        candidates.append(dict(
            start=start, stop=stop, event_offset_hours=anchor-start,
            score_rms_fraction=float(np.sqrt(np.mean(deviation**2))),
            max_device_deviation_fraction=float(abs(deviation).max()),
            event_midpoint_distance_hours=abs(anchor+3-start-12),
            start_fraction=endpoints[0].tolist(), end_fraction=endpoints[1].tolist(),
            fleet_start_fraction=float(fleet[0]), fleet_end_fraction=float(fleet[1]),
            net_energy_mwh=float(values.sum()),
            positive_net_energy_mwh=float(np.maximum(values, 0).sum()),
            surplus_available_energy_mwh=float(np.maximum(-values, 0).sum()),
        ))
    candidates.sort(key=lambda c: (c["score_rms_fraction"], c["max_device_deviation_fraction"],
                                  c["event_midpoint_distance_hours"], c["start"]))
    for rank, candidate in enumerate(candidates, 1):
        candidate["rank"] = rank
    return candidates


def plots(output, data, periods, capacity, ids):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    soc = data["runs"]["lossy_dc"]["boundary_soc_mwh"]
    inputs = data["inputs"]
    fig, axes = plt.subplots(4, 2, figsize=(14, 12), constrained_layout=True)
    endpoint_fig, endpoint_axes = plt.subplots(4, 2, figsize=(15, 19), constrained_layout=True)
    for row, period in enumerate(periods):
        c = period["candidates"][0]
        s, e, offset = c["start"], c["stop"], c["event_offset_hours"]
        block = inputs.iloc[s:e]
        renewable = block[["solar_mw", "wind_mw", "dist_solar_mw"]].sum(axis=1)
        ax = axes[row, 0]
        for label, values, color in (("Load", block.load_mw, "#333333"),
                                     ("Available renewables", renewable, "#009E73"),
                                     ("Available net load", block.net_load_mw, "#0072B2")):
            ax.stairs(values.to_numpy()/1000, np.arange(25), label=label, color=color)
        ax.axhline(0, color="black", linewidth=.5)
        ax.set_ylabel("GW")
        ax.set_title(f"{period['regime']} — {c['start_fixed_utc_minus_08']}", fontsize=10)
        ax = axes[row, 1]
        fractions = soc[s:e+1]/capacity
        ax.plot(np.arange(25), fractions, color="#0072B2", alpha=.28, linewidth=.8)
        ax.plot(np.arange(25), soc[s:e+1].sum(axis=1)/capacity.sum(),
                color="#D55E00", linewidth=2, label="Capacity-weighted fleet")
        ax.axhline(.5, color="black", linestyle="--", linewidth=.8, label="Proposed 50% boundaries")
        ax.set_ylabel("Annual DC SoC / capacity")
        ax.set_ylim(-.02, 1.02)
        ax.set_title("27 individual batteries + fleet; selection context, not E3 dispatch", fontsize=9)
        for ax in axes[row]:
            ax.axvspan(offset, offset+6, color="#CC79A7", alpha=.16)
            ax.set_xlim(0, 24)
            ax.set_xlabel("Hours from proposed start; shaded: retained six-hour event")
            ax.grid(alpha=.15)
        chronological = sorted(period["candidates"], key=lambda c: c["start"])
        chosen_column = next(i for i, entry in enumerate(chronological) if entry["rank"] == 1)
        for col, key in enumerate(("start_fraction", "end_fraction")):
            ax = endpoint_axes[row, col]
            values = np.array([entry[key] for entry in chronological]).T-.5
            im = ax.imshow(values, aspect="auto", cmap="RdBu_r", vmin=-.5, vmax=.5)
            ax.set_yticks(np.arange(len(ids)), [d.removeprefix("storage_") for d in ids], fontsize=7)
            ax.set_xticks(np.arange(len(chronological)),
                          [entry["event_offset_hours"] for entry in chronological], fontsize=8)
            ax.axvline(chosen_column-.5, color="black", linewidth=1)
            ax.axvline(chosen_column+.5, color="black", linewidth=1)
            ax.set_xlabel("Event offset from candidate start (h); outlined: ranked proposal")
            ax.set_title(f"{period['regime']} — {key.replace('_', ' ')} deviation from 50%", fontsize=10)
    axes[0, 0].legend(fontsize=8)
    axes[0, 1].legend(fontsize=8)
    fig.savefig(output / "proposed_windows.png", dpi=160)
    endpoint_fig.colorbar(im, ax=endpoint_axes, label="SoC fraction minus 0.5", shrink=.5)
    endpoint_fig.savefig(output / "candidate_endpoints.png", dpi=160)
    plt.close(fig)
    plt.close(endpoint_fig)


def report(evidence):
    lines = ["# E3 24-hour window proposals", "", "2026-10-02. No OPF solves; awaiting owner acceptance of dates and boundaries.", "",
             "Ranked from the accepted annual lossy-DC trajectory using the predeclared equal-device endpoint RMS rule.", "",
             "| Retained event | Proposed start (fixed UTC−08) | Stop, exclusive | Global hours | Event offset | RMS deviation (percentage points) | Worst deviation (pp) | Fleet start → end |",
             "| --- | --- | --- | --- | ---: | ---: | ---: | --- |"]
    for period in evidence["periods"]:
        c = period["candidates"][0]
        lines.append(f"| {period['regime']} | {c['start_fixed_utc_minus_08']} | {c['stop_fixed_utc_minus_08']} | [{c['start']}, {c['stop']}) | {c['event_offset_hours']} h | {100*c['score_rms_fraction']:.2f} | {100*c['max_device_deviation_fraction']:.2f} | {100*c['fleet_start_fraction']:.1f}% → {100*c['fleet_end_fraction']:.1f}% |")
    lines += ["", "![Input and battery trajectory context](proposed_windows.png)", "",
              "![All candidate device endpoint deviations](candidate_endpoints.png)", "",
              "All 76 candidates and every device endpoint are retained in `selection.json` and `candidate_endpoints.csv`. Scores use fractions, not percentage points; displays above multiply by 100. All 27 batteries receive equal weight. Fleet SoC is capacity-weighted and descriptive only.", "",
              "## Boundary interpretation", "",
              "The proposed primary experiment remains 50%-to-50% per battery with a hard terminal equality. The displayed annual states are context, not proposed initial/terminal vectors or interior constraints. Selecting the closest candidate does not establish that either endpoint is near 50% or that the energy-neutral policy faithfully reproduces the annual event.", ""]
    for period in evidence["periods"]:
        c = period["candidates"][0]
        start, end = np.array(c["start_fraction"]), np.array(c["end_fraction"])
        lines.append(f"- {period['regime']}: annual device start range {100*start.min():.1f}–{100*start.max():.1f}%, end range {100*end.min():.1f}–{100*end.max():.1f}%; 24-hour available net energy {c['net_energy_mwh']/1000:+.2f} GWh. These describe available net load, not realized shortages or ENS.")
    lines += ["", "No alternative boundary leg is selected or pooled. Please accept or revise the exact dates and the primary 50%-to-50% policy; any net-depletion/accumulation leg needs explicit identity-aligned vectors and four additional arms in its budget.", "",
              "## Provenance and stopping point", "",
              "The existing annual reader verifies Stage A inputs, accepted Stage C archives and device identities. Their hashes must match the retained Stage D selection; its original event identities/states are checked without rerunning event selection. `selection.json` retains source hashes, selector/plan hashes, capacities, identities, rule and the proposed primary boundary vectors.", "",
              "Run from the repository root using a fresh output directory:", "", "```sh",
              "uv run --extra dev --extra notebook python -m experiments.case118_tracy_2021.select_e3 --output experiments/case118_tracy_2021/results/e3_selection_reproduction", "```", "",
              "Required retained annual/input archives are not part of a fresh public clone; missing/substituted evidence fails, with no synthetic fallback. CI selection tests use public synthetic arrays only. This analytical checkpoint does not implement or launch the numerical runner, resume Stage D, establish annual representativeness, or close M11.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "e3_selection")
    args = parser.parse_args()
    data = load_results()
    original_path = HERE / "stage_d_selection" / "selection.json"
    original = json.loads(original_path.read_text())
    ids = data["tables"]["batteries"].device_id.tolist()
    if original["source_hashes"] != data["hashes"] or original["storage_device_ids"] != ids:
        raise ValueError("annual source/identities no longer match retained event selection")
    if original["selection_source_sha256"] != sha(HERE / "select_stage_d.py"):
        raise ValueError("historical event selector source changed")
    if original["shard_manifest_sha256"] != sha(HERE / "SHARD_BOUNDARIES.json"):
        raise ValueError("historical shard provenance changed")
    if [p["regime"] for p in original["periods"]] != list(REGIMES):
        raise ValueError("expected exactly the four original event identities")
    soc = data["runs"]["lossy_dc"]["boundary_soc_mwh"]
    capacity = data["tables"]["batteries"].capacity_mwh.to_numpy()
    inputs = data["inputs"]
    renewable = inputs[["solar_mw", "wind_mw", "dist_solar_mw"]].sum(axis=1)
    np.testing.assert_allclose(inputs.net_load_mw, inputs.load_mw-renewable, atol=1e-8, rtol=1e-12)
    periods, rows = [], []
    for event in original["periods"]:
        anchor = event["start"]
        np.testing.assert_allclose(soc[anchor], event["initial_soc_mwh"], rtol=0, atol=1e-8)
        if event["start_fixed_utc_minus_08"] != inputs.index[anchor].isoformat()+"-08:00":
            raise ValueError("event time does not match fixed source calendar")
        candidates = rank_candidates(soc, capacity, inputs.net_load_mw, anchor)
        for c in candidates:
            for key, index in (("start_fixed_utc_minus_08", c["start"]), ("stop_fixed_utc_minus_08", c["stop"])):
                c[key] = (inputs.index[0]+pd.Timedelta(hours=index)).isoformat()+"-08:00"
            for k, identity in enumerate(ids):
                rows.append(dict(regime=event["regime"], rank=c["rank"], start=c["start"], stop=c["stop"],
                    score_rms_fraction=c["score_rms_fraction"], device_id=identity, capacity_mwh=float(capacity[k]),
                    start_fraction=c["start_fraction"][k], end_fraction=c["end_fraction"][k],
                    start_deviation_fraction=c["start_fraction"][k]-.5, end_deviation_fraction=c["end_fraction"][k]-.5))
        periods.append(dict(regime=event["regime"], anchor=anchor, candidate_count=len(candidates), candidates=candidates))
    evidence = dict(schema_version=1, status="proposed_not_owner_accepted_not_execution_authority", rule=RULE,
        source_hashes=data["hashes"], original_selection_sha256=sha(original_path),
        stage_a_manifest_sha256=sha(HERE / "stage_a" / "manifest.json"),
        batteries_sha256=sha(HERE / "stage_a" / "batteries.csv"),
        aggregate_inputs_sha256=sha(HERE / "results" / "stage_a_active_inputs" / "aggregate_inputs.csv"),
        selector_sha256=sha(Path(__file__)), plan_sha256=sha(HERE / "E3_SELECTION_PLAN.md"),
        storage_device_ids=ids, capacity_mwh=capacity.tolist(),
        primary_initial_soc_mwh=(.5*capacity).tolist(), primary_terminal_soc_mwh=(.5*capacity).tolist(),
        alternative_boundary_legs=[], solves_attempted=0, periods=periods)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "selection.json").write_text(json.dumps(evidence, indent=2, allow_nan=False)+"\n")
    pd.DataFrame(rows).to_csv(args.output / "candidate_endpoints.csv", index=False)
    plots(args.output, data, periods, capacity, ids)
    (args.output / "REPORT.md").write_text(report(evidence))
    for p in periods:
        c = p["candidates"][0]
        print(p["regime"], c["start"], c["start_fixed_utc_minus_08"], "RMS", c["score_rms_fraction"], "worst", c["max_device_deviation_fraction"])


if __name__ == "__main__":
    main()
