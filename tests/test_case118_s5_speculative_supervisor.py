"""Simulated workers and real temporary archive/checkpoint writes; no solves."""

from dataclasses import asdict
import gzip
import json

import pytest

from experiments.case118_annual_hierarchy.s4b_execution import (
    shard_checkpoint_payload,
    validate_shard_checkpoint,
)
from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    AttemptSpec,
    Completion,
    POLICY_NAME,
    WindowKey,
)
from experiments.case118_annual_hierarchy.s5_speculative_supervisor import (
    MemoryPolicy,
    MemorySample,
    ResourceStop,
    SpeculativeSupervisor,
    UnresolvedWindow,
)
from experiments.case118_annual_hierarchy.s5_speculative_transaction import (
    advance_from_winner,
    pending_winner,
    publish_winner_window,
)
from experiments.case118_annual_hierarchy.streaming_schema import atomic_json


A = WindowKey("s4b-shard-000", 0)
B = WindowKey("s4b-shard-001", 0)


def synthetic_shard(key):
    return {
        "shard_id": key.shard_id,
        "ordinal": int(key.shard_id[-3:]),
        "interval": {"start": 0, "stop": 2},
        "storage": {
            "initial_state": {"soc_mwh": [100.0] * 4},
            "terminal_state": {"soc_mwh": [100.0] * 4},
        },
    }


def initialize(directory, key):
    checkpoint = shard_checkpoint_payload(
        shard=synthetic_shard(key),
        execution_source_fingerprint="source",
        outer_plan_sha256="a" * 64,
        execution_mode="annual",
        realized_soc_mwh=[100.0] * 4,
        preceding_controlling_attempt_id=None,
        windows=(),
        execution_registry_sha256="b" * 64,
        allowed_execution_modes=("annual",),
    )
    atomic_json(directory / "checkpoint.json", checkpoint)
    return checkpoint


def read_checkpoint(directory):
    return json.loads((directory / "checkpoint.json").read_text())


def window_payload(checkpoint, winner):
    # Synthetic accepted window used only to exercise transaction ordering.
    return {
        "schema_version": 2,
        "policy": POLICY_NAME,
        "shard_id": winner.window.shard_id,
        "iteration": winner.window.iteration,
        "storage_device_ids": checkpoint["storage_device_ids"],
        "initial_soc_mwh": checkpoint["realized_soc_mwh"],
        "post_step_soc_mwh": [100.0] * 4,
        "preceding_controlling_attempt_id": checkpoint[
            "preceding_controlling_attempt_id"
        ],
        "executed_interval": {
            "controlling_attempt_id": winner.attempt_id,
            "b_mw": [0.0] * 4,
        },
    }


class FakeBackend:
    def __init__(self, directory):
        self.directory = directory
        self.now = 0.0
        self.running = {}
        self.starts = {}
        self.returns = {}
        self.ready = {}
        self.log = []
        self.entries = {}
        self.rss = MemorySample(10_000.0, (5_000.0, 5_000.0))
        self.fail_after_publish = False

    def launch(self, attempt):
        self.running[attempt.attempt_id] = attempt
        self.starts[attempt.attempt_id] = self.now
        shard_dir = self.directory / attempt.window.shard_id
        if not (shard_dir / "checkpoint.json").exists():
            initialize(shard_dir, attempt.window)

    def solve_started_at(self, attempt):
        return self.starts.get(attempt.attempt_id)

    def solve_returned_at(self, attempt):
        return self.returns.get(attempt.attempt_id)

    def poll_audited(self, attempt):
        return self.ready.pop(attempt.attempt_id, None)

    def reap(self, attempt):
        del self.running[attempt.attempt_id]
        self.log.append(("reaped", attempt.attempt_id))

    def cancel_and_reap(self, attempt, reason):
        self.running.pop(attempt.attempt_id, None)
        self.log.append((reason, attempt.attempt_id))

    def has_complete_start(self, attempt):
        return self.starts.get(attempt.attempt_id) is not None

    def memory(self):
        return self.rss

    def event(self, kind, attempt, now):
        self.log.append((kind, None if attempt is None else attempt.attempt_id))

    def publish_winner(self, winner, attempts):
        directory = self.directory / winner.window.shard_id
        cp = read_checkpoint(directory)
        self.entries[winner.window] = publish_winner_window(
            directory,
            cp,
            window_payload(cp, winner),
        )
        self.log.append(("published", winner.attempt_id))
        if self.fail_after_publish:
            raise OSError("simulated interruption after durable winner")

    def advance_checkpoint(self, winner):
        assert not any(item.window == winner.window for item in self.running.values())
        advance_from_winner(
            self.directory / winner.window.shard_id, self.entries[winner.window]
        )
        self.log.append(("advanced", winner.attempt_id))


def setup(tmp_path, two=True):
    backend = FakeBackend(tmp_path)
    supervisor = SpeculativeSupervisor(backend)
    supervisor.add_window(A, has_preceding=True, now=0)
    if two:
        supervisor.add_window(B, has_preceding=True, now=0)
    return backend, supervisor


def tick(backend, supervisor, now):
    backend.now = now
    return supervisor.tick(now)


def accepted(spec):
    return Completion(spec, "accepted", True)


def test_third_lane_does_not_kill_primary_and_is_globally_shared(tmp_path):
    backend, supervisor = setup(tmp_path)
    tick(backend, supervisor, 299)
    assert len(backend.running) == 2
    tick(backend, supervisor, 300)
    assert len(backend.running) == 3
    assert supervisor.queue.active == A
    assert not any(kind == "solve_budget" for kind, _ in backend.log)
    tick(backend, supervisor, 600)
    assert len(backend.running) == 3
    assert supervisor.queue.active == B
    assert (
        supervisor.races[A].completed[AttemptSpec(A, 1, 6).attempt_id].outcome
        == "timeout"
    )
    assert supervisor.races[A].primary.attempt_id in backend.running


def test_eligibility_order_uses_solve_clock_not_shard_tiebreak(tmp_path):
    backend, supervisor = setup(tmp_path)
    backend.starts[supervisor.races[A].primary.attempt_id] = 20
    tick(backend, supervisor, 400)
    assert supervisor.queue.active == B


def test_helper_winner_archived_before_loser_cleanup_then_checkpoint(tmp_path):
    backend, supervisor = setup(tmp_path)
    tick(backend, supervisor, 300)
    helper = AttemptSpec(A, 1, 6)
    backend.ready[helper.attempt_id] = accepted(helper)
    assert tick(backend, supervisor, 350) == (A,)
    events = backend.log
    primary = supervisor.races[A].primary
    assert events.index(("published", helper.attempt_id)) < events.index(
        ("lost_race", primary.attempt_id)
    )
    assert events.index(("lost_race", primary.attempt_id)) < events.index(
        ("advanced", helper.attempt_id)
    )
    assert read_checkpoint(tmp_path / A.shard_id)["next_global_iteration"] == 1
    assert supervisor.queue.active == B


def test_returned_helper_is_not_timed_out_during_post_processing(tmp_path):
    backend, supervisor = setup(tmp_path, two=False)
    tick(backend, supervisor, 300)
    helper = AttemptSpec(A, 1, 6)
    backend.returns[helper.attempt_id] = 301.0
    tick(backend, supervisor, 900)
    assert helper.attempt_id in backend.running
    assert ("solve_budget", helper.attempt_id) not in backend.log
    backend.ready[helper.attempt_id] = accepted(helper)
    assert tick(backend, supervisor, 901) == (A,)


def test_primary_post_processing_does_not_create_solve_eligibility(tmp_path):
    backend, supervisor = setup(tmp_path, two=False)
    backend.returns[supervisor.races[A].primary.attempt_id] = 1.0
    tick(backend, supervisor, 600)
    assert len(backend.running) == 1
    assert supervisor.queue.active is None


def test_simultaneous_returns_prefer_primary_and_do_not_advance_twice(tmp_path):
    backend, supervisor = setup(tmp_path, two=False)
    tick(backend, supervisor, 300)
    helper, primary = AttemptSpec(A, 1, 6), supervisor.races[A].primary
    backend.ready.update(
        {helper.attempt_id: accepted(helper), primary.attempt_id: accepted(primary)}
    )
    assert tick(backend, supervisor, 700) == (A,)  # returned result precedes timeout
    assert (
        read_checkpoint(tmp_path / A.shard_id)["preceding_controlling_attempt_id"]
        == primary.attempt_id
    )
    assert tick(backend, supervisor, 800) == ()


def test_build_time_does_not_consume_helper_solve_budget(tmp_path):
    backend, supervisor = setup(tmp_path, two=False)
    tick(backend, supervisor, 300)
    helper = AttemptSpec(A, 1, 6)
    backend.starts[helper.attempt_id] = None
    tick(backend, supervisor, 1000)
    assert helper.attempt_id in backend.running
    backend.starts[helper.attempt_id] = 1000
    tick(backend, supervisor, 1299)
    assert helper.attempt_id in backend.running
    tick(backend, supervisor, 1300)
    assert helper.attempt_id not in backend.running


def test_child_start_published_during_poll_does_not_cause_false_stop(tmp_path):
    backend, supervisor = setup(tmp_path, two=False)
    primary = supervisor.races[A].primary
    backend.starts[primary.attempt_id] = 0.1
    tick(backend, supervisor, 0)
    assert not supervisor.stopped
    tick(backend, supervisor, 300.1)
    helper = AttemptSpec(A, 1, 6)
    backend.starts[helper.attempt_id] = 300.2
    tick(backend, supervisor, 300.1)
    assert helper.attempt_id in backend.running


def test_memory_admission_and_pressure_cancel_helper_first(tmp_path):
    backend, supervisor = setup(tmp_path)
    backend.rss = MemorySample(15_000, (7_500, 7_500))
    tick(backend, supervisor, 300)
    assert len(backend.running) == 2
    backend.rss = MemorySample(10_000, (5_000, 5_000))
    tick(backend, supervisor, 301)
    assert len(backend.running) == 3
    backend.rss = MemorySample(23_000, (8_000, 8_000, 7_000))
    tick(backend, supervisor, 302)
    assert len(backend.running) == 2
    assert ("memory_pressure", AttemptSpec(A, 1, 6).attempt_id) in backend.log


@pytest.mark.parametrize(
    "rss", [MemorySample(25_000, (12_500, 12_500)), MemorySample(17_000, (17_000,))]
)
def test_hard_resource_stop_reaps_all_without_advancement(tmp_path, rss):
    backend, supervisor = setup(tmp_path)
    backend.rss = rss
    with pytest.raises(ResourceStop):
        tick(backend, supervisor, 1)
    assert not backend.running and supervisor.stopped
    assert read_checkpoint(tmp_path / A.shard_id)["next_global_iteration"] == 0


def test_uncapped_replay_follows_finite_queue_and_never_times_out(tmp_path):
    backend, supervisor = setup(tmp_path, two=False)
    tick(backend, supervisor, 300)
    for now in (600, 900, 1200):
        tick(backend, supervisor, now)
    tf = AttemptSpec(A, 4, 1)
    backend.ready[tf.attempt_id] = Completion(tf, "rejected", True)
    tick(backend, supervisor, 1201)
    replay = next(spec for spec in backend.running.values() if spec.order == 9)
    assert replay.replay_of == AttemptSpec(A, 1, 6).attempt_id
    tick(backend, supervisor, 20_000)
    assert replay.attempt_id in backend.running
    assert supervisor.races[A].primary.attempt_id in backend.running


def test_exhausted_window_stops_peer_without_inventing_action(tmp_path):
    backend, supervisor = setup(tmp_path)
    # No predecessor: target-free fails, so there are no hard-target helper starts.
    supervisor.races[A].has_preceding = False
    primary = supervisor.races[A].primary
    backend.ready[primary.attempt_id] = Completion(primary, "rejected")
    tick(backend, supervisor, 1)
    tf = AttemptSpec(A, 4, 1)
    backend.ready[tf.attempt_id] = Completion(tf, "rejected")
    with pytest.raises(UnresolvedWindow):
        tick(backend, supervisor, 2)
    assert not backend.running
    assert read_checkpoint(tmp_path / A.shard_id)["completed_intervals"] == 0


def test_restart_after_durable_winner_reuses_selection_without_new_solve(tmp_path):
    backend, supervisor = setup(tmp_path)
    tick(backend, supervisor, 300)
    helper = AttemptSpec(A, 1, 6)
    backend.ready[helper.attempt_id] = accepted(helper)
    backend.fail_after_publish = True
    with pytest.raises(OSError, match="after durable"):
        tick(backend, supervisor, 301)
    assert not backend.running
    assert read_checkpoint(tmp_path / A.shard_id)["completed_intervals"] == 0
    # Reconstruct from disk, not the dead supervisor's remembered registry.
    backend.entries.clear()
    entry = pending_winner(tmp_path / A.shard_id)
    assert entry is not None
    assert advance_from_winner(tmp_path / A.shard_id, entry) is True
    assert advance_from_winner(tmp_path / A.shard_id, entry) is False
    assert read_checkpoint(tmp_path / A.shard_id)["completed_intervals"] == 1
    assert pending_winner(tmp_path / A.shard_id) is None


def test_next_interval_can_complete_shard_and_uses_existing_checkpoint_schema(tmp_path):
    backend, supervisor = setup(tmp_path, two=False)
    primary = supervisor.races[A].primary
    backend.ready[primary.attempt_id] = accepted(primary)
    tick(backend, supervisor, 1)
    next_key = WindowKey(A.shard_id, 1)
    supervisor.add_window(next_key, has_preceding=True, now=1)
    next_primary = supervisor.races[next_key].primary
    backend.ready[next_primary.attempt_id] = accepted(next_primary)
    tick(backend, supervisor, 2)
    cp = read_checkpoint(tmp_path / A.shard_id)
    validate_shard_checkpoint(
        cp,
        shard=synthetic_shard(A),
        expected_execution_registry_sha256="b" * 64,
        allowed_execution_modes=("annual",),
    )
    assert cp["complete"] is True and cp["next_global_iteration"] == 2
    assert advance_from_winner(tmp_path / A.shard_id, backend.entries[A]) is False


def test_durable_winner_cannot_be_replaced_and_registry_corruption_is_rejected(
    tmp_path,
):
    directory = tmp_path / A.shard_id
    cp = initialize(directory, A)
    winner = AttemptSpec(A, 0, 0)
    window = window_payload(cp, winner)
    entry = publish_winner_window(directory, cp, window)
    assert publish_winner_window(directory, cp, window) == entry
    with pytest.raises(ValueError, match="different winner"):
        publish_winner_window(directory, cp, window_payload(cp, AttemptSpec(A, 1, 6)))
    advance_from_winner(directory, entry)
    cp = read_checkpoint(directory)
    cp["windows"][0] = {**asdict(entry), "sha256": "0" * 64}
    atomic_json(directory / "checkpoint.json", cp)
    with pytest.raises(ValueError, match="different winner"):
        advance_from_winner(directory, entry)


def test_winner_transition_has_no_circular_window_digest(tmp_path):
    directory = tmp_path / A.shard_id
    cp = initialize(directory, A)
    entry = publish_winner_window(
        directory, cp, window_payload(cp, AttemptSpec(A, 0, 0))
    )
    with gzip.open(directory / entry.relative_path, "rt") as stream:
        payload = json.load(stream)
    assert entry.sha256 not in json.dumps(payload)
    assert "prior_checkpoint_sha256" in payload["checkpoint_transition"]


def test_frozen_memory_policy_does_not_silently_raise_limits():
    with pytest.raises(ValueError):
        MemoryPolicy(aggregate_limit_mib=32_768)
