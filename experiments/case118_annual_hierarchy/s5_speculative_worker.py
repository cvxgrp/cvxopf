"""One fresh-process candidate. Entry is gated by reviewed policy continuation."""

from __future__ import annotations

import argparse
from dataclasses import asdict, fields
import json
from pathlib import Path
import time
from typing import Any, Mapping

from experiments.case118_annual_hierarchy.run_s4b import _outer
from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.s5_execution import (
    execution_context,
    load_numerical_authority,
)
from experiments.case118_annual_hierarchy.s5_source_transition import (
    require_current_transition,
)
from experiments.case118_annual_hierarchy.s5_speculative_archive import (
    audit_candidate,
    invocation,
)
from experiments.case118_annual_hierarchy.s5_speculative_attempt import (
    SelectedController,
    execute_prepared_attempt,
    load_retained_start,
    prepare_attempt,
)
from experiments.case118_annual_hierarchy.s5_speculative_policy import POLICY_NAME
from experiments.case118_annual_hierarchy.streaming_archive import (
    _json_value,
    causal_source_from_archive,
)
from experiments.case118_annual_hierarchy.streaming_runner import CausalControllerSource
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_json,
    atomic_immutable_json,
    sha256_path,
)


def source_payload(
    source: CausalControllerSource | SelectedController | None,
) -> dict[str, object] | None:
    if source is None:
        return None
    selected = source if isinstance(source, SelectedController) else None
    actual = selected.source if selected is not None else source
    return {
        "invocation": None if selected is None else asdict(selected.invocation),
        "source": {
            field.name: _json_value(getattr(actual, field.name))
            for field in fields(CausalControllerSource)
        },
    }


def restore_source(
    value: Mapping[str, Any] | None,
) -> CausalControllerSource | SelectedController | None:
    if value is None:
        return None
    source = causal_source_from_archive({"causal_source": value["source"]})
    return (
        source
        if value["invocation"] is None
        else SelectedController(invocation(value["invocation"]), source)
    )


def execute(directory: Path) -> None:
    request = json.loads((directory / "request.json").read_text())
    context = execution_context()
    if context.get("git_clean") is not True or request["execution_context"] != context:
        raise ValueError("speculative child requires the bound clean execution source")
    authority = load_numerical_authority(
        Path(request["authority_path"]),
        expected_execution_commit=str(context["git_commit"]),
        expected_source_fingerprint=str(context["source_fingerprint"]),
    )
    if authority.get("recovery_policy") != POLICY_NAME:
        raise ValueError("speculative child lacks explicit policy authority")
    transition = require_current_transition(
        Path(request["output_root"]), context, authority
    )
    if (
        transition is None
        or transition["contract_sha256"] != request["contract_sha256"]
    ):
        raise ValueError("speculative child continuation mismatch")
    spec = invocation(request["invocation"])
    checkpoint_path = Path(request["checkpoint_path"])
    if sha256_path(checkpoint_path) != request["checkpoint_sha256"]:
        raise ValueError("physical checkpoint changed before contender construction")
    checkpoint = json.loads(checkpoint_path.read_text())
    ids = checkpoint["storage_device_ids"]
    initial = dict(zip(ids, checkpoint["realized_soc_mwh"], strict=True))
    if (
        spec.window.shard_id != checkpoint["shard_id"]
        or spec.window.iteration != checkpoint["next_global_iteration"]
        or request["source_sha256"] != object_sha256(request["preceding_source"])
    ):
        raise ValueError("speculative child state/source mismatch")
    fixture, outer = load_s4_fixture(), _outer()
    preceding = restore_source(request["preceding_source"])
    source_id = (
        None
        if preceding is None
        else preceding.invocation.attempt_id
        if isinstance(preceding, SelectedController)
        else preceding.attempt_id
    )
    if checkpoint.get("execution_source_fingerprint") != context[
        "source_fingerprint"
    ] or source_id != checkpoint.get("preceding_controlling_attempt_id"):
        raise ValueError("worker checkpoint provenance/controller source mismatch")
    stop = min(
        spec.window.iteration + fixture.policy.ac_window_steps,
        checkpoint["interval"]["stop"],
    )
    free = None
    if request["target_free_directory"] is not None:
        path = Path(request["target_free_directory"])
        free_spec = invocation(
            json.loads((path / "result.json").read_text())["invocation"]
        )
        free = audit_candidate(
            path,
            free_spec,
            inputs=fixture.inputs,
            policy=fixture.policy,
            outer=outer,
            initial=initial,
            stop=stop,
        ).target_free_source()
    replay = (
        None
        if request["replay_start"] is None
        else load_retained_start(Path(request["replay_start"]))
    )
    events: list[dict[str, object]] = []

    def phase(name: str, iteration: int, ordinal: int) -> None:
        if iteration != spec.window.iteration or ordinal != spec.source_slot:
            raise ValueError("single-attempt phase identity mismatch")
        events.append({"phase": name, "monotonic_seconds": time.monotonic()})
        atomic_json(
            directory / "phase.json", {"invocation": asdict(spec), "events": events}
        )

    phase("before_ac_build", spec.window.iteration, spec.source_slot)
    try:
        prepared = prepare_attempt(
            fixture.inputs,
            fixture.policy,
            fixture.solve_config,
            outer,
            spec,
            initial,
            preceding,
            trajectory_start=checkpoint["interval"]["start"],
            trajectory_stop=checkpoint["interval"]["stop"],
            trajectory_initial_soc_mwh=dict(
                zip(ids, checkpoint["initial_soc_mwh"], strict=True)
            ),
            target_free=free,
            replay=replay,
        )
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
    except Exception as exc:
        atomic_immutable_json(
            directory / "worker-error.json",
            {"invocation": asdict(spec), "exception": f"{type(exc).__name__}: {exc}"},
        )
        raise
    # Detect source/environment changes without attributing a new context to data.
    if execution_context() != context:
        raise ValueError("speculative child execution context changed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    execute(args.directory)


if __name__ == "__main__":
    main()
