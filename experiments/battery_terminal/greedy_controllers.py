"""Causal copper-plate baselines for the battery-terminal experiment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np


Priority = Literal["dispatchable", "battery"]


@dataclass(frozen=True)
class GreedyConfig:
    """Physical limits shared by the two greedy controllers."""

    capacity_mwh: float = 1000.0
    max_power_mw: float = 150.0
    initial_soc_mwh: float = 500.0
    dispatchable_max_mw: float = 350.0
    delta_hours: float = 1.0


@dataclass(frozen=True)
class GreedyResult:
    """One causal controller trajectory."""

    soc_mwh: np.ndarray
    battery_mw: np.ndarray
    dispatchable_mw: np.ndarray
    curtailment_mw: np.ndarray
    load_shedding_mw: np.ndarray


def _validate_inputs(
    load_mw: np.ndarray,
    renewable_mw: np.ndarray,
    config: GreedyConfig,
) -> tuple[np.ndarray, np.ndarray]:
    load = np.asarray(load_mw, dtype=float)
    renewable = np.asarray(renewable_mw, dtype=float)
    if load.ndim != 1 or renewable.ndim != 1 or load.shape != renewable.shape:
        raise ValueError("load_mw and renewable_mw must be equal-length vectors")
    if not np.isfinite(load).all() or not np.isfinite(renewable).all():
        raise ValueError("load_mw and renewable_mw must be finite")
    if np.any(load < 0) or np.any(renewable < 0):
        raise ValueError("load_mw and renewable_mw must be nonnegative")
    limits = (
        config.capacity_mwh,
        config.max_power_mw,
        config.dispatchable_max_mw,
        config.delta_hours,
    )
    if not np.isfinite(limits).all() or np.any(np.asarray(limits) <= 0):
        raise ValueError("capacity, power, generation, and delta must be positive")
    if (
        not np.isfinite(config.initial_soc_mwh)
        or config.initial_soc_mwh < 0
        or config.initial_soc_mwh > config.capacity_mwh
    ):
        raise ValueError("initial_soc_mwh must lie within storage capacity")
    return load, renewable


def run_greedy_controller(
    load_mw: np.ndarray,
    renewable_mw: np.ndarray,
    *,
    priority: Priority,
    config: GreedyConfig = GreedyConfig(),
) -> GreedyResult:
    """Run a dispatchable- or battery-priority causal dispatch policy."""
    if priority not in ("dispatchable", "battery"):
        raise ValueError("priority must be 'dispatchable' or 'battery'")
    load, renewable = _validate_inputs(load_mw, renewable_mw, config)
    steps = len(load)
    battery = np.zeros(steps)
    soc = np.zeros(steps + 1)
    soc[0] = config.initial_soc_mwh
    dispatchable = np.zeros(steps)
    curtailment = np.zeros(steps)
    shedding = np.zeros(steps)

    for step in range(steps):
        net_load = load[step] - renewable[step]
        if net_load <= 0:
            maximum_charge = min(
                config.max_power_mw,
                (config.capacity_mwh - soc[step]) / config.delta_hours,
            )
            battery[step] = max(net_load, -maximum_charge)
            curtailment[step] = battery[step] - net_load
        else:
            maximum_discharge = min(
                config.max_power_mw,
                soc[step] / config.delta_hours,
            )
            if priority == "dispatchable":
                dispatchable[step] = min(
                    net_load,
                    config.dispatchable_max_mw,
                )
                battery[step] = min(
                    net_load - dispatchable[step],
                    maximum_discharge,
                )
            else:
                battery[step] = min(net_load, maximum_discharge)
                dispatchable[step] = min(
                    net_load - battery[step],
                    config.dispatchable_max_mw,
                )
            shedding[step] = (
                net_load - dispatchable[step] - battery[step]
            )
        soc[step + 1] = (
            soc[step] - config.delta_hours * battery[step]
        )

    return GreedyResult(
        soc_mwh=soc,
        battery_mw=battery,
        dispatchable_mw=dispatchable,
        curtailment_mw=curtailment,
        load_shedding_mw=shedding,
    )


def naive_control_dispatchable_priority(
    load_mw: np.ndarray,
    renewable_mw: np.ndarray,
    config: GreedyConfig = GreedyConfig(),
) -> GreedyResult:
    """Dispatch available conventional generation before stored energy."""
    return run_greedy_controller(
        load_mw,
        renewable_mw,
        priority="dispatchable",
        config=config,
    )


def naive_control_battery_priority(
    load_mw: np.ndarray,
    renewable_mw: np.ndarray,
    config: GreedyConfig = GreedyConfig(),
) -> GreedyResult:
    """Dispatch stored energy before available conventional generation."""
    return run_greedy_controller(
        load_mw,
        renewable_mw,
        priority="battery",
        config=config,
    )
