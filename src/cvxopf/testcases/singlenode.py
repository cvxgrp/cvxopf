"""
Single-node (copper-plate) test case constructor.

This module provides make_singlenode_case, a convenience function to build
minimal MATPOWER-format case dicts for the singlenode_dc formulation.
"""

from __future__ import annotations

import numpy as np


def make_singlenode_case(
    P_load_MW: float,
    generators: list[dict],
    baseMVA: float = 100.0,
) -> dict:
    """
    Build a minimal MATPOWER-format case dict for single-node dispatch.

    Parameters
    ----------
    P_load_MW : float
        Total system real load in MW (>= 0).
    generators : list[dict]
        List of generator specifications. Each dict must contain:
        - "P_max_MW": float, required
        - "cost_coeffs": tuple or list of exactly 3 floats (c0, c1, c2), required
        - "P_min_MW": float, optional, default 0.0
    baseMVA : float, default 100.0
        System base MVA.

    Returns
    -------
    dict
        MATPOWER-format case dict with keys: "baseMVA", "bus", "gen", "gencost",
        "branch". The "branch" table is empty (zero rows).

    Notes
    -----
    - The bus table contains a single bus (external ID 1) with type 3 (reference).
    - All generators are connected to this single bus.
    - This dict is accepted by _parse_singlenode_dc_case but would be rejected
      by validate_case due to the empty branch table — this is intentional.
    """
    # MATPOWER column indices
    # bus table
    BUS_I = 0
    BUS_TYPE = 1
    PD = 2
    QD = 3
    VM = 7
    VA = 8
    BASE_KV = 9
    VMAX = 11
    VMIN = 12

    # gen table
    GEN_BUS = 0
    PG = 1
    QG = 2
    QMAX = 3
    QMIN = 4
    VG = 5
    MBASE = 6
    GEN_STATUS = 7
    PMAX = 8
    PMIN = 9

    # gencost table
    MODEL = 0
    STARTUP = 1
    SHUTDOWN = 2
    NCOST = 3
    C2 = 4
    C1 = 5
    C0 = 6

    ng = len(generators)

    # Build bus table: single bus
    bus = np.zeros((1, 13))
    bus[0, BUS_I] = 1
    bus[0, BUS_TYPE] = 3  # reference bus
    bus[0, PD] = P_load_MW
    bus[0, QD] = 0.0
    bus[0, VM] = 1.0
    bus[0, VA] = 0.0
    bus[0, BASE_KV] = 100.0
    bus[0, VMAX] = 1.1
    bus[0, VMIN] = 0.9

    # Build gen table
    gen = np.zeros((ng, 21))
    for k in range(ng):
        gen[k, GEN_BUS] = 1
        gen[k, PG] = 0.0
        gen[k, QG] = 0.0
        gen[k, QMAX] = 0.0
        gen[k, QMIN] = 0.0
        gen[k, VG] = 1.0
        gen[k, MBASE] = baseMVA
        gen[k, GEN_STATUS] = 1
        gen[k, PMAX] = generators[k]["P_max_MW"]
        gen[k, PMIN] = generators[k].get("P_min_MW", 0.0)

    # Build gencost table
    gencost = np.zeros((ng, 7))
    for k in range(ng):
        gencost[k, MODEL] = 2
        gencost[k, STARTUP] = 0.0
        gencost[k, SHUTDOWN] = 0.0
        gencost[k, NCOST] = 3
        c0, c1, c2 = generators[k]["cost_coeffs"]
        # MATPOWER format: c2, c1, c0 (quadratic, linear, constant)
        gencost[k, C2] = c2
        gencost[k, C1] = c1
        gencost[k, C0] = c0

    # Empty branch table
    branch = np.zeros((0, 13))

    return {
        "baseMVA": baseMVA,
        "bus": bus,
        "gen": gen,
        "gencost": gencost,
        "branch": branch,
    }