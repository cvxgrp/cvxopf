"""Non-solving prepared/native replay and executed-only physical accounting."""
from dataclasses import replace

import numpy as np

from cvxopf._ac_rollout import advance_cursor, ACWindowRequest, RolloutCursor
from experiments.case118_tracy_2021 import e3_prepared
from experiments.case118_tracy_2021.ac_archive import verify_logical
from experiments.case118_tracy_2021.stage_b import audit_result, TOLERANCES
from experiments.numerical_preparation.audit import serializable

TIME_FIELDS = ("Pg", "Qg", "b", "b_q", "soc", "p_nd", "q_nd", "curtailment",
    "p_net", "q_net", "p_load", "q_load", "p_load_served", "q_load_served",
    "p_load_shed", "q_load_shed", "load_shed_fraction", "p_load_shed_total", "Vm", "Va_deg",
    "branch_p_from", "branch_p_to", "branch_q_from", "branch_q_to", "branch_s_from", "branch_s_to")


def assess(record, kwargs, start, captured):
    checked = e3_prepared.assess({"formulation": "ac"},
        "primary" if record["hard_target"] else "target_free", record, kwargs, start, captured)
    if checked["passed"]:
        verify_logical(record["logical_solution"], record["result"], kwargs["case"]["baseMVA"], kwargs["T"])
        if record["hard_target"]:
            request = ACWindowRequest(**record["window_request"])
            cp = record["request"]["checkpoint"]
            cursor = RolloutCursor(cp["completed_intervals"], tuple(cp["storage_device_ids"]),
                                  cp["realized_soc_mwh"], cp["preceding_controlling_attempt_id"])
            try:
                _, action = _action(record, cursor, request, kwargs, "independent-first-action-check")
                checked["first_action"] = dict(passed=action["audit"]["passed"])
            except ValueError as exc:
                checked.update(passed=False, first_action=dict(passed=False, reason=str(exc)))
    return checked


def executed_result(results, kwargs, *, require_passed=True):
    """Build a new executed trajectory; never sum overlapping window totals.

    The existing independent numeric audit owns cost/ENS reconstruction. Its
    preliminary result supplies arithmetic only; a second full audit checks the
    new executed envelope against that arithmetic and original physical inputs.
    No native/objective acceptance label is copied into a controlling decision.
    """
    result = {name: np.concatenate([np.asarray(row[name], float)[:1] for row in results])
              for name in TIME_FIELDS}
    result.update(status="optimal", storage_device_ids=[s.device_id for s in kwargs["storage"]],
        objective=0., storage_cost=0., load_shedding_cost=0., energy_not_served=0.,
        energy_not_served_by_load=np.zeros(len(kwargs["loads"])))
    preliminary = audit_result(result, kwargs, {})
    if "costs" not in preliminary:
        raise ValueError("executed trajectory lacks complete physical fields")
    result.update(preliminary["costs"], objective=sum(preliminary["costs"].values()))
    result["energy_not_served_by_load"] = kwargs["delta"] * result["p_load_shed"].sum(axis=0)
    result["energy_not_served"] = float(result["energy_not_served_by_load"].sum())
    checked = audit_result(result, kwargs, preliminary["costs"])
    if require_passed and not checked["passed"]:
        raise ValueError(f"executed-only physical audit failed: {checked}")
    return serializable(result), serializable(checked)


def first_action(record, cursor, request, kwargs, attempt_id):
    if record["classification"] != "accepted" or not record["independent_audit"]["passed"]:
        raise ValueError("unaccepted window cannot supply an action")
    return _action(record, cursor, request, kwargs, attempt_id)


def _action(record, cursor, request, kwargs, attempt_id):
    """First-action physics gate, used before acceptance as well as publication."""
    next_cursor = advance_cursor(cursor, request, attempt_id=attempt_id, accepted=True,
        hard_target=record["hard_target"], first_b_mw=record["result"]["b"][0],
        solved_next_soc_mwh=record["result"]["soc"][0], delta_hours=kwargs["delta"],
        recurrence_tolerance=TOLERANCES["soc_mwh"])
    one = dict(kwargs, T=1, storage=[replace(s, terminal_soc=None, terminal_constraint=None) for s in kwargs["storage"]])
    for field in ("df_load_p", "df_load_q", "df_nd"):
        one[field] = kwargs[field].iloc[:1].copy()
    first = dict(record["result"])
    # Executed physical energy is the independently reconstructed state, not
    # the forecast's numerically approximate post-step coordinate. Keep the
    # latter unchanged in the window record and the separate solved-state field.
    first["soc"] = next_cursor.realized_soc_mwh[None, :]
    result, checked = executed_result([first], one)
    return next_cursor, dict(controlling_attempt_id=attempt_id, result=result, audit=checked,
        solved_next_soc_mwh=record["result"]["soc"][0],
        reconstructed_next_soc_mwh=next_cursor.realized_soc_mwh.tolist())


def trajectory(rows, original, *, complete):
    if not rows:
        return dict(available=False, completed_intervals=0)
    kwargs = dict(original, T=len(rows))
    for field in ("df_load_p", "df_load_q", "df_nd"):
        kwargs[field] = original[field].iloc[:len(rows)].copy()
    if not complete:
        kwargs["storage"] = [replace(s, terminal_soc=None, terminal_constraint=None) for s in original["storage"]]
    result, checked = executed_result(rows, kwargs, require_passed=False)
    dt = kwargs["delta"]
    arrays = {k: np.asarray(v) for k, v in result.items() if k in TIME_FIELDS}
    branch = np.asarray(kwargs["case"]["branch"])
    rates = branch[branch[:, 10] > 0, 5]
    boundaries = np.vstack(([s.initial_soc for s in kwargs["storage"]], arrays["soc"]))
    summary = dict(physical_cost=sum(checked["costs"].values()),
        served_energy_mwh=float(dt * arrays["p_load_served"].sum()),
        conventional_generation_mwh=float(dt * arrays["Pg"].sum()),
        network_absorption_mwh=float(dt * arrays["p_net"].sum()),
        energy_not_served_by_load_mwh=result["energy_not_served_by_load"],
        storage_throughput_by_device_mwh=(dt * abs(arrays["b"]).sum(axis=0)).tolist(),
        storage_final_soc_mwh=arrays["soc"][-1].tolist(),
        storage_min_soc_mwh=boundaries.min(axis=0).tolist(), storage_max_soc_mwh=boundaries.max(axis=0).tolist(),
        final_obligation_error_mwh=None if not complete else float(np.max(abs(arrays["soc"][-1] - [s.terminal_soc for s in original["storage"]]))),
        minimum_voltage_pu=float(arrays["Vm"].min()), maximum_voltage_pu=float(arrays["Vm"].max()),
        minimum_thermal_margin_mva=float(np.min(rates - np.maximum(arrays["branch_s_from"], arrays["branch_s_to"]))),
        generation_by_device_mwh=(dt * arrays["Pg"].sum(axis=0)).tolist(),
        curtailed_by_device_mwh=(dt * arrays["curtailment"].sum(axis=0)).tolist())
    return dict(available=True, completed_intervals=len(rows), complete=complete,
                result=result, audit=checked, summary=summary)
