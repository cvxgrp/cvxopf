"""Frozen qualification inputs/settings; importing this module never solves."""

from dataclasses import asdict, dataclass, replace
from pathlib import Path

import clarabel
import numpy as np
import pandas as pd

from cvxopf import (Load, NondispatchableUnit, NumericalPreparation, OPFOptions,
                    StorageUnitIdeal, build_opf, build_opf_multistep, gen_from_matpower)
from cvxopf.testcases import case9
from experiments.case118_tracy_2021 import e3
from experiments.case118_tracy_2021.prepare import ROOT, digest
from experiments.case118_tracy_2021.stage_d import read
from tests.socp_matched import digest as input_digest, json_value

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "results/qualification_001"
LIMITS = dict(max_launches=25, wall_seconds=180., rss_mib=16384.,
              total_worker_seconds=4500., poll_seconds=1.)
PINS = {
    "plans/socp-conditioning-evidence-inventory.json": "97efdf91c7494765624638571eb8a3d0097c4e3d51b197d60b06ca134f7d0371",
    "experiments/case118_tracy_2021/e3_selection/selection.json": "54c4879ca0cafaa126b498c25e069688432da24acdbd04c29e4903005f78572e",
    "experiments/case118_tracy_2021/results/e3/binding.json": "a423375c2041e929f5425319d6d0b36a605bdcbddb82bffc74a719b757edccfc",
}
POLICIES = {
    "baseline": NumericalPreparation(),
    "normalized": NumericalPreparation(normalize_device_limits=True),
    "combined_ac": NumericalPreparation(normalize_device_limits=True, exact_fixed_boxes=True),
    "prepared_socp": NumericalPreparation(normalize_device_limits=True, exact_fixed_boxes=True, canonical_scaling="joint5"),
    "prepared_dc": NumericalPreparation(exact_fixed_boxes=True, canonical_scaling="joint5"),
}


@dataclass(frozen=True)
class Call:
    id: int
    formulation: str
    treatment: str
    source: str
    T: int
    start: int = 0
    stop: int = 0
    leg: str = "energy_neutral"


def calls() -> tuple[Call, ...]:
    rows = []

    def add(formulation, treatment, source, T, start=0, stop=0, leg="energy_neutral"):
        rows.append(Call(len(rows) + 1, formulation, treatment, source, T, start, stop, leg))

    for start in (3308, 1165, 8580, 2439):
        add("socp", "prepared_socp", "tracy", 24, start, start + 24)
    add("socp", "prepared_socp", "tracy", 24, 1165, 1189, "deficit_depletion")
    for formulation in ("lossy_dc", "singlenode_dc"):
        for source, T in (("case9", 1), ("case9", 3), ("tracy", 24)):
            for treatment in ("baseline", "prepared_dc"):
                add(formulation, treatment, source, T, 1165 if source == "tracy" else 0,
                    1189 if source == "tracy" else 0)
    for T in (1, 3):
        for treatment in ("baseline", "normalized", "combined_ac"):
            add("ac", treatment, "case9", T)
    for treatment in ("baseline", "combined_ac"):
        add("ac", treatment, "tracy", 3, 1165, 1168)
    return tuple(rows)


def verify_pins():
    for name, expected in PINS.items():
        if digest(ROOT / name) != expected:
            raise ValueError(f"pinned historical reference changed: {name}")


def solver_options(formulation):
    if formulation == "ac":
        return dict(verbose=True, warm_start=False, mu_strategy="adaptive", tol=1e-7,
                    bound_relax_factor=0., hessian_approximation="exact",
                    derivative_test="none", least_square_init_duals="no")
    options = dict(direct_solve_method="qdldl", tol_gap_abs=1e-10, tol_gap_rel=1e-10,
                   tol_feas=1e-10, max_iter=5000, max_threads=1)
    if formulation == "socp":
        options.update(tol_gap_rel=1e-6, min_terminate_step_length=1e-8,
                       reduced_tol_gap_abs=1e-10, reduced_tol_gap_rel=1e-10,
                       reduced_tol_feas=1e-10, reduced_tol_infeas_abs=1e-10,
                       reduced_tol_infeas_rel=1e-10, reduced_tol_ktratio=1e-6)
    return options


def resolved_settings(formulation):
    if formulation == "ac":
        # The adapter supplies these six defaults; all are explicit here.
        return solver_options(formulation) | {"ipopt_default_max_iter": 3000}
    settings = clarabel.DefaultSettings()
    for name, value in solver_options(formulation).items():
        setattr(settings, name, value)
    return {name: getattr(settings, name) for name in dir(settings)
            if not name.startswith("_") and not callable(getattr(settings, name))}


def case9_kwargs(T, formulation):
    case = case9()
    generators = gen_from_matpower(case["gen"], case["gencost"])
    generators[1] = replace(generators[1], p_min_mw=50., p_max_mw=50.)
    generators[2] = replace(generators[2], p_min_mw=30., p_max_mw=30. + 1e-7)
    scales = np.array([1., 1.1, .9])[:T]
    loads = [Load(bus=bus, p_load_mw=p, q_load_mvar=q, device_id=f"load{bus}",
                  shedding_cost_per_mwh=10000., max_shed_fraction=1.)
             for bus, p, q in ((5, 90., 30.), (7, 100., 35.), (9, 125., 50.))]
    return dict(case=case, T=T, formulation=formulation, delta=1., generators=generators,
        loads=loads, df_load_p=pd.DataFrame(scales[:, None] * [90., 100., 125.], columns=[u.device_id for u in loads]),
        df_load_q=pd.DataFrame(scales[:, None] * [30., 35., 50.], columns=[u.device_id for u in loads]),
        storage=[StorageUnitIdeal(bus=9, apparent_power_rating=10., capacity=40., initial_soc=20.,
            terminal_soc=18., terminal_constraint="equality", aging_weight=.01, device_id="battery9")],
        nondispatchable=[NondispatchableUnit(bus=5, apparent_power_rating=25., p_available=0., device_id="nd5"),
                        NondispatchableUnit(bus=7, apparent_power_rating=15., p_available=1e-7, device_id="nd7")],
        df_nd=pd.DataFrame({"nd5": [0., 20., 0.][:T], "nd7": [1e-7, 10., 1e-7][:T]}),
        options=OPFOptions(), temporal_assembly="vectorized", automatic_sparse_dispatch=False)


def kwargs_for_call(call, prepared=None):
    if call.source == "case9":
        kwargs = case9_kwargs(call.T, call.formulation)
    else:
        binding = read(ROOT / "experiments/case118_tracy_2021/results/e3/binding.json")
        arm = next(a for a in binding["study"]["arms"] if a["start"] == call.start
                   and a["leg"] == call.leg and a["formulation"] == "socp")
        arm = dict(arm, formulation=call.formulation, stop=call.stop)
        kwargs = e3.kwargs_for_arm(prepared if prepared is not None else e3.verified_inputs(),
                                  arm, binding["study"]["storage_device_ids"])
        if kwargs["T"] != call.T:
            raise ValueError("qualification interval disagrees with T")
    kwargs["options"] = replace(kwargs["options"], numerical_preparation=POLICIES[call.treatment])
    return kwargs


def mathematical_inputs(kwargs):
    value = dict(kwargs)
    value["options"] = replace(value["options"], numerical_preparation=NumericalPreparation())
    return json_value(value)


def build_for_call(call, kwargs):
    if call.source == "case9" and call.T == 1:
        single = {k: v for k, v in kwargs.items() if k not in
                  {"T", "df_load_p", "df_load_q", "df_nd", "temporal_assembly"}}
        return build_opf(**single)
    return build_opf_multistep(**kwargs)


def structural_inputs(kwargs):
    base = float(kwargs["case"]["baseMVA"])
    lower = np.array([g.p_min_mw / base for g in kwargs["generators"]])
    upper = np.array([g.p_max_mw / base for g in kwargs["generators"]])
    pg_fixed = lower == upper
    positive_gaps = np.array([g.p_max_mw > g.p_min_mw for g in kwargs["generators"]])
    if np.any(positive_gaps & pg_fixed):
        raise ValueError("unit conversion collapsed a near-fixed generator")
    availability = kwargs["df_nd"].to_numpy()
    ratings = np.array([u.apparent_power_rating for u in kwargs["nondispatchable"]])
    nd_upper = np.minimum(availability, ratings)
    if np.any((availability > 0) & (nd_upper == 0)):
        raise ValueError("positive renewable availability collapsed")
    return dict(pg_fixed=pg_fixed.tolist(), nd_fixed=(nd_upper == 0).tolist(),
                expected_fixed_count=int(pg_fixed.sum() * kwargs["T"] + (nd_upper == 0).sum()),
                positive_gaps_retained=True)


def call_binding(call, kwargs):
    physical = mathematical_inputs(kwargs)
    return dict(call=asdict(call), mathematical_inputs=physical,
                mathematical_input_sha256=input_digest(physical),
                policy=asdict(POLICIES[call.treatment]),
                solver_options=solver_options(call.formulation),
                resolved_settings=resolved_settings(call.formulation),
                structure=structural_inputs(kwargs))
