"""Public-controller versus streaming outer-only equivalence gate for S4."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Mapping, cast

import cvxpy as cp
import numpy as np

from cvxopf import HierarchicalInputs, OPFBuild, OuterPlanRecord
from cvxopf import _hierarchical_solver as public_solver
from experiments.case118_annual_hierarchy.p0_fixture import (
    policy_sha256,
    solve_config_sha256,
)
from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.streaming_runner import (
    execution_input_sha256,
    solve_frozen_outer,
)


EQUIVALENCE_HORIZON_STEPS = 24
EQUIVALENCE_ATOL = 1e-9
FINGERPRINT_DECIMALS = 9


class _OuterCaptured(RuntimeError):
    pass


def _problem_dimensions(build: OPFBuild) -> Mapping[str, int]:
    equalities = 0
    inequalities = 0
    other = 0
    for constraint in build.prob.constraints:
        if isinstance(constraint, cp.constraints.Equality):
            equalities += int(constraint.size)
        elif isinstance(constraint, cp.constraints.Inequality):
            inequalities += int(constraint.size)
        else:
            other += int(constraint.size)
    return {
        "scalar_variables": sum(
            int(variable.size) for variable in build.prob.variables()
        ),
        "scalar_equalities": equalities,
        "explicit_scalar_inequalities": inequalities,
        "other_scalar_constraints": other,
        "constraint_objects": len(build.prob.constraints),
    }


def _short_inputs(inputs: HierarchicalInputs) -> HierarchicalInputs:
    return replace(
        inputs,
        horizon_steps=EQUIVALENCE_HORIZON_STEPS,
        df_load_p=inputs.df_load_p.iloc[:EQUIVALENCE_HORIZON_STEPS].copy(),
        df_load_q=inputs.df_load_q.iloc[:EQUIVALENCE_HORIZON_STEPS].copy(),
        df_nd=(
            None
            if inputs.df_nd is None
            else inputs.df_nd.iloc[:EQUIVALENCE_HORIZON_STEPS].copy()
        ),
    )


def _numeric_mismatches(
    name: str, left: object, right: object, mismatches: list[str]
) -> None:
    left_array = np.asarray(left)
    right_array = np.asarray(right)
    if left_array.shape != right_array.shape:
        mismatches.append(name)
        return
    if np.issubdtype(left_array.dtype, np.number) and np.issubdtype(
        right_array.dtype, np.number
    ):
        equal = np.allclose(
            left_array.astype(float),
            right_array.astype(float),
            rtol=0.0,
            atol=EQUIVALENCE_ATOL,
            equal_nan=True,
        )
    else:
        equal = np.array_equal(left_array, right_array)
    if not equal:
        mismatches.append(name)


def _canonical(value: object) -> object:
    if isinstance(value, np.ndarray):
        if np.issubdtype(value.dtype, np.number):
            return np.round(value.astype(float), FINGERPRINT_DECIMALS).tolist()
        return value.tolist()
    if isinstance(value, np.generic):
        return _canonical(value.item())
    if isinstance(value, float):
        return round(value, FINGERPRINT_DECIMALS)
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def _digest(value: object) -> str:
    encoded = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _result_summary(result: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "objective": float(cast(float, result["objective"])),
        "result_sha256": _digest(result),
        "result_schema": {
            name: list(np.asarray(value).shape)
            for name, value in sorted(result.items())
        },
    }


def run_s4_outer_equivalence() -> Mapping[str, object]:
    """Solve and compare the exact rated 24-hour public and streaming outers."""
    fixture = load_s4_fixture()
    inputs = _short_inputs(fixture.inputs)
    captured: list[OuterPlanRecord] = []
    original = public_solver._window_attempts

    def intercept(*args: object, **kwargs: object) -> object:
        del kwargs
        captured.append(cast(OuterPlanRecord, args[5]))
        raise _OuterCaptured

    public_solver._window_attempts = intercept
    try:
        try:
            public_solver.solve_hierarchical_opf(
                inputs, fixture.policy, fixture.solve_config
            )
        except _OuterCaptured:
            pass
    finally:
        public_solver._window_attempts = original
    if len(captured) != 1:
        raise RuntimeError("public outer interception did not capture exactly one plan")
    public = captured[0]
    streaming = solve_frozen_outer(inputs, fixture.policy, fixture.solve_config)
    if public.build is None or streaming.build is None:
        raise RuntimeError("equivalence gate requires both live outer builds")
    mismatches: list[str] = []
    if public.build.formulation != streaming.build.formulation:
        mismatches.append("formulation")
    public_dimensions = _problem_dimensions(cast(OPFBuild, public.build))
    streaming_dimensions = _problem_dimensions(streaming.build)
    if public_dimensions != streaming_dimensions:
        mismatches.append("structural_counts")
    if public.storage_device_ids != streaming.storage_device_ids:
        mismatches.append("storage_device_ids")
    _numeric_mismatches(
        "global_boundary_indices",
        public.global_boundary_indices,
        streaming.global_boundary_indices,
        mismatches,
    )
    _numeric_mismatches(
        "boundary_soc_mwh",
        public.boundary_soc_mwh,
        streaming.boundary_soc_mwh,
        mismatches,
    )
    if public.audit.status != streaming.audit.status:
        mismatches.append("status")
    if public.audit.accepted_primal != streaming.audit.accepted_primal:
        mismatches.append("accepted_primal")
    public_residuals = dict(public.audit.residuals)
    if "branch_mw_abs" in public_residuals:
        mismatches.append("unexpected_public_branch_residual")
    streaming_branch_residual = streaming.audit.residuals.get("branch_mw_abs")
    if streaming_branch_residual != 0.0:
        mismatches.append("streaming_branch_residual")
    # Public M17 omits this inactive-limit diagnostic; the streaming probe
    # explicitly reports zero. Match the established P0 projection only after
    # independently checking both asymmetric schemas above.
    public_residuals["branch_mw_abs"] = 0.0
    if public_residuals != streaming.audit.residuals:
        mismatches.append("audit_residuals")
    if public.result.keys() != streaming.result.keys():
        mismatches.append("result_schema")
    else:
        for name in public.result:
            _numeric_mismatches(
                f"result.{name}",
                public.result[name],
                streaming.result[name],
                mismatches,
            )
    common_fingerprints = {
        "input_sha256": execution_input_sha256(inputs),
        "policy_sha256": policy_sha256(fixture.policy),
        "solve_config_sha256": solve_config_sha256(fixture.solve_config),
    }
    public_fingerprints = {
        **common_fingerprints,
        "result_sha256": _digest(public.result),
        "boundary_sha256": _digest(public.boundary_soc_mwh),
        "structure_sha256": _digest(
            {"formulation": public.build.formulation, "dimensions": public_dimensions}
        ),
    }
    streaming_fingerprints = {
        **common_fingerprints,
        "result_sha256": _digest(streaming.result),
        "boundary_sha256": _digest(streaming.boundary_soc_mwh),
        "structure_sha256": _digest(
            {
                "formulation": streaming.build.formulation,
                "dimensions": streaming_dimensions,
            }
        ),
    }
    if public_fingerprints != streaming_fingerprints:
        mismatches.append("outer_record_fingerprints")
    return {
        "schema_version": 1,
        "horizon_steps": EQUIVALENCE_HORIZON_STEPS,
        "equivalent": not mismatches,
        "mismatches": mismatches,
        "formulation": streaming.build.formulation,
        "public_dimensions": public_dimensions,
        "streaming_dimensions": streaming_dimensions,
        "public_status": public.audit.status,
        "streaming_status": streaming.audit.status,
        "storage_device_ids": list(streaming.storage_device_ids),
        "global_boundary_indices_sha256": _digest(streaming.global_boundary_indices),
        "public_summary": _result_summary(public.result),
        "streaming_summary": _result_summary(streaming.result),
        "public_boundary_sha256": _digest(public.boundary_soc_mwh),
        "streaming_boundary_sha256": _digest(streaming.boundary_soc_mwh),
        "public_residuals": dict(public.audit.residuals),
        "streaming_residuals": dict(streaming.audit.residuals),
        "audit_schema_projection": {
            "public_branch_mw_abs_present": False,
            "streaming_branch_mw_abs": streaming_branch_residual,
            "projected_public_branch_mw_abs": 0.0,
        },
        "public_fingerprints": public_fingerprints,
        "streaming_fingerprints": streaming_fingerprints,
        "fingerprints_match": public_fingerprints == streaming_fingerprints,
    }


__all__ = ["EQUIVALENCE_HORIZON_STEPS", "run_s4_outer_equivalence"]
