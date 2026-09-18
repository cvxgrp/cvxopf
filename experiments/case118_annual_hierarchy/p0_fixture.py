"""Frozen compact fixtures for case118 P0 orchestration equivalence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping, cast

import numpy as np
import pandas as pd

from cvxopf import (
    HierarchicalInputs,
    HierarchicalAcceptanceTolerances,
    HierarchicalPolicy,
    HierarchicalSolveConfig,
    LayerSolveConfig,
    OPFOptions,
    StorageUnitIdeal,
    ShiftedRecoveryConfig,
    gen_from_matpower,
)
from cvxopf.load import loads_from_matpower
from cvxopf.testcases import case9


P0_HORIZONS = (6, 24)
P0_AC_WINDOW_STEPS = 3
P0_DELTA_HOURS = 1.0
P0_STORAGE_DEVICE_ID = "p0_storage_bus_7"
P0_PROFILE_DECIMALS = 9
P0_EXPECTED_POLICY_SHA256 = (
    "2186334bd2e7be3760636f0b20575c81deaff5f293fb9a725270157379957520"
)
P0_EXPECTED_SOLVE_CONFIG_SHA256 = (
    "bfb818de03ddbfd983bb02def3aa3c51d0e6c1b075486ec66bca3035d82e2977"
)
P0_EXPECTED_HASHES: Mapping[int, Mapping[str, str]] = {
    6: {
        "case": "e52623fb1e5a1131af3c1f253c0452d29b5301f11b48513133000ca4ce04cc15",
        "load_p": "8525caf68a1cf744ecfce725d5495a8d94cd1a34c8d22ccf6b3f3ee390f105e9",
        "load_q": "43c8b3e26091da67f5303649a6a9a11ce9001276e7c92a584c73ff09da017df8",
    },
    24: {
        "case": "e52623fb1e5a1131af3c1f253c0452d29b5301f11b48513133000ca4ce04cc15",
        "load_p": "5652cecf0546c9877bdeca35d88e43b9619130a64669f29f92590751e39136e2",
        "load_q": "e4ce8a88e9a7262e2c1aec9980ae5efc584d6f5d34bc45ef98ed326e82728b92",
    },
}


def _array_sha256(values: object) -> str:
    array = np.ascontiguousarray(np.asarray(values), dtype="<f8")
    header = f"float64-le|shape={array.shape}|".encode()
    return hashlib.sha256(header + array.tobytes(order="C")).hexdigest()


def _case_sha256(case: Mapping[str, object]) -> str:
    digest = hashlib.sha256()
    for name in ("baseMVA", "bus", "gen", "branch", "gencost"):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(_array_sha256(case[name]).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def policy_sha256(policy: HierarchicalPolicy) -> str:
    recovery = policy.recovery
    payload = {
        "ac_window_steps": policy.ac_window_steps,
        "outer_policy": policy.outer_policy,
        "inner_terminal_policy": policy.inner_terminal_policy,
        "initialization_policy": policy.initialization_policy,
        "quadratic_soft_weight": policy.quadratic_soft_weight,
        "recovery": None
        if recovery is None
        else {
            "perturbation_scales": recovery.perturbation_scales,
            "seed_base": recovery.seed_base,
        },
        "tolerances": {
            name: getattr(policy.tolerances, name)
            for name in policy.tolerances.__dataclass_fields__
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def solve_config_sha256(config: HierarchicalSolveConfig) -> str:
    """Hash both frozen layer solvers and every validated option."""
    payload = {
        "outer": {
            "solver": config.outer.solver,
            "options": dict(config.outer.options),
        },
        "ac": {
            "solver": config.ac.solver,
            "options": dict(config.ac.options),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def frozen_p0_policy() -> HierarchicalPolicy:
    """Construct the fully explicit frozen hierarchy policy."""
    return HierarchicalPolicy(
        ac_window_steps=P0_AC_WINDOW_STEPS,
        outer_policy="frozen",
        inner_terminal_policy="hard_equality",
        initialization_policy="shifted_with_recovery",
        recovery=ShiftedRecoveryConfig(
            perturbation_scales=(1e-4, 1e-3, 1e-2),
            seed_base=17_000_000,
        ),
        tolerances=HierarchicalAcceptanceTolerances(
            soc_recurrence_mwh_abs=1e-4,
            terminal_soc_mwh_abs=1e-3,
            soft_terminal_cost_abs=1e-6,
            ac_active_balance_pu_abs=1e-6,
            ac_reactive_balance_pu_abs=1e-6,
            dc_injection_reporting_mw_abs=1e-4,
            dc_nodal_balance_pu_abs=1e-6,
            voltage_bound_pu_abs=1e-6,
            branch_mva_abs=1e-4,
            branch_normalized_squared_residual=1e-7,
        ),
    )


def frozen_p0_solve_config() -> HierarchicalSolveConfig:
    """Construct the explicit solver-stack contract for both layers."""
    return HierarchicalSolveConfig(
        outer=LayerSolveConfig("CLARABEL", options={}),
        ac=LayerSolveConfig("IPOPT", options={}),
    )


@dataclass(frozen=True)
class P0Fixture:
    """One build-ready compact hierarchy and its trusted archive contract."""

    inputs: HierarchicalInputs
    policy: HierarchicalPolicy
    solve_config: HierarchicalSolveConfig
    case_sha256: str
    load_p_sha256: str
    load_q_sha256: str
    policy_sha256: str
    result_dimensions: Mapping[str, int]

    @property
    def storage_device_ids(self) -> tuple[str, ...]:
        return cast(tuple[str, ...], self.inputs.storage_device_ids)


def load_p0_fixture(horizon_steps: int) -> P0Fixture:
    """Materialize and integrity-check one frozen compact P0 fixture."""
    if horizon_steps not in P0_HORIZONS:
        raise ValueError(f"P0 horizon must be one of {P0_HORIZONS}")
    case = case9()
    loads = tuple(loads_from_matpower(np.asarray(case["bus"], dtype=float)))
    columns = [str(unit.device_id) for unit in loads]
    hour = np.arange(horizon_steps, dtype=float)
    multiplier = np.round(
        1.0
        + 0.06 * np.sin(2.0 * np.pi * hour / 24.0)
        + 0.02 * np.cos(2.0 * np.pi * hour / 12.0),
        decimals=P0_PROFILE_DECIMALS,
    )
    p_static = np.asarray([unit.p_load_mw for unit in loads], dtype=float)
    q_static = np.asarray(
        [
            0.0 if unit.q_load_mvar is None else unit.q_load_mvar
            for unit in loads
        ],
        dtype=float,
    )
    p_values = np.round(multiplier[:, None] * p_static, P0_PROFILE_DECIMALS)
    q_values = np.round(multiplier[:, None] * q_static, P0_PROFILE_DECIMALS)
    index = pd.RangeIndex(horizon_steps, name="hour")
    df_load_p = pd.DataFrame(p_values, index=index, columns=columns)
    df_load_q = pd.DataFrame(q_values, index=index, columns=columns)
    storage = (
        StorageUnitIdeal(
            bus=7,
            apparent_power_rating=125.0,
            capacity=1_000.0,
            initial_soc=500.0,
            terminal_soc=500.0,
            terminal_constraint="equality",
            device_id=P0_STORAGE_DEVICE_ID,
        ),
    )
    inputs = HierarchicalInputs(
        case=case,
        horizon_steps=horizon_steps,
        delta=P0_DELTA_HOURS,
        generators=tuple(gen_from_matpower(case["gen"], case["gencost"])),
        loads=loads,
        storage=storage,
        df_load_p=df_load_p,
        df_load_q=df_load_q,
        options=OPFOptions(enforce_branch_limits=True, init_flat=True),
    )
    policy = frozen_p0_policy()
    solve_config = frozen_p0_solve_config()
    if policy_sha256(policy) != P0_EXPECTED_POLICY_SHA256:
        raise ValueError("P0 policy hash mismatch")
    if solve_config_sha256(solve_config) != P0_EXPECTED_SOLVE_CONFIG_SHA256:
        raise ValueError("P0 solve-configuration hash mismatch")
    hashes = {
        "case": _case_sha256(case),
        "load_p": _array_sha256(p_values),
        "load_q": _array_sha256(q_values),
    }
    expected = P0_EXPECTED_HASHES[horizon_steps]
    for name, actual in hashes.items():
        if expected[name] != actual:
            raise ValueError(f"P0 {horizon_steps}-hour {name} hash mismatch")
    return P0Fixture(
        inputs=inputs,
        policy=policy,
        solve_config=solve_config,
        case_sha256=hashes["case"],
        load_p_sha256=hashes["load_p"],
        load_q_sha256=hashes["load_q"],
        policy_sha256=policy_sha256(policy),
        result_dimensions={
            "generators": len(inputs.generators),
            "buses": len(np.asarray(case["bus"])),
            "branches": len(np.asarray(case["branch"])),
            "loads": len(inputs.loads),
            "storage": len(inputs.storage),
            "nondispatchable": len(inputs.nondispatchable),
            "hvdc": len(inputs.hvdc),
        },
    )


__all__ = [
    "P0_AC_WINDOW_STEPS",
    "P0_DELTA_HOURS",
    "P0_EXPECTED_HASHES",
    "P0_EXPECTED_POLICY_SHA256",
    "P0_EXPECTED_SOLVE_CONFIG_SHA256",
    "P0_HORIZONS",
    "P0_STORAGE_DEVICE_ID",
    "P0Fixture",
    "frozen_p0_policy",
    "frozen_p0_solve_config",
    "load_p0_fixture",
    "policy_sha256",
    "solve_config_sha256",
]
