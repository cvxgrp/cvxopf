"""Tests for shared typed component orchestration."""

import ast
from dataclasses import replace
import inspect

import cvxpy as cp
import numpy as np
import pytest

from cvxopf import DispatchableGenerator, HVDCLink, StorageUnitIdeal
from cvxopf import ac_problem, dc_problem, singlenode_dc_problem
from cvxopf._component_adapter import (
    DCNetworkState,
    FormulationCapability,
    HorizonContext,
    PreparationContext,
    StepContext,
)
from cvxopf._component_adapters import (
    GENERATOR_ADAPTER,
    HVDC_ADAPTER,
    STORAGE_ADAPTER,
    component_requests,
)
from cvxopf._component_assembly import (
    ComponentRequest,
    aggregate_horizon_contributions,
    aggregate_step_contributions,
    assemble_component_horizon,
    assemble_component_step,
    prepare_components,
    publish_component_metadata,
    publish_component_variables,
)


def _preparation(horizon_steps=1):
    return PreparationContext(
        base_mva=100.0,
        nb=2,
        ext_to_int={1: 0, 2: 1},
        ext_bus_ids=frozenset({1, 2}),
        horizon_steps=horizon_steps,
        delta=0.5,
        is_multistep=horizon_steps > 1,
    )


def test_shared_assembly_composes_active_components_across_horizon():
    generator = DispatchableGenerator(
        bus=1,
        p_max_mw=100.0,
        q_min_mvar=-50.0,
        q_max_mvar=50.0,
    )
    storage = StorageUnitIdeal(
        bus=2,
        apparent_power_rating=20.0,
        capacity=40.0,
        initial_soc=20.0,
    )
    prepared = prepare_components(
        (
            ComponentRequest(GENERATOR_ADAPTER, (generator,)),
            ComponentRequest(STORAGE_ADAPTER, (storage,)),
        ),
        "lossy_dc",
        _preparation(horizon_steps=2),
    )
    context_0 = StepContext(
        "lossy_dc", 0, 100.0, {1: 0, 2: 1}, DCNetworkState()
    )
    context_1 = StepContext(
        "lossy_dc", 1, 100.0, {1: 0, 2: 1}, DCNetworkState()
    )
    step_0 = assemble_component_step(
        prepared, context_0, variable_suffix="_0"
    )
    step_1 = assemble_component_step(
        prepared, context_1, variable_suffix="_1"
    )

    assert tuple(step_0) == ("generator", "storage")
    assert step_0["generator"].variables["Pg"].name() == "Pg_0"
    assert step_1["storage"].variables["soc"].name() == "soc_1"
    scale = step_0["storage"].injection.inv_base_mva
    assert scale is not None
    assert scale.value == pytest.approx(0.01)
    assert step_0["generator"].injection.inv_base_mva is None
    step_aggregate = aggregate_step_contributions(step_0)
    assert step_aggregate.injection.p_pu is not None
    assert step_aggregate.injection.q_pu is None
    assert len(step_aggregate.operating_constraints) == (
        len(step_0["generator"].operating_constraints)
        + len(step_0["storage"].operating_constraints)
    )
    assert step_aggregate.cost is not None

    horizon = assemble_component_horizon(
        prepared,
        (step_0, step_1),
        HorizonContext("lossy_dc", 2, 0.5),
    )
    assert len(horizon["storage"].constraints) == 2
    assert horizon["generator"].constraints == ()
    horizon_aggregate = aggregate_horizon_contributions(horizon)
    assert len(horizon_aggregate.constraints) == 2

    variables = publish_component_variables(
        (step_0, step_1), multistep=True
    )
    assert set(variables) == {"Pg", "b", "soc"}
    assert len(variables["Pg"]) == 2
    metadata = publish_component_metadata(prepared)
    assert {"ng", "ns", "storage_delta"} <= set(metadata)


def test_common_registry_has_one_order_for_every_formulation():
    generator = DispatchableGenerator(bus=1, p_max_mw=100.0)
    storage = StorageUnitIdeal(2, 20.0, 40.0, 20.0)
    link = HVDCLink(1, 2, -10.0, 10.0)
    for formulation in ("ac", "lossy_dc", "singlenode_dc"):
        requests = component_requests(
            formulation,
            generators=(generator,),
            storage_units=(storage,),
            hvdc_links=(link,),
        )
        assert tuple(request.adapter.name for request in requests) == (
            "generator",
            "storage",
            "nondispatchable",
            "hvdc",
        )
        expected_hvdc = (
            FormulationCapability.NULL
            if formulation == "singlenode_dc"
            else FormulationCapability.ACTIVE
        )
        assert requests[-1].required_capability is expected_hvdc


def test_shared_assembly_distinguishes_absent_and_explicit_null():
    link = HVDCLink(1, 2, -10.0, 10.0)
    context = _preparation()
    absent = prepare_components(
        (
            ComponentRequest(
                HVDC_ADAPTER,
                (),
                required_capability=FormulationCapability.NULL,
            ),
        ),
        "singlenode_dc",
        context,
    )
    assert absent.components == {}

    explicit_null = prepare_components(
        (
            ComponentRequest(
                HVDC_ADAPTER,
                (link,),
                required_capability=FormulationCapability.NULL,
            ),
        ),
        "singlenode_dc",
        context,
    )
    assert tuple(explicit_null.components) == ("hvdc",)
    step = assemble_component_step(
        explicit_null,
        StepContext(
            "singlenode_dc",
            0,
            100.0,
            {1: 0, 2: 1},
            DCNetworkState(),
        ),
    )
    assert step["hvdc"].variables == {}
    assert step["hvdc"].injection.p_pu is None
    assert publish_component_variables((step,), multistep=False) == {}
    assert publish_component_metadata(explicit_null) == {}


def test_required_capability_is_checked_only_for_supplied_components():
    formulations = dict(HVDC_ADAPTER.formulations)
    formulations["singlenode_dc"] = formulations["lossy_dc"]
    corrupted = replace(HVDC_ADAPTER, formulations=formulations)
    request = ComponentRequest(
        corrupted,
        (),
        required_capability=FormulationCapability.NULL,
    )
    prepare_components((request,), "singlenode_dc", _preparation())

    with pytest.raises(RuntimeError, match="registered null HVDC"):
        prepare_components(
            (replace(request, units=(HVDCLink(1, 2, -10.0, 10.0),)),),
            "singlenode_dc",
            _preparation(),
        )


def test_shared_assembly_rejects_formulation_and_horizon_mismatches():
    generator = DispatchableGenerator(
        bus=1, p_max_mw=100.0, cost_coeffs=(0.0, 1.0, 0.0)
    )
    prepared = prepare_components(
        (ComponentRequest(GENERATOR_ADAPTER, (generator,)),),
        "lossy_dc",
        _preparation(),
    )
    with pytest.raises(ValueError, match="step formulation"):
        assemble_component_step(
            prepared,
            StepContext(
                "singlenode_dc",
                0,
                100.0,
                {1: 0, 2: 1},
                DCNetworkState(),
            ),
        )
    step = assemble_component_step(
        prepared,
        StepContext(
            "lossy_dc",
            0,
            100.0,
            {1: 0, 2: 1},
            DCNetworkState(),
        ),
    )
    with pytest.raises(ValueError, match="horizon_steps"):
        assemble_component_horizon(
            prepared,
            (step,),
            HorizonContext("lossy_dc", 2, 1.0),
        )

    assert isinstance(step["generator"].cost, cp.Expression)
    np.testing.assert_equal(
        step["generator"].variables["Pg"].shape, (1,)
    )


def test_formulation_builders_do_not_call_adapter_hooks_directly():
    hook_names = {
        "prepare",
        "variable_specs",
        "injections",
        "operating_constraints",
        "network_constraints",
        "step_cost",
        "horizon",
        "metadata",
    }
    for module in (ac_problem, dc_problem, singlenode_dc_problem):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        direct_hook_calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in hook_names
        }
        assert direct_hook_calls == set()
        assert "ComponentRequest(" not in source
        assert "_ADAPTER" not in source
