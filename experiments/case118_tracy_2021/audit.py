"""Independent source-row reconstruction; does not call the preparation builder."""

import numpy as np
import pandas as pd

from experiments.case118_annual_hierarchy.pglib_case import load_pglib_case118
from .prepare import SOURCE, PreparedInputs


def audit_inputs(p: PreparedInputs) -> dict:
    raw = pd.read_csv(SOURCE)
    raw = raw.loc[raw.time.str.startswith("2021-")]
    source = raw[
        [f"9q9wtp_{c}" for c in ("load", "solar", "wind", "dist_solar")]
    ].to_numpy()
    scale = 6000 / np.max(source[:, 0] - source[:, 1:].sum(axis=1))
    case = load_pglib_case118()
    bus = np.asarray(case["bus"])
    load_rows = bus[bus[:, 2] > 0]
    load_rows = load_rows[np.argsort(load_rows[:, 0])]
    expected_p = np.outer(source[:, 0] * scale, load_rows[:, 2] / bus[:, 2].sum())
    expected_q = np.outer(source[:, 0] * scale, load_rows[:, 3] / bus[:, 2].sum())
    np.testing.assert_allclose(p.load_p_mw, expected_p, rtol=2e-14, atol=1e-10)
    np.testing.assert_allclose(p.load_q_mvar, expected_q, rtol=2e-14, atol=1e-10)
    residuals = {
        "load_mw": float(np.max(np.abs(p.load_p_mw.sum(axis=1) - source[:, 0] * scale)))
    }
    for k, c in enumerate(("solar", "wind", "dist_solar"), 1):
        rows = p.renewables.channel == c
        shares = p.renewables.loc[rows, "share"].to_numpy()
        assert (shares >= 0).all()
        np.testing.assert_allclose(shares.sum(), 1, rtol=0, atol=1e-14)
        values = p.nd_available_mw[:, rows]
        np.testing.assert_allclose(
            values, np.outer(source[:, k] * scale, shares), rtol=2e-14, atol=1e-10
        )
        residuals[c + "_mw"] = float(
            np.max(np.abs(values.sum(axis=1) - source[:, k] * scale))
        )
    assert max(residuals.values()) < 1e-8
    assert p.batteries.bus.nunique() == 27
    assert p.renewables.device_id.is_unique
    np.testing.assert_allclose(p.generators.pmax_mw.sum(), 5000, atol=1e-10, rtol=0)
    energy = 4 * float((source[:, 0] * scale).mean())
    for pool, group in p.batteries.groupby("pool"):
        assert len(group) == (22 if pool == "load_side" else 5)
        np.testing.assert_allclose(
            group.capacity_mwh.sum(), energy / 2, atol=1e-9, rtol=0
        )
    np.testing.assert_allclose(
        p.batteries.capacity_mwh / p.batteries.active_power_limit_mw, 3
    )
    gen = np.asarray(case["gen"])
    cost = np.asarray(case["gencost"])
    positive = gen[:, 8] > 0
    np.testing.assert_array_equal(p.generators.c1, cost[:, 5])
    np.testing.assert_array_equal(p.generators.c0, cost[:, 6])
    g = p.generators.loc[positive]
    np.testing.assert_allclose(g.c2 * g.pmax_mw**2, g.c1 * g.pmax_mw / 3)
    np.testing.assert_allclose(
        100 * g.marginal_cost_at_pmax.max(), 20763.594, rtol=1e-14
    )
    return dict(
        passed=True,
        scope="all 8760 source rows; every allocated active/reactive load and renewable channel; fleet totals and starting costs",
        maximum_channel_conservation_residual_mw=residuals,
        network_deliverability_tested=False,
        numerical_solves=0,
    )
