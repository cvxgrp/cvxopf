"""Exploratory, cached DC-stress/AC-adjustment associations. No OPF calls.

Run in an isolated environment with numpy, pandas and matplotlib; leaves study
and notebook untouched. Calendar-adjusted
coefficients are correlations of within-stratum demeaned global ranks, not
causal estimates or out-of-sample prediction scores. No IID significance tests.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
FEATURES = {
    "system_load_mw": "System load",
    "net_load_mw": "Net load (load − available RE)",
    "renewable_available_mw": "Renewable availability",
    "renewable_available_fraction_of_load": "Renewable share of load",
    "maximum_branch_utilization": "Maximum DC branch utilization",
    "p95_branch_utilization": "95th-percentile branch utilization",
    "branches_at_or_above_95pct": "Branches ≥95% utilized",
    "aggregate_soc_fraction": "Aggregate initial DC SoC",
    "aggregate_charging_mw": "DC charging power",
    "aggregate_discharging_mw": "DC discharging power",
    "maximum_required_average_power_fraction": "Required signpost power / rating",
    "maximum_signpost_movement_fraction": "Signpost movement / capacity",
    "absolute_net_load_ramp_mw": "Absolute net-load ramp",
}
TARGETS = {
    "generator_counterdirection_mw": "Opposing generator\nredispatch (MW)",
    "battery_l1_mw": "Battery power\nL1 change (MW)",
    "soc_end_l1_mwh": "End-SoC\nL1 change (MWh)",
    "generator_net_change_mw": "Net generator\nchange (MW)",
    "battery_net_change_mw": "Net battery\nchange (MW)",
    "soc_net_change_mwh": "Net end-SoC\nchange (MWh)",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def correlations(frame, xs, ys, calendar=False):
    ranks = frame[list(dict.fromkeys([*xs, *ys]))].rank(method="average")
    if calendar:
        ranks -= ranks.groupby(frame["calendar_stratum"]).transform("mean")
    return ranks.corr().loc[xs, ys]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--features", type=Path)
    args = parser.parse_args()
    snapshot = args.snapshot or Path(__file__).resolve().parent / "artifacts/final_snapshot"
    feature_path = args.features or Path(__file__).resolve().parent / "artifacts/dc_features/annual_features.json"
    dispatch_path = snapshot / "dispatch/intervals.csv"
    dispatch_meta_path = snapshot / "dispatch/report.json"
    feature_meta_path = feature_path.parent / "report.json"
    dm = json.loads(dispatch_meta_path.read_text())
    fm = json.loads(feature_meta_path.read_text())
    for key in ("outer_sha256", "manifest_sha256", "fixture_hashes"):
        assert dm[key] == fm["provenance"][key], key
    source = json.loads(feature_path.read_text())
    annual = pd.DataFrame(source["features"])
    assert len(annual) == dm["horizon"] == 8760
    annual["timestamp"] = pd.to_datetime(source["timestamps"], utc=True)
    annual["absolute_net_load_ramp_mw"] = annual["net_load_ramp_mw_per_hour"].abs()
    df = pd.read_csv(dispatch_path)
    assert len(df) == dm["completed"] and df.iteration.is_unique
    assert df.iteration.between(0, 8759).all()
    df = df.set_index("iteration").sort_index()
    assert np.array_equal(pd.to_datetime(df.timestamp, utc=True), annual.loc[df.index, "timestamp"])
    for key in FEATURES:
        df[key] = annual.loc[df.index, key]
    df["soc_net_change_mwh"] = df[[f"{s}.delta_soc_end_mwh" for s in dm["storage_ids"]]].sum(axis=1)
    stamp = pd.to_datetime(df.timestamp, utc=True)
    df["calendar_stratum"] = stamp.dt.month.astype(str) + ":" + stamp.dt.hour.astype(str) + ":" + (stamp.dt.dayofweek >= 5).astype(str)
    assert np.isfinite(df[[*FEATURES, *TARGETS]].to_numpy()).all()
    np.testing.assert_allclose(df.generator_counterdirection_mw,
        (df.generator_l1_mw - df.generator_net_change_mw.abs()) / 2, atol=1e-8)
    raw = correlations(df, list(FEATURES), list(TARGETS))
    adjusted = correlations(df, list(FEATURES), list(TARGETS), calendar=True)
    ordinary = df.loc[~df.operator_intervention.astype(bool)]
    sensitivity = correlations(ordinary, list(FEATURES), list(TARGETS), calendar=True)
    # Numerical-noise sensitivity only, not a change to scientific acceptance.
    # MW/MWh to .001; normalized fractions to .0001. Collapse solver-sized ties.
    rounded = df.copy()
    for key in [*FEATURES, *TARGETS]:
        rounded[key] = rounded[key].round(3 if "mw" in key else 4)
    noise_sensitivity = correlations(rounded, list(FEATURES), list(TARGETS), calendar=True)

    # Annual quintile cutpoints, not thresholds chosen to maximize separation.
    bands = []
    for feature in FEATURES:
        cuts = np.unique(np.quantile(annual[feature], [0, .2, .4, .6, .8, 1]))
        if len(cuts) < 2:
            continue
        all_bin = np.searchsorted(cuts[1:-1], annual[feature], side="right")
        for band in range(len(cuts)-1):
            annual_mask = all_bin == band
            part = df.loc[annual_mask[df.index]]
            if not len(part):
                continue
            rec = dict(feature=feature, bin=band, lower=float(cuts[band]),
                upper=float(cuts[band+1]), annual_count=int(annual_mask.sum()), completed_count=len(part))
            for target in list(TARGETS)[:3]:
                rec[target + "_median"] = float(part[target].median())
                rec[target + "_mean"] = float(part[target].mean())
                rec[target + "_p90"] = float(part[target].quantile(.9))
            bands.append(rec)

    dest = ROOT / "outputs/s5_analysis" / ("stress_correlations_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    dest.mkdir(parents=True, exist_ok=False)
    df.to_csv(dest / "joined_intervals.csv")
    raw.to_csv(dest / "spearman.csv")
    adjusted.to_csv(dest / "calendar_adjusted_rank_correlations.csv")
    sensitivity.to_csv(dest / "calendar_adjusted_excluding_interventions.csv")
    noise_sensitivity.to_csv(dest / "calendar_adjusted_rounded.csv")
    pd.DataFrame(bands).to_csv(dest / "annual_quintile_summaries.csv", index=False)
    correlations(df, list(FEATURES), list(FEATURES)).to_csv(dest / "feature_correlations.csv")
    for subset, label in [(df.loc[stamp.dt.month.between(4,8)], "apr_aug"),
                          (df.loc[stamp.dt.hour.between(9,15)], "daytime"),
                          (df.loc[stamp.dt.hour.between(16,21)], "evening")]:
        correlations(subset, list(FEATURES), list(TARGETS), calendar=True).to_csv(dest / f"adjusted_{label}.csv")
    info = dict(snapshot_utc=dm["snapshot_utc"], completed=len(df), fraction=len(df)/8760,
        outer_sha256=dm["outer_sha256"], manifest_sha256=dm["manifest_sha256"],
        source_files={str(p):sha(p) for p in [dispatch_path, dispatch_meta_path, feature_path, feature_meta_path, Path(__file__)]},
        note="Older coverage snapshot contributes fixed full-year DC covariates only; its completed mask is NOT used.",
        calendar_adjustment="Global average ranks, demeaned within month × hour × weekend/weekday strata; Pearson correlation of residual ranks.",
        operator_interventions=int(df.operator_intervention.sum()),
        rounding_sensitivity="Round MW/MWh to 0.001 and normalized fractions to 0.0001 before ranking; not a solver tolerance change.",
        strata_size_range=[int(df.groupby("calendar_stratum").size().min()),int(df.groupby("calendar_stratum").size().max())],
        features={k:dict(minimum=float(df[k].min()),maximum=float(df[k].max()),sd=float(df[k].std()),unique=int(df[k].nunique())) for k in FEATURES},
        limitations=["Associations are exploratory, autocorrelated, univariate, and not causal or validated predictive scores.",
          "Only completed intervals; nonuniform seasonal/shard coverage. No p-values or IID uncertainty claims.",
          "DC utilization is an MW/rating proxy, not AC apparent utilization or a congestion shadow price.",
          "SoC includes inherited deviations; same network stress may have different local states.",
          "Controller-selected differences do not establish minimum AC feasibility correction."])
    (dest / "provenance.json").write_text(json.dumps(info, indent=2, allow_nan=False))
    summary = ["# Preliminary DC stress versus AC adjustments", "",
        f"Snapshot: {dm['snapshot_utc']}; {len(df):,}/8,760 accepted intervals ({len(df)/87.6:.2f}%).",
        "", "Calendar-adjusted rank correlations (month × hour × weekday/weekend).",
        "Rounded-value sensitivity collapses solver-sized differences; associations are descriptive, not causal.", "",
        "| Indicator | Generator opposing MW | Battery L1 MW | SoC L1 MWh |",
        "|---|---:|---:|---:|"]
    for key, label in FEATURES.items():
        if key == "maximum_branch_utilization":
            continue
        summary.append("| " + label + " | " + " | ".join(f"{v:+.3f}" for v in noise_sensitivity.loc[key].iloc[:3]) + " |")
    summary.extend(["", "Maximum branch utilization is 99.9519–100% in this sample: the raw rank coefficient is not a useful physical stress signal.",
        "The 95th-percentile loading and count of branches ≥95% measure the breadth of loading; neither establishes an economic binding constraint.",
        "Signpost power is the largest device-wise absolute DC boundary-energy change divided by window duration and device power rating; it is not headroom from the actual inherited AC state.",
        "Counts/quantiles use the completed subset with full-year DC bin thresholds. The old coverage file's completion mask is ignored.",
        "", *["- " + s for s in info["limitations"]]])
    (dest / "REPORT.md").write_text("\n".join(summary) + "\n")
    fig, axs = plt.subplots(1,2,figsize=(13,8),sharey=True,layout="constrained")
    for ax, table, title in zip(axs,[raw,noise_sensitivity],["Raw Spearman association","Calendar-adjusted · numerical-noise sensitivity"]):
        data=table.iloc[:,:3].to_numpy().copy()
        data[list(FEATURES).index("maximum_branch_utilization")]=np.nan
        im=ax.imshow(data,vmin=-1,vmax=1,cmap="RdBu_r",aspect="auto")
        ax.set_xticks(range(3),list(TARGETS.values())[:3],fontsize=9)
        ax.set_yticks(range(len(FEATURES)),list(FEATURES.values()),fontsize=9)
        ax.set_title(title,fontsize=11)
        for (i,j), v in np.ndenumerate(data):
            ax.text(j,i,"—" if not np.isfinite(v) else f"{v:.2f}",ha="center",va="center",color="white" if abs(v)>.55 else "black",fontsize=9)
    fig.colorbar(im,ax=axs,label="Rank correlation",shrink=.65)
    fig.suptitle(f"DC operating conditions versus AC adjustments · {len(df):,}/8,760 completed\nMaximum-utilization row omitted: saturated near 100%. Descriptive, not causal.",fontsize=12)
    fig.savefig(dest / "stress_correlations.png",dpi=170)
    plt.close(fig)
    print('OUTPUT',dest)
    print('RAW\n',raw.iloc[:,:3].round(3).to_string())
    print('CALENDAR ADJUSTED\n',adjusted.iloc[:,:3].round(3).to_string())
    print('FEATURE RANGE\n',json.dumps(info['features'],indent=2))


if __name__ == "__main__":
    main()
