"""Load and verify the frozen M17 Tracy-derived scenario artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pandas as pd

from cvxopf.problem import (
    DispatchableGenerator,
    HVDCLink,
    Load,
    NondispatchableUnit,
    OPFOptions,
    StorageUnitIdeal,
)
from cvxopf.testcases import case9


SCENARIO_DIR = Path(__file__).with_name("prepared_scenario")
MANIFEST_PATH = SCENARIO_DIR / "manifest.json"

# The manifest's numeric-array hashes record the original preparation
# environment and remain unchanged because S3/S3b provenance binds that exact
# manifest. These hashes lock the explicitly round-tripped CSV interpretation
# used by clean checkouts on every supported platform.
_ROUND_TRIP_ARRAY_SHA256 = {
    "load_p": "136f926f73eb7bade8880ec260c1d175957588303f3c180d5777556e11e37554",
    "load_q": "ce6a0b5269f186b0fe65a4b1069df97556b7a19cab5ec4e6fe17d3f1dc2d6929",
    "nondispatchable": (
        "e2f49a4606614b29b802a7341d72ef7442671481c05d93599eb64b2ec163b510"
    ),
}


@dataclass(frozen=True)
class FrozenControlConfig:
    """Typed horizon, policy, and acceptance configuration for S2."""

    horizon_steps: int
    delta_hours: float
    nominal_ac_window_steps: int
    outer_policies: tuple[str, ...]
    inner_terminal_policies: tuple[str, ...]
    quadratic_soft_weight: float
    accepted_statuses: tuple[str, ...]
    automatic_fallback: bool
    perfect_forecast: bool
    global_terminal_boundary: int
    replans_retain_global_terminal_boundary: bool
    acceptance_tolerances: Mapping[str, float]


@dataclass(frozen=True)
class FrozenScenario:
    """Verified build-ready scenario and its machine-readable provenance."""

    manifest: dict
    case: dict
    options: OPFOptions
    generators: tuple[DispatchableGenerator, ...]
    loads: tuple[Load, ...]
    nondispatchable: tuple[NondispatchableUnit, ...]
    storage: tuple[StorageUnitIdeal, ...]
    hvdc: tuple[HVDCLink, ...]
    control: FrozenControlConfig
    df_load_p: pd.DataFrame
    df_load_q: pd.DataFrame
    df_nd: pd.DataFrame


def array_sha256(values: np.ndarray) -> str:
    """Hash a numeric array using a documented little-endian float64 form."""
    array = np.ascontiguousarray(values, dtype="<f8")
    header = f"float64-le|shape={array.shape}|".encode()
    return hashlib.sha256(header + array.tobytes(order="C")).hexdigest()


def file_sha256(path: Path) -> str:
    """Return the SHA-256 digest of one file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_frame(path: Path) -> pd.DataFrame:
    # The default pandas parser can differ by a few float64 ULPs across
    # versions and platforms. Round-trip parsing is the portable contract for
    # these checked-in decimal artifacts.
    frame = pd.read_csv(path, float_precision="round_trip")
    if "time" not in frame:
        raise ValueError(f"Prepared scenario file has no time column: {path}")
    frame["time"] = pd.to_datetime(frame["time"])
    return frame.set_index("time")


def _materialize_generators(
    specifications: list[dict],
) -> tuple[DispatchableGenerator, ...]:
    units = []
    for specification in specifications:
        values = dict(specification)
        if values["cost_coeffs"] is not None:
            values["cost_coeffs"] = tuple(values["cost_coeffs"])
        if values["cost_points"] is not None:
            values["cost_points"] = tuple(
                tuple(point) for point in values["cost_points"]
            )
        units.append(DispatchableGenerator(**values))
    return tuple(units)


def _materialize_loads(specifications: list[dict]) -> tuple[Load, ...]:
    return tuple(
        Load(
            bus=specification["bus"],
            p_load_mw=specification["p_load_mw_static_fallback"],
            device_id=specification["device_id"],
            q_load_mvar=specification["q_load_mvar_static_fallback"],
            shedding_cost_per_mwh=specification["shedding_cost_per_mwh"],
            max_shed_fraction=specification["max_shed_fraction"],
        )
        for specification in specifications
    )


def _materialize_nondispatchable(
    specifications: list[dict],
) -> tuple[NondispatchableUnit, ...]:
    return tuple(
        NondispatchableUnit(**specification)
        for specification in specifications
    )


def _materialize_storage(
    specifications: list[dict],
) -> tuple[StorageUnitIdeal, ...]:
    return tuple(
        StorageUnitIdeal(
            bus=specification["bus"],
            apparent_power_rating=specification[
                "apparent_power_rating_mva"
            ],
            capacity=specification["capacity_mwh"],
            initial_soc=specification["initial_soc_mwh"],
            aging_weight=specification["aging_weight"],
            terminal_soc=specification["outer_terminal_soc_mwh"],
            terminal_constraint=specification[
                "outer_terminal_constraint"
            ],
            device_id=specification["device_id"],
        )
        for specification in specifications
    )


def _materialize_hvdc(specifications: list[dict]) -> tuple[HVDCLink, ...]:
    links = []
    for specification in specifications:
        values = dict(specification)
        if "cost_coeffs" in values:
            values["cost_coeffs"] = tuple(values["cost_coeffs"])
        links.append(HVDCLink(**values))
    return tuple(links)


def load_frozen_scenario(
    directory: str | Path = SCENARIO_DIR,
    *,
    case_factory: Callable[[], dict] = case9,
) -> FrozenScenario:
    """Load the normative scenario and reject any artifact drift."""
    root = Path(directory)
    manifest = json.loads((root / "manifest.json").read_text())
    frames = {
        name: _read_frame(root / specification["file"])
        for name, specification in manifest["arrays"].items()
    }

    expected_index = None
    for name, frame in frames.items():
        specification = manifest["arrays"][name]
        if list(frame.columns) != specification["columns"]:
            raise ValueError(f"Prepared {name} column order does not match manifest")
        if frame.shape != tuple(specification["shape"]):
            raise ValueError(f"Prepared {name} shape does not match manifest")
        if not np.isfinite(frame.to_numpy(dtype=float)).all():
            raise ValueError(f"Prepared {name} contains nonfinite values")
        if array_sha256(frame.to_numpy()) != _ROUND_TRIP_ARRAY_SHA256[name]:
            raise ValueError(f"Prepared {name} numeric-array hash mismatch")
        if file_sha256(root / specification["file"]) != specification["file_sha256"]:
            raise ValueError(f"Prepared {name} file hash mismatch")
        if expected_index is None:
            expected_index = frame.index
        elif not frame.index.equals(expected_index):
            raise ValueError("Prepared scenario time indices are not aligned")

    if expected_index is None:
        raise ValueError("Prepared scenario manifest contains no arrays")
    if len(expected_index) != manifest["horizon_steps"]:
        raise ValueError("Prepared scenario horizon does not match manifest")
    if expected_index[0].isoformat() != manifest["start"]:
        raise ValueError("Prepared scenario start timestamp does not match manifest")
    if expected_index[-1].isoformat() != manifest["end"]:
        raise ValueError("Prepared scenario end timestamp does not match manifest")
    elapsed = expected_index.to_series().diff().dropna().dt.total_seconds() / 3600
    if not np.allclose(elapsed, manifest["delta_hours"]):
        raise ValueError("Prepared scenario cadence does not match manifest")

    if manifest["case"]["factory"] != "cvxopf.testcases.case9":
        raise ValueError("Frozen scenario requires cvxopf.testcases.case9")
    network_case = case_factory()
    if float(network_case["baseMVA"]) != manifest["case"]["base_mva"]:
        raise ValueError("Frozen case9 baseMVA does not match manifest")
    if (
        array_sha256(network_case["bus"])
        != manifest["case"]["bus_array_sha256"]
    ):
        raise ValueError("Frozen case9 bus-array hash mismatch")
    if (
        array_sha256(network_case["branch"])
        != manifest["case"]["branch_array_sha256"]
    ):
        raise ValueError("Frozen case9 branch-array hash mismatch")

    policies = manifest["policies"]
    generators = _materialize_generators(manifest["generators"])
    loads = _materialize_loads(manifest["loads"])
    nondispatchable = _materialize_nondispatchable(
        manifest["nondispatchable"]
    )
    storage = _materialize_storage(manifest["storage"])
    hvdc = _materialize_hvdc(manifest["hvdc"])
    if [unit.device_id for unit in loads] != list(frames["load_p"].columns):
        raise ValueError("Frozen load identity does not match active trajectories")
    if list(frames["load_q"].columns) != list(frames["load_p"].columns):
        raise ValueError("Frozen active and reactive load identities differ")
    if [unit.device_id for unit in nondispatchable] != list(
        frames["nondispatchable"].columns
    ):
        raise ValueError(
            "Frozen nondispatchable identity does not match trajectories"
        )
    storage_ids = [unit.device_id for unit in storage]
    if (
        any(device_id is None or not device_id.strip() for device_id in storage_ids)
        or len(set(storage_ids)) != len(storage_ids)
    ):
        raise ValueError("Frozen M17 storage identity must be explicit and unique")
    if policies["global_terminal_boundary"] != manifest["horizon_steps"]:
        raise ValueError("Frozen global terminal boundary must equal the horizon")
    if not policies["replans_retain_global_terminal_boundary"]:
        raise ValueError("Frozen replans must retain the global terminal boundary")

    control = FrozenControlConfig(
        horizon_steps=manifest["horizon_steps"],
        delta_hours=manifest["delta_hours"],
        nominal_ac_window_steps=manifest["nominal_ac_window_steps"],
        outer_policies=tuple(policies["outer"]),
        inner_terminal_policies=tuple(policies["inner_terminal"]),
        quadratic_soft_weight=policies["quadratic_soft_weight"],
        accepted_statuses=tuple(policies["accepted_statuses"]),
        automatic_fallback=policies["automatic_fallback"],
        perfect_forecast=policies["perfect_forecast"],
        global_terminal_boundary=policies["global_terminal_boundary"],
        replans_retain_global_terminal_boundary=policies[
            "replans_retain_global_terminal_boundary"
        ],
        acceptance_tolerances=MappingProxyType(
            dict(manifest["acceptance_tolerances"])
        ),
    )

    return FrozenScenario(
        manifest=manifest,
        case=network_case,
        options=OPFOptions(**manifest["case"]["options"]),
        generators=generators,
        loads=loads,
        nondispatchable=nondispatchable,
        storage=storage,
        hvdc=hvdc,
        control=control,
        df_load_p=frames["load_p"],
        df_load_q=frames["load_q"],
        df_nd=frames["nondispatchable"],
    )
