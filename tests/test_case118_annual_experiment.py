"""Characterization gates for the annual case118 hierarchy experiment."""

from copy import deepcopy
from contextlib import nullcontext
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import cvxpy as cp

from cvxopf import OPFBuild, OPFOptions, build_opf_multistep
from cvxopf.data import validate_case
from cvxopf.generator import gen_from_matpower
from cvxopf.results import extract_results
from cvxopf.testcases import case118
from experiments.case118_annual_hierarchy.audit import audit_probe
from experiments.case118_annual_hierarchy.pglib_case import (
    SOURCE_CASE_PATH,
    convert_pglib_case118,
    load_pglib_case118,
    loads_from_pglib_case,
    make_effectively_unlimited_case,
    parse_pglib_case118,
)
from experiments.case118_annual_hierarchy.scenario import (
    HOURS_PER_YEAR,
    PILOT_GRID,
    PROFILE_QUANTIZATION_DECIMALS,
    PilotParameters,
    build_annual_profiles,
    deterministic_siting,
    electrical_distance_matrix,
    materialize_pilot,
    select_pilot_window,
)
from experiments.case118_annual_hierarchy import run_s0, run_s1


EXPERIMENT_DIRECTORY = Path("experiments/case118_annual_hierarchy")


@pytest.fixture(scope="module")
def source_case():
    return parse_pglib_case118(SOURCE_CASE_PATH.read_text())


@pytest.fixture(scope="module")
def converted_case():
    return load_pglib_case118()


def test_pinned_source_parses_complete_matpower_tables(source_case):
    assert source_case["version"] == "2"
    assert source_case["baseMVA"] == 100.0
    assert source_case["bus"].shape == (118, 13)
    assert source_case["gen"].shape == (54, 10)
    assert source_case["gencost"].shape == (54, 7)
    assert source_case["branch"].shape == (186, 13)


def test_conversion_changes_only_optional_generator_layout(
    source_case, converted_case
):
    np.testing.assert_array_equal(converted_case["bus"], source_case["bus"])
    np.testing.assert_array_equal(
        converted_case["branch"], source_case["branch"]
    )
    np.testing.assert_array_equal(
        converted_case["gencost"], source_case["gencost"]
    )
    np.testing.assert_array_equal(
        converted_case["gen"][:, :10], source_case["gen"]
    )
    np.testing.assert_array_equal(converted_case["gen"][:, 10:], 0.0)
    assert converted_case["baseMVA"] == source_case["baseMVA"]
    validate_case(converted_case)


def test_conversion_preserves_every_scientifically_relevant_source_field(
    source_case, converted_case
):
    bus = converted_case["bus"]
    source_bus = source_case["bus"]
    # IDs/type, both signed demand channels, shunts, voltage, and bounds.
    np.testing.assert_array_equal(
        bus[:, [0, 1, 2, 3, 4, 5, 7, 8, 11, 12]],
        source_bus[:, [0, 1, 2, 3, 4, 5, 7, 8, 11, 12]],
    )
    assert np.count_nonzero(bus[:, 1] == 3) == 1

    # Generator identity/status, P/Q limits, setpoints, and costs.
    np.testing.assert_array_equal(converted_case["gen"][:, :10], source_case["gen"])
    np.testing.assert_array_equal(converted_case["gencost"], source_case["gencost"])
    np.testing.assert_array_equal(converted_case["gen"][:, 7], 1.0)
    np.testing.assert_array_equal(converted_case["gencost"][:, 0], 2.0)

    # Endpoints, impedance, ratings, taps/shifts, status, and angle limits.
    np.testing.assert_array_equal(converted_case["branch"], source_case["branch"])
    assert np.all(converted_case["branch"][:, 5] > 0.0)


def test_source_contains_all_expected_demand_channels(converted_case):
    bus = converted_case["bus"]
    assert np.count_nonzero(bus[:, 2] > 0.0) == 99
    assert np.count_nonzero(bus[:, 3] > 0.0) == 90
    assert np.count_nonzero(bus[:, 2] < 0.0) == 0
    assert np.count_nonzero(bus[:, 3] < 0.0) == 0
    assert np.count_nonzero((bus[:, 2] != 0.0) | (bus[:, 3] != 0.0)) == 99


def test_first_class_load_conversion_is_complete_and_identity_stable(
    converted_case,
):
    loads = loads_from_pglib_case(converted_case)
    expected_rows = converted_case["bus"][
        (converted_case["bus"][:, 2] != 0.0)
        | (converted_case["bus"][:, 3] != 0.0)
    ]
    assert len(loads) == 99
    assert [load.device_id for load in loads] == [
        f"load_bus_{int(row[0])}" for row in expected_rows
    ]
    np.testing.assert_array_equal(
        [load.p_load_mw for load in loads], expected_rows[:, 2]
    )
    np.testing.assert_array_equal(
        [load.q_load_mvar for load in loads], expected_rows[:, 3]
    )
    assert all(load.shedding_cost_per_mwh is None for load in loads)


@pytest.mark.parametrize("formulation", ["lossy_dc", "ac"])
def test_explicit_load_build_preserves_device_order(formulation, converted_case):
    loads = loads_from_pglib_case(converted_case)
    columns = [load.device_id for load in loads]
    p = pd.DataFrame([[load.p_load_mw for load in loads]], columns=columns)
    q = pd.DataFrame([[load.q_load_mvar for load in loads]], columns=columns)
    warning_context = (
        pytest.warns(UserWarning, match="retained as reactive load")
        if formulation == "lossy_dc"
        else nullcontext()
    )
    with warning_context:
        build = build_opf_multistep(
            converted_case,
            T=1,
            formulation=formulation,
            loads=list(loads),
            df_load_p=p,
            df_load_q=q,
        )
    np.testing.assert_array_equal(build.data["load_device_ids"], columns)
    np.testing.assert_array_equal(
        build.data["load_bus_external"], [load.bus for load in loads]
    )


def test_effectively_unlimited_control_changes_only_rate_a(converted_case):
    original = deepcopy(converted_case)
    control = make_effectively_unlimited_case(converted_case)

    for key in ("bus", "gen", "gencost"):
        np.testing.assert_array_equal(control[key], converted_case[key])
    np.testing.assert_array_equal(control["branch"][:, :5], converted_case["branch"][:, :5])
    np.testing.assert_array_equal(control["branch"][:, 6:], converted_case["branch"][:, 6:])
    np.testing.assert_array_equal(control["branch"][:, 5], 0.0)
    np.testing.assert_array_equal(converted_case["branch"], original["branch"])


@pytest.mark.parametrize("formulation", ["lossy_dc", "ac"])
def test_one_hour_case_builds_without_reordering(formulation, converted_case):
    bus = converted_case["bus"]
    p = pd.DataFrame([bus[:, 2]])
    q = pd.DataFrame([bus[:, 3]])
    options = OPFOptions(branch_limit_sentinel=1e6)

    warning_context = (
        pytest.warns(UserWarning, match="retained as reactive load")
        if formulation == "lossy_dc"
        else nullcontext()
    )
    with warning_context:
        build = build_opf_multistep(
            converted_case,
            p,
            q,
            T=1,
            formulation=formulation,
            options=options,
        )

    assert build.data["T"] == 1
    assert list(build.data["ext_to_int"]) == bus[:, 0].astype(int).tolist()
    np.testing.assert_array_equal(build.data["load_bus_external"], bus[:, 0])
    assert build.prob.is_dcp() is (formulation == "lossy_dc")


def test_parser_rejects_source_identity_and_shape_drift():
    source = SOURCE_CASE_PATH.read_text()
    with pytest.raises(ValueError, match="function name"):
        parse_pglib_case118(
            source.replace("pglib_opf_case118_ieee", "different_case", 1)
        )
    with pytest.raises(ValueError, match="shape"):
        parse_pglib_case118(
            source.replace("\t1\t 2\t 51.0", "% removed\t1\t 2\t 51.0", 1)
        )


def test_converter_does_not_mutate_source(source_case):
    original = np.asarray(source_case["gen"]).copy()
    converted = convert_pglib_case118(source_case)
    converted["gen"][0, 0] = -1
    np.testing.assert_array_equal(source_case["gen"], original)


def test_annual_profiles_are_timezone_stable_and_hash_frozen():
    profiles = build_annual_profiles()
    assert len(profiles.index) == HOURS_PER_YEAR
    assert str(profiles.index.tz) == "UTC"
    assert profiles.index[0].isoformat() == "2025-01-01T00:00:00+00:00"
    assert profiles.index[-1].isoformat() == "2025-12-31T23:00:00+00:00"
    assert np.mean(profiles.load_multiplier) == pytest.approx(1.0)
    assert np.min(profiles.load_multiplier) > 0.0
    assert np.min(profiles.wind_capacity_factor) >= 0.0
    assert np.max(profiles.wind_capacity_factor) <= 1.0
    assert np.min(profiles.solar_capacity_factor) >= 0.0
    assert np.max(profiles.solar_capacity_factor) <= 1.0
    assert profiles.hashes() == {
        "load_multiplier": (
            "d3128b43dc7fc075b7ac0a192e09563b96848a2ec8549f433bc12624b980e070"
        ),
        "wind_capacity_factor": (
            "29739d37bcc0d459157737fa7128e2ab6e74081c7b3a3d8fdfa9dc425146d9f9"
        ),
        "solar_capacity_factor": (
            "710a5be5ada057cdbe39f0b86d1d40bef54d428960581b53e10257e401ef775e"
        ),
    }
    for values in (
        profiles.load_multiplier,
        profiles.wind_capacity_factor,
        profiles.solar_capacity_factor,
    ):
        np.testing.assert_array_equal(
            values, np.round(values, PROFILE_QUANTIZATION_DECIMALS)
        )


def test_electrical_distance_and_siting_are_deterministic(converted_case):
    external_ids, distance = electrical_distance_matrix(converted_case)
    np.testing.assert_array_equal(external_ids, np.arange(1, 119))
    np.testing.assert_allclose(distance, distance.T, rtol=0.0, atol=0.0)
    np.testing.assert_array_equal(np.diag(distance), 0.0)
    assert np.isfinite(distance).all()
    assert np.all(distance >= 0.0)

    first = deterministic_siting(converted_case)
    second = deterministic_siting(converted_case)
    assert first == second
    assert first.storage_buses == (41, 65, 89, 105)
    assert first.solar_bus == 65
    assert first.wind_bus == 105
    assert first.distance_sha256 == (
        "370643318261ace9747f771eedc95bf4b1e106d2a77892e90e0a3fa6f6e1293c"
    )


def test_distance_contract_handles_zero_and_negative_reactance(converted_case):
    changed = deepcopy(converted_case)
    changed["branch"] = changed["branch"].copy()
    changed["branch"][0, 3] = 0.0
    changed["branch"][1, 3] = -changed["branch"][1, 3]
    _, distance = electrical_distance_matrix(changed)
    assert np.isfinite(distance).all()
    assert np.all(distance >= 0.0)


def test_distance_contract_rejects_disconnected_topology(converted_case):
    changed = deepcopy(converted_case)
    changed["branch"] = changed["branch"].copy()
    changed["branch"][:, 10] = 0.0
    with pytest.raises(ValueError, match="connected"):
        electrical_distance_matrix(changed)


@pytest.mark.parametrize("parameters", PILOT_GRID)
def test_pilot_materialization_obeys_sizing_and_identity_rules(
    parameters, converted_case
):
    pilot = materialize_pilot(converted_case, parameters)
    assert pilot.df_load_p.shape == (HOURS_PER_YEAR, 99)
    assert pilot.df_load_q.shape == (HOURS_PER_YEAR, 99)
    assert list(pilot.df_load_p.columns) == [
        load.device_id for load in pilot.loads
    ]
    assert list(pilot.df_nd.columns) == [
        unit.device_id for unit in pilot.nondispatchable
    ]
    annual_load = float(pilot.df_load_p.to_numpy().sum())
    annual_renewable = float(pilot.df_nd.to_numpy().sum())
    assert annual_renewable / annual_load == pytest.approx(
        parameters.renewable_energy_share
    )
    peak_load = float(pilot.df_load_p.sum(axis=1).max())
    assert sum(unit.apparent_power_rating for unit in pilot.storage) == (
        pytest.approx(parameters.storage_power_fraction_of_peak * peak_load)
    )
    assert [unit.bus for unit in pilot.storage] == [41, 65, 89, 105]
    assert len({unit.device_id for unit in pilot.storage}) == 4
    for unit in pilot.storage:
        assert unit.capacity == pytest.approx(
            unit.apparent_power_rating * parameters.storage_duration_hours
        )
        assert unit.initial_soc == pytest.approx(0.5 * unit.capacity)
        assert unit.terminal_soc == pytest.approx(unit.initial_soc)
        assert unit.terminal_constraint == "equality"


def test_pilot_rejects_outcome_driven_parameter_invention(converted_case):
    with pytest.raises(ValueError, match="predeclared"):
        materialize_pilot(converted_case, PilotParameters(0.2, 0.08, 6.0))


def test_common_pilot_window_selection_is_frozen_and_nonwrapping(converted_case):
    selection = select_pilot_window(converted_case)
    assert selection.start == 3757
    assert selection.stop == 3763
    assert selection.split == 5
    assert selection.score_mw == pytest.approx(1182.9499038)
    assert selection.start_timestamp == "2025-06-06T13:00:00+00:00"
    assert selection.stop_timestamp == "2025-06-06T18:00:00+00:00"
    assert selection.reference_net_load_sha256 == (
        "03a77b95e5ae3437df5b02f710a5bd6cd3f4f1ac50035640050ef10a10f9d46e"
    )
    assert selection.stop <= HOURS_PER_YEAR


def test_lossy_dc_probe_passes_independent_audit_and_detects_drift(
    converted_case,
):
    pilot = materialize_pilot(converted_case, PILOT_GRID[0])
    generators = gen_from_matpower(
        converted_case["gen"], converted_case["gencost"]
    )
    with pytest.warns(UserWarning) as captured:
        build = build_opf_multistep(
            converted_case,
            T=1,
            formulation="lossy_dc",
            generators=generators,
            loads=list(pilot.loads),
            df_load_p=pilot.df_load_p.iloc[:1],
            df_load_q=pilot.df_load_q.iloc[:1],
            nondispatchable=list(pilot.nondispatchable),
            df_nd=pilot.df_nd.iloc[:1],
            storage=list(pilot.storage),
        )
    messages = [str(item.message) for item in captured]
    assert any("retained as reactive load" in message for message in messages)
    assert any("real power limit only" in message for message in messages)
    build.solve()
    result = extract_results(build)
    audit = audit_probe(
        converted_case,
        build,
        result,
        generators=generators,
        loads=pilot.loads,
        nondispatchable=pilot.nondispatchable,
        storage=pilot.storage,
    )
    assert audit.accepted_primal
    assert max(audit.residuals.values()) < 1e-10

    changed = dict(result)
    changed["p_net"] = np.asarray(result["p_net"]).copy()
    changed["p_net"][0, 0] += 1.0
    rejected = audit_probe(
        converted_case,
        build,
        changed,
        generators=generators,
        loads=pilot.loads,
        nondispatchable=pilot.nondispatchable,
        storage=pilot.storage,
    )
    assert not rejected.accepted_primal
    assert rejected.residuals["dc_injection_reporting_mw_abs"] == pytest.approx(
        1.0
    )


def _synthetic_accepted_ac_probe(converted_case):
    pilot = materialize_pilot(converted_case, PILOT_GRID[0])
    generators = gen_from_matpower(
        converted_case["gen"], converted_case["gencost"]
    )
    result = {
        "status": "optimal",
        "objective": 0.0,
        "b": np.zeros((1, 4)),
        "b_q": np.zeros((1, 4)),
        "soc": np.array([[unit.initial_soc for unit in pilot.storage]]),
        "Pg": np.zeros((1, 54)),
        "Qg": np.zeros((1, 54)),
        "Vm": np.ones((1, 118)),
        "Va_deg": np.zeros((1, 118)),
        "p_net": np.zeros((1, 118)),
        "q_net": np.zeros((1, 118)),
        "branch_p_from": np.zeros((1, 186)),
        "branch_q_from": np.zeros((1, 186)),
        "branch_p_to": np.zeros((1, 186)),
        "branch_q_to": np.zeros((1, 186)),
        "branch_s_from": np.zeros((1, 186)),
        "branch_s_to": np.zeros((1, 186)),
        "p_load": np.zeros((1, 99)),
        "q_load": np.zeros((1, 99)),
        "p_load_served": np.zeros((1, 99)),
        "q_load_served": np.zeros((1, 99)),
        "p_nd": np.zeros((1, 2)),
        "q_nd": np.zeros((1, 2)),
        "curtailment": np.zeros((1, 2)),
        "storage_device_ids": np.array(
            [unit.device_id for unit in pilot.storage]
        ),
    }
    build = OPFBuild(
        prob=cp.Problem(cp.Minimize(0.0)),
        variables={},
        data={},
        formulation="ac",
        is_convex=False,
    )
    arguments = {
        "generators": generators,
        "loads": pilot.loads,
        "nondispatchable": pilot.nondispatchable,
        "storage": pilot.storage,
    }
    return pilot, build, result, arguments


@pytest.mark.parametrize(
    "missing_field", ["curtailment", "branch_p_from", "branch_p_to"]
)
def test_ac_probe_requires_nonnegativity_evidence(
    missing_field, converted_case
):
    _, build, result, arguments = _synthetic_accepted_ac_probe(converted_case)
    assert audit_probe(converted_case, build, result, **arguments).accepted_primal
    del result[missing_field]
    audit = audit_probe(converted_case, build, result, **arguments)
    assert not audit.accepted_primal
    assert missing_field in audit.missing_or_nonfinite_fields


def test_ac_probe_rejects_material_negative_curtailment(converted_case):
    _, build, result, arguments = _synthetic_accepted_ac_probe(converted_case)
    result["curtailment"][0, 0] = -1.0
    audit = audit_probe(converted_case, build, result, **arguments)
    assert not audit.accepted_primal
    assert audit.residuals["curtailment_nonnegativity_pu_abs"] == pytest.approx(
        0.01
    )


def test_ac_probe_rejects_material_negative_branch_loss(converted_case):
    _, build, result, arguments = _synthetic_accepted_ac_probe(converted_case)
    result["branch_p_to"][0, 0] = -1.0
    audit = audit_probe(converted_case, build, result, **arguments)
    assert not audit.accepted_primal
    assert audit.residuals[
        "branch_loss_nonnegativity_pu_abs"
    ] == pytest.approx(0.01)


def test_s0_runner_problem_dimensions_count_scalar_structure():
    x = cp.Variable(3)
    build = OPFBuild(
        prob=cp.Problem(cp.Minimize(cp.sum(x)), [x == 1.0, x >= 0.0]),
        variables={"x": x},
        data={},
        formulation="lossy_dc",
        is_convex=True,
    )
    assert run_s0._problem_dimensions(build) == {
        "scalar_variables": 3,
        "scalar_equalities": 3,
        "explicit_scalar_inequalities": 3,
        "other_scalar_constraints": 0,
        "constraint_objects": 2,
    }


def test_s0_runner_writes_deterministic_atomic_gzip(tmp_path):
    path = tmp_path / "artifact.json.gz"
    payload = {"array": np.array([1.0, 2.0]), "status": "complete"}
    run_s0._atomic_gzip_json(path, payload)
    first = path.read_bytes()
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        assert json.load(stream) == {"array": [1.0, 2.0], "status": "complete"}
    run_s0._atomic_gzip_json(path, payload)
    assert path.read_bytes() == first
    assert first[4:8] == b"\x00\x00\x00\x00"


def test_s0_artifact_registers_all_four_cases_before_execution(monkeypatch):
    observed = []

    def fake_run(network, formulation, case, window_start, window_stop):
        observed.append((network, formulation))
        record = {
            "network": network,
            "formulation": formulation,
            "accepted_primal": True,
        }
        if network == "pglib_rated" and formulation == "ac":
            record["movement_gate"] = {"passed": True}
        return record

    monkeypatch.setattr(run_s0, "_run_case", fake_run)
    artifact = run_s0.build_artifact()
    assert observed == [
        ("pglib_rated", "lossy_dc"),
        ("pglib_rated", "ac"),
        ("pglib_effectively_unlimited", "lossy_dc"),
        ("pglib_effectively_unlimited", "ac"),
    ]
    assert artifact["all_accepted"] is True
    assert artifact["horizon_steps"] == 6
    assert artifact["branch_limit_sentinel_mw"] == 1e6
    assert artifact["rated_ac_movement_gate_passed"] is True
    assert artifact["window_selection"]["start"] == 3757


def test_s0_movement_gate_requires_power_and_throughput(converted_case):
    pilot = materialize_pilot(converted_case, PILOT_GRID[0])
    power = np.zeros((6, len(pilot.storage)))
    result = {"b": power}
    zero = run_s0._movement_gate(pilot.storage, result, 100.0)
    assert not zero["passed"]
    thresholds = np.asarray(zero["power_threshold_by_storage_mw"])
    throughput_threshold = float(zero["throughput_threshold_mwh"])

    power[0, 0] = 2.0 * thresholds[0]
    instantaneous_only = run_s0._movement_gate(
        pilot.storage, result, 100.0
    )
    assert instantaneous_only["instantaneous_passed"]
    assert not instantaneous_only["throughput_passed"]
    assert not instantaneous_only["passed"]

    power[:, 0] = max(
        2.0 * thresholds[0], 2.0 * throughput_threshold / len(power)
    )
    complete = run_s0._movement_gate(pilot.storage, result, 100.0)
    assert complete["instantaneous_passed"]
    assert complete["throughput_passed"]
    assert complete["passed"]


def test_s0_completion_metadata_freezes_repository_comparator_and_s1_limits():
    metadata = json.loads(
        (EXPERIMENT_DIRECTORY / "S0_COMPLETION_METADATA.json").read_text()
    )
    comparator = metadata["repository_case118_comparator"]
    repository_case = case118()
    for table, expected in comparator["array_sha256"].items():
        values = np.ascontiguousarray(np.asarray(repository_case[table]))
        assert hashlib.sha256(values.tobytes()).hexdigest() == expected

    policy = metadata["s1_resource_policy"]
    assert policy["process_rss_limit_gib"] == 16.0
    assert policy["per_ac_solve_wall_time_minutes"] == 45.0
    assert policy["complete_s1_wall_time_hours"] == 2.0
    assert policy["direct_ac_maximum_authorized_horizon_hours"] == 6
    assert (
        policy["direct_ac_24_hour_classification"]
        == "not_authorized_by_s0_resource_gate"
    )


def test_s1_worker_registers_outer_endpoint_and_forbidden_direct_ac(
    monkeypatch, tmp_path
):
    outer_soc = np.tile(np.arange(1.0, 25.0)[:, None], (1, 4))

    def fake_build_and_solve(
        network, formulation, case, storage, start, stop
    ):
        del network, case, storage
        if formulation == "lossy_dc":
            return {
                "record_id": "outer_lossy_dc_24h",
                "classification": "accepted",
                "accepted_primal": True,
            }, {"soc": outer_soc}
        assert (start, stop) == (run_s1.AC_START, run_s1.AC_STOP)
        return {
            "record_id": "endpoint_ac_6h",
            "classification": "accepted",
            "accepted_primal": True,
        }, {}

    monkeypatch.setattr(run_s1, "_build_and_solve", fake_build_and_solve)
    checkpoint = tmp_path / "worker.json.gz"
    artifact = run_s1.build_worker_artifact("pglib_rated", checkpoint)
    records = artifact["records"]
    assert [record["record_id"] for record in records] == [
        "outer_lossy_dc_24h",
        "endpoint_ac_6h",
        "direct_ac_24h",
    ]
    handoff = records[1]["outer_boundary_handoff"]
    np.testing.assert_array_equal(handoff["initial_soc_mwh"], 13.0)
    np.testing.assert_array_equal(handoff["target_soc_mwh"], 19.0)
    assert records[2]["classification"] == (
        "not_authorized_by_s0_resource_gate"
    )
    assert records[2]["builder_called"] is False
    assert records[2]["solver_called"] is False
    assert artifact["eligible_for_advancement"] is True
    assert checkpoint.exists()


def test_s1_outer_failure_retains_all_slots_without_constructing_ac(
    monkeypatch, tmp_path
):
    calls = []

    def fake_build_and_solve(
        network, formulation, case, storage, start, stop
    ):
        del network, case, storage, start, stop
        calls.append(formulation)
        return {
            "record_id": "outer_lossy_dc_24h",
            "accepted_primal": False,
        }, {}

    monkeypatch.setattr(run_s1, "_build_and_solve", fake_build_and_solve)
    artifact = run_s1.build_worker_artifact(
        "pglib_rated", tmp_path / "worker.json.gz"
    )
    assert calls == ["lossy_dc"]
    records = artifact["records"]
    assert len(records) == 3
    assert records[1]["classification"] == "source_unavailable"
    assert records[1]["builder_called"] is False
    assert records[1]["solver_called"] is False
    assert artifact["eligible_for_advancement"] is False


@pytest.mark.parametrize("classification", ["resource_limit", "worker_failure"])
def test_s1_precheckpoint_failure_preserves_complete_registry(classification):
    artifact = run_s1._unexecuted_worker(
        "pglib_rated", classification, "synthetic boundary"
    )
    records = artifact["records"]
    assert [record["record_id"] for record in records] == [
        "outer_lossy_dc_24h",
        "endpoint_ac_6h",
        "direct_ac_24h",
    ]
    assert records[0]["classification"] == classification
    assert records[1]["classification"] == "source_unavailable"
    assert records[2]["classification"] == (
        "not_authorized_by_s0_resource_gate"
    )


@pytest.mark.parametrize(
    ("status", "exception", "accepted", "expected"),
    [
        ("optimal", None, True, "accepted"),
        ("infeasible", None, False, "solver_certified_infeasible"),
        (
            "infeasible_inaccurate",
            None,
            False,
            "solver_certified_infeasible",
        ),
        (None, "SolverError: failed", False, "solver_failure"),
        ("optimal", None, False, "unusable_primal"),
        ("user_limit", None, False, "unusable_primal"),
    ],
)
def test_s1_solve_classification_is_unambiguous(
    status, exception, accepted, expected
):
    assert run_s1._classify_solve(status, exception, accepted) == expected


def test_s1_resource_limit_overrides_worker_advancement_not_solve_evidence():
    context = {"git_commit": "abc", "source_fingerprint": "def"}
    accepted_endpoint = {
        "record_id": "endpoint_ac_6h",
        "classification": "accepted",
        "accepted_primal": True,
    }
    artifact = {
        "records": [accepted_endpoint],
        "start_context": context,
        "end_context": context,
    }
    run_s1._apply_supervision_outcome(
        artifact,
        limit_reason="rss_limit",
        returncode=-15,
        expected_context=context,
    )
    assert accepted_endpoint["classification"] == "accepted"
    assert artifact["worker_classification"] == "resource_limit"
    assert artifact["eligible_for_advancement"] is False


def test_s1_parent_rejects_worker_context_mismatch():
    expected = {"git_commit": "abc", "source_fingerprint": "def"}
    artifact = {
        "records": [],
        "start_context": expected,
        "end_context": {**expected, "git_commit": "changed"},
    }
    run_s1._apply_supervision_outcome(
        artifact,
        limit_reason=None,
        returncode=0,
        expected_context=expected,
    )
    assert artifact["worker_classification"] == "provenance_mismatch"
    assert artifact["eligible_for_advancement"] is False


def test_s1_clean_worker_with_rejected_outer_is_not_eligible():
    context = {"git_commit": "abc", "source_fingerprint": "def"}
    artifact = run_s1._unexecuted_worker(
        "pglib_rated",
        "solver_certified_infeasible",
        "synthetic infeasible outer",
    )
    artifact["start_context"] = context
    artifact["end_context"] = context
    run_s1._apply_supervision_outcome(
        artifact,
        limit_reason=None,
        returncode=0,
        expected_context=context,
    )
    assert artifact["worker_classification"] == "completed"
    assert artifact["eligible_for_advancement"] is False


@pytest.mark.parametrize(
    ("endpoint_classification", "expected"),
    [("pending", True), ("source_unavailable", False), ("accepted", False)],
)
def test_s1_ac_clock_marker_requires_pending_endpoint(
    tmp_path, endpoint_classification, expected
):
    checkpoint = tmp_path / "checkpoint.json.gz"
    run_s0._atomic_gzip_json(
        checkpoint,
        {
            "records": [
                {
                    "record_id": "endpoint_ac_6h",
                    "classification": endpoint_classification,
                }
            ]
        },
    )
    assert run_s1._checkpoint_endpoint_pending(checkpoint) is expected


def test_s1_tracked_record_binds_runner_and_nonexecution_contract():
    metadata = json.loads(
        (EXPERIMENT_DIRECTORY / "S1_RESULTS_METADATA.json").read_text()
    )
    runner = EXPERIMENT_DIRECTORY / "run_s1.py"
    assert hashlib.sha256(runner.read_bytes()).hexdigest() == (
        metadata["execution_source"]["runner_sha256"]
    )
    assert metadata["status"] == "complete"
    assert all(
        worker["eligible_for_advancement"]
        and worker["parent_context_matches"]
        and worker["direct_ac_24h"]
        == "not_authorized_by_s0_resource_gate"
        for worker in metadata["workers"]
    )
