"""Clone-ready tests of the weekly scatter containment convention."""

import numpy as np
import pandas as pd

from experiments.case118_tracy_2021.plot_window_selection import (
    APPROVED_WINDOWS,
    WINDOWS,
    contained,
    select_ordinary,
    weekly_metrics,
)


def test_containment_includes_last_complete_week_not_partial_overlap():
    net = pd.Series(
        np.arange(8760.0),
        index=pd.date_range("2021-01-01", periods=8760, freq="h", tz="Etc/GMT+8"),
    )
    weeks = weekly_metrics(net)
    assert len(weeks) == 359
    for (_, start, stop, _, _), count in zip(
        APPROVED_WINDOWS, (22, 55, 8, 8, 22), strict=True
    ):
        selected = weeks.loc[contained(weeks, start, stop)]
        assert len(selected) == count
        assert selected.iloc[0].start == pd.Timestamp(start, tz="Etc/GMT+8")
        assert selected.iloc[-1].stop_exclusive == pd.Timestamp(stop, tz="Etc/GMT+8")
    for i in (0, 100, 358):
        block = net.iloc[i * 24 : i * 24 + 168]
        assert weeks.iloc[i].net_energy_gwh == block.sum() / 1000
        assert weeks.iloc[i].peak_net_load_mw == block.max()
        assert weeks.iloc[i].minimum_net_load_mw == block.min()


def test_ordinary_rule_and_within_window_ramps():
    index = pd.date_range("2021-01-01", periods=8760, freq="h", tz="Etc/GMT+8")
    t = np.arange(8760.0)
    frame = pd.DataFrame(
        dict(
            load_mw=100 + np.sin(t / 100), net_load_mw=np.cos(t / 71) * 20 + t / 10000
        ),
        index=index,
    )
    table, selected = select_ordinary(frame)
    assert len(table) == 352
    values = []
    for start in range(0, 8760 - 335, 24):
        load = frame.load_mw.to_numpy()[start : start + 336]
        net = frame.net_load_mw.to_numpy()[start : start + 336]
        values.append(
            [load.mean(), net.mean(), max(0, net.max()), np.abs(np.diff(net)).mean()]
        )
    values = np.array(values)
    iqr = np.quantile(values, 0.75, axis=0) - np.quantile(values, 0.25, axis=0)
    expected = np.square((values - np.median(values, axis=0)) / iqr).sum(axis=1)
    np.testing.assert_allclose(table.score, expected)
    winner = table.loc[table.eligible].sort_values(["score", "start"]).iloc[0]
    assert winner.start.isoformat() == selected["start"]
    assert table.selected.sum() == 1
    for _, a, b, _, _ in WINDOWS:
        overlap = (table.start < pd.Timestamp(b, tz="Etc/GMT+8")) & (
            table.stop_exclusive > pd.Timestamp(a, tz="Etc/GMT+8")
        )
        assert not table.loc[overlap].eligible.any()


def test_ordinary_zero_iqr_and_earliest_tie():
    frame = pd.DataFrame(
        dict(load_mw=np.ones(8760), net_load_mw=-np.ones(8760)),
        index=pd.date_range("2021-01-01", periods=8760, freq="h", tz="Etc/GMT+8"),
    )
    table, selected = select_ordinary(frame)
    assert (table.score == 0).all()
    assert len(selected["omitted_zero_iqr_features"]) == 4
    assert selected["start"] == "2021-01-01T00:00:00-08:00"
