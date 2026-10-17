"""Descriptive condition-dependent model comparisons for the six E3 scenarios.

No solves, changed gates, statistical inference, or optimality certificates.
Totals, fleet trajectories, and asset trajectories answer different questions.
The retained study has hourly intervals (delta=1), matched within each scenario.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .e3_results import COLORS, LABELS

HORIZONS = {
    "Served energy": ("MWh", "p_load_served"),
    "Unserved energy": ("MWh", "p_load_shed"),
    "Conventional generation": ("MWh", "Pg"),
    "Renewable curtailment": ("MWh", "curtailment"),
    "Battery throughput": ("MWh", "b"),
    "Network real-energy absorption": ("MWh", "p_net"),
    "Physical dispatch cost": ("cost units", "objective"),
}
PROFILES = {
    "Generation": ("Pg", "generators", "pmax_mw"),
    "Renewable dispatch": ("p_nd", "renewables", "apparent_power_rating_mva"),
    "Served load": ("p_load_served", "loads", None),
    "Battery real power": ("b", "batteries", "apparent_power_rating_mva"),
}


def percentage(value: float, scale: float) -> float:
    """Undefined near-zero ratios stay NaN; raw differences are never clipped."""
    return 100 * value / abs(scale) if abs(scale) > 1e-6 else np.nan


def trajectory_difference(
    model: np.ndarray, reference: np.ndarray, ratings: np.ndarray
) -> dict:
    """Separate fleet cancellation from asset-level differences (hourly MW)."""
    difference = np.asarray(model) - np.asarray(reference)
    ratings = np.asarray(ratings)
    fleet = difference.sum(axis=1)
    fleet_l1 = float(np.abs(fleet).sum())
    asset_l1 = float(np.abs(difference).sum())
    rated = ratings > 0
    return {
        "Fleet peak difference (MW)": float(np.abs(fleet).max()),
        "Asset peak difference (MW)": float(np.abs(difference).max()),
        "Fleet peak / total rating (%)": percentage(
            float(np.abs(fleet).max()), float(ratings.sum())
        ),
        "Asset peak / own rating (%)": float(
            (np.abs(difference[:, rated]) / ratings[rated]).max() * 100
        )
        if rated.any()
        else np.nan,
        "Fleet absolute trajectory difference (MWh)": fleet_l1,
        "Asset absolute trajectory difference (MWh)": asset_l1,
        "Difference hidden by fleet aggregation (%)": percentage(
            asset_l1 - fleet_l1, asset_l1
        ),
    }


def sensitivity_tables(data: dict, reference: str = "ac") -> dict[str, pd.DataFrame]:
    """Matched model contrasts plus input conditions and AC operating context."""
    conditions, totals, profiles, assets, hours = [], [], [], [], []
    generator_capacity = float(data["tables"]["generators"].pmax_mw.sum())
    rated_branches = (data["branches"][:, 5] > 0) & np.isfinite(data["branches"][:, 5])
    for scenario, case in data["cases"].items():
        base = case[reference]
        ac = case["ac"]
        demand = ac["result"]["p_load"].sum(axis=1)
        available = ac["renewable_available_mw"].sum(axis=1)
        margin = available + generator_capacity - demand
        utilization = np.maximum(
            ac["result"]["branch_s_from"], ac["result"]["branch_s_to"]
        )
        utilization = (
            utilization[:, rated_branches] / data["branches"][rated_branches, 5]
        )
        conditions.append(
            {
                "Scenario": scenario,
                "Gross demand (GWh)": float(demand.sum() / 1000),
                "Available renewables / demand (%)": percentage(
                    float(available.sum()), float(demand.sum())
                ),
                "Renewable-surplus hours": int((available > demand).sum()),
                "Resource-deficit hours (before storage/network)": int(
                    (margin < 0).sum()
                ),
                "Permitted battery net withdrawal (MWh)": float(
                    sum(ac["arm"]["initial_soc_mwh"])
                    - sum(ac["arm"]["terminal_soc_mwh"])
                ),
                "AC branch-hours above 99% rating (%)": float(
                    100 * (utilization > 0.99).mean()
                )
                if utilization.size
                else np.nan,
                "AC real network absorption (MWh)": float(ac["result"]["p_net"].sum()),
                "SOCP maximum normalized rank gap (%)": 100
                * case["socp"]["relaxation_audit"]["edge_gaps"]["relative_summary"][
                    "maximum"
                ],
            }
        )
        for model, record in case.items():
            if model == reference:
                continue
            common = {"Scenario": scenario, "Model": model, "Reference": reference}
            for quantity, (unit, key) in HORIZONS.items():
                value = record["result"][key]
                baseline = base["result"][key]
                if key == "b":
                    value, baseline = np.abs(value), np.abs(baseline)
                value, baseline = float(np.sum(value)), float(np.sum(baseline))
                delta = value - baseline
                totals.append(
                    {
                        **common,
                        "Quantity": quantity,
                        "Unit": unit,
                        "Model value": value,
                        "Reference value": baseline,
                        "Signed difference": delta,
                        "Difference / reference (%)": percentage(delta, baseline),
                        "Difference / gross demand (%)": percentage(
                            delta, float(demand.sum())
                        )
                        if unit == "MWh"
                        else np.nan,
                    }
                )
            for quantity, (key, family, rating_column) in PROFILES.items():
                if family == "loads":
                    names = base["input_identities"]["loads"]
                    ratings = base["result"]["p_load"].max(axis=0)
                else:
                    names = data["tables"][family].device_id.tolist()
                    ratings = data["tables"][family][rating_column].to_numpy()
                values, baselines = record["result"][key], base["result"][key]
                delta = values - baselines
                profiles.append(
                    {
                        **common,
                        "Quantity": quantity,
                        **trajectory_difference(values, baselines, ratings),
                    }
                )
                for i, name in enumerate(names):
                    assets.append(
                        {
                            **common,
                            "Quantity": quantity,
                            "Asset": name,
                            "Peak difference (MW)": float(np.abs(delta[:, i]).max()),
                            "Peak / own rating (%)": percentage(
                                float(np.abs(delta[:, i]).max()), float(ratings[i])
                            ),
                            "Model peak |power| (MW)": float(
                                np.abs(values[:, i]).max()
                            ),
                            "Reference peak |power| (MW)": float(
                                np.abs(baselines[:, i]).max()
                            ),
                            "Model gross |power| integral (MWh)": float(
                                np.abs(values[:, i]).sum()
                            ),
                            "Reference gross |power| integral (MWh)": float(
                                np.abs(baselines[:, i]).sum()
                            ),
                        }
                    )
                for t, fleet_delta in enumerate(delta.sum(axis=1)):
                    hours.append(
                        {
                            **common,
                            "Quantity": quantity,
                            "Global input hour": ac["arm"]["start"] + t,
                            "Resource margin / peak demand (%)": percentage(
                                float(margin[t]), float(demand.max())
                            ),
                            "Fleet difference (MW)": float(fleet_delta),
                            "Fleet difference / total rating (%)": percentage(
                                float(fleet_delta), float(ratings.sum())
                            ),
                        }
                    )
    return {
        name: pd.DataFrame(rows)
        for name, rows in (
            ("conditions", conditions),
            ("totals", totals),
            ("profiles", profiles),
            ("assets", assets),
            ("hours", hours),
        )
    }


def sensitivity_heatmap(
    table: pd.DataFrame,
    panels: list[tuple[str, str]],
    scenarios: list[str],
    models: list[str],
):
    """Absolute magnitudes in color; signed percentages retained in cell labels."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    figure = make_subplots(
        rows=(len(panels) + 1) // 2,
        cols=2,
        subplot_titles=[title for title, _ in panels],
        vertical_spacing=0.2,
    )
    xlabels = [s.split(" · hours")[0].replace(" · ", "<br>") for s in scenarios]
    for index, (title, column) in enumerate(panels):
        subset = table if "Quantity" not in table else table[table["Quantity"] == title]
        matrix = subset.pivot(index="Model", columns="Scenario", values=column).reindex(
            index=models, columns=scenarios
        )
        values = matrix.to_numpy(dtype=float)
        text = np.array(
            [
                [f"{v:+.1f}%" if np.isfinite(v) else "undefined" for v in row]
                for row in values
            ]
        )
        figure.add_trace(
            go.Heatmap(
                z=np.abs(values),
                x=xlabels,
                y=[LABELS[m] for m in models],
                text=text,
                texttemplate="%{text}",
                coloraxis="coloraxis",
                hovertemplate="%{x}<br>%{y}<br>%{text}<extra></extra>",
            ),
            row=index // 2 + 1,
            col=index % 2 + 1,
        )
    figure.update_layout(
        height=280 * ((len(panels) + 1) // 2) + 120,
        coloraxis=dict(colorscale="YlOrRd", cmin=0, colorbar=dict(title="Absolute %")),
        margin=dict(l=30, r=40, b=60),
        template="plotly_white",
    )
    return figure


def condition_scatter(table: pd.DataFrame, quantity: str):
    """Hourly condition association, not a causal fit; time remains in hover."""
    import plotly.graph_objects as go

    figure = go.Figure()
    selected = table[table["Quantity"] == quantity]
    symbols = ("circle", "square", "diamond", "cross", "x", "triangle-up")
    scenarios = list(selected["Scenario"].unique())
    for (model, scenario), rows in selected.groupby(["Model", "Scenario"], sort=False):
        figure.add_trace(
            go.Scatter(
                x=rows["Resource margin / peak demand (%)"],
                y=rows["Fleet difference / total rating (%)"],
                mode="markers",
                name=f"{LABELS[model]} · {scenario.split(' · hours')[0]}",
                marker=dict(
                    color=COLORS[model],
                    symbol=symbols[scenarios.index(scenario) % len(symbols)],
                    size=7,
                    opacity=0.7,
                ),
                customdata=rows[
                    ["Global input hour", "Fleet difference (MW)"]
                ].to_numpy(),
                hovertemplate="Resource margin: %{x:.1f}%<br>Fleet difference: %{y:.2f}% rating<br>%{customdata[1]:.2f} MW<br>Global hour %{customdata[0]}<extra>%{fullData.name}</extra>",
            )
        )
    figure.add_hline(y=0, line_color="gray")
    figure.add_vline(x=0, line_color="gray", line_dash="dot")
    figure.update_layout(
        title=f"{quantity}: hourly model differences versus resource conditions",
        xaxis_title="Available renewables + generator capacity − demand (% of scenario peak demand)",
        yaxis_title="Signed fleet difference (% of total installed rating)",
        height=580,
        template="plotly_white",
        legend=dict(orientation="h", y=-0.25),
    )
    return figure


def findings_markdown(tables: dict[str, pd.DataFrame]) -> str:
    """Engineering interpretation of this six-scenario inventory, against AC."""
    totals = tables["totals"]

    def row(prefix, quantity, model):
        selected = totals[
            (totals["Scenario"].str.startswith(prefix))
            & (totals["Quantity"] == quantity)
            & (totals["Model"] == model)
        ]
        return selected.iloc[0]

    served = totals[totals["Quantity"] == "Served energy"]
    ordinary = served[~served["Scenario"].str.startswith("Forced shedding")]
    transition = row("Surplus to deficit", "Conventional generation", "singlenode_dc")
    deficit = row(
        "Large deficit · energy neutral", "Conventional generation", "singlenode_dc"
    )
    forced = [
        row("Forced shedding", "Served energy", model)
        for model in ("socp", "lossy_dc", "singlenode_dc")
    ]
    cycling = row("Large surplus", "Battery throughput", "lossy_dc")
    absorption = row("Large surplus", "Network real-energy absorption", "socp")
    neutral = row(
        "Large deficit · energy neutral", "Conventional generation", "singlenode_dc"
    )
    depletion = row(
        "Large deficit · deficit depletion", "Conventional generation", "singlenode_dc"
    )
    return f"""
### What the six scenarios currently tell us (AC reference)

**Total load service agrees in the ordinary cases, but not under forced scarcity.** Across
the five original scenarios, the largest served-energy difference from AC is
{ordinary["Signed difference"].abs().max():.2g} MWh: all methods effectively serve
the same total load. In forced shedding, SOCP serves {forced[0]["Difference / reference (%)"]:.2f}%
more than AC, lossy DC {forced[1]["Difference / reference (%)"]:.2f}% more, and
copper plate {forced[2]["Difference / reference (%)"]:.2f}% more. SOCP is closest
for this quantity in this condition—not necessarily for every decision.

**Fuel use can be sensitive even when adequacy is not.** Copper plate uses
{-deficit["Signed difference"] / 1000:.2f} GWh less conventional generation than AC
in the energy-neutral deficit scenario, and {-transition["Signed difference"] / 1000:.2f} GWh
less in surplus-to-deficit ({-transition["Difference / reference (%)"]:.1f}% of AC generation),
without serving less load. A model sufficient for an aggregate reliability
question may therefore be inadequate for fuel, emissions, or dispatch planning.

**Renewable curtailment is particularly sensitive to relaxation freedom.** In
large surplus, SOCP's lifted network absorbs {absorption["Model value"] / 1000:.2f} GWh
versus AC's physical {absorption["Reference value"] / 1000:.2f} GWh. With equal served
load and energy-neutral storage, much of SOCP's lower curtailment feeds that
lifted absorption, not additional customer service. It is not automatically a
realizable renewable-integration benefit. The condition table exposes retained
rank gaps separately from small balance residuals.

**Battery value and battery allocation are different questions.** Large-surplus
AC throughput is {cycling["Reference value"] / 1000:.2f} GWh while both DC models
are essentially idle. In the deficit and transition scenarios, similar daily
throughput can coexist with very different individual schedules. A large
asset peak difference with a small fleet difference identifies redistribution
hidden by aggregation. Extra cycling alone is not evidence of greater value:
energy-neutral storage cannot create energy, and inexpensive cycling is weakly
resolved in some convex candidates.

**Battery boundary conditions interact with fidelity.** On the same ordinary
deficit input window, moving from the neutral to depletion boundary targets
reduces AC generation by {(neutral["Reference value"] - depletion["Reference value"]):,.1f} MWh,
while copper-plate generation changes by only
{(neutral["Model value"] - depletion["Model value"]):.2g} MWh. The available initial
and terminal energy budgets must be kept explicit when interpreting storage value.

**Research implication:** choose fidelity for the quantity and operating regime,
not by a universal ranking. Use the heatmaps for scale, hourly plots for the
association with resource conditions, and asset tables for which decisions change.
Six selected scenarios (including repeated input windows) do not establish a
population-wide threshold or a causal decomposition. These contrasts include
model structure, local solution choice, and numerical resolution; AC is a
physics-aware comparator, not a certified global optimum.
"""
