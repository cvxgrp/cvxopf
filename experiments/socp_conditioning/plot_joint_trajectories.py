"""Overlay retained aggregate trajectories; no builds or optimization calls."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_arm(directory):
    completion = json.loads((directory / "completion.json").read_text())
    path = directory / "arm.json.gz"
    if sha(path) != completion["arm_sha256"]:
        raise ValueError("arm hash differs from completion")
    with gzip.open(path, "rt") as stream:
        record = json.load(stream)
    if record["audit"]["passed"] is not True:
        raise ValueError("plot requires a physical-audit-passing primal")
    arm = record["historical_reference"]["arm"]
    result = record["result"]
    initial = np.asarray(arm["initial_soc_mwh"], float)
    soc, battery, renewable = [
        np.asarray(result[k], float) for k in ("soc", "b", "p_nd")
    ]
    T = arm["stop"] - arm["start"]
    if soc.shape != (T, len(initial)) or battery.shape != soc.shape:
        raise ValueError("invalid time/device axes")
    if renewable.ndim != 2 or renewable.shape[0] != T:
        raise ValueError("invalid renewable time axis")
    if not all(np.isfinite(v).all() for v in (initial, soc, battery, renewable)):
        raise ValueError("nonfinite trajectory")
    states = np.vstack((initial, soc))
    # This frozen fixture uses hourly intervals and lossless ideal storage.
    np.testing.assert_allclose(np.diff(states, axis=0), -battery, atol=1e-7, rtol=0)
    return dict(
        source=str(path.resolve()),
        sha256=sha(path),
        arm=arm,
        storage_ids=result["storage_device_ids"],
        objective=result["objective"],
        native_status=record["native_solution"]["status"],
        aggregate_soc_mwh=states.sum(axis=1),
        aggregate_battery_mw=battery.sum(axis=1),
        aggregate_renewable_mw=renewable.sum(axis=1),
    )


def plot(left, right, output, left_label, right_label):
    a, b = read_arm(left), read_arm(right)
    if a["arm"] != b["arm"] or a["storage_ids"] != b["storage_ids"]:
        raise ValueError("window/state identities differ")
    T = a["arm"]["stop"] - a["arm"]["start"]
    edges = np.arange(T + 1)
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True, layout="constrained")
    fields = (
        ("aggregate_soc_mwh", "Aggregate stored energy", "MWh"),
        ("aggregate_battery_mw", "Net battery power (+ discharge / − charge)", "MW"),
        ("aggregate_renewable_mw", "Aggregate renewable dispatch", "MW"),
    )
    differences = {}
    for ax, (key, title, unit) in zip(axes, fields):
        for data, label, color, style in (
            (a, left_label, "#0072B2", "-"),
            (b, right_label, "#D55E00", "--"),
        ):
            values = data[key]
            if key == "aggregate_soc_mwh":
                ax.plot(
                    edges,
                    values,
                    color=color,
                    linestyle=style,
                    linewidth=2,
                    marker="o" if style == "--" else None,
                    markersize=3,
                    markerfacecolor="none",
                    label=label,
                )
            else:
                ax.stairs(
                    values,
                    edges,
                    baseline=None,
                    color=color,
                    linestyle=style,
                    linewidth=2,
                    label=label,
                )
        differences[key] = float(np.max(np.abs(a[key] - b[key])))
        ax.set_title(title, loc="left", fontsize=12)
        ax.set_ylabel(unit)
        ax.grid(alpha=0.2)
        ax.ticklabel_format(axis="y", style="plain", useOffset=False)
        ax.text(
            0.99,
            0.96,
            f"Max aggregate difference: {differences[key]:.3f} {unit}",
            ha="right",
            va="top",
            transform=ax.transAxes,
            fontsize=10,
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.85),
        )
    axes[0].legend(loc="lower left", framealpha=0.95)
    axes[1].axhline(0, color="black", linewidth=0.7, alpha=0.6)
    axes[-1].set(
        xlabel="Hours from window start", xlim=(0, T), xticks=np.arange(0, T + 1, 2)
    )
    arm = a["arm"]
    fig.suptitle(
        f"{arm['window']} · hours [{arm['start']}, {arm['stop']})\n"
        "Normalized device cones + joint scaling in both arms",
        fontsize=15,
    )
    fig.savefig(output, dpi=170)
    plt.close(fig)
    payload = dict(
        left=a,
        right=b,
        max_aggregate_differences=differences,
        note="hourly power steps; SoC at boundaries including initial state; aggregate sums may hide device differences",
    )
    with output.with_suffix(".json").open("x") as stream:
        json.dump(
            payload, stream, indent=2, default=lambda x: x.tolist(), allow_nan=False
        )
    print(json.dumps(differences, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists() or args.output.with_suffix(".json").exists():
        raise ValueError("refuse to overwrite prior plot evidence")
    plot(
        args.root / "shedding_scaled",
        args.root / "fixed_scaled",
        args.output,
        "Shedding enabled (actual shedding ≈ 0)",
        "Fixed load (no shedding variables)",
    )
