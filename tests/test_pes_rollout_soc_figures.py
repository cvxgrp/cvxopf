"""Realized rollout energy uses first actions, with correct signs and duration."""

import numpy as np
import pytest

from experiments.pes2027.plot_pes_rollout_soc import reconstruct_boundaries


def test_reconstruct_executed_energy_not_forecast():
    windows = [
        dict(
            initial_soc_mwh=[5, 4],
            post_step_soc_mwh=[4, 6],
            executed_interval=dict(result=dict(b=[[2, -4]], soc=[[999, 999]])),
        ),
        dict(
            initial_soc_mwh=[4, 6],
            post_step_soc_mwh=[5, 5],
            executed_interval=dict(result=dict(b=[[-2, 2]], soc=[[999, 999]])),
        ),
    ]
    np.testing.assert_array_equal(
        reconstruct_boundaries(windows, [5, 4], 0.5), [[5, 4], [4, 6], [5, 5]]
    )
    windows[1]["initial_soc_mwh"] = [4, 7]
    with pytest.raises(AssertionError):
        reconstruct_boundaries(windows, [5, 4], 0.5)
