"""Controlled four-way repeat using current code and disabled sparse dispatch."""

import argparse
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import cvxpy as cp
import numpy as np

from experiments.retained_paths import retained_operation
from experiments.case118_annual_hierarchy.s5_speculative_archive import audit_candidate
from . import diagnose_primary as diagnostic
from .prepare_historical import native_libraries
from .telemetry import TemperatureCollector

ROOT = diagnostic.ROOT
EXPERIMENT = Path(__file__).resolve().parent
OUTPUT = EXPERIMENT / "results/case118_6047_four_way_dense_control"
PREVIOUS = EXPERIMENT / "results/case118_6047_four_way"
MODES = diagnostic.FOUR_WAY_MODES


def write(path, value):
    diagnostic.atomic_immutable_json(path, value)


def libraries():
    from cyipopt import ipopt_wrapper
    return native_libraries(Path(ipopt_wrapper.__file__))


def bind():
    old = diagnostic.read(PREVIOUS / "binding.json")
    diagnostic.check_versions(old)
    files = {path: diagnostic.sha(path) for path in old["execution_sources"]}
    changes = {path: dict(previous=old["execution_sources"][path], current=sha)
               for path, sha in files.items() if sha != old["execution_sources"][path]}
    if any(Path(path).is_relative_to(ROOT / "src") or path.endswith("streaming_runner.py")
           for path in changes):
        raise ValueError("Computational code changed since previous four-way test")
    for path in [Path(__file__), EXPERIMENT / "FOUR_WAY_DENSE_CONTROL.md",
                 EXPERIMENT / "prepare_historical.py", EXPERIMENT / "telemetry.py",
                 ROOT / "experiments/retained_paths.py"]:
        files[str(path.resolve())] = diagnostic.sha(path)
    request = diagnostic.read(diagnostic.REQUEST)
    for ref in request["selected"]["references"].values():
        diagnostic.checked(ref)
    for mode in MODES:
        for name in ("start.json", "prepared.json"):
            path = PREVIOUS / mode / name
            files[str(path)] = diagnostic.sha(path)
    files[str(diagnostic.REQUEST)] = diagnostic.sha(diagnostic.REQUEST)
    OUTPUT.mkdir(exist_ok=False)
    write(OUTPUT / "binding.json", dict(
        files=files, previous_source_changes=changes, commit=diagnostic.git("rev-parse", "HEAD"),
        working_tree_status=diagnostic.git("status", "--short"),
        packages={d.metadata["Name"]: d.version for d in distributions()},
        native_libraries=libraries(), previous_binding=diagnostic.ref(PREVIOUS / "binding.json"),
        software_versions=old["software_versions"], sparsediffpy_version=old["sparsediffpy_version"],
        density_threshold=0.0, conditions={mode: diagnostic.CONDITIONS[mode] for mode in MODES},
        external_fan_confirmed_on=True,
        thread_environment={key: os.environ.get(key) for key in (
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS")},
    ))


def verify():
    binding = diagnostic.read(OUTPUT / "binding.json")
    diagnostic.check_sources(binding["files"])
    diagnostic.check_versions(binding)
    if {d.metadata["Name"]: d.version for d in distributions()} != binding["packages"]:
        raise ValueError("Package inventory changed")
    if libraries() != binding["native_libraries"]:
        raise ValueError("Native binaries changed")
    if {k: os.environ.get(k) for k in binding["thread_environment"]} != binding["thread_environment"]:
        raise ValueError("Thread environment changed")
    return binding


@retained_operation()
def worker(mode, dry_run):
    verify()
    cp.settings.SPARSE_DENSITY_THRESHOLD = 0.0
    folder = OUTPUT / ("preparation" if dry_run else "run") / mode
    folder.mkdir(parents=True, exist_ok=False)
    request = diagnostic.read(diagnostic.REQUEST)
    expected = diagnostic.read(PREVIOUS / mode / "start.json")
    fixture, outer = diagnostic.load_s4_fixture(), diagnostic._outer()
    original = diagnostic.checked(request["selected"]["references"]["primary_request.json"])
    context = original["execution_context"]
    if (fixture.policy_sha256 != context["policy_sha256"]
            or fixture.solve_config_sha256 != context["solve_config_sha256"]):
        raise ValueError("Frozen policy/configuration changed")
    events = []

    def phase(name, iteration=6047, ordinal=0):
        if (iteration, ordinal) != (6047, 0):
            raise ValueError("Unexpected attempt")
        events.append(dict(phase=name, monotonic_seconds=time.monotonic()))
        diagnostic.atomic_json(folder / "phase.json", dict(events=events))

    phase("before_ac_build")
    temporal, spatial = diagnostic.CONDITIONS[mode]
    prepared = diagnostic.prepare(folder, temporal, fixture, outer, request, vectorize_pq=spatial)
    phase("after_ac_build")
    if diagnostic.read(folder / "prepared.json") != diagnostic.read(PREVIOUS / mode / "prepared.json"):
        raise ValueError("Preparation differs from previous condition")
    for actual, saved in [(prepared.raw, expected["raw_start"]),
                          (prepared.assigned, expected["assigned_start"])]:
        if set(actual) != set(saved):
            raise ValueError("Physical start namespace changed")
        for name, value in actual.items():
            np.testing.assert_array_equal(value, saved[name])
    import cyipopt
    native_class = cyipopt.Problem
    entered = False

    class PreparationComplete(BaseException):
        pass

    class VerifiedNativeProblem:
        def __init__(self, **kwargs):
            self.kwargs, self.options = kwargs, {}
            for name in ("lb", "ub", "cl", "cu"):
                if dry_run:
                    np.save(folder / f"{name}.npy", kwargs[name])
                else:
                    np.testing.assert_array_equal(kwargs[name], np.load(
                        OUTPUT / "preparation" / mode / f"{name}.npy"))

        def add_option(self, name, value):
            self.options[name] = value

        def solve(self, x0):
            nonlocal entered
            if entered:
                raise ValueError("Only one native call permitted")
            entered = True
            np.testing.assert_array_equal(x0, expected["complete_x0"])
            start = diagnostic.read(folder / "start.json")
            if start["layout_signature"] != expected["layout_signature"]:
                raise ValueError("Canonical layout changed")
            oracle = self.kwargs["problem_obj"]
            entry = dict(options=self.options, n=self.kwargs["n"], m=self.kwargs["m"],
                         jacobian_entries=len(oracle.jacobianstructure()[0]),
                         hessian_entries=len(oracle.hessianstructure()[0]),
                         density_threshold=cp.settings.SPARSE_DENSITY_THRESHOLD,
                         exact_previous_full_x0=True)
            write(folder / "native_entry.json", entry)
            if dry_run:
                raise PreparationComplete()
            if entry != diagnostic.read(OUTPUT / "preparation" / mode / "native_entry.json"):
                raise ValueError("Native entry differs from preparation")
            verify()
            problem = native_class(**self.kwargs)
            for name, value in {**self.options, "print_level": 5}.items():
                problem.add_option(name, value)
            phase("before_native_solve")
            result = problem.solve(x0)
            phase("after_native_solve")
            info = {key: value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
                    for key, value in result[1].items()}
            write(folder / "native_result.json", diagnostic.vectorized_worker._json_value(info))
            return result

    cyipopt.Problem = VerifiedNativeProblem
    try:
        diagnostic.execute_prepared_attempt(
            prepared, fixture.inputs, fixture.policy, fixture.solve_config, outer,
            start_path=folder / "start.json", result_path=folder / "result.json", phase_observer=phase,
        )
    except PreparationComplete:
        summary = dict(dry_run=True, native_entry_verified=True, native_optimization_run=False)
    else:
        if dry_run or not entered:
            raise RuntimeError("Expected native entry was not reached")
        candidate = audit_candidate(
            folder, prepared.invocation, inputs=fixture.inputs, policy=fixture.policy,
            outer=outer, initial=prepared.initial, stop=prepared.stop,
            expected_request_sha256=prepared.request_sha256,
        )
        audit = candidate.payload["attempt"]["audit"]
        summary = dict(dry_run=False, accepted=candidate.completion.outcome == "accepted",
                       execution_error=audit["exception"], audit=audit)
    verify()
    write(folder / "summary.json", summary)
    print(json.dumps(dict(condition=mode, **summary)), flush=True)


def supervise(dry_run):
    verify()
    if not dry_run:
        for mode in MODES:
            if not diagnostic.read(OUTPUT / "preparation" / mode / "summary.json")["native_entry_verified"]:
                raise ValueError("Incomplete preparation")
        processes = subprocess.check_output(["ps", "-axo", "pid,etime,%cpu,command"], text=True)
        (OUTPUT / "processes-before.txt").write_text(processes)
        if any(marker in processes for marker in (
            "solve_historical.py --arm", "s5_speculative_worker --", "four_way_dense_control --worker")):
            raise ValueError("Potential competing worker")

    def sequence():
        rows = []
        for mode in MODES:
            label = "prepare" if dry_run else "run"
            command = [sys.executable, "-u", "-m", __spec__.name, "--worker", mode]
            if dry_run:
                command.append("--dry-run")
            print(f"Starting {label}: {mode}", flush=True)
            with (OUTPUT / f"{label}-{mode}.log").open("x") as log:
                started = time.monotonic()
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
                write(OUTPUT / f"{label}-{mode}-launch.json", dict(pid=process.pid, command=command))
                with (OUTPUT / f"{label}-{mode}-process.jsonl").open("x") as observations:
                    while process.poll() is None:
                        if not dry_run:
                            observation = subprocess.run(["ps", "-p", str(process.pid), "-o", "pid=,etime=,%cpu=,rss="],
                                                         capture_output=True, text=True)
                            observations.write(json.dumps(dict(unix=time.time(), output=observation.stdout,
                                                               returncode=observation.returncode)) + "\n")
                            observations.flush()
                        time.sleep(1 if dry_run else 5)
                row = dict(condition=mode, returncode=process.returncode, wall_seconds=time.monotonic() - started)
            if process.returncode == 0:
                row["summary"] = diagnostic.read(OUTPUT / ("preparation" if dry_run else "run") / mode / "summary.json")
            rows.append(row)
            write(OUTPUT / f"{label}-{mode}-completion.json", row)
            print(json.dumps(row), flush=True)
            if process.returncode != 0 or row.get("summary", {}).get("execution_error"):
                break
        write(OUTPUT / f"{label}-finished.json", rows)
        return len(rows) == 4 and all(row["returncode"] == 0 and not row["summary"].get("execution_error") for row in rows)

    if dry_run:
        return sequence()
    with TemperatureCollector(OUTPUT / "temperature_telemetry") as collector:
        if not collector.first_sample.is_set():
            raise RuntimeError("No thermal sample before launch")
        return sequence()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--worker", choices=MODES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if sum([args.bind, args.prepare, args.run, args.worker is not None]) != 1:
        parser.error("Select exactly one operation")
    if args.bind:
        bind()
    elif args.worker:
        worker(args.worker, args.dry_run)
    else:
        if not supervise(args.prepare):
            sys.exit(1)


if __name__ == "__main__":
    main()
