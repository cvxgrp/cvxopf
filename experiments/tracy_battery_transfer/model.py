"""Transfer retained convex battery real-power schedules into matched AC models.

All hourly battery powers are equality-constrained in MW. The existing battery
API removes the terminal equality; initial energy and ideal-storage dynamics
determine every subsequent state. No source schedule is clipped or repaired.
"""

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import cvxpy as cp
import numpy as np

from cvxopf import build_opf_multistep
from cvxopf._ac_start_mapping import pack_start
from cvxopf._hierarchical_solver import _assign_start
from experiments.ac_production_verification.audit import publication_check
from experiments.case118_tracy_2021 import e3_plus, e3_prepared
from experiments.case118_tracy_2021.e3_results import load_scenarios, sha
from experiments.case118_tracy_2021.prepare import ROOT
from experiments.case118_tracy_2021.stage_b import TOLERANCES
from experiments.case118_tracy_2021.stage_d import read
from experiments.numerical_preparation.audit import serializable

HERE = Path(__file__).resolve().parent
DONORS = ("socp", "lossy_dc", "singlenode_dc")
LIMITS = dict(
    max_launches=18,
    wall_seconds=3600.0,
    rss_mib=16384.0,
    total_worker_seconds=64800.0,
    poll_seconds=1.0,
)
SOURCE_COMMITS = {
    "prepared": "2edc6d3bbeac85bd78f5bf6d9cdc387f851eb0f0",
    "prepared_plus": "13ad35bc5f0e9f8b5d20d54a55d2ab2cd7a26df8",
}
PIN_MW = TOLERANCES["power_mw"]


def object_hash(value):
    return hashlib.sha256(
        json.dumps(
            serializable(value), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def source_inventory():
    data = load_scenarios()
    for run in data["runs"]:
        context = run["binding"]["context"]
        if context["commit"] != SOURCE_COMMITS[context["cohort"]]:
            raise ValueError("unexpected retained source commit")
    return data


def jobs(data):
    if len(data["cases"]) != 6:
        raise ValueError("transfer requires the six retained scenarios")
    return [
        dict(id=i + 1, scenario=scenario, donor=donor)
        for i, (scenario, donor) in enumerate(
            (scenario, donor) for scenario in data["cases"] for donor in DONORS
        )
    ]


def source_path(data, record):
    run = next(
        run
        for run in data["runs"]
        if run["binding"]["context"]["cohort"] == record["cohort"]
    )
    return run["directory"] / record["archive"]


def source_reference(data, record):
    path = source_path(data, record)
    return dict(
        path=str(path.relative_to(ROOT)),
        sha256=sha(path),
        classification=record["classification"],
        cohort=record["cohort"],
    )


def schedule_check(schedule, storage, delta, terminal):
    """Non-solving check of source bounds and the implied energy trajectory."""
    schedule = np.asarray(schedule, float)
    terminal = np.asarray(terminal, float)
    if (
        schedule.ndim != 2
        or schedule.shape[1] != len(storage)
        or not schedule.shape[0]
        or terminal.shape != (len(storage),)
        or not np.isfinite(schedule).all()
        or not np.isfinite(terminal).all()
        or not np.isfinite(delta)
        or delta <= 0
    ):
        raise ValueError("invalid battery schedule/endpoint/duration")
    initial = np.array([s.initial_soc for s in storage])
    ratings = np.array([s.apparent_power_rating for s in storage])
    capacities = np.array([s.capacity for s in storage])
    boundaries = np.vstack((initial, initial - delta * np.cumsum(schedule, axis=0)))
    metrics = dict(
        power_excess_mw=float(max(0.0, np.max(np.abs(schedule) - ratings))),
        energy_excess_mwh=float(
            max(0.0, np.max(-boundaries), np.max(boundaries - capacities))
        ),
        endpoint_difference_mwh=float(np.max(np.abs(boundaries[-1] - terminal))),
    )
    good = (
        metrics["power_excess_mw"] <= TOLERANCES["box"]
        and metrics["energy_excess_mwh"] <= TOLERANCES["soc_mwh"]
        and metrics["endpoint_difference_mwh"] <= TOLERANCES["endpoint_mwh"]
    )
    if not good:
        raise ValueError(
            f"source storage schedule fails original-unit checks: {metrics}"
        )
    return boundaries, dict(metrics, passed=True)


def transfer_build(kwargs, schedule):
    """Use the battery API, not constraint surgery or solver monkey-patching."""
    if kwargs["formulation"] != "ac" or kwargs["temporal_assembly"] != "vectorized":
        raise ValueError("this experiment requires vectorized AC")
    storage = kwargs["storage"]
    if any(
        s.terminal_constraint != "equality"
        or s.terminal_cost is not None
        or s.terminal_weight is not None
        for s in storage
    ):
        raise ValueError(
            "source must have hard terminal equalities without terminal costs"
        )
    targets = [s.terminal_soc for s in storage]
    boundaries, precheck = schedule_check(schedule, storage, kwargs["delta"], targets)
    transfer = dict(
        kwargs,
        storage=[
            replace(s, terminal_soc=None, terminal_constraint=None) for s in storage
        ],
    )
    build = build_opf_multistep(**transfer)
    schedule = np.asarray(schedule, float)
    if schedule.T.shape != build.variables["b"].shape:
        raise ValueError("schedule shape does not match battery/time axes")
    pin = build.variables["b"] == schedule.T
    if not pin.is_dcp():
        raise ValueError("battery pin must remain an affine device constraint")
    build = replace(
        build, prob=cp.Problem(build.prob.objective, [*build.prob.constraints, pin])
    )
    return build, transfer, boundaries, precheck


def construct(job, data, prepared):
    if job not in jobs(data):
        raise ValueError("undeclared transfer job")
    case = data["cases"][job["scenario"]]
    donor, reference = case[job["donor"]], case["ac"]
    ids = reference["input_identities"]["storage"]
    cohort = e3_plus if reference["cohort"] == "prepared_plus" else e3_prepared
    kwargs = cohort.kwargs_for_arm(
        prepared, reference["arm"], ids, assembly="component_first"
    )
    if (
        e3_prepared.physical_input_digest(kwargs)
        != reference["mathematical_input_sha256"]
    ):
        raise ValueError("rebuilt AC inputs differ from retained reference")
    schedule = np.asarray(donor["result"]["b"], float).copy()
    build, transfer, boundaries, precheck = transfer_build(kwargs, schedule)
    if list(build.data["storage_device_ids"]) != ids:
        raise ValueError("destination battery identities differ from source order")
    saved = read(source_path(data, reference))
    start = pack_start(
        saved["logical_solution"], build, reference["arm"]["initial_soc_mwh"]
    )
    # Change only b and its implied SoC. All other initial values come from the
    # same retained AC estimate, not from the convex donor or a new solve.
    start["b"], start["soc"] = schedule.T.copy(), boundaries.T.copy()
    _assign_start(build, start)
    row = serializable(
        dict(
            job=job,
            donor=source_reference(data, donor),
            ac_reference=source_reference(data, reference),
            storage_ids=ids,
            start=reference["arm"]["start"],
            stop=reference["arm"]["stop"],
            original_terminal_soc_mwh=reference["arm"]["terminal_soc_mwh"],
            schedule_sha256=object_hash(schedule),
            start_sha256=object_hash(start),
            mathematical_input_sha256=e3_prepared.physical_input_digest(transfer),
            preparation_policy=e3_prepared.policy_record("ac", "component_first"),
            source_precheck=precheck,
        )
    )
    return build, transfer, schedule, start, row


def pin_audit(result, schedule, kwargs, targets):
    actual, soc = np.asarray(result["b"], float), np.asarray(result["soc"], float)
    schedule = np.asarray(schedule, float)
    initial = np.asarray([s.initial_soc for s in kwargs["storage"]])
    expected = initial - kwargs["delta"] * np.cumsum(schedule, axis=0)
    if actual.shape != schedule.shape or soc.shape != expected.shape:
        raise ValueError("published battery trajectory shape differs")
    metrics = dict(
        maximum_power_difference_mw=float(np.max(np.abs(actual - schedule))),
        maximum_implied_soc_difference_mwh=float(np.max(np.abs(soc - expected))),
        original_endpoint_difference_mwh=float(np.max(np.abs(soc[-1] - targets))),
    )
    return dict(
        metrics,
        passed=bool(
            np.isfinite([*metrics.values()]).all()
            and metrics["maximum_power_difference_mw"] <= PIN_MW
            and metrics["maximum_implied_soc_difference_mwh"] <= TOLERANCES["soc_mwh"]
            and metrics["original_endpoint_difference_mwh"]
            <= TOLERANCES["endpoint_mwh"]
        ),
    )


def assess(build, kwargs, schedule, start, row, record, captured):
    """Restore native coordinates independently; never call an optimizer."""
    evidence, native = record["preparation_evidence"], record["native"]
    if native != evidence["native"]:
        raise ValueError("native/preparation archive mismatch")
    if native["status"] != 0:
        return dict(
            passed=False,
            reason="native numerical rejection",
            native_status=native["status"],
        )
    result, slack, reconstruction = e3_prepared.restore_ac(
        build, evidence, dict(assigned_start=serializable(start)), captured
    )
    publication_check(result, record["result"])
    named = {
        key: float(value.value)
        for key, value in build.expressions.items()
        if key.endswith("_cost")
    }
    if named != record["named_costs"]:
        raise ValueError("named cost archive disagrees with native restoration")
    common, _ = e3_prepared.audit(build, result, kwargs, named)
    economics = e3_prepared.economics(
        native["obj_val"], common["costs"], slack, formulation="ac"
    )
    pins = pin_audit(result, schedule, kwargs, row["original_terminal_soc_mwh"])
    witness = None
    if row["ac_reference"]["cohort"] == "prepared_plus":
        from experiments.ac_cost_qualification.fixture import shortage

        minimum = shortage(kwargs)["minimum_ens_mwh"]
        witness = dict(
            minimum_ens_mwh=minimum,
            passed=bool(
                result["energy_not_served"] + e3_plus.WITNESS_ALLOWANCE_MWH >= minimum
            ),
        )
    return serializable(
        dict(
            common=common,
            economics=economics,
            pins=pins,
            forced_shedding_witness=witness,
            canonical_reconstruction=reconstruction,
            passed=bool(
                common["passed"]
                and economics["passed"]
                and pins["passed"]
                and (witness is None or witness["passed"])
            ),
        )
    )


def comparison(result, named, reference):
    """Signed descriptive contrasts between two AC operating estimates."""

    def quantities(values, costs):
        return dict(
            served_energy_mwh=float(np.sum(values["p_load_served"])),
            unserved_energy_mwh=float(np.sum(values["p_load_shed"])),
            conventional_generation_mwh=float(np.sum(values["Pg"])),
            renewable_curtailment_mwh=float(np.sum(values["curtailment"])),
            network_absorption_mwh=float(np.sum(values["p_net"])),
            battery_throughput_mwh=float(np.sum(np.abs(values["b"]))),
            physical_cost=float(sum(costs.values())),
        )

    transfer = quantities(result, named)
    unrestricted = quantities(reference["result"], reference["named_costs"])
    return dict(
        transfer=transfer,
        unrestricted_ac=unrestricted,
        transfer_minus_unrestricted_ac={
            key: transfer[key] - unrestricted[key] for key in transfer
        },
        cost_components_transfer_minus_unrestricted_ac={
            key: named[key] - reference["named_costs"][key] for key in named
        },
        interpretation="Feasible local AC estimates; no global optimality or truth claim",
    )
