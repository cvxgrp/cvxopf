"""Propose four input-selected AC qualification periods; never solve an OPF.

Run as a module. Outputs are reviewable selection evidence, not launch authority.
"""

import json
from pathlib import Path

import numpy as np

from .annual_results import HERE, load_results, sha

REGIMES = ("Large surplus", "Large deficit", "Surplus to deficit", "Deficit to surplus")


def select_periods(net, boundaries):
    """Hourly MW inputs; six-hour MWh scores and 17-hour padded eligibility."""
    net = np.asarray(net, dtype=float)
    if net.ndim != 1 or not np.isfinite(net).all():
        raise ValueError("net load must be a finite one-dimensional series")
    if boundaries[0] != 0 or boundaries[-1] != len(net):
        raise ValueError("boundaries must partition the input")
    if any(b <= a for a, b in zip(boundaries[:-1], boundaries[1:])):
        raise ValueError("boundaries must increase strictly")
    candidates = []
    for shard, (left, right) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        for start in range(left, right - 17 + 1):
            values = net[start : start + 6]
            first, last = float(values[:3].mean()), float(values[3:].mean())
            candidates.append(
                dict(
                    start=start,
                    shard=shard,
                    net_energy_mwh=float(values.sum()),
                    first_mean_mw=first,
                    last_mean_mw=last,
                    change_mw=last - first,
                )
            )
    selected = []
    for regime in REGIMES:
        eligible = [
            c
            for c in candidates
            if all(
                c["start"] + 6 <= s["start"] or s["start"] + 6 <= c["start"]
                for s in selected
            )
        ]
        if regime == "Surplus to deficit":
            eligible = [
                c for c in eligible if c["first_mean_mw"] < 0 < c["last_mean_mw"]
            ]
        elif regime == "Deficit to surplus":
            eligible = [
                c for c in eligible if c["first_mean_mw"] > 0 > c["last_mean_mw"]
            ]
        if not eligible:
            raise ValueError(f"no eligible period: {regime}")
        key = "net_energy_mwh" if regime in REGIMES[:2] else "change_mw"
        direction = -1 if regime in ("Large deficit", "Surplus to deficit") else 1
        winner = min(eligible, key=lambda c: (direction * c[key], c["start"]))
        selected.append(dict(regime=regime, eligible_count=len(eligible), **winner))
    return selected


def main():
    import matplotlib.pyplot as plt

    data = load_results()
    manifest_path = HERE / "SHARD_BOUNDARIES.json"
    envelope = json.loads(manifest_path.read_text())
    from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256

    if object_sha256(envelope["manifest"]) != envelope["manifest_sha256"]:
        raise ValueError("shard manifest digest mismatch")
    boundaries = [b["index"] for b in envelope["manifest"]["boundaries"]]
    net = data["inputs"].net_load_mw.to_numpy()
    selected = select_periods(net, boundaries)
    dc = data["runs"]["lossy_dc"]
    soc = dc["boundary_soc_mwh"]
    capacity = data["tables"]["batteries"].capacity_mwh.to_numpy()
    output = HERE / "stage_d_selection"
    output.mkdir(exist_ok=True)
    fig, axes = plt.subplots(4, 2, figsize=(13, 11), constrained_layout=True)
    for row, period in enumerate(selected):
        start = period["start"]
        period["start_fixed_utc_minus_08"] = (
            data["inputs"].index[start].isoformat() + "-08:00"
        )
        period["initial_soc_mwh"] = soc[start].tolist()
        x = np.arange(18)
        ax = axes[row, 0]
        ax.step(
            x,
            np.r_[net[start : start + 17], net[start + 16]] / 1000,
            where="post",
            color="#0072B2",
        )
        ax.axhline(0, color="black", linewidth=0.6)
        ax.set_ylabel("Available net load (GW)")
        ax.set_title(f"{period['regime']} — {period['start_fixed_utc_minus_08']}")
        ax = axes[row, 1]
        fractions = soc[start : start + 18] / capacity
        ax.plot(np.arange(18), fractions, color="#0072B2", alpha=0.15, linewidth=0.8)
        ax.plot(
            np.arange(18),
            soc[start : start + 18].sum(axis=1) / capacity.sum(),
            color="#D55E00",
            label="Fleet capacity-weighted SoC",
            linewidth=2,
        )
        ax.set_ylabel("DC state of charge / capacity")
        ax.set_ylim(-0.02, 1.02)
        for ax in axes[row]:
            ax.axvspan(0, 6, color="#009E73", alpha=0.12)
            ax.axvline(6, color="black", linestyle="--", linewidth=0.8)
            ax.set_xlim(0, 17)
            ax.set_xlabel("Hours from start (shaded: six implemented hours)")
            ax.grid(alpha=0.15)
    axes[0, 1].legend(fontsize=8)
    fig.savefig(output / "periods.png", dpi=160)
    plt.close(fig)
    evidence = dict(
        status="proposed_for_owner_review_not_execution_authority",
        delta_hours=1,
        source_hashes=data["hashes"],
        shard_manifest_sha256=sha(manifest_path),
        selection_source_sha256=sha(Path(__file__)),
        storage_device_ids=data["tables"]["batteries"].device_id.tolist(),
        periods=selected,
    )
    (output / "selection.json").write_text(
        json.dumps(evidence, indent=2, allow_nan=False) + "\n"
    )
    for period in selected:
        print({k: v for k, v in period.items() if k != "initial_soc_mwh"})


if __name__ == "__main__":
    main()
