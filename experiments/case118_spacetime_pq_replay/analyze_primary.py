"""Reconstruct the four-way diagnostic report without running any solver."""

import argparse
from pathlib import Path
from datetime import datetime
import hashlib, itertools, json, re, statistics, subprocess
from experiments.retained_paths import retained_operation, retained_path

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent


@retained_operation()
def analyze(write=False):
    root = ROOT
    out = EXPERIMENT / "results/case118_6047_four_way"

    def read(p):
        return json.loads(retained_path(p).read_text())

    finished = read(out / "finished.json")
    thermal_end = read(out / "temperature_telemetry/finished.json")
    binding = read(out / "binding.json")
    receipt = read(out.with_name(out.name + "-launch.json"))
    assert not finished["prepare_only"]
    for path, digest in binding["execution_sources"].items():
        current = retained_path(path)
        if (
            current.is_file()
            and hashlib.sha256(current.read_bytes()).hexdigest() == digest
        ):
            continue
        relative = Path(path).relative_to(root)
        recorded = subprocess.check_output(
            ["git", "show", f"{binding['commit']}:{relative}"], cwd=root
        )
        assert hashlib.sha256(recorded).hexdigest() == digest, path
    rows = []
    attempts = {}
    for name in binding["modes"]:
        d = out / name
        result = read(d / "result.json")
        attempt = result["attempt"]
        attempts[name] = attempt
        completion = read(d / "completion.json")
        preparation = read(d / "prepared.json")
        log = (d / "worker.log").read_text()
        events = {
            e["phase"]: e["monotonic_seconds"] for e in read(d / "phase.json")["events"]
        }
        start_ref = result["start_artifact"]
        assert (
            hashlib.sha256((d / start_ref["relative_path"]).read_bytes()).hexdigest()
            == start_ref["sha256"]
        )
        assert completion["returncode"] == 0 and completion["execution_error"] is None
        native = {}
        for label in [
            "Objective",
            "Dual infeasibility",
            "Constraint violation",
            "Variable bound violation",
            "Complementarity",
            "Overall NLP error",
        ]:
            m = re.search(re.escape(label) + r"\.*:\s*([\deE+.-]+)\s+([\deE+.-]+)", log)
            assert m, label
            native[label] = {"scaled": float(m[1]), "unscaled": float(m[2])}
        row = dict(
            condition=name,
            status=attempt["audit"]["status"],
            accepted=attempt["audit"]["accepted_primal"],
            iterations=int(re.search(r"Number of Iterations\.+:\s*(\d+)", log)[1]),
            native_seconds=float(
                re.search(r"Total seconds in IPOPT\s*=\s*([\d.]+)", log)[1]
            ),
            solve_phase_seconds=events["after_ac_solve"] - events["before_ac_solve"],
            preparation_seconds=events["after_ac_build"] - events["before_ac_build"],
            worker_wall_seconds=completion["wall_seconds"],
            termination=re.search(r"EXIT: (.*)", log)[1],
            physical_residuals=attempt["audit"]["residuals"],
            native_metrics=native,
            extracted_objective=attempt["result"]["objective"],
            preparation=preparation,
        )
        rows.append(row)
    wall = (
        datetime.fromisoformat(thermal_end["finished_utc"])
        - datetime.fromisoformat(receipt["started_utc"])
    ).total_seconds()
    times = [r["solve_phase_seconds"] for r in rows]
    samples = [
        json.loads(l)
        for l in (out / "temperature_telemetry/samples.jsonl").read_text().splitlines()
    ]
    temps = [s["temp"]["cpu_temp_avg"] for s in samples]
    pairs = []
    for a, b in itertools.combinations(rows, 2):
        pairs.append(
            dict(
                first=a["condition"],
                second=b["condition"],
                second_over_first_solve_time=b["solve_phase_seconds"]
                / a["solve_phase_seconds"],
                second_minus_first_seconds=b["solve_phase_seconds"]
                - a["solve_phase_seconds"],
            )
        )
    starts = []
    for a, b in [("none", "spatial_only"), ("time_only", "both")]:
        starts.append(
            dict(
                first=a,
                second=b,
                full_canonical_x0_exact=attempts[a]["solver_x0"]
                == attempts[b]["solver_x0"],
                normalized_layout_exact=attempts[a]["solver_evidence"][
                    "layout_signature"
                ]
                == attempts[b]["solver_evidence"]["layout_signature"],
            )
        )
    historical = []
    for a, b in [("spatial_only", "stepwise"), ("both", "vectorized")]:
        old = read(
            EXPERIMENT
            / f"results/case118_6047_primary_diagnostic_retry/{b}/result.json"
        )["attempt"]
        historical.append(
            dict(
                current=a,
                previous=b,
                extracted_result_exact=attempts[a]["result"] == old["result"],
                physical_residuals_exact=attempts[a]["audit"]["residuals"]
                == old["audit"]["residuals"],
                full_canonical_x0_exact=attempts[a]["solver_x0"] == old["solver_x0"],
                normalized_layout_exact=attempts[a]["solver_evidence"][
                    "layout_signature"
                ]
                == old["solver_evidence"]["layout_signature"],
            )
        )
    summary = dict(
        commit=binding["commit"],
        conditions=rows,
        total_wall_seconds=wall,
        total_solve_phase_seconds=sum(times),
        mean_solve_seconds=statistics.mean(times),
        median_solve_seconds=statistics.median(times),
        completed_attempts_per_hour=4 * 3600 / wall,
        accepted_attempts_per_hour=sum(r["accepted"] for r in rows) * 3600 / wall,
        accepted=sum(r["accepted"] for r in rows),
        pairwise=pairs,
        spatial_pair_start_comparison=starts,
        previous_diagnostic_comparison=historical,
        thermal=dict(
            samples=len(samples),
            errors=thermal_end["errors"],
            cpu_min_c=min(temps),
            cpu_median_c=statistics.median(temps),
            cpu_max_c=max(temps),
        ),
        verification="Recorded execution source hashes verified from unchanged files or the bound Git commit; retained start artifact hashes verified.",
    )
    if write:
        destination = EXPERIMENT / "artifacts/four_way"
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [
        "# Hour 6047 four-way vectorization results",
        "",
        f"Commit: `{binding['commit']}`. One fresh, isolated primary attempt per condition, fixed sequential order. CVXPY 1.9.3 / sparsediffpy 0.6.1; frozen physical inputs, start and numerical settings. Full provenance and logs are in `results/case118_6047_four_way/`.",
        "",
        "| Condition | Status | Accepted | Iterations | Native IPOPT (s) | Solve phase (s) |",
        "|---|---|---|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['condition']} | {r['status']} | {r['accepted']} | {r['iterations']} | {r['native_seconds']:.3f} | {r['solve_phase_seconds']:.3f} |"
        )
    if not any(r["accepted"] for r in rows):
        lines[4:4] = [
            "All four attempts reached the 3,000-iteration limit and were rejected. Disabling both vectorization controls did not restore convergence in the current branch/environment. This does not isolate a dependency regression or establish a root cause; shorter times are times to rejected termination, not successful-solve speedups.",
            "",
        ]
    lines += [
        "",
        f"Total wall time: {wall:.2f} s. Sum of solve phases: {sum(times):.2f} s. Mean: {statistics.mean(times):.2f} s; median: {statistics.median(times):.2f} s. Completed throughput: {summary['completed_attempts_per_hour']:.2f} attempts/hour; accepted throughput: {summary['accepted_attempts_per_hour']:.2f}/hour ({summary['accepted']}/4 accepted).",
        "",
        "Solve phase includes canonicalization, exact start capture/persistence and solver return; native IPOPT time comes from its log. Preparation includes reconstructing the historical model/start and rebuilding the selected representation, so it is not pure build time. Mean/median describe four different conditions, not timing replication.",
        "",
        "| Comparison | Second / first solve time | Difference (s) |",
        "|---|---:|---:|",
    ]
    for p in pairs:
        lines.append(
            f"| {p['second']} vs {p['first']} | {p['second_over_first_solve_time']:.3f} | {p['second_minus_first_seconds']:+.2f} |"
        )
    lines += [
        "",
        "## Native termination and physical audits",
        "",
        "IPOPT native metrics and extracted physical audits are distinct evidence. Rejected objectives are not feasible-cost comparisons. Native metrics below are unscaled final-summary values, which can differ from the last printed iteration.",
        "",
        "| Condition | Native constraint violation | Native dual infeasibility | Extracted active balance (pu) | Extracted reactive balance (pu) | SoC recurrence (MWh) | Terminal SoC (MWh) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        p = r["physical_residuals"]
        n = r["native_metrics"]
        lines.append(
            f"| {r['condition']} | {n['Constraint violation']['unscaled']:.6g} | {n['Dual infeasibility']['unscaled']:.6g} | {p['ac_active_balance_pu_abs']:.6g} | {p['ac_reactive_balance_pu_abs']:.6g} | {p['soc_recurrence_mwh_abs']:.6g} | {p['terminal_soc_mwh_abs']:.6g} |"
        )
    lines += [
        "",
        "## Verification and limitations",
        "",
        summary["verification"],
        "",
        f"Thermal observations: {len(samples)} samples; errors: {thermal_end['errors']}; CPU min/median/max {min(temps):.1f}/{statistics.median(temps):.1f}/{max(temps):.1f} C. Machine-wide telemetry does not isolate solver power or establish throttling.",
        "",
        "Canonical start comparisons across the spatial toggle:",
    ]
    for p in starts:
        lines.append(
            f"- {p['first']} / {p['second']}: full x0 exact = {p['full_canonical_x0_exact']}; normalized layout exact = {p['normalized_layout_exact']}."
        )
    lines += [
        "",
        "Comparison with the preceding isolated two-way diagnostic (historical, not additional fresh observations):",
    ]
    for p in historical:
        lines.append(
            f"- {p['current']}: extracted result exact = {p['extracted_result_exact']}; physical residuals exact = {p['physical_residuals_exact']}; full x0 exact = {p['full_canonical_x0_exact']}; normalized layout exact = {p['normalized_layout_exact']}."
        )
    lines += [
        "",
        "One attempt per condition and fixed order do not establish replicated performance or a root cause. Historical successful runs used a different dependency environment. No retries, recovery attempts, or additional solves were run.",
        "",
    ]
    if write:
        (EXPERIMENT / "FOUR_WAY_REPORT.md").write_text("\n".join(lines))
    print(json.dumps({k: v for k, v in summary.items() if k != "conditions"}, indent=2))
    print("\n".join(lines[:12]))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Regenerate the experiment report and compact summary",
    )
    args = parser.parse_args()
    analyze(write=args.write)
