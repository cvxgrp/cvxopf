"""Structural tests for the M14b horizon-assembly vocabulary."""

import numpy as np
import pytest

from cvxopf._temporal_assembly import (
    HorizonVariableSpec,
    ResultProjectionRegistry,
    ResultProjectionSpec,
    TemporalFieldSpec,
    VariableBoxFamily,
    box_representation_decision,
    broadcast_static_bound,
    merge_result_projection_registries,
    pending_component_box_families,
    pending_component_box_pairs,
    prepare_box_bounds,
)


@pytest.mark.parametrize(
    ("temporal_class", "public_shape", "internal_shape"),
    [
        ("static", (2, 3), (2, 3)),
        ("interval", (4, 2, 3), (2, 3, 4)),
        ("boundary", (5, 2, 3), (2, 3, 5)),
    ],
)
def test_temporal_field_shapes_and_axis_round_trip(
    temporal_class, public_shape, internal_shape
):
    spec = TemporalFieldSpec("field", (2, 3), temporal_class)
    public = np.arange(np.prod(public_shape)).reshape(public_shape)

    internal = spec.to_internal(public, 4)

    assert spec.public_shape(4) == public_shape
    assert spec.internal_shape(4) == internal_shape
    assert internal.shape == internal_shape
    assert np.shares_memory(public, internal)
    assert np.array_equal(spec.to_public(internal, 4), public)


def test_scalar_interval_field_and_boundary_variable_lift_one_axis():
    interval = TemporalFieldSpec("rate", (), "interval")
    boundary = HorizonVariableSpec(
        "soc",
        (3,),
        "boundary",
        result_view="post_step_boundaries",
    )

    assert interval.internal_shape(7) == (7,)
    assert boundary.shape(7) == (3, 8)


def test_temporal_field_preserves_explicit_empty_component_axis():
    spec = TemporalFieldSpec("empty_load", (0,), "interval")
    public = np.empty((4, 0))

    internal = spec.to_internal(public, 4)

    assert internal.shape == (0, 4)
    assert spec.to_public(internal, 4).shape == public.shape


def test_horizon_variable_rejects_zero_sized_axes():
    with pytest.raises(ValueError, match="positive integers"):
        HorizonVariableSpec("empty", (0,))


@pytest.mark.parametrize("horizon_steps", [0, -1, True])
def test_temporal_contract_rejects_invalid_horizons(horizon_steps):
    spec = TemporalFieldSpec("field", (2,), "interval")

    with pytest.raises(ValueError, match="positive integer"):
        spec.internal_shape(horizon_steps)


def test_temporal_contract_rejects_shape_mismatch():
    spec = TemporalFieldSpec("load", (2,), "interval")

    with pytest.raises(ValueError, match="public shape"):
        spec.to_internal(np.ones((2, 4)), 4)


def test_static_bound_broadcast_is_read_only_zero_stride_view():
    source = np.array([1.0, 2.0, 3.0])

    bound = broadcast_static_bound(source, native_shape=(3,), horizon_steps=6)

    assert bound.shape == (3, 6)
    assert bound.strides[-1] == 0
    assert not bound.flags.writeable
    assert np.shares_memory(source, bound)
    assert np.array_equal(bound[:, 0], source)
    assert np.array_equal(bound[:, -1], source)


def test_static_boundary_bound_uses_t_plus_one():
    bound = broadcast_static_bound(
        np.array([4.0, 5.0]),
        native_shape=(2,),
        horizon_steps=3,
        temporal_class="boundary",
    )

    assert bound.shape == (2, 4)


def test_box_preparation_validates_static_bounds_without_tiling():
    lower = np.array([-2.0, -1.0])
    upper = np.array([3.0, 4.0])

    prepared = prepare_box_bounds(
        lower,
        upper,
        native_shape=(2,),
        horizon_steps=5,
        lower_temporal_class="static",
        upper_temporal_class="static",
        variable_temporal_class="interval",
    )

    assert prepared.lower.shape == prepared.upper.shape == (2, 5)
    assert prepared.lower.strides[-1] == prepared.upper.strides[-1] == 0
    assert np.shares_memory(lower, prepared.lower)
    assert np.shares_memory(upper, prepared.upper)
    assert not prepared.lower.flags.writeable
    assert not prepared.upper.flags.writeable


def test_box_preparation_moves_interval_axis_once():
    lower = np.arange(6, dtype=float).reshape(3, 2)
    upper = lower + 1.0

    prepared = prepare_box_bounds(
        lower,
        upper,
        native_shape=(2,),
        horizon_steps=3,
        lower_temporal_class="interval",
        upper_temporal_class="interval",
        variable_temporal_class="interval",
    )

    assert prepared.lower.shape == prepared.upper.shape == (2, 3)
    assert np.array_equal(prepared.lower, lower.T)
    assert np.array_equal(prepared.upper, upper.T)
    assert np.shares_memory(lower, prepared.lower)
    assert np.shares_memory(upper, prepared.upper)


@pytest.mark.parametrize(
    ("lower", "upper", "message"),
    [
        (np.array([np.nan]), np.array([1.0]), "lower bounds"),
        (np.array([0.0]), np.array([np.inf]), "upper bounds"),
        (np.array([2.0]), np.array([1.0]), "must not exceed"),
    ],
)
def test_box_preparation_rejects_nonfinite_or_reversed_bounds(lower, upper, message):
    with pytest.raises(ValueError, match=message):
        prepare_box_bounds(
            lower,
            upper,
            native_shape=(1,),
            horizon_steps=2,
            lower_temporal_class="static",
            upper_temporal_class="static",
            variable_temporal_class="interval",
        )


def test_static_soc_bounds_target_t_plus_one_boundary_states_without_tiling():
    capacity = np.array([10.0, 20.0])

    prepared = prepare_box_bounds(
        np.zeros(2),
        capacity,
        native_shape=(2,),
        horizon_steps=4,
        lower_temporal_class="static",
        upper_temporal_class="static",
        variable_temporal_class="boundary",
    )

    assert prepared.lower.shape == prepared.upper.shape == (2, 5)
    assert prepared.upper.strides[-1] == 0
    assert np.shares_memory(capacity, prepared.upper)
    assert np.array_equal(prepared.upper[:, -1], capacity)


def test_dynamic_box_data_must_match_target_variable_temporality():
    with pytest.raises(ValueError, match="must match"):
        prepare_box_bounds(
            np.zeros((3, 1)),
            np.ones((3, 1)),
            native_shape=(1,),
            horizon_steps=2,
            lower_temporal_class="boundary",
            upper_temporal_class="boundary",
            variable_temporal_class="interval",
        )


def test_box_faces_support_mixed_static_and_interval_temporality():
    lower = np.zeros(2)
    upper = np.arange(6, dtype=float).reshape(3, 2) + 1.0

    prepared = prepare_box_bounds(
        lower,
        upper,
        native_shape=(2,),
        horizon_steps=3,
        lower_temporal_class="static",
        upper_temporal_class="interval",
        variable_temporal_class="interval",
    )

    assert prepared.lower.shape == prepared.upper.shape == (2, 3)
    assert prepared.lower.strides[-1] == 0
    assert np.shares_memory(lower, prepared.lower)
    assert np.shares_memory(upper, prepared.upper)
    assert np.array_equal(prepared.upper, upper.T)


def test_multistep_t1_preserves_interval_and_boundary_axes():
    interval = TemporalFieldSpec("dispatch", (2,), "interval")
    boundary = TemporalFieldSpec("state", (2,), "boundary")
    interval_public = np.array([[1.0, 2.0]])
    boundary_public = np.array([[3.0, 4.0], [5.0, 6.0]])

    interval_internal = interval.to_internal(interval_public, 1)
    boundary_internal = boundary.to_internal(boundary_public, 1)

    assert interval_internal.shape == (2, 1)
    assert boundary_internal.shape == (2, 2)
    assert np.array_equal(interval.to_public(interval_internal, 1), interval_public)
    assert np.array_equal(boundary.to_public(boundary_internal, 1), boundary_public)


def test_interval_result_projection_moves_time_first_and_keeps_t1_unsqueezed():
    projection = ResultProjectionSpec("Pg", (2,), (2,), "interval")
    values = np.array([[1.0], [2.0]])

    public = projection.project(values, 1)

    assert projection.internal_shape(1) == (2, 1)
    assert projection.public_shape(1) == (1, 2)
    assert public.shape == (1, 2)
    np.testing.assert_array_equal(public, [[1.0, 2.0]])


def test_boundary_result_projection_selects_post_step_states_explicitly():
    projection = ResultProjectionSpec("soc", (2,), (2,), "post_step_boundaries")
    values = np.array([[10.0, 11.0, 12.0], [20.0, 21.0, 22.0]])

    public = projection.project(values, 2)

    assert projection.internal_shape(2) == (2, 3)
    assert projection.public_shape(2) == (2, 2)
    np.testing.assert_array_equal(public, [[11.0, 21.0], [12.0, 22.0]])


def test_result_projection_can_publish_all_boundaries_or_reshape_native_axes():
    boundaries = ResultProjectionSpec("state", (2,), (2,), "all_boundaries")
    flattened = ResultProjectionSpec("v", (2, 1), (2,), "interval")

    np.testing.assert_array_equal(
        boundaries.project(np.array([[1.0, 2.0], [3.0, 4.0]]), 1),
        [[1.0, 3.0], [2.0, 4.0]],
    )
    voltage = flattened.project(np.arange(6).reshape(2, 1, 3), 3)
    assert voltage.shape == (3, 2)
    np.testing.assert_array_equal(voltage, [[0, 3], [1, 4], [2, 5]])


def test_horizon_result_projection_has_no_temporal_axis():
    projection = ResultProjectionSpec("ens", (2, 1), (2,), "horizon")

    public = projection.project(np.array([[3.0], [4.0]]), 8)

    assert public.shape == (2,)
    np.testing.assert_array_equal(public, [3.0, 4.0])


def test_result_projection_rejects_shape_and_coordinate_drift():
    with pytest.raises(ValueError, match="same number of coordinates"):
        ResultProjectionSpec("bad", (2,), (3,), "interval")
    with pytest.raises(ValueError, match="only by removing singleton axes"):
        ResultProjectionSpec("reordered", (2, 3), (3, 2), "interval")
    with pytest.raises(ValueError, match="only by removing singleton axes"):
        ResultProjectionSpec("moved_singleton", (2, 1), (1, 2), "interval")
    projection = ResultProjectionSpec("Pg", (2,), (2,), "interval")
    with pytest.raises(ValueError, match="internal shape"):
        projection.project(np.ones((2, 4)), 3)


def test_horizon_variable_retains_its_public_result_projection():
    interval = HorizonVariableSpec("Pg", (3,))
    boundary = HorizonVariableSpec(
        "soc",
        (2,),
        "boundary",
        result_view="post_step_boundaries",
    )
    voltage = HorizonVariableSpec(
        "v",
        (4, 1),
        public_native_shape=(4,),
    )

    assert interval.result_projection().temporal_view == "interval"
    assert boundary.result_projection().temporal_view == "post_step_boundaries"
    assert voltage.result_projection().public_native_shape == (4,)

    with pytest.raises(ValueError, match="only by removing singleton axes"):
        HorizonVariableSpec("bad", (2, 3), public_native_shape=(3, 2))


def test_boundary_variable_requires_deliberate_public_result_view():
    with pytest.raises(ValueError, match="explicit public result view"):
        HorizonVariableSpec("soc", (2,), "boundary")


def test_result_projection_registry_is_immutable_source_specific_and_mergeable():
    variable = ResultProjectionSpec("Pg", (2,), (2,), "interval")
    expression = ResultProjectionSpec("p_net", (3,), (3,), "interval")
    registry = merge_result_projection_registries(
        ResultProjectionRegistry(variables={"Pg": variable}),
        ResultProjectionRegistry(expressions={"p_net": expression}),
    )

    assert registry.projection_for("variable", "Pg") is variable
    assert registry.projection_for("expression", "p_net") is expression
    with pytest.raises(TypeError):
        registry.variables["other"] = variable
    with pytest.raises(ValueError, match="no declared"):
        registry.projection_for("variable", "missing")
    with pytest.raises(ValueError, match="duplicate variable"):
        merge_result_projection_registries(
            registry,
            ResultProjectionRegistry(variables={"Pg": variable}),
        )


@pytest.mark.parametrize(
    ("formulation", "family"),
    [
        ("lossy_dc", VariableBoxFamily.DISPATCHABLE_P),
        ("lossy_dc", VariableBoxFamily.DC_BRANCH_FLOW),
        ("singlenode_dc", VariableBoxFamily.DISPATCHABLE_P),
    ],
)
def test_m14a1_qualified_convex_boxes_select_leaf(formulation, family):
    decision = box_representation_decision(formulation, family)

    assert decision.representation == "leaf"
    assert decision.authority == "m14a1_qualified"
    assert not decision.requires_focused_qualification


def test_existing_ac_voltage_leaf_is_not_blanket_ac_authorization():
    voltage = box_representation_decision("ac", VariableBoxFamily.AC_VOLTAGE)
    active = box_representation_decision("ac", VariableBoxFamily.DISPATCHABLE_P)
    reactive = box_representation_decision("ac", VariableBoxFamily.DISPATCHABLE_Q)

    assert voltage.representation == "leaf"
    assert voltage.authority == "existing_production"
    assert active.representation == reactive.representation == "explicit"
    assert active.authority == reactive.authority == "ac_explicit_policy"


def test_component_boxes_are_explicit_pending_focused_convex_gates():
    expected = {
        VariableBoxFamily.STORAGE_REAL_POWER,
        VariableBoxFamily.STORAGE_SOC,
        VariableBoxFamily.NONDISPATCHABLE_REAL_POWER,
        VariableBoxFamily.HVDC_INPUT_POWER,
        VariableBoxFamily.LOAD_SHED_FRACTION,
    }
    assert set(pending_component_box_families()) == expected

    expected_pairs = {
        *(("lossy_dc", family) for family in expected),
        *(
            ("singlenode_dc", family)
            for family in expected
            if family is not VariableBoxFamily.HVDC_INPUT_POWER
        ),
    }
    assert set(pending_component_box_pairs()) == expected_pairs
    for formulation, family in expected_pairs:
        decision = box_representation_decision(formulation, family)
        assert decision.representation == "explicit"
        assert decision.authority == "pending_component_gate"
        assert decision.requires_focused_qualification


def test_component_box_gates_never_authorize_ac():
    for family in (
        VariableBoxFamily.STORAGE_SOC,
        VariableBoxFamily.NONDISPATCHABLE_REAL_POWER,
        VariableBoxFamily.HVDC_INPUT_POWER,
        VariableBoxFamily.LOAD_SHED_FRACTION,
    ):
        decision = box_representation_decision("ac", family)
        assert decision.representation == "explicit"
        assert decision.authority == "ac_explicit_policy"
        assert not decision.requires_focused_qualification


@pytest.mark.parametrize(
    ("formulation", "family"),
    [
        ("singlenode_dc", VariableBoxFamily.DC_BRANCH_FLOW),
        ("singlenode_dc", VariableBoxFamily.HVDC_INPUT_POWER),
        ("ac", VariableBoxFamily.STORAGE_REAL_POWER),
    ],
)
def test_inapplicable_box_formulation_pair_is_rejected(formulation, family):
    with pytest.raises(ValueError, match="does not apply"):
        box_representation_decision(formulation, family)
