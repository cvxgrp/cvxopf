"""Example figures reuse results and retain sign, boundary, and warning semantics."""

import importlib.util
import builtins
from pathlib import Path

import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]


def load_module(relative):
    spec = importlib.util.spec_from_file_location("example_support", ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plot_uses_boundaries_and_signed_power_with_failure_labels():
    example = load_module("examples/case9_storage_efficiency_96h.py")
    charge, discharge, soc = np.array([2., 0.]), np.array([0., 1.]), np.array([5., 4.])
    trajectories = {
        "Ideal": (charge, discharge, soc, False, "optimal"),
        "Li-ion-like": (charge, discharge, soc, True, "optimal_inaccurate"),
        "Inefficient": None,
    }
    fig = example.plot_trajectories(np.array([3., 4.]), np.array([0., 2.]),
                                     trajectories, 3., 0.5)
    try:
        np.testing.assert_array_equal(fig.axes[1].lines[0].get_xdata(), [0, .5, 1])
        np.testing.assert_array_equal(fig.axes[1].lines[0].get_ydata(), [3, 5, 4])
        np.testing.assert_array_equal(fig.axes[2].patches[0].get_data().values, charge)
        np.testing.assert_array_equal(fig.axes[2].patches[1].get_data().values, -discharge)
        assert "simultaneous operation detected" in fig.axes[3].get_title(loc="left")
        assert "optimal_inaccurate" in fig.axes[3].get_title(loc="left")
        assert "no usable solution" in fig.axes[4].get_title(loc="left")
    finally:
        plt.close(fig)


def test_readme_embeds_fresh_figure_from_single_invocation(tmp_path, monkeypatch):
    generator = load_module("scripts/generate_examples_readme.py")
    script = tmp_path / "demo.py"
    script.write_text('"""Demo."""\n')
    monkeypatch.setattr(generator, "EXAMPLE_GROUPS", {"Storage": ["demo.py"]})
    monkeypatch.setattr(generator, "EXAMPLE_FIGURES", {"demo.py": "assets/demo.png"})
    calls = []

    def run(filepath, arguments):
        calls.append((filepath, arguments))
        assert arguments[0] == "--save-plot"
        Path(arguments[1]).write_bytes(b"fresh figure")
        return "summary"

    monkeypatch.setattr(generator, "_run_script", run)
    generator.generate_readme(tmp_path, tmp_path / "README.md")
    assert len(calls) == 1
    assert (tmp_path / "assets/demo.png").read_bytes() == b"fresh figure"
    assert "(assets/demo.png)" in (tmp_path / "README.md").read_text()


def test_missing_new_figure_does_not_reuse_old_asset(tmp_path, monkeypatch):
    generator = load_module("scripts/generate_examples_readme.py")
    destination = tmp_path / "old.png"
    destination.write_bytes(b"old figure")
    monkeypatch.setattr(generator, "_run_script", lambda *_: "summary without figure")
    with pytest.raises(RuntimeError, match="Figure generation failed"):
        generator.run_script(tmp_path / "demo.py", figure_path=destination)
    assert destination.read_bytes() == b"old figure"


@pytest.mark.parametrize("save", [False, True])
def test_example_solves_once_per_case_and_plotting_is_optional(tmp_path, monkeypatch, save):
    example = load_module("examples/case9_storage_efficiency_96h.py")
    original_build = example.build_opf_multistep
    builds = []

    def build(*args, **kwargs):
        result = original_build(*args, **kwargs)
        builds.append(result)
        return result

    monkeypatch.setattr(example, "build_opf_multistep", build)
    if not save:
        original_import = builtins.__import__

        def no_matplotlib(name, *args, **kwargs):
            if name.startswith("matplotlib"):
                raise AssertionError("Default example must not import Matplotlib")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_matplotlib)
    destination = tmp_path / "figure.png"
    example.main(save_plot=destination if save else None)
    assert len(builds) == 3
    assert all(build.prob.status == "optimal" for build in builds)
    if save:
        assert destination.read_bytes().startswith(b"\x89PNG")
    else:
        assert not destination.exists()
