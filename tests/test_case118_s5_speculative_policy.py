"""No numerical solves: policy clocks, races, leases, and selection evidence."""

from __future__ import annotations

import math

import pytest

from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    AttemptSpec,
    Completion,
    HelperQueue,
    WindowKey,
    WindowRace,
    normalized_violation,
    select_uncapped_replay,
)
from experiments.case118_annual_hierarchy.streaming_schema import perturbation_seed


KEY = WindowKey("s4b-shard-004", 3000)


def race(*, preceding: bool = True) -> WindowRace:
    return WindowRace(KEY, has_preceding=preceding)


def accept(attempt: AttemptSpec) -> Completion:
    return Completion(attempt, "accepted", complete_x0_retained=True)


def test_primary_continues_at_five_minutes() -> None:
    window = race()
    assert not window.needs_help(299.999)
    assert window.needs_help(300)
    assert window.needs_help(500)
    assert window.primary.budget_seconds is None
    assert window.active_attempts == (window.primary,)


def test_failed_primary_eligible_immediately() -> None:
    window = race()
    window.complete_batch([Completion(window.primary, "rejected")])
    assert window.needs_help(1)


@pytest.mark.parametrize("elapsed", [-1, math.nan, math.inf, True])
def test_invalid_clock_rejected(elapsed: float) -> None:
    with pytest.raises(ValueError):
        race().needs_help(elapsed)


def test_causal_starts_precede_target_free_with_legacy_seeds() -> None:
    window = race()
    for order, slot, scale in ((1, 6, 1e-4), (2, 7, 1e-3), (3, 8, 1e-2)):
        helper = window.next_helper()
        assert helper is not None
        assert (helper.order, helper.source_slot, helper.scale) == (order, slot, scale)
        assert helper.seed == perturbation_seed(KEY.iteration, slot)
        assert helper.hard_target and helper.budget_seconds == 300
        window.complete_batch([Completion(helper, "timeout", True)])
    target_free = window.next_helper()
    assert target_free is not None and target_free.source_slot == 1
    assert not target_free.hard_target


def test_shard_start_skips_causal_then_target_free_can_never_win() -> None:
    window = race(preceding=False)
    target_free = window.next_helper()
    assert target_free is not None and target_free.order == 4
    assert window.unavailable == {
        1: "preceding_controller_unavailable",
        2: "preceding_controller_unavailable",
        3: "preceding_controller_unavailable",
    }
    assert window.complete_batch([accept(target_free)]) is None
    copied = window.next_helper()
    assert copied is not None and copied.source_slot == 2
    assert window.complete_batch([accept(copied)]) == copied
    assert window.cancel_after_winner == (window.primary,)


def test_speculative_metadata_matches_production_start_registry() -> None:
    from experiments.case118_annual_hierarchy.streaming_runner import _p0_registry

    production = _p0_registry(KEY.iteration, trajectory_start=2965)
    window = race()
    for _ in range(8):
        helper = window.next_helper()
        assert helper is not None
        source = production[helper.source_slot]
        assert (helper.scale, helper.seed) == (source.scale, source.seed)
        if helper.source_slot >= 6:
            assert source.transformation == "perturb_causal"
        outcome = (
            accept(helper)
            if helper.source_slot == 1
            else Completion(helper, "rejected")
        )
        window.complete_batch([outcome])


def test_target_free_failure_skips_dependents_and_replays_causal() -> None:
    window = race()
    causal = window.next_helper()
    assert causal is not None
    window.complete_batch([Completion(causal, "timeout", True)])
    for _ in range(3):
        helper = window.next_helper()
        assert helper is not None
        window.complete_batch([Completion(helper, "rejected")])
    secondary = window.next_helper()
    assert secondary is not None and secondary.order == 9
    assert secondary.replay_of == causal.attempt_id
    window.complete_batch([Completion(secondary, "rejected")])
    replay = window.next_helper()
    assert replay is not None
    assert replay.order == 12 and replay.replay_of is None
    assert replay.seed is not None and replay.scale is not None
    assert replay.budget_seconds is None
    assert window.unavailable[11] == "already_exercised_by_secondary"
    window.complete_batch([Completion(replay, "rejected")])
    for _ in range(1):
        helper = window.next_helper()
        assert helper is not None and helper.budget_seconds is None
        window.complete_batch([Completion(helper, "rejected")])
    target_free = window.next_helper()
    assert target_free is not None and target_free.source_slot == 1
    window.complete_batch([Completion(target_free, "rejected")])
    assert window.next_helper() is None
    window.complete_batch([Completion(window.primary, "rejected")])
    assert window.exhausted


def test_target_free_timeout_gets_thirty_minute_retry_before_dependents() -> None:
    window = race()
    for _ in range(3):
        helper = window.next_helper()
        assert helper is not None
        window.complete_batch([Completion(helper, "rejected", True, 1.0)])
    first = window.next_helper()
    assert first is not None and (first.order, first.source_slot) == (4, 1)
    window.complete_batch([Completion(first, "timeout", True)])
    retry = window.next_helper()
    assert retry is not None
    assert (retry.order, retry.source_slot, retry.replay_of) == (
        10,
        1,
        first.attempt_id,
    )
    assert retry.budget_seconds == 1800
    window.complete_batch([accept(retry)])
    copied = window.next_helper()
    assert copied is not None and copied.source_slot == 2


def test_final_target_free_acceptance_unlocks_uncapped_dependents() -> None:
    window = race()
    for _ in range(3):
        helper = window.next_helper()
        assert helper is not None
        window.complete_batch([Completion(helper, "rejected", True, 1.0)])
    first = window.next_helper()
    assert first is not None
    window.complete_batch([Completion(first, "timeout", True)])
    retry = window.next_helper()
    assert retry is not None
    window.complete_batch([Completion(retry, "timeout", True)])
    secondary = window.next_helper()
    assert secondary is not None and secondary.order == 9
    window.complete_batch([Completion(secondary, "rejected")])
    for _ in range(2):
        helper = window.next_helper()
        assert helper is not None
        window.complete_batch([Completion(helper, "rejected")])
    final_free = window.next_helper()
    assert final_free is not None
    assert (final_free.order, final_free.source_slot, final_free.budget_seconds) == (
        14,
        1,
        None,
    )
    window.complete_batch([accept(final_free)])
    copied = window.next_helper()
    assert copied is not None
    assert (copied.order, copied.source_slot, copied.budget_seconds) == (15, 2, None)


def test_final_sweep_uses_failed_primary_lane_while_secondary_continues() -> None:
    window = race()
    for slot in (6, 7, 8, 1, 2, 3, 4, 5):
        helper = window.next_helper()
        assert helper is not None and helper.source_slot == slot
        outcome = (
            accept(helper) if slot == 1 else Completion(helper, "rejected", True, 1.0)
        )
        window.complete_batch([outcome])
    secondary = window.next_helper()
    assert secondary is not None and secondary.order == 9
    window.complete_batch([Completion(window.primary, "rejected")])
    final = window.next_final()
    assert final is not None and final.order == 12
    assert final.source_slot != secondary.source_slot
    assert {item.order for item in window.active_attempts} == {9, 12}


def test_two_final_lanes_allocate_distinct_sources_and_wait_for_target_free() -> None:
    window = race()
    for slot in (6, 7, 8):
        helper = window.next_helper()
        assert helper is not None and helper.source_slot == slot
        window.complete_batch([Completion(helper, "rejected")])
    target_free = window.next_helper()
    assert target_free is not None and target_free.source_slot == 1
    window.complete_batch([Completion(target_free, "rejected")])
    window.complete_batch([Completion(window.primary, "rejected")])
    first = window.next_helper()
    second = window.next_final()
    assert first is not None and second is not None
    assert (first.order, second.order) == (11, 12)
    window.complete_batch([Completion(first, "rejected")])
    third = window.next_final()
    assert third is not None and third.order == 13
    window.complete_batch([Completion(second, "rejected")])
    window.complete_batch([Completion(third, "rejected")])
    final_target_free = window.next_final()
    assert final_target_free is not None and final_target_free.order == 14
    assert window.next_final() is None
    assert window.waiting_for_final_source


def test_full_helper_sequence_finite_and_unaccepted_target_free_not_replayed() -> None:
    window = race()
    for slot in (6, 7, 8, 1, 2, 3, 4, 5):
        helper = window.next_helper()
        assert helper is not None and helper.source_slot == slot
        outcome = accept(helper) if slot == 1 else Completion(helper, "rejected")
        window.complete_batch([outcome])
    for slot in (6, 7, 8, 1, 2, 3, 4, 5):
        helper = window.next_helper()
        assert helper is not None and helper.source_slot == slot
        outcome = accept(helper) if slot == 1 else Completion(helper, "rejected")
        window.complete_batch([outcome])
    assert window.next_helper() is None
    assert window.next_helper() is None
    window.complete_batch([Completion(window.primary, "rejected")])
    assert window.exhausted


@pytest.mark.parametrize("primary_first", [True, False])
def test_same_batch_prefers_primary_regardless_of_list_order(
    primary_first: bool,
) -> None:
    window = race()
    helper = window.next_helper()
    assert helper is not None
    completions = [accept(window.primary), accept(helper)]
    if not primary_first:
        completions.reverse()
    assert window.complete_batch(completions) == window.primary
    assert len(window.completed) == 2
    assert not window.cancel_after_winner
    assert window.next_helper() is None


def test_first_accepted_batch_wins_even_if_primary_returns_later() -> None:
    window = race()
    helper = window.next_helper()
    assert helper is not None
    assert window.complete_batch([accept(helper)]) == helper
    assert window.cancel_after_winner == (window.primary,)
    assert window.complete_batch([accept(window.primary)]) == helper
    assert not window.needs_help(1000)


def test_primary_win_requests_helper_cleanup_and_no_more_work() -> None:
    window = race()
    helper = window.next_helper()
    assert helper is not None
    assert window.complete_batch([accept(window.primary)]) == window.primary
    assert window.cancel_after_winner == (helper,)
    window.complete_batch([Completion(helper, "canceled", True)])
    assert not window.cancel_after_winner
    assert window.next_helper() is None


def test_active_helper_must_complete_before_another_is_issued() -> None:
    window = race()
    window.next_helper()
    with pytest.raises(ValueError, match="still active"):
        window.next_helper()


def test_invalid_batch_does_not_partially_accept_winner() -> None:
    window = race()
    unlaunched = AttemptSpec(KEY, 2, 7)
    with pytest.raises(ValueError, match="launched"):
        window.complete_batch([accept(window.primary), accept(unlaunched)])
    assert not window.completed and window.winner is None
    with pytest.raises(ValueError, match="duplicate"):
        window.complete_batch([accept(window.primary), accept(window.primary)])
    window.complete_batch([accept(window.primary)])
    with pytest.raises(ValueError, match="already completed"):
        window.complete_batch([accept(window.primary)])


def test_replay_ranks_complete_residual_before_objective_and_order() -> None:
    a, b, c = (AttemptSpec(KEY, i, i + 5) for i in range(1, 4))
    replay = select_uncapped_replay(
        [
            Completion(a, "rejected", True, 20, -100),
            Completion(b, "rejected", True, 2, 100),
            Completion(c, "rejected", True, 2, 200),
        ]
    )
    assert replay is not None and replay.replay_of == b.attempt_id
    assert replay.attempt_id != b.attempt_id


def test_replay_ties_and_unavailable_objectives() -> None:
    a, b, c = (AttemptSpec(KEY, i, i + 5) for i in range(1, 4))
    replay = select_uncapped_replay(
        [
            Completion(c, "rejected", True, 2, 100),
            Completion(b, "rejected", True, 2, 100),
            Completion(a, "rejected", True, 2, math.nan),
        ]
    )
    assert replay is not None and replay.replay_of == b.attempt_id


def test_timeout_selection_needs_complete_x0_and_never_uses_target_free() -> None:
    a, b = AttemptSpec(KEY, 1, 6), AttemptSpec(KEY, 2, 7)
    tf = AttemptSpec(KEY, 4, 1)
    replay = select_uncapped_replay(
        [
            Completion(a, "timeout"),
            Completion(b, "timeout", True),
            Completion(tf, "timeout", True),
        ]
    )
    assert replay is not None and replay.replay_of == b.attempt_id
    assert select_uncapped_replay([Completion(tf, "timeout", True)]) is None
    assert select_uncapped_replay([Completion(a, "rejected", True)]) is None


def test_duplicate_and_cross_window_replay_candidates_rejected() -> None:
    item = Completion(AttemptSpec(KEY, 1, 6), "timeout", True)
    with pytest.raises(ValueError, match="duplicate"):
        select_uncapped_replay([item, item])
    other = Completion(AttemptSpec(WindowKey("other", 3000), 1, 6), "timeout", True)
    with pytest.raises(ValueError, match="one window"):
        select_uncapped_replay([item, other])


def test_normalization_preserves_units_and_uses_worst_family() -> None:
    limits = {"active_pu": 1e-6, "terminal_mwh": 1e-3, "bound_mw": 1e-4}
    residuals = {"active_pu": 2e-6, "terminal_mwh": 1e-3, "bound_mw": 3e-4}
    assert normalized_violation(residuals, limits) == pytest.approx(3)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -1, True])
def test_bad_or_missing_ranking_residuals_are_not_zero(bad: float) -> None:
    assert normalized_violation({"x": bad}, {"x": 1}) is None
    assert normalized_violation({}, {"x": 1}) is None


def test_zero_tolerance_has_explicit_exact_semantics() -> None:
    assert normalized_violation({"x": 0}, {"x": 0}) == 0
    assert normalized_violation({"x": 1}, {"x": 0}) == math.inf
    with pytest.raises(ValueError):
        normalized_violation({}, {})
    with pytest.raises(ValueError):
        normalized_violation({"x": 0}, {"x": -1})


def test_helper_queue_fair_turns_memory_gate_and_lease_until_reap() -> None:
    queue = HelperQueue()
    other = WindowKey("s4b-shard-005", 3800)
    queue.request(KEY, eligible_at=300)
    queue.request(other, eligible_at=301)
    assert queue.acquire(memory_admitted=False) is None
    assert queue.acquire(memory_admitted=True) == KEY
    queue.cancel_waiting(KEY)  # winner/cancellation is not proof of process exit
    assert queue.acquire(memory_admitted=True) is None
    with pytest.raises(ValueError):
        queue.release_after_reap(other)
    with pytest.raises(ValueError):
        queue.request(KEY, eligible_at=600)
    queue.release_after_reap(KEY)
    queue.request(KEY, eligible_at=600)
    assert queue.acquire(memory_admitted=True) == other
    queue.release_after_reap(other)
    assert queue.acquire(memory_admitted=True) == KEY


def test_queue_ties_are_stable_and_repeat_requests_keep_position() -> None:
    queue = HelperQueue()
    other = WindowKey("s4b-shard-005", 2999)
    queue.request(other, eligible_at=300)
    queue.request(KEY, eligible_at=300)
    queue.request(KEY, eligible_at=800)
    assert queue.acquire(memory_admitted=True) == KEY
    queue.release_after_reap(KEY)
    queue.cancel_waiting(other)
    assert queue.acquire(memory_admitted=True) is None


@pytest.mark.parametrize(
    "order,slot,replay",
    [(0, 1, None), (1, 3, None), (9, 1, "x"), (9, 6, None), (10, 6, "x")],
)
def test_invalid_attempt_identity_rejected(
    order: int, slot: int, replay: str | None
) -> None:
    with pytest.raises(ValueError):
        AttemptSpec(KEY, order, slot, replay)


def test_completion_does_not_treat_timeout_as_returned_primal() -> None:
    spec = AttemptSpec(KEY, 1, 6)
    with pytest.raises(ValueError, match="returned rejected"):
        Completion(spec, "timeout", True, 0)
    with pytest.raises(ValueError, match="complete x0"):
        Completion(spec, "accepted")
