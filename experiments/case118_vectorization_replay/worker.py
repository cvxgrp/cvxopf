"""Replay one historical window with vectorized AC and the original S5 starts."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import time

import numpy as np

from experiments.case118_annual_hierarchy import streaming_runner as streaming
from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.run_s4b import _outer
from experiments.case118_annual_hierarchy.s5_speculative_archive import (
    audit_candidate,
    invocation,
)
from experiments.case118_annual_hierarchy.s5_speculative_attempt import (
    PreparedAttempt,
    prepare_attempt,
    execute_prepared_attempt,
    load_retained_start,
)
from experiments.case118_annual_hierarchy.s5_speculative_worker import restore_source
from experiments.case118_annual_hierarchy.streaming_archive import _json_value
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_json,
    atomic_immutable_json,
)
from .sample import read, checked, sha


def pack_start(step_values, build, initial):
    """Map original time-suffixed physical coordinates to time-last arrays."""
    output = {}
    used = set()
    horizon = build.data["T"]
    for name, variable in streaming.variables_by_name(build).items():
        names = [f"{name}_{t}" for t in range(horizon)]
        columns = [np.asarray(step_values[n], dtype=float).reshape(-1) for n in names]
        used.update(names)
        if name == "soc":
            columns.insert(0, np.asarray(initial, dtype=float))
        value = np.column_stack(columns)
        if value.shape != variable.shape:
            raise ValueError(
                f"Unexpected packed shape for {name}: {value.shape} vs {variable.shape}"
            )
        output[name] = value
    if used != set(step_values):
        raise ValueError("Unmapped historical variables")
    return output


def unpack_values(values, template):
    """Restore stepwise names for the existing deterministic helper initializer."""
    result = {}
    for name, original in template.items():
        base, index = name.rsplit("_", 1)
        column = int(index) + (base == "soc")
        result[name] = np.asarray(values[base])[:, column].reshape(
            np.asarray(original).shape
        )
    return result


def prepare(directory, fixture, outer, request):
    selected = request["selected"]
    spec = invocation(request["invocation"])
    historical = checked(selected["references"]["primary_request.json"])
    retained = load_retained_start(
        Path(selected["references"]["primary_start.json"]["path"])
    )
    checked(selected["references"]["primary_start.json"])
    replay = (
        None
        if request["replay_start"] is None
        else load_retained_start(Path(request["replay_start"]))
    )
    initial = selected["initial_soc_mwh"]
    initial_array = [initial[k] for k in fixture.inputs.storage_device_ids]
    prepared_step = None
    started = time.monotonic()
    if replay is None:
        free = None
        if request["target_free_directory"] is not None:
            free_dir = Path(request["target_free_directory"])
            free_spec = invocation(read(free_dir / "result.json")["invocation"])
            free = audit_candidate(
                free_dir,
                free_spec,
                inputs=fixture.inputs,
                policy=fixture.policy,
                outer=outer,
                initial=initial,
                stop=spec.window.iteration + 3,
            ).target_free_source()
            free = replace(
                free,
                solution_values=unpack_values(free.solution_values, retained.assigned),
            )
        prepared_step = prepare_attempt(
            fixture.inputs,
            fixture.policy,
            fixture.solve_config,
            outer,
            spec,
            initial,
            restore_source(historical["preceding_source"]),
            trajectory_start=selected["trajectory_start"],
            trajectory_stop=selected["trajectory_stop"],
            trajectory_initial_soc_mwh=selected["trajectory_initial_soc_mwh"],
            target_free=free,
        )
        assert prepared_step.request_sha256 == retained.request_sha256
        if spec.order == 0:
            for name, value in retained.assigned.items():
                np.testing.assert_array_equal(
                    prepared_step.assigned[name], value, err_msg=name
                )
    else:
        assert replay.invocation.attempt_id == spec.replay_of
        assert (
            replay.invocation.source_slot == spec.source_slot
            and replay.invocation.window == spec.window
        )
        assert replay.request_sha256 == retained.request_sha256
    preparation_seconds = time.monotonic() - started
    storage = streaming._inner_storage(
        fixture.inputs,
        initial,
        selected["target_soc_mwh"] if spec.hard_target else None,
    )
    build = streaming.build_window(
        fixture.inputs,
        "ac",
        spec.window.iteration,
        spec.window.iteration + 3,
        storage,
        temporal_assembly="vectorized",
    )
    if replay is None:
        raw = pack_start(prepared_step.raw, build, initial_array)
        assigned = pack_start(prepared_step.assigned, build, initial_array)
        source_kind, source_id = (
            prepared_step.source_kind,
            prepared_step.source_attempt_id,
        )
        # Exact round trip for every named coordinate, including lifted branches.
        roundtrip = unpack_values(assigned, prepared_step.assigned)
        assert all(
            np.array_equal(roundtrip[k], v) for k, v in prepared_step.assigned.items()
        )
    else:
        raw, assigned = replay.raw, replay.assigned
        source_kind, source_id = replay.source_kind, replay.source_attempt_id
    streaming.assign_start(build, assigned)
    atomic_immutable_json(
        directory / "replica.json",
        dict(
            historical_primary_start=selected["references"]["primary_start.json"],
            primary_named_start_exact=spec.order == 0,
            named_coordinate_mapping="Each original family_t maps to family[:,t]; theta/v singleton axes removed; fixed initial SoC prepended.",
            initialization_preparation_seconds=preparation_seconds,
            retained_model_request_sha256=retained.request_sha256,
        ),
    )
    return PreparedAttempt(
        spec,
        retained.request_sha256,
        build,
        initial,
        selected["target_soc_mwh"],
        spec.window.iteration + 3,
        raw,
        assigned,
        source_kind,
        source_id,
        None if replay is None else replay.evidence,
    )


def execute(directory):
    request = read(directory / "request.json")
    for path, expected in request["execution_sources"].items():
        assert sha(path) == expected, path
    fixture, outer = load_s4_fixture(), _outer()
    historical = checked(request["selected"]["references"]["primary_request.json"])
    assert fixture.policy_sha256 == historical["execution_context"]["policy_sha256"]
    assert (
        fixture.solve_config_sha256
        == historical["execution_context"]["solve_config_sha256"]
    )
    events = []
    spec = invocation(request["invocation"])

    def phase(name, iteration, ordinal):
        assert iteration == spec.window.iteration and ordinal == spec.source_slot
        events.append(dict(phase=name, monotonic_seconds=time.monotonic()))
        atomic_json(
            directory / "phase.json",
            dict(invocation=request["invocation"], events=events),
        )

    phase("before_ac_build", spec.window.iteration, spec.source_slot)
    prepared = prepare(directory, fixture, outer, request)
    phase("after_ac_build", spec.window.iteration, spec.source_slot)
    execute_prepared_attempt(
        prepared,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        outer,
        start_path=directory / "start.json",
        result_path=directory / "result.json",
        phase_observer=phase,
    )
    for path, expected in request["execution_sources"].items():
        assert sha(path) == expected, path
    atomic_immutable_json(
        directory / "solver_configuration.json",
        _json_value(dict(ac_options=dict(fixture.solve_config.ac.options))),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    execute(args.directory)
