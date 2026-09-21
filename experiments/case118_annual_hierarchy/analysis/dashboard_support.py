"""Frozen S5 dashboard loading and calendar folding. No optimization or writes."""

import csv
from datetime import datetime, timedelta
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
FROZEN_SNAPSHOT = HERE / "artifacts/final_snapshot"
METRICS = (
    ("generator_l1_mw", "Generator redispatch magnitude", "MW"),
    ("battery_l1_mw", "Battery-power change magnitude", "MW"),
    ("soc_end_l1_mwh", "End-of-hour SoC schedule divergence", "MWh"),
)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def read_json(path):
    return json.loads(Path(path).read_text())


def load_final_snapshot():
    """Load the promoted completed toy snapshot; never collect or solve."""
    return load_snapshot(FROZEN_SNAPSHOT)


def fold_calendar(rows, horizon, start):
    """Return metric×hour×day and metric×hour×week×weekday, without averaging.

    Weeks begin Monday. Padding before January 1 and after December 31 remains
    NaN, as do all unexecuted intervals. Calendar UTC is synthetic study time,
    independent of the laptop's wall-time zone and DST.
    """
    if horizon % 24 or start.hour or start.minute or start.second:
        raise ValueError("Calendar folding requires whole days starting at midnight")
    days = horizon // 24
    matrix = np.full((len(METRICS), 24, days), np.nan)
    seen = set()
    for row in rows:
        index = int(row["iteration"])
        if index in seen or not 0 <= index < horizon:
            raise ValueError("Duplicate or out-of-horizon interval")
        seen.add(index)
        if datetime.fromisoformat(row["timestamp"]) != start + timedelta(hours=index):
            raise ValueError("Timestamp and global interval disagree")
        values = np.array([float(row[key]) for key, _, _ in METRICS])
        if not np.isfinite(values).all() or np.any(values < 0):
            raise ValueError("Correction magnitudes must be finite and nonnegative")
        day, hour = divmod(index, 24)
        matrix[:, hour, day] = values
    offset = start.weekday()
    weeks = (offset + days + 6) // 7
    tensor = np.full((len(METRICS), 24, weeks, 7), np.nan)
    for day in range(days):
        week, weekday = divmod(offset + day, 7)
        tensor[:, :, week, weekday] = matrix[:, :, day]
    monday = start - timedelta(days=offset)
    return matrix, tensor, monday


def load_snapshot(path):
    path = Path(path).resolve()
    report = read_json(path / "dispatch/report.json")
    with (path / "dispatch/intervals.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != report["completed"] or report["delta_hours"] != 1:
        raise ValueError("Snapshot count/timestep mismatch")
    first = min(rows, key=lambda r: int(r["iteration"]))
    start = datetime.fromisoformat(first["timestamp"]) - timedelta(hours=int(first["iteration"]))
    matrix, tensor, monday = fold_calendar(rows, report["horizon"], start)
    return dict(path=path, report=report, matrix=matrix, tensor=tensor, start=start, monday=monday,
                completion=read_json(path / "completion/summary.json"),
                periods=read_json(path / "periods/summary.json"),
                solves=read_json(path / "solves/summary.json"))


def plot_heatmaps(data, weekday=None):
    """Comparable linear scales per metric, fixed across every weekday slice."""
    if weekday is not None and weekday not in range(7):
        raise ValueError("Weekday must be 0 (Monday) through 6 (Sunday)")
    cube = data["matrix"] if weekday is None else data["tensor"][:, :, :, weekday]
    fig, axes = plt.subplots(3, 1, figsize=(15, 9), sharex=True, layout="constrained")
    cmap = plt.get_cmap("viridis").copy()
    cmap.set_bad("#dedede")
    for index, (ax, (_, title, unit)) in enumerate(zip(axes, METRICS)):
        maximum = float(np.nanmax(data["matrix"][index]))
        im = ax.imshow(np.ma.masked_invalid(cube[index]), aspect="auto", origin="lower",
                       interpolation="nearest", extent=(0, cube.shape[2], 0, 24),
                       cmap=cmap, vmin=0, vmax=maximum if maximum > 0 else 1)
        ax.set_title(title, loc="left", weight="bold")
        ax.set_yticks([.5, 6.5, 12.5, 18.5, 23.5], ["00", "06", "12", "18", "23"])
        ax.set_ylabel("Hour (study UTC)")
        fig.colorbar(im, ax=ax, pad=.01, label=f"Σ |AC − DC| ({unit})")
    if weekday is None:
        dates = [data["start"] + timedelta(days=d) for d in range(cube.shape[2])]
        ticks = [i for i, date in enumerate(dates) if date.day == 1]
        axes[-1].set_xticks(ticks, [dates[i].strftime("%b") for i in ticks])
        label = "Matrix view · one column per day"
        axes[-1].set_xlabel("Synthetic study date · 2025")
    else:
        ticks = list(range(0, cube.shape[2], 4))
        dates = [data["monday"] + timedelta(days=7*w + weekday) for w in ticks]
        axes[-1].set_xticks(np.array(ticks)+.5, [d.strftime("%b %d") for d in dates])
        label = f"Tensor view · {WEEKDAYS[weekday]} · one column per calendar week"
        axes[-1].set_xlabel("Date of selected weekday · padded/nonexecuted cells remain gray")
    fig.suptitle(f"{label}\n{data['report']['completed']:,} accepted hourly actions", weight="bold")
    return fig


