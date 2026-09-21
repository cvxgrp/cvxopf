"""Build-free candidate and selected-window science for speculative S5.

Selection remains a coordinator decision; IPOPT status alone never supplies an
action. The legacy physical audit is reconstructed from retained public arrays.
The additional box residuals rank rejected starts only, not a new acceptance
policy. This study fixture has fixed loads and no HVDC.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, cast

import numpy as np

from cvxopf import HierarchicalInputs, HierarchicalPolicy, OPFBuild
from experiments.case118_annual_hierarchy.audit import audit_probe
from experiments.case118_annual_hierarchy import streaming_runner as streaming
from experiments.case118_annual_hierarchy.s5_speculative_attempt import (
    SelectedController,
    TargetFreeSource,
    load_retained_start,
)
from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    AttemptSpec,
    Completion,
    POLICY_NAME,
    WindowKey,
    normalized_violation,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    residual_tolerances,
    result_dimensions,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    AC_COMMON_RESIDUAL_NAMES,
    RESIDUAL_TOLERANCE_FIELDS,
    _validate_executed_evidence,
    attempt_id,
    sha256_path,
)


def invocation(value: Mapping[str, Any]) -> AttemptSpec:
    return AttemptSpec(
        WindowKey(**value["window"]),
        value["order"],
        value["source_slot"],
        value.get("replay_of"),
    )


def result_shapes(inputs: HierarchicalInputs, steps: int) -> dict[str, tuple[int, ...]]:
    dims = result_dimensions(inputs)
    groups = {
        "storage": ("b", "b_q", "soc"),
        "generators": ("Pg", "Qg"),
        "buses": ("Vm", "Va_deg", "p_net", "q_net"),
        "branches": (
            "branch_p_from",
            "branch_q_from",
            "branch_p_to",
            "branch_q_to",
            "branch_s_from",
            "branch_s_to",
        ),
        "loads": ("p_load", "q_load", "p_load_served", "q_load_served"),
        "nondispatchable": ("p_nd", "q_nd", "curtailment"),
    }
    return {
        name: (steps, dims[group])
        for group, names in groups.items()
        for name in names
        if dims[group] or group != "nondispatchable"
    }


def box_residuals(
    inputs: HierarchicalInputs, result: Mapping[str, Any], iteration: int = 0
) -> dict[str, float]:
    """Full present-family boxes, in pu for power and MWh for stored energy."""
    base = float(inputs.case["baseMVA"])

    def excess(*values: np.ndarray) -> float:
        return max(0.0, *(float(np.max(value, initial=0.0)) for value in values))

    def array(name: str) -> np.ndarray:
        return np.asarray(result[name], dtype=float)

    pg, qg, b, bq, soc = (array(name) for name in ("Pg", "Qg", "b", "b_q", "soc"))
    active = list(inputs.generators)
    values = {
        "generator_p_box_pu_abs": excess(
            np.array([g.p_min_mw if g.status else 0 for g in active]) - pg,
            pg - np.array([g.p_max_mw if g.status else 0 for g in active]),
        )
        / base,
        "generator_q_box_pu_abs": excess(
            np.array([g.q_min_mvar if g.status else 0 for g in active]) - qg,
            qg - np.array([g.q_max_mvar if g.status else 0 for g in active]),
        )
        / base,
        "storage_soc_box_mwh_abs": excess(
            -soc, soc - np.array([s.capacity for s in inputs.storage])
        ),
        "storage_circle_pu_abs": excess(
            np.hypot(b, bq)
            - np.array([s.apparent_power_rating for s in inputs.storage])
        )
        / base,
        "fixed_load_service_pu_abs": excess(
            np.abs(array("p_load_served") - array("p_load")),
            np.abs(array("q_load_served") - array("q_load")),
        )
        / base,
    }
    if inputs.nondispatchable:
        p, q, curtailed = (array(name) for name in ("p_nd", "q_nd", "curtailment"))
        available = (
            np.array([unit.p_available for unit in inputs.nondispatchable])
            if inputs.df_nd is None
            else inputs.df_nd.loc[
                :, [str(unit.device_id) for unit in inputs.nondispatchable]
            ]
            .iloc[iteration : iteration + p.shape[0]]
            .to_numpy(dtype=float)
        )
        values.update(
            {
                "nd_p_box_pu_abs": excess(-p, -curtailed, p - available) / base,
                "nd_curtailment_identity_pu_abs": excess(
                    np.abs(available - p - curtailed)
                )
                / base,
                "nd_circle_pu_abs": excess(
                    np.hypot(p, q)
                    - np.array(
                        [n.apparent_power_rating for n in inputs.nondispatchable]
                    )
                )
                / base,
            }
        )
    return values


@dataclass(frozen=True)
class AuditedCandidate:
    payload: Mapping[str, Any]
    completion: Completion

    @property
    def spec(self) -> AttemptSpec:
        return self.completion.attempt

    def target_free_source(self) -> TargetFreeSource:
        if self.completion.outcome != "accepted" or self.spec.hard_target:
            raise ValueError("candidate is not an accepted target-free source")
        return TargetFreeSource(
            self.spec, self.payload["request_sha256"], self.values()
        )

    def values(self) -> dict[str, np.ndarray]:
        values = self.payload["solution_values"]
        assigned = self.payload["attempt"]["assigned_start"]
        if not isinstance(values, Mapping) or set(values) != set(assigned):
            raise ValueError("accepted candidate lacks complete named solution")
        result = {key: np.asarray(value, dtype=float) for key, value in values.items()}
        if any(
            not np.isfinite(value).all()
            or value.shape != np.asarray(assigned[key]).shape
            for key, value in result.items()
        ):
            raise ValueError("invalid named solution shape/values")
        return result

    def selected_source(self) -> SelectedController:
        if self.completion.outcome != "accepted" or not self.spec.hard_target:
            raise ValueError("candidate cannot be a controller")
        item = self.payload["attempt"]
        result = item["result"]
        source = streaming.CausalControllerSource(
            attempt_id=item["attempt_id"],
            ordinal=item["ordinal"],
            role=item["role"],
            iteration=item["iteration"],
            global_interval_start=item["global_interval_start"],
            global_interval_stop=item["global_interval_stop"],
            outer_plan_id=item["outer_plan_id"],
            storage_device_ids=tuple(item["storage_device_ids"]),
            initial_soc_mwh=item["initial_soc_mwh"],
            first_soc_mwh=np.asarray(result["soc"], dtype=float)[0],
            first_b_mw=np.asarray(result["b"], dtype=float)[0],
            solution_values=self.values(),
        )
        return SelectedController(self.spec, source)


def audit_candidate(
    directory: Path,
    spec: AttemptSpec,
    *,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    outer: streaming.StreamingOuterPlan,
    initial: Mapping[str, float],
    stop: int,
    expected_request_sha256: str | None = None,
) -> AuditedCandidate:
    """Reconstruct accepted/rejected status without retaining or rebuilding CVXPY."""
    if inputs.hvdc or any(
        load.shedding_cost_per_mwh is not None for load in inputs.loads
    ):
        raise ValueError(
            "speculative audit is scoped to the fixed Case118 device fleet"
        )
    payload = json.loads((directory / "result.json").read_text())
    if (
        payload.get("schema_version") != 1
        or payload.get("policy") != POLICY_NAME
        or payload.get("kind") != "speculative_attempt_result"
        or invocation(payload["invocation"]) != spec
        or payload.get("selected_for_execution") is not False
    ):
        raise ValueError("candidate envelope/selection mismatch")
    if (
        expected_request_sha256 is not None
        and payload["request_sha256"] != expected_request_sha256
    ):
        raise ValueError("candidate request differs from worker request")
    item = payload["attempt"]
    ids = [str(s.device_id) for s in inputs.storage]
    target = outer.target_at(stop)
    slot = streaming._p0_registry(spec.window.iteration)[spec.source_slot]
    expected = {
        "attempt_id": attempt_id(spec.window.iteration, spec.source_slot),
        "ordinal": spec.source_slot,
        "role": slot.role,
        "iteration": spec.window.iteration,
        "global_interval_start": spec.window.iteration,
        "global_interval_stop": stop,
        "outer_plan_id": outer.outer_plan_id,
        "storage_device_ids": ids,
        "initial_soc_mwh": dict(initial),
        "target_soc_mwh": target,
        "scale": spec.scale,
        "seed": spec.seed,
        "formulation": "ac",
        "inner_terminal_policy": "hard_equality",
        "result_dimensions": result_dimensions(inputs),
    }
    if any(item.get(key) != value for key, value in expected.items()):
        raise ValueError("candidate model/state/role identity mismatch")
    ref = payload["start_artifact"]
    if ref is None:
        if item.get("slot_state") != "construction_error":
            raise ValueError("executed candidate lacks complete start")
        return AuditedCandidate(payload, Completion(spec, "construction_error"))
    path = directory / "start.json"
    if ref != {
        "relative_path": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_path(path),
    }:
        raise ValueError("candidate start reference mismatch")
    start = load_retained_start(path)
    if start.invocation != spec or start.request_sha256 != payload["request_sha256"]:
        raise ValueError("candidate/start request mismatch")
    if not np.array_equal(start.evidence.complete_x0, item["solver_x0"]):
        raise ValueError("candidate differs from actual retained x0")
    _validate_executed_evidence(
        item,
        residual_tolerances(policy),
        result_shapes(inputs, stop - spec.window.iteration),
    )
    storage = streaming._inner_storage(
        inputs, initial, target if spec.hard_target else None
    )
    reconstructed = audit_probe(
        inputs.case,
        cast(OPFBuild, SimpleNamespace(formulation="ac")),
        item["result"],
        generators=inputs.generators,
        loads=inputs.loads,
        nondispatchable=inputs.nondispatchable,
        storage=storage,
        delta=inputs.delta,
        tolerances=policy.tolerances,
        include_terminal=spec.hard_target,
    )
    audit = item["audit"]
    # An exception can leave last-iterate arrays; those cannot be accepted.
    accepted = reconstructed.accepted_primal and audit["exception"] is None
    if (
        accepted != audit["accepted_primal"]
        or dict(reconstructed.residuals) != audit["residuals"]
        or list(reconstructed.missing_or_nonfinite_fields)
        != audit["missing_or_nonfinite_fields"]
        or reconstructed.identity_error != audit["identity_error"]
    ):
        raise ValueError("retained and independent candidate audits disagree")
    score = None
    if (
        not accepted
        and not reconstructed.missing_or_nonfinite_fields
        and reconstructed.identity_error is None
        and spec.hard_target
    ):
        boxes = box_residuals(inputs, item["result"], spec.window.iteration)
        limits = {
            name: getattr(policy.tolerances, RESIDUAL_TOLERANCE_FIELDS.get(name, name))
            for name in (*AC_COMMON_RESIDUAL_NAMES, "terminal_soc_mwh_abs")
        }
        limits.update(
            {
                name: (
                    policy.tolerances.soc_recurrence_mwh_abs
                    if name.endswith("mwh_abs")
                    else policy.tolerances.ac_active_balance_pu_abs
                )
                for name in boxes
            }
        )
        score = normalized_violation({**reconstructed.residuals, **boxes}, limits)
    objective = item["result"].get("objective")
    result = AuditedCandidate(
        payload,
        Completion(
            spec, "accepted" if accepted else "rejected", True, score, objective
        ),
    )
    if accepted:
        result.values()
    return result
