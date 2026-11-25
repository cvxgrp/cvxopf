"""Fresh-process AC arm. Only a hash-authorized supervisor may invoke it."""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys
from threading import Thread
from time import monotonic, perf_counter, sleep
import warnings

import cvxpy as cp
import cyipopt
from cvxpy.reductions.cvx_attr2constr import CvxAttr2Constr
from cvxpy.reductions.dnlp2smooth.dnlp2smooth import Dnlp2Smooth
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
from cvxpy.reductions.solvers.nlp_solvers.nlp_solver import Oracles
from cvxpy.reductions.solvers.solving_chain import SolvingChain
from cvxpy.utilities.debug_tools import node_count
import numpy as np

from cvxopf import ac_problem, build_opf, build_opf_multistep, extract_results
from cvxopf._ac_preparation import ReducedOracles
from cvxopf._cost_coordinates import cost_canonicalization, transform_cost_coordinates
from cvxopf._cvxpy_dispatch import sparse_dispatch_policy
from cvxopf._hierarchical_solver import _complete_start
from cvxopf._numerical_preparation import resolve_fixed_map
from cvxopf.network import make_branch_admittance, make_ybus_matpower, reindex_case_to_consecutive
from experiments.case118_tracy_2021 import e3_plus, e3_prepared
from experiments.numerical_preparation.audit import serializable

from .binding import native_solver_identity, require_no_ambient_options, verify_harness
from .preflight import e3, fixture_inputs
from .run import THREADS, atomic_gzip_json, atomic_json, publish, read, sha256_path, utc


def build_model(kwargs):
    return (build_opf_multistep if "T" in kwargs else build_opf)(**kwargs)


def named_start(build):
    # Same input-only primary as E3 (no shifted or recovered source). On a
    # single step this only fills unspecified leaves like stock CVXPY.
    return _complete_start(build)


def graph_evidence(build, warning_messages):
    terminals = {e.id: k for k, e in build.expressions.items() if k.startswith("branch_") and k.endswith("_pu")}
    nodal = {build.variables[k].id: k for k in ("p", "q")}
    reported = {int(match[1]) for message in warning_messages
                if (match := re.match(r"Constraint #(\d+) contains too many subexpressions", message))}
    seen_lhs, shared, rows = set(), set(), []

    def walk(node, seen):
        if id(node) not in seen:
            seen.add(id(node))
            for child in node.args:
                walk(child, seen)

    for i, constraint in enumerate(build.prob.constraints):
        variable = getattr(constraint.args[0], "id", None)
        dependencies = set(terminals).intersection(v.id for v in constraint.variables())
        if variable in terminals:
            role = terminals[variable] + " definition"
        elif variable in nodal:
            role = nodal[variable] + (" balance" if variable in seen_lhs else " row sum")
            seen_lhs.add(variable)
        elif dependencies and isinstance(constraint, cp.constraints.Inequality):
            role = "from thermal limit" if any("from" in terminals[k] for k in dependencies) else "to thermal limit"
        elif i in reported:
            role = "other " + type(constraint).__name__
        else:
            continue
        unique = set()
        walk(constraint, unique)
        shared.update(unique)
        rows.append(dict(index=i, role=role, shape=list(constraint.shape), warned=i in reported,
                         recursive_nodes=node_count(constraint), unique_nodes=len(unique)))
    return dict(constraints=rows, unique_nodes_across_groups=len(shared))


def compiled(build):
    """Public solve's DNLP chain, stopped before the native optimizer."""
    prepared = build.numerical_preparation.enabled
    solver = IPOPT()
    with sparse_dispatch_policy(build.automatic_sparse_dispatch):
        began = perf_counter()
        target, bindings = transform_cost_coordinates(build) if prepared else (build, ())
        smooth = cost_canonicalization(bindings) if prepared else Dnlp2Smooth()
        preparation = perf_counter() - began
        chain = SolvingChain(reductions=[CvxAttr2Constr(reduce_bounds=not solver.BOUNDED_VARIABLES), smooth, solver])
        began = perf_counter()
        data, inverse = chain.apply(target.prob)
        reductions = perf_counter() - began
        began = perf_counter()
        mapping = (resolve_fixed_map(target._exact_boxes, inverse[:-1], inverse[-1],
                   data["_bounds"].problem.constraints, len(data["x0"]), len(data["cl"])) if prepared else None)
        preparation += perf_counter() - began
        began = perf_counter()
        oracle = Oracles(data["problem"], verbose=False, use_hessian=True)
        if mapping is not None:
            oracle = ReducedOracles(oracle, mapping)
        jac = len(oracle.jacobianstructure()[0])
        hess = len(oracle.hessianstructure()[0])
        derivatives = perf_counter() - began
    return dict(preparation_seconds=preparation, reduction_seconds=reductions, derivative_seconds=derivatives,
                canonical_variables=len(data["x0"]), canonical_constraints=len(data["cl"]),
                native_variables=len(mapping.free) if mapping is not None else len(data["x0"]),
                native_constraints=len(mapping.kept) if mapping is not None else len(data["cl"]),
                stored_jacobian_entries=jac, stored_hessian_entries=hess)


def snapshot_audit(case, result, tolerances):
    """Independently evaluate the stock snapshot from physical primals."""
    internal, _ = reindex_case_to_consecutive(case)
    bus, gen, cost = (internal[k] for k in ("bus", "gen", "gencost"))
    base = internal["baseMVA"]
    adm = make_branch_admittance(internal)
    residuals, limits = {}, {}

    def array(key, size):
        x = np.asarray(result[key], dtype=float)
        if x.shape != (size,) or not np.isfinite(x).all():
            raise ValueError(f"Invalid snapshot result channel: {key}")
        return x

    def check(name, values, limit):
        residuals[name] = float(np.max(np.abs(values), initial=0.))
        limits[name] = limit

    def box(name, values, lower, upper, limit):
        check(name, np.maximum(np.maximum(lower-values, values-upper), 0), limit)

    vm, va = (array(k, len(bus)) for k in ("Vm", "Va_deg"))
    pg, qg = (array(k, len(gen)) for k in ("Pg", "Qg"))
    v = vm * np.exp(1j * np.deg2rad(va))
    physical = v * np.conj(make_ybus_matpower(internal, branch_admittance=adm) @ v) * base
    online = gen[:, 7] == 1
    injections = -(bus[:, 2] + 1j * bus[:, 3])
    np.add.at(injections, gen[online, 0].astype(int), (pg + 1j*qg)[online])
    eps = tolerances["power"]
    check("p_balance_mw", (injections-physical).real, eps)
    check("q_balance_mvar", (injections-physical).imag, eps)
    check("p_reporting_mw", array("p_net", len(bus))-physical.real, eps)
    check("q_reporting_mvar", array("q_net", len(bus))-physical.imag, eps)
    box("voltage_pu", vm, bus[:, 12], bus[:, 11], tolerances["voltage"])
    check("reference_angle_deg", va[bus[:, 1] == 3], tolerances["angle"])
    # The vanilla build does not enforce generator voltage setpoints or
    # branch angle differences. Out-of-service generators have zero bounds.
    box("generator_p_mw", pg, np.where(online, gen[:, 9], 0), np.where(online, gen[:, 8], 0), eps)
    box("generator_q_mvar", qg, np.where(online, gen[:, 4], 0), np.where(online, gen[:, 3], 0), eps)
    vf, vt = v[adm.from_bus], v[adm.to_bus]
    rated = adm.status & (adm.rate_a_mva > 0) & np.isfinite(adm.rate_a_mva)
    for side, s in (("from", vf * np.conj(adm.yff*vf + adm.yft*vt) * base),
                    ("to", vt * np.conj(adm.ytf*vf + adm.ytt*vt) * base)):
        mva = abs(s)
        for kind, expected in (("p", s.real), ("q", s.imag), ("s", mva)):
            key = f"branch_{kind}_{side}"
            check(key, array(key, len(s))-expected, eps)
        check(f"thermal_{side}_mva", np.maximum(mva[rated]-adm.rate_a_mva[rated], 0), eps)
    if not np.all(cost[:, 0] == 2):
        raise ValueError("Snapshot audit expects the pinned polynomial costs")
    objective = float(sum(np.polyval(row[4:4+int(row[3])], p) for p, row in zip(pg, cost, strict=True)))
    check("objective", float(result["objective"])-objective,
          tolerances["cost_abs"] + tolerances["cost_rel"]*abs(objective))
    return dict(passed=all(np.isfinite(v) and v <= limits[k] for k, v in residuals.items()),
                residuals=residuals, limits=limits, costs={"generator_cost": objective})


def audit(build, result, kwargs, fixture, tolerances):
    if "T" not in kwargs:
        return snapshot_audit(kwargs["case"], result, tolerances)
    model = e3_plus if fixture == "forced-shedding" else e3_prepared
    named = {k: float(e.value) if e.value is not None else None
             for k, e in build.expressions.items() if k.endswith("_cost") and e.is_scalar()}
    record, _ = model.audit(build, result, kwargs, named)
    return record


def solve(build, settings, capture):
    """Observe the actual handoff in this isolated worker; no prospective compile."""
    original = IPOPT.solve_via_data
    original_problem = cyipopt.Problem
    handoff = None

    class OptionRecorder:
        def __init__(self, *args, **kwargs):
            self.problem = original_problem(*args, **kwargs)
            self.options = {}

        def add_option(self, key, value):
            self.problem.add_option(key, value)
            self.options[key] = value

        def solve(self, x0):
            data, options, cache = handoff
            if not np.array_equal(x0, data["x0"]):
                raise ValueError("Native start differs from the IPOPT adapter handoff")
            capture(data, options, self.options, cache)
            return self.problem.solve(x0)

    def observe(self, data, warm_start, verbose, solver_opts, solver_cache=None):
        nonlocal handoff
        handoff = data, solver_opts, solver_cache
        began = perf_counter()
        raw = original(self, data, warm_start, verbose, solver_opts, solver_cache)
        observable.update(adapter_seconds=perf_counter()-began, native_status=int(raw["status"]))
        return raw

    observable = {}
    IPOPT.solve_via_data = observe
    cyipopt.Problem = OptionRecorder
    try:
        build.solve(solver="IPOPT", verbose=True, warm_start=False, print_level=5,
                    hessian_approximation="exact", **settings)
    except cp.error.SolverError:
        if observable.get("native_status") in (None, 0):
            raise
    finally:
        IPOPT.solve_via_data = original
        cyipopt.Problem = original_problem
    if "native_status" not in observable:
        raise ValueError("IPOPT did not report a native status")
    return observable


def start_receipt(data, adapter_options, native_options, cache):
    offset, layout = 0, []
    for variable in data["problem"].variables():
        stop = offset + variable.size
        layout.append(dict(id=variable.id, name=variable.name(), shape=variable.shape, start=offset, stop=stop))
        offset = stop
    mapping = getattr((cache or {}).get("oracles"), "mapping", None)
    fixed = None if mapping is None else dict(free=mapping.free, fixed=mapping.fixed, values=mapping.values)
    return dict(x0=data["x0"].copy(), full_canonical_layout=layout, fixed=fixed,
                explicit_adapter_options=adapter_options, native_add_options=dict(native_options))


def verify_start(build, physical, receipt):
    """Match assigned and actual coordinates using the original solve's evidence."""
    layout = {row["id"]: row for row in receipt["full_canonical_layout"]}
    evidence = build.preparation_evidence
    maps = {}
    raw = receipt["x0"]
    if build.numerical_preparation.enabled:
        if evidence is None or receipt["fixed"] is None or not np.array_equal(evidence.reduced_x0, raw):
            raise ValueError("Prepared IPOPT handoff differs from preparation evidence")
        mapping = evidence.coordinates
        for key in ("free", "fixed", "values"):
            if not np.array_equal(receipt["fixed"][key], getattr(mapping, key)):
                raise ValueError("Prepared IPOPT fixed-coordinate map differs")
        assigned = evidence.assigned_x0
        actual = mapping.expand(raw)
        if not np.array_equal(actual, evidence.adjusted_x0):
            raise ValueError("Prepared IPOPT starting vector does not round-trip")
        maps = {m["physical_variable_id"]: m for m in evidence.checks.get("cost_coordinate_maps", ())}
    else:
        if receipt["fixed"] is not None:
            raise ValueError("Unexpected coordinate reduction for stock IPOPT")
        actual = assigned = raw
    if max(row["stop"] for row in layout.values()) != actual.size:
        raise ValueError("Canonical starting layout does not cover the full vector")
    adjusted = {}
    by_name = {row["name"]: row for row in layout.values()}
    for variable in build.prob.variables():
        name = variable.name()
        transform = maps.get(variable.id)
        if transform is not None:
            row = layout[transform["solver_variable_id"]]
            scale = transform["scale"]
            if not np.array_equal(transform["physical_start"], physical[name]):
                raise ValueError(f"Cost-coordinate input start changed: {name}")
        else:
            row = layout.get(variable.id, by_name.get(name))
            scale = 1.
        if row is None or tuple(row["shape"]) != variable.shape:
            raise ValueError(f"Native layout omitted physical starting coordinate: {name}")
        indices = slice(row["start"], row["stop"])
        if not np.array_equal(assigned[indices], (physical[name]*scale).ravel(order="F")):
            raise ValueError(f"Bound physical start does not match the actual IPOPT handoff: {name}")
        adjusted[name] = actual[indices].reshape(variable.shape, order="F") / scale
    if set(adjusted) != set(physical):
        raise ValueError("Native physical starting namespaces differ")
    return dict(physical_start_sha256=e3.input_digest(adjusted), cost_coordinate_maps=list(maps.values()),
                assigned_full_x0=assigned, effective_physical_start=adjusted)


def watch_supervisor(fd):
    """EOF when the supervisor exits, including SIGKILL; no stale PID test."""
    def watch():
        with os.fdopen(fd, "rb") as pipe:
            pipe.read(1)
        os._exit(75)

    Thread(target=watch, daemon=True).start()


def worker(root, directory, parent_fd):
    watch_supervisor(parent_fd)
    binding = read(root / "binding.json")
    request = read(directory / "request.json")
    launch = directory / "launch.json"
    deadline = monotonic() + 5
    while not launch.exists() and monotonic() < deadline:
        sleep(0.01)
    spec = next(u for u in binding["work"] if u["id"] == request["id"])
    if (read(launch)["pid"] != os.getpid() or (directory / "supervision.json").exists() or
            request != {**spec, "ordinal": request["ordinal"], "binding_sha256": sha256_path(root / "binding.json")}):
        raise ValueError("Worker does not match its live supervisor or binding")
    require_no_ambient_options()
    verify_harness(binding["harness"])
    if native_solver_identity() != binding["environment"]["ipopt"]:
        raise ValueError("Worker loaded a different native IPOPT library")
    if any(os.environ.get(k) != v for k, v in THREADS.items()):
        raise ValueError("Worker thread environment differs")
    source_rel = "src/cvxopf/ac_problem.py"
    expected = root / "sources" / spec["revision"] / source_rel
    actual = Path(ac_problem.__file__).resolve()
    if (actual != expected.resolve() or sha256_path(actual) != binding["source_hashes"][spec["revision"]][source_rel]
            or Path(ac_problem._make_branch_terminal_flow.__code__.co_filename).resolve() != actual):
        raise ValueError("Worker imported a different AC builder")
    began = monotonic()

    def phase(value):
        atomic_json(directory / "phase.json", dict(phase=value, utc=utc(), elapsed=monotonic()-began))

    phase("inputs")
    fixture = spec["fixture"]
    name, metadata, kwargs = fixture_inputs(fixture, root / "case500.m")
    bound = binding["fixtures"][name]
    if bound != {**metadata, "input_sha256": e3.input_digest(kwargs),
                 "named_start_sha256": bound["named_start_sha256"]}:
        raise ValueError("Worker fixture differed from the bound inputs")
    phase("construction")
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        start = perf_counter()
        build = build_model(kwargs)
        build_seconds = perf_counter() - start
    physical = named_start(build)
    if e3.input_digest(physical) != bound["named_start_sha256"]:
        raise ValueError("Worker named physical start differed from binding")
    metrics = dict(build_seconds=build_seconds)
    evidence = dict(fixture=metadata, input_sha256=bound["input_sha256"],
                    named_start_sha256=bound["named_start_sha256"], model_file=str(actual),
                    model_sha256=sha256_path(actual), warnings=[str(w.message) for w in captured])
    if spec["phase"] == "compile":
        phase("reductions_and_derivatives")
        metrics.update(compiled(build))
        evidence.update(graph=graph_evidence(build, evidence["warnings"]),
                        modeled_variables=sum(v.size for v in build.prob.variables()),
                        modeled_constraints=sum(c.size for c in build.prob.constraints))
        publish(directory, request, "measured", metrics=metrics, evidence=evidence)
        return

    receipt = None

    def capture(data, adapter_options, native_options, cache):
        nonlocal receipt
        receipt = start_receipt(data, adapter_options, native_options, cache)
        atomic_gzip_json(directory / "start.json.gz", serializable(dict(
            iteration=request["ordinal"], **receipt, named_physical_start=physical,
        )))
        phase("native_adapter")

    phase("solve")
    metrics.update(solve(build, binding["settings"]["ipopt"], capture))
    if metrics["native_status"] != 0:
        publish(directory, request, "native_rejection", metrics=metrics, evidence=evidence)
        return
    result = extract_results(build)
    metrics["build_to_extraction_seconds"] = perf_counter() - start
    if receipt is None:
        raise ValueError("No start captured at native entry")
    verified = verify_start(build, physical, receipt)
    metrics["start_sha256"] = verified["physical_start_sha256"]
    metrics["native_options_sha256"] = e3.input_digest(receipt["native_add_options"])
    atomic_gzip_json(directory / "verified-start.json.gz", serializable(dict(iteration=request["ordinal"], **verified)))
    phase("audit")
    try:
        checked = audit(build, result, kwargs, fixture, binding["settings"]["snapshot_tolerances"])
    except (KeyError, TypeError, ValueError) as exc:
        checked = dict(passed=False, reason=str(exc), residuals={}, limits={})
    if "costs" in checked:
        metrics["physical_objective"] = sum(checked["costs"].values())
    outcome = "accepted" if checked["passed"] else "audit_rejection"
    publish(directory, request, outcome, metrics=serializable(metrics), evidence=evidence,
            result=serializable(result), audit=serializable(checked))


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve()
    name = sys.argv[2]
    if Path(name).name != name or not name.startswith("attempt-"):
        raise ValueError("Invalid attempt directory")
    worker(root, root / "attempts" / name, int(sys.argv[3]))
