"""Describe retained E3+ candidates without solving or changing their labels.

Writes separate descriptive figures/JSON under results/e3_plus_assessment.
All four candidates are shown, including the three protocol-rejected primals.
Canonical reconstruction is used only to attribute existing objective gaps;
it does not optimize, repair a dispatch, or certify cross-model optimality.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import e3_results as viewer

HERE = Path(__file__).resolve().parent
RUN = HERE / "results/e3_plus"
OUTPUT = HERE / "results/e3_plus_assessment"


def retained_candidates():
    binding = viewer.read_json(RUN / "binding.json")
    finish = viewer.read_json(RUN / "invocations/invocation-000/finish.json")
    viewer.require(
        finish["outcome"] == "complete_with_unresolved_arms", "Unexpected outcome"
    )
    records = {}
    for arm in binding["study"]["arms"]:
        directory = RUN / f"arm-{arm['id']:03d}/attempt-000"
        completion = viewer.read_json(directory / "completion.json")
        supervision = viewer.read_json(directory / "supervision.json")
        viewer.require(
            supervision["classification"] == "exited"
            and supervision["returncode"] == 0,
            "Candidate is unfinished",
        )
        for name, checksum in completion["artifacts"].items():
            path = viewer.retained_path(directory, name)
            viewer.require(viewer.sha(path) == checksum, "Retained artifact mismatch")
        import gzip

        with gzip.open(directory / "result.json.gz", "rt") as stream:
            record = json.load(stream)
        viewer.require(
            record["execution_context"] == binding["context"], "Context mismatch"
        )
        viewer.require(
            record["classification"] == completion["classification"], "Label mismatch"
        )
        record["arm"] = arm
        for key, value in record["result"].items():
            if isinstance(value, list):
                record["result"][key] = np.asarray(value)
        record["boundary_soc_mwh"] = np.asarray(record["boundary_soc_mwh"])
        record["renewable_available_mw"] = np.asarray(record["renewable_available_mw"])
        records[arm["formulation"]] = record
    reference = records["ac"]
    for record in records.values():
        viewer.require(
            record["input_identities"] == reference["input_identities"],
            "Identity mismatch",
        )
        for key in ("p_load", "q_load"):
            np.testing.assert_array_equal(
                record["result"][key], reference["result"][key]
            )
        np.testing.assert_array_equal(
            record["renewable_available_mw"], reference["renewable_available_mw"]
        )
    return binding, records


def attribute_convex_cost(binding, record, inputs):
    """Read original-space canonical coefficients and the saved native primal."""
    import clarabel
    from cvxopf import build_opf_multistep
    from experiments.m11_socp.e1_compare import canonical_problem
    from . import e3_plus

    def forbidden(*args, **kwargs):
        raise RuntimeError("Descriptive assessment must not optimize")

    previous = clarabel.DefaultSolver
    clarabel.DefaultSolver = forbidden
    try:
        kwargs = e3_plus.kwargs_for_arm(
            inputs,
            record["arm"],
            binding["study"]["storage_device_ids"],
            assembly=record["request"]["objective_assembly"],
        )
        build = build_opf_multistep(**kwargs)
        data, _, inverse, mapping, scale, _, offset = canonical_problem(build)
        np.testing.assert_array_equal(
            scale, record["preparation_evidence"]["variable_scale"]
        )
        x = mapping.expand(np.asarray(record["native"]["x"]) * scale)
        primal_objective = (
            0.5 * x @ (data["P"] @ x) + data["c"] @ x + inverse[-1]["offset"]
        )
        economic = record["independent_audit"]["economics"]
        np.testing.assert_allclose(
            primal_objective, economic["canonical_objective"], atol=1e-3, rtol=0
        )
        # The full matrix (ns,T), coefficient lambda*delta, and non-original
        # identity distinguish the one throughput epigraph from cost/power leaves.
        inv = inverse[-2]
        physical_ids = {v.id for v in build.variables.values() if hasattr(v, "id")}
        wanted_shape = (len(kwargs["storage"]), kwargs["T"])
        weights = (
            np.asarray([s.aging_weight for s in kwargs["storage"]])[:, None]
            * kwargs["delta"]
        )
        candidates = []
        for identity, (start, size) in inv.id_map.items():
            if (
                identity not in physical_ids
                and inv.id2var[identity].shape == wanted_shape
            ):
                coefficients = data["c"][start : start + size].reshape(
                    wanted_shape, order="F"
                )
                if np.allclose(coefficients, weights, atol=0, rtol=0):
                    candidates.append((start, size))
        viewer.require(len(candidates) == 1, "Ambiguous battery epigraph attribution")
        start, size = candidates[0]
        aux = x[start : start + size].reshape(wanted_shape, order="F").T
        physical_power = record["result"]["b"]
        slack_mw = aux - np.abs(physical_power)
        canonical_cycling = float(
            data["c"][start : start + size] @ x[start : start + size]
        )
        physical_cycling = record["audit"]["costs"]["storage_cost"]
        excess = canonical_cycling - physical_cycling
        return dict(
            canonical_cycling_cost=canonical_cycling,
            physical_cycling_cost=physical_cycling,
            cycling_epigraph_excess=excess,
            maximum_auxiliary_power_excess_mw=float(slack_mw.max()),
            other_objective_difference=float(
                economic["unexplained_difference"] - excess
            ),
            shedding_price_per_mwh=kwargs["loads"][0].shedding_cost_per_mwh,
            equivalent_shedding_mwh=excess / kwargs["loads"][0].shedding_cost_per_mwh,
        )
    finally:
        clarabel.DefaultSolver = previous


def summarize(record):
    result = record["result"]
    arrays = {
        k: np.asarray(result[k])
        for k in ("Pg", "p_nd", "b", "p_load_served", "p_load_shed")
    }
    totals = {k: a.sum(axis=1) for k, a in arrays.items()}
    net = np.asarray(result["p_net"])
    network = net.sum(axis=1) if net.ndim == 2 else net
    summary = dict(
        classification=record["classification"],
        physical_costs=record["audit"]["costs"],
        energy_not_served_mwh=result["energy_not_served"],
        served_mwh=float(totals["p_load_served"].sum()),
        generation_mwh=float(totals["Pg"].sum()),
        renewable_mwh=float(totals["p_nd"].sum()),
        curtailment_mwh=float(np.asarray(result["curtailment"]).sum()),
        battery_throughput_mwh=float(np.abs(arrays["b"]).sum()),
        battery_charge_mwh=float(np.maximum(-arrays["b"], 0).sum()),
        battery_discharge_mwh=float(np.maximum(arrays["b"], 0).sum()),
        fleet_soc_min_mwh=float(record["boundary_soc_mwh"].sum(axis=1).min()),
        fleet_soc_max_mwh=float(record["boundary_soc_mwh"].sum(axis=1).max()),
        net_network_mwh=float(network.sum()),
        balance_supply_minus_served_mwh=float(
            (
                totals["Pg"] + totals["p_nd"] + totals["b"] - totals["p_load_served"]
            ).sum()
        ),
        maximum_terminal_soc_error_mwh=float(
            np.abs(
                record["boundary_soc_mwh"][-1] - record["arm"]["terminal_soc_mwh"]
            ).max()
        ),
        audit_failures={
            k: dict(residual=v, tolerance=record["audit"]["limits"][k])
            for k, v in record["audit"]["residuals"].items()
            if v > record["audit"]["limits"][k]
        },
    )
    return summary


def plotting(records):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def line(ax, record, values, *, boundary=False):
        f = record["arm"]["formulation"]
        label = f"{f} ({record['classification']})"
        if boundary:
            ax.plot(
                np.arange(25),
                values,
                color=viewer.COLORS[f],
                label=label,
                linewidth=1.6,
            )
        else:
            ax.stairs(
                values,
                np.arange(25),
                color=viewer.COLORS[f],
                label=label,
                linewidth=1.6,
            )
        ax.grid(alpha=0.25)
        ax.set_xlim(0, 24)

    panels = [
        (
            "Conventional generation",
            "MW",
            lambda r: r["result"]["Pg"].sum(axis=1),
            False,
        ),
        ("Renewable dispatch", "MW", lambda r: r["result"]["p_nd"].sum(axis=1), False),
        (
            "Served load",
            "MW",
            lambda r: r["result"]["p_load_served"].sum(axis=1),
            False,
        ),
        (
            "Load shedding",
            "MW",
            lambda r: r["result"]["p_load_shed"].sum(axis=1),
            False,
        ),
        (
            "Battery net power (+ discharge)",
            "MW",
            lambda r: r["result"]["b"].sum(axis=1),
            False,
        ),
        (
            "Fleet battery energy",
            "MWh",
            lambda r: r["boundary_soc_mwh"].sum(axis=1),
            True,
        ),
        (
            "Gross battery charging",
            "MW",
            lambda r: np.maximum(-r["result"]["b"], 0).sum(axis=1),
            False,
        ),
        (
            "Gross battery discharging",
            "MW",
            lambda r: np.maximum(r["result"]["b"], 0).sum(axis=1),
            False,
        ),
    ]
    fig, axes = plt.subplots(4, 2, figsize=(14, 14), constrained_layout=True)
    for ax, (title, unit, fn, boundary) in zip(axes.flat, panels, strict=True):
        for record in records.values():
            line(ax, record, fn(record), boundary=boundary)
        ax.set_title(title)
        ax.set_ylabel(unit)
        ax.set_xlabel("Hours from global input hour 1165")
    axes.flat[0].legend(fontsize=8)
    fig.suptitle(
        "E3+ forced shedding: retained candidate trajectories\nRejected means protocol-flagged; these are NOT four accepted comparisons."
    )
    fig.savefig(OUTPUT / "aggregate_trajectories.png", dpi=140)
    plt.close(fig)
    # Rank assets by the largest cross-formulation range over all 24 hours.
    selected = {}
    for key, table_name in (("b", "batteries"), ("Pg", "generators")):
        stack = np.stack([r["result"][key] for r in records.values()])
        spread = np.ptp(stack, axis=0).max(axis=0)
        indices = np.argsort(-spread, kind="stable")[:4]
        table = pd.read_csv(HERE / f"stage_a/{table_name}.csv")
        selected[key] = [
            dict(
                index=int(i),
                device_id=table.iloc[i].device_id,
                maximum_pair_difference_mw=float(spread[i]),
            )
            for i in indices
        ]
        fig, axes = plt.subplots(4, 2, figsize=(14, 12), constrained_layout=True)
        for row, index in enumerate(indices):
            for record in records.values():
                line(axes[row, 0], record, record["result"][key][:, index])
                if key == "b":
                    line(
                        axes[row, 1],
                        record,
                        record["boundary_soc_mwh"][:, index],
                        boundary=True,
                    )
                else:
                    line(
                        axes[row, 1],
                        record,
                        record["result"][key][:, index]
                        - records["ac"]["result"][key][:, index],
                    )
            axes[row, 0].set_title(table.iloc[index].device_id)
            axes[row, 0].set_ylabel("MW (+ discharge)" if key == "b" else "MW")
            axes[row, 1].set_title(
                "Battery energy" if key == "b" else "Dispatch difference from AC"
            )
            axes[row, 1].set_ylabel("MWh" if key == "b" else "MW")
            for ax in axes[row]:
                ax.set_xlabel("Hours from global input hour 1165")
        axes[0, 0].legend(fontsize=8)
        fig.suptitle(
            f"E3+ {table_name}: four assets with largest trajectory differences\nRetained candidates; original protocol labels preserved"
        )
        fig.savefig(OUTPUT / f"{table_name}_trajectories.png", dpi=140)
        plt.close(fig)
    return selected


def main():
    binding, records = retained_candidates()
    from . import e3_plus

    inputs = e3_plus.verified_inputs()
    summary = {f: summarize(r) for f, r in records.items()}
    for f, record in records.items():
        if f != "ac":
            summary[f]["cost_attribution"] = attribute_convex_cost(
                binding, record, inputs
            )
    kwargs = e3_plus.kwargs_for_arm(
        inputs, records["socp"]["arm"], binding["study"]["storage_device_ids"]
    )
    buses = {int(row[0]): i for i, row in enumerate(kwargs["case"]["bus"])}
    r = records["socp"]["result"]
    injection = np.zeros_like(r["p_net"])
    for devices, values in (
        (kwargs["generators"], r["Pg"]),
        (kwargs["storage"], r["b"]),
        (kwargs["nondispatchable"], r["p_nd"]),
        (kwargs["loads"], -r["p_load_served"]),
    ):
        for j, device in enumerate(devices):
            injection[:, buses[device.bus]] += values[:, j]
    residual = injection - r["p_net"]
    hour, bus = np.unravel_index(np.abs(residual).argmax(), residual.shape)
    summary["socp"]["nodal_balance"] = dict(
        maximum_mw=float(np.abs(residual).max()),
        global_hour=1165 + int(hour),
        bus_id=int(kwargs["case"]["bus"][bus, 0]),
        absolute_nodal_residual_integral_mwh=float(np.abs(residual).sum()),
        maximum_hourly_sum_mw=float(np.abs(residual.sum(axis=1)).max()),
    )
    pairs = {}
    for f, record in records.items():
        pairs[f] = {}
        for key in ("Pg", "b", "p_nd", "p_load_shed"):
            difference = record["result"][key] - records["ac"]["result"][key]
            pairs[f][key] = dict(
                maximum_asset_difference_mw=float(np.abs(difference).max()),
                maximum_fleet_difference_mw=float(np.abs(difference.sum(axis=1)).max()),
            )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    assets = plotting(records)
    output = dict(
        binding_sha256=viewer.sha(RUN / "binding.json"),
        original_labels_unchanged=True,
        summaries=summary,
        differences_from_ac=pairs,
        selected_assets=assets,
    )
    (OUTPUT / "summary.json").write_text(
        json.dumps(output, indent=2, allow_nan=False) + "\n"
    )
    print(json.dumps(output, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
