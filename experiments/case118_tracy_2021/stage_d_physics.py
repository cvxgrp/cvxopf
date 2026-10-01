"""AC-only physical reconstruction extending the common Tracy device audit."""

import numpy as np

from cvxopf.network import (
    make_branch_admittance,
    make_ybus_matpower,
    reindex_case_to_consecutive,
)


def audit_ac_network(a, kwargs, injection, check, box):
    case = kwargs["case"]
    internal, _ = reindex_case_to_consecutive(case)
    adm = make_branch_admittance(internal)
    y = make_ybus_matpower(internal, branch_admittance=adm)
    base = float(case["baseMVA"])
    voltage = a["Vm"] * np.exp(1j * np.deg2rad(a["Va_deg"]))
    physical = voltage * np.conj(voltage @ y.T) * base
    q = np.zeros_like(injection)
    buses = {int(b[0]): i for i, b in enumerate(case["bus"])}
    for devices, values in (
        (kwargs["generators"], a["Qg"]),
        (kwargs["storage"], a["b_q"]),
        (kwargs["nondispatchable"], a["q_nd"]),
        (kwargs["loads"], -a["q_load_served"]),
    ):
        for j, device in enumerate(devices):
            q[:, buses[device.bus]] += values[:, j]

    def absolute(name, values, limit):
        check(name, float(np.max(np.abs(values), initial=0)), limit)

    absolute("ac_p_balance_pu", (injection - physical.real) / base, 1e-6)
    absolute("ac_q_balance_pu", (q - physical.imag) / base, 1e-6)
    absolute("ac_p_reporting_pu", (a["p_net"] - physical.real) / base, 1e-6)
    absolute("ac_q_reporting_pu", (a["q_net"] - physical.imag) / base, 1e-6)
    box("voltage_pu", a["Vm"], case["bus"][:, 12], case["bus"][:, 11], 1e-6)
    box(
        "generator_q_mvar",
        a["Qg"],
        np.array([g.q_min_mvar for g in kwargs["generators"]]),
        np.array([g.q_max_mvar for g in kwargs["generators"]]),
    )
    demand_q = kwargs["df_load_q"].to_numpy()
    for name, expected in (
        ("q_load_shed", demand_q * a["load_shed_fraction"]),
        ("q_load_served", demand_q * (1 - a["load_shed_fraction"])),
    ):
        absolute(name + "_reporting", a[name] - expected, 1e-4)

    def capability(name, magnitude, ratings):
        check(
            name + "_mva",
            float(np.maximum(magnitude - ratings, 0).max(initial=0)),
            1e-4,
        )
        check(
            name + "_normalized",
            float(np.maximum((magnitude / ratings) ** 2 - 1, 0).max(initial=0)),
            1e-7,
        )

    capability(
        "storage_circle",
        np.hypot(a["b"], a["b_q"]),
        np.array([s.apparent_power_rating for s in kwargs["storage"]]),
    )
    capability(
        "nd_circle",
        np.hypot(a["p_nd"], a["q_nd"]),
        np.array([d.apparent_power_rating for d in kwargs["nondispatchable"]]),
    )
    vf, vt = voltage[:, adm.from_bus], voltage[:, adm.to_bus]
    for side, values in (
        ("from", vf * np.conj(vf * adm.yff + vt * adm.yft) * base),
        ("to", vt * np.conj(vf * adm.ytf + vt * adm.ytt) * base),
    ):
        values = values[:, adm.status]
        for component, expected in (
            ("p", values.real),
            ("q", values.imag),
            ("s", np.abs(values)),
        ):
            absolute(
                f"branch_{component}_{side}_reporting",
                a[f"branch_{component}_{side}"] - expected,
                1e-4,
            )
        ratings = adm.rate_a_mva[adm.status]
        if not np.all(np.isfinite(ratings) & (ratings > 0)):
            raise ValueError("Tracy AC requires finite rated active branches")
        capability("branch_" + side, np.abs(values), ratings)
