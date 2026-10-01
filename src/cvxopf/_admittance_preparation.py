"""Shared case/component preparation for admittance-based network builders.

This is a narrow extraction of AC preparation, not a second device parser.
Network physics and temporal assembly remain in their formulation builders.
"""

import numpy as np

from cvxopf.network import (
    F_BUS, T_BUS, BranchAdmittance, reindex_case_to_consecutive,
    make_branch_admittance, make_ybus_matpower, make_ybus_sparsity_mask,
)
from cvxopf.data import validate_case
from cvxopf.generator import DispatchableGenerator, gen_from_matpower
from cvxopf._component_adapter import Formulation, PreparationContext
from cvxopf._component_assembly import prepare_components, merge_prepared_component_data
from cvxopf._component_adapters import (
    HVDCInputs, LoadInputs, NondispatchableInputs, component_requests,
)
from cvxopf.load import Load, loads_from_matpower
from cvxopf.storage import StorageUnitIdeal
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.hvdc import HVDCLink

BUS_TYPE, VMIN, VMAX, PD, QD = 1, 12, 11, 2, 3

def _validate_branch_limit_inputs(
    options,
    admittance: BranchAdmittance,
) -> None:
    """Validate AC-only inputs that matter when thermal limits are enforced."""
    if not options.enforce_branch_limits:
        return
    if options.sparsity_tol != 0:
        raise ValueError(
            "AC branch-limit enforcement requires sparsity_tol == 0 "
            "so nodal and terminal-flow physics use consistent coefficients."
        )

    invalid = (
        admittance.status
        & (
            ~np.isfinite(admittance.rate_a_mva)
            | (admittance.rate_a_mva < 0)
        )
    )
    if np.any(invalid):
        details = ", ".join(
            f"row {int(row)}: {admittance.rate_a_mva[row]!r}"
            for row in np.flatnonzero(invalid)
        )
        raise ValueError(
            "AC branch-limit enforcement requires every in-service "
            "rateA to be finite and nonnegative; invalid values: "
            f"{details}."
        )


def _make_row_sum_matrix(rows: np.ndarray, cols: np.ndarray, nb: int) -> np.ndarray:
    """
    Build a (nb, nnz) constant numpy matrix Rp such that Rp @ x_vec
    gives the row sums of the (nb, nb) matrix whose nonzero entry at
    position k is (rows[k], cols[k]).

    Rp[i, k] = 1.0 if rows[k] == i, else 0.0.

    Used in the sparse P/Q formulation to express nodal injections
        p = Rp @ P_vec,  q = Rp @ Q_vec
    without materialising a dense (nb, nb) matrix variable.

    Parameters
    ----------
    rows : np.ndarray, shape (nnz,)
        Row indices of Ybus nonzero entries.
    cols : np.ndarray, shape (nnz,)
        Column indices of Ybus nonzero entries.
    nb : int
        Number of buses.

    Returns
    -------
    Rp : np.ndarray, shape (nb, nnz)
    """
    nnz = len(rows)
    Rp  = np.zeros((nb, nnz))
    for k in range(nnz):
        Rp[rows[k], k] = 1.0
    return Rp


def prepare_admittance_case(
    case: dict,
    options,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    hvdc: list[HVDCLink] | None = None,
    generators: list[DispatchableGenerator] | None = None,
    horizon_steps: int = 1,
    nd_available_mw: np.ndarray | None = None,
    hvdc_inputs: HVDCInputs | None = None,
    load_inputs: LoadInputs | None = None,
    is_multistep: bool = False,
    loads: list[Load] | None = None,
    load_participates_when_empty: bool = False,
    nondispatchable_inputs: NondispatchableInputs | None = None,
    formulation: Formulation = "ac",
) -> dict:
    """
    Validate, reindex, and extract all numpy data from a case dict.
    Shared preparation for admittance-based AC and SOCP builders.
    Public series alignment remains in problem.py.
    """
    validate_case(case)
    if formulation == "socp" and options.sparsity_tol != 0:
        raise ValueError("SOCP requires sparsity_tol == 0, even with branch limits disabled")
    if formulation == "socp" and options.loss_weight != 1.0:
        raise ValueError("loss_weight is DC-only and cannot alter the SOCP objective")
    if formulation == "ac" and np.asarray(case["branch"]).shape[0] == 0:
        raise ValueError(
            "Branchless AC cases are unsupported by the current CVXPY "
            "DNLP/IPOPT path. Use formulation='singlenode_dc' only if "
            "collapsing the network and omitting voltage/reactive-power "
            "physics is appropriate."
        )
    branch_from_bus_external = (
        np.asarray(case["branch"])[:, F_BUS].astype(int).copy()
    )
    branch_to_bus_external = (
        np.asarray(case["branch"])[:, T_BUS].astype(int).copy()
    )
    if loads is None:
        loads = loads_from_matpower(case["bus"])
    if generators is None:
        generators = gen_from_matpower(case["gen"], case["gencost"])
    case, ext_to_int = reindex_case_to_consecutive(case)

    baseMVA = float(case["baseMVA"])
    bus     = case["bus"]
    nb      = bus.shape[0]
    branch_admittance = make_branch_admittance(case)
    _validate_branch_limit_inputs(options, branch_admittance)
    constrained_branch_indices = np.flatnonzero(
        branch_admittance.status
        & np.isfinite(branch_admittance.rate_a_mva)
        & (branch_admittance.rate_a_mva > 0)
    )
    Ybus    = make_ybus_matpower(
        case, branch_admittance=branch_admittance
    )
    G       = np.real(Ybus)
    B       = np.imag(Ybus)
    E, Z    = make_ybus_sparsity_mask(Ybus, tol=options.sparsity_tol)

    rows  = E[0]
    cols  = E[1]
    G_vec = G[rows, cols]
    B_vec = B[rows, cols]
    Rp    = _make_row_sum_matrix(rows, cols, nb) if formulation == "ac" else None

    ref_idx = np.where(bus[:, BUS_TYPE] == 3)[0]
    ref     = int(ref_idx[0])
    pv      = np.where(bus[:, BUS_TYPE] == 2)[0]

    vmin_arr = bus[:, VMIN].astype(float)
    vmax_arr = bus[:, VMAX].astype(float)

    Pd = bus[:, PD].astype(float) / baseMVA
    Qd = bus[:, QD].astype(float) / baseMVA

    # Get external bus IDs for validation (needed for both storage and nondispatchable)
    if ext_to_int is not None:
        ext_bus_ids = set(ext_to_int.keys())
        component_ext_to_int = ext_to_int
    else:
        ext_bus_ids = set(bus[:, 0].astype(int).tolist())
        component_ext_to_int = {
            bus_id: bus_id for bus_id in ext_bus_ids
        }

    preparation = PreparationContext(
        base_mva=baseMVA,
        nb=nb,
        ext_to_int=component_ext_to_int,
        ext_bus_ids=frozenset(ext_bus_ids),
        horizon_steps=horizon_steps,
        delta=delta,
        is_multistep=is_multistep,
    )
    if nondispatchable and nd_available_mw is None and nondispatchable_inputs is None:
        nd_available_mw = np.array(
            [[unit.p_available for unit in nondispatchable]],
            dtype=float,
        )
    requests = component_requests(
        formulation,
        generators=generators,
        load_units=loads,
        load_inputs=load_inputs,
        load_participates_when_empty=load_participates_when_empty,
        storage_units=storage or (),
        nondispatchable_units=nondispatchable or (),
        nondispatchable_inputs=(
            None
            if not nondispatchable
            else (nondispatchable_inputs if nondispatchable_inputs is not None
                  else NondispatchableInputs(nd_available_mw))
        ),
        hvdc_links=hvdc or (),
        hvdc_inputs=hvdc_inputs,
    )
    components = prepare_components(requests, formulation, preparation)
    load_p_mw = np.asarray(components.flat_data["_load_p_mw_by_step"], dtype=float)[0]
    load_q_mvar = np.asarray(components.flat_data["_load_q_mvar_by_step"], dtype=float)[0]
    Pd = np.asarray(components.flat_data["Cload"]) @ load_p_mw / baseMVA
    Qd = np.asarray(components.flat_data["Cload"]) @ load_q_mvar / baseMVA

    formulation_data = dict(
        case=case, baseMVA=baseMVA,
        bus=bus,
        nb=nb,
        Ybus=Ybus, G=G, B=B, E=E, Z=Z,
        rows=rows, cols=cols, G_vec=G_vec, B_vec=B_vec, Rp=Rp,
        ref=ref, pv=pv, ext_to_int=ext_to_int,
        _component_ext_to_int=component_ext_to_int,
        ext_bus_ids=ext_bus_ids,
        nl=len(branch_admittance.from_bus),
        branch_admittance=branch_admittance,
        branch_from_bus_internal=branch_admittance.from_bus,
        branch_to_bus_internal=branch_admittance.to_bus,
        branch_from_bus_external=branch_from_bus_external,
        branch_to_bus_external=branch_to_bus_external,
        branch_status=branch_admittance.status,
        branch_rate_a_mva=branch_admittance.rate_a_mva,
        constrained_branch_indices=constrained_branch_indices,
        vmin_arr=vmin_arr, vmax_arr=vmax_arr,
        Pd=Pd, Qd=Qd,
        _components=components,
    )
    return merge_prepared_component_data(components, formulation_data)
