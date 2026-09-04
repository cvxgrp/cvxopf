"""Deterministic recovery/termination equivalence matrix for P0."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import gzip
import json
from pathlib import Path
from typing import Iterator, Literal, Mapping, cast

import numpy as np

from cvxopf import ACAttemptRecord, HierarchicalSolveAudit, solve_hierarchical_opf
from cvxopf import _hierarchical_solver as public_solver
from cvxopf.results import extract_results

from experiments.case118_annual_hierarchy import streaming_runner
from experiments.case118_annual_hierarchy.p0_equivalence import (
    _canonical_attempt,
    _equivalent,
)
from experiments.case118_annual_hierarchy.p0_fixture import load_p0_fixture
from experiments.case118_annual_hierarchy.streaming_archive import (
    attempt_archive_payload,
)
from experiments.case118_annual_hierarchy.streaming_driver import (
    run_streaming_trajectory,
)


InjectedOutcome = Literal[
    "accepted", "solver_failure", "solver_certified_infeasible", "unusable_primal"
]

AC_REQUIRED_FIELDS = (
    "objective",
    "b",
    "b_q",
    "soc",
    "Pg",
    "Qg",
    "Vm",
    "Va_deg",
    "p_net",
    "q_net",
    "branch_p_from",
    "branch_q_from",
    "branch_p_to",
    "branch_q_to",
    "branch_s_from",
    "branch_s_to",
    "p_load",
    "q_load",
    "p_load_served",
    "q_load_served",
)


@dataclass(frozen=True)
class InjectedCase:
    name: str
    horizon_steps: int
    outcomes: Mapping[tuple[int, int], InjectedOutcome]
    expected_controlling_ordinals: tuple[int, ...]
    expected_completed_intervals: int
    expected_terminal_outcome: str | None


@dataclass(frozen=True)
class InjectedEquivalenceReport:
    case_name: str
    public_completed_intervals: int
    streaming_completed_intervals: int
    controlling_ordinals: tuple[int, ...]
    terminal_outcome: str | None
    unsuccessful_evidence_count: int
    unsuccessful_evidence_verified: bool
    mismatches: tuple[str, ...]

    @property
    def equivalent(self) -> bool:
        return not self.mismatches


INJECTED_CASES: tuple[InjectedCase, ...] = (
    InjectedCase("nominal_shifted_primary", 1, {}, (0,), 1, None),
    InjectedCase("copied_recovery", 1, {(0, 0): "solver_failure"}, (2,), 1, None),
    InjectedCase(
        "target_free_perturbation",
        1,
        {(0, 0): "solver_failure", (0, 2): "solver_failure", (0, 3): "solver_failure"},
        (4,),
        1,
        None,
    ),
    InjectedCase(
        "causal_perturbation",
        2,
        {(1, 0): "solver_failure", (1, 1): "solver_failure"},
        (0, 6),
        2,
        None,
    ),
    InjectedCase(
        "certified_infeasibility",
        1,
        {(0, ordinal): "solver_certified_infeasible" for ordinal in (0, 1, 6, 7, 8)},
        (),
        0,
        "solver_certified_infeasible",
    ),
    InjectedCase(
        "exception_then_unusable",
        1,
        {(0, 0): "solver_failure", (0, 1): "unusable_primal"},
        (6,),
        1,
        None,
    ),
    InjectedCase(
        "recovery_exhaustion",
        1,
        {(0, ordinal): "solver_failure" for ordinal in (0, 1, 6, 7, 8)},
        (),
        0,
        "solver_failure",
    ),
)


def _inject(record: ACAttemptRecord, outcome: InjectedOutcome) -> ACAttemptRecord:
    if outcome == "accepted":
        return record
    if record.audit is None:
        raise RuntimeError("injection requires an executed attempt audit")
    audit = record.audit
    if outcome == "solver_failure":
        status = None
    elif outcome == "solver_certified_infeasible":
        status = "infeasible"
    else:
        status = "user_limit"
    if record.build is None:
        raise RuntimeError("injection requires the constructed AC build")
    for variable in record.build.prob.variables():
        variable.value = None
    record.build.prob._status = status
    record.build.prob._value = None
    result = extract_results(record.build)
    missing = tuple(
        name
        for name in AC_REQUIRED_FIELDS
        if result.get(name) is None
        or not np.all(np.isfinite(np.asarray(result[name], dtype=float)))
    )
    changed = replace(
        audit,
        status=status,
        outcome=outcome,
        accepted_primal=False,
        missing_or_nonfinite_fields=missing,
        residuals={},
        exception=(
            "InjectedSolverError: frozen P0 outcome"
            if outcome == "solver_failure"
            else None
        ),
        wall_time_seconds=0.0,
        solver_num_iters=None,
        solver_setup_time_seconds=None,
        solver_solve_time_seconds=None,
    )
    return replace(
        record,
        result=result,
        audit=cast(HierarchicalSolveAudit, changed),
        supplied_executed_action=False,
    )


@contextmanager
def _instrument(
    module: object,
    outcomes: Mapping[tuple[int, int], InjectedOutcome],
) -> Iterator[None]:
    original = module._execute_attempt  # type: ignore[attr-defined]

    def wrapped(*args: object, **kwargs: object) -> ACAttemptRecord:
        record = cast(ACAttemptRecord, original(*args, **kwargs))
        outcome = outcomes.get((record.iteration, record.ordinal), "accepted")
        return _inject(record, outcome)

    module._execute_attempt = wrapped  # type: ignore[attr-defined]
    try:
        yield
    finally:
        module._execute_attempt = original  # type: ignore[attr-defined]


def _read(path: Path) -> Mapping[str, object]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return cast(Mapping[str, object], json.load(stream))


def _terminal_outcome(attempts: list[Mapping[str, object]]) -> str | None:
    executed = [item for item in attempts if item["slot_state"] == "executed"]
    if not executed:
        return "no_solver_attempt"
    audit = cast(Mapping[str, object], executed[-1]["audit"])
    return cast(str, audit["outcome"])


def _expected_tree_mismatches(
    case: InjectedCase, attempts: tuple[ACAttemptRecord, ...]
) -> list[str]:
    mismatches: list[str] = []
    controllers = {
        iteration: ordinal
        for iteration, ordinal in enumerate(case.expected_controlling_ordinals)
    }
    for attempt in attempts:
        controller = controllers.get(attempt.iteration)
        slot_one_outcome = case.outcomes.get(
            (attempt.iteration, 1), "accepted"
        )
        if controller is not None and attempt.ordinal > controller:
            expected_state = "not_needed_after_acceptance"
        elif attempt.ordinal in range(2, 6) and slot_one_outcome != "accepted":
            expected_state = "source_unavailable"
        else:
            expected_state = "executed"
        if attempt.slot_state != expected_state:
            mismatches.append(
                f"expected_slot_state[{attempt.iteration},{attempt.ordinal}]"
            )
        if expected_state == "executed":
            expected_outcome = case.outcomes.get(
                (attempt.iteration, attempt.ordinal), "accepted"
            )
            actual_outcome = None if attempt.audit is None else attempt.audit.outcome
            if actual_outcome != expected_outcome:
                mismatches.append(
                    f"expected_outcome[{attempt.iteration},{attempt.ordinal}]"
                )
    return mismatches


def _failure_evidence_mismatches(
    case: InjectedCase, attempts: tuple[ACAttemptRecord, ...]
) -> list[str]:
    mismatches: list[str] = []
    for attempt in attempts:
        expected = case.outcomes.get((attempt.iteration, attempt.ordinal), "accepted")
        if expected == "accepted":
            continue
        prefix = f"failure_evidence[{attempt.iteration},{attempt.ordinal}]"
        audit = attempt.audit
        result = attempt.result
        if audit is None or result is None or attempt.solver_evidence is None:
            mismatches.append(f"{prefix}.required_payload")
            continue
        expected_status = {
            "solver_failure": None,
            "solver_certified_infeasible": "infeasible",
            "unusable_primal": "user_limit",
        }[expected]
        if audit.status != expected_status or result.get("status") != expected_status:
            mismatches.append(f"{prefix}.status")
        derived_missing = tuple(
            name
            for name in AC_REQUIRED_FIELDS
            if result.get(name) is None
            or not np.all(np.isfinite(np.asarray(result[name], dtype=float)))
        )
        if tuple(audit.missing_or_nonfinite_fields) != derived_missing:
            mismatches.append(f"{prefix}.missing_fields")
        if audit.residuals:
            mismatches.append(f"{prefix}.residuals")
        for name in ("p_load", "q_load", "p_load_served", "q_load_served"):
            value = result.get(name)
            if value is None or not np.all(np.isfinite(np.asarray(value, dtype=float))):
                mismatches.append(f"{prefix}.fixed_load_reporting")
        if not isinstance(result.get("objective"), float) or not np.isnan(
            cast(float, result["objective"])
        ):
            mismatches.append(f"{prefix}.objective")
        if not isinstance(result.get("storage_cost"), float) or not np.isnan(
            cast(float, result["storage_cost"])
        ):
            mismatches.append(f"{prefix}.storage_cost")
        if (expected == "solver_failure") != (audit.exception is not None):
            mismatches.append(f"{prefix}.exception")
        if any(
            value is not None
            for value in (
                audit.solver_num_iters,
                audit.solver_setup_time_seconds,
                audit.solver_solve_time_seconds,
            )
        ):
            mismatches.append(f"{prefix}.solver_stats")
    return mismatches


def run_injected_equivalence(
    case: InjectedCase, directory: Path
) -> InjectedEquivalenceReport:
    """Run one frozen injected schedule through both orchestrators."""
    fixture = load_p0_fixture(6)
    inputs = replace(
        fixture.inputs,
        horizon_steps=case.horizon_steps,
        df_load_p=fixture.inputs.df_load_p.iloc[: case.horizon_steps].copy(),
        df_load_q=fixture.inputs.df_load_q.iloc[: case.horizon_steps].copy(),
    )
    fixture = replace(fixture, inputs=inputs)
    with _instrument(public_solver, case.outcomes):
        public = solve_hierarchical_opf(
            fixture.inputs, fixture.policy, fixture.solve_config
        )
    with _instrument(streaming_runner, case.outcomes):
        streaming = run_streaming_trajectory(
            directory,
            fixture.inputs,
            fixture.policy,
            fixture.solve_config,
            source_fingerprint="p0-injected-equivalence-v1",
            scenario_hash=(
                f"{fixture.case_sha256}:{fixture.load_p_sha256}:"
                f"{fixture.load_q_sha256}"
            ),
            rss_reader=lambda: 1,
        )

    entries = list(streaming.completed_window_artifacts)
    if streaming.failed_window_artifact is not None:
        entries.append(streaming.failed_window_artifact)
    windows = [_read(directory / item.relative_path) for item in entries]
    archived = [
        cast(Mapping[str, object], attempt)
        for window in windows
        for attempt in cast(list[object], window["attempts"])
    ]
    mismatches: list[str] = []
    mismatches.extend(_expected_tree_mismatches(case, public.ac_attempts))
    evidence_mismatches = _failure_evidence_mismatches(case, public.ac_attempts)
    mismatches.extend(evidence_mismatches)
    if len(public.ac_attempts) != len(archived):
        mismatches.append("attempt_count")
    for index, (left, right) in enumerate(
        zip(public.ac_attempts, archived, strict=False)
    ):
        projected = attempt_archive_payload(
            left, result_dimensions=fixture.result_dimensions
        )
        if not _equivalent(
            _canonical_attempt(projected), _canonical_attempt(right)
        ):
            mismatches.append(f"attempt[{index}]")

    public_controllers = tuple(
        attempt.ordinal
        for attempt in public.ac_attempts
        if attempt.supplied_executed_action
    )
    streaming_controllers = tuple(
        int(cast(int, attempt["ordinal"]))
        for attempt in archived
        if attempt["supplied_executed_action"] is True
    )
    if public_controllers != streaming_controllers:
        mismatches.append("controlling_ordinals")
    if public_controllers != case.expected_controlling_ordinals:
        mismatches.append("expected_controlling_ordinals")
    if public.completed_intervals != streaming.completed_intervals:
        mismatches.append("completed_intervals")
    if public.completed_intervals != case.expected_completed_intervals:
        mismatches.append("expected_completed_intervals")
    if public.termination_reason != streaming.termination_reason:
        mismatches.append("termination_reason")
    terminal = None if public.completed else _terminal_outcome(archived)
    if terminal != case.expected_terminal_outcome:
        mismatches.append("terminal_outcome")
    return InjectedEquivalenceReport(
        case_name=case.name,
        public_completed_intervals=public.completed_intervals,
        streaming_completed_intervals=streaming.completed_intervals,
        controlling_ordinals=public_controllers,
        terminal_outcome=terminal,
        unsuccessful_evidence_count=len(case.outcomes),
        unsuccessful_evidence_verified=not evidence_mismatches,
        mismatches=tuple(mismatches),
    )


__all__ = [
    "INJECTED_CASES",
    "InjectedCase",
    "InjectedEquivalenceReport",
    "run_injected_equivalence",
]
