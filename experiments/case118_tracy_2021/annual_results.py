"""Read-only annual result loading and descriptive summaries; no solver imports."""

import gzip
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
LABELS = {"singlenode_dc": "Copper plate", "lossy_dc": "Lossy DC"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_results(directory=HERE / "results/stage_c"):
    directory = Path(directory)
    binding = json.loads((directory / "binding.json").read_text())
    root = json.loads((directory / "study-result.json").read_text())
    analysis = json.loads((directory / "analysis.json").read_text())
    if root["classification"] != "complete" or not analysis["complete"]:
        raise ValueError("annual pair is not complete and independently accepted")
    assert analysis["execution_context"] == binding["context"]
    assert len(root["accepted"]) == len(analysis["accepted"]) == 2
    manifest_path = HERE / "stage_a/manifest.json"
    assert sha(manifest_path) == binding["context"]["stage_a_manifest_sha256"]
    manifest = json.loads(manifest_path.read_text())
    tables = {}
    for name in ("generators", "batteries", "renewables", "buses"):
        path = HERE / f"stage_a/{name}.csv"
        assert sha(path) == manifest["review_artifacts"][path.name]
        tables[name] = pd.read_csv(path)
    path = HERE / "results/stage_a_active_inputs/aggregate_inputs.csv"
    assert sha(path) == manifest["raw_artifacts"][path.name]
    inputs = pd.read_csv(path, index_col=0, parse_dates=True)
    # Plotly must not convert source hours to the browser's DST timezone.
    inputs.index = inputs.index.tz_convert("Etc/GMT+8").tz_localize(None)
    expected = pd.date_range("2021-01-01", periods=8760, freq="h")
    assert inputs.index.equals(expected)
    network = HERE.parent / "case118_annual_hierarchy/source/pglib_opf_case118_ieee.m"
    assert sha(network) == manifest["pglib_sha256"]
    base_mva = float(
        re.search(r"mpc\.baseMVA\s*=\s*([\d.]+)", network.read_text()).group(1)
    )
    text = re.search(
        r"mpc\.branch\s*=\s*\[(.*?)\n\s*\];", network.read_text(), re.S
    ).group(1)
    branches = np.array(
        [
            [float(v) for v in line.split("%")[0].strip().rstrip(";").split()]
            for line in text.splitlines()
            if line.split("%")[0].strip()
        ]
    )
    branches = branches[branches[:, 10] > 0]
    runs = {}
    hashes = {
        name: sha(directory / name)
        for name in ("binding.json", "study-result.json", "analysis.json")
    }
    for n, formulation in enumerate(LABELS):
        arm_dir = directory / f"arm-{n:03d}"
        completion = json.loads((arm_dir / "completion.json").read_text())
        path = arm_dir / "result.json.gz"
        assert sha(path) == completion["result_sha256"]
        assert (
            completion
            == root["accepted"][n]["completion"]
            == analysis["accepted"][n]["completion"]
        )
        with gzip.open(path, "rt") as stream:
            record = json.load(stream)
        assert record["classification"] == completion["classification"] == "accepted"
        assert record["audit"]["passed"] and analysis["accepted"][n]["audit"]["passed"]
        assert record["arm"] == binding["study"]["arms"][n]
        assert record["arm"] == dict(
            window="Full year",
            start=0,
            stop=8760,
            rho=1 / 3,
            throughput=0.01,
            formulation=formulation,
        )
        for key, value in record["result"].items():
            if isinstance(value, list) and key not in ("storage_device_ids",):
                record["result"][key] = np.asarray(value)
        record["boundary_soc_mwh"] = np.asarray(record["boundary_soc_mwh"])
        record["renewable_available_mw"] = np.asarray(record["renewable_available_mw"])
        assert record["boundary_soc_mwh"].shape == (8761, len(tables["batteries"]))
        assert record["identities"]["storage"] == tables["batteries"].device_id.tolist()
        assert (
            record["identities"]["renewables"]
            == tables["renewables"].device_id.tolist()
        )
        assert (
            record["identities"]["generator_source_rows"]
            == tables["generators"].source_row.tolist()
        )
        record["execution"] = root["accepted"][n]
        runs[formulation] = record
        hashes[f"arm-{n:03d}/result.json.gz"] = completion["result_sha256"]
    sn, dc = runs.values()
    assert sn["identities"] == dc["identities"]
    for key in ("p_load", "q_load"):
        np.testing.assert_array_equal(sn["result"][key], dc["result"][key])
    np.testing.assert_array_equal(
        sn["renewable_available_mw"], dc["renewable_available_mw"]
    )
    np.testing.assert_allclose(
        sn["result"]["p_load"].sum(axis=1), inputs.load_mw, atol=1e-8, rtol=1e-12
    )
    np.testing.assert_allclose(
        sn["renewable_available_mw"].sum(axis=1),
        inputs[["solar_mw", "wind_mw", "dist_solar_mw"]].sum(axis=1),
        atol=1e-8,
        rtol=1e-12,
    )
    return dict(
        binding=binding,
        root=root,
        inputs=inputs,
        tables=tables,
        branches=branches,
        base_mva=base_mva,
        runs=runs,
        hashes=hashes,
    )


def aggregate(record):
    r = record["result"]
    return {
        "Dispatchable generation (MW)": r["Pg"].sum(axis=1),
        "Renewable dispatch (MW)": r["p_nd"].sum(axis=1),
        "Curtailment (MW)": r["curtailment"].sum(axis=1),
        "Battery power (MW; + discharge)": r["b"].sum(axis=1),
        "Battery charging (MW)": np.maximum(-r["b"], 0).sum(axis=1),
        "Battery discharging (MW)": np.maximum(r["b"], 0).sum(axis=1),
        "SoC at interval start (MWh)": record["boundary_soc_mwh"][:-1].sum(axis=1),
        "Load shedding (MW)": r["p_load_shed_total"],
        "Served load (MW)": r["p_load_served"].sum(axis=1),
    }


def calendar(values):
    """Fixed 2021 hourly sequence -> hour rows, day columns (no DST reshaping)."""
    values = np.asarray(values)
    if values.shape != (8760,):
        raise ValueError("calendar requires exactly 8760 interval values")
    return values.reshape(365, 24).T


def summaries(data):
    monthly, annual = [], []
    gen = data["tables"]["generators"]
    for formulation, record in data["runs"].items():
        r, agg = record["result"], aggregate(record)
        hourly = pd.DataFrame(agg, index=data["inputs"].index)
        hourly["Generator cost"] = (
            gen.c0.to_numpy()
            + gen.c1.to_numpy() * r["Pg"]
            + gen.c2.to_numpy() * r["Pg"] ** 2
        ).sum(axis=1)
        hourly["Storage cost"] = 0.01 * np.abs(r["b"]).sum(axis=1)
        hourly["Shedding cost"] = 20763.594 * r["p_load_shed_total"]
        hourly["DC loss proxy cost"] = (
            ((r["p_flows"] / data["base_mva"]) ** 2 * data["branches"][:, 2]).sum(
                axis=1
            )
            if formulation == "lossy_dc"
            else 0.0
        )
        month = (
            hourly.drop(columns=["SoC at interval start (MWh)"]).resample("MS").sum()
        )
        month.columns = [c.replace("MW", "MWh") for c in month.columns]
        month["SoC start (MWh)"] = (
            hourly["SoC at interval start (MWh)"].resample("MS").first()
        )
        ends = np.r_[np.flatnonzero(np.diff(data["inputs"].index.month)) + 1, 8760]
        month["SoC end (MWh)"] = record["boundary_soc_mwh"][ends].sum(axis=1)
        month["Formulation"] = LABELS[formulation]
        month.index.name = "Month"
        monthly.append(month.reset_index())
        execution = record["execution"]
        row = dict(
            formulation=LABELS[formulation],
            generation_mwh=float(r["Pg"].sum()),
            renewable_used_mwh=float(r["p_nd"].sum()),
            **record["audit"]["metrics"],
            **record["audit"]["costs"],
            objective=r["objective"],
            generation_peak_mw=float(r["Pg"].sum(axis=1).max()),
            near_zero_generation_hours=int((r["Pg"].sum(axis=1) < 1e-3).sum()),
            solver_seconds=record["solver_stats"]["solve_time"],
            iterations=record["solver_stats"]["num_iters"],
            compilation_seconds=record["solver_stats"]["compilation_time"],
            **record["timings"],
            archive_seconds=execution["completion"]["archive_seconds"],
            worker_seconds=execution["completion"]["worker_seconds"],
            supervisor_seconds=execution["supervision"]["wall_seconds"],
            peak_sampled_rss_mib=execution["supervision"]["peak_sampled_rss_mib"],
        )
        annual.append(row)
        cost_sum = (
            hourly[
                [
                    "Generator cost",
                    "Storage cost",
                    "Shedding cost",
                    "DC loss proxy cost",
                ]
            ]
            .sum()
            .sum()
        )
        np.testing.assert_allclose(cost_sum, r["objective"], atol=0.02, rtol=1e-10)
    return pd.DataFrame(annual).fillna({"dc_loss_cost": 0.0}), pd.concat(
        monthly, ignore_index=True
    )


def comparison(data):
    """Descriptive paired trajectory/congestion metrics, not controller tests."""
    sn, dc = (aggregate(data["runs"][f]) for f in LABELS)
    trajectories = {}
    for key in (
        "Dispatchable generation (MW)",
        "Battery power (MW; + discharge)",
        "SoC at interval start (MWh)",
    ):
        delta = dc[key] - sn[key]
        trajectories[key] = dict(
            correlation=float(np.corrcoef(sn[key], dc[key])[0, 1]),
            rms_difference=float(np.sqrt(np.mean(delta**2))),
            mean_difference=float(delta.mean()),
        )
    utilization = (
        np.abs(data["runs"]["lossy_dc"]["result"]["p_flows"]) / data["branches"][:, 5]
    )
    binding = utilization >= 0.99999
    return dict(
        trajectories=trajectories,
        congestion=dict(
            threshold=0.99999,
            hours_any_branch=int(binding.any(axis=1).sum()),
            distinct_branches=int(binding.any(axis=0).sum()),
            maximum_utilization=float(utilization.max()),
            top_branches=[
                dict(
                    row=int(j),
                    from_bus=int(data["branches"][j, 0]),
                    to_bus=int(data["branches"][j, 1]),
                    hours=int(binding[:, j].sum()),
                )
                for j in np.argsort(binding.sum(axis=0))[-5:][::-1]
            ],
        ),
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="fresh destination for compact descriptive evidence",
    )
    args = parser.parse_args()
    data = load_results()
    annual, monthly = summaries(data)
    args.output.mkdir(parents=True, exist_ok=False)
    summary = dict(
        execution_commit=data["binding"]["context"]["commit"],
        hashes=data["hashes"],
        annual=json.loads(annual.to_json(orient="records")),
        comparison=comparison(data),
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n"
    )
    monthly.to_csv(args.output / "monthly.csv", index=False)
