"""Small calendar/reactivity-support tests without touching the live study."""

from datetime import datetime, timedelta, timezone
import unittest
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch


if importlib.util.find_spec("matplotlib") is None:
    raise unittest.SkipTest("Dashboard tests require the notebook extra (matplotlib)")

import matplotlib.pyplot as plt
import numpy as np

from experiments.case118_annual_hierarchy.analysis import dashboard_support as ds


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2025, 1, 1, tzinfo=timezone.utc)  # Wednesday

    def row(self, index, value):
        return dict(iteration=index, timestamp=(self.start + timedelta(hours=index)).isoformat(),
                    **{key: value + n for n, (key, _, _) in enumerate(ds.METRICS)})

    def test_calendar_positions_and_padding(self):
        rows = [self.row(i, i) for i in range(8760)]
        matrix, tensor, monday = ds.fold_calendar(rows, 8760, self.start)
        self.assertEqual(matrix.shape, (3, 24, 365))
        self.assertEqual(tensor.shape, (3, 24, 53, 7))
        self.assertEqual(monday.isoformat(), "2024-12-30T00:00:00+00:00")
        for day in range(365):
            week, weekday = divmod(day + 2, 7)
            np.testing.assert_array_equal(matrix[:, :, day], tensor[:, :, week, weekday])
        self.assertTrue(np.isnan(tensor[:, :, 0, :2]).all())
        self.assertTrue(np.isnan(tensor[:, :, -1, 3:]).all())
        self.assertEqual(tensor[0, 0, 1, 0], 5*24)  # Monday January 6

    def test_gaps_do_not_become_zeros(self):
        matrix, tensor, _ = ds.fold_calendar([self.row(0, 0), self.row(8759, 2)], 8760, self.start)
        self.assertEqual(matrix[0, 0, 0], 0)
        self.assertTrue(np.isnan(matrix[0, 1, 0]))
        self.assertEqual(np.isfinite(tensor).sum(), 6)

    def test_bad_coordinates_and_values_rejected(self):
        good = self.row(0, 0)
        cases = [[good, good], [self.row(8760, 0)], [self.row(-1, 0)],
                 [dict(good, timestamp=self.row(1, 0)["timestamp"])],
                 [dict(good, battery_l1_mw=float("nan"))],
                 [dict(good, battery_l1_mw=-1)]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                ds.fold_calendar(rows, 8760, self.start)

    def test_non_day_horizon_rejected(self):
        with self.assertRaises(ValueError):
            ds.fold_calendar([], 25, self.start)

    def test_weekday_scales_match_matrix_without_mutation(self):
        matrix, tensor, monday = ds.fold_calendar([self.row(0, 10), self.row(120, 20)], 8760, self.start)
        original = matrix.copy()
        data = dict(matrix=matrix, tensor=tensor, monday=monday, start=self.start,
                    report=dict(completed=2))
        figures = [ds.plot_heatmaps(data, day) for day in [None, *range(7)]]
        for fig in figures:
            for index, ax in enumerate([a for a in fig.axes if a.images]):
                self.assertEqual(ax.images[0].get_clim(), (0, 20 + index))
            plt.close(fig)
        np.testing.assert_array_equal(original, matrix)


class FrozenSnapshotTests(unittest.TestCase):
    def test_complete_calendar_and_aligned_dc_features(self):
        from experiments.case118_annual_hierarchy.analysis.dashboard_stress import load_stress_data
        data = ds.load_final_snapshot()
        self.assertEqual(data["report"]["completed"], 8760)
        self.assertTrue(np.isfinite(data["matrix"]).all())
        stress = load_stress_data(data)
        self.assertEqual(len(stress["frame"]), 8760)
        self.assertEqual(stress["frame"].index.tolist(), list(range(8760)))


class ReproductionOutputTests(unittest.TestCase):
    def test_stress_cli_with_no_outputs_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            analysis = root / "experiments/case118_annual_hierarchy/analysis"
            analysis.mkdir(parents=True)
            shutil.copy2(ds.HERE / "analyze_stress_correlations.py", analysis)
            for relative in ("final_snapshot/dispatch", "dc_features"):
                shutil.copytree(ds.HERE / "artifacts" / relative,
                                analysis / "artifacts" / relative)
            result = subprocess.run(
                [sys.executable, str(analysis / "analyze_stress_correlations.py")],
                cwd=root, capture_output=True, text=True,
                env=dict(os.environ, MPLBACKEND="Agg"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs = list((root / "experiments/case118_annual_hierarchy/results/reproductions").glob("stress_correlations_*"))
            self.assertEqual(len(outputs), 1)
            self.assertTrue(list(outputs[0].glob("*.json")))
            self.assertTrue(list(outputs[0].glob("*.png")))

    def test_completion_wrapper_creates_parents_but_refuses_existing_output(self):
        class CollectionReached(Exception):
            pass

        source = ds.HERE / "collectors/refresh_completion_band.py"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "new-parent/completion"
            spec = importlib.util.spec_from_file_location("completion_wrapper_test", source)
            def collect(path):
                self.assertEqual(Path(path).name, "collect_completion.py")
                self.assertTrue(output.is_dir())
                raise CollectionReached
            with patch.dict(os.environ, {"S5_COMPLETION_OUT": str(output)}), \
                    patch("runpy.run_path", side_effect=collect):
                with self.assertRaises(CollectionReached):
                    spec.loader.exec_module(importlib.util.module_from_spec(spec))
                with self.assertRaises(FileExistsError):
                    spec.loader.exec_module(importlib.util.module_from_spec(spec))


if __name__ == "__main__":
    unittest.main()
