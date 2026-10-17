"""Paper network rendering preserves topology and asset marker quantities."""

import numpy as np
import pandas as pd

from experiments.pes2027.plot_pes_network import render


def test_configured_network_labels_and_marker_scales(monkeypatch, tmp_path):
    from matplotlib.figure import Figure

    monkeypatch.setattr(Figure, "savefig", lambda *args, **kwargs: None)
    branches = np.zeros((3, 11))
    branches[:, :2] = [[1, 2], [1, 2], [2, 3]]
    branches[:, 10] = [1, 1, 0]
    fig = render(
        {"branch": branches},
        pd.DataFrame({"bus": [1, 2, 3], "source_p_mw": [10, 20, 0]}),
        pd.DataFrame({"bus": [2], "active_power_limit_mw": [50]}),
        pd.DataFrame(
            {
                "bus": [1, 1, 3],
                "channel": ["solar", "wind", "dist_solar"],
                "peak_available_mw": [10, 20, 40],
            }
        ),
        tmp_path / "network",
    )
    ax = fig.axes[0]
    assert [t.get_text() for t in ax.get_legend().texts] == [
        "Load: source demand (MW)",
        "Utility solar/wind: sum of channel peaks",
        "Battery: active MW rating",
    ]
    # One drawn edge for two active parallel circuits; inactive branch omitted.
    assert len(ax.collections[0].get_segments()) == 1
    np.testing.assert_allclose(ax.collections[2].get_sizes(), [10 * 0.805, 20 * 0.805])
    np.testing.assert_allclose(ax.collections[3].get_sizes(), [30 * 0.12])
    np.testing.assert_allclose(ax.collections[4].get_sizes(), [50 * 0.4])
