"""Input-only regional transfer screen; no model construction or solving."""

from collections import deque
import json

import numpy as np
import pandas as pd

from experiments.case118_annual_hierarchy.pglib_case import (
    SOURCE_CASE_PATH,
    load_pglib_case118,
)
from .plot_window_selection import (
    APPROVED_WINDOWS,
    contained,
    select_ordinary,
    weekly_metrics,
)
from .prepare import HERE, digest, write_json


def regions(bus_ids, branches, count=4):
    """Farthest-first graph Voronoi partition; bus IDs resolve all ties."""
    ids = sorted(int(b) for b in bus_ids)
    graph = {b: set() for b in ids}
    for row in branches:
        if row[10] > 0:
            a, b = int(row[0]), int(row[1])
            graph[a].add(b)
            graph[b].add(a)

    def distances(seed):
        result = {seed: 0}
        queue = deque([seed])
        while queue:
            node = queue.popleft()
            for neighbor in sorted(graph[node]):
                if neighbor not in result:
                    result[neighbor] = result[node] + 1
                    queue.append(neighbor)
        if len(result) != len(ids):
            raise ValueError("screen requires a connected active network")
        return result

    if not 1 <= count <= len(ids) or 1 not in ids:
        raise ValueError("invalid region count or missing initial bus 1")
    seeds = [1]
    distance = {1: distances(1)}
    while len(seeds) < count:
        seed = min(ids, key=lambda b: (-min(distance[s][b] for s in seeds), b))
        seeds.append(seed)
        distance[seed] = distances(seed)
    assignment = {b: min(seeds, key=lambda s: (distance[s][b], s)) for b in ids}
    return {s: [b for b in ids if assignment[b] == s] for s in sorted(seeds)}


def cut_branches(members, branches):
    """Keep parallel branches separately, with original zero-based row IDs."""
    result = []
    for i, row in enumerate(branches):
        a, b = int(row[0]), int(row[1])
        if row[10] > 0 and ((a in members) != (b in members)):
            if not np.isfinite(row[5]) or row[5] <= 0:
                raise ValueError("unrated regional cut; no invented capacity")
            result.append(dict(source_row=i, from_bus=a, to_bus=b, rateA=float(row[5])))
    if not result:
        raise ValueError("empty regional cut")
    return result


def pressures(load, renewable, generation, battery):
    """MW proxies, with full service and availability (not dispatch)."""
    net = load - renewable
    return dict(
        import_before_flex=np.maximum(net, 0),
        import_after_generation=np.maximum(net - generation, 0),
        import_after_generation_and_discharge=np.maximum(net - generation - battery, 0),
        export_before_flex=np.maximum(-net, 0),
        export_after_charging=np.maximum(-net - battery, 0),
    )


def coverage(values, index, windows):
    """Peak and fully-contained weekly-mean coverage, including zero-only case."""
    series = pd.Series(values, index=index)
    weeks = weekly_metrics(series)
    means = weeks.net_energy_gwh.to_numpy() * 1000 / 168
    selected_hours = np.zeros(len(index), dtype=bool)
    selected_weeks = np.zeros(len(weeks), dtype=bool)
    per_window = {}
    for name, start, stop in windows:
        mask = (index >= pd.Timestamp(start, tz="Etc/GMT+8")) & (
            index < pd.Timestamp(stop, tz="Etc/GMT+8")
        )
        wmask = contained(weeks, start, stop).to_numpy()
        selected_hours |= mask
        selected_weeks |= wmask
        per_window[name] = dict(
            hourly_max_mw=float(values[mask].max()),
            weekly_mean_max_mw=float(means[wmask].max()),
        )
    peak = int(np.argmax(values))
    week = int(np.argmax(means))
    threshold = float(np.quantile(values, 0.99))
    tail = (values > 0) & (values >= threshold)
    nonzero = bool(values[peak] > 0)
    return dict(
        annual_peak_mw=float(values[peak]),
        annual_peak_time=index[peak].isoformat() if nonzero else None,
        earliest_peak_covered=bool(selected_hours[peak]) if nonzero else None,
        selected_peak_mw=float(values[selected_hours].max()),
        selected_to_annual_peak=float(values[selected_hours].max() / values[peak])
        if nonzero
        else None,
        annual_weekly_mean_mw=float(means[week]),
        annual_week_start=weeks.iloc[week].start.isoformat() if nonzero else None,
        annual_week_stop=weeks.iloc[week].stop_exclusive.isoformat()
        if nonzero
        else None,
        selected_weekly_mean_mw=float(means[selected_weeks].max()),
        selected_to_annual_weekly_mean=float(means[selected_weeks].max() / means[week])
        if nonzero
        else None,
        positive_tail_threshold_mw=threshold,
        positive_tail_hours=int(tail.sum()),
        positive_tail_hours_covered=int((tail & selected_hours).sum()),
        by_window=per_window,
    )


def main():
    stage_a = HERE / "stage_a"
    raw = HERE / "results/stage_a_active_inputs"
    manifest_path = stage_a / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    paths = [raw / "active_inputs.npz", raw / "aggregate_inputs.csv"]
    paths += [
        stage_a / f"{n}.csv" for n in ("buses", "generators", "batteries", "renewables")
    ]
    for path in paths:
        expected = manifest[
            "raw_artifacts" if path.parent == raw else "review_artifacts"
        ][path.name]
        if digest(path) != expected:
            raise ValueError(f"Stage A input changed: {path.name}")
    if digest(SOURCE_CASE_PATH) != manifest["pglib_sha256"]:
        raise ValueError("network source differs from Stage A")
    case = load_pglib_case118()
    partition = regions(np.asarray(case["bus"])[:, 0], case["branch"])
    buses, generators, batteries, renewables = [
        pd.read_csv(stage_a / f"{n}.csv")
        for n in ("buses", "generators", "batteries", "renewables")
    ]
    load_buses = buses.loc[buses.load_share > 0, "bus"]
    if [f"load_bus_{b}" for b in load_buses] != manifest["load_device_ids"]:
        raise ValueError("load column identities differ")
    if renewables.device_id.tolist() != manifest["renewable_device_ids"]:
        raise ValueError("renewable column identities differ")
    if (generators.pmin_mw != 0).any():
        raise ValueError("export proxy assumes zero generator minimums")
    frame = pd.read_csv(raw / "aggregate_inputs.csv", index_col=0, parse_dates=True)
    _, ordinary = select_ordinary(frame)
    windows = [(n, a, b) for n, a, b, _, _ in APPROVED_WINDOWS] + [
        ("Ordinary control", ordinary["start"][:10], ordinary["stop_exclusive"][:10])
    ]
    with np.load(raw / "active_inputs.npz") as arrays:
        load = arrays["load_p_mw"]
        renewable = arrays["nd_available_mw"]
    np.testing.assert_allclose(load.sum(axis=1), frame.load_mw, rtol=1e-12)
    np.testing.assert_allclose(
        load.sum(axis=1) - renewable.sum(axis=1), frame.net_load_mw, atol=1e-9
    )
    result = []
    hour_frames = []
    summary = []
    for seed, members in partition.items():
        cuts = cut_branches(members, case["branch"])
        rating = sum(row["rateA"] for row in cuts)
        g = float(generators.loc[generators.bus.isin(members), "pmax_mw"].sum())
        b = float(
            batteries.loc[batteries.bus.isin(members), "active_power_limit_mw"].sum()
        )
        energy = float(batteries.loc[batteries.bus.isin(members), "capacity_mwh"].sum())
        local_load = load[:, load_buses.isin(members)].sum(axis=1)
        local_renewable = renewable[:, renewables.bus.isin(members)].sum(axis=1)
        scores = pressures(local_load, local_renewable, g, b)
        details = {}
        for name, values in scores.items():
            record = coverage(values, frame.index, windows)
            record["annual_peak_over_cut_rating"] = record["annual_peak_mw"] / rating
            details[name] = record
            summary.append(
                dict(
                    region=f"bus_{seed}",
                    metric=name,
                    **{k: v for k, v in record.items() if k != "by_window"},
                )
            )
        result.append(
            dict(
                region=f"bus_{seed}",
                buses=members,
                cut_branches=cuts,
                cut_rateA_sum_mva=rating,
                generator_pmax_mw=g,
                battery_power_mw=b,
                battery_capacity_mwh=energy,
                coverage=details,
            )
        )
        hour_frames.append(
            pd.DataFrame(
                dict(
                    time=frame.index,
                    region=f"bus_{seed}",
                    load_mw=local_load,
                    renewable_available_mw=local_renewable,
                    signed_net_load_mw=local_load - local_renewable,
                    **{f"{k}_mw": v for k, v in scores.items()},
                    **{f"{k}_over_cut_rating": v / rating for k, v in scores.items()},
                )
            )
        )
    destination = HERE / "stage_b_selection"
    destination.mkdir(exist_ok=True)
    # Full hourly evidence is reproducible local output, not another tracked raw dataset.
    raw_output = HERE / "results/stage_b_spatial_screen"
    raw_output.mkdir(exist_ok=True)
    hourly_path = raw_output / "regional_hourly_approved.csv"
    pd.concat(hour_frames, ignore_index=True).to_csv(hourly_path, index=False)
    summary_path = destination / "spatial_coverage_approved.csv"
    pd.DataFrame(summary).to_csv(summary_path, index=False)
    write_json(
        destination / "spatial_screen_approved.json",
        dict(
            method="four farthest-first graph Voronoi regions; initial seed 1; smallest ID ties",
            classification="input_only_screen_not_solved_congestion",
            windows=[dict(name=n, start=a, stop_exclusive=b) for n, a, b in windows],
            regions=result,
            provenance=dict(
                stage_a_manifest_sha256=digest(manifest_path),
                network_sha256=digest(SOURCE_CASE_PATH),
                inputs={str(p.relative_to(HERE)): digest(p) for p in paths},
                script_sha256=digest(HERE / "spatial_screen.py"),
                window_selection_sha256=digest(HERE / "plot_window_selection.py"),
                hourly_output_sha256=digest(hourly_path),
                coverage_csv_sha256=digest(summary_path),
            ),
        ),
    )
    print(
        pd.DataFrame(summary)[
            [
                "region",
                "metric",
                "annual_peak_mw",
                "annual_peak_time",
                "selected_to_annual_peak",
                "annual_week_start",
                "selected_to_annual_weekly_mean",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
