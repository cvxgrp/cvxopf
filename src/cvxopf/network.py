"""
Network topology and admittance matrix construction.

All functions operate on MATPOWER-format case dicts with 0-based consecutive
bus indexing (i.e., after reindex_case_to_consecutive has been applied).
No CVXPY in this module.
"""

import numpy as np

# MATPOWER column indices
BUS_I    = 0
BUS_TYPE = 1
PD       = 2
QD       = 3
GS       = 4
BS       = 5
VMAX     = 11
VMIN     = 12

F_BUS    = 0
T_BUS    = 1
BR_R     = 2
BR_X     = 3
BR_B     = 4
BR_STATUS = 10
TAP      = 8
SHIFT    = 9

GEN_BUS    = 0
GEN_STATUS = 7


def reindex_case_to_consecutive(case: dict) -> tuple[dict, dict | None]:
    """
    Remap bus IDs to 0-based consecutive integers.

    If the bus IDs are already 0-based consecutive, the case is returned
    unchanged and the mapping is None. Otherwise, a remapped copy is returned
    alongside the ext_to_int mapping dict.

    Parameters
    ----------
    case : dict
        MATPOWER-format case dict.

    Returns
    -------
    case : dict
        Case with remapped bus IDs (or original if already consecutive).
    ext_to_int : dict | None
        Mapping from original bus IDs to new 0-based IDs, or None if no
        remapping was needed.

    Raises
    ------
    ValueError
        If duplicate BUS_I values are present, or if branch/gen reference
        a bus ID not in the bus table.
    """
    bus    = case["bus"].copy()
    branch = case["branch"].copy()
    gen    = case["gen"].copy()

    ext = bus[:, BUS_I].astype(int)
    nb  = bus.shape[0]

    if np.unique(ext).size != ext.size:
        raise ValueError("Duplicate BUS_I values found in bus table.")

    if np.array_equal(ext, np.arange(nb)):
        return {**case, "bus": bus, "branch": branch, "gen": gen}, None

    ext_to_int = {ext[i]: i for i in range(nb)}
    ext_set    = set(ext_to_int.keys())

    def remap(arr: np.ndarray, name: str) -> np.ndarray:
        arr     = arr.astype(int)
        missing = sorted(set(arr.tolist()) - ext_set)
        if missing:
            raise ValueError(f"{name} references unknown bus IDs: {missing}")
        return np.fromiter(
            (ext_to_int[i] for i in arr), dtype=int, count=arr.size
        )

    bus[:, BUS_I]      = np.arange(nb)
    branch[:, F_BUS]   = remap(branch[:, F_BUS], "branch F_BUS")
    branch[:, T_BUS]   = remap(branch[:, T_BUS], "branch T_BUS")
    gen[:, GEN_BUS]    = remap(gen[:, GEN_BUS],  "gen GEN_BUS")

    return {**case, "bus": bus, "branch": branch, "gen": gen}, ext_to_int


def make_ybus_matpower(case: dict) -> np.ndarray:
    """
    Build the complex nodal admittance matrix using MATPOWER conventions.

    Handles off-nominal tap ratios and phase shifts. Adds diagonal bus shunts.
    Assumes the case has already been reindexed to 0-based consecutive bus IDs.

    Parameters
    ----------
    case : dict
        MATPOWER-format case dict with 0-based consecutive bus IDs.

    Returns
    -------
    Y : np.ndarray, shape (nb, nb), dtype complex128
        Nodal admittance matrix.

    Raises
    ------
    ValueError
        If any in-service branch has r = x = 0.
    """
    baseMVA = float(case["baseMVA"])
    bus     = case["bus"]
    branch  = case["branch"]
    nb      = bus.shape[0]
    Y       = np.zeros((nb, nb), dtype=np.complex128)

    for e in range(branch.shape[0]):
        if int(branch[e, BR_STATUS]) == 0:
            continue

        f     = int(branch[e, F_BUS])
        t     = int(branch[e, T_BUS])
        r     = float(branch[e, BR_R])
        x     = float(branch[e, BR_X])
        b     = float(branch[e, BR_B])
        tap   = float(branch[e, TAP])
        shift = float(branch[e, SHIFT])

        z = r + 1j * x
        if z == 0:
            raise ValueError(
                f"Branch {e} (bus {f} -> {t}) has r = x = 0; unsupported."
            )
        y   = 1.0 / z
        ysh = 1j * b / 2.0

        if tap == 0.0:
            tap = 1.0
        tau = tap * np.exp(1j * np.deg2rad(shift))

        Yff = (y + ysh) / (tau * np.conj(tau))
        Yft = -y / np.conj(tau)
        Ytf = -y / tau
        Ytt = y + ysh

        Y[f, f] += Yff
        Y[f, t] += Yft
        Y[t, f] += Ytf
        Y[t, t] += Ytt

    gs  = bus[:, GS].astype(float) / baseMVA
    bs  = bus[:, BS].astype(float) / baseMVA
    Y  += np.diag(gs + 1j * bs)

    return Y


def make_incidence_matrix(case: dict) -> np.ndarray:
    """
    Build the generator-to-bus incidence matrix Cg.

    Cg[i, k] = 1 if generator k is in-service and connected to bus i,
    else 0. Assumes 0-based consecutive bus IDs.

    Parameters
    ----------
    case : dict
        MATPOWER-format case dict with 0-based consecutive bus IDs.

    Returns
    -------
    Cg : np.ndarray, shape (nb, ng)
        Generator incidence matrix.
    """
    gen     = case["gen"]
    nb      = case["bus"].shape[0]
    ng      = gen.shape[0]
    status  = gen[:, GEN_STATUS].astype(int)
    gen_bus = gen[:, GEN_BUS].astype(int)

    Cg = np.zeros((nb, ng))
    for k in range(ng):
        if status[k] == 1:
            Cg[gen_bus[k], k] = 1.0
    return Cg


def make_ybus_sparsity_mask(
    Y: np.ndarray, tol: float = 0.0
) -> tuple[tuple, tuple]:
    """
    Compute the sparsity mask of Y for use in DNLP constraint construction.

    Parameters
    ----------
    Y : np.ndarray, shape (nb, nb), dtype complex128
        Nodal admittance matrix.
    tol : float
        Entries with |G[i,j]| <= tol AND |B[i,j]| <= tol are treated as zero.
        Default 0.0 (exact sparsity).

    Returns
    -------
    E : tuple of np.ndarray
        (row_indices, col_indices) of nonzero entries, as returned by np.where.
    Z : tuple of np.ndarray
        (row_indices, col_indices) of zero entries.
    """
    G    = np.real(Y)
    B    = np.imag(Y)
    mask = (np.abs(G) > tol) | (np.abs(B) > tol)
    return np.where(mask), np.where(~mask)
