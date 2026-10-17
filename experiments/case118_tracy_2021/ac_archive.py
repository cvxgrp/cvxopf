"""Shared non-solving checks for retained AC initialization and primal handoff."""

import numpy as np

from cvxopf.hierarchical import IPOPTStartEvidence


def verify_x0(start, raw_x0):
    raw_x0 = dict(raw_x0)
    raw_x0.pop("iteration")
    evidence = IPOPTStartEvidence(**raw_x0)
    seen = set()
    for item in evidence.layout:
        if item["is_original_variable"]:
            name = item["name"]
            seen.add(name)
            expected = np.asarray(start["assigned_start"][name]).flatten(order="F")
            np.testing.assert_array_equal(
                evidence.complete_x0[item["start"]:item["stop"]], expected)
    if seen != set(start["assigned_start"]):
        raise ValueError("retained IPOPT x0 omits model variables")


def verify_logical(logical, result, base, horizon):
    if not logical or any(not np.isfinite(np.asarray(v)).all() for v in logical.values()):
        raise ValueError("accepted source lacks finite logical solution")
    aliases = {"Pg": "Pg", "Qg": "Qg", "b": "b", "b_q": "b_q", "soc": "soc",
        "p_nd": "p_nd", "q_nd": "q_nd", "load_shed_fraction": "load_shed_fraction",
        "v": "Vm", "theta": "Va_deg", "p": "p_net", "q": "q_net"}
    for family, public in aliases.items():
        for t in range(horizon):
            values = np.asarray(logical[f"{family}_{t}"]).reshape(-1)
            if family in {"Pg", "Qg", "p", "q"}:
                values = values*base
            elif family == "theta":
                values = np.rad2deg(values)
            np.testing.assert_allclose(values, result[public][t], rtol=1e-12, atol=1e-10)
