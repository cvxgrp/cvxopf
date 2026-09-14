"""Noncircular archive-before-checkpoint transaction for a selected S5 window.

The caller independently audits the scientific window before publication.
This module enforces exact-once pointer movement, not AC feasibility or execution
authority. The immutable winner archive itself is the durable decision, avoiding
a second marker whose absence after a crash could invite a new race.
"""

from __future__ import annotations

from dataclasses import asdict
import gzip
import json
from pathlib import Path
from typing import Mapping, Sequence, cast

import numpy as np

from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.s5_speculative_policy import POLICY_NAME
from experiments.case118_annual_hierarchy.streaming_schema import (
    WindowIndexEntry,
    atomic_gzip_json,
    atomic_json,
    sha256_path,
)

_MUTABLE_CHECKPOINT_FIELDS = {
    "windows",
    "completed_intervals",
    "next_global_iteration",
    "complete",
    "realized_soc_mwh",
    "preceding_controlling_attempt_id",
}


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("expected a JSON mapping")
    return value


def _static_identity(checkpoint: Mapping[str, object]) -> str:
    return object_sha256(
        {
            key: value
            for key, value in checkpoint.items()
            if key not in _MUTABLE_CHECKPOINT_FIELDS
        }
    )


def _state(value: object, size: int) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if result.shape != (size,) or not np.all(np.isfinite(result)):
        raise ValueError("winner state must be a finite identity-aligned vector")
    return result


def publish_winner_window(
    directory: Path,
    checkpoint: Mapping[str, object],
    window: Mapping[str, object],
) -> WindowIndexEntry:
    """Publish one already-audited winner without advancing the checkpoint.

    A repeated identical publication is safe. A different candidate cannot
    replace the durable winner, even if checkpoint advancement was interrupted.
    """
    current = _mapping(json.loads((directory / "checkpoint.json").read_text()))
    if current != checkpoint:
        raise ValueError("checkpoint changed before winner publication")
    iteration = checkpoint["next_global_iteration"]
    if (
        isinstance(iteration, bool)
        or not isinstance(iteration, int)
        or window.get("iteration") != iteration
        or window.get("policy") != POLICY_NAME
        or window.get("schema_version") != 2
        or window.get("shard_id") != checkpoint["shard_id"]
        or window.get("storage_device_ids") != checkpoint["storage_device_ids"]
        or checkpoint.get("complete") is not False
        or window.get("preceding_controlling_attempt_id")
        != checkpoint["preceding_controlling_attempt_id"]
    ):
        raise ValueError("winner/checkpoint identity mismatch")
    ids = cast(Sequence[str], checkpoint["storage_device_ids"])
    initial = _state(window.get("initial_soc_mwh"), len(ids))
    post = _state(window.get("post_step_soc_mwh"), len(ids))
    if not np.array_equal(initial, _state(checkpoint["realized_soc_mwh"], len(ids))):
        raise ValueError("winner starts from a different physical state")
    executed = _mapping(window.get("executed_interval"))
    selected = executed.get("controlling_attempt_id")
    if not isinstance(selected, str) or not selected:
        raise ValueError("winner lacks its selected invocation")
    count = checkpoint["completed_intervals"]
    windows = checkpoint["windows"]
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or not isinstance(windows, list)
        or count != len(windows)
    ):
        raise ValueError("checkpoint count/registry mismatch")
    payload = {
        **window,
        "checkpoint_transition": {
            "prior_checkpoint_sha256": object_sha256(checkpoint),
            "prior_completed_intervals": count,
            "prior_windows_sha256": object_sha256(windows),
            "static_checkpoint_sha256": _static_identity(checkpoint),
            "selected_invocation": selected,
            "post_step_soc_mwh": post.tolist(),
        },
    }
    path = directory / f"window-{iteration:06d}-speculative.json.gz"
    if path.exists():
        with gzip.open(path, "rt") as stream:
            if json.load(stream) != payload:
                raise ValueError("a different winner is already durable")
        return WindowIndexEntry(
            iteration, path.name, path.stat().st_size, sha256_path(path)
        )
    return atomic_gzip_json(path, payload)


def advance_from_winner(directory: Path, entry: WindowIndexEntry) -> bool:
    """Advance once, or return False when this exact winner was already applied.

    Caller must verify process cleanup before invoking this function. It may
    be called after restart with the retained entry, without rerunning a solve.
    """
    if Path(entry.relative_path).name != entry.relative_path:
        raise ValueError("winner archive must be local to its shard directory")
    path = directory / entry.relative_path
    if path.stat().st_size != entry.bytes or sha256_path(path) != entry.sha256:
        raise ValueError("winner archive size/hash mismatch")
    with gzip.open(path, "rt") as stream:
        window = _mapping(json.load(stream))
    if (
        window.get("schema_version") != 2
        or window.get("policy") != POLICY_NAME
        or window.get("iteration") != entry.iteration
    ):
        raise ValueError("unsupported winner archive")
    transaction = _mapping(window.get("checkpoint_transition"))
    checkpoint_path = directory / "checkpoint.json"
    checkpoint = dict(_mapping(json.loads(checkpoint_path.read_text())))
    if _static_identity(checkpoint) != transaction["static_checkpoint_sha256"]:
        raise ValueError("checkpoint authority/identity changed during advancement")
    before = transaction["prior_completed_intervals"]
    count = checkpoint["completed_intervals"]
    windows = checkpoint["windows"]
    if (
        isinstance(before, bool)
        or not isinstance(before, int)
        or before < 0
        or isinstance(count, bool)
        or not isinstance(count, int)
        or not isinstance(windows, list)
        or len(windows) != count
        or count < before
        or object_sha256(windows[:before]) != transaction["prior_windows_sha256"]
    ):
        raise ValueError("checkpoint prefix differs from winner predecessor")
    interval = _mapping(checkpoint["interval"])
    if checkpoint["next_global_iteration"] != cast(int, interval["start"]) + count:
        raise ValueError("checkpoint next-interval/count mismatch")
    if count > before:
        if windows[before] != asdict(entry):
            raise ValueError("checkpoint already advanced using a different winner")
        if count == before + 1 and (
            checkpoint["realized_soc_mwh"] != transaction["post_step_soc_mwh"]
            or checkpoint["preceding_controlling_attempt_id"]
            != transaction["selected_invocation"]
        ):
            raise ValueError("advanced checkpoint disagrees with selected state")
        return False
    if object_sha256(checkpoint) != transaction["prior_checkpoint_sha256"]:
        raise ValueError("checkpoint differs from the exact winner predecessor")
    if checkpoint["next_global_iteration"] != entry.iteration:
        raise ValueError("winner is not the next checkpoint interval")
    next_iteration = entry.iteration + 1
    if next_iteration > cast(int, interval["stop"]):
        raise ValueError("winner advances beyond the shard")
    checkpoint.update(
        {
            "windows": [*windows, asdict(entry)],
            "completed_intervals": count + 1,
            "next_global_iteration": next_iteration,
            "complete": next_iteration == interval["stop"],
            "realized_soc_mwh": transaction["post_step_soc_mwh"],
            "preceding_controlling_attempt_id": transaction["selected_invocation"],
        }
    )
    atomic_json(checkpoint_path, checkpoint)
    return True


def pending_winner(directory: Path) -> WindowIndexEntry | None:
    """Discover the next durable decision after losing all supervisor memory.

    Discovery does not advance state. The restarting supervisor must reconcile
    and reap any old contenders before calling `advance_from_winner`.
    """
    checkpoint = _mapping(json.loads((directory / "checkpoint.json").read_text()))
    iteration = checkpoint["next_global_iteration"]
    if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 0:
        raise ValueError("checkpoint next interval must be a nonnegative integer")
    if checkpoint.get("complete") is True:
        return None
    path = directory / f"window-{iteration:06d}-speculative.json.gz"
    if not path.exists():
        return None
    return WindowIndexEntry(
        iteration, path.name, path.stat().st_size, sha256_path(path)
    )
