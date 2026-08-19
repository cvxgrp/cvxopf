"""Tests for the Milestone 19 Stage 1 fixed-load device boundary."""

import cvxpy as cp
from contextlib import nullcontext
import numpy as np
import pandas as pd
import pytest

from cvxopf import Load, build_opf, build_opf_multistep, extract_results
from cvxopf._component_adapter import (
    ACNetworkState,
    DCNetworkState,
    HorizonContext,
    PreparationContext,
    StepContext,
)
from cvxopf._component_adapters import LOAD_ADAPTER, LoadInputs
from cvxopf._component_assembly import (
    ComponentRequest,
    assemble_component_horizon,
    assemble_component_step,
    integrate_component_stage_costs,
    prepare_components,
)
from cvxopf.load import (
    _PreparedLoadParameters,
    _build_metadata,
    _prepare_data,
    fixed_expressions,
)
from cvxopf.testcases import case9


def _context(
    formulation,
    *,
    single_node=False,
    horizon_steps=1,
):
    case = case9()
    external = frozenset(case["bus"][:, 0].astype(int))
    if single_node:
        nb = 1
        ext_to_int = {bus: 0 for bus in external}
    else:
        nb = len(external)
        ext_to_int = {
            bus: index for index, bus in enumerate(sorted(external))
        }
    preparation = PreparationContext(
        base_mva=100.0,
        nb=nb,
        ext_to_int=ext_to_int,
        ext_bus_ids=external,
        horizon_steps=horizon_steps,
        delta=1.0,
    )
    step = StepContext(
        formulation=formulation,
        step=0,
        base_mva=100.0,
        ext_to_int=ext_to_int,
        network_state=(
            ACNetworkState(cp.Variable(nb), (), False)
            if formulation == "ac"
            else DCNetworkState()
        ),
    )
    return preparation, step


class TestLoadValidation:
    @pytest.mark.parametrize(
        ("changes", "error", "message"),
        [
            ({"bus": True}, TypeError, "bus must be an integer"),
            ({"p_load_mw": True}, TypeError, "must be a real scalar"),
            ({"p_load_mw": np.nan}, ValueError, "must be finite"),
            ({"q_load_mvar": np.inf}, ValueError, "must be finite"),
            ({"device_id": ""}, ValueError, "nonempty string"),
            (
                {"shedding_cost_per_mwh": 0.0},
                ValueError,
                "shedding_cost_per_mwh must be > 0",
            ),
            (
                {"max_shed_fraction": 0.0},
                ValueError,
                "0 < value <= 1",
            ),
            (
                {"max_shed_fraction": 1.1},
                ValueError,
                "0 < value <= 1",
            ),
        ],
    )
    def test_constructor_rejects_invalid_device_fields(
        self, changes, error, message
    ):
        values = {"bus": 5, "p_load_mw": 10.0, "device_id": "load-5"}
        values.update(changes)
        with pytest.raises(error, match=message):
            Load(**values)

    def test_signed_active_and_valid_shedding_policy_construct(self):
        unit = Load(
            bus=np.int64(5),
            p_load_mw=-10.0,
            q_load_mvar=0.0,
            device_id="net-injection-5",
            shedding_cost_per_mwh=5000.0,
            max_shed_fraction=0.5,
        )

        assert unit.p_load_mw == -10.0

    def test_preparation_rejects_unknown_bus_and_duplicate_identity(self):
        with pytest.raises(ValueError, match="not in case bus table"):
            _prepare_data([Load(99, 1.0, "unknown")], 1, {}, {1})

        duplicates = [Load(1, 1.0, "same"), Load(1, 2.0, "same")]
        with pytest.raises(ValueError, match="duplicate device_id"):
            _prepare_data(duplicates, 1, {1: 0}, {1})


class TestLoadPreparation:
    def test_prepared_data_preserves_identity_reactive_semantics_and_order(self):
        units = [
            Load(5, 10.0, "undefined-q"),
            Load(5, -3.0, "explicit-zero-q", q_load_mvar=0.0),
            Load(7, 4.0, "reactive", q_load_mvar=-2.0),
        ]
        prepared = _prepare_data(
            units,
            3,
            {5: 0, 7: 2},
            {5, 7},
        )

        assert prepared["nload"] == 3
        assert prepared["nsheddable"] == 0
        np.testing.assert_array_equal(
            prepared["load_device_ids"],
            ["undefined-q", "explicit-zero-q", "reactive"],
        )
        np.testing.assert_array_equal(prepared["load_p_mw"], [10, -3, 4])
        np.testing.assert_array_equal(prepared["load_q_mvar"], [0, 0, -2])
        np.testing.assert_array_equal(
            prepared["load_has_reactive"], [False, True, True]
        )
        np.testing.assert_array_equal(
            prepared["Cload"],
            [[1, 1, 0], [0, 0, 0], [0, 0, 1]],
        )

    def test_empty_preparation_has_complete_zero_length_schema(self):
        prepared = _prepare_data([], 2, {1: 0, 2: 1}, {1, 2})

        assert prepared["nload"] == 0
        assert prepared["nsheddable"] == 0
        assert prepared["Cload"].shape == (2, 0)
        for key in (
            "load_device_ids",
            "load_bus_external",
            "load_bus_internal",
            "load_p_mw",
            "load_q_mvar",
            "load_has_reactive",
            "load_is_sheddable",
            "sheddable_load_indices",
            "sheddable_load_device_ids",
            "load_max_shed_fraction",
            "load_shedding_cost_per_mwh",
        ):
            assert prepared[key].shape == (0,)

    def test_prepared_shedding_policy_metadata_preserves_device_alignment(self):
        prepared = _prepare_data(
            [
                Load(5, 10.0, "fixed", max_shed_fraction=0.6),
                Load(
                    7,
                    20.0,
                    "future-sheddable",
                    shedding_cost_per_mwh=5000.0,
                    max_shed_fraction=0.8,
                ),
            ],
            2,
            {5: 0, 7: 1},
            {5, 7},
        )

        assert prepared["nsheddable"] == 1
        np.testing.assert_array_equal(
            prepared["load_is_sheddable"], [False, True]
        )
        np.testing.assert_array_equal(
            prepared["sheddable_load_indices"], [1]
        )
        np.testing.assert_array_equal(
            prepared["sheddable_load_device_ids"], ["future-sheddable"]
        )
        np.testing.assert_array_equal(
            prepared["load_max_shed_fraction"], [0.6, 0.8]
        )
        np.testing.assert_array_equal(
            np.isnan(prepared["load_shedding_cost_per_mwh"]),
            [True, False],
        )
        assert prepared["load_shedding_cost_per_mwh"][1] == 5000.0


class TestExplicitLoadAPI:
    @staticmethod
    def _loads():
        return [
            Load(5, 10.0, "industrial", q_load_mvar=None),
            Load(7, 20.0, "residential", q_load_mvar=4.0),
        ]

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_single_step_explicit_loads_replace_matpower_loads(
        self, formulation
    ):
        build = build_opf(
            case9(), formulation=formulation, loads=self._loads()
        )

        np.testing.assert_array_equal(
            build.data["load_device_ids"],
            ["industrial", "residential"],
        )
        np.testing.assert_allclose(
            build.expressions["p_load"].value, [10.0, 20.0]
        )
        if formulation == "singlenode_dc":
            assert build.data["Pd_total"] == pytest.approx(0.3)
        else:
            expected = np.zeros(9)
            expected[[4, 6]] = [0.1, 0.2]
            np.testing.assert_allclose(build.data["Pd"], expected)

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_identity_aligned_frames_are_reordered_and_static_q_can_be_replaced(
        self, formulation
    ):
        loads = self._loads()
        p = pd.DataFrame(
            {"residential": [22.0, 24.0], "industrial": [11.0, 12.0]}
        )
        q = pd.DataFrame(
            {"residential": [5.0, 6.0], "industrial": [-1.0, -2.0]}
        )
        warning = pytest.warns(UserWarning, match="retained.*not used")
        context = warning if formulation != "ac" else nullcontext()
        with context:
            build = build_opf_multistep(
                case9(),
                T=2,
                formulation=formulation,
                loads=loads,
                df_load_p=p,
                df_load_q=q,
            )

        np.testing.assert_allclose(
            [item.value for item in build.expressions["p_load"]],
            [[11.0, 22.0], [12.0, 24.0]],
        )
        np.testing.assert_allclose(
            [item.value for item in build.expressions["q_load"]],
            [[-1.0, 5.0], [-2.0, 6.0]],
        )
        assert build.data["load_has_reactive"].tolist() == [True, True]

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_explicit_empty_loads_publish_complete_empty_schema(self, formulation):
        build = build_opf_multistep(
            case9(), T=1, formulation=formulation, loads=[]
        )

        assert build.data["nload"] == 0
        assert build.data["load_has_reactive"].shape == (0,)
        assert build.data["Cload"].shape == (
            1 if formulation == "singlenode_dc" else 9,
            0,
        )
        assert len(build.expressions["p_load"]) == 1
        assert build.expressions["p_load"][0].shape == (0,)
        assert build.data["Pd_series"].shape == (
            (1,) if formulation == "singlenode_dc" else (1, 9)
        )

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            (
                {"loads": [Load(5, 1.0, "x")], "df_P": pd.DataFrame([[1]])},
                "does not accept legacy",
            ),
            (
                {"df_load_p": pd.DataFrame({"x": [1.0]})},
                "require explicit loads",
            ),
            ({}, "requires df_P"),
        ],
    )
    def test_input_modes_are_mutually_exclusive_before_case_dispatch(
        self, kwargs, message
    ):
        malformed_case = {"baseMVA": 100.0}
        with pytest.raises(ValueError, match=message):
            build_opf_multistep(malformed_case, T=1, **kwargs)

    @pytest.mark.parametrize("frame_name", ["df_load_p", "df_load_q"])
    def test_explicit_frames_require_exact_finite_identity_set(self, frame_name):
        frame = pd.DataFrame({"wrong": [np.nan]})
        with pytest.raises(ValueError, match="columns must match"):
            build_opf_multistep(
                case9(),
                T=1,
                loads=self._loads(),
                **{frame_name: frame},
            )

    def test_explicit_frame_rejects_nonfinite_value_after_alignment(self):
        frame = pd.DataFrame(
            {"industrial": [np.nan], "residential": [2.0]}
        )
        with pytest.raises(ValueError, match="non-finite value"):
            build_opf_multistep(
                case9(), T=1, loads=self._loads(), df_load_p=frame
            )

    def test_explicit_frame_rejects_wrong_horizon_length(self):
        frame = pd.DataFrame(
            {"industrial": [1.0, 2.0], "residential": [3.0, 4.0]}
        )
        with pytest.raises(ValueError, match="2 rows but T=1"):
            build_opf_multistep(
                case9(), T=1, loads=self._loads(), df_load_p=frame
            )

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_multistep_t1_retains_time_axis_and_matches_single_step(
        self, formulation
    ):
        loads = self._loads()
        single = build_opf(case9(), formulation=formulation, loads=loads)
        multi = build_opf_multistep(
            case9(), T=1, formulation=formulation, loads=loads
        )
        single.solve()
        multi.solve()

        assert multi.data["T"] == 1
        assert multi.data["load_has_reactive"].tolist() == [False, True]
        assert len(multi.expressions["p_load"]) == 1
        assert extract_results(multi)["p_load"].shape == (1, 2)
        assert multi.prob.value == pytest.approx(
            single.prob.value, rel=2e-5, abs=2e-3
        )


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_fixed_load_adapter_composes_without_variables_or_constraints(formulation):
    single_node = formulation == "singlenode_dc"
    preparation, step = _context(formulation, single_node=single_node)
    units = (
        Load(5, 40.0, "load-5", q_load_mvar=8.0),
        Load(7, -10.0, "net-injection-7", q_load_mvar=-3.0),
    )
    prepared = prepare_components(
        [ComponentRequest(LOAD_ADAPTER, units)],
        formulation,
        preparation,
    )
    metadata = LOAD_ADAPTER.metadata(
        prepared.components["load"].data, formulation
    )
    assert metadata["nload"] == 2
    assert metadata["nsheddable"] == 0
    np.testing.assert_array_equal(metadata["load_device_ids"], ["load-5", "net-injection-7"])
    contributions = assemble_component_step(prepared, step)
    contribution = contributions["load"]

    assert contribution.variables == {}
    assert contribution.operating_constraints == ()
    assert contribution.network_constraints == ()
    assert contribution.cost is None
    assert contribution.injection.inv_base_mva is not None
    assert contribution.injection.inv_base_mva.value == pytest.approx(0.01)
    assert contribution.injection.p_pu is not None
    assert contribution.injection.p_pu.is_dcp()
    assert all(expression.is_dcp() for expression in contribution.expressions.values())
    assert set(contribution.expressions) == (
        {"p_load", "q_load", "p_load_served", "q_load_served"}
        if formulation == "ac"
        else {"p_load", "q_load", "p_load_served"}
    )

    expected_p = np.zeros(preparation.nb)
    if single_node:
        expected_p[0] = -0.3
    else:
        expected_p[preparation.ext_to_int[5]] = -0.4
        expected_p[preparation.ext_to_int[7]] = 0.1
    np.testing.assert_allclose(contribution.injection.p_pu.value, expected_p)
    if formulation == "ac":
        assert contribution.injection.q_pu.is_dcp()
        expected_q = np.zeros(preparation.nb)
        expected_q[preparation.ext_to_int[5]] = -0.08
        expected_q[preparation.ext_to_int[7]] = 0.03
        np.testing.assert_allclose(
            contribution.injection.q_pu.value, expected_q
        )
    else:
        assert contribution.injection.q_pu is None

    horizon = assemble_component_horizon(
        prepared,
        [contributions],
        HorizonContext(formulation, 1, 1.0),
    )["load"]
    assert horizon.constraints == ()
    assert horizon.terminal_cost is None
    assert horizon.expressions == {}


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_empty_load_adapter_functions_retain_complete_empty_contract(formulation):
    single_node = formulation == "singlenode_dc"
    preparation, step = _context(formulation, single_node=single_node)
    prepared = LOAD_ADAPTER.prepare((), None, preparation)
    metadata = LOAD_ADAPTER.metadata(prepared, formulation)
    binding = LOAD_ADAPTER.formulations[formulation]

    assert metadata["nload"] == 0
    assert metadata["nsheddable"] == 0
    assert metadata["Cload"].shape == (preparation.nb, 0)
    assert binding.variable_specs is not None
    assert binding.injections is not None
    assert binding.operating_constraints is not None
    assert binding.step_expressions is not None
    assert binding.horizon is not None
    assert binding.variable_specs((), prepared, step) == ()
    injection = binding.injections((), prepared, {}, step)
    assert injection.p_pu is not None
    assert injection.p_pu.shape == (preparation.nb,)
    assert injection.p_pu.value is None
    injection.inv_base_mva.value = 0.01
    np.testing.assert_array_equal(injection.p_pu.value, np.zeros(preparation.nb))
    assert (injection.q_pu is not None) is (formulation == "ac")
    assert binding.operating_constraints((), prepared, {}, step) == ()
    expressions = binding.step_expressions((), prepared, {}, step)
    assert all(expression.shape == (0,) for expression in expressions.values())
    horizon = binding.horizon(
        (), prepared, {}, HorizonContext(formulation, 1, 1.0)
    )
    assert horizon.constraints == ()
    assert horizon.terminal_cost is None


def test_shedding_configuration_activates_adapter_variable_and_cost():
    preparation, step = _context("ac")
    unit = Load(
        5,
        10.0,
        "future-sheddable",
        shedding_cost_per_mwh=5000.0,
        max_shed_fraction=0.8,
    )

    prepared = prepare_components(
        (ComponentRequest(LOAD_ADAPTER, (unit,)),),
        "ac",
        preparation,
    )
    contributions = assemble_component_step(prepared, step)
    contribution = contributions["load"]

    assert tuple(contribution.variables) == ("load_shed_fraction",)
    assert len(contribution.operating_constraints) == 2
    assert contribution.cost is not None
    assert contribution.cost_expression_name == "load_shedding_cost"
    assert "p_load_shed" in contribution.expressions


def test_normalized_load_inputs_are_selected_by_step():
    preparation, _ = _context("lossy_dc", horizon_steps=2)
    unit = Load(5, 10.0, "load-5", q_load_mvar=2.0)
    prepared = LOAD_ADAPTER.prepare(
        (unit,),
        LoadInputs(
            p_mw=np.array([[10.0], [-4.0]]),
            q_mvar=np.array([[2.0], [-1.0]]),
        ),
        preparation,
    )
    binding = LOAD_ADAPTER.formulations["lossy_dc"]
    assert binding.injections is not None
    assert binding.step_expressions is not None

    for step_index, expected_p in enumerate((10.0, -4.0)):
        step = StepContext(
            "lossy_dc",
            step_index,
            100.0,
            preparation.ext_to_int,
            DCNetworkState(),
        )
        injection = binding.injections((unit,), prepared, {}, step)
        injection.inv_base_mva.value = 0.01
        expected = np.zeros(preparation.nb)
        expected[preparation.ext_to_int[5]] = -expected_p / 100.0
        np.testing.assert_array_equal(injection.p_pu.value, expected)
        expressions = binding.step_expressions((unit,), prepared, {}, step)
        np.testing.assert_array_equal(
            expressions["p_load"].value, [expected_p]
        )


def test_atomic_active_load_update_crosses_zero_without_partial_assignment():
    parameters = _PreparedLoadParameters.create(
        np.array([[10.0], [0.0], [-4.0]]),
        np.zeros((3, 1)),
    )
    np.testing.assert_array_equal(
        parameters.p_eligible_mw.value, [[10.0], [0.0], [0.0]]
    )
    np.testing.assert_array_equal(
        parameters.eligibility_mask.value, [[1.0], [0.0], [0.0]]
    )

    parameters.update_active(np.array([[-2.0], [3.0], [0.0]]))
    np.testing.assert_array_equal(
        parameters.p_load_mw.value, [[-2.0], [3.0], [0.0]]
    )
    np.testing.assert_array_equal(
        parameters.p_eligible_mw.value, [[0.0], [3.0], [0.0]]
    )
    np.testing.assert_array_equal(
        parameters.eligibility_mask.value, [[0.0], [1.0], [0.0]]
    )

    tracked = (
        parameters.p_load_mw,
        parameters.p_eligible_mw,
        parameters.eligibility_mask,
    )
    before = tuple(parameter.value.copy() for parameter in tracked)
    with pytest.raises(ValueError, match="must be finite"):
        parameters.update_active(np.array([[np.nan], [1.0], [2.0]]))
    for parameter, expected in zip(tracked, before, strict=True):
        np.testing.assert_array_equal(parameter.value, expected)


def test_atomic_updates_preserve_parameter_objects_across_sign_transitions():
    parameters = _PreparedLoadParameters.create(
        np.array([[4.0]]), np.zeros((1, 1))
    )
    identities = tuple(
        id(parameter)
        for parameter in (
            parameters.p_load_mw,
            parameters.p_eligible_mw,
            parameters.eligibility_mask,
        )
    )

    for signed, eligible, mask in (
        (0.0, 0.0, 0.0),
        (-2.0, 0.0, 0.0),
        (5.0, 5.0, 1.0),
    ):
        parameters.update_active(np.array([[signed]]))
        assert parameters.p_load_mw.value[0, 0] == signed
        assert parameters.p_eligible_mw.value[0, 0] == eligible
        assert parameters.eligibility_mask.value[0, 0] == mask
        assert tuple(
            id(parameter)
            for parameter in (
                parameters.p_load_mw,
                parameters.p_eligible_mw,
                parameters.eligibility_mask,
            )
        ) == identities


def test_public_shedding_cost_uses_scientific_expression_name():
    unit = Load(
        5,
        900.0,
        "interruptible",
        shedding_cost_per_mwh=5000.0,
    )
    build = build_opf(
        case9(), formulation="singlenode_dc", loads=[unit]
    )
    build.solve()

    assert "load_shedding_cost" in build.expressions
    assert "load_cost" not in build.expressions
    assert build.expressions["load_shedding_cost"].value > 0


def test_zero_and_negative_active_load_force_exactly_zero_shedding():
    preparation, _ = _context("lossy_dc", horizon_steps=3)
    unit = Load(5, 10.0, "crossing", shedding_cost_per_mwh=1000.0)
    prepared = prepare_components(
        (
            ComponentRequest(
                LOAD_ADAPTER,
                (unit,),
                LoadInputs(
                    np.array([[10.0], [0.0], [-5.0]]),
                    np.zeros((3, 1)),
                ),
            ),
        ),
        "lossy_dc",
        preparation,
    )

    for step_index, expected in enumerate((1.0, 0.0, 0.0)):
        step = StepContext(
            "lossy_dc",
            step_index,
            100.0,
            preparation.ext_to_int,
            DCNetworkState(),
        )
        contribution = assemble_component_step(prepared, step)["load"]
        fraction = contribution.variables["load_shed_fraction"]
        problem = cp.Problem(
            cp.Maximize(fraction[0]),
            list(contribution.operating_constraints),
        )
        problem.solve()
        assert fraction.value[0] == pytest.approx(expected, abs=1e-8)


def test_reactive_shedding_preserves_leading_and_lagging_power_factor():
    preparation, _ = _context("ac", horizon_steps=2)
    unit = Load(
        5,
        40.0,
        "reactive",
        q_load_mvar=20.0,
        shedding_cost_per_mwh=1000.0,
    )
    prepared = prepare_components(
        (
            ComponentRequest(
                LOAD_ADAPTER,
                (unit,),
                LoadInputs(
                    np.array([[40.0], [40.0]]),
                    np.array([[20.0], [-12.0]]),
                ),
            ),
        ),
        "ac",
        preparation,
    )

    for step_index, (q_shed, q_served) in enumerate(
        ((5.0, 15.0), (-3.0, -9.0))
    ):
        step = StepContext(
            "ac",
            step_index,
            100.0,
            preparation.ext_to_int,
            ACNetworkState(cp.Variable(preparation.nb), (), False),
        )
        contribution = assemble_component_step(prepared, step)["load"]
        contribution.variables["load_shed_fraction"].value = [0.25]
        assert contribution.expressions["q_load_shed"].value[0] == pytest.approx(
            q_shed
        )
        assert contribution.expressions["q_load_served"].value[0] == pytest.approx(
            q_served
        )


def test_reactive_only_load_remains_fixed():
    unit = Load(
        5,
        0.0,
        "reactive-only",
        q_load_mvar=20.0,
        shedding_cost_per_mwh=1000.0,
    )
    preparation, step = _context("ac")
    prepared = prepare_components(
        (ComponentRequest(LOAD_ADAPTER, (unit,)),), "ac", preparation
    )
    contribution = assemble_component_step(prepared, step)["load"]
    fraction = contribution.variables["load_shed_fraction"]
    cp.Problem(
        cp.Maximize(fraction[0]),
        list(contribution.operating_constraints),
    ).solve()

    assert fraction.value[0] == pytest.approx(0.0, abs=1e-8)
    assert contribution.expressions["q_load_shed"].value[0] == pytest.approx(
        0.0, abs=1e-8
    )
    assert contribution.expressions["q_load_served"].value[0] == pytest.approx(
        20.0
    )


def test_mixed_fixed_and_sheddable_alignment_and_fraction_cap():
    loads = [
        Load(5, 100.0, "fixed"),
        Load(
            7,
            900.0,
            "limited",
            shedding_cost_per_mwh=5000.0,
            max_shed_fraction=0.5,
        ),
    ]
    build = build_opf(
        case9(), formulation="singlenode_dc", loads=loads
    )
    build.solve()

    fraction = build.variables["load_shed_fraction"].value[0]
    assert 0 <= fraction <= 0.5 + 1e-8
    assert build.expressions["p_load_shed"].shape == (1,)
    assert build.expressions["p_load_served"].shape == (2,)
    assert build.expressions["p_load_served"].value[0] == pytest.approx(100.0)


def test_maximum_shed_fraction_below_one_is_binding():
    preparation, step = _context("lossy_dc")
    unit = Load(
        5,
        20.0,
        "partially-interruptible",
        shedding_cost_per_mwh=1000.0,
        max_shed_fraction=0.4,
    )
    prepared = prepare_components(
        (ComponentRequest(LOAD_ADAPTER, (unit,)),),
        "lossy_dc",
        preparation,
    )
    contribution = assemble_component_step(prepared, step)["load"]
    fraction = contribution.variables["load_shed_fraction"]
    cp.Problem(
        cp.Maximize(fraction[0]),
        list(contribution.operating_constraints),
    ).solve()

    assert fraction.value[0] == pytest.approx(0.4, abs=1e-8)


def test_delta_integrates_shedding_cost_and_ens_exactly_once():
    unit = Load(
        5,
        900.0,
        "interruptible",
        shedding_cost_per_mwh=5000.0,
    )
    build = build_opf_multistep(
        case9(),
        T=2,
        formulation="singlenode_dc",
        loads=[unit],
        delta=0.25,
    )
    build.solve()

    shed = np.array(
        [item.value[0] for item in build.expressions["p_load_shed"]]
    )
    expected_ens = 0.25 * np.sum(shed)
    assert build.expressions["energy_not_served_by_load"].shape == (1,)
    assert build.expressions["energy_not_served_by_load"].value[0] == (
        pytest.approx(expected_ens)
    )
    assert build.expressions["energy_not_served"].value == pytest.approx(
        expected_ens
    )
    assert build.expressions["load_shedding_cost"].value == pytest.approx(
        5000.0 * expected_ens
    )


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc"])
def test_shedding_reconstruction_agrees_across_all_adapter_hooks(
    formulation,
):
    """Lock duplicated reporting, injection, cost, and horizon formulas."""
    loads = (
        Load(
            5,
            100.0,
            "shed-lagging",
            q_load_mvar=20.0,
            shedding_cost_per_mwh=1000.0,
            max_shed_fraction=0.5,
        ),
        Load(6, 30.0, "fixed", q_load_mvar=5.0),
        Load(
            7,
            80.0,
            "shed-leading",
            q_load_mvar=-16.0,
            shedding_cost_per_mwh=3000.0,
            max_shed_fraction=0.25,
        ),
    )
    base_preparation, _ = _context(formulation)
    preparation = PreparationContext(
        base_mva=base_preparation.base_mva,
        nb=base_preparation.nb,
        ext_to_int=base_preparation.ext_to_int,
        ext_bus_ids=base_preparation.ext_bus_ids,
        horizon_steps=1,
        delta=0.5,
    )
    step = StepContext(
        formulation,
        0,
        preparation.base_mva,
        preparation.ext_to_int,
        (
            ACNetworkState(cp.Variable(preparation.nb), (), False)
            if formulation == "ac"
            else DCNetworkState()
        ),
    )
    prepared = prepare_components(
        (ComponentRequest(LOAD_ADAPTER, loads),),
        formulation,
        preparation,
    )
    contributions = assemble_component_step(prepared, step)
    contribution = contributions["load"]
    contribution.variables["load_shed_fraction"].value = [0.4, 0.25]

    expected_p_shed = np.array([40.0, 20.0])
    expected_p_served = np.array([60.0, 30.0, 60.0])
    np.testing.assert_allclose(
        contribution.expressions["p_load_shed"].value, expected_p_shed
    )
    np.testing.assert_allclose(
        contribution.expressions["p_load_served"].value, expected_p_served
    )

    expected_p_injection = np.zeros(preparation.nb)
    for bus, served in zip((5, 6, 7), expected_p_served, strict=True):
        expected_p_injection[preparation.ext_to_int[bus]] = -served / 100.0
    np.testing.assert_allclose(
        contribution.injection.p_pu.value, expected_p_injection
    )

    if formulation == "ac":
        expected_q_shed = np.array([8.0, -4.0])
        expected_q_served = np.array([12.0, 5.0, -12.0])
        np.testing.assert_allclose(
            contribution.expressions["q_load_shed"].value,
            expected_q_shed,
        )
        np.testing.assert_allclose(
            contribution.expressions["q_load_served"].value,
            expected_q_served,
        )
        expected_q_injection = np.zeros(preparation.nb)
        for bus, served in zip((5, 6, 7), expected_q_served, strict=True):
            expected_q_injection[preparation.ext_to_int[bus]] = -served / 100.0
        np.testing.assert_allclose(
            contribution.injection.q_pu.value, expected_q_injection
        )
    else:
        assert contribution.injection.q_pu is None
        assert "q_load_shed" not in contribution.expressions
        assert "q_load_served" not in contribution.expressions

    expected_cost_rate = 1000.0 * 40.0 + 3000.0 * 20.0
    assert contribution.cost.value == pytest.approx(expected_cost_rate)
    integrated_costs = integrate_component_stage_costs(
        [contributions], preparation.delta
    )
    assert set(integrated_costs) == {"load_shedding_cost"}
    assert integrated_costs["load_shedding_cost"].value == pytest.approx(
        0.5 * expected_cost_rate
    )

    horizon = assemble_component_horizon(
        prepared,
        [contributions],
        HorizonContext(formulation, 1, 0.5),
    )["load"]
    expected_ens_by_load = 0.5 * expected_p_shed
    np.testing.assert_allclose(
        horizon.expressions["energy_not_served_by_load"].value,
        expected_ens_by_load,
    )
    assert horizon.expressions["energy_not_served"].value == pytest.approx(
        np.sum(expected_ens_by_load)
    )


@pytest.mark.parametrize(
    "inputs",
    [
        LoadInputs(np.ones((1, 1)), np.ones((2, 1))),
        LoadInputs(np.ones((2, 2)), np.ones((2, 2))),
        LoadInputs(np.array([[1.0], [np.nan]]), np.ones((2, 1))),
    ],
)
def test_load_adapter_rejects_invalid_normalized_channels(inputs):
    preparation, _ = _context("ac", horizon_steps=2)
    unit = Load(5, 10.0, "load-5")

    with pytest.raises(ValueError, match="load input channels"):
        LOAD_ADAPTER.prepare((unit,), inputs, preparation)


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_fixed_load_values_survive_unsuccessful_extraction(formulation):
    case = case9()
    build = build_opf(case, formulation=formulation)
    external_ids = set(case["bus"][:, 0].astype(int))
    ext_to_int = (
        {bus: 0 for bus in external_ids}
        if formulation == "singlenode_dc"
        else dict(build.data["ext_to_int"])
    )
    prepared = _prepare_data(
        [
            Load(5, 40.0, "undefined-q"),
            Load(7, -10.0, "explicit-zero-q", q_load_mvar=0.0),
        ],
        int(build.data["nb"]),
        ext_to_int,
        external_ids,
    )
    build.data.update(_build_metadata(prepared))
    build.expressions.update(
        fixed_expressions(
            prepared["load_p_mw"],
            prepared["load_q_mvar"],
            reactive_service=formulation == "ac",
        )
    )

    results = extract_results(build)

    assert results["status"] is None
    np.testing.assert_array_equal(results["p_load"], [40.0, -10.0])
    np.testing.assert_array_equal(results["q_load"], [0.0, 0.0])
    np.testing.assert_array_equal(results["p_load_served"], [40.0, -10.0])
    if formulation == "ac":
        np.testing.assert_array_equal(results["q_load_served"], [0.0, 0.0])
    else:
        assert "q_load_served" not in results
    np.testing.assert_array_equal(
        build.data["load_has_reactive"], [False, True]
    )
