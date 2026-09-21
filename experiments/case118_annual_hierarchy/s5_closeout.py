"""Toy-study closeout tables and figures from frozen inputs and saved results.

No OPF solves or full S5 audit. Preparation checks the frozen input identities,
the accepted outer archive, dashboard checkpoint membership, and selected AC
first actions. Rendering needs only the small, saved closeout package.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import platform
import shutil

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_PACKAGE = HERE / "s5_closeout"
DEFAULT_SNAPSHOT = ROOT / "experiments/case118_annual_hierarchy/results/s5_analysis/dashboard_snapshots/20260917T152634.819559Z"
RUN = HERE / "results/s4b_annual_ac"
SIGNALS = {
    "load_mw": "Gross active load",
    "available_nd_mw": "Available wind + utility solar",
    "net_load_mw": "Available net load",
    "solar_mw": "Utility solar availability",
    "wind_mw": "Wind availability",
}
FEATURES = [
    "load_mw", "net_load_mw", "available_nd_mw", "renewable_fraction",
    "p95_dc_utilization", "dc_branches_ge_95pct", "dc_soc_fraction",
    "dc_charging_mw", "dc_discharging_mw", "signpost_power_fraction",
    "absolute_net_ramp_mw",
]
TARGETS = [
    "generator_net_change_mw", "generator_l1_mw", "generator_counterdirection_mw",
    "battery_net_change_mw", "battery_l1_mw", "soc_net_change_mwh", "soc_end_l1_mwh",
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def read_json(path):
    return json.loads(path.read_text())


def calendar_matrix(values):
    """Hour rows, chronological day columns; never interpolate."""
    values = np.asarray(values, dtype=float)
    if values.shape != (8760,) or not np.isfinite(values).all():
        raise ValueError("expected a finite 8760-hour nonleap year")
    return values.reshape(365, 24).T


def shortfall_events(net_load, capacity, timestamps):
    """Contiguous, non-wrapping exceedances; one-hour interval energies."""
    deficit = np.maximum(np.asarray(net_load) - capacity, 0)
    edges = np.diff(np.r_[False, deficit > 0, False].astype(int))
    return [dict(start=str(timestamps[a]), stop_exclusive=str(timestamps[b-1] + pd.Timedelta(hours=1)),
                 hours=int(b-a), energy_mwh=float(deficit[a:b].sum()),
                 peak_mw=float(deficit[a:b].max()))
            for a, b in zip(np.where(edges == 1)[0], np.where(edges == -1)[0], strict=True)]


def input_summary(frame, capacity):
    events = shortfall_events(frame.net_load_mw, capacity, frame.index)
    record = {f"{key.removesuffix('_mw')}_mwh": float(frame[key].sum()) for key in SIGNALS}
    for key in SIGNALS:
        record.update({f"{key}_{stat}": float(getattr(frame[key], stat)()) for stat in ("mean", "min", "max")})
    record.update(renewable_to_load_energy=record["available_nd_mwh"]/record["load_mwh"],
                  shortfall_hours=sum(e["hours"] for e in events), shortfall_events=len(events),
                  shortfall_energy_mwh=sum(e["energy_mwh"] for e in events),
                  peak_shortfall_mw=max((e["peak_mw"] for e in events), default=0),
                  longest_shortfall_hours=max((e["hours"] for e in events), default=0),
                  hours_net_below_dispatchable=int((frame.net_load_mw < capacity).sum()),
                  aggregate_headroom_mwh=float(np.maximum(capacity-frame.net_load_mw, 0).sum()))
    # Differences stay inside the selected period; no wrapping or cross-month ramp.
    for key in ("load_mw", "net_load_mw"):
        diff = frame[key].diff().dropna()
        record[f"{key}_largest_up_ramp_mw_per_hour"] = float(diff.max())
        record[f"{key}_largest_down_ramp_mw_per_hour"] = float(diff.min())
    return record


def rank_correlations(frame, *, adjusted=False, rounded=False):
    values = frame[FEATURES + TARGETS].copy()
    if rounded:
        for key in values:
            values[key] = values[key].round(3 if "mw" in key else 4)
    ranks = values.rank(method="average")
    if adjusted:
        t = pd.DatetimeIndex(frame.index)
        groups = pd.Series(list(zip(t.month, t.hour, t.dayofweek >= 5)), index=frame.index)
        ranks -= ranks.groupby(groups).transform("mean")
    return ranks.corr().loc[FEATURES, TARGETS]


def verify_dispatch(frame, meta, fixture, outer, soc, power):
    """Check all memberships; independently recompute a bounded action sample."""
    from experiments.case118_annual_hierarchy.s4b_manifest import S4_OUTER_ARCHIVE_SHA256, EXPECTED_MANIFEST_SHA256
    if (meta["fixture_hashes"] != fixture.hashes or meta["outer_sha256"] != S4_OUTER_ARCHIVE_SHA256
            or meta["manifest_sha256"] != EXPECTED_MANIFEST_SHA256 or meta["completed"] != 8760
            or meta["storage_ids"] != list(fixture.storage_device_ids)):
        raise ValueError("dashboard identity or coverage mismatch")
    if not np.array_equal(frame.iteration, np.arange(8760)):
        raise ValueError("dashboard must contain every hour exactly once in order")
    if not pd.DatetimeIndex(pd.to_datetime(frame.timestamp, utc=True)).equals(fixture.inputs.df_load_p.index):
        raise ValueError("dashboard calendar mismatch")
    entries = {}
    for ref in meta["checkpoints"]:
        path = RUN / Path(ref["path"]).parent.name / "checkpoint.json"
        if sha(path) != ref["sha256"]:
            raise ValueError("retained checkpoint changed")
        cp = read_json(path)
        for e in cp["windows"]:
            i = e["iteration"]
            if i in entries or frame.iloc[i].archive_sha256 != e["sha256"]:
                raise ValueError("dashboard/checkpoint membership mismatch")
            entries[i] = (path.parent / e["relative_path"], e["sha256"])
    if set(entries) != set(range(8760)):
        raise ValueError("checkpoint coverage mismatch")
    ids = meta["storage_ids"]
    db = frame[[f"{s}.delta_b_mw" for s in ids]].to_numpy()
    ds = frame[[f"{s}.delta_soc_end_mwh" for s in ids]].to_numpy()
    for actual, expected in [(db.sum(1), frame.battery_net_change_mw), (abs(db).sum(1), frame.battery_l1_mw),
                             (abs(ds).sum(1), frame.soc_end_l1_mwh),
                             ((frame.generator_l1_mw-abs(frame.generator_net_change_mw))/2, frame.generator_counterdirection_mw)]:
        np.testing.assert_allclose(actual, expected, atol=1e-8, rtol=0)
    sample = sorted({0, 2448, 6122, 8759, int(frame.generator_l1_mw.idxmax()), int(frame.battery_l1_mw.idxmax())})
    for i in sample:
        path, digest = entries[i]
        if sha(path) != digest:
            raise ValueError("sample action archive hash mismatch")
        w = json.loads(gzip.decompress(path.read_bytes()))
        controls = [a for a in w["attempts"] if a["supplied_executed_action"]]
        if len(controls) != 1 or controls[0]["attempt_id"] != frame.iloc[i].controller_id:
            raise ValueError("sample controlling action mismatch")
        result = controls[0]["result"]
        dg = np.asarray(result["Pg"])[0] - np.asarray(outer["result"]["Pg"])[i]
        np.testing.assert_allclose([dg.sum(), abs(dg).sum()], frame.iloc[i][["generator_net_change_mw", "generator_l1_mw"]].astype(float), atol=1e-8, rtol=0)
        np.testing.assert_allclose(np.asarray(result["b"])[0]-power[i], db[i], atol=1e-8, rtol=0)
        np.testing.assert_allclose(np.asarray(result["soc"])[0]-soc[i+1], ds[i], atol=1e-6, rtol=0)
    return db, ds, sample


def prepare(destination, snapshot):
    from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
    from experiments.case118_annual_hierarchy.s4b_manifest import (
        load_authoritative_outer, EXPECTED_BOUNDARIES, S4_OUTER_ARCHIVE_PATH,
    )
    fixture = load_s4_fixture()
    inputs = fixture.inputs
    outer, soc, power = load_authoritative_outer()
    p, q, nd = (x.to_numpy() for x in (inputs.df_load_p, inputs.df_load_q, inputs.df_nd))
    np.testing.assert_array_equal(p, outer["result"]["p_load"])
    np.testing.assert_array_equal(q, outer["result"]["q_load"])
    meta = read_json(snapshot / "dispatch/report.json")
    dispatch = pd.read_csv(snapshot / "dispatch/intervals.csv", float_precision="round_trip")
    db, ds, sample = verify_dispatch(dispatch, meta, fixture, outer, soc, power)
    index = inputs.df_load_p.index
    wind = inputs.df_nd["wind_case118"].to_numpy()
    solar = inputs.df_nd["solar_case118"].to_numpy()
    frame = pd.DataFrame(dict(load_mw=p.sum(1), available_nd_mw=nd.sum(1),
                              net_load_mw=p.sum(1)-nd.sum(1), solar_mw=solar, wind_mw=wind), index=index)
    capacity = sum(g.p_max_mw for g in inputs.generators)
    storage = [asdict(u) for u in inputs.storage]
    energy = sum(u.capacity for u in inputs.storage)
    rating = sum(u.apparent_power_rating for u in inputs.storage)
    annual = input_summary(frame, capacity)
    annual.update(dispatchable_pmax_mw=capacity, storage_energy_mwh=energy, storage_active_power_limit_mw=rating,
                  storage_apparent_rating_mva=rating, storage_energy_over_average_load_hours=energy/frame.load_mw.mean(),
                  storage_e_over_p_hours=energy/rating, base_load_mw=float(np.asarray(inputs.case["bus"])[:, 2].sum()),
                  distributed_solar="not modeled", calendar="2025 UTC", delta_hours=1.0)
    # Recompute annual DC features from its exact primal, without an optimization or S5 audit.
    branch = np.asarray(inputs.case["branch"])
    rated = (branch[:, 10] > 0) & (branch[:, 5] > 0) & np.isfinite(branch[:, 5])
    utilization = abs(np.asarray(outer["result"]["p_flows"])[:, rated])/branch[rated, 5]
    stop = np.minimum(np.arange(8760)+fixture.policy.ac_window_steps,
                      np.asarray(EXPECTED_BOUNDARIES)[np.searchsorted(EXPECTED_BOUNDARIES, np.arange(8760), side="right")])
    ratings = np.array([u.apparent_power_rating for u in inputs.storage])
    required = (soc[:-1]-soc[stop])/((stop-np.arange(8760))[:, None]*ratings)
    joined = dispatch.set_index(index).copy()
    for key in SIGNALS:
        joined[key] = frame[key]
    joined["renewable_fraction"] = frame.available_nd_mw/frame.load_mw
    joined["p95_dc_utilization"] = np.quantile(utilization, .95, axis=1)
    joined["dc_branches_ge_95pct"] = (utilization >= .95).sum(1)
    joined["dc_soc_fraction"] = soc[:-1].sum(1)/energy
    joined["dc_charging_mw"] = np.maximum(-power, 0).sum(1)
    joined["dc_discharging_mw"] = np.maximum(power, 0).sum(1)
    joined["signpost_power_fraction"] = abs(required).max(1)
    joined["absolute_net_ramp_mw"] = frame.net_load_mw.diff().abs().fillna(0)
    joined["soc_net_change_mwh"] = ds.sum(1)
    joined["recovery_selected"] = ~joined.controller_id.str.endswith(("-00-primary_controlling", "/spec-v1-00"))
    merged = read_json(RUN / "merged-result.json")
    if joined.recovery_selected.sum() != merged["recovery_window_count"]:
        raise ValueError("recovery definition differs from accepted merge")
    ac_power = power + db
    ac_soc = soc[1:] + ds
    if not np.isfinite(joined[FEATURES+TARGETS]).all().all():
        raise ValueError("nonfinite closeout measurements")
    destination.mkdir(parents=True, exist_ok=False)
    frame.to_csv(destination / "aggregate_inputs.csv", index_label="timestamp")
    np.savez_compressed(destination / "final_inputs.npz", load_p_mw=p, load_q_mvar=q, available_nd_mw=nd)
    write_json(destination / "devices.json", dict(loads=[asdict(u) for u in inputs.loads],
               nondispatchable=[asdict(u) for u in inputs.nondispatchable], storage=storage,
               generators=[asdict(u) for u in inputs.generators],
               load_ids=list(inputs.df_load_p.columns), nd_ids=list(inputs.df_nd.columns)))
    write_json(destination / "input_summary.json", annual)
    pd.DataFrame([dict(month=str(month), **input_summary(group, capacity))
                  for month, group in frame.groupby(frame.index.strftime("%Y-%m"))]).to_csv(destination / "monthly_inputs.csv", index=False)
    write_json(destination / "shortfall_events.json", shortfall_events(frame.net_load_mw, capacity, index))
    joined.to_csv(destination / "hourly_comparison.csv", index_label="calendar_timestamp")
    summary = joined[TARGETS].agg(["mean", "median", "min", "max"]).T
    summary["p95"] = joined[TARGETS].quantile(.95)
    summary["sum_over_hours"] = joined[TARGETS].sum()
    # A state-difference sum has no throughput interpretation; do not publish it.
    summary.loc[["soc_net_change_mwh", "soc_end_l1_mwh"], "sum_over_hours"] = np.nan
    summary.to_csv(destination / "dispatch_summary.csv", index_label="metric")
    for name, adjusted, rounded in [("raw", False, False), ("calendar", True, False), ("rounded_calendar", True, True)]:
        rank_correlations(joined, adjusted=adjusted, rounded=rounded).to_csv(destination / f"correlations_{name}.csv")
    rank_correlations(joined.loc[~joined.operator_intervention], adjusted=True, rounded=True).to_csv(destination / "correlations_without_interventions.csv")
    groups = joined.groupby([joined.index.month, "recovery_selected"])[FEATURES+TARGETS].mean()
    groups["hours"] = joined.groupby([joined.index.month, "recovery_selected"]).size()
    groups.to_csv(destination / "monthly_recovery_groups.csv", index_label=["month", "recovery_selected"])
    branch_table = pd.DataFrame(dict(branch_row=np.flatnonzero(rated), from_bus=branch[rated, 0].astype(int),
        to_bus=branch[rated, 1].astype(int), rating_mva=branch[rated, 5],
        hours_dc_ge_95pct=(utilization >= .95).sum(0), hours_dc_ge_999pct=(utilization >= .999).sum(0),
        maximum_dc_mw_over_rating=utilization.max(0)))
    branch_table.to_csv(destination / "dc_branch_loading.csv", index=False)
    operation = dict(maximum_dc_utilization_min=float(utilization.max(1).min()),
        maximum_dc_utilization_max=float(utilization.max(1).max()),
        dc_renewable_used_mwh=float(np.asarray(outer["result"]["p_nd"]).sum()),
        dc_storage_throughput_mwh=float(abs(power).sum()),
        ac_storage_throughput_mwh=float(abs(ac_power).sum()),
        ac_renewable_used_mwh=float(nd.sum()-merged["renewable_curtailment_mwh"]),
        dc_soc_min_mwh=float(soc.sum(1).min()), dc_soc_max_mwh=float(soc.sum(1).max()),
        ac_end_soc_min_mwh=float(ac_soc.sum(1).min()), ac_end_soc_max_mwh=float(ac_soc.sum(1).max()),
        mean_generator_l1_pct_load=float((100*joined.generator_l1_mw/frame.load_mw).mean()),
        mean_generator_net_pct_load=float((100*joined.generator_net_change_mw/frame.load_mw).mean()),
        recovery_hours=int(joined.recovery_selected.sum()))
    np.testing.assert_allclose(operation["ac_storage_throughput_mwh"], merged["storage_throughput_mwh"], atol=1e-6, rtol=0)
    dc_curtailment = float(nd.sum()-np.asarray(outer["result"]["p_nd"]).sum())
    operation["dc_renewable_curtailment_mwh"] = dc_curtailment
    operation["net_generation_energy_change_mwh"] = float(joined.generator_net_change_mw.sum())
    operation["generation_energy_balance_residual_mwh"] = float(
        joined.generator_net_change_mw.sum() + joined.battery_net_change_mw.sum()
        - merged["active_losses_mwh"] - merged["renewable_curtailment_mwh"] + dc_curtailment)
    np.testing.assert_allclose(operation["generation_energy_balance_residual_mwh"], 0, atol=.01, rtol=0)
    pg = np.asarray(outer["result"]["Pg"])
    coefficients = np.array([g.cost_coeffs for g in inputs.generators])
    operation["dc_generation_cost_common_coefficients"] = float(
        (coefficients[:, 0] + pg*coefficients[:, 1] + pg**2*coefficients[:, 2]).sum())
    operation["ac_generation_cost"] = merged["generation_cost"]
    nodal = []
    for row in np.asarray(inputs.case["bus"]):
        bus = int(row[0])
        nd_units = [u for u in inputs.nondispatchable if u.bus == bus]
        batteries = [u for u in inputs.storage if u.bus == bus]
        nd_energy = sum(float(inputs.df_nd[u.device_id].sum()) for u in nd_units)
        nodal.append(dict(bus=bus, base_load_mw=float(row[2]), load_share=float(row[2]/annual["base_load_mw"]),
            dispatchable_pmax_mw=sum(g.p_max_mw for g in inputs.generators if g.bus == bus),
            renewable_ids=";".join(u.device_id for u in nd_units), available_renewable_mwh=nd_energy,
            storage_power_mw=sum(u.apparent_power_rating for u in batteries),
            storage_energy_mwh=sum(u.capacity for u in batteries),
            annual_load_minus_available_renewables_mwh=float(row[2]/annual["base_load_mw"]*annual["load_mwh"]-nd_energy)))
    pd.DataFrame(nodal).to_csv(destination / "bus_resources.csv", index=False)
    write_json(destination / "operation_summary.json", operation)
    # These few retained records directly support this report; broader triage remains Stage 0b.
    for source, name in [(snapshot / "dispatch/report.json", "dispatch_source.json"),
                         (RUN / "merged-result.json", "retained_merge.json"),
                         (ROOT / "experiments/case118_annual_hierarchy/results/s5_analysis/completed-analysis-summary.json", "prior_analysis_summary.json")]:
        shutil.copyfile(source, destination / name)
    sources = [S4_OUTER_ARCHIVE_PATH, RUN / "merged-result.json", snapshot / "dispatch/report.json",
               snapshot / "dispatch/intervals.csv", Path(__file__), HERE / "scenario.py", HERE / "s4_fixture.py"]
    write_json(destination / "provenance.json", dict(schema_version=1, scenario="analytical toy Case118, not Tracy",
        fixture_hashes=fixture.hashes, calendar="2025 UTC, hourly interval starts; end-SoC plotted at start hour",
        snapshot_utc=meta["snapshot_utc"], sampled_ac_hours=sample,
        verification="All checkpoint memberships and input hashes; bounded first-action recomputation. No full S5 audit.",
        sources={str(p.relative_to(ROOT)): sha(p) for p in sources},
        versions=dict(python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__),
        units=dict(final_inputs="MW, MVAr; see NPZ field suffixes", inputs="MW; hourly energy MW × 1 h",
                   differences="MW for power, MWh for end-SoC; sum of state differences is not throughput"),
        artifacts={p.name: sha(p) for p in sorted(destination.iterdir()) if p.is_file()}))


def render(destination):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    provenance = read_json(destination / "provenance.json")
    for name, expected in provenance["artifacts"].items():
        if sha(destination / name) != expected:
            raise ValueError(f"closeout artifact changed: {name}")
    inputs = pd.read_csv(destination / "aggregate_inputs.csv")
    comparison = pd.read_csv(destination / "hourly_comparison.csv")

    def heatmaps(columns, labels, filename, signed=False):
        fig, axes = plt.subplots(len(columns), 1, figsize=(13, 2.0*len(columns)), layout="constrained")
        for ax, values, label in zip(np.atleast_1d(axes), columns, labels, strict=True):
            matrix = calendar_matrix(values)
            limits = dict(vmin=-abs(matrix).max(), vmax=abs(matrix).max()) if signed or label.startswith("Available net") else dict(vmin=0, vmax=matrix.max())
            im = ax.imshow(matrix, origin="lower", aspect="auto", extent=(.5, 365.5, -.5, 23.5),
                           cmap="RdBu_r" if "vmin" in limits and limits["vmin"] < 0 else "viridis", **limits)
            ax.set_title(label, loc="left", fontsize=11)
            ax.set_ylabel("Hour UTC")
            ax.set_yticks([0, 6, 12, 18, 23])
            fig.colorbar(im, ax=ax, pad=.015, fraction=.025)
        np.atleast_1d(axes)[-1].set_xlabel("Day of 2025 (UTC); chronological columns")
        fig.savefig(destination / filename, dpi=160)
        plt.close(fig)

    heatmaps([inputs[k] for k in SIGNALS], [v+" (MW)" for v in SIGNALS.values()], "input_heatmaps.png")
    heatmaps([comparison[k] for k in ["generator_net_change_mw", "battery_net_change_mw", "soc_net_change_mwh"]],
             ["Net generator change (MW)", "Net battery-power change (MW)", "Net end-SoC difference (MWh)"], "signed_changes.png", True)
    heatmaps([comparison[k] for k in ["generator_l1_mw", "generator_counterdirection_mw", "battery_l1_mw", "soc_end_l1_mwh"]],
             ["Generator absolute change (MW)", "Opposing generator redispatch (MW)", "Battery absolute change (MW)", "End-SoC absolute difference (MWh)"], "absolute_changes.png")
    raw = pd.read_csv(destination / "correlations_raw.csv", index_col=0)
    adjusted = pd.read_csv(destination / "correlations_rounded_calendar.csv", index_col=0)
    fig, axes = plt.subplots(1, 2, figsize=(15, 7), sharey=True, layout="constrained")
    for ax, table, title in zip(axes, [raw, adjusted], ["Raw rank correlation", "Calendar-adjusted rank correlation · rounded"]):
        im = ax.imshow(table, vmin=-1, vmax=1, cmap="RdBu_r", aspect="auto")
        ax.set_xticks(range(len(TARGETS)), ["Gen net", "Gen L1", "Gen opposing", "Battery net", "Battery L1", "SoC net", "SoC L1"], rotation=55, ha="right")
        ax.set_yticks(range(len(FEATURES)), FEATURES)
        ax.set_title(title)
        for (i, j), v in np.ndenumerate(table.to_numpy()):
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=8, color="white" if abs(v)>.55 else "black")
    fig.colorbar(im, ax=axes, shrink=.75, label="Descriptive association; no causal or predictive claim")
    fig.savefig(destination / "correlations.png", dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "render"])
    parser.add_argument("--destination", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.destination, args.snapshot)
    else:
        render(args.destination)


if __name__ == "__main__":
    main()
