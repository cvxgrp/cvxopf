"""Compare combined vectorization with both retained timing sets; no solves."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from experiments.case118_vectorization_replay.analyze import (
    analyze, weighted_quantile, wrapped_angle_delta,
)
from experiments.case118_vectorization_replay.sample import read, ref, sha
from experiments.retained_paths import retained_operation, retained_path
from .run import OUTPUT


def compare(previous, current):
    """Join the same windows and preserve original cohort/weight identities."""
    old = {int(r["iteration"]): r for r in previous}
    if len(old) != len(previous) or len({r["iteration"] for r in current}) != len(current):
        raise ValueError("Duplicate comparison window")
    rows = []
    for new in current:
        iteration = int(new["iteration"])
        prior = old[iteration]
        for name in ("historical_group", "stratum"):
            if str(prior[name]) != str(new[name]):
                raise ValueError(f"Historical window identity differs: {iteration}, {name}")
        for name in ("population_weight", "historical_objective",
                     "historical_solve_seconds", "historical_window_seconds"):
            if not math.isclose(float(prior[name]), float(new[name]), rel_tol=1e-12, abs_tol=1e-9):
                raise ValueError(f"Historical comparison differs: {iteration}, {name}")
        row = dict(iteration=iteration, historical_group=new["historical_group"],
                   stratum=new["stratum"], population_weight=float(new["population_weight"]))
        for metric in ("solve_seconds", "window_seconds", "build_seconds", "objective"):
            row[f"stepwise_{metric}"] = float(new[f"historical_{metric}"])
            row[f"time_only_{metric}"] = float(prior[f"new_{metric}"])
            row[f"combined_{metric}"] = float(new[f"new_{metric}"])
        for metric in ("solve_seconds", "window_seconds"):
            for variant in ("stepwise", "time_only", "combined"):
                value = row[f"{variant}_{metric}"]
                if not math.isfinite(value) or value <= 0:
                    raise ValueError("Timing comparisons require finite positive durations")
            for baseline in ("stepwise", "time_only"):
                row[f"{baseline}_to_combined_{metric}_speedup"] = (
                    row[f"{baseline}_{metric}"] / row[f"combined_{metric}"]
                )
        row["time_only_to_combined_objective_relative_change"] = (
            row["combined_objective"] - row["time_only_objective"]
        ) / max(abs(row["time_only_objective"]), 1e-12)
        row.update(
            time_only_winner_order=int(prior["new_winner_order"]),
            combined_winner_order=int(new["new_winner_order"]),
            combined_accepted=new["accepted"],
            combined_status=new["new_status"],
        )
        rows.append(row)
    return rows


def summarize(rows):
    primary = [r for r in rows if r["historical_group"] == "primary"]
    weights = [r["population_weight"] for r in primary]
    result = dict(primary_completed=len(primary), helper_completed=len(rows) - len(primary),
                  weighted_primary_means={}, weighted_primary_quantiles={},
                  weighted_primary_geometric_speedups={})
    if primary:
        for variant in ("stepwise", "time_only", "combined"):
            for metric in ("solve_seconds", "window_seconds", "build_seconds"):
                key = f"{variant}_{metric}"
                values = [r[key] for r in primary]
                result["weighted_primary_means"][key] = float(np.average(values, weights=weights))
                result["weighted_primary_quantiles"][key] = weighted_quantile(
                    values, weights, [0.5, 0.9, 0.95, 0.99],
                )
        for baseline in ("stepwise", "time_only"):
            for metric in ("solve_seconds", "window_seconds"):
                key = f"{baseline}_to_combined_{metric}_speedup"
                result["weighted_primary_geometric_speedups"][key] = float(np.exp(
                    np.average(np.log([r[key] for r in primary]), weights=weights),
                ))
    result["objective_over_0_1_percent_vs_time_only"] = [
        r["iteration"] for r in rows
        if abs(r["time_only_to_combined_objective_relative_change"]) > 0.001
    ]
    result["changed_winners_vs_time_only"] = [
        r["iteration"] for r in rows if r["time_only_winner_order"] != r["combined_winner_order"]
    ]
    result["helper_cohort"] = [r for r in rows if r["historical_group"] == "helper"]
    return result


def winner_result(output, iteration):
    decision = read(output / "run" / f"winner-{iteration:06d}.json")
    winner = decision["winner"]
    directory = (output / "run" / winner["window"]["shard_id"]
                 / f"ac-{iteration:06d}-spec-{winner['order']:02d}")
    lifecycle = read(directory / "lifecycle.json")
    if sha(directory / "result.json") != lifecycle["artifacts"]["result.json"]["sha256"]:
        raise ValueError("Retained winner result hash changed")
    return read(directory / "result.json")["attempt"]["result"]


def dispatch_deltas(previous, current):
    result = {}
    for key in ("Pg", "Qg", "b", "b_q", "soc", "p_nd", "q_nd", "Vm", "Va_deg"):
        old, new = np.asarray(previous[key]), np.asarray(current[key])
        if old.shape != new.shape or not np.isfinite(old).all() or not np.isfinite(new).all():
            raise ValueError(f"Invalid dispatch comparison: {key}")
        delta = wrapped_angle_delta(new, old) if key == "Va_deg" else new - old
        result[f"time_only_to_combined_max_abs_delta_{key}"] = float(np.max(np.abs(delta)))
    return result


@retained_operation()
def run_analysis():
    binding = read(OUTPUT / "binding.json")
    for reference in binding["previous_replay"].values():
        if sha(reference["path"]) != reference["sha256"]:
            raise ValueError(f"Retained previous replay changed: {reference['path']}")
    if sha(OUTPUT / "sample.json") != binding["sample"]["sha256"]:
        raise ValueError("Replay sample changed since launch")
    previous_path = retained_path(binding["previous_replay"]["comparison.csv"]["path"])
    with open(previous_path, newline="") as stream:
        previous = list(csv.DictReader(stream))
    if {int(r["iteration"]) for r in previous} != {
        s["iteration"] for s in read(OUTPUT / "sample.json")["selected"]
    }:
        raise ValueError("Previous comparison does not contain the frozen sample")
    current, diagnostics = analyze(OUTPUT, telemetry_folder=OUTPUT / "temperature_telemetry")
    rows = compare(previous, current)
    previous_output = Path(previous_path).parent
    for row in rows:
        row.update(dispatch_deltas(winner_result(previous_output, row["iteration"]),
                                   winner_result(OUTPUT, row["iteration"])))
    summary = summarize(rows)
    summary.update(complete=diagnostics["complete"], completed=len(rows), expected=126,
                   binding=binding, diagnostics=ref(OUTPUT / "summary.json"))
    with (OUTPUT / "three_way_comparison.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (OUTPUT / "three_way_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = ["# Case118 AC vectorization timing comparison", "",
             f"Status: {'complete' if summary['complete'] else 'PARTIAL'}; {len(rows)}/126 windows.",
             f"Bound commit: `{binding['commit']}`.", "",
             "Primary-cohort means use the original population weights. Partial results describe only completed windows.",
             "Solve phases include canonicalization, solver work, and start persistence.", "",
             "| Timing set | Mean solve (s) | Mean window (s) | Mean build (s) |",
             "| --- | ---: | ---: | ---: |"]
    means = summary["weighted_primary_means"]
    if means:
        for variant in ("stepwise", "time_only", "combined"):
            values = [means[f"{variant}_{metric}_seconds"] for metric in ("solve", "window", "build")]
            lines.append(f"| {variant} | {values[0]:.3f} | {values[1]:.3f} | {values[2]:.3f} |")
    lines += ["", "## Six historical helper winners", "",
              "| Hour | Stepwise solve (s) | Time-only solve (s) | Combined solve (s) |",
              "| --- | ---: | ---: | ---: |"]
    for row in summary["helper_cohort"]:
        lines.append(f"| {row['iteration']} | {row['stepwise_solve_seconds']:.3f} | "
                     f"{row['time_only_solve_seconds']:.3f} | {row['combined_solve_seconds']:.3f} |")
    lines += ["", "## Checks and limitations", "",
              f"Accepted: {diagnostics['accepted_count']}/{len(rows)}. "
              f"Helper attempts: {diagnostics['helper_attempts']}. "
              f"Attempt outcomes: {diagnostics['attempt_outcomes']}.",
              f"Objective changes above 0.1% versus time-only: {summary['objective_over_0_1_percent_vs_time_only']}.",
              f"Objective changes above 0.1% versus stepwise: {diagnostics['objective_over_0_1_percent']}.",
              "These are paired historical observations. Dependencies and machine conditions also changed; "
              "the results do not isolate a causal effect of spatial vectorization.",
              "The external fan was confirmed on before launch. Telemetry is machine-wide observational data.",
              f"Thermal telemetry: {'available' if diagnostics.get('temperature_telemetry') else 'unavailable or incomplete'}.",
              "Nonconvex solutions and winner identities can change. Inspect flagged objectives and physical audits before drawing conclusions.",
              "Full quantiles, geometric speedups, changed winners, thermal coverage and numerical/memory diagnostics "
              "are in three_way_summary.json and summary.json. No spatial-only condition was run.", ""]
    (OUTPUT / "REPORT.md").write_text("\n".join(lines))
    return rows, summary


if __name__ == "__main__":
    run_analysis()
