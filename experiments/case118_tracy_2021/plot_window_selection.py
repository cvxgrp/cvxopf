"""Static weekly input projections with contained Stage B candidate weeks.

Mirrors the live Tracy explorer's weekly metrics and named-candidate rules.
Reads verified Stage A inputs and applies the declared ordinary-window rule.
No numerical solves.
"""

from pathlib import Path
import json

import numpy as np
import pandas as pd

from .prepare import HERE, digest, write_json

WINDOWS = (
    ("Deficit", "2021-02-01", "2021-03-01", "#0072B2", "o"),
    ("Persistent surplus", "2021-04-01", "2021-06-01", "#E69F00", "s"),
    ("Gross-load peak", "2021-06-03", "2021-06-17", "#009E73", "^"),
    ("Ramp", "2021-10-11", "2021-10-25", "#CC79A7", "D"),
)
# Keep the ordinary-control selection's original four-window exclusions frozen.
APPROVED_WINDOWS = WINDOWS + (
    ("Regional import", "2021-07-12", "2021-08-09", "#332288", "X"),
)
METRICS = ("net_energy_gwh", "peak_net_load_mw", "minimum_net_load_mw")
LABELS = ("Net energy (GWh)", "Peak net load (MW)", "Minimum net load (MW)")


def weekly_metrics(net: pd.Series) -> pd.DataFrame:
    if len(net) != 8760 or not np.isfinite(net).all():
        raise ValueError("expected a finite hourly nonleap year")
    expected = pd.date_range("2021-01-01", periods=8760, freq="h", tz="Etc/GMT+8")
    if not np.array_equal(net.index.asi8, expected.asi8):
        raise ValueError("expected fixed UTC-08:00 2021 calendar")
    blocks = np.lib.stride_tricks.sliding_window_view(net.to_numpy(), 168)[::24]
    start = net.index[: len(net) - 167 : 24]
    return pd.DataFrame(
        dict(
            start=start,
            stop_exclusive=start + pd.Timedelta(hours=168),
            net_energy_gwh=blocks.sum(axis=1) / 1000,
            peak_net_load_mw=blocks.max(axis=1),
            minimum_net_load_mw=blocks.min(axis=1),
        )
    )


def contained(weeks: pd.DataFrame, start: str, stop: str) -> pd.Series:
    first = pd.Timestamp(start, tz="Etc/GMT+8")
    last = pd.Timestamp(stop, tz="Etc/GMT+8")
    return (weeks.start >= first) & (weeks.stop_exclusive <= last)


def candidate_indices(weeks: pd.DataFrame) -> dict:
    result = {}
    for label, metric, quantile in (
        ("Energy low", METRICS[0], 0),
        ("Energy median", METRICS[0], 0.5),
        ("Energy high", METRICS[0], 1),
        ("Peak low", METRICS[1], 0),
        ("Peak median", METRICS[1], 0.5),
        ("Peak high", METRICS[1], 1),
        ("Deepest surplus", METRICS[2], 0),
        ("Shallowest minimum", METRICS[2], 1),
    ):
        result[label] = int(
            (weeks[metric] - weeks[metric].quantile(quantile)).abs().idxmin()
        )
    result["Energy balance"] = int(weeks.net_energy_gwh.abs().idxmin())
    return result


def select_ordinary(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Score all 352 fortnights; exclude event overlaps only at selection."""
    weekly_metrics(frame.net_load_mw)  # Verify the exact hourly calendar.
    if not np.isfinite(frame.load_mw).all():
        raise ValueError("load must be finite")
    rows = []
    for offset in range(0, 8760 - 336 + 1, 24):
        block = frame.iloc[offset : offset + 336]
        start = block.index[0]
        stop = start + pd.Timedelta(hours=336)
        eligible = all(
            not (
                start < pd.Timestamp(b, tz="Etc/GMT+8")
                and stop > pd.Timestamp(a, tz="Etc/GMT+8")
            )
            for _, a, b, _, _ in WINDOWS
        )
        rows.append(
            dict(
                start=start,
                stop_exclusive=stop,
                eligible=eligible,
                mean_load_mw=float(block.load_mw.mean()),
                mean_net_load_mw=float(block.net_load_mw.mean()),
                peak_positive_net_load_mw=max(0.0, float(block.net_load_mw.max())),
                mean_absolute_net_ramp_mw=float(
                    block.net_load_mw.diff().abs().iloc[1:].mean()
                ),
            )
        )
    table = pd.DataFrame(rows)
    names = list(rows[0])[3:]
    features = table[names]
    median = features.median()
    iqr = features.quantile(0.75) - features.quantile(0.25)
    used = iqr > 0
    table["score"] = (
        ((features.loc[:, used] - median[used]) / iqr[used]).pow(2).sum(axis=1)
    )
    eligible = table.loc[table.eligible].sort_values(["score", "start"])
    if eligible.empty:
        raise ValueError("no eligible ordinary fortnight")
    winner = eligible.iloc[0]
    table["selected"] = table.index == winner.name
    return table, dict(
        start=winner.start.isoformat(),
        stop_exclusive=winner.stop_exclusive.isoformat(),
        score=float(winner.score),
        features={n: float(winner[n]) for n in names},
        feature_medians=median.to_dict(),
        feature_iqrs=iqr.to_dict(),
        omitted_zero_iqr_features=iqr.index[~used].tolist(),
        candidates=len(table),
        eligible_candidates=len(eligible),
        rule="all-candidate medians/IQR; exclude half-open event overlaps; minimum squared distance; earliest start breaks ties",
    )


def main() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = HERE / "results/stage_a_active_inputs/aggregate_inputs.csv"
    manifest_path = HERE / "stage_a/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if digest(path) != manifest["raw_artifacts"][path.name]:
        raise ValueError("Stage A aggregate input changed")
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    weeks = weekly_metrics(frame.net_load_mw)
    candidates = candidate_indices(weeks)
    destination = HERE / "stage_b_selection"
    destination.mkdir(exist_ok=True)
    scores, ordinary = select_ordinary(frame)
    scores.to_csv(destination / "ordinary_candidates.csv", index=False)
    write_json(destination / "ordinary_selection.json", ordinary)
    windows = APPROVED_WINDOWS + (
        (
            "Ordinary control",
            ordinary["start"][:10],
            ordinary["stop_exclusive"][:10],
            "#D55E00",
            "P",
        ),
    )
    for name, start, stop, _, _ in windows:
        weeks[name] = contained(weeks, start, stop)
    weeks.to_csv(destination / "weekly_metrics.csv", index=False)
    records = []
    for label, idx in candidates.items():
        r = weeks.loc[idx]
        records.append(
            dict(
                label=label,
                start=r.start.isoformat(),
                stop_exclusive=r.stop_exclusive.isoformat(),
                **{m: float(r[m]) for m in METRICS},
            )
        )
    write_json(destination / "labeled_weeks.json", records)
    outputs = []
    for x, y, filename in (
        (0, 1, "energy_peak.png"),
        (0, 2, "energy_minimum.png"),
        (1, 2, "peak_minimum.png"),
    ):
        fig, ax = plt.subplots(figsize=(12, 7))
        fig.subplots_adjust(left=0.10, right=0.72, bottom=0.20, top=0.89)
        ax.scatter(
            weeks[METRICS[x]],
            weeks[METRICS[y]],
            s=20,
            color="#b4bbc2",
            alpha=0.65,
            label="All 359 daily-start weeks",
            zorder=1,
        )
        for name, start, stop, color, marker in windows:
            selected = weeks.loc[weeks[name]]
            ax.scatter(
                selected[METRICS[x]],
                selected[METRICS[y]],
                s=42,
                marker=marker,
                color=color,
                edgecolor="white",
                linewidth=0.4,
                label=f"{name}: {len(selected)} contained weeks",
                zorder=3,
            )
        # Combine names for the same week; keep labels outside the data cloud.
        grouped = {}
        for label, idx in candidates.items():
            grouped.setdefault(idx, []).append(label)
        ordered = sorted(grouped, key=lambda idx: (weeks.loc[idx, METRICS[y]], idx))
        for idx, ypos in zip(
            ordered, np.linspace(0.03, 0.97, len(ordered)), strict=True
        ):
            row = weeks.loc[idx]
            point = (row[METRICS[x]], row[METRICS[y]])
            ax.scatter(
                *point,
                s=75,
                facecolors="none",
                edgecolors="#222222",
                linewidth=1.1,
                zorder=5,
            )
            ax.annotate(
                " / ".join(grouped[idx])
                + "\n"
                + row.start.strftime("%b %d")
                + "–"
                + (row.stop_exclusive - pd.Timedelta(hours=1)).strftime("%b %d"),
                xy=point,
                xycoords="data",
                xytext=(1.04, ypos),
                textcoords="axes fraction",
                fontsize=8,
                va="center",
                annotation_clip=False,
                arrowprops=dict(arrowstyle="-", color="#777777", linewidth=0.6),
                bbox=dict(facecolor="white", edgecolor="none", pad=1.5),
            )
        ax.set(xlabel=LABELS[x], ylabel=LABELS[y])
        ax.grid(alpha=0.15)
        ax.set_axisbelow(True)
        ax.margins(0.08)
        fig.suptitle(
            f"{LABELS[x].split(' (')[0]} versus {LABELS[y].split(' (')[0].lower()}",
            fontsize=14,
        )
        ax.set_title(
            "Tracy 2021 · Case118 scale · each point is 168 hours", fontsize=10
        )
        handles, labels = ax.get_legend_handles_labels()
        fig.legend(
            handles, labels, loc="lower center", ncol=2, fontsize=9, frameon=False
        )
        fig.savefig(destination / filename, dpi=160)
        plt.close(fig)
        outputs.append(filename)
    write_json(
        destination / "provenance.json",
        dict(
            input_sha256=digest(path),
            stage_a_manifest_sha256=digest(manifest_path),
            script_sha256=digest(Path(__file__)),
            scale_factor=manifest["alpha"],
            semantics="168-hour daily-start windows wholly contained in half-open candidate intervals",
            ordinary_control=ordinary,
            windows=[
                dict(
                    name=n,
                    start=a,
                    stop_exclusive=b,
                    contained_weeks=int(weeks[n].sum()),
                )
                for n, a, b, _, _ in windows
            ],
            artifacts={
                name: digest(destination / name)
                for name in outputs
                + [
                    "weekly_metrics.csv",
                    "labeled_weeks.json",
                    "ordinary_candidates.csv",
                    "ordinary_selection.json",
                ]
            },
        ),
    )


if __name__ == "__main__":
    main()
