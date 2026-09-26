"""Compare fresh factorial replay conditions; never launch solves."""

import argparse
from collections import Counter
import itertools
import json
import math
from pathlib import Path

import numpy as np

from experiments.case118_vectorization_replay.analyze import analyze, weighted_quantile
from experiments.case118_vectorization_replay.sample import read, sha
from experiments.retained_paths import retained_operation
from .analyze import winner_result, dispatch_deltas
from .study import CONFIGURATIONS, OUTPUT
from .run import SAMPLE_SHA256


def paired(first, second):
    """Matched fresh observations, preserving original strata and cohort weights."""
    a = {r["iteration"]: r for r in first}
    b = {r["iteration"]: r for r in second}
    if len(a) != len(first) or len(b) != len(second):
        raise ValueError("Duplicate window")
    result = []
    for hour in sorted(a.keys() & b.keys()):
        x, y = a[hour], b[hour]
        for key in ("historical_group", "stratum", "population_weight",
                    "historical_objective", "historical_solve_seconds", "historical_window_seconds"):
            if x[key] != y[key]:
                raise ValueError(f"Mismatched historical identity: {hour}, {key}")
        row = dict(iteration=hour, historical_group=x["historical_group"],
                   population_weight=x["population_weight"], stratum=x["stratum"],
                   first_accepted=x["accepted"], second_accepted=y["accepted"],
                   first_status=x["new_status"], second_status=y["new_status"],
                   first_winner_order=x["new_winner_order"], second_winner_order=y["new_winner_order"])
        for metric in ("solve_seconds", "window_seconds", "build_seconds"):
            v, w = x[f"new_{metric}"], y[f"new_{metric}"]
            if not all(math.isfinite(t) and t > 0 for t in (v, w)):
                raise ValueError("Nonpositive or nonfinite duration")
            row[f"first_{metric}"] = v
            row[f"second_{metric}"] = w
            # Rejected outcomes remain visible, but never become speedup evidence.
            row[f"{metric}_speedup"] = v / w if x["accepted"] and y["accepted"] else None
        row["objective_relative_change"] = (y["new_objective"] - x["new_objective"]) / max(abs(x["new_objective"]), 1e-12)
        result.append(row)
    return result


def summarize(rows):
    primary = [r for r in rows if r["historical_group"] == "primary"]
    accepted = [r for r in primary if r["first_accepted"] and r["second_accepted"]]
    summary = dict(matched=len(rows), primary_matched=len(primary),
                   primary_accepted_pairs=len(accepted),
                   helper_cohort=[r for r in rows if r["historical_group"] == "helper"],
                   rejected_pairs=[r["iteration"] for r in rows
                                   if not (r["first_accepted"] and r["second_accepted"])],
                   changed_winners=[r["iteration"] for r in rows
                                    if r["first_winner_order"] != r["second_winner_order"]],
                   objective_over_0_1_percent=[r["iteration"] for r in rows
                                              if abs(r["objective_relative_change"]) > .001],
                   weighted_primary_means={}, weighted_primary_quantiles={}, geometric_speedups={})
    if accepted:
        weights = [r["population_weight"] for r in accepted]
        for metric in ("solve_seconds", "window_seconds", "build_seconds"):
            for side in ("first", "second"):
                key = f"{side}_{metric}"
                values = [r[key] for r in accepted]
                summary["weighted_primary_means"][key] = float(np.average(values, weights=weights))
                summary["weighted_primary_quantiles"][key] = weighted_quantile(values, weights, [.5, .9, .95, .99])
            summary["geometric_speedups"][metric] = float(np.exp(np.average(
                np.log([r[f"{metric}_speedup"] for r in accepted]), weights=weights)))
    return summary


def attempt_diagnostics(folder, config):
    """Account for attempts even before any window has an accepted winner."""
    attempts = []
    for request_path in (folder / "run").glob("s4b-shard-*/ac-*/request.json"):
        directory = request_path.parent
        request = read(request_path)
        if request["execution_configuration"] != config:
            raise ValueError(f"Attempt configuration differs: {directory}")
        receipt = directory / "execution_configuration.json"
        life_path = directory / "lifecycle.json"
        life = read(life_path) if life_path.exists() else None
        if receipt.exists():
            if read(receipt) != dict(**config, effective_sparse_density_threshold=0.0):
                raise ValueError(f"Effective dispatch differs: {directory}")
        elif (directory / "start.json").exists() or (directory / "result.json").exists():
            raise ValueError(f"Missing effective configuration: {directory}")
        replica = directory / "replica.json"
        if replica.exists() and read(replica)["execution_configuration"] != config:
            raise ValueError(f"Prepared representation differs: {directory}")
        if life:
            for name, reference in life["artifacts"].items():
                if sha(directory / name) != reference["sha256"]:
                    raise ValueError(f"Attempt evidence changed: {directory}, {name}")
        spec = request["invocation"]
        completion = None if life is None else life["completion"]
        attempts.append(dict(iteration=spec["window"]["iteration"], order=spec["order"],
                             outcome=(completion["outcome"] if completion else
                                      life["reason"] if life else "active_or_unreaped"),
                             effective_configuration_available=receipt.exists()))
    return dict(attempts=attempts, attempt_count=len(attempts),
                helper_attempts=sum(a["order"] != 0 for a in attempts),
                attempt_outcomes=dict(Counter(a["outcome"] for a in attempts)))


@retained_operation()
def run(output=OUTPUT):
    output = Path(output)
    binding = read(output / "binding.json")
    if binding["conditions"] != CONFIGURATIONS:
        raise ValueError("Study condition binding changed")
    rows, diagnostics = {}, {}
    for mode, config in CONFIGURATIONS.items():
        folder = output / mode
        receipt = folder / "run/completed.json"
        attempts = attempt_diagnostics(folder, config)
        if not receipt.exists():
            diagnostics[mode] = dict(complete=False, completed=0, **attempts)
            continue
        if sha(folder / "sample.json") != SAMPLE_SHA256:
            raise ValueError("Condition sample differs")
        environment = read(folder / "run/environment.json")
        if environment["execution_configuration"] != config or environment["provenance"] != binding:
            raise ValueError("Condition provenance differs")
        completed = set(read(receipt)["iterations"])
        # The reused historical analyzer needs a nonempty primary cohort.
        primary = {s["iteration"] for s in read(folder / "sample.json")["selected"]
                   if s["historical_group"] == "primary"}
        if not completed & primary:
            diagnostics[mode] = dict(complete=False, completed=len(completed),
                                     **attempts,
                                     analysis_pending="No completed primary window yet; helper results retained.")
            continue
        rows[mode], diagnostics[mode] = analyze(folder, telemetry_folder=folder / "temperature_telemetry")
        diagnostics[mode].update(attempts)
    comparisons = {}
    for first, second in itertools.combinations(CONFIGURATIONS, 2):
        observations = paired(rows.get(first, []), rows.get(second, []))
        for row in observations:
            deltas = dispatch_deltas(winner_result(output / first, row["iteration"]),
                                     winner_result(output / second, row["iteration"]))
            row["max_absolute_dispatch_differences"] = {
                k.removeprefix("time_only_to_combined_max_abs_delta_"): v for k, v in deltas.items()}
        comparisons[f"{first}_to_{second}"] = dict(rows=observations, **summarize(observations))
    common = set.intersection(*[{r["iteration"] for r in rows.get(mode, [])}
                               for mode in CONFIGURATIONS])
    lookup = {m: {r["iteration"]: r for r in rows.get(m, [])} for m in CONFIGURATIONS}
    interaction = []
    for hour in sorted(common):
        if not all(lookup[m][hour]["accepted"] for m in CONFIGURATIONS):
            continue
        row = dict(iteration=hour, historical_group=lookup["none"][hour]["historical_group"],
                   population_weight=lookup["none"][hour]["population_weight"])
        for metric in ("solve_seconds", "window_seconds"):
            times = {m: lookup[m][hour][f"new_{metric}"] for m in CONFIGURATIONS}
            row[metric] = times["none"] * times["both"] / (times["time_only"] * times["spatial_only"])
        interaction.append(row)
    primary_interaction = [r for r in interaction if r["historical_group"] == "primary"]
    geometric_interaction = {metric: float(np.exp(np.average(
        np.log([r[metric] for r in primary_interaction]),
        weights=[r["population_weight"] for r in primary_interaction])))
        for metric in ("solve_seconds", "window_seconds")} if primary_interaction else {}
    summary = dict(complete=(output / "finished.json").exists()
                   and all(d.get("complete", False) for d in diagnostics.values()),
                   expected_windows_per_mode=126, diagnostics=diagnostics,
                   comparisons=comparisons, interaction_rows=interaction,
                   weighted_primary_geometric_interaction=geometric_interaction,
                   interaction_definition="T_none*T_both/(T_time_only*T_spatial_only); 1 means multiplicative independence",
                   binding_sha256=sha(output / "binding.json"))
    (output / "four_way_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = ["# Four fresh 120+6 vectorization replays", "",
             f"Status: {'complete' if summary['complete'] else 'PARTIAL'}.", "",
             "All modes use new packages, sparse P/Q storage, and disabled automatic sparse dispatch.",
             "Solve time includes canonicalization and start persistence. These are single replays with concurrent helpers.",
             "Window latency is primary launch to winner reaping; final loser cleanup is excluded. Build includes historical initializer reconstruction.",
             "Partial summaries describe accepted matched observations only; counts and rejected cases remain explicit.", "",
             "| Comparison | Matched windows | Accepted primary pairs | Geometric solve speedup | Geometric latency speedup |",
             "|---|---:|---:|---:|---:|"]
    for name, result in comparisons.items():
        speedups = result["geometric_speedups"]
        lines.append(f"| {name} | {result['matched']} | {result['primary_accepted_pairs']} | "
                     f"{speedups.get('solve_seconds', float('nan')):.3f} | {speedups.get('window_seconds', float('nan')):.3f} |")
    lines += ["", "Time contrasts: none→time_only and spatial_only→both. Spatial contrasts: none→spatial_only and time_only→both.",
              "Full means, quantiles, six-helper-cohort rows, winner changes, objective flags, dispatch differences,",
              "interaction ratios, and per-condition acceptance/attempt/memory/thermal diagnostics are in four_way_summary.json.",
              "Inspect all objective flags and acceptance evidence before drawing performance conclusions.",
              "Historical timings are context only. Sequential condition order and shared-machine activity limit causal attribution.", ""]
    (output / "REPORT.md").write_text("\n".join(lines))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    run(parser.parse_args().output)
