"""Constructor-only tests for the public M17-S4 typed contract."""

from dataclasses import FrozenInstanceError, replace

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import (
    ACCEPTED_SOLVER_STATUSES,
    ACAttemptRecord,
    HierarchicalAcceptanceTolerances,
    HierarchicalInputs,
    HierarchicalPolicy,
    HierarchicalProvenance,
    HierarchicalResult,
    HierarchicalSolveAudit,
    HierarchicalSolveConfig,
    IPOPTStartEvidence,
    LayerSolveConfig,
    Load,
    OPFBuild,
    OPFOptions,
    OuterPlanRecord,
    ShiftedRecoveryConfig,
    StorageUnitIdeal,
    ExecutedIntervalRecord,
    gen_from_matpower,
)
from cvxopf.testcases import case9


def _inputs(**overrides):
    case = case9()
    load = Load(5, 90.0, "load-5", q_load_mvar=30.0)
    storage = StorageUnitIdeal(
        bus=7,
        apparent_power_rating=125.0,
        capacity=1_000.0,
        initial_soc=500.0,
        terminal_soc=500.0,
        terminal_constraint="equality",
        device_id="battery-7",
    )
    values = {
        "case": case,
        "horizon_steps": 2,
        "delta": 1.0,
        "generators": tuple(gen_from_matpower(case["gen"], case["gencost"])),
        "loads": (load,),
        "storage": (storage,),
        "df_load_p": pd.DataFrame(
            [[90.0], [95.0]], columns=["load-5"]
        ),
        "df_load_q": pd.DataFrame(
            [[30.0], [31.0]], columns=["load-5"]
        ),
    }
    values.update(overrides)
    return HierarchicalInputs(**values)


def _build():
    variable = cp.Variable(1, name="x")
    return OPFBuild(cp.Problem(cp.Minimize(cp.sum_squares(variable))), {}, {}, "ac", False)


def _audit(*, accepted=True, status="optimal", residuals=None, **overrides):
    residual_values = {
        "soc_recurrence_mwh_abs": 0.0,
        "terminal_soc_mwh_abs": 0.0,
        "ac_active_balance_pu_abs": 0.0,
        "ac_reactive_balance_pu_abs": 0.0,
        "dc_injection_reporting_mw_abs": 0.0,
        "dc_nodal_balance_pu_abs": 0.0,
        "voltage_bound_pu_abs": 0.0,
        "branch_mva_abs": 0.0,
        "branch_normalized_squared_residual": 0.0,
        "curtailment_nonnegativity_pu_abs": 0.0,
        "branch_loss_nonnegativity_pu_abs": 0.0,
    }
    if residuals is not None:
        residual_values = residuals
    values = {
        "status": status,
        "outcome": "accepted" if accepted else "unusable_primal",
        "accepted_primal": accepted,
        "missing_or_nonfinite_fields": (),
        "identity_error": None,
        "residuals": residual_values,
        "exception": None,
        "wall_time_seconds": 0.1,
        "solver_num_iters": None,
        "solver_setup_time_seconds": None,
        "solver_solve_time_seconds": None,
    }
    values.update(overrides)
    return HierarchicalSolveAudit(**values)


def _evidence():
    return IPOPTStartEvidence(
        complete_x0=np.array([1.0, 0.0]),
        layout=(
            {
                "name": "x",
                "start": 0,
                "stop": 1,
                "is_original_variable": True,
            },
            {
                "name": "aux",
                "start": 1,
                "stop": 2,
                "is_original_variable": False,
            },
        ),
        layout_signature="signature",
        model_coordinate_count=1,
        auxiliary_coordinate_count=1,
        object_ids_before={"variables": (1,)},
        object_ids_after={"variables": (1,)},
    )


def _attempt(**overrides):
    values = {
        "attempt_id": "ac-000-00",
        "slot_state": "executed",
        "role": "primary_controlling",
        "transformation": "flat",
        "ordinal": 0,
        "iteration": 0,
        "local_interval_start": 0,
        "local_interval_stop": 2,
        "global_interval_start": 0,
        "global_interval_stop": 2,
        "outer_plan_id": "outer-000",
        "source_kind": "generated_flat",
        "source_attempt_id": None,
        "inner_terminal_policy": "hard_equality",
        "storage_device_ids": ("battery-7",),
        "initial_soc_mwh": {"battery-7": 500.0},
        "target_soc_mwh": {"battery-7": 500.0},
        "terminal_deviation_mwh": {"battery-7": 0.0},
        "build": _build(),
        "raw_start": {"x": np.array([1.0])},
        "assigned_start": {"x": np.array([1.0])},
        "solver_evidence": _evidence(),
        "result": {"status": "optimal", "b": np.array([[0.0]])},
        "audit": _audit(),
        "reason": None,
        "supplied_executed_action": True,
    }
    values.update(overrides)
    return ACAttemptRecord(**values)


def _outer_record(**overrides):
    values = {
        "outer_plan_id": "outer-000",
        "created_iteration": 0,
        "global_interval_start": 0,
        "global_interval_stop": 2,
        "local_boundary_indices": np.array([0, 1, 2]),
        "global_boundary_indices": np.array([0, 1, 2]),
        "storage_device_ids": ("battery-7",),
        "terminal_modes": {"battery-7": "equality"},
        "boundary_soc_mwh": np.array([[500.0], [500.0], [500.0]]),
        "build": _build(),
        "result": {"status": "optimal"},
        "audit": _audit(),
    }
    values.update(overrides)
    return OuterPlanRecord(**values)


def _executed_interval(**overrides):
    values = {
        "iteration": 0,
        "controlling_attempt_id": "ac-000-00",
        "generation_cost": 10.0,
        "storage_cycling_cost": 0.1,
        "renewable_curtailment_mwh": 0.0,
        "active_loss_mwh": 0.2,
        "active_loss_crosscheck_mw_abs": 1e-10,
        "state_transition_residual_mwh_abs": 1e-10,
        "voltage_violation_pu": 0.0,
        "thermal_residual_mva": 0.0,
        "normalized_squared_thermal_residual": 0.0,
    }
    values.update(overrides)
    return ExecutedIntervalRecord(**values)


def _result(**overrides):
    values = {
        "policy": HierarchicalPolicy(
            ac_window_steps=1, initialization_policy="flat_only"
        ),
        "provenance": HierarchicalProvenance(
            HierarchicalSolveConfig(), {"cvxpy": "1.9.2"}
        ),
        "horizon_steps": 1,
        "delta": 1.0,
        "storage_device_ids": ("battery-7",),
        "outer_plans": {
            "outer-000": _outer_record(
                global_interval_stop=1,
                local_boundary_indices=np.array([0, 1]),
                global_boundary_indices=np.array([0, 1]),
                boundary_soc_mwh=np.array([[500.0], [500.0]]),
            )
        },
        "ac_attempts": (
            _attempt(local_interval_stop=1, global_interval_stop=1),
        ),
        "executed_intervals": (_executed_interval(),),
        "realized_soc_mwh": np.array([[500.0], [500.0]]),
        "executed_b_mw": np.array([[0.0]]),
        "trajectory_summary": {"generation_cost": 10.0},
        "completed_intervals": 1,
        "completion_fraction": 1.0,
        "completed": True,
        "termination_iteration": None,
        "termination_reason": None,
    }
    values.update(overrides)
    return HierarchicalResult(**values)


def _skipped_recovery_attempt(ordinal, *, iteration=0):
    roles = (
        "primary_controlling",
        "target_free",
        "copied_target_free",
        "perturbed_target_free",
        "perturbed_target_free",
        "perturbed_target_free",
        "perturbed_causal",
        "perturbed_causal",
        "perturbed_causal",
    )
    causal_transformation = "flat" if iteration == 0 else "shifted_preceding"
    transformations = (
        causal_transformation,
        causal_transformation,
        "copy_target_free",
        "perturb_target_free",
        "perturb_target_free",
        "perturb_target_free",
        "perturb_causal",
        "perturb_causal",
        "perturb_causal",
    )
    scales = (None, None, None, 1e-4, 1e-3, 1e-2, 1e-4, 1e-3, 1e-2)
    seeds = (
        None,
        None,
        None,
        17_000_011 + 100 * iteration,
        17_000_012 + 100 * iteration,
        17_000_013 + 100 * iteration,
        17_000_021 + 100 * iteration,
        17_000_022 + 100 * iteration,
        17_000_023 + 100 * iteration,
    )
    return _attempt(
        attempt_id=f"ac-{iteration:03d}-{ordinal:02d}",
        slot_state="not_needed_after_acceptance",
        role=roles[ordinal],
        transformation=transformations[ordinal],
        ordinal=ordinal,
        iteration=iteration,
        local_interval_stop=1,
        global_interval_start=iteration,
        global_interval_stop=iteration + 1,
        outer_plan_id=f"outer-{iteration:03d}",
        source_kind=None,
        source_attempt_id=None,
        build=None,
        raw_start=None,
        assigned_start=None,
        solver_evidence=None,
        result=None,
        audit=None,
        terminal_deviation_mwh=None,
        reason="earlier controlling attempt accepted",
        supplied_executed_action=False,
        scale=scales[ordinal],
        seed=seeds[ordinal],
    )


def _recovery_registry(replacements=None, *, iteration=0):
    records = [
        _skipped_recovery_attempt(index, iteration=iteration)
        for index in range(9)
    ]
    records[0] = _attempt(
        attempt_id=f"ac-{iteration:03d}-00",
        transformation="flat" if iteration == 0 else "shifted_preceding",
        iteration=iteration,
        local_interval_stop=1,
        global_interval_start=iteration,
        global_interval_stop=iteration + 1,
        outer_plan_id=f"outer-{iteration:03d}",
    )
    for ordinal, record in (replacements or {}).items():
        records[ordinal] = record
    return tuple(records)


class TestPolicy:
    @pytest.mark.parametrize("value", [True, 1.5])
    def test_window_steps_must_be_an_integer(self, value):
        with pytest.raises(TypeError, match="must be an integer"):
            HierarchicalPolicy(ac_window_steps=value)

    def test_window_steps_must_be_positive(self):
        with pytest.raises(ValueError, match="must be positive"):
            HierarchicalPolicy(ac_window_steps=0)

    def test_reference_defaults_are_explicit(self):
        policy = HierarchicalPolicy(ac_window_steps=5)

        assert policy.outer_policy == "replan_every_step"
        assert policy.inner_terminal_policy == "hard_equality"
        assert policy.initialization_policy == "shifted_with_recovery"
        assert policy.recovery == ShiftedRecoveryConfig()
        assert policy.quadratic_soft_weight is None

    def test_quadratic_soft_requires_positive_weight(self):
        with pytest.raises(ValueError, match="requires quadratic_soft_weight"):
            HierarchicalPolicy(
                ac_window_steps=5,
                inner_terminal_policy="quadratic_soft",
            )
        with pytest.raises(ValueError, match="must be positive"):
            HierarchicalPolicy(
                ac_window_steps=5,
                inner_terminal_policy="quadratic_soft",
                quadratic_soft_weight=0.0,
            )

    def test_flat_only_rejects_recovery_configuration(self):
        with pytest.raises(ValueError, match="cannot define recovery"):
            HierarchicalPolicy(
                ac_window_steps=5,
                initialization_policy="flat_only",
                recovery=ShiftedRecoveryConfig(),
            )

    def test_recovery_scales_are_ordered_unique_and_immutable(self):
        with pytest.raises(ValueError, match="exactly three"):
            ShiftedRecoveryConfig((1e-4, 1e-3))
        with pytest.raises(ValueError, match="must be unique"):
            ShiftedRecoveryConfig((1e-4, 1e-4, 1e-2))
        with pytest.raises(ValueError, match="strictly increasing"):
            ShiftedRecoveryConfig((1e-3, 1e-4, 1e-2))
        config = ShiftedRecoveryConfig([1e-4, 1e-3, 1e-2])
        assert config.perturbation_scales == (1e-4, 1e-3, 1e-2)
        with pytest.raises(FrozenInstanceError):
            config.seed_base = 1

    @pytest.mark.parametrize("seed", [True, 1.5])
    def test_recovery_seed_must_be_an_integer(self, seed):
        with pytest.raises(TypeError, match="seed_base must be an integer"):
            ShiftedRecoveryConfig(seed_base=seed)

    def test_recovery_seed_must_be_nonnegative(self):
        with pytest.raises(ValueError, match="seed_base must be nonnegative"):
            ShiftedRecoveryConfig(seed_base=-1)

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("outer_policy", "other", "unsupported outer_policy"),
            ("inner_terminal_policy", "other", "inner_terminal_policy"),
            ("initialization_policy", "other", "initialization_policy"),
        ],
    )
    def test_policy_enums_are_closed(self, field, value, message):
        with pytest.raises(ValueError, match=message):
            HierarchicalPolicy(ac_window_steps=1, **{field: value})

    def test_hard_policy_rejects_soft_weight(self):
        with pytest.raises(ValueError, match="valid only for quadratic_soft"):
            HierarchicalPolicy(ac_window_steps=1, quadratic_soft_weight=1.0)

    def test_policy_requires_typed_recovery_and_tolerances(self):
        with pytest.raises(TypeError, match="recovery must be"):
            HierarchicalPolicy(ac_window_steps=1, recovery={})
        with pytest.raises(TypeError, match="tolerances must be"):
            HierarchicalPolicy(ac_window_steps=1, tolerances={})

    @pytest.mark.parametrize("value", [True, "1", np.inf])
    def test_tolerance_values_must_be_finite_real_scalars(self, value):
        error = TypeError if value is True or isinstance(value, str) else ValueError
        with pytest.raises(error):
            HierarchicalAcceptanceTolerances(soc_recurrence_mwh_abs=value)

    def test_tolerances_are_finite_nonnegative(self):
        with pytest.raises(ValueError, match="must be nonnegative"):
            HierarchicalAcceptanceTolerances(ac_active_balance_pu_abs=-1.0)
        with pytest.raises(ValueError, match="cannot be looser"):
            HierarchicalPolicy(
                ac_window_steps=5,
                tolerances=HierarchicalAcceptanceTolerances(
                    ac_active_balance_pu_abs=2e-6
                ),
            )
        stricter = HierarchicalPolicy(
            ac_window_steps=5,
            tolerances=HierarchicalAcceptanceTolerances(
                ac_active_balance_pu_abs=5e-7
            ),
        )
        assert stricter.tolerances.ac_active_balance_pu_abs == 5e-7


class TestSolveConfiguration:
    def test_layer_options_are_copied_and_read_only(self):
        source = {"max_iter": 100, "verbose": False}
        config = LayerSolveConfig("ipopt", source)
        source["max_iter"] = 1

        assert config.solver == "IPOPT"
        assert config.options["max_iter"] == 100
        with pytest.raises(TypeError):
            config.options["max_iter"] = 2

    @pytest.mark.parametrize("key", ["solver", "nlp", "unknown"])
    def test_unowned_or_unknown_solve_options_are_rejected(self, key):
        with pytest.raises(ValueError):
            LayerSolveConfig("IPOPT", {key: "IPOPT"})

    def test_solve_option_types_and_ranges_are_validated(self):
        with pytest.raises(TypeError, match="must be an integer"):
            LayerSolveConfig("IPOPT", {"max_iter": True})
        with pytest.raises(ValueError, match="must be positive"):
            LayerSolveConfig("CLARABEL", {"tol_feas": 0.0})

    @pytest.mark.parametrize(
        ("solver", "options", "error", "message"),
        [
            ("IPOPT", {"verbose": 1}, TypeError, "must be Boolean"),
            ("IPOPT", {"print_level": -1}, ValueError, "nonnegative"),
            ("IPOPT", {"print_level": 13}, ValueError, "must not exceed"),
            ("IPOPT", {"sb": "maybe"}, ValueError, "must be one of"),
            ("IPOPT", {"mu_strategy": ""}, ValueError, "nonempty string"),
            ("IPOPT", {"max_cpu_time": np.inf}, ValueError, "finite"),
        ],
    )
    def test_each_solve_option_kind_is_validated(
        self, solver, options, error, message
    ):
        with pytest.raises(error, match=message):
            LayerSolveConfig(solver, options)

    def test_string_solve_options_are_normalized(self):
        config = LayerSolveConfig(
            "IPOPT",
            {
                "sb": "yes",
                "mu_strategy": "adaptive",
                "linear_solver": "mumps",
                "warm_start_init_point": "no",
            },
        )
        assert dict(config.options) == {
            "sb": "yes",
            "mu_strategy": "adaptive",
            "linear_solver": "mumps",
            "warm_start_init_point": "no",
        }

    def test_layer_configuration_rejects_bad_container_values(self):
        with pytest.raises(ValueError, match="unsupported hierarchical solver"):
            LayerSolveConfig("SCS")
        with pytest.raises(TypeError, match="must be a mapping"):
            LayerSolveConfig("IPOPT", [])
        with pytest.raises(TypeError, match="must be LayerSolveConfig"):
            HierarchicalSolveConfig(outer="CLARABEL")

    def test_layer_roles_are_closed(self):
        with pytest.raises(ValueError, match="outer.*CLARABEL"):
            HierarchicalSolveConfig(
                outer=LayerSolveConfig("IPOPT"),
                ac=LayerSolveConfig("IPOPT"),
            )
        with pytest.raises(ValueError, match="AC.*IPOPT"):
            HierarchicalSolveConfig(
                outer=LayerSolveConfig("CLARABEL"),
                ac=LayerSolveConfig("CLARABEL"),
            )


class TestInputs:
    def test_inputs_copy_case_options_devices_and_frames(self):
        case = case9()
        frame = pd.DataFrame([[90.0], [95.0]], columns=["load-5"])
        options = OPFOptions()
        inputs = _inputs(case=case, df_load_p=frame, options=options)

        case["bus"][0, 0] = 999
        frame.iloc[0, 0] = 999.0
        options.loss_weight = 999.0

        assert inputs.case["bus"][0, 0] == 1
        assert inputs.df_load_p.iloc[0, 0] == 90.0
        assert inputs.options.loss_weight == 1.0
        assert not inputs.case["bus"].flags.writeable
        assert inputs.storage_device_ids == ("battery-7",)

    def test_init_flat_false_is_rejected(self):
        with pytest.raises(ValueError, match="init_flat=True"):
            _inputs(options=OPFOptions(init_flat=False))

    def test_disabled_ac_branch_limits_are_rejected(self):
        with pytest.raises(ValueError, match="enforce_branch_limits=True"):
            _inputs(options=OPFOptions(enforce_branch_limits=False))

    def test_storage_identity_is_required_and_unique(self):
        missing = StorageUnitIdeal(7, 10.0, 20.0, 10.0)
        with pytest.raises(ValueError, match=r"storage\[0\].device_id"):
            _inputs(storage=(missing,))
        duplicate = StorageUnitIdeal(
            7, 10.0, 20.0, 10.0, device_id="battery-7"
        )
        with pytest.raises(ValueError, match="must be unique"):
            _inputs(storage=(_inputs().storage[0], duplicate))

    def test_identity_aligned_frames_are_required(self):
        wrong = pd.DataFrame([[90.0], [95.0]], columns=["wrong"])
        with pytest.raises(ValueError, match="exactly match device order"):
            _inputs(df_load_p=wrong)
        short = pd.DataFrame([[90.0]], columns=["load-5"])
        with pytest.raises(ValueError, match="must have 2 rows"):
            _inputs(df_load_p=short)

    def test_sheddable_loads_are_deferred(self):
        load = Load(5, 90.0, "load-5", shedding_cost_per_mwh=1_000.0)
        with pytest.raises(ValueError, match="does not yet support"):
            _inputs(loads=(load,))

    def test_explicit_generator_fleet_cannot_be_empty(self):
        with pytest.raises(ValueError, match="explicit generator"):
            _inputs(generators=())

    def test_input_container_types_and_fleet_members_are_validated(self):
        with pytest.raises(TypeError, match="case must be a mapping"):
            _inputs(case=[])
        with pytest.raises(TypeError, match="options must be OPFOptions"):
            _inputs(options={})
        with pytest.raises(TypeError, match="generators must contain"):
            _inputs(generators=(object(),))
        with pytest.raises(ValueError, match="at least one storage"):
            _inputs(storage=())

    @pytest.mark.parametrize("value", ["bad", pd.DataFrame([["bad"]])])
    def test_load_frame_must_be_a_finite_numeric_dataframe(self, value):
        with pytest.raises(TypeError):
            _inputs(df_load_p=value)

    def test_load_frame_rejects_nonfinite_values(self):
        with pytest.raises(ValueError, match="finite"):
            _inputs(
                df_load_p=pd.DataFrame(
                    [[90.0], [np.nan]], columns=["load-5"]
                )
            )

    def test_trajectory_indices_are_unique_and_aligned(self):
        p = pd.DataFrame(
            [[90.0], [95.0]], columns=["load-5"], index=[0, 0]
        )
        with pytest.raises(ValueError, match="index must be unique"):
            _inputs(df_load_p=p, df_load_q=None)
        q = pd.DataFrame(
            [[30.0], [31.0]], columns=["load-5"], index=[1, 2]
        )
        with pytest.raises(ValueError, match="indices must match"):
            _inputs(df_load_q=q)

    def test_hvdc_trajectory_bounds_must_be_paired(self):
        frame = pd.DataFrame([[0.0], [0.0]], columns=["link"])
        with pytest.raises(ValueError, match="must be supplied together"):
            _inputs(df_hvdc_min=frame)


class TestAttemptRecords:
    def test_executed_payload_is_copied_and_read_only(self):
        start = np.array([1.0])
        record = _attempt(assigned_start={"x": start})
        start[0] = 2.0

        assert record.assigned_start["x"] == pytest.approx([1.0])
        assert not record.assigned_start["x"].flags.writeable
        assert record.solver_evidence.complete_x0.shape == (2,)

    def test_retained_result_arrays_are_copied_and_read_only(self):
        source = np.array([[1.0]])
        nested = np.array([2.0])
        record = _attempt(result={"b": source, "nested": {"x": nested}})

        source[0, 0] = 9.0
        nested[0] = 9.0

        assert np.array_equal(record.result["b"], [[1.0]])
        assert np.array_equal(record.result["nested"]["x"], [2.0])
        with pytest.raises(ValueError, match="read-only"):
            record.result["b"][0, 0] = 3.0
        with pytest.raises(ValueError, match="read-only"):
            record.result["nested"]["x"][0] = 3.0

    def test_retained_result_sequences_and_sets_are_immutable(self):
        record = _attempt(result={"items": [np.array([1.0])], "tags": {"a"}})
        assert isinstance(record.result["items"], tuple)
        assert isinstance(record.result["tags"], frozenset)
        assert not record.result["items"][0].flags.writeable

    def test_outer_plan_result_arrays_are_copied_and_read_only(self):
        source = np.array([[500.0]])
        record = _outer_record(result={"soc": source})
        source[0, 0] = 0.0

        assert np.array_equal(record.result["soc"], [[500.0]])
        with pytest.raises(ValueError, match="read-only"):
            record.result["soc"][0, 0] = 0.0

    @pytest.mark.parametrize(
        "missing_field",
        [
            "build",
            "raw_start",
            "assigned_start",
            "solver_evidence",
            "result",
            "audit",
        ],
    )
    def test_executed_state_requires_complete_payload(self, missing_field):
        with pytest.raises(ValueError, match="requires payload"):
            _attempt(**{missing_field: None})

    def test_construction_error_allows_partial_construction_only(self):
        record = _attempt(
            slot_state="construction_error",
            raw_start=None,
            assigned_start=None,
            solver_evidence=None,
            result=None,
            audit=None,
            terminal_deviation_mwh=None,
            reason="canonicalization failed",
            supplied_executed_action=False,
        )
        assert record.build is not None
        with pytest.raises(ValueError, match="cannot retain solver evidence"):
            _attempt(
                slot_state="construction_error",
                result=None,
                audit=None,
                terminal_deviation_mwh=None,
                reason="failed",
                supplied_executed_action=False,
            )

    @pytest.mark.parametrize(
        "state", ["source_unavailable", "not_needed_after_acceptance"]
    )
    def test_skipped_states_reject_execution_payload(self, state):
        with pytest.raises(ValueError, match="cannot retain execution payload"):
            _attempt(
                slot_state=state,
                reason="not executed",
                supplied_executed_action=False,
            )
        record = _attempt(
            slot_state=state,
            build=None,
            raw_start=None,
            assigned_start=None,
            solver_evidence=None,
            result=None,
            audit=None,
            terminal_deviation_mwh=None,
            reason="not executed",
            supplied_executed_action=False,
        )
        assert record.slot_state == state

    def test_target_free_attempt_cannot_supply_action(self):
        with pytest.raises(ValueError, match="accepted controlling"):
            _attempt(role="target_free")

    def test_status_eligibility_is_fixed(self):
        assert ACCEPTED_SOLVER_STATUSES == {"optimal", "optimal_inaccurate"}
        with pytest.raises(ValueError, match="eligible solver status"):
            _audit(status="user_limit")

    @pytest.mark.parametrize(
        "kwargs",
        [
            {
                "accepted": False,
                "status": "optimal",
                "outcome": "solver_certified_infeasible",
            },
            {
                "accepted": False,
                "status": "infeasible",
                "outcome": "unusable_primal",
            },
            {
                "accepted": False,
                "status": "user_limit",
                "outcome": "solver_failure",
            },
            {
                "accepted": False,
                "status": "user_limit",
                "outcome": "unusable_primal",
                "exception": "backend error",
            },
        ],
    )
    def test_solve_outcome_matrix_rejects_contradictions(self, kwargs):
        with pytest.raises(ValueError):
            _audit(**kwargs)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("missing_or_nonfinite_fields", ("Vm",)),
            ("identity_error", "storage IDs differ"),
            ("exception", "solver failed"),
        ],
    )
    def test_accepted_audit_rejects_failed_gate_evidence(self, field, value):
        with pytest.raises(
            ValueError, match="accepted primal cannot|exception requires"
        ):
            _audit(**{field: value})

    def test_ipopt_layout_must_be_contiguous_and_identity_preserving(self):
        with pytest.raises(ValueError, match="contiguous"):
            IPOPTStartEvidence(
                complete_x0=np.array([1.0, 0.0]),
                layout=(
                    {
                        "name": "x",
                        "start": 1,
                        "stop": 2,
                        "is_original_variable": True,
                    },
                ),
                layout_signature="signature",
                model_coordinate_count=1,
                auxiliary_coordinate_count=1,
                object_ids_before={"variables": (1,)},
                object_ids_after={"variables": (1,)},
            )
        with pytest.raises(ValueError, match="object identity changed"):
            IPOPTStartEvidence(
                complete_x0=np.array([1.0]),
                layout=(
                    {
                        "name": "x",
                        "start": 0,
                        "stop": 1,
                        "is_original_variable": True,
                    },
                ),
                layout_signature="signature",
                model_coordinate_count=1,
                auxiliary_coordinate_count=0,
                object_ids_before={"variables": (1,)},
                object_ids_after={"variables": (2,)},
            )

    @pytest.mark.parametrize(
        ("overrides", "error", "message"),
        [
            ({"complete_x0": np.array([[1.0]])}, ValueError, "one-dimensional"),
            ({"auxiliary_coordinate_count": True}, TypeError, "must be an integer"),
            ({"auxiliary_coordinate_count": -1}, ValueError, "nonnegative"),
            ({"auxiliary_coordinate_count": 2}, ValueError, "counts"),
            ({"layout": ({"name": "x"},)}, ValueError, "must contain"),
            (
                {
                    "layout": (
                        {
                            "name": "x",
                            "start": 0.0,
                            "stop": 1,
                            "is_original_variable": True,
                        },
                    )
                },
                TypeError,
                "offsets must be integers",
            ),
            (
                {
                    "layout": (
                        {
                            "name": "x",
                            "start": 0,
                            "stop": 1,
                            "is_original_variable": 1,
                        },
                    )
                },
                TypeError,
                "must be Boolean",
            ),
        ],
    )
    def test_ipopt_start_evidence_rejects_malformed_layouts(
        self, overrides, error, message
    ):
        values = {
            "complete_x0": np.array([1.0, 0.0]),
            "layout": (
                {
                    "name": "x",
                    "start": 0,
                    "stop": 1,
                    "is_original_variable": True,
                },
                {
                    "name": "aux",
                    "start": 1,
                    "stop": 2,
                    "is_original_variable": False,
                },
            ),
            "layout_signature": "signature",
            "model_coordinate_count": 1,
            "auxiliary_coordinate_count": 1,
            "object_ids_before": {"variables": (1,)},
            "object_ids_after": {"variables": (1,)},
        }
        values.update(overrides)
        with pytest.raises(error, match=message):
            IPOPTStartEvidence(**values)

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"slot_state": "bad"}, "unsupported attempt slot state"),
            ({"role": "bad"}, "unsupported attempt role"),
            ({"source_kind": "bad"}, "unsupported attempt source kind"),
            (
                {"source_attempt_id": "other"},
                "generated-flat sources cannot name",
            ),
            (
                {"source_kind": "attempt", "source_attempt_id": None},
                "require source_attempt_id",
            ),
            (
                {"source_kind": None, "source_attempt_id": "other"},
                "requires source_kind",
            ),
            ({"inner_terminal_policy": "bad"}, "unsupported inner_terminal"),
            ({"local_interval_start": 1}, "must be zero"),
            ({"local_interval_stop": 0}, "must be nonempty"),
            ({"global_interval_start": 1}, "must equal iteration"),
            ({"global_interval_stop": 3}, "window lengths must match"),
            ({"ordinal": -1}, "ordinal must be nonnegative"),
            ({"storage_device_ids": ()}, "nonempty and unique"),
            ({"initial_soc_mwh": {}}, "mappings must match"),
            ({"terminal_deviation_mwh": {}}, "deviation must match"),
            ({"source_kind": None}, "require an initialization source"),
            (
                {"role": "target_free", "supplied_executed_action": False},
                "target-free attempts cannot report terminal deviation",
            ),
            (
                {"role": "primary_controlling", "scale": 1e-3},
                "valid only for perturbation",
            ),
        ],
    )
    def test_attempt_record_rejects_malformed_contracts(self, overrides, message):
        with pytest.raises(ValueError, match=message):
            _attempt(**overrides)

    def test_attempt_start_names_shapes_and_size_must_match(self):
        with pytest.raises(ValueError, match="namespaces must match"):
            _attempt(assigned_start={"y": np.array([1.0])})
        with pytest.raises(ValueError, match="shapes must match"):
            _attempt(assigned_start={"x": np.array([[1.0]])})
        with pytest.raises(ValueError, match="model coordinates"):
            _attempt(
                raw_start={"x": np.array([1.0, 2.0])},
                assigned_start={"x": np.array([1.0, 2.0])},
            )

    @pytest.mark.parametrize("seed", [True, -1])
    def test_perturbation_seed_is_nonnegative_integer(self, seed):
        error = TypeError if seed is True else ValueError
        with pytest.raises(error):
            _attempt(
                role="perturbed_causal",
                scale=1e-3,
                seed=seed,
            )


class TestAuditTree:
    def test_outer_boundaries_are_aligned_and_read_only(self):
        record = _outer_record()

        assert record.boundary_soc_mwh.shape == (3, 1)
        assert not record.boundary_soc_mwh.flags.writeable
        with pytest.raises(ValueError, match="consecutive from zero"):
            _outer_record(local_boundary_indices=np.array([0, 2, 3]))

    @pytest.mark.parametrize(
        ("overrides", "error", "message"),
        [
            ({"created_iteration": -1}, ValueError, "nonnegative"),
            ({"global_interval_stop": 0}, ValueError, "nonempty"),
            ({"created_iteration": 1}, ValueError, "must equal"),
            ({"build": object()}, TypeError, "build must be"),
            ({"audit": object()}, TypeError, "audit must be"),
            (
                {"storage_device_ids": ("battery-7", "battery-7")},
                ValueError,
                "must be unique",
            ),
            ({"terminal_modes": {}}, ValueError, "must match"),
            (
                {"terminal_modes": {"battery-7": "bad"}},
                ValueError,
                "unsupported outer terminal modes",
            ),
            (
                {"global_boundary_indices": np.array([0, 2, 3])},
                ValueError,
                "do not match",
            ),
            (
                {"boundary_soc_mwh": np.array([[500.0]])},
                ValueError,
                "must have shape",
            ),
            ({"boundary_soc_mwh": None}, ValueError, "requires boundary"),
            ({"result": []}, TypeError, "result must be a mapping"),
        ],
    )
    def test_outer_record_rejects_malformed_contracts(
        self, overrides, error, message
    ):
        with pytest.raises(error, match=message):
            _outer_record(**overrides)

    @pytest.mark.parametrize(
        ("mode", "residuals"),
        [
            (
                "none",
                {
                    "soc_recurrence_mwh_abs": 0.0,
                    "dc_injection_reporting_mw_abs": 0.0,
                    "dc_nodal_balance_pu_abs": 0.0,
                },
            ),
            (
                "quadratic",
                {
                    "soc_recurrence_mwh_abs": 0.0,
                    "dc_injection_reporting_mw_abs": 0.0,
                    "dc_nodal_balance_pu_abs": 0.0,
                    "soft_terminal_cost_abs": 0.0,
                },
            ),
        ],
    )
    def test_outer_terminal_residuals_follow_configured_mode(
        self, mode, residuals
    ):
        plan = _outer_record(
            global_interval_stop=1,
            local_boundary_indices=np.array([0, 1]),
            global_boundary_indices=np.array([0, 1]),
            boundary_soc_mwh=np.array([[500.0], [500.0]]),
            terminal_modes={"battery-7": mode},
            audit=_audit(residuals=residuals),
        )

        result = _result(outer_plans={"outer-000": plan})

        assert result.outer_plans["outer-000"].terminal_modes["battery-7"] == mode

    def test_complete_result_links_execution_to_controlling_attempt(self):
        result = _result()

        assert result.completed
        assert not result.realized_soc_mwh.flags.writeable
        with pytest.raises(TypeError):
            result.outer_plans["other"] = _outer_record()

    def test_result_rejects_unlinked_executed_interval(self):
        attempt = _attempt(
            attempt_id="different",
            local_interval_stop=1,
            global_interval_stop=1,
        )
        with pytest.raises(ValueError, match="controlling attempt"):
            HierarchicalResult(
                policy=HierarchicalPolicy(
                    ac_window_steps=1,
                    outer_policy="frozen",
                    initialization_policy="flat_only",
                ),
                provenance=HierarchicalProvenance(
                    HierarchicalSolveConfig(), {"cvxpy": "1.9.2"}
                ),
                horizon_steps=1,
                delta=1.0,
                storage_device_ids=("battery-7",),
                outer_plans={
                    "outer-000": _outer_record(
                        global_interval_stop=1,
                        local_boundary_indices=np.array([0, 1]),
                        global_boundary_indices=np.array([0, 1]),
                        boundary_soc_mwh=np.array([[500.0], [500.0]]),
                    )
                },
                ac_attempts=(attempt,),
                executed_intervals=(_executed_interval(),),
                realized_soc_mwh=np.array([[500.0], [500.0]]),
                executed_b_mw=np.array([[0.0]]),
                trajectory_summary={},
                completed_intervals=1,
                completion_fraction=1.0,
                completed=True,
                termination_iteration=None,
                termination_reason=None,
            )

    def test_result_enforces_outer_and_ac_residual_tolerances(self):
        bad_outer = _outer_record(
            global_interval_stop=1,
            local_boundary_indices=np.array([0, 1]),
            global_boundary_indices=np.array([0, 1]),
            boundary_soc_mwh=np.array([[500.0], [500.0]]),
            audit=_audit(
                residuals={
                    "soc_recurrence_mwh_abs": 0.0,
                    "terminal_soc_mwh_abs": 0.0,
                    "dc_injection_reporting_mw_abs": 0.0,
                    "dc_nodal_balance_pu_abs": 2e-6,
                }
            )
        )
        with pytest.raises(ValueError, match="outer plan.*exceeds"):
            _result(outer_plans={"outer-000": bad_outer})

        bad_ac = _attempt(
            local_interval_stop=1,
            global_interval_stop=1,
            audit=_audit(
                residuals={
                    "soc_recurrence_mwh_abs": 0.0,
                    "terminal_soc_mwh_abs": 0.0,
                    "ac_active_balance_pu_abs": 2e-6,
                    "ac_reactive_balance_pu_abs": 0.0,
                    "voltage_bound_pu_abs": 0.0,
                    "branch_mva_abs": 0.0,
                    "branch_normalized_squared_residual": 0.0,
                    "curtailment_nonnegativity_pu_abs": 0.0,
                    "branch_loss_nonnegativity_pu_abs": 0.0,
                }
            ),
        )
        with pytest.raises(ValueError, match="AC attempt.*exceeds"):
            _result(ac_attempts=(bad_ac,))

    def test_result_requires_complete_accepted_residual_set(self):
        incomplete = _attempt(
            local_interval_stop=1,
            global_interval_stop=1,
            audit=_audit(residuals={"soc_recurrence_mwh_abs": 0.0}),
        )
        with pytest.raises(ValueError, match="missing required residuals"):
            _result(ac_attempts=(incomplete,))

    def test_attempt_terminal_policy_must_match_result_policy(self):
        soft = _attempt(
            local_interval_stop=1,
            global_interval_stop=1,
            inner_terminal_policy="quadratic_soft",
            audit=_audit(
                residuals={
                    "soc_recurrence_mwh_abs": 0.0,
                    "soft_terminal_cost_abs": 0.0,
                    "ac_active_balance_pu_abs": 0.0,
                    "ac_reactive_balance_pu_abs": 0.0,
                    "voltage_bound_pu_abs": 0.0,
                    "branch_mva_abs": 0.0,
                    "branch_normalized_squared_residual": 0.0,
                    "curtailment_nonnegativity_pu_abs": 0.0,
                    "branch_loss_nonnegativity_pu_abs": 0.0,
                }
            ),
        )

        with pytest.raises(ValueError, match="configured inner terminal policy"):
            _result(ac_attempts=(soft,))

    def test_outer_plan_references_follow_selected_outer_policy(self):
        outer_one = _outer_record(
            outer_plan_id="outer-001",
            created_iteration=1,
            global_interval_start=1,
            global_interval_stop=2,
            local_boundary_indices=np.array([0, 1]),
            global_boundary_indices=np.array([1, 2]),
            boundary_soc_mwh=np.array([[500.0], [500.0]]),
        )
        with pytest.raises(ValueError, match="exactly one iteration-zero"):
            _result(
                policy=HierarchicalPolicy(
                    ac_window_steps=1,
                    outer_policy="frozen",
                    initialization_policy="flat_only",
                ),
                outer_plans={
                    "outer-000": _result().outer_plans["outer-000"],
                    "outer-001": outer_one,
                },
            )

        later = _attempt(
            attempt_id="ac-001-00",
            iteration=1,
            local_interval_stop=1,
            global_interval_start=1,
            global_interval_stop=2,
            outer_plan_id="outer-000",
        )
        with pytest.raises(ValueError, match="their iteration's plan"):
            _result(
                horizon_steps=2,
                outer_plans={
                    "outer-000": _outer_record(),
                    "outer-001": outer_one,
                },
                ac_attempts=(later,),
                executed_intervals=(),
                realized_soc_mwh=np.array([[500.0]]),
                executed_b_mw=np.empty((0, 1)),
                completed_intervals=0,
                completion_fraction=0.0,
                completed=False,
                termination_iteration=0,
                termination_reason="not executed",
            )

    def test_unsuccessful_outer_plan_is_terminal_and_has_no_ac_attempts(self):
        failed_plan = _outer_record(
            global_interval_stop=1,
            local_boundary_indices=np.array([0, 1]),
            global_boundary_indices=np.array([0, 1]),
            boundary_soc_mwh=None,
            audit=_audit(
                accepted=False,
                status="infeasible",
                outcome="solver_certified_infeasible",
            ),
        )
        terminal = _result(
            outer_plans={"outer-000": failed_plan},
            ac_attempts=(),
            executed_intervals=(),
            realized_soc_mwh=np.array([[500.0]]),
            executed_b_mw=np.empty((0, 1)),
            trajectory_summary={},
            completed_intervals=0,
            completion_fraction=0.0,
            completed=False,
            termination_iteration=0,
            termination_reason="outer_solver_certified_infeasible",
        )
        assert not terminal.outer_plans["outer-000"].audit.accepted_primal

        with pytest.raises(ValueError, match="accepted referenced outer plan"):
            _result(outer_plans={"outer-000": failed_plan})

    def test_accepted_terminal_outer_plan_requires_attempt_registry(self):
        with pytest.raises(ValueError, match="requires a complete AC attempt"):
            _result(
                ac_attempts=(),
                executed_intervals=(),
                realized_soc_mwh=np.array([[500.0]]),
                executed_b_mw=np.empty((0, 1)),
                trajectory_summary={},
                completed_intervals=0,
                completion_fraction=0.0,
                completed=False,
                termination_iteration=0,
                termination_reason="AC construction failed",
            )

        construction_error = _attempt(
            slot_state="construction_error",
            local_interval_stop=1,
            global_interval_stop=1,
            source_kind=None,
            build=None,
            raw_start=None,
            assigned_start=None,
            solver_evidence=None,
            result=None,
            audit=None,
            terminal_deviation_mwh=None,
            reason="initialization construction failed",
            supplied_executed_action=False,
        )
        retained = _result(
            ac_attempts=(construction_error,),
            executed_intervals=(),
            realized_soc_mwh=np.array([[500.0]]),
            executed_b_mw=np.empty((0, 1)),
            trajectory_summary={},
            completed_intervals=0,
            completion_fraction=0.0,
            completed=False,
            termination_iteration=0,
            termination_reason="AC construction failed",
        )
        assert retained.ac_attempts[0].slot_state == "construction_error"

    def test_eligible_clean_status_cannot_be_labeled_unusable(self):
        mislabeled = _attempt(
            local_interval_stop=1,
            global_interval_stop=1,
            audit=_audit(accepted=False, status="optimal"),
            supplied_executed_action=False,
        )

        with pytest.raises(ValueError, match="accepted-primal gate"):
            _result(
                ac_attempts=(mislabeled,),
                executed_intervals=(),
                realized_soc_mwh=np.array([[500.0]]),
                executed_b_mw=np.empty((0, 1)),
                trajectory_summary={},
                completed_intervals=0,
                completion_fraction=0.0,
                completed=False,
                termination_iteration=0,
                termination_reason="mislabeled audit",
            )

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("initial_soc_mwh", {"battery-7": 499.0}, "realized state"),
            ("target_soc_mwh", {"battery-7": 499.0}, "outer-plan signpost"),
        ],
    )
    def test_attempt_state_and_target_match_cross_layer_handoff(
        self, field, value, message
    ):
        attempt = _attempt(
            local_interval_stop=1,
            global_interval_stop=1,
            **{field: value},
        )

        with pytest.raises(ValueError, match=message):
            _result(ac_attempts=(attempt,))

    def test_flat_only_executed_attempt_requires_generated_flat_source(self):
        sourced = _attempt(
            local_interval_stop=1,
            global_interval_stop=1,
            source_kind="attempt",
            source_attempt_id="some-prior-attempt",
        )

        with pytest.raises(ValueError, match="generated-flat provenance"):
            _result(ac_attempts=(sourced,))

    def test_attempt_sequence_stops_after_first_accepted_controller(self):
        target_free = _attempt(
            attempt_id="target-free",
            role="target_free",
            ordinal=1,
            local_interval_stop=1,
            global_interval_stop=1,
            terminal_deviation_mwh=None,
            supplied_executed_action=False,
        )
        second_controller = _attempt(
            attempt_id="second-controller",
            role="copied_target_free",
            transformation="copy_target_free",
            ordinal=2,
            local_interval_stop=1,
            global_interval_stop=1,
            source_kind="attempt",
            source_attempt_id="target-free",
        )
        attempts = _recovery_registry(
            {1: target_free, 2: second_controller}
        )

        with pytest.raises(ValueError, match="exactly one action-supplying"):
            _result(
                policy=HierarchicalPolicy(ac_window_steps=1),
                ac_attempts=attempts,
            )

        late_target_free = replace(
            target_free,
            attempt_id="late-target-free",
        )
        with pytest.raises(ValueError, match="after the first accepted"):
            _result(
                policy=HierarchicalPolicy(ac_window_steps=1),
                ac_attempts=_recovery_registry({1: late_target_free}),
            )

    def test_result_rejects_wrong_iteration_controlling_attempt(self):
        attempt = _attempt(
            iteration=1,
            global_interval_start=1,
            global_interval_stop=2,
            local_interval_stop=1,
        )
        with pytest.raises(ValueError, match="iterations must match"):
            _result(
                policy=HierarchicalPolicy(
                    ac_window_steps=1,
                    outer_policy="frozen",
                    initialization_policy="flat_only",
                ),
                horizon_steps=2,
                outer_plans={"outer-000": _outer_record()},
                ac_attempts=(attempt,),
                completed=False,
                completion_fraction=0.5,
                termination_iteration=1,
                termination_reason="stopped",
            )

    def test_result_rejects_unknown_or_noncausal_sources(self):
        dangling = _attempt(
            local_interval_stop=1,
            global_interval_stop=1,
            source_kind="attempt",
            source_attempt_id="missing",
        )
        with pytest.raises(ValueError, match="unknown source"):
            _result(
                policy=HierarchicalPolicy(ac_window_steps=1),
                ac_attempts=_recovery_registry({0: dangling}),
            )

        source = _attempt(
            attempt_id="later-source",
            role="target_free",
            ordinal=1,
            local_interval_stop=1,
            global_interval_stop=1,
            terminal_deviation_mwh=None,
            supplied_executed_action=False,
        )
        dependent = _attempt(
            local_interval_stop=1,
            global_interval_stop=1,
            source_kind="attempt",
            source_attempt_id="later-source",
        )
        with pytest.raises(ValueError, match="causally prior"):
            _result(
                policy=HierarchicalPolicy(ac_window_steps=1),
                ac_attempts=_recovery_registry({0: dependent, 1: source}),
            )

    def test_result_accepts_a_causal_target_free_copy_chain(self):
        source = _attempt(
            attempt_id="target-free",
            role="target_free",
            ordinal=1,
            local_interval_stop=1,
            global_interval_stop=1,
            terminal_deviation_mwh=None,
            supplied_executed_action=False,
        )
        controlling = _attempt(
            attempt_id="copied-hard",
            role="copied_target_free",
            transformation="copy_target_free",
            ordinal=2,
            local_interval_stop=1,
            global_interval_stop=1,
            source_kind="attempt",
            source_attempt_id="target-free",
        )

        result = _result(
            policy=HierarchicalPolicy(ac_window_steps=1),
            ac_attempts=_recovery_registry(
                {
                    0: _attempt(
                        local_interval_stop=1,
                        global_interval_stop=1,
                        audit=_audit(accepted=False, status="user_limit"),
                        supplied_executed_action=False,
                    ),
                    1: source,
                    2: controlling,
                }
            ),
            executed_intervals=(
                _executed_interval(controlling_attempt_id="copied-hard"),
            ),
        )

        assert result.executed_intervals[0].controlling_attempt_id == "copied-hard"

    def test_later_window_recovery_reuses_prior_controlling_source(self):
        first_window = _recovery_registry()
        prior_id = first_window[0].attempt_id
        failed_primary = _attempt(
            attempt_id="ac-001-00",
            transformation="shifted_preceding",
            iteration=1,
            local_interval_stop=1,
            global_interval_start=1,
            global_interval_stop=2,
            outer_plan_id="outer-001",
            source_kind="attempt",
            source_attempt_id=prior_id,
            audit=_audit(accepted=False, status="user_limit"),
            supplied_executed_action=False,
        )
        target_free = _attempt(
            attempt_id="ac-001-01",
            role="target_free",
            transformation="shifted_preceding",
            ordinal=1,
            iteration=1,
            local_interval_stop=1,
            global_interval_start=1,
            global_interval_stop=2,
            outer_plan_id="outer-001",
            source_kind="attempt",
            source_attempt_id=prior_id,
            terminal_deviation_mwh=None,
            supplied_executed_action=False,
        )
        failed_copy = _attempt(
            attempt_id="ac-001-02",
            role="copied_target_free",
            transformation="copy_target_free",
            ordinal=2,
            iteration=1,
            local_interval_stop=1,
            global_interval_start=1,
            global_interval_stop=2,
            outer_plan_id="outer-001",
            source_kind="attempt",
            source_attempt_id=target_free.attempt_id,
            audit=_audit(accepted=False, status="user_limit"),
            supplied_executed_action=False,
        )
        causal_perturbation = _attempt(
            attempt_id="ac-001-06",
            role="perturbed_causal",
            transformation="perturb_causal",
            ordinal=6,
            iteration=1,
            local_interval_stop=1,
            global_interval_start=1,
            global_interval_stop=2,
            outer_plan_id="outer-001",
            source_kind="attempt",
            source_attempt_id=prior_id,
            scale=1e-4,
            seed=17_000_121,
        )
        replacements = {
            0: failed_primary,
            1: target_free,
            2: failed_copy,
            6: causal_perturbation,
        }
        for offset, scale in enumerate((1e-4, 1e-3, 1e-2), start=3):
            replacements[offset] = _attempt(
                attempt_id=f"ac-001-{offset:02d}",
                role="perturbed_target_free",
                transformation="perturb_target_free",
                ordinal=offset,
                iteration=1,
                local_interval_stop=1,
                global_interval_start=1,
                global_interval_stop=2,
                outer_plan_id="outer-001",
                source_kind="attempt",
                source_attempt_id=target_free.attempt_id,
                audit=_audit(accepted=False, status="user_limit"),
                supplied_executed_action=False,
                scale=scale,
                seed=17_000_108 + offset,
            )
        second_window = _recovery_registry(replacements, iteration=1)
        outer_one = _outer_record(
            outer_plan_id="outer-001",
            created_iteration=1,
            global_interval_start=1,
            global_interval_stop=2,
            local_boundary_indices=np.array([0, 1]),
            global_boundary_indices=np.array([1, 2]),
            boundary_soc_mwh=np.array([[500.0], [500.0]]),
        )

        result = _result(
            policy=HierarchicalPolicy(ac_window_steps=1),
            horizon_steps=2,
            outer_plans={
                "outer-000": _outer_record(),
                "outer-001": outer_one,
            },
            ac_attempts=first_window + second_window,
            executed_intervals=(
                _executed_interval(),
                _executed_interval(
                    iteration=1,
                    controlling_attempt_id="ac-001-06",
                ),
            ),
            realized_soc_mwh=np.array([[500.0], [500.0], [500.0]]),
            executed_b_mw=np.array([[0.0], [0.0]]),
            completed_intervals=2,
            completion_fraction=1.0,
        )

        assert result.ac_attempts[10].source_attempt_id == prior_id
        assert result.ac_attempts[15].source_attempt_id == prior_id

    def test_shifted_causal_source_must_be_immediately_preceding(self):
        first_window = _recovery_registry()
        too_old_source = first_window[0].attempt_id
        third_primary = _attempt(
            attempt_id="ac-002-00",
            transformation="shifted_preceding",
            iteration=2,
            local_interval_stop=1,
            global_interval_start=2,
            global_interval_stop=3,
            outer_plan_id="outer-002",
            source_kind="attempt",
            source_attempt_id=too_old_source,
            audit=_audit(accepted=False, status="user_limit"),
            supplied_executed_action=False,
        )
        third_window = _recovery_registry({0: third_primary}, iteration=2)
        outer_one = _outer_record(
            outer_plan_id="outer-001",
            created_iteration=1,
            global_interval_start=1,
            global_interval_stop=3,
            local_boundary_indices=np.array([0, 1, 2]),
            global_boundary_indices=np.array([1, 2, 3]),
        )
        outer_two = _outer_record(
            outer_plan_id="outer-002",
            created_iteration=2,
            global_interval_start=2,
            global_interval_stop=3,
            local_boundary_indices=np.array([0, 1]),
            global_boundary_indices=np.array([2, 3]),
            boundary_soc_mwh=np.array([[500.0], [500.0]]),
        )

        with pytest.raises(ValueError, match="immediately preceding"):
            _result(
                policy=HierarchicalPolicy(ac_window_steps=1),
                horizon_steps=3,
                outer_plans={
                    "outer-000": _outer_record(
                        global_interval_stop=3,
                        local_boundary_indices=np.array([0, 1, 2, 3]),
                        global_boundary_indices=np.array([0, 1, 2, 3]),
                        boundary_soc_mwh=np.array(
                            [[500.0], [500.0], [500.0], [500.0]]
                        ),
                    ),
                    "outer-001": outer_one,
                    "outer-002": outer_two,
                },
                ac_attempts=first_window + third_window,
                executed_intervals=(),
                realized_soc_mwh=np.array([[500.0]]),
                executed_b_mw=np.empty((0, 1)),
                completed_intervals=0,
                completion_fraction=0.0,
                completed=False,
                termination_iteration=0,
                termination_reason="diagnostic validation fixture",
            )

    def test_target_free_acceptance_requires_only_common_ac_residuals(self):
        target_free = _attempt(
            attempt_id="target-free",
            role="target_free",
            ordinal=1,
            local_interval_stop=1,
            global_interval_stop=1,
            terminal_deviation_mwh=None,
            supplied_executed_action=False,
            audit=_audit(
                residuals={
                    "soc_recurrence_mwh_abs": 0.0,
                    "ac_active_balance_pu_abs": 0.0,
                    "ac_reactive_balance_pu_abs": 0.0,
                    "voltage_bound_pu_abs": 0.0,
                    "branch_mva_abs": 0.0,
                    "branch_normalized_squared_residual": 0.0,
                    "curtailment_nonnegativity_pu_abs": 0.0,
                    "branch_loss_nonnegativity_pu_abs": 0.0,
                }
            ),
        )
        controlling = _attempt(
            attempt_id="copied-hard",
            role="copied_target_free",
            transformation="copy_target_free",
            ordinal=2,
            local_interval_stop=1,
            global_interval_stop=1,
            source_kind="attempt",
            source_attempt_id="target-free",
        )

        result = _result(
            policy=HierarchicalPolicy(ac_window_steps=1),
            ac_attempts=_recovery_registry(
                {
                    0: _attempt(
                        local_interval_stop=1,
                        global_interval_stop=1,
                        audit=_audit(accepted=False, status="user_limit"),
                        supplied_executed_action=False,
                    ),
                    1: target_free,
                    2: controlling,
                }
            ),
            executed_intervals=(
                _executed_interval(controlling_attempt_id="copied-hard"),
            ),
        )

        assert result.ac_attempts[1].audit.accepted_primal

    def test_first_window_causal_perturbation_can_use_generated_flat(self):
        perturbation = _attempt(
            attempt_id="flat-perturbation",
            role="perturbed_causal",
            transformation="perturb_causal",
            ordinal=6,
            local_interval_stop=1,
            global_interval_stop=1,
            source_kind="generated_flat",
            source_attempt_id=None,
            scale=1e-4,
            seed=17_000_021,
        )

        result = _result(
            policy=HierarchicalPolicy(ac_window_steps=1),
            ac_attempts=_recovery_registry(
                {
                    0: _attempt(
                        local_interval_stop=1,
                        global_interval_stop=1,
                        audit=_audit(accepted=False, status="user_limit"),
                        supplied_executed_action=False,
                    ),
                    6: perturbation,
                }
            ),
            executed_intervals=(
                _executed_interval(
                    controlling_attempt_id="flat-perturbation"
                ),
            ),
        )

        assert result.ac_attempts[6].source_attempt_id is None

    def test_unneeded_perturbation_slot_need_not_fabricate_source(self):
        skipped = _attempt(
            attempt_id="unused-perturbation",
            slot_state="not_needed_after_acceptance",
            role="perturbed_causal",
            source_kind=None,
            source_attempt_id=None,
            build=None,
            raw_start=None,
            assigned_start=None,
            solver_evidence=None,
            result=None,
            audit=None,
            terminal_deviation_mwh=None,
            reason="earlier controlling attempt accepted",
            supplied_executed_action=False,
            scale=1e-4,
            seed=17_000_001,
        )

        assert skipped.source_kind is None

    def test_result_enforces_policy_specific_slot_registry(self):
        extra = _skipped_recovery_attempt(1)
        with pytest.raises(ValueError, match="flat_only requires exactly one"):
            _result(ac_attempts=(_result().ac_attempts[0], extra))

        with pytest.raises(ValueError, match="exactly nine"):
            _result(
                policy=HierarchicalPolicy(ac_window_steps=1),
                ac_attempts=(_result().ac_attempts[0],),
            )

        malformed = list(_recovery_registry())
        malformed[8] = replace(
            _skipped_recovery_attempt(7), attempt_id="duplicate-ordinal"
        )
        with pytest.raises(ValueError, match="ordinals must be exactly"):
            _result(
                policy=HierarchicalPolicy(ac_window_steps=1),
                ac_attempts=tuple(malformed),
            )

    @pytest.mark.parametrize(
        ("ordinal", "changes", "message"),
        [
            (1, {"role": "primary_controlling"}, "roles do not match"),
            (1, {"transformation": "copy_target_free"}, "transformations"),
            (3, {"scale": 5e-4}, "scales do not match"),
            (3, {"seed": 99}, "seeds do not match"),
        ],
    )
    def test_recovery_registry_matches_declared_policy(
        self, ordinal, changes, message
    ):
        malformed = list(_recovery_registry())
        malformed[ordinal] = replace(malformed[ordinal], **changes)
        with pytest.raises(ValueError, match=message):
            _result(
                policy=HierarchicalPolicy(ac_window_steps=1),
                ac_attempts=tuple(malformed),
            )

    def test_attempt_registries_must_be_ordered(self):
        attempts = list(_recovery_registry())
        attempts[0], attempts[1] = attempts[1], attempts[0]
        with pytest.raises(ValueError, match="ordered by iteration and ordinal"):
            _result(
                policy=HierarchicalPolicy(ac_window_steps=1),
                ac_attempts=tuple(attempts),
            )

    @pytest.mark.parametrize(
        ("overrides", "error", "message"),
        [
            ({"policy": object()}, TypeError, "policy must be"),
            ({"provenance": object()}, TypeError, "provenance must be"),
            ({"storage_device_ids": ()}, ValueError, "nonempty and unique"),
            ({"outer_plans": {"outer-000": object()}}, TypeError, "OuterPlanRecord"),
            (
                {"outer_plans": {"wrong": _result().outer_plans["outer-000"]}},
                ValueError,
                "keys must match",
            ),
            ({"ac_attempts": (object(),)}, TypeError, "ACAttemptRecord"),
            (
                {"ac_attempts": (_result().ac_attempts[0],) * 2},
                ValueError,
                "IDs must be unique",
            ),
            (
                {"executed_intervals": (object(),)},
                TypeError,
                "ExecutedIntervalRecord",
            ),
        ],
    )
    def test_result_rejects_malformed_top_level_records(
        self, overrides, error, message
    ):
        with pytest.raises(error, match=message):
            _result(**overrides)

    @pytest.mark.parametrize(
        ("overrides", "error", "message"),
        [
            ({"completed_intervals": True}, TypeError, "must be an integer"),
            ({"completed_intervals": 2}, ValueError, "within the horizon"),
            ({"completed_intervals": 0}, ValueError, "executed record count"),
            ({"completion_fraction": 2.0}, ValueError, "must not exceed"),
            ({"completion_fraction": 0.5}, ValueError, "must equal"),
            ({"completed": False}, ValueError, "agree with horizon"),
            (
                {"termination_iteration": 0, "termination_reason": "bad"},
                ValueError,
                "cannot carry termination",
            ),
        ],
    )
    def test_result_coverage_and_completion_fields_are_consistent(
        self, overrides, error, message
    ):
        with pytest.raises(error, match=message):
            _result(**overrides)

    @pytest.mark.parametrize(
        ("overrides", "error", "message"),
        [
            ({"termination_reason": None}, ValueError, "require termination_reason"),
            ({"termination_iteration": None}, TypeError, "integer termination"),
            ({"termination_iteration": 1}, ValueError, "must equal"),
            (
                {"realized_soc_mwh": np.empty((0, 1))},
                ValueError,
                "realized_soc_mwh must have shape",
            ),
            (
                {"executed_b_mw": np.array([[0.0]])},
                ValueError,
                "executed_b_mw must have shape",
            ),
        ],
    )
    def test_incomplete_result_requires_consistent_termination_and_shapes(
        self, overrides, error, message
    ):
        values = {
            "ac_attempts": (
                _attempt(
                    slot_state="construction_error",
                    local_interval_stop=1,
                    global_interval_stop=1,
                    source_kind=None,
                    build=None,
                    raw_start=None,
                    assigned_start=None,
                    solver_evidence=None,
                    result=None,
                    audit=None,
                    terminal_deviation_mwh=None,
                    reason="failed",
                    supplied_executed_action=False,
                ),
            ),
            "executed_intervals": (),
            "realized_soc_mwh": np.array([[500.0]]),
            "executed_b_mw": np.empty((0, 1)),
            "completed_intervals": 0,
            "completion_fraction": 0.0,
            "completed": False,
            "termination_iteration": 0,
            "termination_reason": "failed",
        }
        values.update(overrides)
        with pytest.raises(error, match=message):
            _result(**values)

    def test_generated_flat_source_is_restricted_to_first_window(self):
        later = _attempt(
            iteration=1,
            global_interval_start=1,
            global_interval_stop=2,
            local_interval_stop=1,
            audit=_audit(accepted=False, status="user_limit"),
            supplied_executed_action=False,
        )
        with pytest.raises(ValueError, match="only at iteration zero"):
            _result(
                policy=HierarchicalPolicy(ac_window_steps=1),
                horizon_steps=2,
                outer_plans={
                    "outer-000": _outer_record(
                        created_iteration=1,
                        global_interval_start=1,
                        global_interval_stop=2,
                        local_boundary_indices=np.array([0, 1]),
                        global_boundary_indices=np.array([1, 2]),
                        boundary_soc_mwh=np.array([[500.0], [500.0]]),
                    )
                },
                ac_attempts=_recovery_registry({0: later}, iteration=1),
                executed_intervals=(),
                realized_soc_mwh=np.array([[500.0]]),
                executed_b_mw=np.empty((0, 1)),
                trajectory_summary={},
                completed_intervals=0,
                completion_fraction=0.0,
                completed=False,
                termination_iteration=0,
                termination_reason="first attempted window failed",
            )


def test_provenance_cannot_weaken_accepted_statuses():
    with pytest.raises(ValueError, match="fixed by M17"):
        HierarchicalProvenance(
            solve_config=HierarchicalSolveConfig(),
            software_versions={"cvxpy": "1.9.2"},
            accepted_solver_statuses=frozenset({"optimal", "user_limit"}),
        )
