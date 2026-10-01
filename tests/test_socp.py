"""Public SOCP construction, shared results and bounded numerical checks.

Budget/settings: experiments/m11_socp/PLAN.md. No private fixtures.
"""

from dataclasses import replace

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import (
    build_opf, build_opf_multistep, extract_results, OPFOptions,
    StorageUnitIdeal, NondispatchableUnit, HVDCLink, Load,
    audit_socp_relaxation, recover_socp_voltage,
)
from cvxopf.testcases import case9, case14


def solve(build):
    assert build.is_convex and build.prob.is_dcp()
    build.solve(max_iter=200, time_limit=20)
    result = extract_results(build)
    assert result["status"] in ("optimal", "optimal_inaccurate")
    audit = audit_socp_relaxation(build, result)
    assert audit["feasible"], audit
    return result


def multistep(case, T=3, **kwargs):
    if "loads" in kwargs:
        return build_opf_multistep(case, T=T, formulation="socp", **kwargs)
    p = pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1)))
    q = pd.DataFrame(np.tile(case["bus"][:, 3], (T, 1)))
    return build_opf_multistep(case, p, q, T=T, formulation="socp", **kwargs)


@pytest.mark.parametrize("factory", [case9, case14])
def test_public_single_and_t1_parity(factory):
    case = factory()
    single = build_opf(case, formulation="socp")
    reference = solve(single)
    assert "Vm" not in reference and "Va_deg" not in reference
    np.testing.assert_allclose(reference["Vm_relaxed"]**2, reference["w"])
    for assembly in ("vectorized", "stepwise"):
        build = multistep(case, T=1, temporal_assembly=assembly)
        result = solve(build)
        assert build.canonicalization_backend == ("SCIPY" if assembly == "vectorized" else "CPP")
        assert result["objective"] == pytest.approx(reference["objective"], rel=2e-6)
        for key in ("w", "W_re", "W_im", "Pg", "Qg", "p_net", "q_net", "branch_p_from"):
            assert result[key].shape == (1, *reference[key].shape)
        np.testing.assert_allclose(result["branch_loss_mw"], result["branch_p_from"] + result["branch_p_to"])


@pytest.mark.parametrize("terminal", ["equality", "shortfall", "soft"])
def test_mixed_devices_series_delta_terminal_and_stepwise_parity(terminal):
    case = case9()
    storage = StorageUnitIdeal(bus=5, apparent_power_rating=10, capacity=20,
                               initial_soc=12, terminal_soc=10, device_id="battery")
    storage = replace(storage, **({"terminal_cost": "quadratic", "terminal_weight": 3}
                                 if terminal == "soft" else {"terminal_constraint": terminal}))
    kwargs = dict(
        delta=0.5, storage=[storage],
        nondispatchable=[NondispatchableUnit(bus=5, p_available=4,
            apparent_power_rating=8, device_id="solar")],
        df_nd=pd.DataFrame({"solar": [4, 6, 3]}),
        hvdc=[HVDCLink(from_bus=1, to_bus=5, p_min_mw=-10, p_max_mw=-1,
                       loss_percent=3, device_id="link")],
        loads=[Load(bus=5, p_load_mw=80, q_load_mvar=20, device_id="a", shedding_cost_per_mwh=1000),
               Load(bus=7, p_load_mw=100, q_load_mvar=30, device_id="b", shedding_cost_per_mwh=1000)],
        df_load_p=pd.DataFrame({"b": [100, 110, 90], "a": [80, 0, 85]}),
        df_load_q=pd.DataFrame({"b": [30, 35, 25], "a": [20, 3, 20]}),
    )
    results = []
    for assembly in ("vectorized", "stepwise"):
        build = multistep(case, temporal_assembly=assembly, **kwargs)
        result = solve(build)
        results.append(result)
        assert result["soc"].shape == (3, 1)
        assert result["q_load_served"].shape == (3, 2)
        assert result["load_shed_fraction"].shape == (3, 2)
        np.testing.assert_allclose(result["soc"][:, 0], 12 - 0.5 * np.cumsum(result["b"][:, 0]), atol=1e-5)
        assert result["q_load_served"][1, 0] == pytest.approx(3)
        assert result["load_shed_fraction"][1, 0] == pytest.approx(0, abs=1e-7)
        if terminal != "soft":
            assert result["soc"][-1, 0] >= 10 - 1e-5
        costs = [value.value for key, value in build.expressions.items() if key in
                 ("generator_cost", "storage_cost", "storage_terminal_cost", "hvdc_cost", "load_shedding_cost")]
        assert sum(costs) == pytest.approx(result["objective"], rel=1e-8)
        recovered = recover_socp_voltage(build, result)
        assert recovered["available"]
        for key in ("Pg", "Qg", "b", "b_q", "soc", "p_nd", "q_nd", "p_hvdc_in", "p_hvdc_out", "load_shed_fraction"):
            np.testing.assert_array_equal(recovered["candidate"][key], result[key])
    assert results[0]["objective"] == pytest.approx(results[1]["objective"], rel=3e-6)


@pytest.mark.parametrize("limits", [False, True])
@pytest.mark.parametrize("assembly", ["single", "vectorized", "stepwise"])
def test_full_admittance_required_even_without_limits(limits, assembly):
    options = OPFOptions(sparsity_tol=1e-5, enforce_branch_limits=limits)
    with pytest.raises(ValueError, match="sparsity_tol == 0"):
        if assembly == "single":
            build_opf(case9(), formulation="socp", options=options)
        else:
            multistep(case9(), options=options, temporal_assembly=assembly)


def test_dc_loss_proxy_rejected_and_reactive_input_required():
    with pytest.raises(ValueError, match="loss_weight is DC-only"):
        build_opf(case9(), formulation="socp", options=OPFOptions(loss_weight=2))
    with pytest.raises(ValueError, match="requires df_Q"):
        build_opf_multistep(case9(), pd.DataFrame(np.zeros((1, 9))), T=1, formulation="socp")


@pytest.mark.parametrize("assembly", ["single", "vectorized", "stepwise"])
def test_unsolved_partial_and_failed_results_reuse_common_schema(assembly, monkeypatch):
    import cvxopf.socp_diagnostics as diagnostics

    kwargs = dict(storage=[StorageUnitIdeal(bus=5, apparent_power_rating=10, capacity=20, initial_soc=12)],
                  loads=[Load(bus=5, p_load_mw=30, device_id="load", shedding_cost_per_mwh=1000)])
    build = (build_opf(case9(), formulation="socp", **kwargs) if assembly == "single"
             else multistep(case9(), temporal_assembly=assembly, **kwargs))
    def forbidden(*args, **kwargs):
        pytest.fail("extraction must not solve, audit or recover")
    monkeypatch.setattr(diagnostics, "audit_socp_relaxation", forbidden)
    monkeypatch.setattr(diagnostics, "recover_socp_voltage", forbidden)
    monkeypatch.setattr(cp.Problem, "solve", forbidden)
    result = extract_results(build)
    assert result["w"] is None and result["b_q"] is None
    assert result["q_load_served"] is None
    assert np.isnan(result["objective"])
    assert not audit_socp_relaxation(build)["available"]
    variables = build.variables["w"]
    for variable in variables if isinstance(variables, list) else [variables]:
        variable.value = np.ones(variable.shape)
    partial = extract_results(build)
    assert partial["w"] is not None and partial["W_re"] is None
    assert partial["branch_p_from"] is None
    assert not audit_socp_relaxation(build)["available"]
    build.prob._status = "infeasible"
    failed = extract_results(build)
    assert failed.keys() == result.keys()
    assert not recover_socp_voltage(build)["available"]


def test_vectorized_graph_count_does_not_scale_by_interval():
    one = multistep(case9(), T=1)
    three = multistep(case9(), T=3)
    assert len(one.prob.constraints) == len(three.prob.constraints)
    assert len(one.prob.variables()) == len(three.prob.variables())
    assert three.prob.size_metrics.num_scalar_variables == 3 * one.prob.size_metrics.num_scalar_variables
