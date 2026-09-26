"""Recompute matched historical/replay timing and numerical comparisons."""

from __future__ import annotations

from experiments.retained_paths import retained_operation

import csv
from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path

import numpy as np

from .sample import OUT, read, checked, ref


def duration(phases, label):
    events = {e["phase"]: e["monotonic_seconds"] for e in phases["events"]}
    return events[f"after_ac_{label}"] - events[f"before_ac_{label}"]


def weighted_quantile(values, weights, quantiles):
    order = np.argsort(values)
    values, weights = np.asarray(values)[order], np.asarray(weights)[order]
    cumulative = np.cumsum(weights) / np.sum(weights)
    return [
        float(values[min(np.searchsorted(cumulative, q), len(values) - 1)])
        for q in quantiles
    ]


def wrapped_angle_delta(new, old):
    """Shortest signed difference in degrees, invariant to full turns."""
    return (np.asarray(new) - np.asarray(old) + 180) % 360 - 180


def temperature_snapshot(end, *, output=None, folder=None):
    """Freeze complete collector records received no later than solve completion."""
    output = OUT if output is None else output
    folder = (output / "temperature_telemetry" / "20260920T203816Z"
              if folder is None else folder)
    if not (folder / "samples.jsonl").exists():
        return None
    metadata = read(folder / "metadata.json")
    lines = [
        line
        for line in (folder / "samples.jsonl").read_bytes().splitlines(keepends=True)
        if line.endswith(b"\n")
        and datetime.fromisoformat(json.loads(line)["received_utc"]) <= end
    ]
    samples = [json.loads(line) for line in lines]
    if not samples:
        return None
    snapshot = output / "temperature_samples.jsonl"
    snapshot.write_bytes(b"".join(lines))
    return dict(
        metadata=metadata,
        metadata_reference=ref(folder / "metadata.json"),
        snapshot=ref(snapshot),
        sample_count=len(samples),
        first_sample_utc=samples[0]["timestamp"],
        last_sample_utc=samples[-1]["timestamp"],
        cpu_temperature_c_range=[
            min(s["temp"]["cpu_temp_avg"] for s in samples),
            max(s["temp"]["cpu_temp_avg"] for s in samples),
        ],
        performance_core_frequency_mhz_range=[
            min(s["pcpu_freq_mhz"] for s in samples),
            max(s["pcpu_freq_mhz"] for s in samples),
        ],
    )


@retained_operation()
def analyze(output=None, *, telemetry_folder=None):
    output = OUT if output is None else output
    root = output / "run"
    manifest = read(output / "sample.json")
    completed_path = root / "completed.json"
    completed = (
        set(read(completed_path)["iterations"]) if completed_path.exists() else set()
    )
    rows = []
    attempts = []
    for s in manifest["selected"]:
        # A published winner can precede loser reaping. Only the supervisor's
        # completion receipt guarantees that every lifecycle is available.
        if s["iteration"] not in completed:
            continue
        path = root / f"winner-{s['iteration']:06d}.json"
        if not path.exists():
            continue
        decision = read(path)
        spec = decision["winner"]
        directory = (
            root
            / spec["window"]["shard_id"]
            / f"ac-{s['iteration']:06d}-spec-{spec['order']:02d}"
        )
        new = read(directory / "result.json")["attempt"]
        old = checked(s["references"]["winner_result.json"])["attempt"]
        old_phase = checked(s["references"]["winner_phase.json"])
        new_phase = read(directory / "phase.json")
        life = read(directory / "lifecycle.json")
        primary = directory.with_name(f"ac-{s['iteration']:06d}-spec-00")
        first_life = read(primary / "lifecycle.json")
        anchor = first_life["clock_anchor"]

        def utc_at(monotonic):
            return (
                datetime.fromisoformat(anchor["utc"])
                + timedelta(seconds=monotonic - anchor["monotonic_seconds"])
            ).isoformat()

        historical_seconds = duration(old_phase, "solve")
        if not np.isclose(
            historical_seconds, s["historical_solve_seconds"], atol=1e-6, rtol=1e-10
        ):
            raise ValueError("Historical timing table and retained phases disagree")
        new_seconds = duration(new_phase, "solve")
        row = dict(
            iteration=s["iteration"],
            historical_group=s["historical_group"],
            stratum=s["stratum"],
            population_weight=s["population_weight"],
            historical_winner_order=s["historical_winner_order"],
            new_winner_order=spec["order"],
            new_window_launched_utc=utc_at(first_life["launched_monotonic"]),
            new_window_completed_utc=utc_at(life["reaped_monotonic"]),
            historical_solve_seconds=historical_seconds,
            new_solve_seconds=new_seconds,
            winner_solve_speedup=historical_seconds / new_seconds,
            historical_window_seconds=s["historical_window_seconds"],
            new_window_seconds=life["reaped_monotonic"]
            - first_life["launched_monotonic"],
            historical_build_seconds=duration(old_phase, "build"),
            new_build_seconds=duration(new_phase, "build"),
            initialization_preparation_seconds=read(directory / "replica.json")[
                "initialization_preparation_seconds"
            ],
            historical_objective=old["result"]["objective"],
            new_objective=new["result"]["objective"],
            objective_relative_change=(
                new["result"]["objective"] - old["result"]["objective"]
            )
            / max(abs(old["result"]["objective"]), 1e-12),
            accepted=new["audit"]["accepted_primal"],
            new_status=new["audit"]["status"],
        )
        row["window_speedup"] = (
            row["historical_window_seconds"] / row["new_window_seconds"]
        )
        for label, attempt in (("historical", old), ("new", new)):
            signature = attempt["structural_signature"]
            row[f"{label}_variable_objects"] = len(signature["variables"])
            row[f"{label}_constraint_objects"] = len(signature["constraints"])
        old_start = checked(s["references"]["primary_start.json"])
        new_start = read(primary / "start.json") if (primary / "start.json").exists() else None

        def auxiliary(start):
            x0 = np.asarray(start["complete_x0"])
            return np.sort(
                np.concatenate(
                    [
                        x0[v["start"] : v["stop"]]
                        for v in start["layout"]
                        if not v["is_original_variable"]
                    ]
                )
            )

        row["primary_auxiliary_values_match"] = None if new_start is None else np.array_equal(
            auxiliary(old_start), auxiliary(new_start)
        )
        row["historical_model_coordinates"] = old_start["model_coordinate_count"]
        row["new_model_coordinates"] = None if new_start is None else new_start["model_coordinate_count"]
        for key in ("Pg", "Qg", "b", "b_q", "soc", "p_nd", "q_nd", "Vm", "Va_deg"):
            label = f"{key}_raw" if key == "Va_deg" else key
            row[f"max_abs_delta_{label}"] = float(
                np.max(
                    np.abs(
                        np.asarray(new["result"][key]) - np.asarray(old["result"][key])
                    )
                )
            )
        row["max_abs_delta_Va_deg_wrapped"] = float(
            np.max(
                np.abs(
                    wrapped_angle_delta(
                        new["result"]["Va_deg"], old["result"]["Va_deg"]
                    )
                )
            )
        )
        for key, value in new["audit"]["residuals"].items():
            row[f"residual_{key}"] = value
        rows.append(row)
    for path in root.glob("s4b-shard-*/ac-*/lifecycle.json"):
        life = read(path)
        attempts.append(
            dict(
                iteration=life["invocation"]["window"]["iteration"],
                order=life["invocation"]["order"],
                reason=life["reason"],
                outcome=None
                if life["completion"] is None
                else life["completion"]["outcome"],
            )
        )
    if not rows:
        raise ValueError("No completed replay windows")
    with (output / "comparison.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    primary = [r for r in rows if r["historical_group"] == "primary"]
    weights = [r["population_weight"] for r in primary]
    summary = dict(
        complete=len(rows) == len(manifest["selected"])
        and (root / "finished.json").exists(),
        completed=len(rows),
        expected=len(manifest["selected"]),
        attempts=attempts,
        primary_completed=len(primary),
        helper_completed=len(rows) - len(primary),
        new_helper_winners=sum(r["new_winner_order"] != 0 for r in rows),
        helper_attempts=sum(a["order"] != 0 for a in attempts),
        attempt_outcomes=dict(Counter(a["outcome"] or a["reason"] for a in attempts)),
        objective_over_0_1_percent=[
            r["iteration"] for r in rows if abs(r["objective_relative_change"]) > 0.001
        ],
        max_abs_objective_relative_change=max(
            abs(r["objective_relative_change"]) for r in rows
        ),
        weighted_primary_quantiles={
            key: weighted_quantile(
                [r[key] for r in primary], weights, [0.5, 0.9, 0.95, 0.99]
            )
            for key in (
                "historical_solve_seconds",
                "new_solve_seconds",
                "winner_solve_speedup",
                "historical_window_seconds",
                "new_window_seconds",
                "window_speedup",
            )
        },
        weighted_primary_means={
            key: float(np.average([r[key] for r in primary], weights=weights))
            for key in (
                "historical_solve_seconds",
                "new_solve_seconds",
                "historical_window_seconds",
                "new_window_seconds",
            )
        },
        quantile_probabilities=[0.5, 0.9, 0.95, 0.99],
        weighted_primary_geometric_mean_window_speedup=float(
            np.exp(
                np.average(
                    np.log([r["window_speedup"] for r in primary]), weights=weights
                )
            )
        ),
        weighted_primary_geometric_mean_solve_speedup=float(
            np.exp(
                np.average(
                    np.log([r["winner_solve_speedup"] for r in primary]),
                    weights=weights,
                )
            )
        ),
        primary_slower_count=sum(r["winner_solve_speedup"] < 1 for r in primary),
        accepted_count=sum(r["accepted"] for r in rows),
        primary_auxiliary_mismatch_hours=[
            r["iteration"] for r in rows if r["primary_auxiliary_values_match"] is False
        ],
        primary_start_unavailable_hours=[
            r["iteration"] for r in rows if r["primary_auxiliary_values_match"] is None
        ],
        evidence=dict(
            sample=ref(output / "sample.json"),
            environment=ref(root / "environment.json"),
            analysis=ref(Path(__file__)),
            comparison=ref(output / "comparison.csv"),
        ),
        sampling={
            key: manifest[key]
            for key in (
                "seed",
                "population_counts",
                "primary_strata",
                "helper_strata",
                "sampling",
            )
        },
        software_versions=read(root / "environment.json")["software_versions"],
    )
    progress = read(root / "supervisor-progress.json")
    notes_path = root / "environment-notes.json"
    summary["environment_notes"] = read(notes_path) if notes_path.exists() else []
    if notes_path.exists():
        summary["evidence"]["environment_notes"] = ref(notes_path)
    summary["peak_sampled_aggregate_rss_mib"] = max(
        s["aggregate_mib"] for s in progress["memory_samples"]
    )
    summary["peak_sampled_worker_rss_mib"] = max(
        v for s in progress["memory_samples"] for v in s["worker_trees_mib"]
    )
    if (root / "finished.json").exists():
        begin = datetime.fromisoformat(read(root / "environment.json")["started_utc"])
        end = datetime.fromisoformat(read(root / "finished.json")["finished_utc"])
        summary["experiment_wall_seconds"] = (end - begin).total_seconds()
        summary["windows_per_minute"] = (
            len(rows) * 60 / summary["experiment_wall_seconds"]
        )
        summary["temperature_telemetry"] = temperature_snapshot(
            end, output=output, folder=telemetry_folder,
        )
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "attempts"}, indent=2))
    return rows, summary


def plot(rows, destination):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), layout="constrained")
    for group, color, marker in (
        ("primary", "#176a88", "o"),
        ("helper", "#e07a24", "D"),
    ):
        data = [r for r in rows if r["historical_group"] == group]
        label = f"Historical {group} winners (n={len(data)})"
        axes[0].scatter(
            [r["historical_solve_seconds"] for r in data],
            [r["new_solve_seconds"] for r in data],
            s=25,
            alpha=0.75,
            color=color,
            marker=marker,
            label=label,
        )
        axes[1].scatter(
            [r["historical_window_seconds"] for r in data],
            [r["new_window_seconds"] for r in data],
            s=25,
            alpha=0.75,
            color=color,
            marker=marker,
        )
        axes[2].scatter(
            [r["historical_solve_seconds"] for r in data],
            [100 * r["objective_relative_change"] for r in data],
            s=25,
            alpha=0.75,
            color=color,
            marker=marker,
        )
    for ax, field in zip(axes[:2], ("solve", "window")):
        low = (
            min(
                min(r[f"historical_{field}_seconds"], r[f"new_{field}_seconds"])
                for r in rows
            )
            * 0.8
        )
        high = (
            max(
                max(r[f"historical_{field}_seconds"], r[f"new_{field}_seconds"])
                for r in rows
            )
            * 1.25
        )
        ax.plot([low, high], [low, high], color=".4", linestyle="--", linewidth=1)
        ax.set(
            xscale="log",
            yscale="log",
            xlim=(low, high),
            ylim=(low, high),
            xlabel="Historical stepwise (seconds)",
            ylabel="Vectorized replay (seconds)",
        )
        ax.grid(alpha=0.2)
    axes[0].set_title("Winner solve phase: canonicalization + solver")
    axes[1].set_title("Window latency: launch through winner reaping")
    axes[2].set(
        xscale="log",
        xlabel="Historical winner solve phase (seconds)",
        ylabel="Objective change (%)",
        title="Numerical agreement",
    )
    axes[2].axhline(0, color=".4", linewidth=1)
    for value in (-0.1, 0.1):
        axes[2].axhline(value, color="#b34545", linestyle="--", linewidth=1)
    axes[2].grid(alpha=0.2)
    for ax in axes:
        ax.xaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5)))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda x, pos: f"{x:g}"))
        ax.xaxis.set_minor_formatter(NullFormatter())
    for ax in axes[:2]:
        ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5)))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, pos: f"{x:g}"))
        ax.yaxis.set_minor_formatter(NullFormatter())
    axes[0].legend(fontsize=8, loc="upper left")
    fig.suptitle(
        "Case118 · independent three-hour AC replays · identical historical problem and primary start",
        fontsize=13,
    )
    fig.savefig(destination, dpi=170)
    plt.close(fig)


def report(rows, summary, destination):
    primary = [r for r in rows if r["historical_group"] == "primary"]
    helper = [r for r in rows if r["historical_group"] == "helper"]
    q = summary["weighted_primary_quantiles"]
    slowest = max(primary, key=lambda r: r["historical_solve_seconds"])
    worst = min(primary, key=lambda r: r["window_speedup"])
    thermal = summary.get("temperature_telemetry")
    thermal_note = "No temperature telemetry is available in this analysis."
    if thermal is not None:
        low, high = thermal["cpu_temperature_c_range"]
        thermal_note = (
            f"No temperatures were measured at the fan intervention. Later owner-authorized macmon telemetry began at {thermal['metadata']['started_utc']}; "
            f"{thermal['sample_count']} complete samples through solve completion are retained with the artifacts. "
            f"The reported average CPU temperature ranged from {low:.1f} to {high:.1f} °C. "
            "This is machine-wide observational data with no pre-fan baseline, and it does not establish a causal fan effect or diagnose throttling."
        )
    lines = [
        "# Independent Case118 three-hour vectorization replay",
        "",
        f"All {len(rows)} sampled windows completed; {summary['accepted_count']} passed the original independent physical audit. "
        f"The experiment took {summary['experiment_wall_seconds'] / 60:.2f} minutes with the original two-primary/one-helper supervisor "
        f"({summary['windows_per_minute']:.2f} completed windows/minute). "
        f"There were {len(summary['attempts'])} attempts, including {summary['helper_attempts']} helper attempts, "
        f"and {summary['new_helper_winners']} helper wins. Attempt outcomes: {summary['attempt_outcomes']}.",
        "Whole-batch throughput describes this deliberately tail/helper-enriched sample, not predicted annual-study throughput.",
        "",
        "The sample contains 120 historical primary winners and six historical helper winners from the completed toy Case118 study. "
        "Every replay preserves the historical problem, storage state and target, costs, primary initialization, solver options, and numerical library versions. "
        "Only the time assembly changes; there is no new state passed between sampled windows. The original experimental recovery ladder remains enabled.",
        "",
        "## Timing",
        "",
        "The table estimates the distribution of the 5,154 eligible historical primary-win windows, weighting each sampled window by its stratum population/sample size. "
        "Percentiles use the inverse weighted empirical CDF; they are point estimates from this stratified sample, without uncertainty intervals. "
        "Solve phase means canonicalization plus solver and retained-start persistence. These historical records do not separate canonicalization from native IPOPT time.",
        "",
        "| Weighted statistic | Historical solve phase (s) | Replay winner solve phase (s) | Historical window latency (s) | Replay window latency (s) |",
        "|---|---:|---:|---:|---:|",
    ]
    means = summary["weighted_primary_means"]
    lines.append(
        f"| Arithmetic mean | {means['historical_solve_seconds']:.2f} | {means['new_solve_seconds']:.2f} | {means['historical_window_seconds']:.2f} | {means['new_window_seconds']:.2f} |"
    )
    for i, label in enumerate(("50th", "90th", "95th", "99th")):
        lines.append(
            f"| {label} | {q['historical_solve_seconds'][i]:.2f} | {q['new_solve_seconds'][i]:.2f} | {q['historical_window_seconds'][i]:.2f} | {q['new_window_seconds'][i]:.2f} |"
        )
    lines.extend(
        [
            "",
            f"The weighted geometric mean of paired winner-solve speedups is **{summary['weighted_primary_geometric_mean_solve_speedup']:.2f}×**; "
            f"the corresponding window-latency speedup is **{summary['weighted_primary_geometric_mean_window_speedup']:.2f}×**. "
            f"{summary['primary_slower_count']} of the 120 sampled historical primary winners had a slower replay winner solve phase. "
            "These speedups are paired ratios, not ratios of marginal percentiles.",
            "",
            f"The slowest sampled historical primary (hour {slowest['iteration']}) took {slowest['historical_solve_seconds']:.2f} s originally and "
            f"{slowest['new_solve_seconds']:.2f} s in the replay winner ({slowest['winner_solve_speedup']:.2f}×). "
            f"Its new winner order was {slowest['new_winner_order']} (zero denotes primary).",
            "",
            f"The largest window-latency slowdown was hour {worst['iteration']}: "
            f"{worst['historical_window_seconds']:.2f} s historically versus {worst['new_window_seconds']:.2f} s on replay "
            f"({1 / worst['window_speedup']:.2f}× longer). Improvements are not uniform across initial-value problems.",
            "",
            "Window latency runs from primary process launch to winner reaping, excluding final loser cleanup. "
            "Winner solve phase excludes waiting and other contenders, so it must not substitute for window latency when comparing changed race winners. "
            "Randomized pairing changes contention for the shared helper; historical machine conditions also differ. "
            "This is a matched historical comparison, not a randomized causal estimate of vectorization alone.",
            "",
            *[
                f"Cooling observation recorded at {note['reported_utc']}: {note['owner_observation']} "
                f"{note['switch_time']}"
                for note in summary["environment_notes"]
            ],
            thermal_note,
            "",
            "## Six historical helper winners",
            "",
            "| Hour | Old/new winner order | Historical/replay winner solve (s) | Historical/replay window latency (s) | Latency speedup |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for r in sorted(helper, key=lambda r: r["historical_window_seconds"]):
        lines.append(
            f"| {r['iteration']} | {r['historical_winner_order']} / {r['new_winner_order']} | {r['historical_solve_seconds']:.2f} / {r['new_solve_seconds']:.2f} | {r['historical_window_seconds']:.2f} / {r['new_window_seconds']:.2f} | {r['window_speedup']:.2f}× |"
        )
    raced = {a["iteration"] for a in summary["attempts"] if a["order"] != 0}
    if raced:
        lines.extend(
            [
                "",
                "## Races triggered during replay",
                "",
                "| Hour | Historical/replay winner solve (s) | Historical/replay window latency (s) | New winner order | Helper outcomes |",
                "|---|---:|---:|---:|---|",
            ]
        )
        for r in sorted(
            (r for r in rows if r["iteration"] in raced), key=lambda r: r["iteration"]
        ):
            outcomes = [
                a["outcome"] or a["reason"]
                for a in sorted(summary["attempts"], key=lambda a: a["order"])
                if a["iteration"] == r["iteration"] and a["order"] != 0
            ]
            lines.append(
                f"| {r['iteration']} | {r['historical_solve_seconds']:.2f} / {r['new_solve_seconds']:.2f} | {r['historical_window_seconds']:.2f} / {r['new_window_seconds']:.2f} | {r['new_winner_order']} | {', '.join(outcomes)} |"
            )
        lines.extend(
            [
                "",
                "`solve_budget` denotes a bounded helper timeout; `lost_race` denotes cancellation after another contender won. Neither supplies a numerical solution.",
            ]
        )
    lines.extend(
        [
            "",
            "## Numerical and structural checks",
            "",
            f"Maximum absolute relative objective change: **{100 * summary['max_abs_objective_relative_change']:.6g}%**. "
            f"Windows exceeding the 0.1% inspection threshold: {summary['objective_over_0_1_percent'] or 'none'}. "
            "Every accepted result is checked by the original build-free network, device, and terminal-SoC audit. "
            "Different trajectories are permissible in this nonconvex problem; reactive dispatch remains unregularized.",
            "Close objectives do not imply equal trajectories. Maximum differences across the sampled solutions: "
            f"Pg {max(r['max_abs_delta_Pg'] for r in rows):.3f} MW; battery power {max(r['max_abs_delta_b'] for r in rows):.3f} MW; "
            f"SoC {max(r['max_abs_delta_soc'] for r in rows):.3f} MWh; Qg {max(r['max_abs_delta_Qg'] for r in rows):.3f} MVAr. "
            "The CSV distinguishes raw bus-angle differences from differences wrapped modulo 360 degrees; whole-turn offsets are physically equivalent. "
            f"The maximum wrapped difference is {max(r['max_abs_delta_Va_deg_wrapped'] for r in rows):.6g} degrees.",
            "",
            f"Historical primary starts are regenerated and checked exactly before each solve. "
            f"Auxiliary-value multiset mismatches after canonicalization: {summary['primary_auxiliary_mismatch_hours'] or 'none'}. "
            "The vectorized model adds four fixed initial-SoC coordinates, and canonical coordinate ordering changes. "
            "Matching auxiliary multisets supplement the exact named-variable check; they are not a coordinate-by-coordinate native-vector identity claim.",
            "",
            f"Variable-object counts: {sorted(set(r['historical_variable_objects'] for r in rows))} historical, "
            f"{sorted(set(r['new_variable_objects'] for r in rows))} vectorized. "
            f"Constraint-object counts: {sorted(set(r['historical_constraint_objects'] for r in rows))} historical, "
            f"{sorted(set(r['new_constraint_objects'] for r in rows))} vectorized. "
            "These count CVXPY objects, not scalar mathematical constraints.",
            "",
            f"Median replay build phase: {np.median([r['new_build_seconds'] for r in rows]):.3f} s, "
            f"including {np.median([r['initialization_preparation_seconds'] for r in rows]):.3f} s median historical-initializer reconstruction overhead. "
            f"Median historical winner build phase: {np.median([r['historical_build_seconds'] for r in rows]):.3f} s. "
            "These are unweighted sample medians. Reconstruction deliberately builds the original initialization graph before mapping; that overhead is not charged to the solve phase.",
            "",
            f"Maximum sampled RSS: {summary['peak_sampled_worker_rss_mib'] / 1024:.2f} GiB per worker tree; "
            f"{summary['peak_sampled_aggregate_rss_mib'] / 1024:.2f} GiB aggregate including supervisor. "
            "These are sampled maxima, not exact process peaks; no matched historical memory-speedup claim is made.",
            "",
            "![Paired timing and objective comparison](artifacts/comparison.png)",
            "",
            "The frozen selection, row-level comparison, and summary are in `artifacts/`. "
            "Raw phases, starts, results, lifecycle records, and source hashes are retained under "
            "`experiments/case118_vectorization_replay/results/case118_vectorization_replay/`. Recompute with `python -m experiments.case118_vectorization_replay.analyze` "
            "in the project environment. The sample seed is 20260920; see README.md for population definitions, allocation, and execution commands.",
            "",
        ]
    )
    destination.write_text("\n".join(lines))


if __name__ == "__main__":
    rows, summary = analyze()
    if summary["complete"]:
        destination = Path(__file__).parent / "artifacts"
        destination.mkdir(exist_ok=True)
        (destination / "comparison.csv").write_bytes(
            (OUT / "comparison.csv").read_bytes()
        )
        (destination / "summary.json").write_bytes((OUT / "summary.json").read_bytes())
        (destination / "sample.json").write_bytes((OUT / "sample.json").read_bytes())
        (destination / "environment.json").write_bytes(
            (OUT / "run" / "environment.json").read_bytes()
        )
        if summary.get("temperature_telemetry") is not None:
            (destination / "temperature_samples.jsonl").write_bytes(
                (OUT / "temperature_samples.jsonl").read_bytes()
            )
        plot(rows, destination / "comparison.png")
        report(rows, summary, Path(__file__).parent / "REPORT.md")
