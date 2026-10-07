"""No-solve fixed-load ablation and audit projection checks."""

from dataclasses import asdict
import numpy as np
import pytest

from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import no_shedding as n
from cvxopf import extract_results


def test_only_load_feature_is_removed_and_graph_contains_no_shedding():
    source = d.smoke_inputs()
    fixed = n.fixed_inputs(source)
    for k in source:
        if k != "loads":
            assert fixed[k] is source[k]
    for before, after in zip(source["loads"], fixed["loads"], strict=True):
        assert asdict(after) == asdict(before) | {"shedding_cost_per_mwh": None}
    build, _, _ = n.build_variant(fixed, "cones")
    raw = extract_results(build)
    assert "p_load_shed" not in raw
    assert "load_shedding_cost" not in raw
    assert build.prob.is_dcp()


def test_projection_keeps_actual_served_values_and_rejects_overwrite():
    served = np.array([[5.0]])
    result = {"p_load_served": served}
    projected = n.zero_shedding_projection(result, 1, 1)
    assert projected["p_load_served"] is served
    assert projected["energy_not_served"] == 0
    assert set(result) == {"p_load_served"}
    with pytest.raises(ValueError, match="shedding outputs"):
        n.zero_shedding_projection(projected, 1, 1)
