"""Permanent E2 matched-model regression gates; no experiment/private inputs."""

from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from tests.socp_matched import (
    AC_SETTINGS, SOCP_SETTINGS, PAIR_NAMES, build_pair, check_pair,
    fixture, objective_components, validate_matching,
)


@pytest.mark.parametrize("name", PAIR_NAMES)
def test_matching_accepts_identical_full_admittance_inputs(name):
    spec = fixture(name)
    assert len(validate_matching(spec, deepcopy(spec))) == 64


@pytest.mark.parametrize("limits", [False, True])
@pytest.mark.parametrize("arm", ["ac", "socp"])
def test_matched_comparison_rejects_thresholded_references_before_build(limits, arm, monkeypatch):
    import tests.socp_matched as helpers

    def forbidden(*args, **kwargs):
        pytest.fail("must reject before building either model")
    monkeypatch.setattr(helpers, "build_opf", forbidden)
    ac, socp = fixture("case9"), fixture("case9")
    for spec in (ac, socp):
        spec["kwargs"]["options"] = replace(spec["kwargs"]["options"], enforce_branch_limits=limits)
    changed = ac if arm == "ac" else socp
    changed["kwargs"]["options"] = replace(changed["kwargs"]["options"], sparsity_tol=1e-5)
    with pytest.raises(ValueError, match="sparsity_tol == 0"):
        build_pair(ac, socp)


@pytest.mark.parametrize("mutation", [
    "base", "branch", "gen", "cost", "voltage_policy", "rating_policy", "load_p", "load_q",
    "load_permission", "load_price", "storage_initial", "storage_terminal", "storage_cost",
    "storage_identity", "availability", "nd_capability", "hvdc_loss", "hvdc_cost", "T", "delta",
])
def test_matching_rejects_model_input_drift(mutation):
    ac, socp = fixture("mixed_vectorized"), fixture("mixed_vectorized")
    kwargs = socp["kwargs"]
    if mutation == "base":
        socp["case"]["baseMVA"] += 1
    elif mutation in ("branch", "gen", "cost"):
        table, column = {"branch": ("branch", 2), "gen": ("gen", 8), "cost": ("gencost", 4)}[mutation]
        socp["case"][table][0, column] += .01
    elif mutation in ("voltage_policy", "rating_policy"):
        field = "enforce_vset" if mutation == "voltage_policy" else "enforce_branch_limits"
        kwargs["options"] = replace(kwargs["options"], **{field: False})
    elif mutation in ("load_p", "load_q", "availability"):
        key = {"load_p": "df_load_p", "load_q": "df_load_q", "availability": "df_nd"}[mutation]
        kwargs[key].iloc[1, 0] += 1
    elif mutation in ("load_permission", "load_price"):
        field = "max_shed_fraction" if mutation == "load_permission" else "shedding_cost_per_mwh"
        kwargs["loads"][0] = replace(kwargs["loads"][0], **{field: .5})
    elif mutation.startswith("storage_"):
        field, value = {
            "storage_initial": ("initial_soc", 5), "storage_terminal": ("terminal_soc", 5),
            "storage_cost": ("aging_weight", .2), "storage_identity": ("device_id", "other"),
        }[mutation]
        kwargs["storage"][0] = replace(kwargs["storage"][0], **{field: value})
    elif mutation == "nd_capability":
        kwargs["nondispatchable"][0] = replace(kwargs["nondispatchable"][0], apparent_power_rating=9)
    elif mutation.startswith("hvdc_"):
        field, value = ("loss_percent", 4) if mutation == "hvdc_loss" else ("cost_coeffs", (0, 1, 0))
        kwargs["hvdc"][0] = replace(kwargs["hvdc"][0], **{field: value})
    else:
        kwargs[mutation] += 1
    with pytest.raises(ValueError, match="do not match"):
        validate_matching(ac, socp)


def test_matching_rejects_even_identical_unsupported_coupling():
    ac = fixture("mixed_vectorized")
    ac["kwargs"]["coupling_constraints"] = []
    with pytest.raises(ValueError, match="Caller coupling"):
        validate_matching(ac, deepcopy(ac))


def test_numeric_objective_integrates_rates_but_not_terminal_cost():
    spec = fixture("mixed_vectorized")
    result = dict(Pg=np.array([[10.], [20.], [30.]]),
        b=np.array([[2., 1.], [-2., -1.], [0., 0.]]),
        soc=np.array([[5., 1.5], [6., 2.], [6., 2.]]),
        p_hvdc_in=np.array([[-1.], [-2.], [-1.]]),
        load_shed_fraction=np.array([[0.], [.5], [0.]]))
    costs = objective_components(spec, result)
    assert costs == pytest.approx(dict(generator_cost=37., storage_cost=.3,
        storage_terminal_cost=7., hvdc_cost=.53, load_shedding_cost=22500.))
    changed = deepcopy(spec)
    changed["kwargs"]["delta"] = .25
    other = objective_components(changed, result)
    assert other["storage_terminal_cost"] == costs["storage_terminal_cost"]
    for key in costs.keys()-{"storage_terminal_cost"}:
        assert other[key] == pytest.approx(.5*costs[key])


@pytest.fixture(scope="module")
def solved_pairs():
    # One solve per arm; later assertions reuse it instead of duplicating work.
    records = {}
    for name in PAIR_NAMES:
        spec = fixture(name)
        ac, socp, _ = build_pair(spec)
        ac.solve(**AC_SETTINGS)
        socp.solve(**SOCP_SETTINGS)
        records[name] = check_pair(spec, ac, socp)
    return records


@pytest.mark.parametrize("name", PAIR_NAMES)
def test_matched_ac_lifts_with_complete_devices_and_identical_cost(solved_pairs, name):
    record = solved_pairs[name]
    assert record["containment"]["relaxation_audit"]["feasible"]
    assert record["socp_audit"]["feasible"]
    assert record["dual_certificate"] is None


def test_mixed_temporal_assemblies_agree_in_objective_not_nonunique_dispatch(solved_pairs):
    vector = solved_pairs["mixed_vectorized"]["socp_result"]["objective"]
    stepwise = solved_pairs["mixed_stepwise"]["socp_result"]["objective"]
    assert vector == pytest.approx(stepwise, rel=2e-6, abs=2e-5)
