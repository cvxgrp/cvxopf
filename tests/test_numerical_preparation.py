"""Assembly-only checks; these are not numerical qualification or study runs."""

from dataclasses import FrozenInstanceError, replace

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import (
    NumericalPreparation,
    OPFOptions,
    build_opf,
    build_opf_multistep,
    gen_from_matpower,
    NondispatchableUnit,
    StorageUnitIdeal,
    Load,
)
from cvxopf import nondispatchable, storage
from cvxopf._numerical_preparation import (
    emit_exact_box,
)
from cvxopf._temporal_assembly import PreparedBoxBounds, VariableBoxFamily
from cvxopf.hierarchical import (
    HierarchicalInputs,
    HierarchicalPolicy,
    solve_hierarchical_opf,
)
from cvxopf.testcases import case9


FORMULATIONS = ("ac", "socp", "lossy_dc", "singlenode_dc")
ASSEMBLIES = ("single", "stepwise", "vectorized")


@pytest.mark.parametrize("name", ["normalize_device_limits", "exact_fixed_boxes"])
@pytest.mark.parametrize("value", [1, 0, None, "true", np.bool_(True)])
def test_policy_requires_actual_bool(name, value):
    with pytest.raises(TypeError, match="must be a bool"):
        NumericalPreparation(**{name: value})


@pytest.mark.parametrize(
    "value", [None, True, 5, "joint6", "auto", np.array("none"), ["none"]]
)
def test_scaling_is_closed(value):
    with pytest.raises(ValueError, match="canonical_scaling"):
        NumericalPreparation(canonical_scaling=value)


def test_policy_is_frozen_and_disabled_by_default():
    policy = OPFOptions().numerical_preparation
    assert not policy.enabled
    with pytest.raises(FrozenInstanceError):
        policy.exact_fixed_boxes = True


@pytest.mark.parametrize("formulation", FORMULATIONS)
@pytest.mark.parametrize("assembly", ASSEMBLIES)
def test_invalid_policy_rejected_before_builder(monkeypatch, formulation, assembly):
    # No data conversion or private builder runs for an invalid typed policy.
    import cvxopf.problem as problem

    def forbidden(*args, **kwargs):
        pytest.fail("private builder must not run")

    monkeypatch.setattr(
        problem, "_get_single_builders", lambda: {formulation: forbidden}
    )
    monkeypatch.setattr(
        problem, "_get_multistep_builders", lambda: {formulation: forbidden}
    )
    options = OPFOptions(numerical_preparation=True)
    with pytest.raises(TypeError, match="NumericalPreparation"):
        if assembly == "single":
            build_opf(case9(), formulation=formulation, options=options)
        else:
            build_opf_multistep(
                case9(),
                T=3,
                formulation=formulation,
                options=options,
                temporal_assembly=assembly,
            )


@pytest.mark.parametrize(
    "formulation,policy",
    [
        ("lossy_dc", NumericalPreparation(normalize_device_limits=True)),
        ("singlenode_dc", NumericalPreparation(normalize_device_limits=True)),
        ("ac", NumericalPreparation(canonical_scaling="joint5")),
    ],
)
def test_inapplicable_fields_rejected(formulation, policy):
    with pytest.raises(ValueError, match="does not support|do not support"):
        build_opf(
            case9(),
            formulation=formulation,
            options=OPFOptions(numerical_preparation=policy),
        )


def _fleet():
    case = case9()
    generators = gen_from_matpower(case["gen"], case["gencost"])
    generators[0] = replace(generators[0], p_min_mw=50, p_max_mw=50)
    generators[1] = replace(generators[1], p_min_mw=30, p_max_mw=30 + 1e-7)
    nd = [
        NondispatchableUnit(
            bus=5, p_available=0, apparent_power_rating=10, device_id="zero"
        ),
        NondispatchableUnit(
            bus=5, p_available=1e-7, apparent_power_rating=10, device_id="near"
        ),
    ]
    batteries = [
        StorageUnitIdeal(
            bus=5,
            apparent_power_rating=10,
            capacity=40,
            initial_soc=20,
            device_id="battery",
        )
    ]
    return case, generators, nd, batteries


def _build(formulation, assembly, policy):
    case, generators, nd, batteries = _fleet()
    kwargs = dict(
        formulation=formulation,
        generators=generators,
        nondispatchable=nd,
        storage=batteries,
        loads=[Load(bus=5, p_load_mw=10, q_load_mvar=2, device_id="load")],
        options=OPFOptions(numerical_preparation=policy),
    )
    if assembly == "single":
        return build_opf(case, **kwargs)
    return build_opf_multistep(
        case,
        T=3,
        temporal_assembly=assembly,
        df_load_p=pd.DataFrame({"load": [10.0, 12.0, 9.0]}),
        df_load_q=pd.DataFrame({"load": [2.0, 2.4, 1.8]}),
        **kwargs,
    )


@pytest.mark.parametrize("formulation", FORMULATIONS)
@pytest.mark.parametrize("assembly", ASSEMBLIES)
def test_exact_bindings_follow_original_variables_units_and_order(
    formulation, assembly
):
    policy = NumericalPreparation(exact_fixed_boxes=True)
    build = _build(formulation, assembly, policy)
    variables = {variable.id: variable for variable in build.prob.variables()}
    constraints = {constraint.id: constraint for constraint in build.prob.constraints}
    assert build.numerical_preparation == policy
    assert len(build._exact_boxes) == (6 if assembly == "stepwise" else 2)
    for binding in build._exact_boxes:
        variable = variables[binding.variable_id]
        assert variable.shape == binding.variable_shape
        equality = constraints[binding.equality_id]
        assert isinstance(equality, cp.constraints.zero.Equality)
        assert equality.is_dcp()
        assert [v.id for v in equality.variables()] == [variable.id]
        is_pg = binding.family == VariableBoxFamily.DISPATCHABLE_P
        expected = [0, 3, 6] if is_pg else [0, 2, 4]
        assert binding.fixed_indices.tolist() == (
            expected if assembly == "vectorized" else [0]
        )
        fixed_value = binding.bounds.lower.ravel(order="F")[binding.fixed_indices]
        np.testing.assert_array_equal(fixed_value, 0.5 if is_pg else 0)
        assert not binding.bounds.lower.flags.writeable
        with pytest.raises(ValueError):
            binding.bounds.lower.setflags(write=True)
        if assembly == "vectorized" and formulation in ("lossy_dc", "singlenode_dc"):
            lower, upper = variable.attributes["bounds"]
            fixed = binding.fixed_indices
            assert np.all(np.isneginf(lower.ravel(order="F")[fixed]))
            assert np.all(np.isposinf(upper.ravel(order="F")[fixed]))
            # Near-fixed coordinates retain their two unequal leaf faces.
            assert np.all(upper[1] > lower[1])
    assert all(
        box.family
        in (
            VariableBoxFamily.DISPATCHABLE_P,
            VariableBoxFamily.NONDISPATCHABLE_REAL_POWER,
        )
        for box in build._exact_boxes
    )


@pytest.mark.parametrize("formulation", FORMULATIONS)
@pytest.mark.parametrize("assembly", ASSEMBLIES)
def test_options_are_build_time_snapshot_and_prepared_solve_uses_local_bridge(
    monkeypatch, formulation, assembly
):
    build = _build(formulation, assembly, NumericalPreparation(exact_fixed_boxes=True))
    monkeypatch.setattr(
        cp.Problem, "solve", lambda *a, **k: pytest.fail("no numerical call")
    )
    import cvxopf._convex_preparation as convex
    import cvxopf._ac_preparation as ac
    calls = []
    monkeypatch.setattr(convex, "solve_prepared_convex", lambda b, k: calls.append((b, k)))
    monkeypatch.setattr(ac, "solve_prepared_ac", lambda b, k: calls.append((b, k)))
    build.solve()
    assert calls == [(build, {})]
    with pytest.raises(AttributeError):
        build.numerical_preparation = NumericalPreparation()
    options = OPFOptions(
        numerical_preparation=NumericalPreparation(exact_fixed_boxes=True)
    )
    second = build_opf(case9(), formulation=formulation, options=options)
    options.numerical_preparation = NumericalPreparation()
    assert second.numerical_preparation.exact_fixed_boxes


@pytest.mark.parametrize("formulation", FORMULATIONS)
@pytest.mark.parametrize("assembly", ASSEMBLIES)
def test_disabled_path_has_no_bindings_and_calls_existing_solver(
    monkeypatch, formulation, assembly
):
    build = _build(formulation, assembly, NumericalPreparation())
    assert not build._exact_boxes
    calls = []
    monkeypatch.setattr(
        cp.Problem, "solve", lambda prob, **kwargs: calls.append(kwargs)
    )
    build.solve()
    assert len(calls) == 1
    assert calls[0]["solver"] == (cp.IPOPT if formulation == "ac" else cp.CLARABEL)
    assert calls[0]["nlp"] is (formulation == "ac")


@pytest.mark.parametrize("formulation", ["ac", "socp"])
@pytest.mark.parametrize("assembly", ASSEMBLIES)
@pytest.mark.parametrize("enabled", [False, True])
def test_component_selection_preserves_disabled_invocation(
    monkeypatch, formulation, assembly, enabled
):
    calls = []
    name = (
        "vectorized_ac_operating_constraints"
        if assembly == "vectorized"
        else "ac_operating_constraints"
    )
    for module in (storage, nondispatchable):
        original = getattr(module, name)

        def capture(*args, _original=original, **kwargs):
            calls.append(kwargs)
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, capture)
    build = _build(
        formulation, assembly, NumericalPreparation(normalize_device_limits=enabled)
    )
    assert build.numerical_preparation.normalize_device_limits is enabled
    assert calls
    assert all(
        options
        == (
            {"normalize_limits": True, "use_soc": formulation == "socp"}
            if enabled
            else {}
        )
        for options in calls
    )
    assert not build._exact_boxes


@pytest.mark.parametrize("formulation", ["ac", "socp"])
@pytest.mark.parametrize("assembly", ASSEMBLIES)
def test_normalized_graph_without_optional_devices(formulation, assembly):
    kwargs = dict(
        formulation=formulation,
        loads=[],
        options=OPFOptions(
            numerical_preparation=NumericalPreparation(
                normalize_device_limits=True, exact_fixed_boxes=True
            )
        ),
    )
    if assembly == "single":
        build = build_opf(case9(), **kwargs)
    else:
        build = build_opf_multistep(
            case9(),
            T=1,
            df_load_p=pd.DataFrame(index=[0]),
            temporal_assembly=assembly,
            **kwargs,
        )
    assert not build._exact_boxes
    assert "p_nd" not in build.variables
    assert "b" not in build.variables


def test_shared_emitter_preserves_user_coupling_and_defensive_bounds():
    lower, upper = np.array([2.0, 3.0, 4.0]), np.array([2.0, 3.0 + 1e-12, 6.0])
    lower.flags.writeable = upper.flags.writeable = False
    box = PreparedBoxBounds(lower, upper)
    variable = cp.Variable(3)
    user = variable[0] + variable[2] == 8
    emitted = emit_exact_box(
        variable, box, VariableBoxFamily.DISPATCHABLE_P, "explicit"
    )
    assert len(emitted.constraints) == 3
    assert user.id != emitted.exact_boxes[0].equality_id
    assert emitted.exact_boxes[0].fixed_indices.tolist() == [0]
    lower.flags.writeable = True
    lower[0] = 999
    assert emitted.exact_boxes[0].bounds.lower[0] == 2
    variable.value = [2, 3, 6]
    assert all(np.max(c.violation()) == 0 for c in (*emitted.constraints, user))


def test_box_identity_and_all_fixed_graphs_are_not_solver_convergence():
    for lower, upper, expected_constraints in [
        ([0.0, 0.0], [1.0, 2.0], 2),
        ([1.0, 2.0], [1.0, 2.0], 1),
    ]:
        arrays = [np.array(lower), np.array(upper)]
        for array in arrays:
            array.flags.writeable = False
        box = PreparedBoxBounds(*arrays)
        emitted = emit_exact_box(
            cp.Variable(2), box, VariableBoxFamily.DISPATCHABLE_P, "explicit"
        )
        assert len(emitted.constraints) == expected_constraints
        assert bool(emitted.exact_boxes) is (expected_constraints == 1)


@pytest.mark.parametrize("use_soc", [False, True])
@pytest.mark.parametrize("vectorized", [False, True])
@pytest.mark.parametrize("component", ["nd", "storage"])
def test_normalized_device_region_and_physical_units(component, vectorized, use_soc):
    shape = (2, 3) if vectorized else (2,)
    p, q = cp.Variable(shape), cp.Variable(shape)
    ratings = np.array([10.0, 1000.0])
    units = (
        [
            NondispatchableUnit(bus=1, p_available=s, apparent_power_rating=s)
            for s in ratings
        ]
        if component == "nd"
        else [
            StorageUnitIdeal(
                bus=1, apparent_power_rating=s, capacity=40, initial_soc=20
            )
            for s in ratings
        ]
    )
    module = nondispatchable if component == "nd" else storage
    method = (
        module.vectorized_ac_operating_constraints
        if vectorized
        else module.ac_operating_constraints
    )
    if component == "nd":
        aux = np.broadcast_to(ratings[:, None], shape) if vectorized else ratings
    else:
        aux = cp.Variable((2, 4) if vectorized else (2,))
        aux.value = np.full(aux.shape, 20.0)
    constraints = method(units, p, q, aux, normalize_limits=True, use_soc=use_soc)
    assert all(c.is_dcp() for c in constraints)
    radii = ratings[:, None] if vectorized else ratings
    p.value, q.value = (
        np.broadcast_to(0.6 * radii, shape),
        np.broadcast_to(0.8 * radii, shape),
    )
    assert all(np.max(c.violation()) < 1e-12 for c in constraints)
    q.value = np.broadcast_to(0.9 * radii, shape)
    assert any(np.max(c.violation()) > 0.01 for c in constraints)


@pytest.mark.parametrize("module", [nondispatchable, storage])
@pytest.mark.parametrize("shape", [(2,), (2, 1), (2, 3)])
def test_normalized_soc_has_no_norm_epigraph(module, shape):
    p, q = cp.Variable(shape), cp.Variable(shape)
    ratings = np.array([10.0, 1000.0])
    if len(shape) == 2:
        ratings = ratings[:, None]
    constraints = module._normalized_capability_constraints(p, q, ratings, True)
    assert len(constraints) == 1
    assert isinstance(constraints[0], cp.constraints.second_order.SOC)
    assert constraints[0].is_dcp()
    # Canonicalize only: two original power channels and one direct 3D cone
    # per device/time point, with no extra epigraph columns or inequalities.
    data, _, _ = cp.Problem(cp.Minimize(0), constraints).get_problem_data(cp.CLARABEL)
    assert data["A"].shape == (3 * p.size, 2 * p.size)
    assert data["dims"].soc == [3] * p.size
    assert data["dims"].nonneg == 0
    assert data["dims"].zero == 0


@pytest.mark.parametrize(
    "policy",
    [
        NumericalPreparation(normalize_device_limits=True),
        NumericalPreparation(exact_fixed_boxes=True),
        NumericalPreparation(canonical_scaling="joint5"),
    ],
)
def test_hierarchy_rejects_preparation_before_build(monkeypatch, policy):
    case, generators, _, batteries = _fleet()
    inputs_kwargs = dict(
        case=case,
        generators=generators,
        storage=batteries,
        horizon_steps=3,
        delta=1.0,
        loads=[Load(bus=5, p_load_mw=10, device_id="load")],
        df_load_p=pd.DataFrame({"load": [10.0, 12.0, 9.0]}),
    )
    with pytest.raises(ValueError, match="Milestone 21"):
        HierarchicalInputs(
            **inputs_kwargs, options=OPFOptions(numerical_preparation=policy)
        )
    inputs = HierarchicalInputs(**inputs_kwargs)
    inputs.options.numerical_preparation = policy
    import cvxopf._hierarchical_solver as execution

    monkeypatch.setattr(
        execution, "_execution_snapshot", lambda *a: pytest.fail("no layer build")
    )
    with pytest.raises(ValueError, match="Milestone 21"):
        solve_hierarchical_opf(inputs, HierarchicalPolicy(ac_window_steps=1))
    from experiments.case118_annual_hierarchy.streaming_runner import (
        execution_input_sha256,
    )

    with pytest.raises(ValueError, match="legacy hierarchy fingerprints"):
        execution_input_sha256(inputs)
