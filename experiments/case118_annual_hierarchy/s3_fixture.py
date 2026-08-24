"""Frozen one-month Case118 hierarchy fixture for S3."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Mapping, cast

import numpy as np

from cvxopf import HierarchicalInputs, OPFOptions, gen_from_matpower
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
from experiments.case118_annual_hierarchy.scenario import PILOT_GRID, materialize_pilot
from experiments.case118_annual_hierarchy.streaming_runner import (
    execution_input_sha256,
)


S3_START = 3744
S3_STOP = 4464
S3_HORIZON_STEPS = S3_STOP - S3_START
S3_DELTA_HOURS = 1.0
S3_RECYCLE_INTERVALS = 16
S3_RESTART_BOUNDARIES = tuple(
    range(S3_RECYCLE_INTERVALS, S3_HORIZON_STEPS, S3_RECYCLE_INTERVALS)
)
S3_EXPECTED_HASHES: Mapping[str, str] = {
    "case": "815ed943bb2e38dc4da0ad176c0df5fd95b09d352000378987849acbd1eb46ca",
    "load_p": "da9019176c66873d83ec3d64db754bd36bd6c8b01576a82f04fa45c9aecdba1c",
    "load_q": "04ca6fe1ea952ace0fa8282300b2ca42a774b4aee81bbb6eb4919298048dfc2b",
    "nondispatchable": (
        "bfea123377e8fb32a86fe4369089888278174564497607ffd4e93e1d2653ef6f"
    ),
    "input_fingerprint": (
        "a339f13f31138536d640b9a3f66c4a0272a0dd30644172c52dfd75ee0666716b"
    ),
    "scenario": (
        "bcd9bfac11baeafbdf85014118b558587f7f5ac953b7f276555d01cc5cfdd235"
    ),
}


@dataclass(frozen=True)
class S3Fixture:
    """Build-ready one-month inputs and their frozen integrity identity."""

    inputs: HierarchicalInputs
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


def load_s3_fixture(*, verify_hashes: bool = True) -> S3Fixture:
    """Materialize the rated PGLib one-month S3 inputs without solving."""
    case = load_pglib_case118()
    pilot = materialize_pilot(case, PILOT_GRID[0])
    load_p = pilot.df_load_p.iloc[S3_START:S3_STOP].copy()
    load_q = pilot.df_load_q.iloc[S3_START:S3_STOP].copy()
    nondispatchable = pilot.df_nd.iloc[S3_START:S3_STOP].copy()
    inputs = HierarchicalInputs(
        case=case,
        horizon_steps=S3_HORIZON_STEPS,
        delta=S3_DELTA_HOURS,
        generators=tuple(gen_from_matpower(case["gen"], case["gencost"])),
        loads=pilot.loads,
        storage=pilot.storage,
        nondispatchable=pilot.nondispatchable,
        df_load_p=load_p,
        df_load_q=load_q,
        df_nd=nondispatchable,
        options=OPFOptions(enforce_branch_limits=True, init_flat=True),
    )
    policy = frozen_p0_policy()
    solve_config = frozen_p0_solve_config()
    if policy_sha256(policy) != P0_EXPECTED_POLICY_SHA256:
        raise ValueError("S3 hierarchy policy hash mismatch")
    if solve_config_sha256(solve_config) != P0_EXPECTED_SOLVE_CONFIG_SHA256:
        raise ValueError("S3 solve-configuration hash mismatch")
    component_hashes = {
        "case": _case_sha256(case),
        "load_p": array_sha256(load_p.to_numpy()),
        "load_q": array_sha256(load_q.to_numpy()),
        "nondispatchable": array_sha256(nondispatchable.to_numpy()),
        "input_fingerprint": execution_input_sha256(inputs),
    }
    hashes = {**component_hashes, "scenario": _scenario_sha256(component_hashes)}
    if verify_hashes:
        if set(S3_EXPECTED_HASHES) != set(hashes):
            raise ValueError("S3 expected-hash registry is incomplete")
        for name, expected in S3_EXPECTED_HASHES.items():
            if hashes[name] != expected:
                raise ValueError(f"S3 {name} hash mismatch")
    return S3Fixture(
        inputs=inputs,
        policy_sha256=policy_sha256(policy),
        solve_config_sha256=solve_config_sha256(solve_config),
        hashes=hashes,
        start_timestamp=str(load_p.index[0]),
        stop_timestamp=str(load_p.index[-1]),
    )


__all__ = [
    "S3_DELTA_HOURS",
    "S3_EXPECTED_HASHES",
    "S3_HORIZON_STEPS",
    "S3_RECYCLE_INTERVALS",
    "S3_RESTART_BOUNDARIES",
    "S3_START",
    "S3_STOP",
    "S3Fixture",
    "load_s3_fixture",
]
