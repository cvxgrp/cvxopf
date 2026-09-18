"""Read-only, snapshot-aligned stress charts for the completed toy S5 dashboard."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from experiments.case118_annual_hierarchy.analysis.analyze_stress_correlations import FEATURES, TARGETS, correlations, sha

ROOT = Path(__file__).resolve().parents[3]
MAGNITUDES = list(TARGETS)[:3]


def load_stress_data(snapshot, feature_path=None):
    """Join fixed annual DC features to this exact correction snapshot, not LATEST."""
    if feature_path is None:
        feature_path = Path(__file__).resolve().parent / "artifacts/dc_features/annual_features.json"
    feature_path = Path(feature_path)
    meta_path = feature_path.parent / "report.json"
    historical = json.loads(meta_path.read_text())["provenance"]
    current = snapshot["report"]
    for key in ("outer_sha256", "manifest_sha256", "fixture_hashes"):
        if current[key] != historical[key]:
            raise ValueError(f"DC/correction provenance mismatch: {key}")
    payload = json.loads(feature_path.read_text())
    annual = pd.DataFrame(payload["features"])
    if len(annual) != current["horizon"]:
        raise ValueError("Annual feature horizon mismatch")
    annual["timestamp"] = pd.to_datetime(payload["timestamps"], utc=True)
    annual["absolute_net_load_ramp_mw"] = annual.net_load_ramp_mw_per_hour.abs()
    csv_path = snapshot["path"] / "dispatch/intervals.csv"
    frame = pd.read_csv(csv_path)
    if (len(frame) != current["completed"] or not frame.iteration.is_unique
            or not frame.iteration.between(0, len(annual)-1).all()):
        raise ValueError("Correction interval identity/count mismatch")
    frame = frame.set_index("iteration").sort_index()
    timestamp = pd.to_datetime(frame.timestamp, utc=True)
    if not np.array_equal(timestamp, annual.loc[frame.index, "timestamp"]):
        raise ValueError("DC/correction timestamp mismatch")
    for key in FEATURES:
        frame[key] = annual.loc[frame.index, key]
    frame["calendar_stratum"] = (
        timestamp.dt.month.astype(str) + ":" + timestamp.dt.hour.astype(str)
        + ":" + (timestamp.dt.dayofweek >= 5).astype(str)
    )
    frame["study_hour"] = timestamp.dt.hour
    if not np.isfinite(frame[[*FEATURES, *MAGNITUDES, "soc_initial_l1_mwh"]]).all().all():
        raise ValueError("Nonfinite stress/adjustment values")
    np.testing.assert_allclose(frame.generator_counterdirection_mw,
        (frame.generator_l1_mw - frame.generator_net_change_mw.abs()) / 2, atol=1e-8)
    return dict(frame=frame, snapshot_utc=current["snapshot_utc"],
        source=feature_path, feature_sha256=sha(feature_path),
        dispatch_sha256=sha(csv_path), horizon=current["horizon"])


def stress_tables(data, collapse_noise=True, exclude_interventions=False):
    frame = data["frame"]
    if exclude_interventions:
        frame = frame.loc[~frame.operator_intervention.astype(bool)]
    if len(frame) < 3:
        raise ValueError("At least three completed intervals are needed")
    ranked_source = frame.copy()
    if collapse_noise:
        for key in [*FEATURES, *MAGNITUDES, "soc_initial_l1_mwh"]:
            ranked_source[key] = ranked_source[key].round(3 if "mw" in key else 4)
    raw = correlations(ranked_source, list(FEATURES), MAGNITUDES)
    adjusted = correlations(ranked_source, list(FEATURES), MAGNITUDES, calendar=True)
    return dict(frame=frame, ranked_source=ranked_source, raw=raw, adjusted=adjusted,
        collapse_noise=collapse_noise, snapshot_utc=data["snapshot_utc"], horizon=data["horizon"])


def plot_stress_matrix(tables):
    fig, axes = plt.subplots(1, 2, figsize=(14, 8), sharey=True, layout="constrained")
    cmap = plt.get_cmap("RdBu_r").with_extremes(bad="#eeeeee")
    # The maximum is almost saturated; do not interpret ranks of bound slack.
    saturated = tables["frame"].maximum_branch_utilization.min() > .999
    for ax, key, title in zip(axes, ("raw", "adjusted"),
            ("Raw Spearman correlation", "Calendar-adjusted rank correlation")):
        values = tables[key].to_numpy().copy()
        if saturated:
            values[list(FEATURES).index("maximum_branch_utilization")] = np.nan
        image = ax.imshow(values, vmin=-1, vmax=1, cmap=cmap, aspect="auto")
        ax.set_xticks(range(3), [TARGETS[k] for k in MAGNITUDES], fontsize=9)
        ax.set_yticks(range(len(FEATURES)), list(FEATURES.values()), fontsize=9)
        ax.set_title(title, fontsize=12)
        for (i, j), value in np.ndenumerate(values):
            ax.text(j, i, f"{value:+.2f}" if np.isfinite(value) else "—", ha="center",
                va="center", fontsize=9, color="white" if abs(value) > .55 else "black")
    fig.colorbar(image, ax=axes, shrink=.7, label="Rank correlation · fixed −1 to +1")
    suffix = " · solver-sized differences rounded" if tables["collapse_noise"] else " · unrounded"
    fig.suptitle(f"DC operating conditions vs AC adjustments · {len(tables['frame']):,} intervals{suffix}", fontsize=12)
    return fig


def pair_definitions():
    return [
        ("branches_at_or_above_95pct", "generator_counterdirection_mw", "renewable_available_fraction_of_load",
         "Breadth of heavy branch loading", "Branches ≥95% utilized", "Opposing redispatch (MW)", "Available RE / load", 1.),
        ("renewable_available_fraction_of_load", "generator_counterdirection_mw", "branches_at_or_above_95pct",
         "Renewable penetration", "Available RE / load (%)", "Opposing redispatch (MW)", "Branches ≥95%", 100.),
        ("net_load_mw", "generator_counterdirection_mw", "renewable_available_fraction_of_load",
         "Net load is not a monotone stress score", "Load − available RE (MW)", "Opposing redispatch (MW)", "Available RE / load", 1.),
        ("maximum_required_average_power_fraction", "battery_l1_mw", "study_hour",
         "DC signpost movement", "Required average power / rating (%)", "Battery L1 change (MW)", "Study hour (UTC)", 100.),
        ("system_load_mw", "battery_l1_mw", "study_hour",
         "System demand", "System load (MW)", "Battery L1 change (MW)", "Study hour (UTC)", 1.),
        ("soc_initial_l1_mwh", "soc_end_l1_mwh", "study_hour",
         "Inherited state divergence · retrospective", "Initial SoC L1 difference (MWh)", "End-SoC L1 difference (MWh)", "Study hour (UTC)", 1.),
    ]


def plot_stress_pairs(tables):
    """All points in physical units; color context and descriptive binned medians."""
    frame, ranked_source = tables["frame"], tables["ranked_source"]
    fig, axes = plt.subplots(3, 2, figsize=(14, 13), layout="constrained")
    for ax, (x, y, color, title, xlabel, ylabel, clabel, scale) in zip(axes.flat, pair_definitions()):
        palette = "twilight" if color == "study_hour" else "viridis"
        options = dict(vmin=0, vmax=23) if color == "study_hour" else {}
        points = ax.scatter(frame[x]*scale, frame[y], c=frame[color], cmap=palette,
            s=8, alpha=.4, edgecolors="none", rasterized=True, **options)
        # Equal-width bins, >=20 samples; no interpolation/fitted causal curve.
        edges = np.linspace(frame[x].min(), frame[x].max(), 13)
        bins = np.minimum(np.searchsorted(edges[1:], frame[x], side="right"), 11)
        medians = []
        for ordinal in range(12):
            subset = frame.loc[bins == ordinal]
            if len(subset) >= 20:
                medians.append((float(subset[x].median())*scale, float(subset[y].median())))
        if medians:
            mx, my = np.asarray(medians).T
            ax.plot(mx, my, "ks", ms=4, label="Bin median (n ≥ 20)")
        rho = correlations(ranked_source, [x], [y]).iloc[0, 0]
        adjusted = correlations(ranked_source, [x], [y], calendar=True).iloc[0, 0]
        ax.set_title(f"{title}\nρ = {rho:+.2f} · calendar-adjusted = {adjusted:+.2f}", fontsize=11)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.grid(alpha=.12)
        ax.legend(loc="best", fontsize=8)
        fig.colorbar(points, ax=ax, label=clabel, shrink=.85)
    fig.suptitle(f"Raw pairwise relationships · {len(frame):,} completed intervals\nPoints are raw units; adjusted coefficients do not describe a fitted scatterplot line", fontsize=13)
    return fig
