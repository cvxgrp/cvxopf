"""Scenario construction for the battery terminal-policy experiment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd


SOURCE_COLUMNS = (
    "9q9wtp_solar",
    "9q9wtp_wind",
    "9q9wtp_dist_solar",
    "9q9wtp_load",
)

CASE_BASE_LOAD_MW = 315.0
SOURCE_MEAN_LOAD_MW = 1138.7624473656565
DEFAULT_SOURCE_TO_CASE_SCALE = CASE_BASE_LOAD_MW / SOURCE_MEAN_LOAD_MW

LOAD_FRACTIONS = {5: 90.0 / 315.0, 7: 100.0 / 315.0, 9: 125.0 / 315.0}
LOAD_Q_OVER_P = {5: 30.0 / 90.0, 7: 35.0 / 100.0, 9: 50.0 / 125.0}

RESOURCE_FRACTIONS = {
    "utility_solar": {1: 0.20, 2: 0.80},
    "wind": {2: 0.20, 3: 0.80},
    "dist_solar": LOAD_FRACTIONS,
}

RESOURCE_COLUMNS = {
    "utility_solar": "9q9wtp_solar",
    "wind": "9q9wtp_wind",
    "dist_solar": "9q9wtp_dist_solar",
}


@dataclass(frozen=True)
class ScenarioConfig:
    """Aggregate stress controls and spatial-noise configuration."""

    source_to_case_scale: float = DEFAULT_SOURCE_TO_CASE_SCALE
    load_scale: float = 1.0
    load_shift_mw: float = 0.0
    solar_scale: float = 1.0
    wind_scale: float = 1.0
    dist_solar_scale: float = 1.0
    spatial_noise_std: float = 0.0
    random_seed: int | None = 0


@dataclass(frozen=True)
class ScenarioData:
    """OPF-ready load and nondispatchable availability frames."""

    df_P: pd.DataFrame
    df_Q: pd.DataFrame
    df_nd: pd.DataFrame
    load_fractions: Mapping[int, float]
    resource_fractions: Mapping[str, Mapping[int, float]]


@dataclass(frozen=True)
class WindowSpec:
    """Named inclusive window on the fixed Pacific-standard-time grid."""

    start: str
    end: str
    interpretation: str


REPRESENTATIVE_WINDOWS = {
    "low": WindowSpec(
        start="2022-03-19 00:00:00-08:00",
        end="2022-03-22 23:00:00-08:00",
        interpretation="renewable-energy surplus with short deficit intervals",
    ),
    "moderate": WindowSpec(
        start="2019-02-04 00:00:00-08:00",
        end="2019-02-07 23:00:00-08:00",
        interpretation="energy-balanced window with a large peak deficit",
    ),
    "high": WindowSpec(
        start="2021-12-18 00:00:00-08:00",
        end="2021-12-21 23:00:00-08:00",
        interpretation="sustained energy-deficit window",
    ),
}


def read_source_data(path: str | Path) -> pd.DataFrame:
    """Read the local Tracy source data and retain its fixed-offset time axis."""
    frame = pd.read_csv(path)
    missing = set(("time", *SOURCE_COLUMNS)) - set(frame.columns)
    if missing:
        raise ValueError(f"Source data missing required columns: {sorted(missing)}")

    frame = frame.loc[:, ("time", *SOURCE_COLUMNS)].copy()
    frame["time"] = pd.to_datetime(frame["time"])
    if frame["time"].isna().any():
        raise ValueError("Source data contain invalid timestamps")
    if frame["time"].duplicated().any():
        raise ValueError("Source data contain duplicate timestamps")
    if not frame["time"].is_monotonic_increasing:
        raise ValueError("Source timestamps must be strictly increasing")

    return frame.set_index("time")


def select_complete_window(
    frame: pd.DataFrame,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
) -> pd.DataFrame:
    """Select an inclusive, contiguous hourly window with complete channels."""
    window = frame.loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()
    _validate_window(window)
    return window


def select_representative_window(
    frame: pd.DataFrame,
    name: str,
) -> pd.DataFrame:
    """Select one of the approved low, moderate, or high stress windows."""
    if name not in REPRESENTATIVE_WINDOWS:
        raise ValueError(
            f"Unknown representative window {name!r}; "
            f"expected one of {sorted(REPRESENTATIVE_WINDOWS)}"
        )
    spec = REPRESENTATIVE_WINDOWS[name]
    return select_complete_window(frame, spec.start, spec.end)


def _validate_window(window: pd.DataFrame) -> None:
    """Validate the common source-window contract."""
    missing = set(SOURCE_COLUMNS) - set(window.columns)
    if missing:
        raise ValueError(f"Scenario window missing columns: {sorted(missing)}")
    if window.empty:
        raise ValueError("Scenario window is empty")
    if not isinstance(window.index, pd.DatetimeIndex):
        raise ValueError("Scenario window must have a DatetimeIndex")
    if window.index.has_duplicates or not window.index.is_monotonic_increasing:
        raise ValueError("Scenario timestamps must be unique and increasing")

    values = window.loc[:, SOURCE_COLUMNS].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Scenario window contains missing or nonfinite observations")
    if np.any(values < 0):
        raise ValueError("Scenario source power must be nonnegative")

    elapsed_hours = window.index.to_series().diff().dropna().dt.total_seconds() / 3600
    if not np.allclose(elapsed_hours, 1.0):
        raise ValueError("Scenario window must have an hourly cadence")


def _validate_config(config: ScenarioConfig) -> None:
    values = {
        "source_to_case_scale": config.source_to_case_scale,
        "load_scale": config.load_scale,
        "load_shift_mw": config.load_shift_mw,
        "solar_scale": config.solar_scale,
        "wind_scale": config.wind_scale,
        "dist_solar_scale": config.dist_solar_scale,
        "spatial_noise_std": config.spatial_noise_std,
    }
    for name, value in values.items():
        if not np.isfinite(value):
            raise ValueError(f"{name} must be finite, got {value}")

    if config.source_to_case_scale <= 0:
        raise ValueError(
            "source_to_case_scale must be positive, got "
            f"{config.source_to_case_scale}"
        )

    for name in (
        "load_scale",
        "solar_scale",
        "wind_scale",
        "dist_solar_scale",
        "spatial_noise_std",
    ):
        if values[name] < 0:
            raise ValueError(f"{name} must be nonnegative, got {values[name]}")


def _perturb_fractions(
    fractions: Mapping[int, float],
    noise_std: float,
    rng: np.random.Generator,
) -> dict[int, float]:
    buses = tuple(fractions)
    base = np.array([fractions[bus] for bus in buses], dtype=float)
    if np.any(base < 0) or not np.isclose(base.sum(), 1.0):
        raise ValueError("Spatial fractions must be nonnegative and sum to one")

    factors = np.exp(rng.normal(0.0, noise_std, size=len(base)))
    perturbed = base * factors
    perturbed /= perturbed.sum()
    return dict(zip(buses, perturbed, strict=True))


def _allocate_to_buses(
    aggregate: np.ndarray,
    fractions: Mapping[int, float],
    index: pd.Index,
) -> pd.DataFrame:
    values = np.zeros((len(aggregate), 9), dtype=float)
    for bus, fraction in fractions.items():
        values[:, bus - 1] = aggregate * fraction
    return pd.DataFrame(values, index=index, columns=range(1, 10))


def generate_scenario(
    window: pd.DataFrame,
    config: ScenarioConfig = ScenarioConfig(),
) -> ScenarioData:
    """Scale aggregate trajectories and allocate them to the nine-bus model."""
    _validate_config(config)
    _validate_window(window)

    rng = np.random.default_rng(config.random_seed)
    load_fractions = _perturb_fractions(
        LOAD_FRACTIONS, config.spatial_noise_std, rng
    )
    resource_fractions = {
        resource: _perturb_fractions(
            fractions, config.spatial_noise_std, rng
        )
        for resource, fractions in RESOURCE_FRACTIONS.items()
    }

    load = (
        config.source_to_case_scale
        * config.load_scale
        * window["9q9wtp_load"].to_numpy(dtype=float)
        + config.load_shift_mw
    )
    if np.any(load < 0):
        raise ValueError("Load scaling and shift produce negative load")

    df_P = _allocate_to_buses(load, load_fractions, window.index)
    df_Q = df_P.copy()
    for bus in df_Q:
        df_Q[bus] *= LOAD_Q_OVER_P.get(bus, 0.0)

    resource_scales = {
        "utility_solar": config.solar_scale,
        "wind": config.wind_scale,
        "dist_solar": config.dist_solar_scale,
    }
    nd_columns = {}
    for resource, source_column in RESOURCE_COLUMNS.items():
        aggregate = (
            config.source_to_case_scale
            * resource_scales[resource]
            * window[source_column].to_numpy(dtype=float)
        )
        for bus, fraction in resource_fractions[resource].items():
            nd_columns[f"{resource}_bus_{bus}"] = aggregate * fraction

    df_nd = pd.DataFrame(nd_columns, index=window.index)
    return ScenarioData(
        df_P=df_P,
        df_Q=df_Q,
        df_nd=df_nd,
        load_fractions=load_fractions,
        resource_fractions=resource_fractions,
    )
