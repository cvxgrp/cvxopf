"""Frozen Case118 prefix-ladder fixtures and resource limits for M14c."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
from typing import Mapping

import numpy as np

from cvxopf import HierarchicalInputs
from experiments.case118_annual_hierarchy.s4_fixture import S4Fixture, load_s4_fixture
from experiments.case118_annual_hierarchy.streaming_runner import (
    execution_input_sha256,
)


M14C_INTEGRATION_COMMIT = "360aaf5f75d7bf2d4b2ec1672d319af90bd8626e"
PREFIX_LADDER_HORIZONS = (24, 168, 720)
PREFIX_EXPECTED_HASHES: Mapping[int, Mapping[str, str]] = {
    24: {
        "input": "8ea38cc285c4a1efba9e0c640cd410edbb6bc252cf7a09cbcd26c5d2e6712268",
        "scenario": "c727a3166d20e0cd719652e72bcb07ba262a24aaa0aee650725839fef5ac70e5",
    },
    168: {
        "input": "ed1b5ed53c4d3bb77a6a8f69f8db9b4a160ec16a52579555287e46ae9a8bb881",
        "scenario": "8cd2beb5f5302af60d6234d0a8b14161e25a1acdabaa8160da1fcf9cc355d885",
    },
    720: {
        "input": "a8b0906b56b6a8b8ccb74964730d18df7043c44e650102efa6278ec9881ce477",
        "scenario": "b9f4af61dbf31be0cac86c104d49b1d63528bbe2930f159796b63d8dcc628afa",
    },
}
PREFIX_LADDER_OUTPUT_DIRECTORY = Path(
    "experiments/m14_time_vectorization/results/m14c_case118_prefix_ladder"
)


@dataclass(frozen=True)
class PrefixExecutionLimits:
    """Frozen resource envelope for one prefix worker."""

    child_rss_mib: float
    worker_wall_seconds: float
    supervisor_wall_seconds: float
    poll_seconds: float = 1.0

    def __post_init__(self) -> None:
        values = (
            self.child_rss_mib,
            self.worker_wall_seconds,
            self.supervisor_wall_seconds,
            self.poll_seconds,
        )
        if not all(np.isfinite(value) and value > 0.0 for value in values):
            raise ValueError("prefix execution limits must be positive and finite")
        if self.supervisor_wall_seconds < self.worker_wall_seconds:
            raise ValueError("prefix supervisor limit cannot precede worker limit")


PREFIX_EXECUTION_LIMITS: Mapping[int, PrefixExecutionLimits] = {
    24: PrefixExecutionLimits(16_384.0, 600.0, 900.0),
    168: PrefixExecutionLimits(16_384.0, 1_800.0, 2_400.0),
    720: PrefixExecutionLimits(16_384.0, 3_600.0, 4_500.0),
}


@dataclass(frozen=True)
class PrefixFixture:
    """One deterministic first-hours prefix of the frozen S4 fixture."""

    annual: S4Fixture
    inputs: HierarchicalInputs
    horizon_steps: int
    input_sha256: str
    scenario_sha256: str
    limits: PrefixExecutionLimits


def _prefix_scenario_sha256(
    annual_scenario_sha256: str, horizon_steps: int, input_sha256: str
) -> str:
    digest = hashlib.sha256()
    for value in (annual_scenario_sha256, str(horizon_steps), input_sha256):
        digest.update(value.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def prefix_inputs(inputs: HierarchicalInputs, horizon_steps: int) -> HierarchicalInputs:
    """Return the deterministic first-hours S4 prefix with terminal policy intact."""
    if horizon_steps not in PREFIX_LADDER_HORIZONS:
        raise ValueError("horizon is not in the frozen M14c prefix ladder")
    if horizon_steps > inputs.horizon_steps:
        raise ValueError("prefix horizon exceeds the frozen annual fixture")
    return replace(
        inputs,
        horizon_steps=horizon_steps,
        df_load_p=inputs.df_load_p.iloc[:horizon_steps].copy(),
        df_load_q=inputs.df_load_q.iloc[:horizon_steps].copy(),
        df_nd=(
            None if inputs.df_nd is None else inputs.df_nd.iloc[:horizon_steps].copy()
        ),
    )


def load_prefix_fixture(horizon_steps: int) -> PrefixFixture:
    """Materialize and bind one prefix to the unchanged annual S4 authority."""
    annual = load_s4_fixture()
    if annual.prefix_ladder_executed or annual.annual_execution_authorized:
        raise ValueError(
            "prefix execution requires the pre-ladder integration authority"
        )
    inputs = prefix_inputs(annual.inputs, horizon_steps)
    input_sha256 = execution_input_sha256(inputs)
    scenario_sha256 = _prefix_scenario_sha256(
        annual.scenario_hash, horizon_steps, input_sha256
    )
    expected = PREFIX_EXPECTED_HASHES[horizon_steps]
    if input_sha256 != expected["input"] or scenario_sha256 != expected["scenario"]:
        raise ValueError("prefix fixture hash mismatch")
    return PrefixFixture(
        annual=annual,
        inputs=inputs,
        horizon_steps=horizon_steps,
        input_sha256=input_sha256,
        scenario_sha256=scenario_sha256,
        limits=PREFIX_EXECUTION_LIMITS[horizon_steps],
    )
