"""Frozen annual Case118 outer-plan fixture for S4."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Mapping, cast

import numpy as np

from cvxopf import (
    HierarchicalInputs,
    HierarchicalPolicy,
    HierarchicalSolveConfig,
    OPFOptions,
    gen_from_matpower,
)
from experiments.case118_annual_hierarchy.p0_fixture import (
    P0_EXPECTED_POLICY_SHA256,
    P0_EXPECTED_SOLVE_CONFIG_SHA256,
    frozen_p0_policy,
    frozen_p0_solve_config,
    policy_sha256,
    solve_config_sha256,
)
from experiments.case118_annual_hierarchy.pglib_case import (
    array_sha256,
    load_pglib_case118,
)
from experiments.case118_annual_hierarchy.scenario import (
    HOURS_PER_YEAR,
    PILOT_GRID,
    materialize_pilot,
)
from experiments.case118_annual_hierarchy.streaming_runner import (
    execution_input_sha256,
)


S4_HORIZON_STEPS = HOURS_PER_YEAR
S4_DELTA_HOURS = 1.0
S4_OUTPUT_DIRECTORY = Path(
    "experiments/case118_annual_hierarchy/results/s4_annual_outer_rated"
)
S4_EXPECTED_HASHES: Mapping[str, str] = {
    "case": "815ed943bb2e38dc4da0ad176c0df5fd95b09d352000378987849acbd1eb46ca",
    "load_p": "2f1845f158ed5149b36cb5587d579c09d821d475e02285d2e348f6521bf27764",
    "load_q": "e11d9fe44698056270896f6134735d5f1300da774b65b10bbb942ac5f4baf90b",
    "nondispatchable": "529e2cd33f57c16b9ca5702d2c023e4739d1fedf43d1134456cd1a9ab2c4245b",
    "input_fingerprint": "1a6040898ebb4af4680bae5461a92002d0d1cff74e5e8421f79ad324068278f1",
    "scenario": "223b239aa5fa83d2144e57efc9517ab415927de2f211e039d23ac0cccca34370",
}


@dataclass(frozen=True)
class S4Fixture:
    """Build-ready annual inputs and their frozen integrity identity."""

    inputs: HierarchicalInputs
    policy: HierarchicalPolicy
    solve_config: HierarchicalSolveConfig
    policy_sha256: str
    solve_config_sha256: str
    hashes: Mapping[str, str]
    start_timestamp: str
    stop_timestamp: str

    @property
    def scenario_hash(self) -> str:
        return self.hashes["scenario"]

    @property
    def storage_device_ids(self) -> tuple[str, ...]:
        return cast(tuple[str, ...], self.inputs.storage_device_ids)


@dataclass(frozen=True)
class S4ExecutionLimits:
    """Frozen supervisor limits for the one-shot annual outer solve."""

    child_rss_mib: float = 16_384.0
    worker_wall_seconds: float = 7_200.0
    supervisor_wall_seconds: float = 10_800.0
    poll_seconds: float = 1.0

    def __post_init__(self) -> None:
        values = (
            self.child_rss_mib,
            self.worker_wall_seconds,
            self.supervisor_wall_seconds,
            self.poll_seconds,
        )
        if not all(np.isfinite(value) and value > 0.0 for value in values):
            raise ValueError("S4 execution limits must be positive and finite")
        if self.supervisor_wall_seconds < self.worker_wall_seconds:
            raise ValueError("S4 supervisor wall limit cannot precede worker limit")


S4_EXECUTION_LIMITS = S4ExecutionLimits()


def _case_sha256(case: Mapping[str, object]) -> str:
    digest = hashlib.sha256()
    for name in ("baseMVA", "bus", "gen", "branch", "gencost"):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(array_sha256(np.asarray(case[name])).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _scenario_sha256(hashes: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(hashes):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(hashes[name].encode())
        digest.update(b"\0")
    return digest.hexdigest()


def load_s4_fixture(*, verify_hashes: bool = True) -> S4Fixture:
    """Materialize the full rated annual inputs without constructing a model."""
    case = load_pglib_case118()
    pilot = materialize_pilot(case, PILOT_GRID[0])
    inputs = HierarchicalInputs(
        case=case,
        horizon_steps=S4_HORIZON_STEPS,
        delta=S4_DELTA_HOURS,
        generators=tuple(gen_from_matpower(case["gen"], case["gencost"])),
        loads=pilot.loads,
        storage=pilot.storage,
        nondispatchable=pilot.nondispatchable,
        df_load_p=pilot.df_load_p.copy(),
        df_load_q=pilot.df_load_q.copy(),
        df_nd=pilot.df_nd.copy(),
        options=OPFOptions(enforce_branch_limits=True, init_flat=True),
    )
    policy = frozen_p0_policy()
    solve_config = frozen_p0_solve_config()
    if policy_sha256(policy) != P0_EXPECTED_POLICY_SHA256:
        raise ValueError("S4 hierarchy policy hash mismatch")
    if solve_config_sha256(solve_config) != P0_EXPECTED_SOLVE_CONFIG_SHA256:
        raise ValueError("S4 solve-configuration hash mismatch")
    component_hashes = {
        "case": _case_sha256(case),
        "load_p": array_sha256(pilot.df_load_p.to_numpy()),
        "load_q": array_sha256(pilot.df_load_q.to_numpy()),
        "nondispatchable": array_sha256(pilot.df_nd.to_numpy()),
        "input_fingerprint": execution_input_sha256(inputs),
    }
    hashes = {**component_hashes, "scenario": _scenario_sha256(component_hashes)}
    if verify_hashes:
        if set(S4_EXPECTED_HASHES) != set(hashes):
            raise ValueError("S4 expected-hash registry is incomplete")
        for name, expected in S4_EXPECTED_HASHES.items():
            if hashes[name] != expected:
                raise ValueError(f"S4 {name} hash mismatch")
    return S4Fixture(
        inputs=inputs,
        policy=policy,
        solve_config=solve_config,
        policy_sha256=policy_sha256(policy),
        solve_config_sha256=solve_config_sha256(solve_config),
        hashes=hashes,
        start_timestamp=str(pilot.df_load_p.index[0]),
        stop_timestamp=str(pilot.df_load_p.index[-1]),
    )


__all__ = [
    "S4_DELTA_HOURS",
    "S4_EXPECTED_HASHES",
    "S4_EXECUTION_LIMITS",
    "S4_HORIZON_STEPS",
    "S4_OUTPUT_DIRECTORY",
    "S4ExecutionLimits",
    "S4Fixture",
    "load_s4_fixture",
]
