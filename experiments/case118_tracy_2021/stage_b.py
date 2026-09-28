"""Frozen comparison grid, verified inputs, and independent primal audit."""

from dataclasses import asdict, dataclass, replace
import json

import numpy as np
import pandas as pd

from cvxopf import OPFOptions
from experiments.case118_annual_hierarchy.pglib_case import array_sha256
from .model_inputs import model_inputs
from .plot_window_selection import APPROVED_WINDOWS
from .prepare import HERE, digest, prepare

SOLVER_OPTIONS = dict(
    tol_gap_abs=1e-10, tol_gap_rel=1e-10, tol_feas=1e-10, max_iter=5000, max_threads=1
)
LIMITS = dict(wall_seconds=1800.0, rss_mib=16384.0, poll_seconds=1.0)
TOLERANCES = dict(
    power_mw=1e-4,
    box=2e-5,
    soc_mwh=1e-4,
    endpoint_mwh=1e-3,
    fraction=1e-8,
    cost_abs=1e-4,
    cost_rel=1e-10,
    energy_mwh=1e-4,
)


@dataclass(frozen=True)
class Arm:
    window: str
    start: int
    stop: int
    rho: float
    throughput: float
    formulation: str


def arms() -> list[Arm]:
    """Shortest first, then calendar start; paired formulations per setting."""
    windows = [(n, a, b) for n, a, b, _, _ in APPROVED_WINDOWS]
    windows.append(("Ordinary control", "2021-01-14", "2021-01-28"))
    origin = pd.Timestamp("2021-01-01")
    offsets = [
        (
            n,
            int((pd.Timestamp(a) - origin).total_seconds() / 3600),
            int((pd.Timestamp(b) - origin).total_seconds() / 3600),
        )
        for n, a, b in windows
    ]
    return [
        Arm(n, a, b, rho, weight, formulation)
        for n, a, b in sorted(offsets, key=lambda row: (row[2] - row[1], row[1]))
        for rho in (1 / 3, 0.001)
        for weight in (0.01, 0.0001, 1.0)
        for formulation in ("singlenode_dc", "lossy_dc")
    ]


def verified_inputs():
    """Reconstruct from the owner CSV; compare to approved Stage A evidence."""
    manifest = json.loads((HERE / "stage_a/manifest.json").read_text())
    p = prepare()  # Enforces the pinned CSV and network source hashes.
    for name in ("load_p_mw", "load_q_mvar", "nd_available_mw"):
        if array_sha256(getattr(p, name)) != manifest["arrays"][name]["sha256"]:
            raise ValueError(f"Stage A array mismatch: {name}")
    for name in ("buses", "generators", "renewables", "batteries"):
        path = HERE / f"stage_a/{name}.csv"
        if digest(path) != manifest["review_artifacts"][path.name]:
            raise ValueError(f"Stage A table changed: {name}")
        pd.testing.assert_frame_equal(
            getattr(p, name).reset_index(drop=True),
            pd.read_csv(path),
            check_dtype=False,
            rtol=1e-12,
            atol=1e-10,
        )
    return p


def inputs_for_arm(p, arm: Arm) -> dict:
    kwargs = model_inputs(p, arm.start, arm.stop)
    kwargs["generators"] = [
        replace(
            g,
            cost_coeffs=(
                g.cost_coeffs[0],
                g.cost_coeffs[1],
                arm.rho * g.cost_coeffs[1] / g.p_max_mw,
            ),
        )
        if g.p_max_mw > 0
        else g
        for g in kwargs["generators"]
    ]
    kwargs["storage"] = [
        replace(s, aging_weight=arm.throughput) for s in kwargs["storage"]
    ]
    kwargs.update(formulation=arm.formulation, options=OPFOptions(loss_weight=1.0))
    return kwargs


def audit_result(result: dict, kwargs: dict, named_costs: dict) -> dict:
    """Reconstruct physics/accounting from input devices, not build expressions.

    This audit is deliberately specific to the approved ideal-storage, all-loads
    sheddable, no-HVDC fleet. It refuses missing/nonfinite/misshaped payloads.
    """
    checks = {}
    limits = {}

    def check(name, residual, tolerance):
        checks[name] = float(residual)
        limits[name] = float(tolerance)

    def maximum(x):
        return float(np.max(np.abs(x), initial=0))

    def box(name, values, lower, upper, tolerance=TOLERANCES["box"]):
        check(
            name,
            max(
                float(np.max(lower - values, initial=0)),
                float(np.max(values - upper, initial=0)),
            ),
            tolerance,
        )

    T = kwargs["T"]
    gen, storage = kwargs["generators"], kwargs["storage"]
    loads, nd = kwargs["loads"], kwargs["nondispatchable"]
    case = kwargs["case"]
    bus = np.asarray(case["bus"])
    branch = np.asarray(case["branch"])
    active = branch[:, 10] > 0
    branch = branch[active]
    shape = dict(
        Pg=(T, len(gen)),
        b=(T, len(storage)),
        soc=(T, len(storage)),
        p_nd=(T, len(nd)),
        curtailment=(T, len(nd)),
        p_load=(T, len(loads)),
        q_load=(T, len(loads)),
        p_load_served=(T, len(loads)),
        p_load_shed=(T, len(loads)),
        load_shed_fraction=(T, len(loads)),
        p_load_shed_total=(T,),
        energy_not_served_by_load=(len(loads),),
        energy_not_served=(),
        objective=(),
        storage_cost=(),
        load_shedding_cost=(),
    )
    dc = kwargs["formulation"] == "lossy_dc"
    shape["p_net"] = (T, len(bus)) if dc else (T,)
    if dc:
        shape["p_flows"] = (T, len(branch))
    try:
        arrays = {k: np.asarray(result[k], dtype=float) for k in shape}
        if any(
            v.shape != shape[k] or not np.isfinite(v).all() for k, v in arrays.items()
        ):
            raise ValueError("missing, nonfinite or misshaped primal")
        if list(result["storage_device_ids"]) != [s.device_id for s in storage]:
            raise ValueError("storage identity mismatch")
    except (KeyError, TypeError, ValueError) as exc:
        return dict(passed=False, reason=str(exc), residuals={}, limits={})
    a = arrays
    dt = kwargs["delta"]
    demand = kwargs["df_load_p"].to_numpy()
    available = kwargs["df_nd"].to_numpy()
    cap = np.array([s.capacity for s in storage])
    power = np.array([s.apparent_power_rating for s in storage])
    box(
        "generator_bounds_mw",
        a["Pg"],
        np.array([g.p_min_mw for g in gen]),
        np.array([g.p_max_mw for g in gen]),
    )
    box("storage_power_bounds_mw", a["b"], -power, power)
    box("soc_bounds_mwh", a["soc"], 0, cap)
    box(
        "nd_bounds_mw",
        a["p_nd"],
        0,
        np.minimum(available, [d.apparent_power_rating for d in nd]),
    )
    box(
        "shedding_fraction",
        a["load_shed_fraction"],
        0,
        [d.max_shed_fraction for d in loads],
        TOLERANCES["fraction"],
    )
    box("shed_bounds_mw", a["p_load_shed"], 0, demand)
    for key, expected in (
        ("curtailment", available - a["p_nd"]),
        ("p_load", demand),
        ("q_load", kwargs["df_load_q"].to_numpy()),
        ("p_load_shed", demand * a["load_shed_fraction"]),
        ("p_load_served", demand - a["p_load_shed"]),
        ("p_load_shed_total", a["p_load_shed"].sum(axis=1)),
    ):
        check(key + "_reporting", maximum(a[key] - expected), TOLERANCES["power_mw"])
    check(
        "soc_recurrence_mwh",
        maximum(
            np.diff(np.vstack(([s.initial_soc for s in storage], a["soc"])), axis=0)
            + dt * a["b"]
        ),
        TOLERANCES["soc_mwh"],
    )
    check(
        "terminal_soc_mwh",
        maximum(a["soc"][-1] - [s.terminal_soc for s in storage]),
        TOLERANCES["endpoint_mwh"],
    )
    injection = np.zeros((T, len(bus)))
    bus_index = {int(row[0]): i for i, row in enumerate(bus)}
    for devices, values in (
        (gen, a["Pg"]),
        (storage, a["b"]),
        (nd, a["p_nd"]),
        (loads, -a["p_load_served"]),
    ):
        for j, device in enumerate(devices):
            injection[:, bus_index[device.bus]] += values[:, j]
    reported = injection if dc else injection.sum(axis=1)
    check(
        "injection_reporting_mw", maximum(a["p_net"] - reported), TOLERANCES["power_mw"]
    )
    balance = injection.copy() if dc else reported
    loss = 0.0
    if dc:
        if not np.all(np.isfinite(branch[:, 5]) & (branch[:, 5] > 0)):
            raise ValueError("Tracy audit requires rated active branches")
        box("branch_bounds_mw", a["p_flows"], -branch[:, 5], branch[:, 5])
        for j, row in enumerate(branch):
            balance[:, bus_index[int(row[0])]] -= a["p_flows"][:, j]
            balance[:, bus_index[int(row[1])]] += a["p_flows"][:, j]
        loss = float(dt * np.sum(branch[:, 2] * (a["p_flows"] / case["baseMVA"]) ** 2))
    check("balance_mw", maximum(balance), TOLERANCES["power_mw"])
    costs = dict(
        generator_cost=float(
            dt
            * sum(
                np.sum(
                    g.cost_coeffs[0]
                    + g.cost_coeffs[1] * a["Pg"][:, j]
                    + g.cost_coeffs[2] * a["Pg"][:, j] ** 2
                )
                for j, g in enumerate(gen)
            )
        ),
        storage_cost=float(
            dt * np.sum(np.abs(a["b"]) * [s.aging_weight for s in storage])
        ),
        load_shedding_cost=float(
            dt * np.sum(a["p_load_shed"] * [d.shedding_cost_per_mwh for d in loads])
        ),
    )
    if dc:
        costs["dc_loss_cost"] = loss
    for key, expected in {**costs, "objective": sum(costs.values())}.items():
        actual = result.get(key, named_costs.get(key))
        residual = abs(float(actual) - expected) if actual is not None else float("inf")
        check(
            key + "_accounting",
            residual,
            TOLERANCES["cost_abs"] + TOLERANCES["cost_rel"] * abs(expected),
        )
    ens = dt * a["p_load_shed"].sum(axis=0)
    check(
        "ens_by_load_mwh",
        maximum(a["energy_not_served_by_load"] - ens),
        TOLERANCES["energy_mwh"],
    )
    check(
        "ens_mwh",
        abs(float(a["energy_not_served"]) - ens.sum()),
        TOLERANCES["energy_mwh"],
    )
    passed = result["status"] == "optimal" and all(
        np.isfinite(v) and v <= limits[k] for k, v in checks.items()
    )
    return dict(
        passed=passed,
        residuals=checks,
        limits=limits,
        costs=costs,
        metrics=dict(
            energy_not_served_mwh=float(ens.sum()),
            curtailed_mwh=float(dt * np.sum(available - a["p_nd"])),
            throughput_mwh=float(dt * np.abs(a["b"]).sum()),
        ),
    )


def study_spec() -> dict:
    return dict(
        schema_version=1,
        arms=[asdict(a) for a in arms()],
        solver="CLARABEL",
        backend="SCIPY",
        solver_options=SOLVER_OPTIONS,
        limits=LIMITS,
        tolerances=TOLERANCES,
        annual_execution=False,
    )
