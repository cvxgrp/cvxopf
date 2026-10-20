"""Aggregation and presentation checks; no private input or optimizer calls."""

from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from experiments.pes2027 import plot_pes_results as figures
from experiments.pes2027.plot_pes_results import totals
from experiments.tracy_ac_rollout.run import no_optimization


def test_paper_package_resolves_notes_and_preserves_retained_evidence_root():
    paper = figures.ROOT / "experiments/pes2027"
    assert Path(figures.__file__).resolve().parent == paper
    assert figures.FIGURE_NOTES == paper / "figure_notes.md"
    assert figures.FIGURE_NOTES.is_file()
    assert figures.ROLLOUT == (
        figures.ROOT / "experiments/tracy_ac_rollout/results/rollout_001"
    )


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


def test_joint_markers_and_stems_preserve_raw_values(rendered):
    saved, bundle = rendered
    fig = saved["01_joint_dispatch"]
    assert not fig.texts  # No overall title, explanation or qualification footer.
    for j, key in enumerate(("device_cost", "ens_mwh", "curtailed_mwh")):
        ax = fig.axes[j]
        assert not ax.containers
        assert len(ax.lines) == 2 * 24 + 1
        for i, row in enumerate(bundle["joint"]):
            stem, marker = ax.lines[2 * i : 2 * i + 2]
            value = row[key] / (1000 if j == 2 else 1)
            np.testing.assert_array_equal(stem.get_xdata(), [0, value])
            np.testing.assert_array_equal(marker.get_xdata(), [value])
            assert ax.get_xlim()[0] < value < ax.get_xlim()[1]
            assert marker.get_marker() == figures.MARKERS[row["method"]]
            assert marker.get_color() == figures.COLORS[row["method"]]
            assert marker.get_markerfacecolor() == figures.COLORS[row["method"]]
            assert marker.get_markersize() == pytest.approx(4.5 * 0.85)
    assert all(ax.get_xscale() == "symlog" for ax in fig.axes[:2])
    assert fig.axes[2].get_xscale() == "linear"


def test_compact_layout_preserves_type_and_separates_labels(rendered):
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    saved, _ = rendered
    for name, fig in saved.items():
        height = {
            "03_04_ac_implementation": 5.4,
            "01_03_04_results": 8.2,
        }.get(name, 3.0)
        np.testing.assert_allclose(fig.get_size_inches(), [7.2, height])
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        renderer = canvas.get_renderer()
        legend_box = fig.legends[0].get_window_extent(renderer)
        for handle in fig.legends[0].legend_handles:
            assert handle.get_markersize() == pytest.approx(5 * 0.85)
        for ax in fig.axes:
            assert ax.xaxis.label.get_fontsize() == 8
            assert ax.title.get_fontsize() == 9
            assert not legend_box.overlaps(ax.title.get_window_extent(renderer))
            labels = [x for x in ax.get_yticklabels() if x.get_visible()]
            boxes = [x.get_window_extent(renderer) for x in labels]
            for left, right in zip(boxes, boxes[1:]):
                assert not left.overlaps(right)
            for label in [ax.xaxis.label, ax.title, *labels]:
                box = label.get_window_extent(renderer)
                assert box.x0 >= 0 and box.y0 >= 0
                assert box.x1 <= fig.bbox.x1 and box.y1 <= fig.bbox.y1


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
        for ax in (left, right):
            assert all(
                marker.get_markersize() == pytest.approx(4.5 * 0.85)
                for marker in ax.lines[1:-1:2]
            )
    assert not fixed.texts and not rollout.texts


def test_combined_implementation_uses_one_shared_key_and_identical_data(rendered):
    saved, _ = rendered
    combined = saved["03_04_ac_implementation"]
    assert len(combined.legends) == 1
    assert [label.get_text() for label in combined.legends[0].get_texts()] == [
        "copper plate",
        "network flow",
        "SOCP",
        "full AC signposts (rollout only)",
    ]
    assert len(combined.axes) == 6
    for index, stem in enumerate(("03_fixed_schedule_transfer", "04_ac_rollouts")):
        for source, target in zip(saved[stem].axes, combined.axes[index * 3 :][:3]):
            assert source.get_xlim() == target.get_xlim()
            assert source.get_ylim() == target.get_ylim()
            assert len(source.lines) == len(target.lines)
            for left, right in zip(source.lines, target.lines):
                np.testing.assert_array_equal(left.get_xdata(), right.get_xdata())
                np.testing.assert_array_equal(left.get_ydata(), right.get_ydata())
                assert left.get_marker() == right.get_marker()
                assert left.get_markersize() == right.get_markersize()
            assert [x.get_text() for x in source.texts] == [
                x.get_text() for x in target.texts
            ]


def test_full_page_results_have_one_legend_and_three_labeled_subfigures(rendered):
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    saved, _ = rendered
    combined = saved["01_03_04_results"]
    assert len(combined.legends) == 1
    assert [x.get_text() for x in combined.legends[0].get_texts()] == [
        "copper plate",
        "network flow",
        "SOCP",
        "full AC",
    ]
    assert [x.get_text() for x in combined.texts] == [
        "(a) Joint dispatch",
        "(b) Fixed-power AC transfer",
        "(c) Energy-signpost AC rollout",
    ]
    canvas = FigureCanvasAgg(combined)
    canvas.draw()
    renderer = canvas.get_renderer()
    legend_box = combined.legends[0].get_window_extent(renderer)
    for row, stem in enumerate(
        ("01_joint_dispatch", "03_fixed_schedule_transfer", "04_ac_rollouts")
    ):
        label_box = combined.texts[row].get_window_extent(renderer)
        assert not legend_box.overlaps(label_box)
        if row:
            for previous in combined.axes[(row - 1) * 3 :][:3]:
                assert not label_box.overlaps(
                    previous.xaxis.label.get_window_extent(renderer)
                )
        for left, right in zip(saved[stem].axes, combined.axes[row * 3 :][:3]):
            assert not label_box.overlaps(right.title.get_window_extent(renderer))
            assert left.get_xlim() == right.get_xlim()
            assert len(left.lines) == len(right.lines)
            for a, b in zip(left.lines, right.lines):
                np.testing.assert_array_equal(a.get_xdata(), b.get_xdata())
                np.testing.assert_array_equal(a.get_ydata(), b.get_ydata())


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
