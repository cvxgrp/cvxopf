"""Prepare the approved Tracy source, seeded mapping and device inputs.

No optimization is constructed or solved. Unresolved operating choices are not
filled with implicit defaults. Full arrays go in ignored results/; compact
review artifacts can be retained separately using --review-output.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, fields
import hashlib
import json
from pathlib import Path
import platform
import subprocess

import numpy as np
import pandas as pd

from cvxopf.storage import StorageUnitIdeal
from experiments.case118_annual_hierarchy.pglib_case import (
    SOURCE_CASE_PATH,
    SOURCE_REVISION,
    array_sha256,
    load_pglib_case118,
)
from experiments.case118_annual_hierarchy.s5_closeout import (
    input_summary,
    shortfall_events,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = ROOT / "experiments/battery_terminal/data/9q9wtp_gen_and_load.csv"
SOURCE_SHA = "45e11f061d736741b18334aea0e9525c355c1a13068c291c1db6ed2e614b1b6f"
TOY = ROOT / "experiments/case118_annual_hierarchy/s5_closeout"
CHANNELS = ("load", "solar", "wind", "dist_solar")
PENDING = {
    "dc_loss_weight": "record explicit study setting before model construction",
}
APPROVED_OPERATING_CHOICES = {
    "reactive_load": "signed source Q/P ratios; fixed shunts separate",
    "generator_q_limits": "unchanged source limits",
    "renewable_mva_peak_factor": 1.1,
    "battery_mva": "equals approved E/3 MW limit numerically",
    "storage_state": "ideal; usable range [0,capacity]; initial/annual terminal 50%",
    "shedding_enabled": True,
    "max_shed_fraction": 1.0,
    "shedding_cost_per_mwh": 20763.594,
    "shedding_calibration": "100 times maximum starting marginal generation cost; fixed across sensitivities",
    "load_location_priority": "none; uniform penalty",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_source(path: Path = SOURCE) -> pd.DataFrame:
    if digest(path) != SOURCE_SHA:
        raise ValueError("Tracy CSV SHA-256 mismatch; no fallback permitted")
    frame = pd.read_csv(path)
    index = pd.DatetimeIndex(pd.to_datetime(frame.pop("time")))
    frame.index = index
    frame = frame.loc[frame.index.year == 2021]
    expected = pd.date_range("2021-01-01", periods=8760, freq="h", tz="Etc/GMT+8")
    if not np.array_equal(frame.index.asi8, expected.asi8):
        raise ValueError("Tracy 2021 must be ordered, complete and hourly")
    if any(t.utcoffset().total_seconds() != -28800 for t in frame.index):
        raise ValueError("source must retain fixed UTC-08:00")
    frame = frame[[f"9q9wtp_{c}" for c in CHANNELS]].copy()
    frame.columns = list(CHANNELS)
    if not np.isfinite(frame.to_numpy()).all() or (frame.to_numpy() < 0).any():
        raise ValueError("source channels must be finite and nonnegative")
    return frame


@dataclass
class PreparedInputs:
    source: pd.DataFrame
    aggregate: pd.DataFrame
    buses: pd.DataFrame
    renewables: pd.DataFrame
    batteries: pd.DataFrame
    generators: pd.DataFrame
    load_p_mw: np.ndarray
    load_q_mvar: np.ndarray
    nd_available_mw: np.ndarray
    alpha: float
    mapping: dict


def prepare() -> PreparedInputs:
    source = read_source()
    net = source.load - source.solar - source.wind - source.dist_solar
    alpha = 6000.0 / float(net.max())
    scaled = source * alpha
    aggregate = scaled.rename(columns={c: f"{c}_mw" for c in CHANNELS})
    aggregate["available_nd_mw"] = scaled[["solar", "wind", "dist_solar"]].sum(axis=1)
    aggregate["net_load_mw"] = aggregate.load_mw - aggregate.available_nd_mw
    case = load_pglib_case118()
    bus = np.asarray(case["bus"])
    gen = np.asarray(case["gen"])
    cost = np.asarray(case["gencost"])
    bus = bus[np.argsort(bus[:, 0])]
    active = (gen[:, 7] > 0) & (gen[:, 8] > 0)
    if not (
        active.sum() == 19
        and gen[active, 8].sum() == 6515
        and np.all(gen[:, 9] == 0)
        and np.all(gen[:, 7] == 1)
        and np.all(cost[:, 0] == 2)
        and np.all(cost[:, 3] == 3)
    ):
        raise ValueError("pinned generator assumptions changed")
    ids = bus[:, 0].astype(int)
    has_load = bus[:, 2] > 0
    has_gen = np.isin(ids, gen[active, 0])
    categories = {
        "load_only": ids[has_load & ~has_gen],
        "load_generation": ids[has_load & has_gen],
        "generation_no_load": ids[~has_load & has_gen],
        "neither": ids[~has_load & ~has_gen],
    }
    if [len(v) for v in categories.values()] != [89, 10, 9, 10]:
        raise ValueError("pinned bus categories changed")
    if bus[:, 2].sum() != 4242 or np.any((bus[:, 2] == 0) & (bus[:, 3] != 0)):
        raise ValueError("pinned load assumptions changed")
    rng = np.random.Generator(np.random.PCG64(42))
    selected = {
        key: np.sort(rng.choice(categories[key], 5, replace=False)).tolist()
        for key in ("load_generation", "generation_no_load", "neither")
    }
    utility = sorted(v for group in selected.values() for v in group)
    assignments = dict(
        zip(utility, rng.choice(["wind_only", "solar_only", "both"], 15), strict=True)
    )
    records = []
    for channel in ("solar", "wind"):
        hosts = [b for b in utility if assignments[b] in (f"{channel}_only", "both")]
        if not hosts:
            raise ValueError(f"seeded realization has no {channel}; no redraw")
        multipliers = rng.uniform(0.8, 1.2, len(hosts))
        for b, u, share in zip(
            hosts, multipliers, multipliers / multipliers.sum(), strict=True
        ):
            records.append(
                dict(
                    device_id=f"{channel}_bus_{b}",
                    bus=b,
                    channel=channel,
                    multiplier=float(u),
                    share=float(share),
                )
            )
    for row in bus[has_load]:
        b = int(row[0])
        records.append(
            dict(
                device_id=f"dist_solar_bus_{b}",
                bus=b,
                channel="dist_solar",
                multiplier=1.0,
                share=float(row[2] / 4242),
            )
        )
    renewables = pd.DataFrame(records)
    nd = np.column_stack(
        [scaled[r.channel].to_numpy() * r.share for r in renewables.itertuples()]
    )
    renewables["peak_available_mw"] = nd.max(axis=0)
    renewables["apparent_power_rating_mva"] = 1.1 * renewables.peak_available_mw
    renewables["annual_available_mwh"] = nd.sum(axis=0)
    load_buses = bus[has_load]
    load_p = scaled.load.to_numpy()[:, None] * (load_buses[:, 2] / 4242)[None, :]
    load_q = load_p * (load_buses[:, 3] / load_buses[:, 2])[None, :]
    no_load = sorted(b for b in utility if b not in categories["load_generation"])
    with_load = sorted(b for b in utility if b in categories["load_generation"])
    battery_sites = {
        "load_side": np.sort(
            rng.choice(categories["load_only"], 22, replace=False)
        ).tolist(),
        "renewable_no_load": np.sort(rng.choice(no_load, 3, replace=False)).tolist(),
        "renewable_with_load": np.sort(
            rng.choice(with_load, 2, replace=False)
        ).tolist(),
    }
    energy = float(4 * scaled.load.mean())
    aging = next(
        f.default for f in fields(StorageUnitIdeal) if f.name == "aging_weight"
    )
    storage_records = []
    for pool, hosts in (
        ("load_side", battery_sites["load_side"]),
        (
            "renewable_side",
            sorted(
                battery_sites["renewable_no_load"]
                + battery_sites["renewable_with_load"]
            ),
        ),
    ):
        weights = np.array(
            [
                float(bus[ids == b, 2][0])
                if pool == "load_side"
                else float(
                    renewables.loc[
                        (renewables.bus == b) & (renewables.channel != "dist_solar"),
                        "annual_available_mwh",
                    ].sum()
                )
                for b in hosts
            ]
        )
        for b, share in zip(hosts, weights / weights.sum(), strict=True):
            capacity = energy / 2 * float(share)
            storage_records.append(
                dict(
                    device_id=f"storage_bus_{b}",
                    bus=b,
                    pool=pool,
                    pool_share=float(share),
                    capacity_mwh=capacity,
                    active_power_limit_mw=capacity / 3,
                    aging_weight=aging,
                )
            )
    batteries = pd.DataFrame(storage_records).sort_values("bus").reset_index(drop=True)
    batteries["apparent_power_rating_mva"] = batteries.active_power_limit_mw
    batteries["initial_soc_mwh"] = batteries.capacity_mwh / 2
    batteries["terminal_soc_mwh"] = batteries.capacity_mwh / 2
    gen_records = []
    for i, (row, coeff) in enumerate(zip(gen, cost, strict=True)):
        maximum = float(row[8] * 5000 / 6515) if active[i] else float(row[8])
        c2, c1, c0 = map(float, coeff[4:7])
        if active[i]:
            if c1 <= 0:
                raise ValueError(
                    "positive-capacity generator requires positive inherited c1"
                )
            c2 = c1 / (3 * maximum)
        gen_records.append(
            dict(
                source_row=i,
                device_id=f"generator_row_{i}_bus_{int(row[0])}",
                bus=int(row[0]),
                original_pmax_mw=float(row[8]),
                pmax_mw=maximum,
                pmin_mw=float(row[9]),
                source_qmin_mvar=float(row[4]),
                source_qmax_mvar=float(row[3]),
                c0=c0,
                c1=c1,
                c2=c2,
                linear_cost_at_pmax=c1 * maximum,
                quadratic_cost_at_pmax=c2 * maximum**2,
                quadratic_to_linear_at_pmax=(1 / 3 if active[i] else None),
                marginal_cost_at_zero=c1,
                marginal_cost_at_pmax=c1 + 2 * c2 * maximum,
            )
        )
    buses = pd.DataFrame(dict(bus=ids, source_p_mw=bus[:, 2], source_q_mvar=bus[:, 3]))
    buses["category"] = [next(k for k, v in categories.items() if b in v) for b in ids]
    buses["load_share"] = bus[:, 2] / 4242
    buses["utility_type"] = [assignments.get(b, "none") for b in ids]
    buses["battery_pool"] = [
        next((r["pool"] for r in storage_records if r["bus"] == b), "none") for b in ids
    ]
    buses["annual_load_mwh"] = buses.load_share * float(scaled.load.sum())
    buses["annual_available_nd_mwh"] = [
        float(renewables.loc[renewables.bus == b, "annual_available_mwh"].sum())
        for b in ids
    ]
    buses["annual_available_net_mwh"] = (
        buses.annual_load_mwh - buses.annual_available_nd_mwh
    )
    return PreparedInputs(
        source,
        aggregate,
        buses,
        renewables,
        batteries,
        pd.DataFrame(gen_records),
        load_p,
        load_q,
        nd,
        alpha,
        dict(
            utility_sites_by_category=selected,
            utility_types=assignments,
            battery_sites=battery_sites,
        ),
    )


def summary(frame: pd.DataFrame, capacity: float) -> dict:
    record = input_summary(frame, capacity)
    if "dist_solar_mw" in frame:
        record["dist_solar_mwh"] = float(frame.dist_solar_mw.sum())
        for stat in ("mean", "min", "max"):
            record[f"dist_solar_mw_{stat}"] = float(
                getattr(frame.dist_solar_mw, stat)()
            )
    else:
        record["distributed_solar"] = "not modeled"
    return record


def write_json(path: Path, record: dict | list) -> None:
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


def publish(output: Path, review: Path) -> None:
    prepared = prepare()
    from .audit import audit_inputs

    audit = audit_inputs(prepared)
    provenance = json.loads((TOY / "provenance.json").read_text())
    for name in ("aggregate_inputs.csv", "input_summary.json", "devices.json"):
        if digest(TOY / name) != provenance["artifacts"][name]:
            raise ValueError(f"historical input artifact changed: {name}")
    # Refuse accidental replacement of either a prior candidate or its review.
    if output.exists() or review.exists():
        raise FileExistsError("use fresh output and review directories")
    output.mkdir(parents=True)
    review.mkdir(parents=True)
    write_json(review / "input_audit.json", audit)
    np.savez_compressed(
        output / "active_inputs.npz",
        load_p_mw=prepared.load_p_mw,
        load_q_mvar=prepared.load_q_mvar,
        nd_available_mw=prepared.nd_available_mw,
    )
    prepared.aggregate.to_csv(output / "aggregate_inputs.csv", index_label="time")
    for name, table in (
        ("buses", prepared.buses),
        ("renewables", prepared.renewables),
        ("batteries", prepared.batteries),
        ("generators", prepared.generators),
    ):
        table.to_csv(review / f"{name}.csv", index=False)
    toy = pd.read_csv(TOY / "aggregate_inputs.csv", index_col=0, parse_dates=True)
    yearly = {
        "toy": summary(toy, 6515),
        "tracy_scaled": summary(prepared.aggregate, 5000),
    }
    raw_summary = {f"{c}_mwh": float(prepared.source[c].sum()) for c in CHANNELS}
    raw_summary["calendar"] = "2021 fixed UTC-08:00"
    raw_summary["role"] = "source context, not the Case118 model input"
    write_json(review / "raw_source_summary.json", raw_summary)
    yearly["tracy_scaled"].update(
        storage_energy_mwh=float(prepared.batteries.capacity_mwh.sum()),
        storage_active_power_limit_mw=float(
            prepared.batteries.active_power_limit_mw.sum()
        ),
        storage_count=27,
        storage_e_over_p_hours=3.0,
        storage_energy_over_average_load_hours=4.0,
    )
    yearly["toy"].update(
        {
            k: v
            for k, v in json.loads((TOY / "input_summary.json").read_text()).items()
            if k.startswith("storage_")
        }
    )
    write_json(review / "annual_comparison.json", yearly)
    monthly = []
    for label, frame, capacity in (
        ("toy", toy, 6515),
        ("tracy_scaled", prepared.aggregate, 5000),
    ):
        for month, group in frame.groupby(frame.index.month):
            monthly.append(
                dict(scenario=label, month=int(month), **summary(group, capacity))
            )
    pd.DataFrame(monthly).to_csv(review / "monthly_comparison.csv", index=False)
    write_json(
        review / "shortfall_events.json",
        shortfall_events(
            prepared.aggregate.net_load_mw, 5000, prepared.aggregate.index
        ),
    )
    row_times = [
        "2021-01-01 00:00",
        "2021-02-19 01:00",
        "2021-12-18 00:00",
        "2021-12-21 23:00",
        "2021-12-31 23:00",
    ]
    checks = []
    for timestamp in row_times:
        t = pd.Timestamp(timestamp, tz="Etc/GMT+8")
        for c in CHANNELS:
            checks.append(
                dict(
                    time=str(t),
                    source_column=f"9q9wtp_{c}",
                    source_mw=float(prepared.source.loc[t, c]),
                    alpha=prepared.alpha,
                    prepared_mw=float(prepared.aggregate.loc[t, f"{c}_mw"]),
                )
            )
    pd.DataFrame(checks).to_csv(review / "source_row_checks.csv", index=False)
    manifest = dict(
        status="input_candidate_for_owner_review",
        numerical_execution_authorized=False,
        branch=subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=ROOT, text=True
        ).strip(),
        base_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        preparation_sha256=digest(Path(__file__)),
        code_sha256={p.name: digest(p) for p in HERE.glob("*.py")},
        source_path=str(SOURCE.relative_to(ROOT)),
        source_sha256=SOURCE_SHA,
        pglib_revision=SOURCE_REVISION,
        pglib_sha256=digest(SOURCE_CASE_PATH),
        calendar="2021 fixed UTC-08:00; hourly interval starts; no DST",
        delta_hours=1.0,
        python=platform.python_version(),
        numpy=np.__version__,
        pandas=pd.__version__,
        random_protocol="PCG64(42); sorted categories/sites; choice without replacement; categorical choice in wind_only,solar_only,both order; solar then wind multipliers; batteries load_only,no_load,with_load",
        alpha=prepared.alpha,
        generator_curvature_ratio=1 / 3,
        mapping=prepared.mapping,
        pending_operating_choices=PENDING,
        approved_operating_choices=APPROVED_OPERATING_CHOICES,
        load_device_ids=[
            f"load_bus_{b}"
            for b in prepared.buses.loc[prepared.buses.load_share > 0, "bus"]
        ],
        renewable_device_ids=prepared.renewables.device_id.tolist(),
        arrays={
            "load_p_mw": dict(
                shape=list(prepared.load_p_mw.shape),
                sha256=array_sha256(prepared.load_p_mw),
            ),
            "load_q_mvar": dict(
                shape=list(prepared.load_q_mvar.shape),
                sha256=array_sha256(prepared.load_q_mvar),
            ),
            "nd_available_mw": dict(
                shape=list(prepared.nd_available_mw.shape),
                sha256=array_sha256(prepared.nd_available_mw),
            ),
        },
        raw_artifacts={p.name: digest(p) for p in output.iterdir()},
        review_artifacts={p.name: digest(p) for p in review.iterdir()},
        toy_artifacts={
            n: digest(TOY / n)
            for n in ("aggregate_inputs.csv", "input_summary.json", "devices.json")
        },
    )
    write_json(review / "manifest.json", manifest)
    print(
        json.dumps(
            dict(
                review=str(review),
                output=str(output),
                alpha=prepared.alpha,
                utility_sites=prepared.mapping["utility_sites_by_category"],
                batteries=27,
                renewable_channels=len(prepared.renewables),
            ),
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=HERE / "results/stage_a_active_inputs"
    )
    parser.add_argument("--review-output", type=Path, default=HERE / "stage_a")
    args = parser.parse_args()
    publish(args.output, args.review_output)


if __name__ == "__main__":
    main()
