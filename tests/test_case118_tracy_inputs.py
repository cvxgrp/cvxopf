"""Stage A source/mapping/API checks. No numerical solves."""

from copy import deepcopy
import hashlib
import numpy as np
import pandas as pd
import pytest

from cvxopf import build_opf_multistep
from experiments.case118_tracy_2021.prepare import SOURCE, prepare, read_source
from experiments.case118_tracy_2021.audit import audit_inputs
from experiments.case118_tracy_2021.model_inputs import model_inputs


@pytest.fixture(scope="module")
def inputs():
    if not SOURCE.is_file():
        pytest.skip("Owner-provided Tracy CSV unavailable; see experiment README")
    return prepare()


@pytest.fixture
def synthetic_source():
    """Clone-ready parser input, never a replacement for study data."""
    times = pd.date_range("2021-01-01", periods=8760, freq="h", tz="Etc/GMT+8")
    return pd.DataFrame(
        {
            "time": times.astype(str),
            "9q9wtp_load": np.full(8760, 100.0),
            "9q9wtp_solar": np.full(8760, 20.0),
            "9q9wtp_wind": np.full(8760, 30.0),
            "9q9wtp_dist_solar": np.full(8760, 5.0),
        }
    )


def test_independent_source_reconstruction(inputs):
    assert audit_inputs(inputs)["passed"]
    assert inputs.source.shape == (8760, 4)
    assert inputs.alpha == pytest.approx(3.0548333420396085)


def test_repeated_construction(inputs):
    other = prepare()
    assert inputs.mapping == other.mapping
    np.testing.assert_array_equal(inputs.load_p_mw, other.load_p_mw)
    np.testing.assert_array_equal(inputs.nd_available_mw, other.nd_available_mw)


def test_mapping_and_pool_weights(inputs):
    p = inputs
    for category, sites in p.mapping["utility_sites_by_category"].items():
        assert len(sites) == 5
        assert set(sites) <= set(p.buses.loc[p.buses.category == category, "bus"])
    assert len(set(sum(p.mapping["utility_sites_by_category"].values(), []))) == 15
    for pool, group in p.batteries.groupby("pool"):
        if pool == "load_side":
            assert set(group.bus) <= set(
                p.buses.loc[p.buses.category == "load_only", "bus"]
            )
            weight = p.buses.set_index("bus").loc[group.bus, "source_p_mw"].to_numpy()
        else:
            rows = p.renewables.loc[p.renewables.channel != "dist_solar"]
            weight = (
                rows.groupby("bus").annual_available_mwh.sum().loc[group.bus].to_numpy()
            )
            categories = p.buses.set_index("bus").loc[group.bus, "source_p_mw"]
            assert (categories > 0).sum() == 2
            assert (categories == 0).sum() == 3
        np.testing.assert_allclose(group.pool_share, weight / weight.sum())


def test_source_substitution_rejected(tmp_path):
    path = tmp_path / "wrong.csv"
    path.write_text("time,load\n2021-01-01,1\n")
    with pytest.raises(ValueError, match="SHA-256"):
        read_source(path)


def test_missing_source_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_source(tmp_path / "absent.csv")


def test_valid_synthetic_source(tmp_path, monkeypatch, synthetic_source):
    from experiments.case118_tracy_2021 import prepare as module

    path = tmp_path / "synthetic.csv"
    synthetic_source.to_csv(path, index=False)
    monkeypatch.setattr(
        module, "SOURCE_SHA", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    result = module.read_source(path)
    assert result.shape == (8760, 4)
    assert result.index[0].isoformat() == "2021-01-01T00:00:00-08:00"
    assert result.index[-1].isoformat() == "2021-12-31T23:00:00-08:00"
    np.testing.assert_array_equal(
        result.to_numpy(),
        synthetic_source[
            ["9q9wtp_load", "9q9wtp_solar", "9q9wtp_wind", "9q9wtp_dist_solar"]
        ].to_numpy(),
    )


@pytest.mark.parametrize(
    "corruption", ["missing", "duplicate", "negative", "nonfinite"]
)
def test_source_semantic_validation(
    tmp_path, monkeypatch, corruption, synthetic_source
):
    from experiments.case118_tracy_2021 import prepare as module

    frame = synthetic_source.copy()
    if corruption == "missing":
        frame = frame.iloc[1:]
    elif corruption == "duplicate":
        frame.loc[1, "time"] = frame.loc[0, "time"]
    else:
        frame.loc[0, "9q9wtp_load"] = -1 if corruption == "negative" else np.nan
    path = tmp_path / "source.csv"
    frame.to_csv(path, index=False)
    # Isolate semantic checks from the separately tested source-identity gate.
    monkeypatch.setattr(
        module, "SOURCE_SHA", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError):
        module.read_source(path)


def test_device_operating_choices(inputs):
    kwargs = model_inputs(inputs, 0, 3)
    np.testing.assert_allclose(
        [d.apparent_power_rating for d in kwargs["nondispatchable"]],
        1.1 * inputs.nd_available_mw.max(axis=0),
    )
    assert all(
        d.initial_soc == d.capacity / 2 == d.terminal_soc for d in kwargs["storage"]
    )
    assert all(d.terminal_constraint == "equality" for d in kwargs["storage"])
    np.testing.assert_allclose(
        [d.apparent_power_rating for d in kwargs["storage"]],
        inputs.batteries.active_power_limit_mw,
    )
    np.testing.assert_array_equal(
        [g.q_max_mvar for g in kwargs["generators"]], inputs.generators.source_qmax_mvar
    )
    np.testing.assert_array_equal(
        [g.q_min_mvar for g in kwargs["generators"]], inputs.generators.source_qmin_mvar
    )


def test_frozen_seeded_sites(inputs):
    assert inputs.mapping["utility_sites_by_category"] == {
        "load_generation": [12, 49, 54, 59, 80],
        "generation_no_load": [10, 61, 69, 87, 111],
        "neither": [9, 37, 38, 63, 71],
    }
    assert inputs.batteries.bus.tolist() == [
        1,
        9,
        11,
        15,
        16,
        22,
        24,
        29,
        32,
        41,
        44,
        54,
        58,
        61,
        71,
        72,
        76,
        79,
        80,
        82,
        86,
        88,
        92,
        97,
        98,
        112,
        115,
    ]


@pytest.mark.parametrize("field", ["load_p_mw", "load_q_mvar", "nd_available_mw"])
def test_independent_audit_rejects_corruption(inputs, field):
    changed = deepcopy(inputs)
    getattr(changed, field)[0, 0] += 1
    with pytest.raises(AssertionError):
        audit_inputs(changed)


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_public_construction_without_solve(inputs, formulation):
    kwargs = model_inputs(inputs, 8424, 8427)
    # Public identity alignment must survive supplied frame-column reordering.
    kwargs["df_nd"] = kwargs["df_nd"].iloc[:, ::-1]
    kwargs["df_load_p"] = kwargs["df_load_p"].iloc[:, ::-1]
    kwargs["df_load_q"] = kwargs["df_load_q"].iloc[:, ::-1]
    build = build_opf_multistep(**kwargs, formulation=formulation)
    assert build.prob.status is None
    assert build.data["ns"] == 27
    assert build.data["nnd"] == len(inputs.renewables)
    np.testing.assert_allclose(
        build.expressions["p_load"].value.T, inputs.load_p_mw[8424:8427]
    )
    np.testing.assert_allclose(
        build.expressions["q_load"].value.T, inputs.load_q_mvar[8424:8427]
    )
    np.testing.assert_allclose(
        build.data["nd_available"], inputs.nd_available_mw[8424:8427]
    )
    np.testing.assert_allclose(
        build.data["storage_capacity"], inputs.batteries.capacity_mwh
    )
    assert len(kwargs["loads"]) == 99
    assert all(d.shedding_cost_per_mwh == 20763.594 for d in kwargs["loads"])
    assert all(d.max_shed_fraction == 1 for d in kwargs["loads"])
    assert all(d.aging_weight == 0.01 for d in kwargs["storage"])


def test_fixed_load_comparison(inputs):
    assert all(
        d.shedding_cost_per_mwh is None
        for d in model_inputs(inputs, 0, 1, shedding=False)["loads"]
    )
