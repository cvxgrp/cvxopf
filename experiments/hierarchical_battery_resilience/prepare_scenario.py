"""Regenerate the checked-in M17 scenario from the authorized Tracy source.

The normative experiment loads the committed prepared arrays and does not
require the raw composite. This script is the optional provenance path for
maintainers who possess the source file with the recorded SHA-256 digest.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys

import cvxpy
import cyipopt
import numpy as np
import pandas as pd

from cvxopf.problem import OPFOptions
from cvxopf.testcases import case9

from experiments.battery_terminal.devices import (
    make_dispatchable_generators,
    make_nondispatchable_units,
)
from experiments.battery_terminal.scenario import (
    DEFAULT_SOURCE_TO_CASE_SCALE,
    LOAD_FRACTIONS,
    LOAD_Q_OVER_P,
    REPRESENTATIVE_WINDOWS,
    RESOURCE_FRACTIONS,
    ScenarioConfig,
    generate_scenario,
    read_source_data,
    select_representative_window,
)
from experiments.hierarchical_battery_resilience.scenario import (
    SCENARIO_DIR,
    array_sha256,
    file_sha256,
)


SOURCE_SHA256 = "45e11f061d736741b18334aea0e9525c355c1a13068c291c1db6ed2e614b1b6f"
LOAD_IDS = ("load_bus_5", "load_bus_7", "load_bus_9")
STORAGE_ID = "battery_bus_7"


def _source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_frame(frame: pd.DataFrame, path: Path) -> None:
    output = frame.copy()
    output.index = output.index.map(lambda value: value.isoformat())
    output.index.name = "time"
    output.to_csv(path, float_format="%.17g", lineterminator="\n")


def prepare(source_path: Path, output_dir: Path = SCENARIO_DIR) -> dict:
    """Regenerate the frozen high-stress arrays and manifest."""
    digest = _source_sha256(source_path)
    if digest != SOURCE_SHA256:
        raise ValueError(
            f"Tracy source SHA-256 mismatch: expected {SOURCE_SHA256}, got {digest}"
        )
    source = read_source_data(source_path)
    config = ScenarioConfig()
    all_scenarios = {
        name: generate_scenario(
            select_representative_window(source, name), config
        )
        for name in REPRESENTATIVE_WINDOWS
    }
    scenario = all_scenarios["high"]
    nondispatchable = make_nondispatchable_units(
        [item.df_nd for item in all_scenarios.values()]
    )
    generators = make_dispatchable_generators()

    load_p = scenario.df_P.loc[:, [5, 7, 9]].copy()
    load_q = scenario.df_Q.loc[:, [5, 7, 9]].copy()
    load_p.columns = LOAD_IDS
    load_q.columns = LOAD_IDS
    frames = {
        "load_p": load_p,
        "load_q": load_q,
        "nondispatchable": scenario.df_nd,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "load_p": "load_p.csv",
        "load_q": "load_q.csv",
        "nondispatchable": "nondispatchable.csv",
    }
    for name, frame in frames.items():
        _write_frame(frame, output_dir / files[name])

    options = OPFOptions()
    network_case = case9()
    manifest = {
        "schema_version": 1,
        "scenario_name": "tracy_high_96h_v1",
        "interpretation": "sustained energy-deficit window",
        "start": scenario.df_P.index[0].isoformat(),
        "end": scenario.df_P.index[-1].isoformat(),
        "horizon_steps": len(scenario.df_P),
        "delta_hours": 1.0,
        "nominal_ac_window_steps": 5,
        "source": {
            "description": "BM-authored Tracy reduced-order composite derived from public sources",
            "redistribution": "Derived scenario arrays approved for redistribution by the project owner",
            "raw_file_required": False,
            "raw_sha256": SOURCE_SHA256,
            "raw_columns": [
                "9q9wtp_solar",
                "9q9wtp_wind",
                "9q9wtp_dist_solar",
                "9q9wtp_load",
            ],
        },
        "transformation": {
            "source_to_case_scale": DEFAULT_SOURCE_TO_CASE_SCALE,
            "load_scale": config.load_scale,
            "load_shift_mw": config.load_shift_mw,
            "solar_scale": config.solar_scale,
            "wind_scale": config.wind_scale,
            "dist_solar_scale": config.dist_solar_scale,
            "spatial_noise_std": config.spatial_noise_std,
            "random_seed": config.random_seed,
            "load_fractions": {str(key): value for key, value in LOAD_FRACTIONS.items()},
            "load_q_over_p": {str(key): value for key, value in LOAD_Q_OVER_P.items()},
            "resource_fractions": {
                resource: {str(key): value for key, value in fractions.items()}
                for resource, fractions in RESOURCE_FRACTIONS.items()
            },
            "renewable_rating_multiplier": 1.10,
            "renewable_ratings_sized_across": list(REPRESENTATIVE_WINDOWS),
        },
        "case": {
            "factory": "cvxopf.testcases.case9",
            "base_mva": 100.0,
            "outer_formulation": "lossy_dc",
            "inner_formulation": "ac",
            "options": asdict(options),
            "bus_array_sha256": array_sha256(network_case["bus"]),
            "branch_array_sha256": array_sha256(network_case["branch"]),
        },
        "generators": [asdict(unit) for unit in generators],
        "loads": [
            {
                "device_id": device_id,
                "bus": bus,
                "p_load_mw_static_fallback": 0.0,
                "q_load_mvar_static_fallback": None,
                "shedding_cost_per_mwh": None,
                "max_shed_fraction": 1.0,
            }
            for device_id, bus in zip(LOAD_IDS, (5, 7, 9), strict=True)
        ],
        "nondispatchable": [asdict(unit) for unit in nondispatchable],
        "storage": [
            {
                "device_id": STORAGE_ID,
                "bus": 7,
                "apparent_power_rating_mva": 150.0,
                "capacity_mwh": 1000.0,
                "initial_soc_mwh": 500.0,
                "aging_weight": 0.01,
                "outer_terminal_soc_mwh": 500.0,
                "outer_terminal_constraint": "equality",
            }
        ],
        "hvdc": [],
        "policies": {
            "outer": ["frozen", "replan_every_step"],
            "inner_terminal": ["hard_equality", "quadratic_soft"],
            "quadratic_soft_weight": 0.05,
            "automatic_fallback": False,
            "perfect_forecast": True,
            "replans_retain_global_terminal_boundary": True,
            "global_terminal_boundary": 96,
            "accepted_statuses": ["optimal", "optimal_inaccurate"],
        },
        "solver": {
            "outer": "CVXPY default convex solver",
            "inner": "IPOPT through CVXPY DNLP",
            "inner_initialization": "project default flat start",
        },
        "acceptance_tolerances": {
            "soc_recurrence_mwh_abs": 1e-4,
            "terminal_soc_mwh_abs": 1e-3,
            "soft_terminal_cost_abs": 1e-6,
            "ac_active_balance_pu_abs": 1e-6,
            "ac_reactive_balance_pu_abs": 1e-6,
            "dc_injection_reporting_mw_abs": 1e-4,
            "dc_nodal_balance_pu_abs": 1e-6,
            "voltage_bound_pu_abs": 1e-6,
            "branch_mva_abs": 1e-4,
            "branch_normalized_squared_residual": 1e-7,
        },
        "preparation_environment": {
            "python": ".".join(map(str, sys.version_info[:3])),
            "cvxopf": version("cvxopf"),
            "cvxpy": cvxpy.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "cyipopt": cyipopt.__version__,
        },
        "arrays": {},
    }
    for name, frame in frames.items():
        path = output_dir / files[name]
        persisted = pd.read_csv(path).drop(columns="time").to_numpy()
        manifest["arrays"][name] = {
            "file": files[name],
            "columns": list(frame.columns),
            "shape": list(frame.shape),
            "array_sha256": array_sha256(persisted),
            "file_sha256": file_sha256(path),
        }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source",
        type=Path,
        help="authorized Tracy source CSV with the manifest-recorded SHA-256",
    )
    parser.add_argument("--output", type=Path, default=SCENARIO_DIR)
    args = parser.parse_args()
    prepare(args.source, args.output)


if __name__ == "__main__":
    main()
