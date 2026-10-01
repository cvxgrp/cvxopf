"""One device fleet for all three formulations; no solving in this module."""

from dataclasses import replace

import numpy as np
import pandas as pd

from cvxopf import Load, NondispatchableUnit, StorageUnitIdeal, gen_from_matpower
from experiments.case118_annual_hierarchy.pglib_case import load_pglib_case118
from .prepare import PreparedInputs, APPROVED_OPERATING_CHOICES


def model_inputs(
    prepared: PreparedInputs, start: int, stop: int, *, shedding: bool = True
) -> dict:
    """Return fresh public API kwargs for a half-open input window.

    The 50% endpoints are the approved annual convention, not a qualification
    of arbitrary short-window endpoints. Stage B must declare its boundaries.
    """
    if not (0 <= start < stop <= 8760):
        raise ValueError("window must lie inside the frozen year")
    case = load_pglib_case118()
    generators = gen_from_matpower(case["gen"], case["gencost"])
    generators = [
        replace(g, p_max_mw=float(r.pmax_mw), cost_coeffs=(r.c0, r.c1, r.c2))
        for g, r in zip(generators, prepared.generators.itertuples(), strict=True)
    ]
    load_rows = prepared.buses.loc[prepared.buses.load_share > 0]
    penalty = APPROVED_OPERATING_CHOICES["shedding_cost_per_mwh"] if shedding else None
    loads = [
        Load(
            bus=int(r.bus),
            p_load_mw=float(prepared.load_p_mw[start, j]),
            q_load_mvar=float(prepared.load_q_mvar[start, j]),
            device_id=f"load_bus_{r.bus}",
            shedding_cost_per_mwh=penalty,
            max_shed_fraction=1.0,
        )
        for j, r in enumerate(load_rows.itertuples())
    ]
    renewable = [
        NondispatchableUnit(
            bus=int(r.bus),
            device_id=r.device_id,
            p_available=float(prepared.nd_available_mw[start, j]),
            apparent_power_rating=float(r.apparent_power_rating_mva),
        )
        for j, r in enumerate(prepared.renewables.itertuples())
    ]
    storage = [
        StorageUnitIdeal(
            bus=int(r.bus),
            device_id=r.device_id,
            capacity=float(r.capacity_mwh),
            initial_soc=float(r.initial_soc_mwh),
            apparent_power_rating=float(r.apparent_power_rating_mva),
            terminal_soc=float(r.terminal_soc_mwh),
            terminal_constraint="equality",
        )
        for r in prepared.batteries.itertuples()
    ]
    if not np.allclose(
        [s.aging_weight for s in storage],
        prepared.batteries.aging_weight,
        rtol=0,
        atol=0,
    ):
        raise ValueError("default storage regularization changed")
    index = prepared.source.index[start:stop]
    return dict(
        case=case,
        T=stop - start,
        delta=1.0,
        temporal_assembly="vectorized",
        generators=generators,
        loads=loads,
        nondispatchable=renewable,
        storage=storage,
        df_load_p=pd.DataFrame(
            prepared.load_p_mw[start:stop],
            index=index,
            columns=[d.device_id for d in loads],
        ),
        df_load_q=pd.DataFrame(
            prepared.load_q_mvar[start:stop],
            index=index,
            columns=[d.device_id for d in loads],
        ),
        df_nd=pd.DataFrame(
            prepared.nd_available_mw[start:stop],
            index=index,
            columns=[d.device_id for d in renewable],
        ),
    )
