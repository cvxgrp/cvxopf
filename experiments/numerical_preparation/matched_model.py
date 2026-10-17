"""Matched physical fixtures and solver-forbidden native replay.

The baseline cycling tracker is analysis-only: it delegates to the installed
DNLP canonicalizer and never replaces the baseline production solve graph.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
from importlib.metadata import distribution, version
from pathlib import Path
import platform
import subprocess
import sys

import cvxpy as cp
import numpy as np
from cvxpy.reductions.cvx_attr2constr import CvxAttr2Constr
from cvxpy.reductions.dnlp2smooth.dnlp2smooth import Dnlp2Smooth
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
from cvxpy.reductions.solvers.solving_chain import SolvingChain

from cvxopf import (NumericalPreparation, OPFOptions, StorageUnitIdeal, Load,
                    NondispatchableUnit, DispatchableGenerator, build_opf_multistep)
from cvxopf._cvxpy_dispatch import sparse_dispatch_policy
from cvxopf._numerical_preparation import resolve_fixed_map
from experiments.case118_tracy_2021 import e3, e3_prepared
from experiments.case118_tracy_2021.prepare import ROOT, digest, SOURCE
from experiments.case118_tracy_2021.stage_b import TOLERANCES
from experiments.m11_socp.e1_compare import canonical_problem, restored_result
from experiments.numerical_preparation.audit import serializable
from experiments.numerical_preparation.fixture import (
    PINS, mathematical_inputs, resolved_settings, solver_options, verify_pins,
)
from experiments.numerical_preparation.run_qualification import physical_start
from tests.socp_matched import digest as input_digest

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "results/matched_comparison_001"
THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "VECLIB_MAXIMUM_THREADS")
LIMITS = dict(rss_mib=16384., worker_seconds_warning=57600.,
              poll_seconds=1., wall_seconds_by_T={"3": 3600., "24": 3600.})


def arms():
    """Stable identities; counterbalance separately within each formulation."""
    rows = []
    for scenario, start in (("Large deficit", 1165), ("Deficit to surplus", 2439)):
        for T in (3, 24):
            for formulation in ("ac", "socp"):
                pair = len(rows) // 2 + 1
                fixture_number = len(rows) // 4
                order = ("baseline", "prepared") if fixture_number % 2 == 0 else ("prepared", "baseline")
                for treatment in order:
                    rows.append(dict(id=len(rows)+1, pair=pair, scenario=scenario,
                                     start=start, stop=start+T, T=T,
                                     formulation=formulation, treatment=treatment))
    return rows


def policy(arm):
    return (NumericalPreparation() if arm["treatment"] == "baseline" else
            e3_prepared.policy(arm["formulation"],
                               "component_first" if arm["formulation"] == "ac" else "hourly"))


def inputs(prepared, study, arm):
    original = next(a for a in study["arms"] if a["start"] == arm["start"]
                    and a["leg"] == "energy_neutral" and a["formulation"] == arm["formulation"])
    kwargs = e3.kwargs_for_arm(prepared, dict(original, stop=arm["stop"]), study["storage_device_ids"])
    kwargs["options"] = replace(kwargs["options"], numerical_preparation=policy(arm))
    return kwargs


def row(arm, kwargs):
    return serializable(dict(arm=arm, input_sha256=input_digest(mathematical_inputs(kwargs)),
        physical_inputs=mathematical_inputs(kwargs), policy=asdict(policy(arm)),
        settings=solver_options(arm["formulation"]),
        resolved_settings=resolved_settings(arm["formulation"])))


def thaw(item):
    """Materialize the admitted narrow slice, not the complete annual inventory."""
    import pandas as pd
    from copy import deepcopy
    value = deepcopy(item["physical_inputs"])
    value["case"] = {k: np.asarray(v) if isinstance(v, list) else v
                     for k, v in value["case"].items()}
    for name, cls in (("storage", StorageUnitIdeal), ("generators", DispatchableGenerator),
                      ("loads", Load), ("nondispatchable", NondispatchableUnit)):
        value[name] = [cls(**v) for v in value[name]]
    for name in ("df_load_p", "df_load_q", "df_nd"):
        frame = value[name]
        value[name] = pd.DataFrame(frame["values"], columns=frame["columns"],
                                  index=pd.to_datetime(frame["index"]) if frame["index"] and
                                  isinstance(frame["index"][0], str) else frame["index"])
    options = value["options"]
    options["numerical_preparation"] = NumericalPreparation(**item["policy"])
    value["options"] = OPFOptions(**options)
    if row(item["arm"], value) != {k: v for k, v in item.items() if k != "preflight_start"}:
        raise ValueError("admitted physical slice does not round-trip exactly")
    return value


def admit_inputs():
    """Validate stable E3 history once per invocation, never per worker."""
    verify_pins()
    return e3.verified_inputs(), e3.specification()


def context():
    """Explicit dependency scope, not a whole-worktree cleanliness lock.

    src/cvxopf is one model dependency unit. Existing experiment helpers are
    listed explicitly; numerical distributions include adapters and binaries.
    """
    import os
    import cyipopt
    paths = list((ROOT / "src/cvxopf").rglob("*.py"))
    helpers = {
        "experiments/numerical_preparation": ("matched_model.py", "run_matched.py", "matched_audit.py",
            "fixture.py", "audit.py", "tracy_variables.py", "run_qualification.py", "MATCHED_COMPARISON_PLAN.md"),
        "experiments/case118_tracy_2021": ("e3.py", "e3_prepared.py", "prepare.py", "model_inputs.py",
            "stage_b.py", "stage_d.py", "stage_d_physics.py", "annual_results.py", "run_stage_b.py",
            "run_stage_d.py", "run_e3.py", "ac_archive.py", "plot_window_selection.py"),
        "experiments/m11_socp": ("e1_compare.py", "e2_v2_audit.py", "e2_compare.py"),
        "experiments/ac_cost_qualification": ("audit.py", "fixture.py"),
        "experiments/ac_cost_coordinates": ("model.py", "run.py"),
        "experiments/ac_production_verification": ("audit.py", "fixture.py"),
        "experiments/case118_annual_hierarchy": ("streaming_schema.py", "run_s4.py", "pglib_case.py",
            "s5_closeout.py"),
        "tests": ("socp_matched.py", "socp_reference_cases.py", "socp_reference_comparison.py", "socp_reference_audit.py"),
    }
    paths += [ROOT / directory / name for directory, names in helpers.items() for name in names]
    paths += [ROOT / "uv.lock", ROOT / "pyproject.toml"]
    sources = {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths))}
    packages, installed = {}, {}
    for name in ("cvxpy", "clarabel", "cyipopt", "sparsediffpy", "numpy", "scipy", "pandas"):
        packages[name] = version(name)
        for item in distribution(name).files or ():
            if str(item).endswith((".py", ".so", ".dylib")):
                path = Path(distribution(name).locate_file(item)).resolve()
                installed[str(path)] = digest(path)
    if (ROOT / "ipopt.opt").exists():
        raise ValueError("ambient ipopt.opt would alter matched settings")
    data_paths = [SOURCE, ROOT / "experiments/case118_tracy_2021/e3_selection/selection.json"]
    data_paths += list((ROOT / "experiments/case118_tracy_2021/stage_a").glob("*"))
    from experiments.case118_annual_hierarchy.pglib_case import SOURCE_CASE_PATH
    data_paths.append(SOURCE_CASE_PATH)
    data_paths += [ROOT / name for name in PINS]
    return dict(sources=sources, packages=packages, installed=installed,
        inputs={str(p.relative_to(ROOT)): digest(p) for p in data_paths if p.is_file()},
        ipopt_version=list(cyipopt.IPOPT_VERSION),
        python=sys.version, executable=sys.executable,
        thread_environment={k: os.environ.get(k) for k in THREAD_KEYS},
        platform=platform.platform())


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def execution_revision(current, commit):
    if not commit or commit != git("rev-parse", "HEAD"):
        raise ValueError("name the reviewed full execution commit")
    for path in current["sources"]:
        if not git("ls-files", "--", path) or git("status", "--porcelain", "--", path):
            raise ValueError(f"uncommitted execution dependency: {path}")
    if any(current["thread_environment"][k] != "1" for k in THREAD_KEYS):
        raise ValueError("single numerical thread required before imports")
    return commit


@contextmanager
def no_optimization():
    """Forbid both public calls and direct native entry during science replay."""
    import clarabel
    import cyipopt
    previous = cp.Problem.solve, clarabel.DefaultSolver, cyipopt.Problem, IPOPT.solve_via_data
    def forbidden(*args, **kwargs):
        raise RuntimeError("matched admission/replay must not optimize")
    cp.Problem.solve = clarabel.DefaultSolver = cyipopt.Problem = IPOPT.solve_via_data = forbidden
    try:
        yield
    finally:
        cp.Problem.solve, clarabel.DefaultSolver, cyipopt.Problem, IPOPT.solve_via_data = previous


class BaselineCycling(Dnlp2Smooth):
    """Track only declared storage abs atoms; preserve stock DNLP mathematics."""
    def __init__(self, build):
        super().__init__()
        self.terms = {term.variable.id: term for term in build._cost_coordinate_terms
                      if term.kind == "cycling" and np.any(np.asarray(term.weights.value) > 0)}
        self.cycling_auxiliaries = []

    def canonicalize_expr(self, expr, args, affine_above):
        result, constraints = super().canonicalize_expr(expr, args, affine_above)
        if isinstance(expr, cp.abs) and len(expr.args) == 1 and len(expr.variables()) == 1:
            term = self.terms.get(expr.variables()[0].id)
            if term is not None:
                if not isinstance(result, cp.Variable):
                    raise ValueError("unsupported baseline cycling canonicalizer")
                self.cycling_auxiliaries.append(((term, expr.args[0]), result))
        return result, constraints


def ac_graph(build, assigned):
    e3_prepared.starts._assign_start(build, {k: np.asarray(v) for k, v in assigned.items()})
    if build.numerical_preparation.cost_coordinates:
        return e3_prepared.ac_canonical(build)
    reduction = BaselineCycling(build)
    solver = IPOPT()
    with sparse_dispatch_policy(build.automatic_sparse_dispatch):
        data, inverse = SolvingChain(reductions=[
            CvxAttr2Constr(reduce_bounds=not solver.BOUNDED_VARIABLES), reduction, solver,
        ]).apply(build.prob)
    if len(reduction.cycling_auxiliaries) != len(reduction.terms):
        raise ValueError("baseline cycling provenance coverage differs")
    mapping = resolve_fixed_map(build._exact_boxes, inverse[:-1], inverse[-1],
        data["_bounds"].problem.constraints, len(data["x0"]), len(data["cl"]))
    return assigned, build, (), reduction, data, inverse, mapping


def ac_start(build, assigned, *, graph=None):
    _, _, bindings, _, data, inverse, mapping = ac_graph(build, assigned) if graph is None else graph
    adjusted = data["x0"].copy()
    adjusted[mapping.fixed] = mapping.values
    return serializable(dict(assigned_start=assigned, complete_x0=data["x0"],
        adjusted_x0=adjusted, reduced_x0=adjusted[mapping.free],
        layout=[[list(v.shape), inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size]
                for v in data["problem"].variables()],
        coordinates=dict(full_size=mapping.full_size, row_count=mapping.row_count,
                         fixed=mapping.fixed, values=mapping.values, dropped=mapping.dropped),
        cost_maps=[dict(kind=b.term.kind, shape=list(b.leaf.shape), scale=b.scale,
                        physical_start=b.physical_start) for b in bindings]))


def frozen_binding(*, canonical=False):
    with no_optimization():
        prepared, study = admit_inputs()
        rows, starts = [], {}
        for arm in arms():
            kwargs = inputs(prepared, study, arm)
            item = row(arm, kwargs)
            if canonical:
                build = build_opf_multistep(**kwargs)
                if arm["formulation"] == "ac":
                    assigned = serializable(physical_start(build, kwargs))
                    if arm["pair"] in starts and starts[arm["pair"]] != assigned:
                        raise ValueError("paired AC physical starts differ")
                    starts[arm["pair"]] = assigned
                    graph = ac_graph(build, assigned)
                    item["preflight_start"] = ac_start(build, assigned, graph=graph)
                    original, transformed = float(build.prob.objective.value), float(graph[1].prob.objective.value)
                    if not np.isclose(original, transformed, rtol=1e-12, atol=1e-7):
                        raise ValueError("cost transformation changes the physical cold-start objective")
                else:
                    canonical_problem(build)
            rows.append(item)
        for pair in range(1, 9):
            a, b = [r for r in rows if r["arm"]["pair"] == pair]
            if a["physical_inputs"] != b["physical_inputs"] or a["settings"] != b["settings"]:
                raise ValueError("matched pair changes physics or solver settings")
    return dict(schema=2, experiment="matched_numerical_preparation", context=context(),
        rows=rows, limits=dict(LIMITS), interruption_policy="retry_on_explicit_resume",
        admission=dict(historical_pins=PINS, source_hashes=study["source_hashes"],
                       storage_device_ids=study["storage_device_ids"]),
        gates=dict(physical=TOLERANCES, socp=asdict(e3.RELAXATION_TOLERANCES),
                   canonical_reconstruction=dict(abs=1e-7, rel=1e-12),
                   economic=dict(abs=1e-4, rel=1e-6), cycling="explained slack is advisory"))
