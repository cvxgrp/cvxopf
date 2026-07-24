"""Cross-device conformance tests for the Milestone 16 component contract."""

import cvxpy as cp

from cvxopf import (
    DispatchableGenerator,
    HVDCLink,
    NondispatchableUnit,
    StorageUnitIdeal,
)
from cvxopf import generator, hvdc, nondispatchable, storage


def test_all_components_expose_network_and_temporal_interface():
    for module in (generator, storage, nondispatchable, hvdc):
        assert callable(module.ac_injections)
        assert callable(module.dc_injections)
        assert callable(module.ac_operating_constraints)
        assert callable(module.dc_operating_constraints)
        assert callable(module.coupling_constraints)


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


def test_memoryless_components_have_empty_coupling_slot():
    assert generator.coupling_constraints() == []
    assert nondispatchable.coupling_constraints() == []
    assert hvdc.coupling_constraints() == []
