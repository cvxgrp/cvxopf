from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from experiments.case118_annual_hierarchy.p0_fixture import (
    P0_EXPECTED_POLICY_SHA256,
    P0_EXPECTED_SOLVE_CONFIG_SHA256,
    policy_sha256,
    solve_config_sha256,
)
from experiments.case118_annual_hierarchy.scenario import PILOT_GRID
from experiments.case118_annual_hierarchy.s4_fixture import (
    BIG_EXPERIMENT_PARENT_COMMIT,
    M14C_INTEGRATION_PATH,
    M14C_INTEGRATION_CHECKPOINT,
    M14C_MERGE_BASE_COMMIT,
    M14C_PREFIX_LADDER_RESULTS_SHA256,
    M14C_REPRESENTATION_DISPOSITION_PATH,
    M14C_REPRESENTATION_DISPOSITION_SHA256,
    M14C_SOURCE_COMMIT,
    S4_CANONICALIZATION_BACKEND,
    S4_DELTA_HOURS,
    S4_EXPECTED_HASHES,
    S4_EXECUTION_LIMITS,
    S4_GENERATOR_CONDITIONING_EVIDENCE_SHA256,
    S4_GENERATOR_QUADRATIC_COST,
    S4_HORIZON_STEPS,
    S4_OUTPUT_DIRECTORY,
    S4_TEMPORAL_ASSEMBLY,
    S4ExecutionLimits,
    load_s4_fixture,
)


ROOT = Path(__file__).resolve().parents[1]


def test_s4_fixture_loads_exact_frozen_annual_problem() -> None:
    fixture = load_s4_fixture()
    inputs = fixture.inputs

    assert S4_HORIZON_STEPS == 8760
    assert S4_DELTA_HOURS == 1.0
    assert inputs.horizon_steps == 8760
    assert inputs.delta == 1.0
    assert inputs.df_load_p.shape[0] == 8760
    assert inputs.df_load_q.shape[0] == 8760
    assert inputs.df_nd is not None and inputs.df_nd.shape[0] == 8760
    assert fixture.start_timestamp == "2025-01-01 00:00:00+00:00"
    assert fixture.stop_timestamp == "2025-12-31 23:00:00+00:00"
    assert fixture.hashes == S4_EXPECTED_HASHES
    assert policy_sha256(fixture.policy) == fixture.policy_sha256
    assert solve_config_sha256(fixture.solve_config) == fixture.solve_config_sha256
    assert fixture.policy_sha256 == P0_EXPECTED_POLICY_SHA256
    assert fixture.solve_config_sha256 == P0_EXPECTED_SOLVE_CONFIG_SHA256
    assert fixture.temporal_assembly == S4_TEMPORAL_ASSEMBLY == "vectorized"
    assert fixture.canonicalization_backend == S4_CANONICALIZATION_BACKEND == "SCIPY"
    assert fixture.generator_quadratic_cost == S4_GENERATOR_QUADRATIC_COST == 1e-4
    assert (
        fixture.generator_conditioning_evidence_sha256
        == S4_GENERATOR_CONDITIONING_EVIDENCE_SHA256
    )
    evidence_path = M14C_INTEGRATION_PATH.with_name("M14C_GENERATOR_CONDITIONING.json")
    assert hashlib.sha256(evidence_path.read_bytes()).hexdigest() == (
        fixture.generator_conditioning_evidence_sha256
    )
    evidence = json.loads(evidence_path.read_text())
    for key in ("diagnostic_runner", "protocol"):
        artifact = evidence[key]
        path = ROOT / artifact["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    assert (
        evidence["diagnostic_script_sha256"]
        == (evidence["diagnostic_runner"]["sha256"])
    )
    rationale = evidence["selection_rationale"]
    assert rationale["generator_count"] == 54
    assert rationale["maximum_added_marginal_cost_per_mwh"] == 0.2364
    assert [item["scope"] for item in rationale["tested_alternatives"]] == [
        "none",
        "bus_69",
        "bus_69",
        "all_dispatchable_generators",
    ]
    assert fixture.m14c_integration_checkpoint == M14C_INTEGRATION_CHECKPOINT
    assert fixture.m14c_source_commit == M14C_SOURCE_COMMIT
    assert fixture.big_experiment_parent_commit == BIG_EXPERIMENT_PARENT_COMMIT
    assert fixture.m14c_merge_base_commit == M14C_MERGE_BASE_COMMIT
    assert fixture.prefix_ladder_executed is True
    assert fixture.annual_execution_authorized is True
    assert fixture.m14c_representation_disposition_sha256 == (
        M14C_REPRESENTATION_DISPOSITION_SHA256
    )
    assert fixture.m14c_prefix_ladder_results_sha256 == (
        M14C_PREFIX_LADDER_RESULTS_SHA256
    )
    assert hashlib.sha256(
        M14C_REPRESENTATION_DISPOSITION_PATH.read_bytes()
    ).hexdigest() == (M14C_REPRESENTATION_DISPOSITION_SHA256)
    disposition = json.loads(M14C_REPRESENTATION_DISPOSITION_PATH.read_text())
    historical = disposition["historical_profile"]
    assert historical["retrospective_reclassification"] is False
    assert historical["mismatch_disposition"]["Pg"] == (
        "solver_resolution_certificate_limited_not_mathematically_nonunique"
    )
    assert [
        record["maximum_pg_difference_mw"]
        for record in historical["observed_mismatches"]
    ] == [0.02298733018034227, 0.2320286542567942, 0.5758110961889997]
    scientific = disposition["scientific_disposition"]
    assert scientific["certificate_limited_coordinates"] == ["dispatchable_generation"]
    assert scientific["derived_differences"] == [
        "p_net",
        "generation_cost",
        "dc_loss_cost",
        "storage_cost",
    ]
    assert len(fixture.m14c_integration_sha256) == 64
    assert M14C_INTEGRATION_PATH.is_file()


def test_s4_execution_boundary_matches_frozen_protocol() -> None:
    assert S4_OUTPUT_DIRECTORY.as_posix().endswith(
        "results/s4_annual_outer_rated_attempt_005"
    )
    assert S4_EXECUTION_LIMITS == S4ExecutionLimits(
        child_rss_mib=16_384.0,
        worker_wall_seconds=7_200.0,
        supervisor_wall_seconds=10_800.0,
        poll_seconds=1.0,
    )


def test_s4_fixture_freezes_scientific_semantics() -> None:
    fixture = load_s4_fixture()
    inputs = fixture.inputs
    point = PILOT_GRID[0]

    assert point.renewable_energy_share == 0.15
    assert point.storage_power_fraction_of_peak == 0.05
    assert point.storage_duration_hours == 4.0
    assert fixture.storage_device_ids == (
        "storage_bus_41",
        "storage_bus_65",
        "storage_bus_89",
        "storage_bus_105",
    )
    assert tuple(unit.bus for unit in inputs.storage) == (41, 65, 89, 105)
    assert all(
        np.isclose(unit.initial_soc, 0.5 * unit.capacity)
        and np.isclose(unit.terminal_soc, 0.5 * unit.capacity)
        and unit.terminal_constraint == "equality"
        for unit in inputs.storage
    )
    assert inputs.options.enforce_branch_limits is True
    assert inputs.options.init_flat is True
    assert len(inputs.generators) == 54
    assert all(
        unit.cost_type == "polynomial"
        and unit.cost_coeffs is not None
        and unit.cost_coeffs[2] == S4_GENERATOR_QUADRATIC_COST
        for unit in inputs.generators
    )
