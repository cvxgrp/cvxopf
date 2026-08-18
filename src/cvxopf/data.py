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
_BR_STATUS  = 10


def _validate_integer_column(name: str, values: np.ndarray) -> None:
    """Require a numeric identifier column to contain finite integers."""
    values = np.asarray(values)
    try:
        numeric = values.astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} values must be finite integers.") from exc
    invalid = ~np.isfinite(numeric) | (numeric != np.trunc(numeric))
    if np.any(invalid):
        details = ", ".join(
            f"row {int(row)}: {numeric[row]!r}"
            for row in np.flatnonzero(invalid)
        )
        raise ValueError(
            f"{name} values must be finite integers; invalid values: "
            f"{details}."
        )


def validate_case_identifiers(case: dict) -> None:
    """Validate bus-reference columns without imposing full case structure."""
    bus = np.asarray(case["bus"])
    branch = np.asarray(case["branch"])
    gen = np.asarray(case["gen"])
    _validate_integer_column("BUS_I", bus[:, _BUS_I])
    _validate_integer_column("F_BUS", branch[:, _F_BUS])
    _validate_integer_column("T_BUS", branch[:, _T_BUS])
    _validate_integer_column("GEN_BUS", gen[:, _GEN_BUS])


def validate_branch_status(branch: np.ndarray) -> None:
    """Require every MATPOWER branch status to be exactly zero or one."""
    branch = np.asarray(branch)
    if branch.ndim != 2 or branch.shape[1] <= _BR_STATUS:
        raise ValueError(
            "branch array must be two-dimensional and include the "
            f"BR_STATUS column at index {_BR_STATUS}; got shape "
            f"{branch.shape}."
        )

    status = branch[:, _BR_STATUS]
    invalid = ~np.isin(status, (0, 1))
    if np.any(invalid):
        rows = np.flatnonzero(invalid)
        values = status[invalid]
        details = ", ".join(
            f"row {int(row)}: {value!r}"
            for row, value in zip(rows, values, strict=True)
        )
        raise ValueError(
            "branch BR_STATUS values must be exactly 0 or 1; "
            f"invalid values: {details}."
        )


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

    validate_branch_status(branch)

    validate_case_identifiers(case)

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
    df_Q: pd.DataFrame | None,
    case: dict,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Convert nodal load time-series DataFrames to per-unit numpy arrays.

    Parameters
    ----------
    df_P : pd.DataFrame, shape (T, nb)
        Active load time series in MW. Columns should correspond to buses.
    df_Q : pd.DataFrame or None, shape (T, nb)
        Reactive load time series in MVAr. Columns should correspond to buses.
        ``None`` preserves the legacy DC convention by supplying a zero
        reactive reporting channel.
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

    if df_Q is None:
        df_Q = pd.DataFrame(
            np.zeros_like(df_P.to_numpy()),
            index=df_P.index,
            columns=df_P.columns,
        )

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


def align_device_dataframe(
    frame: pd.DataFrame,
    devices: list,
    T: int,
    frame_name: str,
    *,
    nonnegative: bool = False,
) -> np.ndarray:
    """
    Validate and align externally keyed device data to device-list order.

    Devices must expose unique, nonempty string ``device_id`` values. Frame
    columns must contain exactly the same IDs; arbitrary input column order is
    accepted and reordered deterministically.
    """
    if frame.shape[0] != T:
        raise ValueError(
            f"{frame_name} has {frame.shape[0]} rows but T={T}. "
            f"Expected {T} rows (one per time step)."
        )

    device_ids = [getattr(device, "device_id", None) for device in devices]
    missing_ids = [i for i, device_id in enumerate(device_ids) if device_id is None]
    if missing_ids:
        raise ValueError(
            f"{frame_name} requires device_id on every device; missing at "
            f"indices {missing_ids}."
        )
    invalid_ids = [
        (i, device_id)
        for i, device_id in enumerate(device_ids)
        if not isinstance(device_id, str) or not device_id.strip()
    ]
    if invalid_ids:
        raise ValueError(
            f"{frame_name} device_id values must be nonempty strings; "
            f"invalid entries: {invalid_ids}."
        )
    duplicate_device_ids = sorted(
        {device_id for device_id in device_ids if device_ids.count(device_id) > 1}
    )
    if duplicate_device_ids:
        raise ValueError(
            f"{frame_name} device_id values must be unique; duplicates: "
            f"{duplicate_device_ids}."
        )
    if frame.columns.has_duplicates:
        duplicates = frame.columns[frame.columns.duplicated()].unique().tolist()
        raise ValueError(
            f"{frame_name} columns must be unique; duplicates: {duplicates}."
        )

    columns = frame.columns.tolist()
    missing_columns = sorted(set(device_ids) - set(columns), key=repr)
    extra_columns = sorted(set(columns) - set(device_ids), key=repr)
    if missing_columns or extra_columns:
        raise ValueError(
            f"{frame_name} columns must match device IDs exactly; "
            f"missing={missing_columns}, extra={extra_columns}."
        )

    values = frame.loc[:, device_ids].to_numpy(dtype=float)
    if not np.all(np.isfinite(values)):
        bad = np.argwhere(~np.isfinite(values))[0]
        raise ValueError(
            f"{frame_name} contains a non-finite value at "
            f"row {int(bad[0])}, device_id={device_ids[int(bad[1])]!r}."
        )
    if nonnegative and np.any(values < 0):
        bad = np.argwhere(values < 0)[0]
        raise ValueError(
            f"{frame_name} contains a negative value at "
            f"row {int(bad[0])}, device_id={device_ids[int(bad[1])]!r}."
        )
    return values
