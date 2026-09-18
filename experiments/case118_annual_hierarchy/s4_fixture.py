"""Frozen annual Case118 outer-plan fixture for S4."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Mapping, cast

import numpy as np

from cvxopf import (
    DispatchableGenerator,
    HierarchicalInputs,
    HierarchicalPolicy,
    HierarchicalSolveConfig,
    OPFOptions,
    TemporalAssembly,
    gen_from_matpower,
)
from experiments.case118_annual_hierarchy.p0_fixture import (
    P0_EXPECTED_POLICY_SHA256,
    P0_EXPECTED_SOLVE_CONFIG_SHA256,
    frozen_p0_policy,
    frozen_p0_solve_config,
    policy_sha256,
    solve_config_sha256,
)
from experiments.case118_annual_hierarchy.pglib_case import (
    array_sha256,
    load_pglib_case118,
)
from experiments.case118_annual_hierarchy.scenario import (
    HOURS_PER_YEAR,
    PILOT_GRID,
    materialize_pilot,
)
from experiments.case118_annual_hierarchy.streaming_runner import (
    execution_input_sha256,
)


S4_HORIZON_STEPS = HOURS_PER_YEAR
S4_DELTA_HOURS = 1.0
S4_TEMPORAL_ASSEMBLY: TemporalAssembly = "vectorized"
S4_CANONICALIZATION_BACKEND = "SCIPY"
S4_GENERATOR_QUADRATIC_COST = 1e-4
S4_GENERATOR_CONDITIONING_EVIDENCE_SHA256 = (
    "c10d344c3985982f19b2e039134318f5eba70dc8b2e81cc21fd5433419d9abbe"
)
S4_GENERATOR_CONDITIONING_RAW_SHA256 = (
    "2de21da15aac1bc58ecfc70c2b11a56cbbe67ba7905ab376b717bb02fd9d6aac"
)
M14C_INTEGRATION_CHECKPOINT = "big-experiment-conditioned-annual-authorized"
M14C_SOURCE_COMMIT = "0ef895b5e665fdb3a8fffab60292329ed22fd32b"
BIG_EXPERIMENT_PARENT_COMMIT = "6a9cd130b7817f2ac6fbca2ce0de634da8967b25"
M14C_MERGE_BASE_COMMIT = "f7a120c991202e9024405539c2bcd3ab74ff7f1e"
M14C_INTEGRATION_PATH = (
    Path(__file__).parents[1] / "m14_time_vectorization" / "M14C_INTEGRATION.json"
)
M14C_REPRESENTATION_DISPOSITION_PATH = M14C_INTEGRATION_PATH.with_name(
    "M14C_REPRESENTATION_DISPOSITION.json"
)
M14C_REPRESENTATION_DISPOSITION_SHA256 = (
    "74b38999a60c1cded3840273d9caaf9c115bbe257313fda30c448398c5178beb"
)
M14C_PREFIX_LADDER_RESULTS_SHA256 = (
    "8c5e5cee101489fe708f7a8c501268c98515bf71583e3bda76fa8073f38cb779"
)
M14C_TIGHT_DIAGNOSTIC_RESULTS_SHA256 = (
    "cf51b169ffb35d5ca519462a4a38b31713538ce0e66f245d41f72be16c753a36"
)
M14C_HISTORICAL_PROFILE_RESULT_SHA256 = (
    "49cf9270ddaaf07a0c1529bf23440b0e6bf68fabc918a36e5f65b6420514b16a"
)
M14C_HISTORICAL_PROFILE_ANALYSIS_SHA256 = (
    "e0d7ef556c6b1ad698764d3f0ccc565fdaaf1d4ee52411b4c8ddf22d8f3df458"
)
S4_OUTPUT_DIRECTORY = Path(
    "experiments/case118_annual_hierarchy/results/s4_annual_outer_rated_attempt_005"
)
S4_EXPECTED_HASHES: Mapping[str, str] = {
    "case": "815ed943bb2e38dc4da0ad176c0df5fd95b09d352000378987849acbd1eb46ca",
    "load_p": "2f1845f158ed5149b36cb5587d579c09d821d475e02285d2e348f6521bf27764",
    "load_q": "e11d9fe44698056270896f6134735d5f1300da774b65b10bbb942ac5f4baf90b",
    "nondispatchable": "529e2cd33f57c16b9ca5702d2c023e4739d1fedf43d1134456cd1a9ab2c4245b",
    "input_fingerprint": "40480e8cc09b40b472de51ddd7b032a46f2dc23339bef8d9fdbb44ef15c1cb13",
    "scenario": "94cbf310b7e5c911d66ff44a71f7f0a94bcc8271c65849cc3e6cd1c4050e3f07",
}


@dataclass(frozen=True)
class S4Fixture:
    """Build-ready annual inputs and their frozen integrity identity."""

    inputs: HierarchicalInputs
    policy: HierarchicalPolicy
    solve_config: HierarchicalSolveConfig
    policy_sha256: str
    solve_config_sha256: str
    temporal_assembly: TemporalAssembly
    canonicalization_backend: str
    generator_quadratic_cost: float
    generator_conditioning_evidence_sha256: str
    m14c_integration_checkpoint: str
    m14c_source_commit: str
    big_experiment_parent_commit: str
    m14c_merge_base_commit: str
    prefix_ladder_executed: bool
    annual_execution_authorized: bool
    m14c_representation_disposition_sha256: str
    m14c_prefix_ladder_results_sha256: str
    m14c_integration_sha256: str
    hashes: Mapping[str, str]
    start_timestamp: str
    stop_timestamp: str

    @property
    def scenario_hash(self) -> str:
        return self.hashes["scenario"]

    @property
    def storage_device_ids(self) -> tuple[str, ...]:
        return cast(tuple[str, ...], self.inputs.storage_device_ids)


@dataclass(frozen=True)
class S4ExecutionLimits:
    """Frozen supervisor limits for the one-shot annual outer solve."""

    child_rss_mib: float = 16_384.0
    worker_wall_seconds: float = 7_200.0
    supervisor_wall_seconds: float = 10_800.0
    poll_seconds: float = 1.0

    def __post_init__(self) -> None:
        values = (
            self.child_rss_mib,
            self.worker_wall_seconds,
            self.supervisor_wall_seconds,
            self.poll_seconds,
        )
        if not all(np.isfinite(value) and value > 0.0 for value in values):
            raise ValueError("S4 execution limits must be positive and finite")
        if self.supervisor_wall_seconds < self.worker_wall_seconds:
            raise ValueError("S4 supervisor wall limit cannot precede worker limit")


S4_EXECUTION_LIMITS = S4ExecutionLimits()


def _case_sha256(case: Mapping[str, object]) -> str:
    digest = hashlib.sha256()
    for name in ("baseMVA", "bus", "gen", "branch", "gencost"):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(array_sha256(np.asarray(case[name])).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _scenario_sha256(hashes: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(hashes):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(hashes[name].encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _condition_generator_costs(
    case: Mapping[str, object],
) -> tuple[DispatchableGenerator, ...]:
    """Add frozen synthetic curvature that reduces representation sensitivity."""
    return tuple(
        replace(
            unit,
            cost_coeffs=(
                float(unit.cost_coeffs[0]),
                float(unit.cost_coeffs[1]),
                S4_GENERATOR_QUADRATIC_COST,
            ),
        )
        for unit in gen_from_matpower(case["gen"], case["gencost"])
    )


def _integration_provenance(
    hashes: Mapping[str, str], policy_hash: str, solve_config_hash: str
) -> tuple[str, str, str, str, bool, bool, str, str, str]:
    payload = cast(Mapping[str, object], json.loads(M14C_INTEGRATION_PATH.read_text()))
    if payload.get("schema_version") != 1:
        raise ValueError("M14c integration schema mismatch")
    if payload.get("temporal_assembly") != S4_TEMPORAL_ASSEMBLY:
        raise ValueError("M14c integration temporal assembly mismatch")
    if payload.get("canonicalization_backend") != S4_CANONICALIZATION_BACKEND:
        raise ValueError("M14c integration canonicalization backend mismatch")
    if payload.get("s4_fixture") != hashes:
        raise ValueError("M14c integration S4 fixture hashes mismatch")
    if payload.get("policy_sha256") != policy_hash:
        raise ValueError("M14c integration policy hash mismatch")
    if payload.get("solve_config_sha256") != solve_config_hash:
        raise ValueError("M14c integration solve-configuration hash mismatch")
    if payload.get("checkpoint") != M14C_INTEGRATION_CHECKPOINT:
        raise ValueError("M14c integration checkpoint mismatch")
    conditioning = payload.get("generator_cost_conditioning")
    if not isinstance(conditioning, Mapping):
        raise ValueError("M14c generator-cost conditioning record is missing")
    if conditioning.get("scope") != "all_dispatchable_generators":
        raise ValueError("M14c generator-cost conditioning scope mismatch")
    if conditioning.get("quadratic_coefficient") != S4_GENERATOR_QUADRATIC_COST:
        raise ValueError("M14c generator quadratic cost mismatch")
    if conditioning.get("record_sha256") != S4_GENERATOR_CONDITIONING_EVIDENCE_SHA256:
        raise ValueError("M14c generator-cost conditioning evidence mismatch")
    if conditioning.get("raw_result_sha256") != S4_GENERATOR_CONDITIONING_RAW_SHA256:
        raise ValueError("M14c raw generator-cost evidence mismatch")
    if payload.get("m14c_source_commit") != M14C_SOURCE_COMMIT:
        raise ValueError("M14c integration source commit mismatch")
    if payload.get("big_experiment_parent_commit") != BIG_EXPERIMENT_PARENT_COMMIT:
        raise ValueError("M14c integration target-parent commit mismatch")
    if payload.get("merge_base_commit") != M14C_MERGE_BASE_COMMIT:
        raise ValueError("M14c integration merge-base commit mismatch")
    disposition = payload.get("representation_disposition")
    expected_disposition_path = M14C_REPRESENTATION_DISPOSITION_PATH.relative_to(
        Path(__file__).parents[2]
    ).as_posix()
    if not isinstance(disposition, Mapping):
        raise ValueError("M14c representation disposition is missing")
    if disposition.get("path") != expected_disposition_path:
        raise ValueError("M14c representation disposition path mismatch")
    disposition_hash = hashlib.sha256(
        M14C_REPRESENTATION_DISPOSITION_PATH.read_bytes()
    ).hexdigest()
    if (
        disposition.get("sha256") != M14C_REPRESENTATION_DISPOSITION_SHA256
        or disposition_hash != M14C_REPRESENTATION_DISPOSITION_SHA256
    ):
        raise ValueError("M14c representation disposition hash mismatch")
    disposition_payload = cast(
        Mapping[str, object],
        json.loads(M14C_REPRESENTATION_DISPOSITION_PATH.read_text()),
    )
    historical = disposition_payload.get("historical_profile")
    diagnostic = disposition_payload.get("tight_tolerance_diagnostic")
    scientific = disposition_payload.get("scientific_disposition")
    if not all(
        isinstance(value, Mapping) for value in (historical, diagnostic, scientific)
    ):
        raise ValueError("M14c representation evidence is incomplete")
    historical = cast(Mapping[str, object], historical)
    diagnostic = cast(Mapping[str, object], diagnostic)
    scientific = cast(Mapping[str, object], scientific)
    comparisons = diagnostic.get("comparisons")
    mismatch_disposition = historical.get("mismatch_disposition")
    observed_mismatches = historical.get("observed_mismatches")
    if not isinstance(mismatch_disposition, Mapping):
        raise ValueError("M14c historical mismatch disposition is incomplete")
    if not isinstance(observed_mismatches, list) or not all(
        isinstance(item, Mapping) for item in observed_mismatches
    ):
        raise ValueError("M14c historical mismatch evidence is incomplete")
    historical_records = cast(list[Mapping[str, object]], observed_mismatches)
    if not isinstance(comparisons, list) or not all(
        isinstance(item, Mapping) for item in comparisons
    ):
        raise ValueError("M14c tight diagnostic comparisons are incomplete")
    comparison_records = cast(list[Mapping[str, object]], comparisons)
    if (
        disposition_payload.get("schema_version") != 1
        or disposition_payload.get("classification")
        != "equivalent_representations_for_case118_annual_study"
        or historical.get("classification") != "mismatch_under_original_coordinate_gate"
        or historical.get("result_sha256") != M14C_HISTORICAL_PROFILE_RESULT_SHA256
        or historical.get("analysis_sha256") != M14C_HISTORICAL_PROFILE_ANALYSIS_SHA256
        or historical.get("retrospective_reclassification") is not False
        or mismatch_disposition.get("Pg")
        != "solver_resolution_certificate_limited_not_mathematically_nonunique"
        or mismatch_disposition.get("p_net")
        != "derived_from_alternative_storage_trajectory"
        or [item.get("horizon_steps") for item in historical_records] != [24, 168, 720]
        or diagnostic.get("result_sha256") != M14C_TIGHT_DIAGNOSTIC_RESULTS_SHA256
        or diagnostic.get("classification") != "accepted"
        or diagnostic.get("annual_execution_authorized") is not False
        or [item.get("horizon_steps") for item in comparison_records] != [24, 168, 720]
        or any(
            item.get("objective_difference_within_combined_native_gap") is not True
            for item in comparison_records
        )
        or scientific.get("equivalent_for_this_study") is not True
        or scientific.get("authoritative_annual_policy_realization")
        != "vectorized_scipy"
        or scientific.get("weakly_identified_coordinates")
        != [
            "intermediate_storage_power",
            "intermediate_storage_soc",
            "branch_flow",
        ]
        or scientific.get("certificate_limited_coordinates")
        != ["dispatchable_generation"]
        or scientific.get("derived_differences")
        != ["p_net", "generation_cost", "dc_loss_cost", "storage_cost"]
        or disposition_payload.get("prefix_ladder_executed") is not True
        or disposition_payload.get("annual_execution_authorized") is not True
        or disposition_payload.get("prefix_ladder_results_sha256")
        != M14C_PREFIX_LADDER_RESULTS_SHA256
    ):
        raise ValueError("M14c representation disposition is not authoritative")
    if payload.get("prefix_ladder_results_sha256") != M14C_PREFIX_LADDER_RESULTS_SHA256:
        raise ValueError("M14c prefix-ladder result hash mismatch")
    prefix_ladder_executed = payload.get("prefix_ladder_executed")
    annual_execution_authorized = payload.get("annual_execution_authorized")
    if not isinstance(prefix_ladder_executed, bool):
        raise ValueError("M14c prefix-ladder authority must be boolean")
    if not isinstance(annual_execution_authorized, bool):
        raise ValueError("M14c annual-execution authority must be boolean")
    if annual_execution_authorized and not prefix_ladder_executed:
        raise ValueError("M14c annual execution requires a completed prefix ladder")
    return (
        M14C_INTEGRATION_CHECKPOINT,
        M14C_SOURCE_COMMIT,
        BIG_EXPERIMENT_PARENT_COMMIT,
        M14C_MERGE_BASE_COMMIT,
        prefix_ladder_executed,
        annual_execution_authorized,
        disposition_hash,
        M14C_PREFIX_LADDER_RESULTS_SHA256,
        hashlib.sha256(M14C_INTEGRATION_PATH.read_bytes()).hexdigest(),
    )


def load_s4_fixture(*, verify_hashes: bool = True) -> S4Fixture:
    """Materialize the full rated annual inputs without constructing a model."""
    case = load_pglib_case118()
    pilot = materialize_pilot(case, PILOT_GRID[0])
    inputs = HierarchicalInputs(
        case=case,
        horizon_steps=S4_HORIZON_STEPS,
        delta=S4_DELTA_HOURS,
        generators=_condition_generator_costs(case),
        loads=pilot.loads,
        storage=pilot.storage,
        nondispatchable=pilot.nondispatchable,
        df_load_p=pilot.df_load_p.copy(),
        df_load_q=pilot.df_load_q.copy(),
        df_nd=pilot.df_nd.copy(),
        options=OPFOptions(enforce_branch_limits=True, init_flat=True),
    )
    policy = frozen_p0_policy()
    solve_config = frozen_p0_solve_config()
    if policy_sha256(policy) != P0_EXPECTED_POLICY_SHA256:
        raise ValueError("S4 hierarchy policy hash mismatch")
    if solve_config_sha256(solve_config) != P0_EXPECTED_SOLVE_CONFIG_SHA256:
        raise ValueError("S4 solve-configuration hash mismatch")
    component_hashes = {
        "case": _case_sha256(case),
        "load_p": array_sha256(pilot.df_load_p.to_numpy()),
        "load_q": array_sha256(pilot.df_load_q.to_numpy()),
        "nondispatchable": array_sha256(pilot.df_nd.to_numpy()),
        "input_fingerprint": execution_input_sha256(inputs),
    }
    hashes = {**component_hashes, "scenario": _scenario_sha256(component_hashes)}
    if verify_hashes:
        if set(S4_EXPECTED_HASHES) != set(hashes):
            raise ValueError("S4 expected-hash registry is incomplete")
        for name, expected in S4_EXPECTED_HASHES.items():
            if hashes[name] != expected:
                raise ValueError(f"S4 {name} hash mismatch")
    policy_hash = policy_sha256(policy)
    solve_hash = solve_config_sha256(solve_config)
    (
        integration_checkpoint,
        m14c_source_commit,
        big_parent_commit,
        merge_base_commit,
        prefix_ladder_executed,
        annual_execution_authorized,
        disposition_hash,
        prefix_ladder_results_hash,
        integration_hash,
    ) = _integration_provenance(hashes, policy_hash, solve_hash)
    return S4Fixture(
        inputs=inputs,
        policy=policy,
        solve_config=solve_config,
        policy_sha256=policy_hash,
        solve_config_sha256=solve_hash,
        temporal_assembly=S4_TEMPORAL_ASSEMBLY,
        canonicalization_backend=S4_CANONICALIZATION_BACKEND,
        generator_quadratic_cost=S4_GENERATOR_QUADRATIC_COST,
        generator_conditioning_evidence_sha256=(
            S4_GENERATOR_CONDITIONING_EVIDENCE_SHA256
        ),
        m14c_integration_checkpoint=integration_checkpoint,
        m14c_source_commit=m14c_source_commit,
        big_experiment_parent_commit=big_parent_commit,
        m14c_merge_base_commit=merge_base_commit,
        prefix_ladder_executed=prefix_ladder_executed,
        annual_execution_authorized=annual_execution_authorized,
        m14c_representation_disposition_sha256=disposition_hash,
        m14c_prefix_ladder_results_sha256=prefix_ladder_results_hash,
        m14c_integration_sha256=integration_hash,
        hashes=hashes,
        start_timestamp=str(pilot.df_load_p.index[0]),
        stop_timestamp=str(pilot.df_load_p.index[-1]),
    )


__all__ = [
    "S4_DELTA_HOURS",
    "S4_EXPECTED_HASHES",
    "S4_EXECUTION_LIMITS",
    "S4_HORIZON_STEPS",
    "S4_GENERATOR_QUADRATIC_COST",
    "S4_GENERATOR_CONDITIONING_EVIDENCE_SHA256",
    "S4_GENERATOR_CONDITIONING_RAW_SHA256",
    "M14C_INTEGRATION_PATH",
    "M14C_REPRESENTATION_DISPOSITION_PATH",
    "M14C_REPRESENTATION_DISPOSITION_SHA256",
    "M14C_PREFIX_LADDER_RESULTS_SHA256",
    "M14C_TIGHT_DIAGNOSTIC_RESULTS_SHA256",
    "S4_OUTPUT_DIRECTORY",
    "S4_TEMPORAL_ASSEMBLY",
    "S4_CANONICALIZATION_BACKEND",
    "S4ExecutionLimits",
    "S4Fixture",
    "load_s4_fixture",
]
