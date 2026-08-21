"""S1 reproducibility gates for the frozen M17 experiment scenario."""

from copy import deepcopy
from pathlib import Path
import shutil
import warnings

import numpy as np
import pytest

from cvxopf.problem import (
    DispatchableGenerator,
    Load,
    NondispatchableUnit,
    OPFOptions,
    StorageUnitIdeal,
    build_opf_multistep,
)

from experiments.hierarchical_battery_resilience.scenario import (
    SCENARIO_DIR,
    array_sha256,
    load_frozen_scenario,
)
from cvxopf.testcases import case9


def test_frozen_scenario_loads_from_checked_in_artifacts():
    scenario = load_frozen_scenario()

    assert scenario.manifest["scenario_name"] == "tracy_high_96h_v1"
    assert scenario.manifest["horizon_steps"] == 96
    assert scenario.manifest["delta_hours"] == pytest.approx(1.0)
    assert scenario.manifest["nominal_ac_window_steps"] == 5
    assert scenario.df_load_p.shape == (96, 3)
    assert scenario.df_load_q.shape == (96, 3)
    assert scenario.df_nd.shape == (96, 7)


def test_frozen_scenario_materializes_one_build_ready_contract():
    scenario = load_frozen_scenario()

    assert scenario.case["baseMVA"] == pytest.approx(100.0)
    assert isinstance(scenario.options, OPFOptions)
    assert scenario.options.enforce_branch_limits is True
    assert all(
        isinstance(unit, DispatchableGenerator)
        for unit in scenario.generators
    )
    assert all(isinstance(unit, Load) for unit in scenario.loads)
    assert all(
        isinstance(unit, NondispatchableUnit)
        for unit in scenario.nondispatchable
    )
    assert all(isinstance(unit, StorageUnitIdeal) for unit in scenario.storage)
    assert scenario.hvdc == ()
    assert [unit.device_id for unit in scenario.loads] == list(
        scenario.df_load_p.columns
    )
    assert [unit.device_id for unit in scenario.nondispatchable] == list(
        scenario.df_nd.columns
    )
    assert [unit.device_id for unit in scenario.storage] == ["battery_bus_7"]
    assert scenario.control.horizon_steps == 96
    assert scenario.control.nominal_ac_window_steps == 5
    assert scenario.control.outer_policies == (
        "frozen", "replan_every_step"
    )
    assert scenario.control.automatic_fallback is False
    assert scenario.control.perfect_forecast is True
    assert scenario.control.global_terminal_boundary == 96
    assert scenario.control.replans_retain_global_terminal_boundary is True
    assert set(scenario.control.acceptance_tolerances) == {
        "soc_recurrence_mwh_abs",
        "terminal_soc_mwh_abs",
        "soft_terminal_cost_abs",
        "ac_active_balance_pu_abs",
        "ac_reactive_balance_pu_abs",
        "dc_injection_reporting_mw_abs",
        "dc_nodal_balance_pu_abs",
        "voltage_bound_pu_abs",
        "branch_mva_abs",
        "branch_normalized_squared_residual",
    }


@pytest.mark.parametrize(
    ("formulation", "steps"), [("lossy_dc", 96), ("ac", 5)]
)
def test_materialized_contract_builds_existing_formulations(
    formulation, steps
):
    scenario = load_frozen_scenario()
    load_p = scenario.df_load_p.iloc[:steps]
    load_q = scenario.df_load_q.iloc[:steps]
    nd = scenario.df_nd.iloc[:steps]

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*reactive load input metadata.*",
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message="Storage apparent_power_rating is applied.*",
            category=UserWarning,
        )
        build = build_opf_multistep(
            deepcopy(scenario.case),
            T=steps,
            formulation=formulation,
            options=scenario.options,
            generators=list(scenario.generators),
            loads=list(scenario.loads),
            df_load_p=load_p,
            df_load_q=load_q,
            nondispatchable=list(scenario.nondispatchable),
            df_nd=nd,
            storage=list(scenario.storage),
            hvdc=list(scenario.hvdc),
            delta=scenario.control.delta_hours,
        )

    assert build.formulation == formulation
    assert build.data["T"] == steps
    np.testing.assert_array_equal(
        build.data["storage_device_ids"], ["battery_bus_7"]
    )


def test_frozen_scenario_preserves_reviewed_energy_totals_and_alignment():
    scenario = load_frozen_scenario()

    assert scenario.df_load_p.index.equals(scenario.df_load_q.index)
    assert scenario.df_load_p.index.equals(scenario.df_nd.index)
    assert scenario.df_load_p.to_numpy().sum() == pytest.approx(
        30_224.23585807647
    )
    assert scenario.df_nd.to_numpy().sum() == pytest.approx(
        7_510.025291808148
    )
    assert np.all(scenario.df_load_p.to_numpy() >= 0.0)
    assert np.all(scenario.df_nd.to_numpy() >= 0.0)


def test_reactive_load_channels_reconstruct_locked_power_factors():
    scenario = load_frozen_scenario()
    ratios = {
        "load_bus_5": 30.0 / 90.0,
        "load_bus_7": 35.0 / 100.0,
        "load_bus_9": 50.0 / 125.0,
    }
    for device_id, ratio in ratios.items():
        np.testing.assert_allclose(
            scenario.df_load_q[device_id],
            ratio * scenario.df_load_p[device_id],
        )


def test_manifest_freezes_identity_and_no_shedding_baseline():
    manifest = load_frozen_scenario().manifest

    assert [item["device_id"] for item in manifest["storage"]] == [
        "battery_bus_7"
    ]
    assert manifest["storage"][0]["initial_soc_mwh"] == pytest.approx(500.0)
    assert manifest["storage"][0]["outer_terminal_soc_mwh"] == pytest.approx(
        500.0
    )
    assert all(
        item["shedding_cost_per_mwh"] is None
        for item in manifest["loads"]
    )
    assert manifest["policies"]["automatic_fallback"] is False


def test_manifest_freezes_reviewed_policy_and_network_choices():
    manifest = load_frozen_scenario().manifest
    network_case = case9()

    assert manifest["policies"]["outer"] == [
        "frozen", "replan_every_step"
    ]
    assert manifest["policies"]["inner_terminal"] == [
        "hard_equality", "quadratic_soft"
    ]
    assert manifest["policies"]["quadratic_soft_weight"] == pytest.approx(0.05)
    assert manifest["case"]["options"]["enforce_branch_limits"] is True
    assert manifest["case"]["options"]["sparsity_tol"] == pytest.approx(0.0)
    assert manifest["case"]["bus_array_sha256"] == array_sha256(
        network_case["bus"]
    )
    assert manifest["case"]["branch_array_sha256"] == array_sha256(
        network_case["branch"]
    )


def test_loader_rejects_artifact_drift(tmp_path: Path):
    copied = tmp_path / "prepared_scenario"
    shutil.copytree(SCENARIO_DIR, copied)
    load_path = copied / "load_p.csv"
    lines = load_path.read_text().splitlines()
    fields = lines[1].split(",")
    fields[1] = str(float(fields[1]) + 1.0)
    lines[1] = ",".join(fields)
    load_path.write_text("\n".join(lines) + "\n")

    with pytest.raises(ValueError, match="numeric-array hash mismatch"):
        load_frozen_scenario(copied)


@pytest.mark.parametrize("table", ["bus", "branch"])
def test_loader_rejects_case9_network_drift(table):
    changed = deepcopy(case9())
    changed[table][0, 0] += 1.0

    with pytest.raises(ValueError, match=f"{table}-array hash mismatch"):
        load_frozen_scenario(case_factory=lambda: changed)
