"""Declared AC-only qualification matrix and exact, non-solving fixture assembly."""

from dataclasses import asdict, dataclass

import numpy as np
from cvxopf.network import BR_R, GS

from experiments.ac_cost_coordinates import model as coordinates, run as diagnostic
from experiments.numerical_preparation import fixture as original, run_qualification as q
from experiments.numerical_preparation.audit import serializable
from tests.socp_matched import digest as input_digest

MODES = {"original": (), "cycling": ("b",), "shedding": ("load_shed_fraction",),
         "both": ("b", "load_shed_fraction")}
LIMITS = dict(max_launches=22, wall_seconds=180., rss_mib=16384.,
              total_worker_seconds=3960., poll_seconds=1.)
GATES = dict(cost_abs=1e-4, cost_rel=1e-6, reconstruction_abs=1e-7,
             reconstruction_rel=1e-12, forced_shedding_energy_mwh=1e-4)


@dataclass(frozen=True)
class Arm:
    id: int
    dataset: str
    T: int
    start: int
    initialization: str
    mode: str
    forced_shedding: bool = False

    @property
    def group(self):
        return f"{self.dataset}:T{self.T}:{self.start}:{self.initialization}:forced={self.forced_shedding}"


def arms():
    rows = []

    def add(dataset, T, start, initialization="stock", modes=("original", "both"), forced=False):
        for mode in modes:
            rows.append(Arm(len(rows)+1, dataset, T, start, initialization, mode, forced))

    add("tracy", 3, 1165, modes=tuple(MODES))
    for initialization in ("historical_unprepared", "historical_prepared"):
        add("tracy", 3, 1165, initialization)
    for start in (3308, 8580):
        add("tracy", 3, start)
    add("tracy", 6, 1165)
    add("case9", 3, 0)
    for T in (3, 6, 24):
        add("tracy", T, 1165, forced=True)
    assert len(rows) == LIMITS["max_launches"]
    return tuple(rows)


def shortage(kwargs):
    """Conservative aggregate real-supply bound, including full storage rating.

    Ignoring energy limits, reactive support and network losses overestimates
    deliverable supply. Thus a positive deficit proves unavoidable shedding,
    not feasibility of the remaining AC constraints or optimal shedding.
    This bound is specific to these passive, no-HVDC, nonnegative-load fixtures.
    """
    demand = kwargs["df_load_p"].to_numpy(dtype=float).sum(axis=1)
    generator = sum(g.p_max_mw for g in kwargs["generators"])
    renewable = np.minimum(kwargs["df_nd"].to_numpy(dtype=float),
                           [u.apparent_power_rating for u in kwargs["nondispatchable"]]).sum(axis=1)
    battery = sum(s.apparent_power_rating for s in kwargs["storage"])
    upper = generator + renewable + battery
    if (kwargs.get("hvdc") or np.any(kwargs["df_load_p"].to_numpy() < 0)
            or np.any(kwargs["case"]["branch"][:, BR_R] < 0) or np.any(kwargs["case"]["bus"][:, GS] < 0)
            or not np.isfinite(demand).all() or not np.isfinite(upper).all() or np.any(demand <= 0)):
        raise ValueError("shortage witness requires finite positive demand and no HVDC")
    deficit = np.maximum(demand-upper, 0.)
    return dict(demand_mw=demand.tolist(), supply_upper_mw=upper.tolist(),
                minimum_shed_mw=deficit.tolist(),
                minimum_ens_mwh=float(kwargs["delta"] * deficit.sum()))


def kwargs_for_arm(arm, prepared=None):
    call = original.Call(arm.id, "ac", "combined_ac", arm.dataset, arm.T,
                         arm.start, arm.start+arm.T if arm.dataset == "tracy" else 0)
    kwargs = original.kwargs_for_call(call, prepared)
    before = shortage(kwargs)
    factor = 1.
    if arm.forced_shedding:
        # Prespecified input-only rule: one scalar per interval, 10% above the
        # largest supply/demand ratio. Never selected using solver outcomes.
        factor = max(1., 1.1 * max(np.asarray(before["supply_upper_mw"]) /
                                 np.asarray(before["demand_mw"])))
        kwargs["df_load_p"] = kwargs["df_load_p"] * factor
        kwargs["df_load_q"] = kwargs["df_load_q"] * factor
    witness = shortage(kwargs)
    if arm.forced_shedding and (min(witness["minimum_shed_mw"]) <= 0 or
                                witness["minimum_ens_mwh"] <= GATES["forced_shedding_energy_mwh"]):
        raise ValueError("stress fixture does not prove material unavoidable shedding")
    return call, kwargs, dict(forced_shedding=arm.forced_shedding, demand_multiplier=factor,
                             unmodified=before, modified=witness)


def construct(arm, prepared=None):
    call, kwargs, stress = kwargs_for_arm(arm, prepared)
    build = original.build_for_call(call, kwargs)
    start = q.physical_start(build, kwargs)
    if arm.initialization != "stock":
        if (arm.dataset, arm.T, arm.start, arm.forced_shedding) != ("tracy", 3, 1165, False):
            raise ValueError("historical starts apply only to the identical original Tracy T=3 inputs")
        number = {"historical_unprepared": 24, "historical_prepared": 25}[arm.initialization]
        start = diagnostic.historical_point(number)
        for variable in build.prob.variables():
            variable.save_value(np.asarray(start[variable.name()], float).copy())
        # Reuse the declared start rule: exact Pg/ND entries and the initial
        # SoC boundary are set to their input values, without clipping free
        # coordinates. The same adjusted physical start is used in both arms.
        start = q.physical_start(build, kwargs)
    components = MODES[arm.mode]
    view = coordinates.transform(build, kwargs["delta"], bool(components),
                                 components=components or MODES["both"])
    view.assign(start)
    frozen = serializable(original.call_binding(call, kwargs))
    return call, kwargs, view, start, stress, frozen


def row_binding(arm, prepared=None):
    _, _, view, start, stress, frozen = construct(arm, prepared)
    check = coordinates.equivalence(view, start)
    if not check["passed"]:
        raise ValueError("physical/objective substitution equivalence failed")
    return dict(arm=asdict(arm), group=arm.group, frozen=frozen,
                physical_start=serializable(start), start_sha256=input_digest(serializable(start)),
                scales=serializable(view.scales), stress=stress, equivalence=check)
