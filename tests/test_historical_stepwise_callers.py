"""Keep historical list-dependent diagnostics on their original graph layout."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from cvxopf import NondispatchableUnit, StorageUnitIdeal
from cvxopf.testcases import case9
from experiments.battery_terminal import problem_setup
from experiments.battery_terminal.subset_study import _assign_dc_restriction
from experiments.branch_limits_s0 import s0_characterization


def _battery_build(builder, horizon):
    case = case9()
    prepared = SimpleNamespace(
        nondispatchable=[NondispatchableUnit(1, 10.0, 10.0, device_id="nd")],
        generators=None,
    )
    scenario = SimpleNamespace(
        df_P=pd.DataFrame(np.tile(case["bus"][:, 2], (horizon, 1))),
        df_Q=pd.DataFrame(np.tile(case["bus"][:, 3], (horizon, 1))),
        df_nd=pd.DataFrame({"nd": [10.0] * horizon}),
    )
    return builder(prepared, scenario, StorageUnitIdeal(1, 150.0, 1000.0, 500.0))


@pytest.mark.parametrize("builder", [
    problem_setup.build_lossy_dc, problem_setup.build_singlenode_dc,
    problem_setup.build_ac,
])
def test_historical_terminal_index_selects_final_post_step_state(builder):
    build = _battery_build(builder, 2)
    build.variables["soc"][0].value = [600.0]
    build.variables["soc"][1].value = [700.0]
    # This is the exact terminal expression used by the adequacy diagnostic.
    assert build.variables["soc"][-1][0].value == 700.0
    assert build.data["storage_initial_soc"][0] == 500.0


def test_historical_subset_restriction_assigns_the_selected_time_interval():
    reference = _battery_build(problem_setup.build_lossy_dc, 2)
    subsection = _battery_build(problem_setup.build_lossy_dc, 1)
    for name in ("p_flows", "Pg", "b", "soc", "p_nd"):
        for step, variable in enumerate(reference.variables[name]):
            variable.value = variable.project(np.full(variable.shape, step + 1.0))
    _assign_dc_restriction(reference, subsection, 1, 2)
    for name in ("p_flows", "Pg", "b", "soc", "p_nd"):
        np.testing.assert_array_equal(
            subsection.variables[name][0].value, reference.variables[name][1].value,
        )


@pytest.mark.parametrize("lifted", [False, True])
def test_historical_branch_characterization_constructs_each_time_step(monkeypatch, lifted):
    # Exercise its actual graph assembly without launching a diagnostic solve.
    monkeypatch.setattr(s0_characterization, "_solve_problem", lambda problem: ("not_solved", 0.0))
    report = s0_characterization.characterize_multistep(lifted=lifted)
    assert report["T"] == 3
    assert report["status"] == "not_solved"
