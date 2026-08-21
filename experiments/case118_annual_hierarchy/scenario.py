"""Deterministic annual profiles and electrical-distance siting for S0."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Mapping

import numpy as np
import pandas as pd

from cvxopf import Load, NondispatchableUnit, StorageUnitIdeal

from experiments.case118_annual_hierarchy.pglib_case import (
    loads_from_pglib_case,
)


HOURS_PER_YEAR = 8760
PROFILE_YEAR = 2025
DISTANCE_FLOOR_PU = 1e-6
PROFILE_QUANTIZATION_DECIMALS = 9


@dataclass(frozen=True)
class AnnualProfiles:
    """Normalized, deterministic profiles on one timezone-stable index."""

    index: pd.DatetimeIndex
    load_multiplier: np.ndarray
    wind_capacity_factor: np.ndarray
    solar_capacity_factor: np.ndarray

    def hashes(self) -> dict[str, str]:
        """Return stable hashes for the three prepared numeric channels."""
        return {
            "load_multiplier": _array_sha256(self.load_multiplier),
            "wind_capacity_factor": _array_sha256(
                self.wind_capacity_factor
            ),
            "solar_capacity_factor": _array_sha256(
                self.solar_capacity_factor
            ),
        }


@dataclass(frozen=True)
class SitingResult:
    """Deterministic storage clusters and renewable sites."""

    storage_buses: tuple[int, ...]
    cluster_by_external_bus: tuple[int, ...]
    solar_bus: int
    wind_bus: int
    distance_sha256: str


@dataclass(frozen=True)
class PilotParameters:
    """One predeclared S0 sizing point."""

    renewable_energy_share: float
    storage_power_fraction_of_peak: float
    storage_duration_hours: float


@dataclass(frozen=True)
class MaterializedPilot:
    """Build-ready explicit fleets and aligned annual trajectories."""

    parameters: PilotParameters
    profiles: AnnualProfiles
    siting: SitingResult
    loads: tuple[Load, ...]
    df_load_p: pd.DataFrame
    df_load_q: pd.DataFrame
    nondispatchable: tuple[NondispatchableUnit, ...]
    df_nd: pd.DataFrame
    storage: tuple[StorageUnitIdeal, ...]


PILOT_GRID = tuple(
    PilotParameters(renewable_share, storage_power, duration)
    for renewable_share in (0.15, 0.30)
    for storage_power in (0.05, 0.10)
    for duration in (4.0, 8.0)
)


def _array_sha256(values: np.ndarray) -> str:
    array = np.ascontiguousarray(values, dtype="<f8")
    header = f"float64-le|shape={array.shape}|".encode()
    return hashlib.sha256(header + array.tobytes(order="C")).hexdigest()


def build_annual_profiles() -> AnnualProfiles:
    """Build one deterministic, nonleap synthetic year in UTC."""
    index = pd.date_range(
        f"{PROFILE_YEAR}-01-01", periods=HOURS_PER_YEAR, freq="h", tz="UTC"
    )
    hour = np.arange(HOURS_PER_YEAR, dtype=float)
    hour_of_day = index.hour.to_numpy(dtype=float)
    day_of_year = index.dayofyear.to_numpy(dtype=float)
    weekday = index.dayofweek.to_numpy() < 5

    seasonal = 0.10 * np.cos(2.0 * np.pi * (day_of_year - 18.0) / 365.0)
    diurnal = 0.11 * np.cos(2.0 * np.pi * (hour_of_day - 18.0) / 24.0)
    workweek = np.where(weekday, 0.035, -0.055)
    weather = (
        0.025 * np.sin(2.0 * np.pi * hour / 173.0 + 0.4)
        + 0.018 * np.sin(2.0 * np.pi * hour / 619.0 + 1.7)
        + 0.010 * np.cos(2.0 * np.pi * hour / 41.0)
    )
    load = 1.0 + seasonal + diurnal + workweek + weather
    load /= np.mean(load)

    daylight = np.maximum(
        np.sin(np.pi * (hour_of_day - 6.0) / 12.0), 0.0
    )
    solar_season = 0.78 + 0.22 * np.cos(
        2.0 * np.pi * (day_of_year - 172.0) / 365.0
    )
    solar_weather = np.clip(
        0.86
        + 0.10 * np.sin(2.0 * np.pi * hour / 113.0 + 0.8)
        + 0.06 * np.cos(2.0 * np.pi * hour / 47.0),
        0.55,
        1.0,
    )
    solar = np.clip(daylight * solar_season * solar_weather, 0.0, 1.0)

    wind = np.clip(
        0.43
        + 0.16 * np.sin(2.0 * np.pi * hour / 149.0 + 2.1)
        + 0.10 * np.sin(2.0 * np.pi * hour / 509.0 + 0.2)
        + 0.06 * np.cos(2.0 * np.pi * hour / 31.0),
        0.05,
        0.90,
    )
    load = np.round(load, decimals=PROFILE_QUANTIZATION_DECIMALS)
    solar = np.round(solar, decimals=PROFILE_QUANTIZATION_DECIMALS)
    wind = np.round(wind, decimals=PROFILE_QUANTIZATION_DECIMALS)
    for values in (load, solar, wind):
        if values.shape != (HOURS_PER_YEAR,) or not np.isfinite(values).all():
            raise RuntimeError("annual profile construction invariant failed")
    if np.min(load) <= 0.0:
        raise RuntimeError("annual load multiplier must remain positive")
    return AnnualProfiles(index, load, wind, solar)


def electrical_distance_matrix(
    case: Mapping[str, object],
) -> tuple[np.ndarray, np.ndarray]:
    """Return external bus IDs and all-pairs frozen-topology distances."""
    bus = np.asarray(case["bus"], dtype=float)
    branch = np.asarray(case["branch"], dtype=float)
    external_ids = bus[:, 0].astype(int)
    ext_to_int = {external: index for index, external in enumerate(external_ids)}
    if len(ext_to_int) != len(external_ids):
        raise ValueError("electrical-distance topology has duplicate bus IDs")
    distance = np.full((len(external_ids), len(external_ids)), np.inf)
    np.fill_diagonal(distance, 0.0)
    for row in branch:
        if row[10] == 0:
            continue
        reactance = float(row[3])
        if not np.isfinite(reactance):
            raise ValueError("active branch reactance must be finite")
        weight = max(abs(reactance), DISTANCE_FLOOR_PU)
        start = ext_to_int[int(row[0])]
        end = ext_to_int[int(row[1])]
        distance[start, end] = min(distance[start, end], weight)
        distance[end, start] = min(distance[end, start], weight)
    for intermediate in range(len(external_ids)):
        distance = np.minimum(
            distance,
            distance[:, [intermediate]] + distance[[intermediate], :],
        )
    if not np.isfinite(distance).all():
        raise ValueError("active PGLib topology must be connected")
    if np.any(distance < 0.0):
        raise ValueError("electrical distances must be nonnegative")
    return external_ids, distance


def _lowest_bus_argmin(values: np.ndarray, external_ids: np.ndarray) -> int:
    minimum = np.min(values)
    candidates = np.flatnonzero(np.isclose(values, minimum, rtol=0.0, atol=1e-14))
    return int(candidates[np.argmin(external_ids[candidates])])


def deterministic_siting(
    case: Mapping[str, object], *, storage_count: int = 4
) -> SitingResult:
    """Select load-weighted k-medoids and two reproducible renewable sites."""
    if storage_count <= 0:
        raise ValueError("storage_count must be positive")
    external_ids, distance = electrical_distance_matrix(case)
    bus = np.asarray(case["bus"], dtype=float)
    weights = np.hypot(bus[:, 2], bus[:, 3])
    if not np.isfinite(weights).all() or np.sum(weights) <= 0.0:
        raise ValueError("load weights must be finite with positive total")

    medoids = [_lowest_bus_argmin(distance @ weights, external_ids)]
    while len(medoids) < storage_count:
        nearest = np.min(distance[:, medoids], axis=1)
        scores = weights * nearest
        scores[medoids] = -np.inf
        maximum = np.max(scores)
        candidates = np.flatnonzero(
            np.isclose(scores, maximum, rtol=0.0, atol=1e-14)
        )
        medoids.append(int(candidates[np.argmin(external_ids[candidates])]))

    for _ in range(100):
        medoids = sorted(medoids, key=lambda index: external_ids[index])
        labels = np.argmin(distance[:, medoids], axis=1)
        updated: list[int] = []
        for cluster in range(storage_count):
            members = np.flatnonzero(labels == cluster)
            if members.size == 0:
                raise RuntimeError("deterministic k-medoids produced an empty cluster")
            costs = distance[np.ix_(members, members)] @ weights[members]
            updated.append(
                int(members[_lowest_bus_argmin(costs, external_ids[members])])
            )
        if updated == medoids:
            break
        medoids = updated
    else:
        raise RuntimeError("deterministic k-medoids did not converge")

    medoids = sorted(medoids, key=lambda index: external_ids[index])
    labels = np.argmin(distance[:, medoids], axis=1)
    cluster_weights = np.array(
        [np.sum(weights[labels == cluster]) for cluster in range(storage_count)]
    )
    solar_cluster = _lowest_bus_argmin(-cluster_weights, external_ids[medoids])
    solar_index = medoids[solar_cluster]
    medoid_distances = distance[solar_index, medoids]
    wind_cluster = _lowest_bus_argmin(-medoid_distances, external_ids[medoids])
    wind_index = medoids[wind_cluster]
    return SitingResult(
        storage_buses=tuple(int(external_ids[index]) for index in medoids),
        cluster_by_external_bus=tuple(int(value) for value in labels),
        solar_bus=int(external_ids[solar_index]),
        wind_bus=int(external_ids[wind_index]),
        distance_sha256=_array_sha256(distance),
    )


def materialize_pilot(
    case: Mapping[str, object], parameters: PilotParameters
) -> MaterializedPilot:
    """Create annual explicit inputs from one predeclared pilot point."""
    if parameters not in PILOT_GRID:
        raise ValueError("parameters must be one of the predeclared PILOT_GRID points")
    profiles = build_annual_profiles()
    siting = deterministic_siting(case)
    loads = loads_from_pglib_case(case)
    load_ids = [load.device_id for load in loads]
    p_base = np.array([load.p_load_mw for load in loads], dtype=float)
    q_base = np.array([load.q_load_mvar for load in loads], dtype=float)
    df_load_p = pd.DataFrame(
        profiles.load_multiplier[:, None] * p_base[None, :],
        index=profiles.index,
        columns=load_ids,
    )
    df_load_q = pd.DataFrame(
        profiles.load_multiplier[:, None] * q_base[None, :],
        index=profiles.index,
        columns=load_ids,
    )

    annual_load_mwh = float(np.sum(df_load_p.to_numpy()))
    half_renewable_mwh = (
        0.5 * parameters.renewable_energy_share * annual_load_mwh
    )
    wind_capacity = half_renewable_mwh / float(
        np.sum(profiles.wind_capacity_factor)
    )
    solar_capacity = half_renewable_mwh / float(
        np.sum(profiles.solar_capacity_factor)
    )
    nondispatchable = (
        NondispatchableUnit(
            bus=siting.wind_bus,
            p_available=0.0,
            apparent_power_rating=wind_capacity,
            device_id="wind_case118",
        ),
        NondispatchableUnit(
            bus=siting.solar_bus,
            p_available=0.0,
            apparent_power_rating=solar_capacity,
            device_id="solar_case118",
        ),
    )
    df_nd = pd.DataFrame(
        {
            "wind_case118": wind_capacity * profiles.wind_capacity_factor,
            "solar_case118": solar_capacity * profiles.solar_capacity_factor,
        },
        index=profiles.index,
    )

    bus = np.asarray(case["bus"], dtype=float)
    peak_load_mw = float(np.max(profiles.load_multiplier) * np.sum(bus[:, 2]))
    aggregate_storage_power = (
        parameters.storage_power_fraction_of_peak * peak_load_mw
    )
    cluster_labels = np.asarray(siting.cluster_by_external_bus, dtype=int)
    cluster_load = np.array(
        [
            np.sum(bus[cluster_labels == cluster, 2])
            for cluster in range(len(siting.storage_buses))
        ],
        dtype=float,
    )
    if np.any(cluster_load <= 0.0):
        raise RuntimeError("every storage cluster must have positive active load")
    ratings = aggregate_storage_power * cluster_load / np.sum(cluster_load)
    storage = tuple(
        StorageUnitIdeal(
            bus=external_bus,
            apparent_power_rating=float(rating),
            capacity=float(rating * parameters.storage_duration_hours),
            initial_soc=float(
                0.5 * rating * parameters.storage_duration_hours
            ),
            aging_weight=1.0,
            terminal_soc=float(
                0.5 * rating * parameters.storage_duration_hours
            ),
            terminal_constraint="equality",
            device_id=f"storage_bus_{external_bus}",
        )
        for external_bus, rating in zip(
            siting.storage_buses, ratings, strict=True
        )
    )
    return MaterializedPilot(
        parameters=parameters,
        profiles=profiles,
        siting=siting,
        loads=loads,
        df_load_p=df_load_p,
        df_load_q=df_load_q,
        nondispatchable=nondispatchable,
        df_nd=df_nd,
        storage=storage,
    )
