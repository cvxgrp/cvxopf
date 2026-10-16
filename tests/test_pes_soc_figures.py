"""Boundary-indexed, capacity-weighted SoC presentation; no optimizer calls."""

import numpy as np
import pytest

from experiments.case118_tracy_2021.e3_results import HERE
from experiments.pes2027 import plot_pes_soc
from experiments.pes2027.plot_pes_soc import normalized_soc, render


def test_paper_package_preserves_source_relative_identities():
    assert plot_pes_soc.HERE == HERE
    assert HERE.name == "case118_tracy_2021"


def test_fleet_total_uses_capacity_weighting_and_keeps_initial_boundary():
    energy = np.array([[1, 9], [2, 0], [-1e-12, 4.5]])
    individual, fleet = normalized_soc(energy, [2, 18])
    np.testing.assert_allclose(individual, energy / [2, 18])
    np.testing.assert_allclose(fleet, energy.sum(axis=1) / 20)
    assert fleet[1] == 0.1  # Not the unweighted mean of the two device fractions.
    assert individual.shape == (3, 2) and individual[-1, 0] < 0


@pytest.mark.parametrize("capacity", ([0, 18], [2], [2, np.nan], [-2, 18]))
def test_invalid_capacity_denominators_are_rejected(capacity):
    with pytest.raises(ValueError):
        normalized_soc([[1, 9], [2, 0]], capacity)


def test_stacked_ac_and_copper_plate_show_every_boundary(monkeypatch, tmp_path):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    monkeypatch.setattr(Figure, "savefig", lambda *args, **kwargs: None)
    energies = [[1, 9], [2, 0], [1, 9]]
    case = dict(
        start=10,
        stop=12,
        requested_load_mw=[1000, 2000],
        available_renewables_mw=[3000, 4000],
        arms=[
            dict(method=m, boundary_soc_mwh=energies) for m in ("ac", "singlenode_dc")
        ],
    )
    fig = render(case, [2, 18], tmp_path / "soc")
    assert len(fig.axes) == 3 and len(fig.legends) == 1
    assert [ax.get_title() for ax in fig.axes[:2]] == [
        "(b) full AC",
        "(c) copper plate",
    ]
    assert [text.get_text() for text in fig.legends[0].texts] == [
        "Demand",
        "Nondispatchable availability",
        "Fleet total",
        "Individual batteries",
    ]
    for ax in fig.axes[:2]:
        assert len(ax.lines) == 3
        fleet = ax.lines[-1]
        assert fleet.get_color() == "black"
        assert fleet.get_linewidth() > ax.lines[0].get_linewidth()
        np.testing.assert_array_equal(fleet.get_xdata(), [0, 1, 2])
        np.testing.assert_allclose(fleet.get_ydata(), [0.5, 0.1, 0.5])
    context = fig.axes[2]
    assert context.get_ylabel() == "Power (GW)"
    for patch, expected in zip(context.patches, ([1, 2], [3, 4])):
        np.testing.assert_array_equal(patch.get_data().values, expected)
        np.testing.assert_array_equal(patch.get_data().edges, [0, 1, 2])
    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    renderer = canvas.get_renderer()
    legend = fig.legends[0].get_window_extent(renderer)
    assert legend.x0 >= 0 and legend.x1 <= fig.bbox.x1
    assert legend.y0 >= 0 and legend.y1 <= fig.bbox.y1
    for ax in fig.axes:
        assert not legend.overlaps(ax.title.get_window_extent(renderer))
