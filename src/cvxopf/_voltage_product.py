"""Sparse real power maps for the voltage-product network representation.

No CVXPY, device preparation, or admittance formulas live here. The caller
validates/reindexes the case and supplies the authoritative ``network.py``
admittances. All powers are per-unit, positive from the bus into the network
(or from the bus into a branch terminal).
"""

from dataclasses import dataclass

import numpy as np
from scipy import sparse

from cvxopf.network import BranchAdmittance


@dataclass(frozen=True)
class PowerMap:
    """Real sparse maps: P = p @ x, Q = q @ x, with x = [w; c; s].

    For a horizon, x has shape (nb + 2 * npairs, T); the very same maps apply
    to every interval in one matrix multiplication. No time expansion is needed.
    """

    p: sparse.csr_matrix
    q: sparse.csr_matrix


@dataclass(frozen=True)
class VoltageProductMaps:
    """Network maps and deterministic edge identities, independent of horizon.

    ``pairs`` has shape (npairs, 2), lexicographically sorted with i < j.
    Each stored product means V_i * conj(V_j). Parallel branches share a pair.
    ``branch_pair`` is -1 for inactive rows and self-loops; ``branch_orientation``
    is +1/-1 for forward/reverse active rows and zero otherwise. Self-loops use
    the corresponding diagonal w, never an independent product. Branch maps
    retain original branch row order, with exact zero rows for inactive branches.
    """

    nb: int
    pairs: np.ndarray
    branch_pair: np.ndarray
    branch_orientation: np.ndarray
    nodal: PowerMap
    branch_from: PowerMap
    branch_to: PowerMap


def _power_map(
    nrows: int,
    nb: int,
    pair_lookup: dict[tuple[int, int], int],
    entries: list[tuple[int, int, int, complex]],
) -> PowerMap:
    """Map terms ``coefficient * W_ij`` to real P/Q sparse coefficients."""
    p_rows, p_cols, p_values = [], [], []
    q_rows, q_cols, q_values = [], [], []
    npairs = len(pair_lookup)
    for row, i, j, coefficient in entries:
        # Diagonal W_ii = w_i is real. Off-diagonal entries use conjugation
        # when traversed against the canonical i < j orientation.
        if i == j:
            columns = (i,)
            p_terms = (coefficient.real,)
            q_terms = (coefficient.imag,)
        else:
            pair = pair_lookup[min(i, j), max(i, j)]
            sign = 1 if i < j else -1
            columns = (nb + pair, nb + npairs + pair)
            p_terms = (coefficient.real, -sign * coefficient.imag)
            q_terms = (coefficient.imag, sign * coefficient.real)
        for column, p_value, q_value in zip(columns, p_terms, q_terms, strict=True):
            if p_value != 0:
                p_rows.append(row)
                p_cols.append(column)
                p_values.append(p_value)
            if q_value != 0:
                q_rows.append(row)
                q_cols.append(column)
                q_values.append(q_value)
    shape = (nrows, nb + 2 * npairs)
    p = sparse.csr_matrix((p_values, (p_rows, p_cols)), shape=shape)
    q = sparse.csr_matrix((q_values, (q_rows, q_cols)), shape=shape)
    # Self-loops and repeated entries may cancel after sparse aggregation.
    p.eliminate_zeros()
    q.eliminate_zeros()
    return PowerMap(p, q)


def make_voltage_product_maps(
    ybus: np.ndarray, admittance: BranchAdmittance,
) -> VoltageProductMaps:
    """Construct full-admittance maps from matching, reindexed network data.

    Ybus must be the unthresholded ``make_ybus_matpower`` result. The pair set
    comes from active branch topology, not Ybus nonzeros: cancelling parallel
    admittances still require voltage products for their separate terminal flows.
    No symmetry assumption is made about Ybus or transformer coefficients.
    """
    ybus = np.asarray(ybus, dtype=complex)
    if ybus.ndim != 2 or ybus.shape[0] != ybus.shape[1]:
        raise ValueError("Ybus must be square")
    if not np.all(np.isfinite(ybus)):
        raise ValueError("Ybus must contain finite admittances")
    nb = ybus.shape[0]
    active = np.flatnonzero(admittance.status)
    pair_keys = sorted({
        (min(int(admittance.from_bus[e]), int(admittance.to_bus[e])),
         max(int(admittance.from_bus[e]), int(admittance.to_bus[e])))
        for e in active
        if admittance.from_bus[e] != admittance.to_bus[e]
    })
    pairs = np.asarray(pair_keys, dtype=int).reshape(-1, 2)
    lookup = {pair: index for index, pair in enumerate(pair_keys)}
    nl = len(admittance.from_bus)
    branch_pair = np.full(nl, -1, dtype=int)
    orientation = np.zeros(nl, dtype=int)

    from_entries, to_entries = [], []
    for e in active:
        f, t = int(admittance.from_bus[e]), int(admittance.to_bus[e])
        if not (0 <= f < nb and 0 <= t < nb):
            raise ValueError("Active branch endpoints must use reindexed Ybus indices")
        if f != t:
            branch_pair[e] = lookup[min(f, t), max(f, t)]
            orientation[e] = 1 if f < t else -1
        from_entries.extend((
            (int(e), f, f, np.conj(admittance.yff[e])),
            (int(e), f, t, np.conj(admittance.yft[e])),
        ))
        to_entries.extend((
            (int(e), t, t, np.conj(admittance.ytt[e])),
            (int(e), t, f, np.conj(admittance.ytf[e])),
        ))

    rows, cols = np.nonzero(ybus)
    nodal_entries = []
    for i, j in zip(rows, cols, strict=True):
        if i != j and (min(i, j), max(i, j)) not in lookup:
            raise ValueError("Ybus contains an off-diagonal entry outside active topology")
        nodal_entries.append((int(i), int(i), int(j), np.conj(ybus[i, j])))

    return VoltageProductMaps(
        nb=nb,
        pairs=pairs,
        branch_pair=branch_pair,
        branch_orientation=orientation,
        nodal=_power_map(nb, nb, lookup, nodal_entries),
        branch_from=_power_map(nl, nb, lookup, from_entries),
        branch_to=_power_map(nl, nb, lookup, to_entries),
    )
