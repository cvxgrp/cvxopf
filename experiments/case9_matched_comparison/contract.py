"""Case-specific physical coordinates shared by both evaluator processes."""

import numpy as np

ACTIVE_LINK_ROWS = (0, 1, 3)
# Frozen inputs independent of the case constructor. Generator rows are in
# source order (buses 1, 2, 30), including coefficients, knots, and padding.
SMOOTH_COST_ROWS = (
    (2, 1500, 0, 3, 0.11, 5, 150),
    (2, 2000, 0, 3, 0.085, 1.2, 600),
    (2, 3000, 0, 3, 0.1225, 1, 335),
)
PWL_COST_ROWS = (
    (1, 0, 0, 4, 0, 0, 100, 2500, 200, 5500, 250, 7250),
    (2, 0, 0, 2, 24.035, -403.5, 0, 0, 0, 0, 0, 0),
    (1, 0, 0, 3, 0, 0, 200, 3000, 300, 5000, 0, 0),
)
CELL_SPECS = {
    "smooth_no_links": ((), SMOOTH_COST_ROWS),
    "smooth_links": (ACTIVE_LINK_ROWS, SMOOTH_COST_ROWS),
    "pwl_no_links": ((), PWL_COST_ROWS),
    "pwl_links": (ACTIVE_LINK_ROWS, PWL_COST_ROWS),
}
CELLS = tuple(CELL_SPECS)
# From/to, sending bounds, fixed loss, proportional loss.
LINK_SPECS = {0: (30, 4, 1, 10, 1, .01), 1: (7, 9, 2, 10, 0, 0), 3: (5, 9, 0, 10, 0, .05)}
# Source row, from bus, to bus, effective lower and upper angle in degrees.
ANGLES = ((0, 1, 4, -360, 2.48), (8, 9, 4, -2, 360))
MW_TOL = 1e-3
V_TOL = 1e-6
ANGLE_TOL_DEG = 1e-5
PYPINS = {"PYPOWER": "5.1.19", "numpy": "2.2.6", "scipy": "1.18.0"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(actual, expected, message, *, atol=1e-10, rtol=0):
    require(
        np.shape(actual) == np.shape(expected)
        and np.allclose(actual, expected, atol=atol, rtol=rtol),
        f"{message}: actual {actual!r}, expected {expected!r}",
    )


def cost_close(actual, expected, message):
    close(actual, expected, message, atol=1e-4, rtol=1e-6)


def encode_case(case):
    return {key: value.tolist() if isinstance(value, np.ndarray) else value
            for key, value in case.items()}


def decode_case(record):
    return {key: np.array(value, dtype=float) if isinstance(value, list) else value
            for key, value in record.items()}


def selected_links(case):
    active = tuple(np.flatnonzero(case["dcline"][:, 2] > 0)) if "dcline" in case else ()
    require(active in ((), ACTIVE_LINK_ROWS), f"unexpected active DC rows: {active}")
    return {int(row): case["dcline"][row] for row in active}


def validate_cell(cell, case):
    """Check the supplied variant against the requested label, before building."""
    require(cell in CELL_SPECS, f"unknown cell: {cell}")
    expected_links, expected_costs = CELL_SPECS[cell]
    links = selected_links(case)
    require(tuple(links) == expected_links,
            f"{cell} active DC link rows: actual {tuple(links)}, expected {expected_links}")
    for row, dc in links.items():
        close(dc[[0, 1, 9, 10, 15, 16]], LINK_SPECS[row], f"{cell} DC link source row {row}")
    close(case["gencost"], expected_costs, f"{cell} generator cost rows")


def native_bounds(row, dc):
    return -dc[10], -1e-6 if row == 3 else -dc[9]


def link_point(row, dc, sent_mw):
    """Native gross and external net terminal coordinates, kept separately."""
    gross = (1 - dc[16]) * sent_mw
    return {
        "row": row, "from_bus": int(dc[0]), "to_bus": int(dc[1]),
        "native_from_mw": -sent_mw, "native_to_mw": gross,
        "fixed_withdrawal_mw": dc[15],
        "net_from_mw": -sent_mw, "net_to_mw": gross - dc[15],
        "pf_mw": sent_mw, "pt_mw": gross - dc[15],
        "physical_loss_mw": sent_mw - (gross - dc[15]),
    }


def audit_loss(sent, delivered, reported_loss):
    physical = sent - delivered
    close(reported_loss, physical, "reported loss sign", atol=MW_TOL)
    require(physical >= -MW_TOL, f"negative physical loss: {physical:g} MW")
    return physical


def audit_link(row, dc, point):
    require(
        (point["row"], point["from_bus"], point["to_bus"]) == (row, int(dc[0]), int(dc[1])),
        f"link {row} identity",
    )
    p, q = point["native_from_mw"], point["native_to_mw"]
    lower, upper = native_bounds(row, dc)
    require(lower - MW_TOL <= p <= upper + MW_TOL, f"link {row} native direction/box")
    fixed = point["fixed_withdrawal_mw"]
    close(fixed, dc[15], f"link {row} fixed withdrawal", atol=MW_TOL)
    pf, pt = point["pf_mw"], point["pt_mw"]
    close([pf, pt], [-p, q - fixed], f"link {row} converter accounting", atol=MW_TOL)
    close(
        [point["net_from_mw"], point["net_to_mw"]], [-pf, pt],
        f"link {row} net injections", atol=MW_TOL,
    )
    loss = audit_loss(pf, pt, point["physical_loss_mw"])
    close([loss, loss], [dc[16] * pf + fixed, -(p + q) + fixed],
          f"link {row} proportional and fixed loss", atol=MW_TOL)
    return loss


def generator_cost(row, p_mw):
    n = int(row[3])
    if row[0] == 2:
        return float(np.polyval(row[4:4 + n], p_mw))
    require(row[0] == 1 and n >= 3, "expected convex PWL or polynomial generator cost")
    x, y = row[4:4 + 2 * n].reshape(n, 2).T
    slopes = np.diff(y) / np.diff(x)
    require(np.all(np.diff(x) > 0) and np.all(np.diff(slopes) >= 0),
            "expected ascending convex PWL cost")
    return float(np.max(y[:-1] + slopes * (p_mw - x[:-1])))


def native_from_net(from_mw, to_mw, withdrawal_mw):
    """Recover CVXOPF's converter injections from net PYPOWER terminals."""
    return [from_mw, to_mw + withdrawal_mw]


def box_excess(lower, upper, value):
    return np.maximum(0, np.maximum(lower - value, value - upper))


def capability(case):
    """Yield upper/lower oriented unit-normal rows in MW/MVAr coordinates."""
    for family, sign in (("PQh", 1), ("PQl", -1)):
        # MATPOWER: PC1=10, PC2=11, QC1MIN=12, QC1MAX=13, QC2MIN=14, QC2MAX=15.
        # The upper face uses the max columns, the lower face the min columns.
        q1col, q2col = (13, 15) if sign == 1 else (12, 14)
        for index in (1, 2):
            gen = case["gen"][index]
            p1, p2, q1, q2 = gen[10], gen[11], gen[q1col], gen[q2col]
            normal = sign * np.array([q1 - q2, p2 - p1], dtype=float)
            normal /= np.linalg.norm(normal)
            bound = float(normal @ [p1, q1])
            yield family, index, normal, bound, ((p1, q1), (p2, q2))


def point_suite(case):
    """Identical, deliberately unsolved points for accounting and sensitivity."""
    ids = tuple(int(bus) for bus in case["bus"][:, 0])
    links = selected_links(case)

    def point(name, pg, *, sent_by_row=None, floor=False, high_q=False):
        theta = np.linspace(-0.017, 0.018, len(ids))
        theta[ids.index(1)] = 0
        if floor:
            theta[ids.index(9)] = theta[ids.index(4)] - np.deg2rad(3)
        return {
            "name": name, "bus_ids": list(ids), "gen_rows": list(range(len(pg))),
            "voltage_pu": np.linspace(0.98, 1.04, len(ids)).tolist(),
            "angle_rad": theta.tolist(),
            "pg_mw": list(pg), "qg_mvar": [3.0, 40.0 if high_q else 4.0, 5.0],
            "links": [link_point(row, dc, (sent_by_row or {}).get(row, 10.0))
                      for row, dc in links.items()],
        }

    points = [
        point("accounting", (110, 140, 80)),
        point("low_send", (140, 95, 95), sent_by_row={0: 1.0}),
        point("violated_angle", (110, 140, 80), floor=True),
        point("violated_capability", (110, 140, 80), high_q=True),
    ]
    if links:
        points.append(point("unequal_bus9_links", (110, 140, 80), sent_by_row={1: 2., 3: 8.}))
    for name, src, dst, deg in (
        ("violated_opposite_lower", 1, 4, -361),
        ("violated_opposite_upper", 9, 4, 361),
    ):
        probe = point(name, (110, 140, 80))
        if src == 1:  # Keep the reference angle exactly zero.
            probe["angle_rad"][ids.index(dst)] = -np.deg2rad(deg)
        else:
            probe["angle_rad"][ids.index(src)] = probe["angle_rad"][ids.index(dst)] + np.deg2rad(deg)
        points.append(probe)
    for family, i, _, _, endpoints in capability(case):
        for k, (p_mw, q_mvar) in enumerate(endpoints):
            pg = points[0]["pg_mw"].copy()
            pg[i] = float(p_mw)
            probe = point(f"{family}_gen{i}_endpoint{k}", pg)
            probe["qg_mvar"][i] = float(q_mvar)
            points.append(probe)
    for i, row in enumerate(case["gencost"]):
        base = points[0]["pg_mw"]
        ends = case["gen"][i, [9, 8]]
        knots = row[4:4 + 2 * int(row[3]):2] if row[0] == 1 else []
        # Midpoints check slopes as well as endpoint and kink costs.
        knots = sorted(set([*ends, *knots]))
        for j, pg in enumerate([*knots, *[(x + y) / 2 for x, y in zip(knots, knots[1:])]]):
            dispatch = base.copy()
            dispatch[i] = float(pg)
            points.append(point(f"cost_{i}_{j}", dispatch))
    return points


def coordinates(case, point):
    """Validate identities and provide engineering-unit arrays."""
    close(point["bus_ids"], case["bus"][:, 0], "point bus identity")
    close(point["gen_rows"], np.arange(len(case["gen"])), "point generator identity")
    links = selected_links(case)
    require(list(links) == [link["row"] for link in point["links"]], "point link row identity")
    for (row, dc), link in zip(links.items(), point["links"], strict=True):
        audit_link(row, dc, link)
    return tuple(np.asarray(point[key], dtype=float) for key in (
        "angle_rad", "voltage_pu", "pg_mw", "qg_mvar",
    ))


def worst(values, identities, units):
    """Largest absolute signed residual and its physical or assembly identity."""
    values = np.asarray(values).ravel()
    require(values.size == len(identities) and np.isfinite(values).all(),
            "residual shape or finite check failed")
    if not values.size:
        return None
    i = int(np.argmax(abs(values)))
    return {"id": identities[i], "signed": float(values[i]), "units": units}
