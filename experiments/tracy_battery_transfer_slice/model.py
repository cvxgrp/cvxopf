"""Four matched full-network, one-hour AC OPFs with retained battery pins."""

from dataclasses import replace
from pathlib import Path

import cvxpy as cp
import numpy as np

from cvxopf import build_opf_multistep, extract_results
from cvxopf._ac_start_mapping import pack_start
from experiments.case118_tracy_2021 import e3_prepared
from experiments.numerical_preparation.audit import serializable
from experiments.tracy_battery_transfer import model as transfer
from experiments.tracy_battery_transfer_starts import model as starts

HERE = Path(__file__).resolve().parent
HOUR = 2452
FORMS = ("ac", "socp", "lossy_dc", "singlenode_dc")
LIMITS = dict(
    max_launches=4,
    wall_seconds=300.0,
    rss_mib=8192.0,
    total_worker_seconds=1200.0,
    poll_seconds=1.0,
)
FRAMES = (
    "df_P",
    "df_Q",
    "df_nd",
    "df_load_p",
    "df_load_q",
    "df_hvdc_min",
    "df_hvdc_max",
)


def source_case(data):
    cases = [
        case
        for case in data["cases"].values()
        if case["ac"]["arm"]["start"] == 2439
        and case["ac"]["arm"]["stop"] == 2463
        and case["ac"]["arm"]["leg"] == "energy_neutral"
    ]
    if len(cases) != 1:
        raise ValueError("single ordinary neutral source case required")
    return cases[0]


def slice_inputs(wide, schedule, offset, targets):
    """Explicitly derive boundaries, and slice all identity-aligned frames."""
    if wide.get("coupling_constraints") or wide.get("hvdc"):
        raise ValueError("unexpected additional coupling/HVDC")
    if not 0 <= offset < wide["T"] or wide["formulation"] != "ac":
        raise ValueError("invalid AC slice")
    boundaries, precheck = transfer.schedule_check(
        schedule, wide["storage"], wide["delta"], targets
    )
    result = dict(
        wide,
        T=1,
        storage=[
            replace(
                unit,
                initial_soc=float(boundaries[offset, j]),
                terminal_soc=None,
                terminal_constraint=None,
                terminal_cost=None,
                terminal_weight=None,
            )
            for j, unit in enumerate(wide["storage"])
        ],
    )
    for key in FRAMES:
        if wide.get(key) is not None:
            if len(wide[key]) != wide["T"]:
                raise ValueError("input frame length differs from source horizon")
            result[key] = wide[key].iloc[offset : offset + 1].copy()
    return result, boundaries[offset : offset + 2].copy(), precheck


def slice_start(full, offset, powers, boundaries):
    values = {
        name: np.asarray(value)[:, offset : offset + (2 if name == "soc" else 1)].copy()
        for name, value in full.items()
    }
    values["b"], values["soc"] = np.asarray(powers).T.copy(), boundaries.T.copy()
    return values


def construct(number, data, prepared):
    if type(number) is not int or number not in range(1, 5):
        raise ValueError("undeclared slice case")
    form, case = FORMS[number - 1], source_case(data)
    reference, donor = case["ac"], case[FORMS[number - 1]]
    ids = reference["input_identities"]["storage"]
    if donor["input_identities"]["storage"] != ids:
        raise ValueError("source battery identities differ")
    if reference["cohort"] != "prepared" or reference["classification"] != "accepted":
        raise ValueError("accepted ordinary AC reference required")
    wide = e3_prepared.kwargs_for_arm(
        prepared, reference["arm"], ids, assembly="component_first"
    )
    if (
        e3_prepared.physical_input_digest(wide)
        != reference["mathematical_input_sha256"]
    ):
        raise ValueError("retained AC inputs differ")
    schedule = np.asarray(donor["result"]["b"], float)
    offset = HOUR - reference["arm"]["start"]
    kwargs, boundaries, precheck = slice_inputs(
        wide, schedule, offset, reference["arm"]["terminal_soc_mwh"]
    )
    powers = schedule[offset : offset + 1].copy()
    build = build_opf_multistep(**kwargs)
    build = replace(
        build,
        prob=cp.Problem(
            build.prob.objective,
            [*build.prob.constraints, build.variables["b"] == powers.T],
        ),
    )
    # Production mapping retains per-unit native network/generator coordinates.
    full_build = build_opf_multistep(**wide)
    saved = transfer.read(transfer.source_path(data, reference))
    full = pack_start(
        saved["logical_solution"], full_build, reference["arm"]["initial_soc_mwh"]
    )
    matched = slice_start(full, offset, powers, boundaries)
    raw, assigned = starts.alternative_start(
        build, kwargs, matched, dict(center="matched_ac", scale=None, seed=None)
    )
    del full_build
    row = serializable(
        dict(
            call_id=number,
            donor_formulation=form,
            hour=HOUR,
            source_offset=offset,
            source_interval=[2439, 2463],
            storage_ids=ids,
            donor=transfer.source_reference(data, donor),
            ac_reference=transfer.source_reference(data, reference),
            source_schedule_sha256=transfer.object_hash(schedule),
            source_precheck=precheck,
            initial_soc_mwh=boundaries[0],
            original_terminal_soc_mwh=boundaries[1],
            endpoint_role="derived hour-2453 energy, not original end-of-day target",
            pinned_power_mw=powers[0],
            mathematical_input_sha256=e3_prepared.physical_input_digest(kwargs),
            raw_start_sha256=transfer.object_hash(raw),
            assigned_start_sha256=transfer.object_hash(assigned),
        )
    )
    return build, kwargs, powers, raw, assigned, row


def start_audit(view):
    build, kwargs, powers, _, _, row = view
    result = extract_results(build)
    # No optimizer ran. Supply expression accounting without changing the
    # unsolved Problem's status or claiming a new solver success.
    result["objective"] = float(build.prob.objective.value)
    named = {
        k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")
    }
    common, _ = e3_prepared.audit(build, result, kwargs, named)
    pins = transfer.pin_audit(result, powers, kwargs, row["original_terminal_soc_mwh"])
    residual_passed = bool(common["residuals"]) and all(
        np.isfinite(v) and v <= common["limits"][k]
        for k, v in common["residuals"].items()
    )
    return serializable(
        dict(
            common=common,
            pins=pins,
            residual_gates_passed=residual_passed,
            evidence_kind="retained accepted AC point, non-solving slice audit",
            passed=residual_passed and pins["passed"],
        )
    )


def summarize(result, kwargs):
    buses = np.asarray(kwargs["case"]["bus"][:, 0], int)
    voltage = np.asarray(result["Vm"], float)
    branch = kwargs["case"]["branch"]
    active = branch[:, 10] > 0
    rates = np.asarray(branch[active, 5], float)
    limits = (rates > 0) & np.isfinite(rates)
    margins = {}
    for side in ("from", "to"):
        values = np.asarray(result["branch_s_" + side])[0]
        k = np.argmin(np.where(limits, rates - values, np.inf))
        rows = np.flatnonzero(active)
        margins[side] = dict(
            row_zero_based=int(rows[k]),
            from_bus=int(branch[rows[k], 0]),
            to_bus=int(branch[rows[k], 1]),
            rating_mva=float(rates[k]),
            apparent_mva=float(values[k]),
            margin_mva=float(rates[k] - values[k]),
        )
    return serializable(
        dict(
            objective=result["objective"],
            energy_not_served_mwh=result["energy_not_served"],
            battery_q_mvar=result["b_q"][0],
            total_battery_p_mw=np.sum(result["b"][0]),
            voltage_pu={
                str(b): voltage[0, np.flatnonzero(buses == b)[0]]
                for b in (70, 71, 72, 73)
            },
            tightest_branch_margins=margins,
        )
    )
