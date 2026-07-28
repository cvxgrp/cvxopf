"""
Single-node (copper-plate) test case constructor.

This module provides make_singlenode_case, a convenience function to build
minimal MATPOWER-format case dicts for the singlenode_dc formulation.
"""

from __future__ import annotations

import numpy as np

from cvxopf.generator import (
    DispatchableGenerator,
    _validate_generators,
    generator_gencost,
    generator_matpower_gen,
)


def make_singlenode_case(
    P_load_MW: float,
    generators: list[DispatchableGenerator],
    baseMVA: float = 100.0,
) -> dict:
    """
    Build a minimal MATPOWER-format case dict for single-node dispatch.

    Parameters
    ----------
    P_load_MW : float
        Total system real load in MW (>= 0).
    generators : list[DispatchableGenerator]
        Dispatchable generators connected to the single bus (external ID 1).
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

    _validate_generators(generators, {1})

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

    gen = generator_matpower_gen(generators, baseMVA)
    gencost = generator_gencost(generators)

    # Empty branch table
    branch = np.zeros((0, 13))

    return {
        "baseMVA": baseMVA,
        "bus": bus,
        "gen": gen,
        "gencost": gencost,
        "branch": branch,
    }
