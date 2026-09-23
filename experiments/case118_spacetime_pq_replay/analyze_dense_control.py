"""Reconstruct compact evidence for the completed four-way dispatch control."""

import hashlib
import itertools
import json
from pathlib import Path
import re

import numpy as np

from .four_way_dense_control import MODES, OUTPUT, PREVIOUS, verify


def read(path):
    return json.loads(path.read_text())


def main():
    binding = verify()
    finished = read(OUTPUT / "run-finished.json")
    assert [r["condition"] for r in finished] == list(MODES)
    old_rows = {r["condition"]: r for r in read(PREVIOUS / "summary.json")["conditions"]}
    rows, results = [], {}
    evidence = {}
    for mode, completion in zip(MODES, finished):
        assert completion["returncode"] == 0
        folder = OUTPUT / "run" / mode
        record = read(folder / "result.json")
        attempt = record["attempt"]
        audit = attempt["audit"]
        assert audit["exception"] is None and audit["accepted_primal"]
        assert completion["summary"]["accepted"]
        start = read(folder / "start.json")
        old_start = read(PREVIOUS / mode / "start.json")
        for key in ("complete_x0", "layout_signature", "raw_start", "assigned_start"):
            assert start[key] == old_start[key], (mode, key)
        log = (OUTPUT / f"run-{mode}.log").read_text()
        phases = {e["phase"]: e["monotonic_seconds"]
                  for e in read(folder / "phase.json")["events"]}
        native = {}
        for label in ("Objective", "Dual infeasibility", "Constraint violation",
                      "Variable bound violation", "Complementarity", "Overall NLP error"):
            match = re.search(re.escape(label) + r"\.*:\s*([\deE+.-]+)\s+([\deE+.-]+)", log)
            assert match, (mode, label)
            native[label] = dict(scaled=float(match[1]), unscaled=float(match[2]))
        rows.append(dict(
            condition=mode, temporal_assembly=binding["conditions"][mode][0],
            vectorize_pq=binding["conditions"][mode][1], sparse_pq=True,
            status=audit["status"], accepted=audit["accepted_primal"],
            iterations=int(re.search(r"Number of Iterations\.+:\s*(\d+)", log)[1]),
            native_seconds=float(re.search(r"Total seconds in IPOPT\s*=\s*([\d.]+)", log)[1]),
            solve_phase_seconds=phases["after_ac_solve"] - phases["before_ac_solve"],
            build_seconds=phases["after_ac_build"] - phases["before_ac_build"],
            worker_wall_seconds=completion["wall_seconds"],
            termination=re.search(r"EXIT: (.*)", log)[1],
            native_entry=read(folder / "native_entry.json"), native_metrics=native,
            physical_residuals=audit["residuals"],
            extracted_objective=attempt["result"]["objective"],
            previous_default={k: old_rows[mode][k] for k in (
                "status", "accepted", "iterations", "native_seconds", "solve_phase_seconds")},
        ))
        results[mode] = record
        for path in [folder / f"{name}.json" for name in (
            "result", "start", "phase", "native_entry", "native_result", "summary")]:
            evidence[str(path.relative_to(OUTPUT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        path = OUTPUT / f"run-{mode}.log"
        evidence[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    pairs = []
    for first, second in itertools.combinations(MODES, 2):
        a, b = (results[m]["attempt"]["result"] for m in (first, second))
        differences = {key: float(np.max(np.abs(np.asarray(a[key]) - np.asarray(b[key]))))
                       for key in ("Pg", "Qg", "Vm", "Va_deg", "b", "b_q", "soc", "p_nd", "q_nd")}
        pairs.append(dict(first=first, second=second,
                          objective_difference=b["objective"] - a["objective"],
                          max_absolute_differences=differences))
    historical = read(OUTPUT.parent / "hour6047_environment_reproduction/convergence-test/a/result.json")
    time_only = results["time_only"]
    exact = dict(named_values=time_only["solution_values"] == historical["solution_values"],
                 extracted_result=time_only["attempt"]["result"] == historical["attempt"]["result"])
    assert all(exact.values())
    thermal = read(OUTPUT / "temperature_telemetry/finished.json")
    assert not thermal["errors"]
    samples = [json.loads(line) for line in
               (OUTPUT / "temperature_telemetry/samples.jsonl").read_text().splitlines()]
    temps = [s["temp"]["cpu_temp_avg"] for s in samples]
    summary = dict(
        source_commit=binding["commit"], density_threshold=0.0,
        cvxpy=binding["packages"]["cvxpy"], sparsediffpy=binding["sparsediffpy_version"],
        conditions=rows, solution_comparisons=pairs,
        time_only_exact_historical_solution=exact,
        thermal=dict(samples=len(samples), errors=thermal["errors"],
                     cpu_temperature_min_c=min(temps), cpu_temperature_max_c=max(temps)),
        binding_sha256=hashlib.sha256((OUTPUT / "binding.json").read_bytes()).hexdigest(),
        evidence_sha256=evidence,
    )
    destination = Path(__file__).resolve().parent / "artifacts/provenance/four_way_dense_control.json"
    destination.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "evidence_sha256"}, indent=2))


if __name__ == "__main__":
    main()
