"""Cross-device conformance tests for the Milestone 16 component contract."""

import warnings

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import (
    DispatchableGenerator,
    HVDCLink,
    NondispatchableUnit,
    StorageUnitIdeal,
)
from cvxopf import generator, hvdc, nondispatchable, storage
from cvxopf import ac_problem, dc_problem, singlenode_dc_problem
from cvxopf.problem import build_opf, build_opf_multistep
from cvxopf.results import extract_results
from cvxopf.testcases import case9


def test_all_components_expose_network_and_temporal_interface():
    for module in (generator, storage, nondispatchable, hvdc):
        assert callable(module.ac_injections)
        assert callable(module.dc_injections)
        assert callable(module.ac_operating_constraints)
        assert callable(module.dc_operating_constraints)
        assert callable(module.coupling_constraints)
    assert callable(generator.ac_network_constraints)
    assert callable(generator.dc_network_constraints)


def test_all_component_injection_methods_return_fixed_three_tuple():
    ext_to_int = {1: 0, 2: 1}

    gen = DispatchableGenerator(bus=1, p_max_mw=100.0)
    pg = cp.Variable(1)
    qg = cp.Variable(1)
    generator_ac = generator.ac_injections([gen], pg, qg, ext_to_int)
    generator_dc = generator.dc_injections([gen], pg, ext_to_int)

    store = StorageUnitIdeal(
        bus=1,
        apparent_power_rating=10.0,
        capacity=20.0,
        initial_soc=5.0,
    )
    b = cp.Variable(1)
    bq = cp.Variable(1)
    storage_ac = storage.ac_injections([store], b, bq, ext_to_int)
    storage_dc = storage.dc_injections([store], b, ext_to_int)

    nd = NondispatchableUnit(
        bus=1,
        p_available=10.0,
        apparent_power_rating=12.0,
    )
    p_nd = cp.Variable(1)
    q_nd = cp.Variable(1)
    nd_ac = nondispatchable.ac_injections(
        [nd], p_nd, q_nd, ext_to_int
    )
    nd_dc = nondispatchable.dc_injections([nd], p_nd, ext_to_int)

    link = HVDCLink(
        from_bus=1,
        to_bus=2,
        p_min_mw=-10.0,
        p_max_mw=10.0,
    )
    p_in = cp.Variable(1)
    p_out = cp.Variable(1)
    hvdc_ac = hvdc.ac_injections([link], p_in, p_out, ext_to_int)
    hvdc_dc = hvdc.dc_injections([link], p_in, p_out, ext_to_int)

    for result in (
        generator_ac,
        generator_dc,
        storage_ac,
        storage_dc,
        nd_ac,
        nd_dc,
        hvdc_ac,
        hvdc_dc,
    ):
        assert isinstance(result, tuple)
        assert len(result) == 3


def test_device_preparation_helpers_return_existing_flat_contract():
    ext_to_int = {1: 0, 2: 1}
    ext_bus_ids = set(ext_to_int)

    gen = DispatchableGenerator(bus=1, p_max_mw=100.0)
    gen_data = generator._prepare_data(
        [gen], 100.0, 2, ext_to_int, ext_bus_ids
    )
    assert {
        "ng", "generators", "Cg", "gen_bus", "status", "vg",
        "Pgmin", "Pgmax", "Qgmin", "Qgmax", "gencost",
    } <= set(gen_data)
    np.testing.assert_array_equal(gen_data["Cg"], [[1.0], [0.0]])

    store = StorageUnitIdeal(2, 10.0, 20.0, 5.0)
    storage_data = storage._prepare_data(
        [store], 2, ext_to_int, ext_bus_ids
    )
    assert {"ns", "Cs", "storage_bus"} <= set(storage_data)
    np.testing.assert_array_equal(storage_data["Cs"], [[0.0], [1.0]])

    nd = NondispatchableUnit(2, 10.0, 12.0)
    nd_data = nondispatchable._prepare_data(
        [nd], 2, ext_to_int, ext_bus_ids
    )
    assert {"nnd", "Cnd", "nd_bus"} <= set(nd_data)
    np.testing.assert_array_equal(nd_data["Cnd"], [[0.0], [1.0]])

    link = HVDCLink(1, 2, -10.0, 10.0)
    hvdc_data = hvdc._prepare_data(
        [link], 2, ext_to_int, ext_bus_ids
    )
    assert {"n_hvdc", "Ch_from", "Ch_to"} == set(hvdc_data)


def test_dc_injections_use_supplied_prepared_incidence():
    ext_to_int = {1: 0, 2: 1}
    routed_to_bus_two = np.array([[0.0], [1.0]])

    gen = DispatchableGenerator(bus=1, p_max_mw=100.0)
    pg = cp.Variable(1)
    gen_inj, _, _ = generator.dc_injections(
        [gen], pg, ext_to_int, incidence=routed_to_bus_two
    )
    pg.value = np.array([0.3])
    np.testing.assert_allclose(gen_inj.value, [0.0, 0.3])

    store = StorageUnitIdeal(1, 10.0, 20.0, 5.0)
    b = cp.Variable(1)
    storage_inj, _, storage_scale = storage.dc_injections(
        [store], b, ext_to_int, incidence=routed_to_bus_two
    )
    storage_scale.value = 0.01
    b.value = np.array([10.0])
    np.testing.assert_allclose(storage_inj.value, [0.0, 0.1])

    nd = NondispatchableUnit(1, 10.0, 12.0)
    p_nd = cp.Variable(1)
    nd_inj, _, nd_scale = nondispatchable.dc_injections(
        [nd], p_nd, ext_to_int, incidence=routed_to_bus_two
    )
    nd_scale.value = 0.01
    p_nd.value = np.array([10.0])
    np.testing.assert_allclose(nd_inj.value, [0.0, 0.1])

    link = HVDCLink(1, 2, -10.0, 10.0)
    p_in = cp.Variable(1)
    p_out = cp.Variable(1)
    hvdc_inj, _, hvdc_scale = hvdc.dc_injections(
        [link],
        p_in,
        p_out,
        ext_to_int,
        incidence=(routed_to_bus_two, np.array([[1.0], [0.0]])),
    )
    hvdc_scale.value = 0.01
    p_in.value = np.array([10.0])
    p_out.value = np.array([-10.0])
    np.testing.assert_allclose(hvdc_inj.value, [-0.1, 0.1])


def test_memoryless_components_have_empty_coupling_slot():
    assert generator.coupling_constraints([], []) == []
    assert nondispatchable.coupling_constraints([], []) == []
    assert hvdc.coupling_constraints([], [], []) == []


@pytest.mark.parametrize(
    ("formulation", "builder_module"),
    [
        ("ac", ac_problem),
        ("lossy_dc", dc_problem),
        ("singlenode_dc", singlenode_dc_problem),
    ],
)
def test_multistep_builders_compose_generator_coupling_hook(
    formulation, builder_module, monkeypatch
):
    calls = []

    def coupling_spy(generators, Pg_list, Qg_list=None, delta=1.0):
        calls.append((generators, Pg_list, Qg_list, delta))
        return []

    monkeypatch.setattr(
        builder_module, "generator_coupling_constraints", coupling_spy
    )
    case = case9()
    T = 2
    df_P = pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1)))
    df_Q = pd.DataFrame(np.tile(case["bus"][:, 3], (T, 1)))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build_opf_multistep(
            case, df_P, df_Q, T=T, formulation=formulation, delta=0.5
        )

    assert len(calls) == 1
    assert len(calls[0][1]) == T
    assert calls[0][3] == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("formulation", "builder_module"),
    [
        ("ac", ac_problem),
        ("lossy_dc", dc_problem),
        ("singlenode_dc", singlenode_dc_problem),
    ],
)
def test_multistep_builders_compose_nd_coupling_hook(
    formulation, builder_module, monkeypatch
):
    calls = []

    def coupling_spy(units, p_nd_list, q_nd_list=None, delta=1.0):
        calls.append((units, p_nd_list, q_nd_list, delta))
        return []

    monkeypatch.setattr(builder_module, "nd_coupling_constraints", coupling_spy)
    case = case9()
    T = 2
    df_P = pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1)))
    df_Q = pd.DataFrame(np.tile(case["bus"][:, 3], (T, 1)))
    units = [NondispatchableUnit(5, 20.0, 25.0, device_id="nd")]
    df_nd = pd.DataFrame({"nd": [20.0, 15.0]})

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build_opf_multistep(
            case,
            df_P,
            df_Q,
            T=T,
            formulation=formulation,
            nondispatchable=units,
            df_nd=df_nd,
            delta=0.5,
        )

    assert len(calls) == 1
    assert len(calls[0][1]) == T
    assert calls[0][3] == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("formulation", "builder_module"),
    [("ac", ac_problem), ("lossy_dc", dc_problem)],
)
def test_multistep_builders_compose_hvdc_coupling_hook(
    formulation, builder_module, monkeypatch
):
    calls = []

    def coupling_spy(links, p_in_list, p_out_list, delta=1.0):
        calls.append((links, p_in_list, p_out_list, delta))
        return []

    monkeypatch.setattr(
        builder_module, "hvdc_coupling_constraints", coupling_spy
    )
    case = case9()
    T = 2
    df_P = pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1)))
    df_Q = pd.DataFrame(np.tile(case["bus"][:, 3], (T, 1)))
    links = [HVDCLink(4, 9, -10.0, 10.0, device_id="hvdc")]
    df_min = pd.DataFrame({"hvdc": [-10.0, -10.0]})
    df_max = pd.DataFrame({"hvdc": [10.0, 10.0]})

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build_opf_multistep(
            case,
            df_P,
            df_Q,
            T=T,
            formulation=formulation,
            hvdc=links,
            df_hvdc_min=df_min,
            df_hvdc_max=df_max,
            delta=0.5,
        )

    assert len(calls) == 1
    assert len(calls[0][1]) == T
    assert calls[0][3] == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("formulation", "builder_module"),
    [
        ("lossy_dc", dc_problem),
        ("singlenode_dc", singlenode_dc_problem),
    ],
)
def test_dc_builders_compose_generator_network_hook(
    formulation, builder_module, monkeypatch
):
    calls = []

    def network_spy(
        generators,
        network_state,
        ext_to_int,
        controlled_buses,
        *,
        enforce_vset,
    ):
        calls.append((generators, network_state, ext_to_int))
        return []

    monkeypatch.setattr(
        builder_module, "generator_dc_network_constraints", network_spy
    )
    case = case9()
    build_opf(case, formulation=formulation)
    assert len(calls) == 1

    calls.clear()
    T = 2
    df_P = pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1)))
    df_Q = pd.DataFrame(np.tile(case["bus"][:, 3], (T, 1)))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build_opf_multistep(
            case, df_P, df_Q, T=T, formulation=formulation
        )
    assert len(calls) == T


def test_multistep_delta_must_be_positive_without_storage():
    case = case9()
    df_P = pd.DataFrame([case["bus"][:, 2]])
    df_Q = pd.DataFrame([case["bus"][:, 3]])
    with pytest.raises(ValueError, match="delta must be > 0"):
        build_opf_multistep(
            case, df_P, df_Q, T=1, formulation="ac", delta=0.0
        )


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_builders_retain_modeled_net_injection_expression(formulation):
    case = case9()
    single = build_opf(case, formulation=formulation)
    assert isinstance(single.expressions["p_net"], cp.Expression)

    T = 2
    df_P = pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1)))
    df_Q = pd.DataFrame(np.tile(case["bus"][:, 3], (T, 1)))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        multi = build_opf_multistep(
            case, df_P, df_Q, T=T, formulation=formulation
        )

    assert len(multi.expressions["p_net"]) == T
    assert all(
        isinstance(expression, cp.Expression)
        for expression in multi.expressions["p_net"]
    )


@pytest.mark.parametrize("formulation", ["lossy_dc", "singlenode_dc"])
def test_dc_results_use_retained_modeled_net_injection(formulation):
    build = build_opf(case9(), formulation=formulation)
    build.solve()
    expected = np.asarray(build.expressions["p_net"].value) * build.data["baseMVA"]

    # Input metadata is retained for inspection, but is not a second result
    # model. Changing it after solve must not change the reported injection.
    if formulation == "lossy_dc":
        build.data["Pd"] = np.full_like(build.data["Pd"], 1e6)
    else:
        build.data["Pd_total"] = 1e6

    actual = np.asarray(extract_results(build)["p_net"])
    np.testing.assert_allclose(actual, expected)
