"""Prespecified convex cost-coordinate matrix; construction never optimizes."""

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from experiments.ac_cost_qualification.fixture import shortage
from experiments.numerical_preparation import fixture as old
from experiments.numerical_preparation.audit import serializable
from tests.socp_matched import digest as input_digest
from . import model

HERE = Path(__file__).resolve().parent
FORMS = ("socp", "lossy_dc", "singlenode_dc")
LIMITS = dict(max_launches=96, wall_seconds=180., rss_mib=16384.,
              total_worker_seconds=17280., poll_seconds=1.)
GATES = dict(cost_abs=1e-4, cost_rel=1e-6, reconstruction_abs=1e-7,
             reconstruction_rel=1e-12, forced_shedding_energy_mwh=1e-4)


@dataclass(frozen=True)
class Arm:
    id: int
    formulation: str
    dataset: str
    T: int
    start: int
    forced_shedding: bool
    prepared: bool
    cost_coordinates: bool

    @property
    def group(self):
        return f"{self.formulation}:{self.dataset}:T{self.T}:{self.start}:forced={self.forced_shedding}"

    @property
    def treatment(self):
        return f"{'prepared' if self.prepared else 'baseline'}_{'cost' if self.cost_coordinates else 'original'}"


def arms():
    rows = []
    fixtures = (("tracy", 3, 1165, False), ("tracy", 3, 3308, False),
                ("tracy", 3, 8580, False), ("tracy", 6, 1165, False),
                ("case9", 3, 0, False), ("tracy", 3, 1165, True),
                ("tracy", 6, 1165, True), ("tracy", 24, 1165, True))
    for form in FORMS:
        for dataset, T, start, forced in fixtures:
            for prepared in (False, True):
                for scaled in (False, True):
                    rows.append(Arm(len(rows)+1, form, dataset, T, start, forced, prepared, scaled))
    return tuple(rows)


def kwargs_for_arm(arm, prepared_inputs=None):
    treatment = ("prepared_socp" if arm.formulation == "socp" else "prepared_dc") if arm.prepared else "baseline"
    call = old.Call(arm.id, arm.formulation, treatment, arm.dataset, arm.T,
                    arm.start, arm.start+arm.T if arm.dataset == "tracy" else 0)
    kwargs = old.kwargs_for_call(call, prepared_inputs)
    before = shortage(kwargs)
    factor = max(1., 1.1*max(np.asarray(before["supply_upper_mw"])/
                            np.asarray(before["demand_mw"]))) if arm.forced_shedding else 1.
    if arm.forced_shedding:
        kwargs["df_load_p"] = kwargs["df_load_p"]*factor
        kwargs["df_load_q"] = kwargs["df_load_q"]*factor
    witness = shortage(kwargs)
    if arm.forced_shedding and (min(witness["minimum_shed_mw"]) <= 0 or
                               witness["minimum_ens_mwh"] <= GATES["forced_shedding_energy_mwh"]):
        raise ValueError("forced fixture lacks an input-only shortage witness")
    return call, kwargs, dict(forced_shedding=arm.forced_shedding, demand_multiplier=factor,
                             unmodified=before, modified=witness)


def construct(arm, prepared_inputs=None):
    from cvxopf import build_opf_multistep
    call, kwargs, stress = kwargs_for_arm(arm, prepared_inputs)
    view = model.transform(build_opf_multistep(**kwargs), kwargs["delta"], arm.cost_coordinates)
    # CLARABEL is cold-started; this deterministic point checks algebra only.
    point = {v.name(): np.zeros(v.shape) for v in view.physical.prob.variables()}
    point["b"][:] = 2.
    point["load_shed_fraction"][:] = .1
    view.assign(point)
    check = model.equivalence(view, point)
    if not check["passed"] or not view.solver.prob.is_dcp():
        raise ValueError("cost-coordinate expression equivalence or DCP failed")
    return call, kwargs, view, stress, check


def row_binding(arm, prepared_inputs=None):
    call, kwargs, view, stress, check = construct(arm, prepared_inputs)
    canonical = model.canonical(view.solver)
    return dict(arm=asdict(arm), group=arm.group, treatment=arm.treatment,
                frozen=serializable(old.call_binding(call, kwargs)),
                scales=serializable(view.scales), stress=stress, equivalence=check,
                canonical=canonical.signature,
                mathematical_input_sha256=input_digest(old.mathematical_inputs(kwargs)))
