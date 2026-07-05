"""
Input validation and time-series data preparation.

No CVXPY in this module.
"""

import numpy as np
import pandas as pd

# MATPOWER column counts
_BUS_COLS    = 13
_GEN_COLS    = 21
_BRANCH_COLS = 13
_GENCOST_MIN_COLS = 5  # model, startup, shutdown, n, plus at least one coeff

# MATPOWER bus type for slack
_BUS_TYPE_REF = 3

# Column indices used for cross-reference checks
_BUS_I      = 0
_BUS_TYPE   = 1
_GEN_BUS    = 0
_GEN_STATUS = 7
_F_BUS      = 0
_T_BUS      = 1


def validate_case(case: dict) -> None:
    """
    Validate a MATPOWER-format case dict.

    Checks required keys, array shapes, bus ID uniqueness, exactly one slack
    bus, and that all branch/gen bus references exist in the bus table.

    Parameters
    ----------
    case : dict
        MATPOWER-format case dict.

    Raises
    ------
    ValueError
        On any structural or referential inconsistency, with a descriptive
        message.
    """
    required = {"bus", "branch", "gen", "gencost", "baseMVA"}
    missing  = required - set(case.keys())
    if missing:
        raise ValueError(f"Case is missing required keys: {sorted(missing)}")

    bus     = np.asarray(case["bus"])
    branch  = np.asarray(case["branch"])
    gen     = np.asarray(case["gen"])
    gencost = np.asarray(case["gencost"])

    if bus.ndim != 2 or bus.shape[1] < _BUS_COLS:
        raise ValueError(
            f"bus array must have at least {_BUS_COLS} columns; "
            f"got shape {bus.shape}."
        )
    if branch.ndim != 2 or branch.shape[1] < _BRANCH_COLS:
        raise ValueError(
            f"branch array must have at least {_BRANCH_COLS} columns; "
            f"got shape {branch.shape}."
        )
    if gen.ndim != 2 or gen.shape[1] < _GEN_COLS:
        raise ValueError(
            f"gen array must have at least {_GEN_COLS} columns; "
            f"got shape {gen.shape}."
        )
    if gencost.ndim != 2 or gencost.shape[1] < _GENCOST_MIN_COLS:
        raise ValueError(
            f"gencost array must have at least {_GENCOST_MIN_COLS} columns; "
            f"got shape {gencost.shape}."
        )

    bus_ids = bus[:, _BUS_I].astype(int)
    if np.unique(bus_ids).size != bus_ids.size:
        raise ValueError("Duplicate BUS_I values found in bus table.")

    bus_id_set = set(bus_ids.tolist())

    slack_mask = bus[:, _BUS_TYPE].astype(int) == _BUS_TYPE_REF
    n_slack    = int(slack_mask.sum())
    if n_slack != 1:
        raise ValueError(
            f"Exactly one slack bus (BUS_TYPE=3) required; found {n_slack}."
        )

    for col, name in ((_F_BUS, "F_BUS"), (_T_BUS, "T_BUS")):
        bad = sorted(
            set(branch[:, col].astype(int).tolist()) - bus_id_set
        )
        if bad:
            raise ValueError(
                f"branch {name} references unknown bus IDs: {bad}"
            )

    bad_gen_buses = sorted(
        set(gen[:, _GEN_BUS].astype(int).tolist()) - bus_id_set
    )
    if bad_gen_buses:
        raise ValueError(
            f"gen GEN_BUS references unknown bus IDs: {bad_gen_buses}"
        )

    ng_case = gen.shape[0]
    ng_cost = gencost.shape[0]
    if ng_cost != ng_case:
        raise ValueError(
            f"gencost has {ng_cost} rows but gen has {ng_case} rows; "
            "they must match."
        )


def load_timeseries_from_dataframe(
    df_P: pd.DataFrame,
    df_Q: pd.DataFrame,
    case: dict,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Convert nodal load time-series DataFrames to per-unit numpy arrays.

    Parameters
    ----------
    df_P : pd.DataFrame, shape (T, nb)
        Active load time series in MW. Columns should correspond to buses.
    df_Q : pd.DataFrame, shape (T, nb)
        Reactive load time series in MVAr. Columns should correspond to buses.
    case : dict
        MATPOWER-format case dict. Used to read baseMVA and nb.

    Returns
    -------
    Pd_pu : np.ndarray, shape (T, nb)
        Per-unit active load (divided by baseMVA).
    Qd_pu : np.ndarray, shape (T, nb)
        Per-unit reactive load (divided by baseMVA).

    Raises
    ------
    ValueError
        If DataFrame shapes do not match (T, nb) or if the two DataFrames
        have different numbers of rows.
    """
    baseMVA = float(case["baseMVA"])
    nb      = case["bus"].shape[0]

    if df_P.shape[1] != nb:
        raise ValueError(
            f"df_P has {df_P.shape[1]} columns but case has {nb} buses."
        )
    if df_Q.shape[1] != nb:
        raise ValueError(
            f"df_Q has {df_Q.shape[1]} columns but case has {nb} buses."
        )
    if df_P.shape[0] != df_Q.shape[0]:
        raise ValueError(
            f"df_P has {df_P.shape[0]} rows but df_Q has {df_Q.shape[0]} rows; "
            "they must match."
        )

    Pd_pu = df_P.to_numpy(dtype=float) / baseMVA
    Qd_pu = df_Q.to_numpy(dtype=float) / baseMVA
    return Pd_pu, Qd_pu
