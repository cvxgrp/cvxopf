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
PRE_LADDER_INTEGRATION_CHECKPOINT = "big-experiment-conditioned-pre-prefix"
PRE_LADDER_INTEGRATION_SHA256 = (
    "3cdd1974d150290fcae22317ef86c8d381b4b8af69997d3b2b8f2145630b65d3"
)
PREFIX_LADDER_HORIZONS = (24, 168, 720)
PREFIX_EXPECTED_HASHES: Mapping[int, Mapping[str, str]] = {
    24: {
        "input": "54c86688e700c0166c3d0eafdab46d24095b055cdef589c3e7c8f7bb50c01630",
        "scenario": "655f18d7f5d473b14b6e74c7127ebec995c41131cea7d1a70f5476ecfdcee19b",
    },
    168: {
        "input": "a340488b074bd385258769251ac73ff43f3c7b9411717d55ce6a4bf7e31216c4",
        "scenario": "abeebdb0ed8e602264bf12c0fbe5655af1ef105f9fbe5648bf28909865aa8027",
    },
    720: {
        "input": "2130d764dac2fd8ecfe25cefd3b703fa41b7c4a311ab862dbe8fc31a68e27b04",
        "scenario": "d59176ce4075bb15364e7d7ee1517ab40445b7908f8462a7d2d2046cfeaa2cb9",
    },
}
PREFIX_LADDER_OUTPUT_DIRECTORY = Path(
    "experiments/m14_time_vectorization/results/m14c_case118_prefix_ladder_conditioned_attempt_002"
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
    """Materialize one frozen prefix under the current annual S4 authority.

    Execution runners enforce their own lifecycle authority. Keeping fixture
    materialization independent permits reconstruction of historical ladder and
    profiling artifacts after the authority advances.
    """
    annual = load_s4_fixture()
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
