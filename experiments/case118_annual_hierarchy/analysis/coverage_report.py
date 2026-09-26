"""Descriptive DC operating-condition coverage of checkpointed S5 intervals.

No solver calls, execution changes, advancement decisions, or AC-necessity claims.
Run from the repository root; outputs are fresh, ignored report directories.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import html
import json
from pathlib import Path
import platform
import subprocess
import sys

import numpy as np
import scipy
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
DEFAULT_RUN = ROOT / "experiments/case118_annual_hierarchy/results/s4b_annual_ac"


@dataclass(frozen=True)
class Settings:
    """Exploratory reporting thresholds, not frozen execution/acceptance gates."""

    near_bound_fraction: float = 0.05
    active_power_fraction: float = 0.01
    congestion_fraction: float = 0.95
    minimum_regime_hours: int = 24
    undercoverage_ratio: float = 0.5
    candidate_hours: int = 6
    candidate_count: int = 12


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def finite(value, shape, label):
    array = np.asarray(value, dtype=float)
    if array.shape != shape or not np.isfinite(array).all():
        raise ValueError(f"{label}: expected finite shape {shape}, got {array.shape}")
    return array


def window_stops(horizon, boundaries, width):
    """Half-open windows truncate at their own shard end, not just year end."""
    boundaries = np.asarray(boundaries)
    if (
        boundaries[0] != 0
        or boundaries[-1] != horizon
        or np.any(np.diff(boundaries) <= 0)
    ):
        raise ValueError("invalid shard boundaries")
    t = np.arange(horizon)
    ends = boundaries[np.searchsorted(boundaries, t, side="right")]
    return np.minimum(t + width, ends)


def snapshot_completed(
    run, boundaries, storage_ids, outer_hash, manifest_hash, soc, width
):
    """Snapshot each atomic checkpoint once; verify referenced accepted archives.

    This authenticates membership, identity and targets, NOT a second complete
    physical AC audit. Different shards need not be sampled at the same instant.
    """
    mask = np.zeros(boundaries[-1], dtype=bool)
    provenance, interventions = [], []
    # Read all checkpoints before the potentially longer archive verification.
    snapshots = [
        (p, p.read_bytes()) for p in sorted(run.glob("shard-*/checkpoint.json"))
    ]
    for path, raw in snapshots:
        c = json.loads(raw)
        ordinal = int(path.parent.name.split("-")[-1])
        if not 0 <= ordinal < len(boundaries) - 1:
            raise ValueError("unexpected shard directory")
        start, stop = boundaries[ordinal : ordinal + 2]
        entries = c["windows"]
        coordinates = [e["iteration"] for e in entries]
        if (
            c["interval"] != {"start": start, "stop": stop, "half_open": True}
            or c["storage_device_ids"] != list(storage_ids)
            or c["outer_plan_sha256"] != outer_hash
            or c["manifest_sha256"] != manifest_hash
            or c["completed_intervals"] != len(entries)
            or c["next_global_iteration"] != start + len(entries)
            or len(entries) > stop - start
            or coordinates != list(range(start, start + len(entries)))
        ):
            raise ValueError(f"checkpoint identity/coverage mismatch: {path}")
        for entry in entries:
            target = (path.parent / entry["relative_path"]).resolve()
            if not target.is_relative_to(path.parent.resolve()):
                raise ValueError("archive outside shard")
            compressed = target.read_bytes()
            if len(compressed) != entry["bytes"] or sha(compressed) != entry["sha256"]:
                raise ValueError(f"archive hash/size mismatch: {target}")
            w = json.loads(gzip.decompress(compressed))
            i = entry["iteration"]
            controllers = [a for a in w["attempts"] if a["supplied_executed_action"]]
            expected_stop = min(i + width, stop)
            if (
                w["iteration"] != i
                or w["interval_start"] != i
                or w["interval_stop"] != expected_stop
                or w["storage_device_ids"] != list(storage_ids)
                or w["delta_hours"] != 1.0
                or mask[i]
                or len(controllers) != 1
            ):
                raise ValueError(f"invalid completed-window contract: {i}")
            control = controllers[0]
            if (
                control["slot_state"] != "executed"
                or control["audit"]["accepted_primal"] is not True
                or control["role"] == "target_free"
                or w.get("executed_interval") is None
                or w["executed_interval"]["controlling_attempt_id"]
                != control["attempt_id"]
            ):
                raise ValueError(f"no accepted controlling action: {i}")
            target_soc = finite(
                w["target_soc_mwh"], (len(storage_ids),), "window target"
            )
            if not np.allclose(target_soc, soc[expected_stop], rtol=0, atol=1e-6):
                raise ValueError(f"outer target mismatch: {i}")
            mask[i] = True
            if w.get("operator_intervention") is not None:
                interventions.append(i)
        provenance.append(
            {
                "path": str(path),
                "sha256": sha(raw),
                "completed": len(entries),
                "interval": [start, stop],
                "execution_source_fingerprint": c.get("execution_source_fingerprint"),
            }
        )
    return mask, provenance, interventions


def build_features(
    load_p,
    load_q,
    available,
    flows,
    branch,
    soc,
    power,
    capacities,
    ratings,
    storage_ids,
    nd_ids,
    boundaries,
    width,
    delta,
    settings,
):
    """All features are DC/exogenous covariates, including for AC-completed rows."""
    n, ns = power.shape
    if delta != 1 or np.any(capacities <= 0) or np.any(ratings <= 0):
        raise ValueError("expected positive ratings and hourly frozen fixture")
    stop = window_stops(n, boundaries, width)
    span = stop - np.arange(n)
    active = branch[:, 10] > 0
    rated = active & (branch[:, 5] > 0) & np.isfinite(branch[:, 5])
    if not rated.any():
        raise ValueError("no active positively rated branches")
    utilization = np.abs(flows[:, rated]) / branch[rated, 5]
    load = load_p.sum(axis=1)
    renewable = available.sum(axis=1)
    if np.any(load <= 0):
        raise ValueError("system load must be positive for fractions")
    peak = float(load.max())
    state = soc[:-1] / capacities
    target = soc[stop] / capacities
    pnorm = power / ratings
    required = (soc[:-1] - soc[stop]) / (span[:, None] * delta * ratings)
    features, scales, regimes = {}, {}, {}

    def add(name, values, separation=None):
        features[name] = finite(values, (n,), name)
        if separation is not None:
            scales[name] = float(separation)

    add("system_load_mw", load, 0.1 * peak)
    add(
        "system_reactive_load_mvar",
        load_q.sum(axis=1),
        0.1 * max(1.0, np.max(np.abs(load_q.sum(axis=1)))),
    )
    add("net_load_mw", load - renewable, 0.1 * peak)
    add("net_load_ramp_mw_per_hour", np.r_[0.0, np.diff(load - renewable)] / delta)
    add("maximum_branch_utilization", utilization.max(axis=1), 0.1)
    add("p95_branch_utilization", np.quantile(utilization, 0.95, axis=1))
    add(
        "branches_at_or_above_95pct",
        (utilization >= settings.congestion_fraction).sum(axis=1),
        3.0,
    )
    add("renewable_available_mw", renewable)
    add("renewable_available_fraction_of_load", renewable / load, 0.1)
    for j, name in enumerate(nd_ids):
        add(f"{name}.available_fraction_of_load", available[:, j] / load, 0.1)
    add("aggregate_soc_fraction", soc[:-1].sum(axis=1) / capacities.sum())
    add("aggregate_charging_mw", np.maximum(-power, 0).sum(axis=1))
    add("aggregate_discharging_mw", np.maximum(power, 0).sum(axis=1))
    add("window_steps", span)
    add("maximum_required_average_power_fraction", np.abs(required).max(axis=1))
    add("maximum_signpost_movement_fraction", np.abs(target - state).max(axis=1))
    for j, name in enumerate(storage_ids):
        add(f"{name}.soc_fraction", state[:, j], 0.1)
        add(f"{name}.power_fraction_positive_discharge", pnorm[:, j], 0.1)
        add(f"{name}.target_soc_fraction", target[:, j], 0.1)
        add(f"{name}.required_average_power_fraction", required[:, j], 0.1)
        low, high = (
            state[:, j] <= settings.near_bound_fraction,
            state[:, j] >= 1 - settings.near_bound_fraction,
        )
        tlow, thigh = (
            target[:, j] <= settings.near_bound_fraction,
            target[:, j] >= 1 - settings.near_bound_fraction,
        )
        charge, discharge = (
            pnorm[:, j] < -settings.active_power_fraction,
            pnorm[:, j] > settings.active_power_fraction,
        )
        for label, condition in {
            "near_empty": low,
            "near_full": high,
            "charging": charge,
            "discharging": discharge,
            "idle": ~(charge | discharge),
            "target_near_empty": tlow,
            "target_near_full": thigh,
            "full_to_full": high & thigh,
            "empty_to_empty": low & tlow,
            "near_full_and_charging": high & charge,
            "near_empty_and_discharging": low & discharge,
        }.items():
            regimes[f"{name}.{label}"] = condition
    regimes["simultaneous_charging_and_discharging_across_devices"] = (
        pnorm.min(axis=1) < -settings.active_power_fraction
    ) & (pnorm.max(axis=1) > settings.active_power_fraction)
    regimes["high_loading_and_high_renewables"] = (utilization.max(axis=1) >= 0.95) & (
        renewable / load >= 0.3
    )
    regimes["truncated_shard_end_window"] = span < width
    # Spatial congestion exposure is not hidden by a single system maximum.
    for local, original in enumerate(np.flatnonzero(rated)):
        regimes[
            f"branch_row_{original:03d}_{int(branch[original, 0])}_{int(branch[original, 1])}.loading_ge_95pct"
        ] = utilization[:, local] >= 0.95
    for name in [
        "system_load_mw",
        "net_load_mw",
        "net_load_ramp_mw_per_hour",
        "maximum_branch_utilization",
        "renewable_available_fraction_of_load",
        "maximum_signpost_movement_fraction",
    ]:
        v = features[name]
        if np.ptp(v) > 1e-10:
            regimes[f"{name}.annual_bottom_5pct"] = v <= np.quantile(v, 0.05)
            regimes[f"{name}.annual_top_5pct"] = v >= np.quantile(v, 0.95)
    return features, scales, regimes, stop, rated


def regime_coverage(regimes, completed, settings):
    fraction = float(completed.mean())
    result = []
    for name, condition in regimes.items():
        total = int(np.count_nonzero(condition))
        observed = int(np.count_nonzero(condition & completed))
        ratio = (observed / total / fraction) if total and fraction else None
        flag = (
            ("unseen" if observed == 0 else "underrepresented")
            if total >= settings.minimum_regime_hours
            and (
                observed == 0
                or (ratio is not None and ratio < settings.undercoverage_ratio)
            )
            else "not_flagged"
        )
        result.append(
            {
                "name": name,
                "annual_hours": total,
                "completed_hours": observed,
                "remaining_hours": total - observed,
                "coverage_fraction": observed / total if total else None,
                "relative_to_overall_coverage": ratio,
                "flag": flag,
            }
        )
    return sorted(
        result,
        key=lambda x: (
            x["flag"] == "not_flagged",
            x["completed_hours"] > 0,
            -x["remaining_hours"],
            x["name"],
        ),
    )


def nearest_coverage(features, scales, completed):
    """Nearest completed condition under max absolute difference / declared scale.

    Distance >1 means NO completed row lies within ALL declared tolerances.
    It is an exploratory geometric gap, not a calibrated scientific classifier.
    """
    names = list(scales)
    matrix = np.column_stack([features[k] / scales[k] for k in names])
    if not completed.any():
        return matrix, None, None
    ids = np.flatnonzero(completed)
    distance, which = cKDTree(matrix[completed]).query(matrix, k=1, p=np.inf, workers=1)
    return matrix, distance, ids[which]


def gap_sensitivity(distance, completed):
    """Retain a small descriptive sensitivity check instead of one magic cutoff."""
    return {
        str(multiplier): int(np.sum((~completed) & (distance > multiplier)))
        if distance is not None
        else None
        for multiplier in (0.5, 1.0, 2.0)
    }


def candidate_windows(matrix, distance, nearest, completed, boundaries, settings):
    """Rank unexecuted conditions and greedily avoid near-duplicate candidates."""
    if distance is None:
        return []
    remaining = np.flatnonzero(~completed)
    order = remaining[np.lexsort((remaining, -distance[remaining]))]
    chosen = []
    used = np.zeros(len(completed), dtype=bool)
    representatives = []
    for anchor in order:
        if distance[anchor] <= 1 or used[anchor]:
            continue
        if (
            representatives
            and min(np.max(np.abs(matrix[anchor] - matrix[j])) for j in representatives)
            <= 1
        ):
            continue
        shard = int(np.searchsorted(boundaries, anchor, side="right") - 1)
        end = min(int(anchor) + settings.candidate_hours, boundaries[shard + 1])
        # Stop at first already-completed interval; never silently cross a gap.
        occupied = np.flatnonzero(completed[anchor:end])
        if len(occupied):
            end = int(anchor) + int(occupied[0])
        if end <= anchor or used[anchor:end].any():
            continue
        chosen.append(
            {
                "anchor": int(anchor),
                "start": int(anchor),
                "stop": int(end),
                "hours": int(end - anchor),
                "shard": shard,
                "distance": float(distance[anchor]),
                "nearest_completed_interval": int(nearest[anchor]),
            }
        )
        representatives.append(int(anchor))
        used[anchor:end] = True
        if len(chosen) >= settings.candidate_count:
            break
    return chosen


def feature_summary(features, completed):
    result = {}
    for name, v in features.items():
        edges = np.unique(np.quantile(v, np.linspace(0, 1, 11)))
        if len(edges) == 1:
            edges = np.array([edges[0] - 0.5, edges[0] + 0.5])

        def quantiles(a):
            return np.quantile(a, [0, 0.05, 0.5, 0.95, 1]).tolist() if len(a) else None

        result[name] = {
            "annual_quantiles": quantiles(v),
            "completed_quantiles": quantiles(v[completed]),
            "remaining_quantiles": quantiles(v[~completed]),
            "bin_edges": edges.tolist(),
            "annual_bins": np.histogram(v, edges)[0].tolist(),
            "completed_bins": np.histogram(v[completed], edges)[0].tolist(),
            "remaining_outside_completed_range": int(
                np.count_nonzero(
                    (~completed) & ((v < v[completed].min()) | (v > v[completed].max()))
                )
            )
            if completed.any()
            else int((~completed).sum()),
        }
    return result


def bar_svg(title, annual, observed):
    """Native SVG normalized-bin comparison, with no plotting dependency."""
    a = np.asarray(annual, float)
    b = np.asarray(observed, float)
    a = a / a.sum() if a.sum() else a
    b = b / b.sum() if b.sum() else b
    ymax = max(a.max(), b.max(), 0.01)
    pieces = []
    width = 460 / len(a)
    for i, (x, y) in enumerate(zip(a, b)):
        for offset, value, color in [(0, x, "#a9b2be"), (width * 0.4, y, "#087f8c")]:
            h = 140 * value / ymax
            pieces.append(
                f'<rect x="{25 + i * width + offset:.2f}" y="{180 - h:.2f}" width="{width * 0.36:.2f}" height="{h:.2f}" fill="{color}"/>'
            )
    return f'<svg viewBox="0 0 510 210" role="img" aria-label="{html.escape(title)}"><text x="15" y="22" font-size="13">{html.escape(title)}</text><text x="15" y="43" font-size="11">Top of scale: {100 * ymax:.1f}% of each population</text>{"".join(pieces)}<text x="20" y="202" font-size="11">Annual-defined value bins: low → high</text></svg>'


def report_html(lines):
    """Render this report's small Markdown subset without a browser dependency."""
    parts = []
    in_table = False
    for line in lines:
        if line.startswith("|"):
            if not in_table:
                parts.append("<table>")
                in_table = True
            if set(line.replace("|", "").replace(" ", "").replace(":", "")) <= {"-"}:
                continue
            cells = line.strip("|").split("|")
            parts.append(
                "<tr>"
                + "".join("<td>" + html.escape(c.strip()) + "</td>" for c in cells)
                + "</tr>"
            )
            continue
        if in_table:
            parts.append("</table>")
            in_table = False
        if line.startswith("#"):
            level = min(3, len(line) - len(line.lstrip("#")))
            parts.append(
                f"<h{level}>" + html.escape(line.lstrip("# ")) + f"</h{level}>"
            )
        elif line:
            parts.append("<p>" + html.escape(line) + "</p>")
    if in_table:
        parts.append("</table>")
    return "\n".join(parts)


def write_report(output, report, features, completed, timestamps):
    output.mkdir(parents=True, exist_ok=False)
    (output / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    # Feature rows are compact JSON rather than an extra spreadsheet dependency.
    (output / "annual_features.json").write_text(
        json.dumps(
            {
                "completed_intervals": np.flatnonzero(completed).tolist(),
                "timestamps": timestamps,
                "features": {k: v.tolist() for k, v in features.items()},
            },
            allow_nan=False,
        )
    )
    s = report["summary"]
    flagged = [r for r in report["regimes"] if r["flag"] != "not_flagged"]
    lines = [
        "# S5 operating-condition coverage",
        "",
        f"Snapshot: {report['snapshot_utc']}. **{s['completed']:,}/{s['horizon']:,} intervals ({100 * s['coverage_fraction']:.2f}%)**.",
        "",
        "**Descriptive only. No stop/continue recommendation, AC-necessity conclusion, or execution authorization.**",
        "",
        f"Remaining conditions without a completed analogue within the declared multivariate tolerances: **{s['remaining_without_analogue']} / {s['remaining']}**.",
        f"Frequent flagged regimes: **{len(flagged)}** (flags overlap; hours must not be added).",
        "",
        "## Calendar coverage",
        "",
        "| Synthetic month | Completed / annual hours | Coverage |",
        "|---|---:|---:|",
    ]
    for m in report["calendar"]:
        lines.append(
            f"| {m['month']} | {m['completed']}/{m['annual']} | {100 * m['completed'] / m['annual']:.1f}% |"
        )
    lines += [
        "",
        "## Joint coverage and threshold sensitivity",
        "",
        "Remaining hours lacking any completed analogue. Wider tolerances are less restrictive; these are descriptive distances, not a pass/fail gate.",
        "",
        "| Feature domain | Half-width tolerances | Declared tolerances | Double-width tolerances |",
        "|---|---:|---:|---:|",
    ]
    for name, domain in report["domain_coverage"].items():
        d = domain["remaining_without_analogue"]
        lines.append(f"| {name} | {d['0.5']} | {d['1.0']} | {d['2.0']} |")
    lines += [
        "",
        "## Largest unrepresented / underrepresented regimes",
        "",
        "| Regime | Annual hours | Completed | Remaining | Flag |",
        "|---|---:|---:|---:|---|",
    ]
    for r in flagged[:25]:
        lines.append(
            f"| {r['name']} | {r['annual_hours']} | {r['completed_hours']} | {r['remaining_hours']} | {r['flag']} |"
        )
    lines += [
        "",
        "## Diverse candidate windows — not scheduled",
        "",
        "Scores describe the anchor hour. A candidate is a ≤6-hour, nonwrapping, wholly unexecuted window within one shard. Window end may truncate.",
        "",
        "| Window [start, stop) | Synthetic start | Nearest completed hour | Distance | Largest difference |",
        "|---|---|---:|---:|---|",
    ]
    for c in report["candidates"]:
        top = c["largest_differences"][0]
        lines.append(
            f"| [{c['start']}, {c['stop']}) | {timestamps[c['start']]} | {c['nearest_completed_interval']} | {c['distance']:.2f} | {top['feature']}: {top['candidate']:.3g} vs {top['completed']:.3g} |"
        )
    lines += [
        "",
        "## Interpretation and limits",
        "",
        *["- " + x for x in report["limitations"]],
        "",
        "See `report.json` for every regime, feature distribution, threshold, candidate comparison and source/checkpoint hash. `annual_features.json` retains aligned covariates and membership.",
        "",
    ]
    (output / "REPORT.md").write_text("\n".join(lines))
    charts = "".join(
        bar_svg(k, v["annual_bins"], v["completed_bins"])
        for k, v in report["features"].items()
    )
    body = report_html(lines)
    page = f'<!doctype html><meta charset="utf-8"><title>S5 coverage</title><style>body{{font:15px system-ui;margin:2em;color:#20303c;max-width:1400px}}.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr))}}svg{{width:100%;border:1px solid #ddd}}td{{padding:6px 10px;border-bottom:1px solid #ddd}}table{{border-collapse:collapse;font-size:13px}}tr:first-child{{font-weight:bold;background:#edf4f4}}h2{{margin-top:2em}}</style>{body}<h2>Marginal operating-condition distributions</h2><p>Gray: full-year distribution. Teal: completed subset. Each population is normalized separately; bin edges come only from the full year. Detailed numerical edges are in report.json.</p><div class="charts">{charts}</div>'
    (output / "REPORT.html").write_text(page)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
    from experiments.case118_annual_hierarchy.s4b_manifest import (
        load_authoritative_outer,
        load_verified_manifest,
        S4_OUTER_ARCHIVE_SHA256,
        EXPECTED_MANIFEST_SHA256,
        S4_RESULTS_SHA256,
        S4_SIGNPOST_SHA256,
    )

    settings = Settings()
    run = args.run_dir.resolve()
    reproduction_root = (
        ROOT / "experiments/case118_annual_hierarchy/results/reproductions/s5_coverage"
    )
    output = (
        args.output
        or reproduction_root
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    ).resolve()
    if (
        (output.is_relative_to(ROOT / "experiments")
         and not output.is_relative_to(reproduction_root))
        or run.is_relative_to(output)
        or output.is_relative_to(run)
    ):
        raise ValueError(
            "report output must be separate from execution/scientific artifacts"
        )
    if output.exists():
        raise ValueError("choose a fresh report output directory")
    fixture = load_s4_fixture()
    outer, soc, power = load_authoritative_outer()
    manifest = load_verified_manifest()["manifest"]
    boundaries = manifest["boundary_indices"]
    inputs = fixture.inputs
    n = inputs.horizon_steps
    ids = list(fixture.storage_device_ids)
    result = outer["result"]
    p = finite(inputs.df_load_p.to_numpy(), (n, len(inputs.loads)), "load P")
    q = finite(inputs.df_load_q.to_numpy(), p.shape, "load Q")
    if not np.array_equal(p, np.asarray(result["p_load"])) or not np.array_equal(
        q, np.asarray(result["q_load"])
    ):
        raise ValueError("frozen fixture and retained outer load differ")
    nd_ids = [u.device_id for u in inputs.nondispatchable]
    available = finite(
        inputs.df_nd.loc[:, nd_ids].to_numpy(), (n, len(nd_ids)), "renewables"
    )
    branch = np.asarray(inputs.case["branch"])
    flows = finite(result["p_flows"], (n, len(branch)), "flows in MW")
    capacities = np.array([u.capacity for u in inputs.storage])
    ratings = np.array([u.apparent_power_rating for u in inputs.storage])
    width = fixture.policy.ac_window_steps
    print("Verifying completed checkpoint/archive membership...", flush=True)
    snapshot_utc = datetime.now(timezone.utc).isoformat()
    completed, checkpoints, interventions = snapshot_completed(
        run,
        boundaries,
        ids,
        S4_OUTER_ARCHIVE_SHA256,
        EXPECTED_MANIFEST_SHA256,
        soc,
        width,
    )
    features, scales, regimes, stops, rated = build_features(
        p,
        q,
        available,
        flows,
        branch,
        soc,
        power,
        capacities,
        ratings,
        ids,
        nd_ids,
        boundaries,
        width,
        inputs.delta,
        settings,
    )
    dispatch = finite(result["p_nd"], available.shape, "ND dispatch")
    curtailment = finite(result["curtailment"], available.shape, "ND curtailment")
    if not np.allclose(dispatch + curtailment, available, rtol=0, atol=1e-6):
        raise ValueError("renewable availability/reporting mismatch")
    features["renewable_dispatch_mw"] = dispatch.sum(axis=1)
    features["renewable_curtailment_mw"] = curtailment.sum(axis=1)
    # No generation is available at zero-availability hours; define fraction 0.
    features["renewable_curtailment_fraction"] = np.divide(
        curtailment.sum(axis=1),
        available.sum(axis=1),
        out=np.zeros(n),
        where=available.sum(axis=1) > 1e-8,
    )
    regimes["renewable_curtailment_above_1pct"] = (
        features["renewable_curtailment_fraction"] > 0.01
    )
    matrix, distance, nearest = nearest_coverage(features, scales, completed)
    domain_coverage = {
        "all_selected_features": {
            "features": list(scales),
            "remaining_without_analogue": gap_sensitivity(distance, completed),
        }
    }
    for label, storage_domain in [
        ("network_and_renewables", False),
        ("storage_and_signposts", True),
    ]:
        selected = {
            k: v
            for k, v in scales.items()
            if k.startswith("storage_") == storage_domain
        }
        _, domain_distance, _ = nearest_coverage(features, selected, completed)
        domain_coverage[label] = {
            "features": list(selected),
            "remaining_without_analogue": gap_sensitivity(domain_distance, completed),
        }
    candidates = candidate_windows(
        matrix, distance, nearest, completed, boundaries, settings
    )
    for c in candidates:
        i, j = c["anchor"], c["nearest_completed_interval"]
        differences = [
            {
                "feature": k,
                "candidate": float(features[k][i]),
                "completed": float(features[k][j]),
                "scale": scales[k],
                "normalized_difference": float(
                    abs(features[k][i] - features[k][j]) / scales[k]
                ),
            }
            for k in scales
        ]
        c["largest_differences"] = sorted(
            differences, key=lambda d: -d["normalized_difference"]
        )[:5]
        c["outer_initial_soc_mwh"] = soc[c["start"]].tolist()
        c["outer_end_soc_mwh"] = soc[c["stop"]].tolist()
        c["anchor_ac_window_stop"] = int(stops[i])
        c["anchor_target_soc_mwh"] = soc[stops[i]].tolist()
    index = inputs.df_load_p.index
    calendar = [
        {
            "month": str(month),
            "annual": int(np.sum(index.strftime("%Y-%m") == month)),
            "completed": int(np.sum(completed & (index.strftime("%Y-%m") == month))),
        }
        for month in sorted(set(index.strftime("%Y-%m")))
    ]
    report = {
        "schema_version": 1,
        "snapshot_utc": snapshot_utc,
        "settings": asdict(settings),
        "summary": {
            "horizon": n,
            "completed": int(completed.sum()),
            "remaining": int((~completed).sum()),
            "coverage_fraction": float(completed.mean()),
            "remaining_without_analogue": int(np.sum((~completed) & (distance > 1)))
            if distance is not None
            else None,
        },
        "provenance": {
            "git_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "analysis_script_sha256": sha(Path(__file__).read_bytes()),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "outer_sha256": S4_OUTER_ARCHIVE_SHA256,
            "s4_results_sha256": S4_RESULTS_SHA256,
            "signpost_sha256": S4_SIGNPOST_SHA256,
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "fixture_hashes": dict(fixture.hashes),
            "checkpoints": checkpoints,
            "operator_intervention_intervals": interventions,
        },
        "geometry": {
            "storage_ids": ids,
            "capacities_mwh": capacities.tolist(),
            "ratings_mw": ratings.tolist(),
            "active_rated_branch_rows": np.flatnonzero(rated).tolist(),
            "ac_window_steps": width,
            "shard_boundaries": boundaries,
            "power_sign": "positive discharge, negative charge",
        },
        "novelty_scales": scales,
        "domain_coverage": domain_coverage,
        "features": feature_summary(features, completed),
        "regimes": regime_coverage(regimes, completed, settings),
        "calendar": calendar,
        "candidates": candidates,
        "limitations": [
            "Coverage means a DC/exogenous condition at a checkpointed accepted AC action, not evidence AC was necessary or optimal.",
            "All compared state/power/signpost features use the same authoritative DC trajectory. Actual AC initial states and warm starts may differ.",
            "DC branch utilization is |MW flow| / positive active rateA; this is a planning proxy, not AC apparent-power feasibility or an economic congestion price.",
            "The net-load first-hour ramp is encoded as zero because no prior-year observation exists. No year-end wrapping is used.",
            "Near bounds = within 5% of capacity; charging/discharging = more than 1% of rating. Reporting thresholds are exploratory, not acceptance gates.",
            "Frequent regimes require >=24 annual hours; underrepresentation means regime coverage <half the overall completed fraction. Flags overlap.",
            "Joint distance is max absolute feature difference divided by declared scale (load 10% of annual peak; normalized states/powers/loading 0.1; congested branch count 3). Distance >1 is an analyst-defined gap, not statistical significance.",
            "Candidates use deterministic descending anchor novelty, earliest-index tie breaking, no overlap, and >1 separation from previously chosen anchors. Scores do not predict solve benefit or runtime.",
            "Calendar coverage is reported separately and excluded from novelty distance: an unsampled month can contain familiar conditions, but aggregate features can also miss spatial/temporal novelty.",
            "Marginal coverage and per-device/branch regimes do not prove full joint or sequence coverage; nearest-neighbor features are a chosen summary, not the full optimization state.",
            "Membership verification checks archive hashes, accepted controllers, identities and targets, not every physical residual or continuation authorization. Operator interventions are counted and listed.",
            "No automatic stopping, scheduling, shard reinitialization, policy change, or numerical execution is authorized by this report.",
        ],
    }
    write_report(output, report, features, completed, [str(t) for t in index])
    print(
        json.dumps(
            {
                "output": str(output),
                **report["summary"],
                "candidate_count": len(candidates),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
