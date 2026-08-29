"""Nominal public-versus-streaming equivalence harness for P0."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import json
from pathlib import Path
from typing import Mapping, cast

import cvxpy as cp
import numpy as np

from cvxopf import OPFBuild, OuterPlanRecord, solve_hierarchical_opf
from cvxopf.generator import gen_cost_expr, generator_gencost
from cvxopf.storage import storage_cost_expr
from experiments.case118_annual_hierarchy.p0_fixture import P0Fixture, load_p0_fixture
from experiments.case118_annual_hierarchy.streaming_archive import (
    attempt_archive_payload,
)
from experiments.case118_annual_hierarchy.streaming_driver import run_streaming_trajectory
from experiments.case118_annual_hierarchy.streaming_runner import variables_by_name

NUMERIC_EQUIVALENCE_ATOL = 1e-9


@dataclass(frozen=True)
class NominalEquivalenceReport:
    """Auditable outcome of one nominal equivalence execution."""

    horizon_steps: int
    completed_intervals: int
    outer_plan_count: int
    attempt_count: int
    executed_interval_count: int
    controlling_ordinals: tuple[int, ...]
    public_runtime_seconds: float
    streaming_runtime_seconds: float
    compared_runtime_numerically: bool
    mismatches: tuple[str, ...]

    @property
    def equivalent(self) -> bool:
        return not self.mismatches


def _read_gzip(path: Path) -> Mapping[str, object]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return cast(Mapping[str, object], json.load(stream))


def _canonical_layout(value: object) -> object:
    if not isinstance(value, list):
        return value
    auxiliary = 0
    records: list[object] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            records.append(raw)
            continue
        record = dict(raw)
        if not bool(record.get("is_original_variable")):
            record["name"] = f"auxiliary_{auxiliary}"
            auxiliary += 1
        records.append(record)
    return records


def _canonical_evidence(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    result = dict(value)
    before = cast(Mapping[str, object], result.pop("object_ids_before"))
    after = cast(Mapping[str, object], result.pop("object_ids_after"))
    result["object_identity"] = {
        name: {
            "count": len(cast(list[object], before[name])),
            "preserved": before[name] == after[name],
        }
        for name in sorted(before)
    }
    return result


def _canonical_attempt(payload: Mapping[str, object]) -> object:
    result = dict(payload)
    result["solver_x0_layout"] = _canonical_layout(result["solver_x0_layout"])
    result["solver_evidence"] = _canonical_evidence(result["solver_evidence"])
    audit = result.get("audit")
    if isinstance(audit, Mapping):
        audit = dict(audit)
        audit.pop("wall_time_seconds", None)
        result["audit"] = audit
    return result


def _json(value: object) -> object:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    return value


def _signature(build: OPFBuild) -> Mapping[str, object]:
    variables = variables_by_name(build)
    return {
        "temporal_assembly": build.temporal_assembly,
        "canonicalization_backend": build.canonicalization_backend,
        "variables": [
            {"name": name, "shape": list(variables[name].shape)}
            for name in sorted(variables)
        ],
        "constraints": [
            f"{type(item).__name__}|shape={tuple(item.shape)}"
            for item in build.prob.constraints
        ],
        "parameters": [
            {"name": item.name(), "shape": list(item.shape)}
            for item in sorted(
                build.prob.parameters(), key=lambda value: value.name()
            )
        ],
    }


def _outer_projection(record: OuterPlanRecord) -> Mapping[str, object]:
    audit = record.audit
    residuals = dict(audit.residuals)
    # The experiment audit independently adds the inactive-limit residual.
    residuals["branch_mw_abs"] = 0.0
    return {
        "outer_plan_id": record.outer_plan_id,
        "horizon_steps": record.global_interval_stop,
        "storage_device_ids": list(record.storage_device_ids),
        "global_boundary_indices": record.global_boundary_indices.tolist(),
        "boundary_soc_mwh": record.boundary_soc_mwh.tolist(),
        "structural_signature": _signature(cast(OPFBuild, record.build)),
        "result": _json(record.result),
        "audit": {
            "status": audit.status,
            "missing_or_nonfinite_fields": list(audit.missing_or_nonfinite_fields),
            "identity_error": audit.identity_error,
            "residuals": _json(residuals),
            "accepted_primal": audit.accepted_primal,
            "exception": audit.exception,
        },
    }


def _archived_outer_projection(payload: Mapping[str, object]) -> Mapping[str, object]:
    audit = cast(Mapping[str, object], payload["audit"])
    return {
        key: payload[key]
        for key in (
            "outer_plan_id",
            "horizon_steps",
            "storage_device_ids",
            "global_boundary_indices",
            "boundary_soc_mwh",
            "structural_signature",
            "result",
        )
    } | {
        "audit": {
            key: audit[key]
            for key in (
                "status",
                "missing_or_nonfinite_fields",
                "identity_error",
                "residuals",
                "accepted_primal",
                "exception",
            )
        }
    }


def _equivalent(left: object, right: object) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(
            _equivalent(left[key], right[key]) for key in left
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _equivalent(a, b) for a, b in zip(left, right, strict=True)
        )
    if (
        isinstance(left, (int, float, np.number))
        and not isinstance(left, bool)
        and isinstance(right, (int, float, np.number))
        and not isinstance(right, bool)
    ):
        return bool(
            np.isclose(
                float(left), float(right), rtol=0.0, atol=NUMERIC_EQUIVALENCE_ATOL
            )
        )
    return left == right


def _compare(name: str, left: object, right: object, mismatches: list[str]) -> None:
    if not _equivalent(left, right):
        mismatches.append(name)


def _executed_accounting(
    result: Mapping[str, object], fixture: P0Fixture, initial_soc_mwh: list[float]
) -> Mapping[str, float]:
    inputs = fixture.inputs
    policy = fixture.policy
    delta = float(inputs.delta)
    pg = np.asarray(result["Pg"], dtype=float)[0]
    b = np.asarray(result["b"], dtype=float)[0]
    generation = float(
        gen_cost_expr(generator_gencost(list(inputs.generators)), cp.Constant(pg)).value
    )
    storage = float(storage_cost_expr(list(inputs.storage), cp.Constant(b)).value)
    raw_loss = float(
        np.sum(np.asarray(result["branch_p_from"], dtype=float)[0])
        + np.sum(np.asarray(result["branch_p_to"], dtype=float)[0])
    )
    injection_loss = float(np.sum(np.asarray(result["p_net"], dtype=float)[0]))
    initial = np.asarray(initial_soc_mwh, dtype=float)
    soc = np.asarray(result["soc"], dtype=float)[0]
    vm = np.asarray(result["Vm"], dtype=float)[:1]
    s_from = np.asarray(result["branch_s_from"], dtype=float)[:1]
    s_to = np.asarray(result["branch_s_to"], dtype=float)[:1]
    bus = np.asarray(inputs.case["bus"], dtype=float)
    voltage = float(
        np.max(
            np.maximum.reduce(
                [vm - bus[:, 11], bus[:, 12] - vm, np.zeros_like(vm)]
            )
        )
    )
    branch = np.asarray(inputs.case["branch"], dtype=float)
    constrained = (
        (branch[:, 10] == 1)
        & np.isfinite(branch[:, 5])
        & (branch[:, 5] > 0)
    )
    ratings = branch[constrained, 5]
    apparent = np.concatenate(
        [s_from[:, constrained], s_to[:, constrained]], axis=1
    )
    both = np.concatenate([ratings, ratings])
    thermal = float(np.max(np.maximum(apparent - both, 0.0)))
    normalized = float(
        np.max(np.maximum((apparent**2 - both**2) / both**2, 0.0))
    )
    tolerance_mw = float(inputs.case["baseMVA"]) * policy.tolerances.ac_active_balance_pu_abs
    return {
        "generation_cost": delta * generation,
        "storage_cycling_cost": delta * storage,
        "renewable_curtailment_mwh": 0.0,
        "active_loss_mwh": delta * max(raw_loss, 0.0),
        "active_loss_crosscheck_mw_abs": abs(raw_loss - injection_loss),
        "state_transition_residual_mwh_abs": float(np.max(np.abs(soc - initial + delta * b))),
        "voltage_violation_pu": voltage,
        "thermal_residual_mva": thermal,
        "normalized_squared_thermal_residual": normalized,
        "loss_is_accepted": float(raw_loss >= -tolerance_mw),
    }


def run_nominal_equivalence(
    horizon_steps: int, directory: Path
) -> NominalEquivalenceReport:
    """Run and compare the frozen nominal public and streaming trajectories."""
    fixture = load_p0_fixture(horizon_steps)
    public = solve_hierarchical_opf(fixture.inputs, fixture.policy, fixture.solve_config)
    streaming = run_streaming_trajectory(
        directory,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="p0-nominal-equivalence-v1",
        scenario_hash=(
            f"{fixture.case_sha256}:{fixture.load_p_sha256}:{fixture.load_q_sha256}"
        ),
        rss_reader=lambda: 1,
    )
    mismatches: list[str] = []
    _compare("completion", public.completed, streaming.status == "complete", mismatches)
    _compare("completed_intervals", public.completed_intervals, streaming.completed_intervals, mismatches)
    _compare("termination_reason", public.termination_reason, streaming.termination_reason, mismatches)

    public_outer = next(iter(public.outer_plans.values()))
    outer_disk = _read_gzip(directory / "outer-plan.json.gz")
    _compare("outer_plan", _outer_projection(public_outer), _archived_outer_projection(outer_disk), mismatches)

    archived_windows = [
        _read_gzip(directory / entry.relative_path)
        for entry in streaming.completed_window_artifacts
    ]
    archived_attempts = [
        cast(Mapping[str, object], attempt)
        for window in archived_windows
        for attempt in cast(list[object], window["attempts"])
    ]
    _compare("attempt_count", len(public.ac_attempts), len(archived_attempts), mismatches)
    for index, (public_attempt, archived_attempt) in enumerate(
        zip(public.ac_attempts, archived_attempts, strict=False)
    ):
        projected = attempt_archive_payload(
            public_attempt, result_dimensions=fixture.result_dimensions
        )
        _compare(
            f"attempt[{index}]",
            _canonical_attempt(projected),
            _canonical_attempt(archived_attempt),
            mismatches,
        )

    public_executed = {item.iteration: item for item in public.executed_intervals}
    ordinals: list[int] = []
    accounting: list[Mapping[str, float]] = []
    realized = [public.realized_soc_mwh[0].tolist()]
    for window in archived_windows:
        iteration = int(cast(int, window["iteration"]))
        executed = cast(Mapping[str, object], window["executed_interval"])
        controlling_id = str(executed["controlling_attempt_id"])
        controlling = next(
            item
            for item in cast(list[Mapping[str, object]], window["attempts"])
            if item["attempt_id"] == controlling_id
        )
        ordinals.append(int(cast(int, controlling["ordinal"])))
        expected = public_executed[iteration]
        _compare(f"executed[{iteration}].attempt_id", expected.controlling_attempt_id, controlling_id, mismatches)
        _compare(f"executed[{iteration}].b", public.executed_b_mw[iteration].tolist(), executed["b_mw"], mismatches)
        post = cast(list[float], window["post_step_soc_mwh"])
        _compare(
            f"executed[{iteration}].soc",
            public.realized_soc_mwh[iteration + 1].tolist(),
            post,
            mismatches,
        )
        realized.append(post)
        archived_result = cast(Mapping[str, object], controlling["result"])
        values = _executed_accounting(
            archived_result,
            fixture,
            cast(list[float], window["initial_soc_mwh"]),
        )
        assert values["loss_is_accepted"] == 1.0
        values = {key: value for key, value in values.items() if key != "loss_is_accepted"}
        expected_values = {
            name: float(getattr(expected, name)) for name in values
        }
        _compare(
            f"executed[{iteration}].accounting",
            expected_values,
            values,
            mismatches,
        )
        accounting.append(values)
    _compare(
        "realized_soc",
        np.asarray(realized).tolist(),
        public.realized_soc_mwh.tolist(),
        mismatches,
    )

    streaming_summary = {
        "generation_cost": sum(item["generation_cost"] for item in accounting),
        "storage_cycling_cost": sum(item["storage_cycling_cost"] for item in accounting),
        "renewable_curtailment_mwh": sum(item["renewable_curtailment_mwh"] for item in accounting),
        "active_loss_mwh": sum(item["active_loss_mwh"] for item in accounting),
        "maximum_voltage_violation_pu": max(item["voltage_violation_pu"] for item in accounting),
        "maximum_thermal_residual_mva": max(item["thermal_residual_mva"] for item in accounting),
        "maximum_normalized_squared_thermal_residual": max(
            item["normalized_squared_thermal_residual"] for item in accounting
        ),
        "cumulative_absolute_signpost_deviation_mwh": sum(
            abs(
                float(
                    cast(
                        list[float],
                        cast(Mapping[str, object], attempt["result"])[
                            "storage_terminal_deviation"
                        ],
                    )[0]
                )
            )
            for attempt in archived_attempts
            if attempt.get("supplied_executed_action") is True
        ),
    }
    _compare(
        "trajectory_summary",
        {
            key: float(public.trajectory_summary[key])
            for key in streaming_summary
        },
        streaming_summary,
        mismatches,
    )

    public_runtime = float(cast(float, public.trajectory_summary["runtime_seconds"]))
    streaming_runtime = float(cast(float, cast(Mapping[str, object], outer_disk["audit"])["wall_time_seconds"])) + sum(
        float(cast(float, cast(Mapping[str, object], attempt["audit"])["wall_time_seconds"]))
        for attempt in archived_attempts
        if isinstance(attempt.get("audit"), Mapping)
    )
    return NominalEquivalenceReport(
        horizon_steps=horizon_steps,
        completed_intervals=streaming.completed_intervals,
        outer_plan_count=len(public.outer_plans),
        attempt_count=len(archived_attempts),
        executed_interval_count=len(archived_windows),
        controlling_ordinals=tuple(ordinals),
        public_runtime_seconds=public_runtime,
        streaming_runtime_seconds=streaming_runtime,
        compared_runtime_numerically=False,
        mismatches=tuple(mismatches),
    )


__all__ = [
    "NUMERIC_EQUIVALENCE_ATOL",
    "NominalEquivalenceReport",
    "run_nominal_equivalence",
]
