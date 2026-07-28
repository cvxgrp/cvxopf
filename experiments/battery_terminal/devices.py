"""Provisional device specification for the battery terminal experiment."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from cvxopf.problem import (
    DispatchableGenerator,
    NondispatchableUnit,
    StorageUnitIdeal,
)


GENERATOR_P_MAX_MW = (105.0, 130.0, 115.0)
STORAGE_BUS = 7
STORAGE_POWER_MVA = 150.0
STORAGE_CAPACITY_MWH = 1000.0
STORAGE_INITIAL_SOC_MWH = 500.0


def make_dispatchable_generators() -> list[DispatchableGenerator]:
    """Return the provisional 350 MW case9 dispatchable fleet."""
    return [
        DispatchableGenerator(
            bus=1,
            p_min_mw=10.0,
            p_max_mw=GENERATOR_P_MAX_MW[0],
            q_min_mvar=-300.0,
            q_max_mvar=300.0,
            cost_coeffs=(150.0, 5.0, 0.11),
        ),
        DispatchableGenerator(
            bus=2,
            p_min_mw=10.0,
            p_max_mw=GENERATOR_P_MAX_MW[1],
            q_min_mvar=-300.0,
            q_max_mvar=300.0,
            cost_coeffs=(600.0, 1.2, 0.085),
        ),
        DispatchableGenerator(
            bus=3,
            p_min_mw=10.0,
            p_max_mw=GENERATOR_P_MAX_MW[2],
            q_min_mvar=-300.0,
            q_max_mvar=300.0,
            cost_coeffs=(335.0, 1.0, 0.1225),
        ),
    ]


def _bus_from_device_id(device_id: str) -> int:
    try:
        return int(device_id.rsplit("_bus_", maxsplit=1)[1])
    except (IndexError, ValueError) as exc:
        raise ValueError(
            f"Renewable device ID must end in '_bus_<integer>', got {device_id!r}"
        ) from exc


def make_nondispatchable_units(
    availability_frames: Iterable[pd.DataFrame],
    *,
    rating_multiplier: float = 1.10,
) -> list[NondispatchableUnit]:
    """Size fixed renewable sites from maxima across experiment scenarios."""
    frames = list(availability_frames)
    if not frames:
        raise ValueError("At least one availability frame is required")
    if not np.isfinite(rating_multiplier) or rating_multiplier < 1.0:
        raise ValueError("rating_multiplier must be finite and at least 1.0")

    columns = list(frames[0].columns)
    if not columns:
        raise ValueError("Availability frames must contain renewable sites")
    for frame in frames:
        if list(frame.columns) != columns:
            raise ValueError(
                "Availability frames must have identical ordered device columns"
            )
        if frame.empty:
            raise ValueError("Availability frames must not be empty")
        values = frame.to_numpy(dtype=float)
        if not np.isfinite(values).all() or np.any(values < 0):
            raise ValueError(
                "Availability frames must contain finite nonnegative power"
            )

    maxima = pd.concat(frames, axis=0).max(axis=0)
    if (maxima <= 0).any():
        raise ValueError("Every renewable site must have positive availability")
    return [
        NondispatchableUnit(
            bus=_bus_from_device_id(device_id),
            p_available=0.0,
            apparent_power_rating=rating_multiplier * float(maxima[device_id]),
            device_id=device_id,
        )
        for device_id in columns
    ]


def make_storage(
    *,
    terminal_soc: float | None = None,
    terminal_constraint: str | None = None,
    terminal_cost: str | None = None,
    terminal_weight: float | None = None,
) -> StorageUnitIdeal:
    """Return the provisional bus-7 battery with an explicit terminal policy."""
    return StorageUnitIdeal(
        bus=STORAGE_BUS,
        apparent_power_rating=STORAGE_POWER_MVA,
        capacity=STORAGE_CAPACITY_MWH,
        initial_soc=STORAGE_INITIAL_SOC_MWH,
        aging_weight=1e-2,
        terminal_soc=terminal_soc,
        terminal_constraint=terminal_constraint,
        terminal_cost=terminal_cost,
        terminal_weight=terminal_weight,
    )
