"""Read-only prepared-E3 viewer support; no model construction or solver imports.

Only finalized, supervised hard-target acceptances are exposed. Hash checks here
protect retained evidence; they do not repeat the runner's numerical replay.
Display calculations are descriptive, leave small values unclipped, and never
change the scientific acceptance decision.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
LABELS = {
    "singlenode_dc": "Copper plate · CLARABEL",
    "lossy_dc": "Lossy DC · CLARABEL",
    "socp": "SOCP · CLARABEL",
    "ac": "Full AC · IPOPT",
}
COLORS = {
    "singlenode_dc": "#777777",
    "lossy_dc": "#0072B2",
    "socp": "#E69F00",
    "ac": "#CC3377",
}
AGGREGATES = {
    "Generation": ("MW", "Pg"),
    "Renewable dispatch": ("MW", "p_nd"),
    "Available renewables": ("MW", "available"),
    "Curtailment": ("MW", "curtailment"),
    "Battery net power (+ discharge)": ("MW", "b"),
    "Battery charging": ("MW", "charging"),
    "Battery discharging": ("MW", "discharging"),
    "Battery SoC (all boundaries)": ("MWh", "soc_boundary"),
    "Gross load": ("MW", "p_load"),
    "Served load": ("MW", "p_load_served"),
    "Load shedding": ("MW", "p_load_shed"),
    "Net network real injection": ("MW", "p_net"),
    "Generator reactive power": ("MVAr", "Qg"),
    "Battery reactive power": ("MVAr", "b_q"),
    "Renewable reactive power": ("MVAr", "q_nd"),
    "Served reactive load": ("MVAr", "q_load_served"),
}
CHANNELS = {
    "Generators": {"Real power": ("MW", "Pg"), "Reactive power": ("MVAr", "Qg")},
    "Batteries": {
        "Real power (+ discharge)": ("MW", "b"),
        "SoC": ("MWh", "soc_boundary"),
        "SoC / capacity": ("%", "soc_percent"),
        "Reactive power": ("MVAr", "b_q"),
    },
    "Renewables": {
        "Real dispatch": ("MW", "p_nd"),
        "Available power": ("MW", "available"),
        "Curtailment": ("MW", "curtailment"),
        "Reactive power": ("MVAr", "q_nd"),
    },
    "Loads": {
        "Served real power": ("MW", "p_load_served"),
        "Shed real power": ("MW", "p_load_shed"),
        "Gross real power": ("MW", "p_load"),
        "Shedding fraction": ("%", "shed_percent"),
        "Served reactive power": ("MVAr", "q_load_served"),
    },
    "Buses": {
        "Real injection": ("MW", "p_net"),
        "Reactive injection": ("MVAr", "q_net"),
        "Voltage magnitude (SOCP: relaxed)": ("pu", "voltage"),
        "Voltage angle (AC only)": ("degrees", "Va_deg"),
    },
    "Branches": {
        "From-end real power (DC: oriented flow)": ("MW", "from_power"),
        "To-end real power": ("MW", "branch_p_to"),
        "From-end reactive power": ("MVAr", "branch_q_from"),
        "To-end reactive power": ("MVAr", "branch_q_to"),
        "From-end apparent power": ("MVA", "branch_s_from"),
        "To-end apparent power": ("MVA", "branch_s_to"),
        "Terminal real-power sum (SOCP: relaxed)": ("MW", "terminal_sum"),
    },
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def retained_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    require(
        path.is_relative_to(root.resolve()), f"Evidence path escapes run: {relative}"
    )
    return path


def case_label(arm: dict) -> str:
    leg = "depletion" if arm["leg"] == "depletion" else arm["leg"].replace("_", " ")
    return f"{arm['window']} · {leg} · hours [{arm['start']}, {arm['stop']})"


def load_results(directory: Path = HERE / "results/e3_prepared") -> dict:
    """Load the completed fresh cohort, checking manifests and matched inputs."""
    directory = Path(directory).resolve()
    binding = read_json(directory / "binding.json")
    progress = read_json(directory / "progress.json")
    require(binding["context"]["cohort"] == "prepared", "Not the prepared E3 cohort")
    require(
        progress["accepted_arms"] == 20,
        "This viewer requires the completed 20-arm cohort",
    )
    manifest_path = HERE / "stage_a/manifest.json"
    require(
        sha(manifest_path) == binding["context"]["stage_a_manifest_sha256"],
        "Stage A manifest mismatch",
    )
    manifest = read_json(manifest_path)
    tables = {}
    for name in ("generators", "batteries", "renewables", "buses"):
        path = HERE / f"stage_a/{name}.csv"
        require(
            sha(path) == manifest["review_artifacts"][path.name],
            f"Input table mismatch: {name}",
        )
        tables[name] = pd.read_csv(path)
    network = HERE.parent / "case118_annual_hierarchy/source/pglib_opf_case118_ieee.m"
    require(sha(network) == manifest["pglib_sha256"], "Network source mismatch")
    match = re.search(r"mpc\.branch\s*=\s*\[(.*?)\n\s*\];", network.read_text(), re.S)
    require(match is not None, "Missing MATPOWER branch table")
    branches = np.array(
        [
            [float(v) for v in line.split("%")[0].strip().rstrip(";").split()]
            for line in match.group(1).splitlines()
            if line.split("%")[0].strip()
        ]
    )
    branch_rows = np.flatnonzero(branches[:, 10] > 0)
    branches = branches[branch_rows]
    cases: dict[str, dict] = {}
    hashes = {}
    bound_arms = {arm["id"]: arm for arm in binding["study"]["arms"]}
    for item in progress["arms"]:
        arm = item["arm"]
        require(arm == bound_arms[arm["id"]], "Progress arm differs from binding")
        require(
            item["classification"] == "accepted", "Unaccepted arm in completed cohort"
        )
        path = retained_path(directory, item["accepted"]["path"])
        completion = read_json(path.parent / "completion.json")
        supervision = read_json(path.parent / "supervision.json")
        require(
            completion["classification"] == "accepted", "Completion is not accepted"
        )
        require(
            supervision["classification"] == "exited"
            and supervision["returncode"] == 0,
            "Attempt lacks successful finalized supervision",
        )
        require(bool(supervision.get("ended_utc")), "Missing supervision end time")
        require(
            {
                "result.json.gz",
                "native.json.gz",
                "preparation.json.gz",
                "request.json",
                "monitoring.json",
            }
            <= set(completion["artifacts"]),
            "Completion manifest is missing required retained artifacts",
        )
        for name, expected in completion["artifacts"].items():
            artifact = retained_path(path.parent, name)
            require(sha(artifact) == expected, f"Artifact hash mismatch: {artifact}")
            hashes[str(artifact.relative_to(directory))] = expected
        require(
            sha(path) == item["accepted"]["sha256"], "Accepted result hash mismatch"
        )
        with gzip.open(path, "rt") as stream:
            record = json.load(stream)
        require(
            record["classification"] == "accepted"
            and record["audit"]["passed"]
            and record["independent_audit"]["passed"],
            "Saved acceptance/audits disagree",
        )
        require(
            record["request"]["arm_id"] == arm["id"]
            and record["request"]["role"] != "target_free",
            "Result is not this hard-target arm",
        )
        require(
            record["request"] == read_json(path.parent / "request.json"),
            "Archived request mismatch",
        )
        require(
            record["execution_context"] == binding["context"],
            "Execution context mismatch",
        )
        # Keep small metadata; canonical vectors are unnecessary for plotting.
        iterations = record["native"].get(
            "num_iters", record["solver_stats"].get("iterations")
        )
        for key in ("native", "preparation_evidence", "logical_solution"):
            record.pop(key)
        for key, value in record["result"].items():
            if isinstance(value, list):
                record["result"][key] = np.asarray(value)
        record["boundary_soc_mwh"] = np.asarray(record["boundary_soc_mwh"])
        record["renewable_available_mw"] = np.asarray(record["renewable_available_mw"])
        identities = record["input_identities"]
        require(
            identities["storage"] == tables["batteries"].device_id.tolist(),
            "Battery identity mismatch",
        )
        require(
            identities["renewables"] == tables["renewables"].device_id.tolist(),
            "Renewable identity mismatch",
        )
        require(
            identities["loads"] == manifest["load_device_ids"], "Load identity mismatch"
        )
        require(
            record["result"]["storage_device_ids"].tolist() == identities["storage"],
            "Published battery order mismatch",
        )
        horizon = arm["stop"] - arm["start"]
        for key, count in (
            ("Pg", len(tables["generators"])),
            ("b", len(identities["storage"])),
            ("p_nd", len(identities["renewables"])),
            ("p_load", len(identities["loads"])),
        ):
            require(
                record["result"][key].shape == (horizon, count),
                f"Unexpected {key} shape",
            )
        require(
            record["boundary_soc_mwh"].shape
            == (horizon + 1, len(identities["storage"])),
            "SoC boundary shape mismatch",
        )
        np.testing.assert_allclose(
            record["boundary_soc_mwh"][0], arm["initial_soc_mwh"], atol=1e-8, rtol=0
        )
        np.testing.assert_allclose(
            record["boundary_soc_mwh"][-1], arm["terminal_soc_mwh"], atol=0.001, rtol=0
        )
        record.update(
            arm=arm,
            completion=completion,
            supervision=supervision,
            iterations=iterations,
            archive=str(path.relative_to(directory)),
        )
        label = case_label(arm)
        case = cases.setdefault(label, {})
        require(arm["formulation"] not in case, "Duplicate formulation in matched case")
        case[arm["formulation"]] = record
    require(
        len(cases) == 5 and sum(map(len, cases.values())) == 20,
        "Unexpected case inventory",
    )
    for case in cases.values():
        require(set(case) == set(LABELS), "Incomplete matched case")
        reference = case["singlenode_dc"]
        for record in case.values():
            require(
                record["input_identities"] == reference["input_identities"],
                "Unmatched device identities",
            )
            for key in ("p_load", "q_load"):
                np.testing.assert_array_equal(
                    record["result"][key], reference["result"][key]
                )
            np.testing.assert_array_equal(
                record["renewable_available_mw"], reference["renewable_available_mw"]
            )
            for key in ("initial_soc_mwh", "terminal_soc_mwh"):
                require(
                    record["arm"][key] == reference["arm"][key],
                    "Unmatched battery boundary targets",
                )
    return dict(
        binding=binding,
        cases=cases,
        tables=tables,
        branches=branches,
        branch_rows=branch_rows,
        hashes=hashes,
        directory=directory,
    )


def channel(
    record: dict, key: str, capacities: np.ndarray | None = None
) -> np.ndarray | None:
    """Engineering arrays; None means genuinely unavailable, not zero."""
    result = record["result"]
    if key == "available":
        return record["renewable_available_mw"]
    if key == "soc_boundary":
        return record["boundary_soc_mwh"]
    if key == "soc_percent":
        return 100 * record["boundary_soc_mwh"] / capacities
    if key in ("charging", "discharging"):
        return np.maximum((-1 if key == "charging" else 1) * result["b"], 0)
    if key == "shed_percent":
        return 100 * result["load_shed_fraction"]
    if key == "voltage":
        return result.get("Vm", result.get("Vm_relaxed"))
    if key == "from_power":
        return result.get("branch_p_from", result.get("p_flows"))
    if key == "terminal_sum":
        return (
            result["branch_p_from"] + result["branch_p_to"]
            if "branch_p_from" in result
            else None
        )
    return result.get(key)


def time_coordinates(
    record: dict, length: int, clock: str
) -> np.ndarray | pd.DatetimeIndex:
    if clock == "Global input hour":
        return np.arange(record["arm"]["start"], record["arm"]["start"] + length)
    if clock == "2021 timestamp (fixed UTC−08:00)":
        return pd.date_range(
            pd.Timestamp("2021-01-01") + pd.Timedelta(hours=record["arm"]["start"]),
            periods=length,
            freq="h",
        )
    return np.arange(length)


def traces(
    record: dict,
    values: np.ndarray,
    clock: str,
    interval: tuple[int, int],
    boundary: bool,
):
    """Power is constant on hourly intervals; SoC has T+1 actual boundaries."""
    lo, hi = interval
    x = time_coordinates(record, len(values) if boundary else len(values) + 1, clock)
    selected = (
        values[lo : hi + 1]
        if boundary
        else np.concatenate([values[lo:hi], values[hi - 1 : hi]])
    )
    return x[lo : hi + 1], selected


def asset_labels(data: dict, family: str) -> dict[str, int]:
    tables = data["tables"]
    if family in ("Generators", "Batteries", "Renewables"):
        table = tables[
            {
                "Generators": "generators",
                "Batteries": "batteries",
                "Renewables": "renewables",
            }[family]
        ]
        return {str(row.device_id): i for i, row in table.iterrows()}
    if family == "Loads":
        first = next(iter(next(iter(data["cases"].values())).values()))
        return {name: i for i, name in enumerate(first["input_identities"]["loads"])}
    if family == "Buses":
        return {f"Bus {int(bus)}": i for i, bus in enumerate(tables["buses"].bus)}
    return {
        f"Branch row {row}: {int(branch[0])} → {int(branch[1])}": i
        for i, (row, branch) in enumerate(zip(data["branch_rows"], data["branches"]))
    }


def summary_table(data: dict) -> pd.DataFrame:
    rows = []
    for case_name, case in data["cases"].items():
        for formulation, record in case.items():
            r = record["result"]
            rows.append(
                {
                    "Case": case_name,
                    "Formulation / solver": LABELS[formulation],
                    "Physical objective": r["objective"],
                    **record["named_costs"],
                    "Generation (MWh)": float(r["Pg"].sum()),
                    "Battery throughput (MWh)": float(np.abs(r["b"]).sum()),
                    "Curtailment (MWh)": float(r["curtailment"].sum()),
                    "Unserved energy (MWh)": r["energy_not_served"],
                    "Worker (s)": record["supervision"]["wall_seconds"],
                    "Peak sampled RSS (MiB)": record["supervision"][
                        "peak_sampled_rss_mib"
                    ],
                    "Native iterations": record["iterations"],
                    "Objective assembly": record["request"]["objective_assembly"],
                    "Cycling warning": record["independent_audit"]
                    .get("economics", {})
                    .get("cycling_warning", False),
                    "Archive": record["archive"],
                }
            )
    return pd.DataFrame(rows)


def plot_panels(
    case: dict,
    specifications: list[tuple],
    formulations: list[str],
    clock: str,
    interval: tuple[int, int],
    capacities: np.ndarray,
    reference: str | None = None,
):
    """One quantity/asset per panel; formulation colors are stable across charts."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    columns = min(2, len(specifications))
    rows = (len(specifications) + columns - 1) // columns
    figure = make_subplots(
        rows=rows,
        cols=columns,
        subplot_titles=[s[0] for s in specifications],
        vertical_spacing=min(0.08, 0.25 / rows),
    )
    unavailable = []
    legend_seen = set()
    for panel, (title, unit, key, asset) in enumerate(specifications):
        row, col = divmod(panel, columns)
        for formulation in formulations:
            record = case[formulation]
            values = channel(record, key, capacities)
            # Copper plate p_net is a system scalar, never a bus trajectory.
            if values is None or (asset is not None and values.ndim != 2):
                unavailable.append(f"{title}: {LABELS[formulation]}")
                continue
            values = (
                values[:, asset]
                if asset is not None
                else (values.sum(axis=1) if values.ndim == 2 else values)
            )
            if reference is not None:
                base = channel(case[reference], key, capacities)
                if base is None or (asset is not None and base.ndim != 2):
                    unavailable.append(f"{title}: reference unavailable")
                    continue
                base = (
                    base[:, asset]
                    if asset is not None
                    else (base.sum(axis=1) if base.ndim == 2 else base)
                )
                values = values - base
            boundary = key in ("soc_boundary", "soc_percent")
            x, y = traces(record, values, clock, interval, boundary)
            figure.add_trace(
                go.Scatter(
                    x=x,
                    y=y,
                    name=LABELS[formulation],
                    legendgroup=formulation,
                    showlegend=formulation not in legend_seen,
                    mode="lines+markers" if boundary else "lines",
                    line=dict(
                        color=COLORS[formulation],
                        width=2,
                        shape="linear" if boundary else "hv",
                    ),
                    marker=dict(size=4),
                ),
                row=row + 1,
                col=col + 1,
            )
            legend_seen.add(formulation)
        figure.update_yaxes(
            title_text=("Δ " if reference else "") + unit, row=row + 1, col=col + 1
        )
        figure.update_xaxes(title_text=clock, row=row + 1, col=col + 1)
    figure.update_layout(
        template="plotly_white",
        height=310 * rows + 85,
        hovermode="x unified",
        legend=dict(orientation="h", y=1.08),
        margin=dict(t=100, b=60),
    )
    return figure, sorted(set(unavailable))
