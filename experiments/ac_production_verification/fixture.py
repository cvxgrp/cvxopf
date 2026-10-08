"""Small prospective Tracy matrix; reuse qualified inputs, not experimental solving."""

from dataclasses import asdict, replace

from cvxopf import NumericalPreparation, build_opf_multistep
from experiments.ac_cost_qualification import fixture as qualified
from experiments.numerical_preparation import fixture as original, run_qualification as q
from experiments.numerical_preparation.audit import serializable

LIMITS = dict(max_launches=8, wall_seconds=180., rss_mib=16384.,
              total_worker_seconds=1440., poll_seconds=1.)


def arms():
    rows = []
    for T, forced in ((3, False), (3, True), (6, True), (24, True)):
        for mode in ("original", "both"):
            rows.append(qualified.Arm(len(rows)+1, "tracy", T, 1165, "stock", mode, forced))
    return tuple(rows)


def construct(arm, prepared=None):
    call, kwargs, stress = qualified.kwargs_for_arm(arm, prepared)
    policy = NumericalPreparation(normalize_device_limits=True, exact_fixed_boxes=True,
                                  cost_coordinates=arm.mode == "both")
    kwargs["options"] = replace(kwargs["options"], numerical_preparation=policy)
    build = build_opf_multistep(**kwargs)
    start = q.physical_start(build, kwargs)
    return call, kwargs, build, start, stress


def row_binding(arm, prepared=None):
    call, kwargs, _, start, stress = construct(arm, prepared)
    frozen = original.call_binding(call, kwargs)
    frozen["policy"] = asdict(kwargs["options"].numerical_preparation)
    return serializable(dict(arm=asdict(arm), group=arm.group, frozen=frozen,
                             physical_start=start, stress=stress))
