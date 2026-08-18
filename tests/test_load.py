"""Tests for the Milestone 19 Stage 1 fixed-load device boundary."""

import cvxpy as cp
import numpy as np
import pytest

from cvxopf import Load, build_opf, extract_results
from cvxopf._component_adapter import (
    ACNetworkState,
    DCNetworkState,
    HorizonContext,
    PreparationContext,
    StepContext,
)
from cvxopf._component_adapters import LOAD_ADAPTER
from cvxopf._component_assembly import (
    ComponentRequest,
    assemble_component_horizon,
    assemble_component_step,
    prepare_components,
)
from cvxopf.load import _build_metadata, _prepare_data, fixed_expressions
from cvxopf.testcases import case9


def _context(
    formulation,
    *,
    single_node=False,
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
        horizon_steps=1,
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


def test_shedding_configuration_is_valid_but_temporarily_rejected_by_adapter():
    preparation, _ = _context("ac")
    unit = Load(
        5,
        10.0,
        "future-sheddable",
        shedding_cost_per_mwh=5000.0,
        max_shed_fraction=0.8,
    )

    with pytest.raises(NotImplementedError, match="Milestone 19 Stage 4"):
        LOAD_ADAPTER.prepare((unit,), None, preparation)


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
