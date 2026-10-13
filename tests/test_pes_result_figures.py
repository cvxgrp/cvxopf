"""Aggregation and presentation checks; no private input or optimizer calls."""

from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from experiments.case118_tracy_2021 import plot_pes_results as figures
from experiments.case118_tracy_2021.plot_pes_results import totals
from experiments.tracy_ac_rollout.run import no_optimization


def test_totals_exclude_only_network_proxy_and_integrate_once():
    result = dict(
        p_load_shed=[[1, 2], [3, 4]],
        curtailment=[[2, 4], [6, 8]],
        p_load=[[10, 20], [30, 40]],
        b=[[2, -3], [-4, 5]],
    )
    costs = dict(
        generator_cost=100, storage_cost=2, load_shedding_cost=30, dc_loss_cost=99
    )
    found = totals(result, costs, 0.5)
    assert found["device_cost"] == 132  # Already horizon-integrated costs.
    assert found["cost_components"]["dc_loss_cost"] == 99
    assert found["ens_mwh"] == 5
    assert found["curtailed_mwh"] == 10
    assert found["demand_mwh"] == 50
    assert found["storage_throughput_mwh"] == 7  # Sum absolute device powers, not net.
    assert costs["dc_loss_cost"] == 99


def test_totals_do_not_clip_small_signed_values():
    result = dict(
        p_load_shed=np.array([[-1e-12]]),
        curtailment=[[2e-12]],
        p_load=[[1]],
        b=[[-3e-12]],
    )
    found = totals(result, dict(generator_cost=1e-12), 1)
    assert found["ens_mwh"] == -1e-12
    assert found["curtailed_mwh"] == 2e-12
    assert found["device_cost"] == 1e-12


def test_collection_guard_forbids_optimizer_entry():
    import cvxpy as cp

    problem = cp.Problem(cp.Minimize(cp.square(cp.Variable())))
    with no_optimization(), pytest.raises(RuntimeError, match="must not invoke"):
        problem.solve()


@pytest.fixture
def rendered(monkeypatch, tmp_path):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    joint, fixed, rollouts = [], [], []
    for case in range(6):
        for method in figures.METHODS:
            joint.append(
                dict(
                    case=case,
                    method=method,
                    qualified=case == 5 and method != "ac",
                    device_cost=1e-12 if case == 0 else (case + 1) * 1e5,
                    ens_mwh=-1e-12 if case == 0 else case,
                    curtailed_mwh=(case + 1) * 1e3,
                )
            )
            row = dict(
                case=case,
                method=method,
                accepted=True,
                deltas=dict(
                    device_cost=(case - 2) * 1e3,
                    ens_mwh=case - 2,
                    curtailed_mwh=case - 2,
                ),
            )
            rollouts.append(row)
            if method != "ac":
                fixed.append(
                    dict(case=case, method=method, accepted=False)
                    if case == 4
                    else deepcopy(row)
                )
    bundle = dict(joint=joint, fixed=fixed, rollouts=rollouts)
    before = deepcopy(bundle)
    saved = {}
    monkeypatch.setattr(
        Figure,
        "savefig",
        lambda self, path, **kwargs: saved.setdefault(Path(path).stem, self),
    )
    with no_optimization():
        figures.render(bundle, tmp_path)
    assert bundle == before
    return saved, bundle


def test_joint_bars_preserve_raw_values_and_use_filled_colors(rendered):
    saved, bundle = rendered
    fig = saved["01_joint_dispatch"]
    assert not fig.texts  # No overall title, explanation or qualification footer.
    for j, key in enumerate(("device_cost", "ens_mwh", "curtailed_mwh")):
        bars = [p for container in fig.axes[j].containers for p in container]
        assert len(bars) == 24
        for bar, row in zip(bars, bundle["joint"]):
            assert bar.get_x() == 0
            assert bar.get_width() == row[key] / (1000 if j == 2 else 1)
            assert bar.get_facecolor()[3] == 1
        assert not fig.axes[j].lines  # Joint results are bars, not point markers.
    assert all(ax.get_xscale() == "symlog" for ax in fig.axes[:2])
    assert fig.axes[2].get_xscale() == "linear"


def test_difference_figures_share_axes_and_distinguish_missing_data(rendered):
    saved, _ = rendered
    fixed = saved["03_fixed_schedule_transfer"]
    rollout = saved["04_ac_rollouts"]
    for left, right in zip(fixed.axes, rollout.axes):
        assert left.get_xlim() == right.get_xlim()
        assert left.get_xscale() == right.get_xscale() == "symlog"
        assert [x.get_text() for x in left.texts] == ["n/a"]
        assert not right.texts
        # Two lines per accepted point, plus the zero reference; missing rows
        # must not become fabricated zero-valued donor observations.
        assert len(left.lines) == 2 * 15 + 1
        assert len(right.lines) == 2 * 24 + 1
    assert not fixed.texts and not rollout.texts


def test_render_only_copies_notes_and_preserves_extracted_snapshot(
    monkeypatch, tmp_path
):
    bundle = dict(sources={}, scope="synthetic", numerical_comparison="Pending")
    data = tmp_path / "figure_data.json"
    data.write_text(json.dumps(bundle))
    digest = figures.sha(data)
    monkeypatch.setattr(figures, "render", lambda *args: None)
    monkeypatch.setattr(
        figures, "collect", lambda: pytest.fail("render-only must not extract evidence")
    )
    monkeypatch.setattr(
        "sys.argv", ["plot_pes_results", "--output", str(tmp_path), "--render-only"]
    )
    figures.main()
    assert figures.sha(data) == digest
    notes = tmp_path / "figure_notes.md"
    assert notes.read_bytes() == figures.FIGURE_NOTES.read_bytes()
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["figure_notes_source_sha256"] == figures.sha(figures.FIGURE_NOTES)
    assert manifest["files"][notes.name] == figures.sha(notes)
    assert manifest["optimizer_calls"] == 0
