"""M17-S0 characterization of storage state and result contracts."""

from contextlib import nullcontext
import warnings

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import StorageUnitIdeal, build_opf, build_opf_multistep
from cvxopf.results import extract_results
from cvxopf.testcases import case9


FORMULATIONS = ("ac", "lossy_dc", "singlenode_dc")


def _storage(**kwargs) -> StorageUnitIdeal:
    values = {
        "bus": 5,
        "apparent_power_rating": 25.0,
        "capacity": 100.0,
        "initial_soc": 50.0,
        "aging_weight": 0.01,
    }
    values.update(kwargs)
    return StorageUnitIdeal(**values)


def _load_frames(T: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    case = case9()
    p = pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1)))
    q = pd.DataFrame(np.tile(case["bus"][:, 3], (T, 1)))
    return p, q


def _multistep_build(
    formulation: str,
    T: int,
    storage: StorageUnitIdeal,
    *,
    delta: float = 0.5,
):
    p, q = _load_frames(T)
    warning_context = (
        pytest.warns(UserWarning, match="retained as reactive load")
        if formulation != "ac"
        else nullcontext()
    )
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Storage apparent_power_rating is applied as a real power",
            category=UserWarning,
        )
        with warning_context:
            return build_opf_multistep(
                case9(),
                p,
                q,
                T=T,
                formulation=formulation,
                storage=[storage],
                delta=delta,
            )


@pytest.mark.parametrize("formulation", FORMULATIONS)
def test_single_and_multistep_t1_storage_schemas_remain_distinct(formulation):
    delta = 0.5
    unit = _storage(
        terminal_soc=45.0,
        terminal_constraint="equality",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        single = build_opf(
            case9(), formulation=formulation, storage=[unit], delta=delta
        )
        multi = _multistep_build(formulation, 1, unit, delta=delta)
        single.solve()
        multi.solve()

    single_result = extract_results(single)
    multi_result = extract_results(multi)

    assert "T" not in single.data
    assert single.variables["b"].shape == (1,)
    assert single.variables["soc"].shape == (1,)
    assert multi.data["T"] == 1
    assert len(multi.variables["b"]) == 1
    assert len(multi.variables["soc"]) == 1
    assert multi.variables["b"][0].shape == (1,)
    assert multi.variables["soc"][0].shape == (1,)

    assert single_result["b"].shape == (1,)
    assert single_result["soc"].shape == (1,)
    assert multi_result["b"].shape == (1, 1)
    assert multi_result["soc"].shape == (1, 1)

    np.testing.assert_allclose(
        single_result["soc"],
        single.data["storage_initial_soc"] - delta * single_result["b"],
        atol=2e-4,
    )
    np.testing.assert_allclose(
        multi_result["soc"][0],
        multi.data["storage_initial_soc"] - delta * multi_result["b"][0],
        atol=2e-4,
    )
    np.testing.assert_allclose(single_result["soc"], [45.0], atol=2e-4)
    np.testing.assert_allclose(multi_result["soc"][-1], [45.0], atol=2e-4)
    np.testing.assert_allclose(
        single_result["storage_terminal_deviation"], [0.0], atol=2e-4
    )
    np.testing.assert_allclose(
        multi_result["storage_terminal_deviation"], [0.0], atol=2e-4
    )

    for build in (single, multi):
        np.testing.assert_array_equal(build.data["storage_initial_soc"], [50.0])
        # Pre-P1 characterization: stable storage identity is not yet published.
        assert "storage_device_ids" not in build.data


@pytest.mark.parametrize("formulation", FORMULATIONS)
def test_multistep_soc_is_post_step_and_obeys_ideal_recurrence(formulation):
    delta = 0.5
    unit = _storage(
        terminal_soc=50.0,
        terminal_constraint="equality",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = _multistep_build(formulation, 3, unit, delta=delta)
        build.solve()
    result = extract_results(build)

    assert result["status"] in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}
    assert result["b"].shape == (3, 1)
    assert result["soc"].shape == (3, 1)
    assert np.all(np.isfinite(result["b"]))
    assert np.all(np.isfinite(result["soc"]))

    conceptual_boundaries = np.vstack(
        [build.data["storage_initial_soc"], result["soc"]]
    )
    assert conceptual_boundaries.shape == (4, 1)
    np.testing.assert_allclose(
        conceptual_boundaries[1:],
        conceptual_boundaries[:-1] - delta * result["b"],
        atol=2e-4,
    )
    np.testing.assert_allclose(conceptual_boundaries[-1], [50.0], atol=2e-4)


def test_replanned_local_boundary_maps_to_post_step_result_index():
    """At global k=1, local boundary 2 is result SoC index 1, not 2."""
    frozen = _multistep_build(
        "lossy_dc",
        3,
        _storage(terminal_soc=50.0, terminal_constraint="equality"),
    )
    frozen.solve()
    frozen_result = extract_results(frozen)
    realized_e1 = float(frozen_result["soc"][0, 0])

    replanned = _multistep_build(
        "lossy_dc",
        2,
        _storage(
            initial_soc=realized_e1,
            terminal_soc=50.0,
            terminal_constraint="equality",
        ),
    )
    replanned.solve()
    replanned_result = extract_results(replanned)
    local_boundaries = np.vstack(
        [replanned.data["storage_initial_soc"], replanned_result["soc"]]
    )

    assert local_boundaries.shape == (3, 1)
    np.testing.assert_allclose(
        local_boundaries[2], replanned_result["soc"][1], atol=1e-8
    )
    np.testing.assert_allclose(local_boundaries[2], [50.0], atol=2e-4)


@pytest.mark.parametrize("formulation", FORMULATIONS)
def test_unsolved_build_has_schema_but_no_usable_storage_primal(formulation):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = _multistep_build(formulation, 1, _storage())
    result = extract_results(build)

    assert result["status"] is None
    assert np.isnan(result["objective"])
    assert result["b"] is None
    assert result["soc"] is None
    assert np.isnan(result["storage_cost"])
    assert result["p_load"] is not None
    assert result["q_load"] is not None
