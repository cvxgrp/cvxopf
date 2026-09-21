"""Replay hour 6047's frozen primary with explicit vectorization conditions."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import os
from pathlib import Path
import subprocess
import sys
import time

import numpy as np

from experiments.case118_annual_hierarchy import streaming_runner as streaming
from experiments.case118_annual_hierarchy.run_s0 import _software_versions
from experiments.case118_annual_hierarchy.run_s4b import _outer
from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.s5_speculative_archive import invocation
from experiments.case118_annual_hierarchy.s5_speculative_attempt import (
    execute_prepared_attempt,
    load_retained_start,
    prepare_attempt,
)
from experiments.case118_annual_hierarchy.s5_speculative_worker import restore_source
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    atomic_json,
)
from experiments.case118_vectorization_replay import worker as vectorized_worker
from experiments.case118_vectorization_replay.sample import ROOT, checked, read, ref, sha
from .run import OUTPUT as STUDY, PREVIOUS, git, validate_destination
from .telemetry import TemperatureCollector

REQUEST = STUDY / "run/s4b-shard-008/ac-006047-spec-00/request.json"
MODES = ("stepwise", "vectorized")
FOUR_WAY_MODES = ("none", "time_only", "spatial_only", "both")
CONDITIONS = {
    "stepwise": ("stepwise", True), "vectorized": ("vectorized", True),
    "none": ("stepwise", False), "time_only": ("vectorized", False),
    "spatial_only": ("stepwise", True), "both": ("vectorized", True),
}
SPATIAL_SWITCH_COMMIT = "e432b8194c14645eae561d013e0eb052959dac36"
SPATIAL_SWITCH_SOURCES = {
    "src/cvxopf/problem.py", "src/cvxopf/ac_problem.py",
    "experiments/case118_annual_hierarchy/streaming_runner.py",
}


@contextmanager
def ipopt_iteration_log():
    """Add logging at the solver boundary without changing the frozen config.

    Each diagnostic runs in its own process. Retain the original streaming
    canonicalization and x0 checks; only the forwarded print level changes.
    """
    original = streaming.IPOPT

    class LoggingIPOPT(original):
        def solve_via_data(self, data, warm_start, verbose, solver_opts,
                           solver_cache=None):
            return original.solve_via_data(
                self, data, warm_start, verbose,
                {**solver_opts, "print_level": 5}, solver_cache,
            )

    streaming.IPOPT = LoggingIPOPT
    try:
        yield
    finally:
        streaming.IPOPT = original


def check_sources(sources):
    for path, expected in sources.items():
        if sha(path) != expected:
            raise ValueError(f"Execution source changed: {path}")


def check_versions(binding):
    if (_software_versions() != binding["software_versions"]
            or version("sparsediffpy") != binding["sparsediffpy_version"]):
        raise ValueError("Diagnostic dependencies differ from the failed study")


def bind_sources(historical, *, four_way):
    """Allow only the committed spatial-switch sources to differ from history."""
    sources, changes = {}, {}
    for path, expected in historical.items():
        actual = sha(path)
        relative = str(Path(path).relative_to(ROOT))
        if four_way and relative in SPATIAL_SWITCH_SOURCES:
            committed = subprocess.check_output(
                ["git", "show", f"{SPATIAL_SWITCH_COMMIT}:{relative}"], cwd=ROOT,
            )
            if actual != sha256(committed).hexdigest():
                raise ValueError(f"Source differs from approved spatial-switch commit: {path}")
            changes[path] = dict(historical_sha256=expected, current_sha256=actual,
                                 approved_commit=SPATIAL_SWITCH_COMMIT)
        elif actual != expected:
            raise ValueError(f"Execution source changed: {path}")
        sources[path] = actual
    return sources, changes


def preflight(output, *, commit=None, prepare_only=False, four_way=False):
    for previous in (STUDY, PREVIOUS,
                     ROOT / "outputs/case118_6047_primary_diagnostic",
                     ROOT / "outputs/case118_6047_primary_diagnostic_retry"):
        validate_destination(output, previous)
    head = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--untracked-files=normal")
    if not prepare_only and (commit != head or dirty):
        raise ValueError("Launch requires the exact reviewed commit and a clean checkout")
    request = read(REQUEST)
    spec = invocation(request["invocation"])
    if (spec.window.iteration != 6047 or spec.order != 0 or spec.source_slot != 0
            or request["replay_start"] is not None
            or request["target_free_directory"] is not None):
        raise ValueError("Expected hour 6047's original primary request")
    for reference in request["selected"]["references"].values():
        checked(reference)
    sources, source_changes = bind_sources(request["execution_sources"], four_way=four_way)
    sources[str(Path(__file__).resolve())] = sha(__file__)
    plan = Path(__file__).with_name("FOUR_WAY_DIAGNOSTIC.md" if four_way
                                  else "PRIMARY_DIAGNOSTIC.md")
    sources[str(plan)] = sha(plan)
    study_binding = read(STUDY / "binding.json")
    check_versions(study_binding)
    return dict(
        created_utc=datetime.now(timezone.utc).isoformat(), commit=head,
        working_tree_status=dirty, prepare_only=prepare_only,
        primary_request=ref(REQUEST), failed_study_binding=ref(STUDY / "binding.json"),
        execution_sources=sources, software_versions=_software_versions(),
        sparsediffpy_version=version("sparsediffpy"),
        modes=list(FOUR_WAY_MODES if four_way else MODES),
        conditions={name: dict(temporal_assembly=CONDITIONS[name][0],
                               vectorize_pq=CONDITIONS[name][1])
                    for name in (FOUR_WAY_MODES if four_way else MODES)},
        historical_source_changes=source_changes, sparse_pq=True,
        numerical_solver_options="Unchanged; print_level=5 is the only logging override",
        workers=1, helpers=0,
    )


def prepare(directory, mode, fixture, outer, request, *, vectorize_pq=True):
    """Build without solving, and verify the physical start in either layout."""
    if mode not in MODES:
        raise ValueError(f"Unknown temporal representation: {mode}")
    if not isinstance(vectorize_pq, bool):
        raise ValueError("vectorize_pq must be a boolean")
    selected = request["selected"]
    historical = checked(selected["references"]["primary_request.json"])
    retained = load_retained_start(
        Path(selected["references"]["primary_start.json"]["path"])
    )
    # Reconstruct the same historical request/start before changing representation.
    prepared = prepare_attempt(
        fixture.inputs, fixture.policy, fixture.solve_config, outer,
        invocation(request["invocation"]), selected["initial_soc_mwh"],
        restore_source(historical["preceding_source"]),
        trajectory_start=selected["trajectory_start"],
        trajectory_stop=selected["trajectory_stop"],
        trajectory_initial_soc_mwh=selected["trajectory_initial_soc_mwh"],
    )
    representation_inputs = replace(
        fixture.inputs, options=replace(fixture.inputs.options, vectorize_pq=vectorize_pq),
    )
    if mode != "stepwise" or not vectorize_pq:
        storage = streaming._inner_storage(fixture.inputs, prepared.initial, prepared.target)
        build = streaming.build_window(
            representation_inputs, "ac", 6047, prepared.stop, storage,
            temporal_assembly=mode,
        )
        raw, assigned = prepared.raw, prepared.assigned
        if mode == "vectorized":
            initial = [prepared.initial[k] for k in fixture.inputs.storage_device_ids]
            raw = vectorized_worker.pack_start(raw, build, initial)
            assigned = vectorized_worker.pack_start(assigned, build, initial)
        streaming.assign_start(build, assigned)
        prepared = replace(prepared, build=build, raw=raw, assigned=assigned)
    assigned = (vectorized_worker.unpack_values(prepared.assigned, retained.assigned)
                if mode == "vectorized" else prepared.assigned)
    if prepared.request_sha256 != retained.request_sha256:
        raise ValueError("Frozen physical request identity changed")
    if set(assigned) != set(retained.assigned):
        raise ValueError("Primary start namespaces differ")
    for name, value in retained.assigned.items():
        np.testing.assert_array_equal(assigned[name], value, err_msg=name)
    if prepared.build.temporal_assembly != mode:
        raise ValueError("Built the wrong temporal representation")
    atomic_immutable_json(directory / "prepared.json", dict(
        temporal_assembly=mode, request_sha256=prepared.request_sha256,
        vectorize_pq=vectorize_pq,
        representation_input_sha256=streaming.execution_input_sha256(representation_inputs),
        historical_named_start_exact=True,
        variable_objects=len(prepared.build.prob.variables()),
        constraint_objects=len(prepared.build.prob.constraints),
        model_coordinates=sum(v.size for v in prepared.build.prob.variables()),
    ))
    return prepared


def worker(directory):
    binding = read(directory.parent / "binding.json")
    if not binding["prepare_only"] and (
        git("rev-parse", "HEAD") != binding["commit"]
        or git("status", "--porcelain", "--untracked-files=normal")
    ):
        raise ValueError("Diagnostic launch binding requires a clean, unchanged commit")
    check_sources(binding["execution_sources"])
    check_versions(binding)
    request = checked(binding["primary_request"])
    fixture, outer = load_s4_fixture(), _outer()
    historical = checked(request["selected"]["references"]["primary_request.json"])
    context = historical["execution_context"]
    if (fixture.policy_sha256 != context["policy_sha256"]
            or fixture.solve_config_sha256 != context["solve_config_sha256"]):
        raise ValueError("Frozen policy or solver configuration changed")
    events = []

    def phase(name, iteration=6047, ordinal=0):
        if (iteration, ordinal) != (6047, 0):
            raise ValueError("Only the selected primary is permitted")
        events.append(dict(phase=name, monotonic_seconds=time.monotonic()))
        atomic_json(directory / "phase.json", dict(events=events))

    phase("before_ac_build")
    condition = binding["conditions"][directory.name]
    prepared = prepare(directory, condition["temporal_assembly"], fixture, outer, request,
                       vectorize_pq=condition["vectorize_pq"])
    phase("after_ac_build")
    if not binding["prepare_only"]:
        # Retain IPOPT iterations/termination diagnostics without numerical tuning.
        atomic_immutable_json(directory / "solver_configuration.json", dict(
            original_options=dict(fixture.solve_config.ac.options),
            frozen_config_unchanged=True,
            solver_boundary_logging_override={"print_level": 5},
        ))
        with ipopt_iteration_log():
            execute_prepared_attempt(
                prepared, fixture.inputs, fixture.policy, fixture.solve_config, outer,
                start_path=directory / "start.json", result_path=directory / "result.json",
                phase_observer=phase,
            )
    check_sources(binding["execution_sources"])


def run_pair(output, *, commit=None, prepare_only=False, fan_on=False, four_way=False):
    if not prepare_only and not fan_on:
        raise ValueError("Confirm the external fan is on before launch")
    binding = preflight(output, commit=commit, prepare_only=prepare_only, four_way=four_way)
    if not prepare_only:
        # Fail before launching a solve if the monitoring permission is absent.
        subprocess.run(["ps", "-o", "pid=", "-p", str(os.getpid())],
                       check=True, capture_output=True)
    output.mkdir()
    atomic_immutable_json(output / "binding.json", binding)

    def pair():
        rows = []
        for mode in (FOUR_WAY_MODES if four_way else MODES):
            directory = output / mode
            directory.mkdir()
            started = time.monotonic()
            print(f"Starting {mode}; prepare_only={prepare_only}", flush=True)
            with (directory / "worker.log").open("w") as log:
                process = subprocess.Popen(
                    [sys.executable, "-u", "-m", __spec__.name,
                     "--worker-directory", str(directory)],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                )
                atomic_immutable_json(directory / "launch.json", dict(pid=process.pid))
                try:
                    code = process.wait()
                except BaseException:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise
            row = dict(mode=mode, returncode=code, wall_seconds=time.monotonic()-started)
            execution_error = None
            if (directory / "result.json").exists():
                attempt = read(directory / "result.json")["attempt"]
                audit, result = attempt.get("audit"), attempt.get("result")
                row.update(audit=audit, objective=(result or {}).get("objective"),
                           slot_state=attempt.get("slot_state"), reason=attempt.get("reason"))
                if attempt.get("slot_state") == "construction_error":
                    execution_error = attempt.get("reason") or "construction_error"
                elif audit and audit.get("exception"):
                    execution_error = audit["exception"]
            elif not prepare_only and not code:
                execution_error = "Worker exited without a result artifact"
            row["execution_error"] = execution_error
            atomic_immutable_json(directory / "completion.json", row)
            if code or execution_error:
                raise RuntimeError(f"{mode} worker failed; inspect {directory / 'worker.log'}")
            rows.append(row)
            print(f"Finished {mode}", flush=True)
        atomic_immutable_json(output / "finished.json", dict(
            prepare_only=prepare_only, attempts=rows,
            note="Execution finished; solver acceptance is recorded separately per attempt.",
        ))

    if prepare_only:
        pair()
    else:
        with TemperatureCollector(output / "temperature_telemetry"):
            pair()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--fan-on", action="store_true")
    parser.add_argument("--four-way", action="store_true",
                        help="Repeat all four time/spatial conditions, once each")
    parser.add_argument("--worker-directory", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_directory is not None:
        worker(args.worker_directory.resolve())
    elif args.output is None:
        parser.error("--output is required and must name a new directory")
    else:
        run_pair(args.output.resolve(), commit=args.commit,
                 prepare_only=args.prepare_only, fan_on=args.fan_on, four_way=args.four_way)
