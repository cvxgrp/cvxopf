"""Deterministic M14a successful and infeasible baseline fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast
import warnings

import numpy as np
import pandas as pd

from cvxopf import (
    HVDCLink,
    Load,
    NondispatchableUnit,
    OPFBuild,
    StorageUnitIdeal,
    build_opf_multistep,
)
from cvxopf.testcases import case9, case118


BaselineCase = Literal["case9", "case118"]
BaselineOutcome = Literal["feasible", "infeasible"]
BaselineComponents = Literal["core", "full"]


@dataclass(frozen=True)
class BaselineFixture:
    """One deterministic build-ready legacy temporal fixture."""

    case_name: BaselineCase
    formulation: str
    horizon: int
    outcome: BaselineOutcome
    components: BaselineComponents
    build: OPFBuild


def _case(case_name: BaselineCase) -> dict[str, Any]:
    return cast(dict[str, Any], case9() if case_name == "case9" else case118())


def build_baseline_fixture(
    formulation: str,
    *,
    horizon: int = 2,
    outcome: BaselineOutcome = "feasible",
    case_name: BaselineCase = "case9",
    components: BaselineComponents = "core",
) -> BaselineFixture:
    """Construct a fixed-load, storage-coupled stepwise baseline."""
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if formulation not in {"ac", "lossy_dc", "singlenode_dc"}:
        raise ValueError("unsupported formulation")
    if outcome not in {"feasible", "infeasible"}:
        raise ValueError("unsupported outcome")
    if components not in {"core", "full"}:
        raise ValueError("unsupported component fixture")
    if components == "full" and outcome == "infeasible":
        raise ValueError("the full component fixture is successful-only")
    if outcome == "infeasible" and horizon >= 100:
        raise ValueError("the one-MW unreachable-target fixture requires horizon < 100")
    ppc = _case(case_name)
    active = pd.DataFrame(np.tile(ppc["bus"][:, 2], (horizon, 1)))
    reactive = pd.DataFrame(np.tile(ppc["bus"][:, 3], (horizon, 1)))
    infeasible = outcome == "infeasible"
    storage = StorageUnitIdeal(
        bus=5,
        apparent_power_rating=1.0 if infeasible else 10.0,
        capacity=100.0,
        initial_soc=0.0 if infeasible else 50.0,
        terminal_soc=100.0 if infeasible else 50.0,
        terminal_constraint="equality",
        device_id="m14a-storage",
    )
    loads = None
    load_active = None
    load_reactive = None
    nondispatchable = None
    nd_available = None
    hvdc = None
    hvdc_min = None
    hvdc_max = None
    legacy_active = active
    legacy_reactive = reactive
    if components == "full":
        load_rows = [row for row in ppc["bus"] if row[2] != 0.0 or row[3] != 0.0]
        loads = [
            Load(
                bus=int(row[0]),
                p_load_mw=float(row[2]),
                q_load_mvar=float(row[3]),
                device_id=f"load-{int(row[0])}",
                shedding_cost_per_mwh=(10_000.0 if index == 0 else None),
                max_shed_fraction=(0.1 if index == 0 else 1.0),
            )
            for index, row in enumerate(load_rows)
        ]
        load_active = pd.DataFrame(
            np.tile([load.p_load_mw for load in loads], (horizon, 1)),
            columns=[load.device_id for load in loads],
        )
        load_reactive = pd.DataFrame(
            np.tile([load.q_load_mvar for load in loads], (horizon, 1)),
            columns=[load.device_id for load in loads],
        )
        nondispatchable = [NondispatchableUnit(6, 5.0, 6.0, "m14a-renewable")]
        nd_available = pd.DataFrame(
            np.linspace(4.0, 5.0, horizon)[:, None],
            columns=["m14a-renewable"],
        )
        hvdc = [HVDCLink(1, 2, -2.0, -0.5, 1.0, device_id="m14a-hvdc")]
        hvdc_min = pd.DataFrame(np.full((horizon, 1), -2.0), columns=["m14a-hvdc"])
        hvdc_max = pd.DataFrame(np.full((horizon, 1), -0.5), columns=["m14a-hvdc"])
        legacy_active = None
        legacy_reactive = None
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message=r"df_(Q|load_q) is retained as reactive load input"
        )
        warnings.filterwarnings(
            "ignore",
            message="Storage apparent_power_rating is applied as a real power",
        )
        build = build_opf_multistep(
            ppc,
            legacy_active,
            legacy_reactive,
            T=horizon,
            formulation=formulation,
            storage=[storage],
            loads=loads,
            df_load_p=load_active,
            df_load_q=load_reactive,
            nondispatchable=nondispatchable,
            df_nd=nd_available,
            hvdc=hvdc,
            df_hvdc_min=hvdc_min,
            df_hvdc_max=hvdc_max,
            temporal_assembly="stepwise",
        )
    return BaselineFixture(case_name, formulation, horizon, outcome, components, build)


def result_schema(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Describe result availability, scalar kind, shape, and dtype."""
    schema: dict[str, dict[str, Any]] = {}
    for name in sorted(result):
        value = result[name]
        if value is None:
            schema[name] = {"availability": "unavailable"}
        elif isinstance(value, str):
            schema[name] = {"availability": "value", "kind": "string"}
        elif np.isscalar(value):
            scalar = float(value)
            schema[name] = {
                "availability": (
                    "nonfinite_scalar" if not np.isfinite(scalar) else "value"
                ),
                "kind": "scalar",
            }
        else:
            array = np.asarray(value)
            schema[name] = {
                "availability": "value",
                "kind": "array",
                "shape": list(array.shape),
                "dtype_kind": array.dtype.kind,
            }
    return schema


def json_result(result: dict[str, Any]) -> dict[str, Any]:
    """Convert result values to strict-JSON-compatible scientific payloads."""
    payload: dict[str, Any] = {}
    for name, value in result.items():
        if value is None:
            payload[name] = None
        elif isinstance(value, str):
            payload[name] = value
        elif np.isscalar(value):
            scalar = float(value)
            payload[name] = scalar if np.isfinite(scalar) else None
        else:
            array = np.asarray(value)
            payload[name] = array.tolist()
    return payload


def _maximum_violation(
    values: np.ndarray, lower: np.ndarray | float, upper: np.ndarray | float
) -> float:
    return float(
        max(
            0.0,
            np.max(np.asarray(lower) - values),
            np.max(values - np.asarray(upper)),
        )
    )


def audit_result(fixture: BaselineFixture, result: dict[str, Any]) -> dict[str, float]:
    """Independently reconstruct common and formulation-specific residuals."""
    build = fixture.build
    data = build.data
    base = float(data["baseMVA"])
    generation = np.asarray(result["Pg"], dtype=float)
    storage_power = np.asarray(result["b"], dtype=float)
    state = np.asarray(result["soc"], dtype=float)
    served = np.asarray(result["p_load_served"], dtype=float)
    modeled = generation @ np.asarray(data["Cg"], dtype=float).T
    modeled += storage_power @ np.asarray(data["Cs"], dtype=float).T
    modeled -= served @ np.asarray(data["Cload"], dtype=float).T
    if result.get("p_nd") is not None:
        modeled += (
            np.asarray(result["p_nd"], dtype=float)
            @ np.asarray(data["Cnd"], dtype=float).T
        )
    if result.get("p_hvdc_in") is not None:
        modeled += (
            np.asarray(result["p_hvdc_in"], dtype=float)
            @ np.asarray(data["Ch_from"], dtype=float).T
        )
        modeled += (
            np.asarray(result["p_hvdc_out"], dtype=float)
            @ np.asarray(data["Ch_to"], dtype=float).T
        )
    network = np.asarray(result["p_net"], dtype=float)
    if network.ndim == 1:
        network = network[:, None]
    preceding = np.vstack((np.asarray(data["storage_initial_soc"]), state[:-1]))
    residuals = {
        "active_nodal_balance_mw_abs": float(np.max(np.abs(network - modeled))),
        "generator_active_bound_mw_abs": _maximum_violation(
            generation,
            base * np.asarray(data["Pgmin"]),
            base * np.asarray(data["Pgmax"]),
        ),
        "storage_recurrence_mwh_abs": float(
            np.max(
                np.abs(
                    state - (preceding - float(data["storage_delta"]) * storage_power)
                )
            )
        ),
        "storage_energy_bound_mwh_abs": _maximum_violation(
            state, 0.0, np.asarray(data["storage_capacity"])
        ),
        "terminal_soc_mwh_abs": float(
            np.max(np.abs(state[-1] - np.asarray(data["storage_terminal_soc"])))
        ),
    }
    ratings = np.asarray(data["storage_apparent_power_rating"], dtype=float)
    if result.get("b_q") is None:
        storage_magnitude = np.abs(storage_power)
    else:
        storage_magnitude = np.hypot(
            storage_power, np.asarray(result["b_q"], dtype=float)
        )
    residuals["storage_power_bound_mva_abs"] = _maximum_violation(
        storage_magnitude, 0.0, ratings
    )

    load = np.asarray(result["p_load"], dtype=float)
    expected_served = load.copy()
    fractions = result.get("load_shed_fraction")
    if fractions is not None:
        indices = np.asarray(data["sheddable_load_indices"], dtype=int)
        fraction_values = np.asarray(fractions, dtype=float)
        expected_served[:, indices] = load[:, indices] - fraction_values * np.maximum(
            load[:, indices], 0.0
        )
        residuals["load_shed_fraction_bound_abs"] = _maximum_violation(
            fraction_values,
            0.0,
            np.asarray(data["load_max_shed_fraction"])[indices],
        )
    residuals["active_load_service_mw_abs"] = float(
        np.max(np.abs(served - expected_served))
    )

    if result.get("curtailment") is not None:
        curtailment = np.asarray(result["curtailment"], dtype=float)
        residuals["curtailment_nonnegativity_mw_abs"] = float(
            max(0.0, -np.min(curtailment))
        )
        nd_active = np.asarray(result["p_nd"], dtype=float)
        residuals["nondispatchable_active_bound_mw_abs"] = _maximum_violation(
            nd_active, 0.0, np.asarray(data["nd_available"], dtype=float)
        )
    if result.get("hvdc_loss") is not None:
        losses = np.asarray(result["hvdc_loss"], dtype=float)
        residuals["hvdc_loss_nonnegativity_mw_abs"] = float(max(0.0, -np.min(losses)))
        hvdc_in = np.asarray(result["p_hvdc_in"], dtype=float)
        hvdc_out = np.asarray(result["p_hvdc_out"], dtype=float)
        residuals["hvdc_box_mw_abs"] = _maximum_violation(hvdc_in, -2.0, -0.5)
        residuals["hvdc_transfer_identity_mw_abs"] = float(
            np.max(np.abs(hvdc_out + 0.99 * hvdc_in))
        )

    if fixture.formulation == "ac":
        reactive = (
            np.asarray(result["Qg"], dtype=float)
            @ np.asarray(data["Cg"], dtype=float).T
        )
        reactive += (
            np.asarray(result["b_q"], dtype=float)
            @ np.asarray(data["Cs"], dtype=float).T
        )
        reactive -= (
            np.asarray(result["q_load_served"], dtype=float)
            @ np.asarray(data["Cload"], dtype=float).T
        )
        if result.get("q_nd") is not None:
            nd_reactive = np.asarray(result["q_nd"], dtype=float)
            reactive += nd_reactive @ np.asarray(data["Cnd"], dtype=float).T
            residuals["nondispatchable_apparent_bound_mva_abs"] = _maximum_violation(
                np.hypot(np.asarray(result["p_nd"], dtype=float), nd_reactive),
                0.0,
                np.asarray(data["nd_apparent_power_rating"], dtype=float),
            )
        residuals["reactive_nodal_balance_mvar_abs"] = float(
            np.max(np.abs(np.asarray(result["q_net"], dtype=float) - reactive))
        )
        expected_q_served = np.asarray(result["q_load"], dtype=float).copy()
        if fractions is not None:
            expected_q_served[:, indices] *= 1.0 - fraction_values
        residuals["reactive_load_service_mvar_abs"] = float(
            np.max(
                np.abs(
                    np.asarray(result["q_load_served"], dtype=float) - expected_q_served
                )
            )
        )
        residuals["generator_reactive_bound_mvar_abs"] = _maximum_violation(
            np.asarray(result["Qg"], dtype=float),
            base * np.asarray(data["Qgmin"]),
            base * np.asarray(data["Qgmax"]),
        )
        ppc = _case(fixture.case_name)
        voltage = np.asarray(result["Vm"], dtype=float)
        residuals["voltage_bound_pu_abs"] = _maximum_violation(
            voltage, ppc["bus"][:, 12], ppc["bus"][:, 11]
        )
        branch_limit = np.asarray(data["branch_rate_a_mva"], dtype=float)
        branch_magnitude = np.maximum(
            np.asarray(result["branch_s_from"], dtype=float),
            np.asarray(result["branch_s_to"], dtype=float),
        )
        residuals["thermal_limit_mva_abs"] = _maximum_violation(
            branch_magnitude, 0.0, branch_limit
        )
        branch_loss = np.asarray(result["branch_p_from"], dtype=float) + np.asarray(
            result["branch_p_to"], dtype=float
        )
        residuals["branch_loss_nonnegativity_mw_abs"] = float(
            max(0.0, -np.min(branch_loss))
        )
    elif fixture.formulation == "lossy_dc":
        residuals["thermal_limit_mw_abs"] = _maximum_violation(
            np.asarray(result["p_flows"], dtype=float),
            -base * np.asarray(data["f_max"]),
            base * np.asarray(data["f_max"]),
        )
        dc_loss = (
            np.asarray(data["r"], dtype=float)
            * np.asarray(result["p_flows"], dtype=float) ** 2
        )
        residuals["dc_loss_nonnegativity_abs"] = float(max(0.0, -np.min(dc_loss)))
    return residuals
