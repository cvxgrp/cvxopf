"""Frozen one-week Case118 hierarchy fixture for S2."""

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
from experiments.case118_annual_hierarchy.scenario import (
    PILOT_GRID,
    materialize_pilot,
)
from experiments.case118_annual_hierarchy.streaming_runner import (
    execution_input_sha256,
)


S2_START = 3744
S2_STOP = 3912
S2_HORIZON_STEPS = S2_STOP - S2_START
S2_DELTA_HOURS = 1.0
S2_EXPECTED_HASHES: Mapping[str, str] = {
    "case": "815ed943bb2e38dc4da0ad176c0df5fd95b09d352000378987849acbd1eb46ca",
    "load_p": "c52254eed83d09481afcd74a88e2b49dbe9bfc44c3e1ed87692337e257c455e9",
    "load_q": "b3b0ac7cd07531526e68ce1a12ccebdf2465d246b745860d334320bd7c7b1ef3",
    "nondispatchable": (
        "d573a9470635740e5f642ae513bdf37d9003c69d26a0eb862c87b7014b8baa76"
    ),
    "input_fingerprint": (
        "b538e7f302a425f8981a9274a3628568bf38573435a4be9bc6b66764cd0458c1"
    ),
    "scenario": (
        "f602d67563d35e62df03cc716f82f0c3ba823813d0719c623b12a727f92ae12b"
    ),
}


@dataclass(frozen=True)
class S2Fixture:
    """Build-ready one-week inputs and their frozen integrity identity."""

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


def load_s2_fixture(*, verify_hashes: bool = True) -> S2Fixture:
    """Materialize the rated PGLib one-week S2 inputs without solving."""
    case = load_pglib_case118()
    pilot = materialize_pilot(case, PILOT_GRID[0])
    load_p = pilot.df_load_p.iloc[S2_START:S2_STOP].copy()
    load_q = pilot.df_load_q.iloc[S2_START:S2_STOP].copy()
    nondispatchable = pilot.df_nd.iloc[S2_START:S2_STOP].copy()
    inputs = HierarchicalInputs(
        case=case,
        horizon_steps=S2_HORIZON_STEPS,
        delta=S2_DELTA_HOURS,
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
        raise ValueError("S2 hierarchy policy hash mismatch")
    if solve_config_sha256(solve_config) != P0_EXPECTED_SOLVE_CONFIG_SHA256:
        raise ValueError("S2 solve-configuration hash mismatch")
    component_hashes = {
        "case": _case_sha256(case),
        "load_p": array_sha256(load_p.to_numpy()),
        "load_q": array_sha256(load_q.to_numpy()),
        "nondispatchable": array_sha256(nondispatchable.to_numpy()),
        "input_fingerprint": execution_input_sha256(inputs),
    }
    hashes = {
        **component_hashes,
        "scenario": _scenario_sha256(component_hashes),
    }
    if verify_hashes:
        for name, expected in S2_EXPECTED_HASHES.items():
            if hashes[name] != expected:
                raise ValueError(f"S2 {name} hash mismatch")
    return S2Fixture(
        inputs=inputs,
        policy_sha256=policy_sha256(policy),
        solve_config_sha256=solve_config_sha256(solve_config),
        hashes=hashes,
        start_timestamp=str(load_p.index[0]),
        stop_timestamp=str(load_p.index[-1]),
    )


__all__ = [
    "S2_DELTA_HOURS",
    "S2_EXPECTED_HASHES",
    "S2_HORIZON_STEPS",
    "S2_START",
    "S2_STOP",
    "S2Fixture",
    "load_s2_fixture",
]
