"""One solver or audit in a fresh process, using that side's environment."""

import argparse
import fcntl
import json
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np

from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json, atomic_json, sha256_path,
)

from .contract import (
    ANGLE_TOL_DEG, MW_TOL, V_TOL, box_excess, coordinates, decode_case, require, selected_links,
    solution_point, validate_cell, worst,
)


def read(path):
    return json.loads(path.read_text())


def serializable(value):
    if isinstance(value, np.ndarray):
        return serializable(value.tolist())
    if isinstance(value, np.generic):
        return serializable(value.item())
    if isinstance(value, dict):
        return {k: serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    if isinstance(value, float) and not np.isfinite(value):
        return str(value)  # Retain diagnostic nonfinites without invalid JSON.
    return value


def build_model(side, cell):
    case = decode_case(cell["case"])
    validate_cell(cell["cell"], case)
    if side == "cvxopf":
        from .cvx_side import CvxModel
        model = CvxModel(case)
        require(model.ratings == cell["ratings"], "CVXOPF branch rating proof changed")
    else:
        from .pypower_side import PypModel, versions
        versions()
        model = PypModel(case, [item["rating_mva"] for item in cell["ratings"]])
    return model


def residual_check(values, ids, units, tolerance):
    """Record the worst excess and the limit used to admit it."""
    return {**worst(values, ids, units), "tolerance": tolerance}


def cvx_native_checks(data, x, tolerance):
    """Evaluate the lowered IPOPT problem on x, including its auxiliary leaves."""
    variables = data["_bounds"].main_var  # The stock adapter's Fortran-order layout.
    leaves, variable_ids = {}, []
    saved, offset = [var.value for var in variables], 0
    try:
        for var in variables:
            value = x[offset:offset + var.size].reshape(var.shape, order="F")
            leaves[var.id] = value
            var.save_value(value)
            variable_ids.extend(f"{var.name()}[{i}]" for i in range(var.size))
            offset += var.size
        require(offset == len(x), "native IPOPT vector/layout mismatch")
        values, row_ids = [], []
        for i, row in enumerate(data["problem"].constraints):
            values.extend(np.asarray(row.expr.value).ravel(order="F"))
            row_ids.extend(f"{i}:{type(row).__name__}[{j}]" for j in range(row.size))
        checks = {
            "variable_bounds": residual_check(box_excess(data["lb"], data["ub"], x),
                                              variable_ids, "native variable units", tolerance),
            "constraint_rows": residual_check(box_excess(data["cl"], data["cu"], np.asarray(values)),
                                              row_ids, "native row units", tolerance),
        }
        return leaves, checks
    finally:
        for var, value in zip(variables, saved, strict=True):
            var.save_value(value)


def pyp_native_checks(model, x):
    """Evaluate all bounds and PIPS callbacks on its actual returned vector."""
    checks = {}
    for name in model.var_order:
        cols = model.cols(name)
        scale, units, tol = {
            "Va": (np.rad2deg(1.), "degrees", ANGLE_TOL_DEG),
            "Vm": (1., "p.u.", V_TOL),
            "Pg": (model.base, "MW", MW_TOL),
            "Qg": (model.base, "MVAr", MW_TOL),
            "y": (1., "objective units", 1e-4),  # Unbounded; ycon checks the epigraph below.
        }[name]
        checks[f"bounds:{name}"] = residual_check(
            scale * box_excess(model.xmin[cols], model.xmax[cols], x[cols]),
            cols.tolist(), units, tol,
        )
    ax = model.A @ x
    for name in model.lin_order:
        rows = model.indices(model.ll, name)
        if not len(rows):
            continue
        values = box_excess(model.lower[rows], model.upper[rows], ax[rows])
        if name == "ycon":
            # Per-generator floor agreement in objective units. Compare each
            # row with the allowance at the projected cost of its own y.
            cols = model.cols("y")
            matrix = model.A[rows, :][:, cols].toarray()
            y = np.asarray([row["minimum_cost"] for row in model.pwl_floors(x)])
            thresholds = 1e-4 + 1e-6 * (abs(matrix) @ np.abs(y))
            i = int(np.argmax(values / thresholds))
            checks[f"linear:{name}"] = residual_check(
                values[[i]], rows[[i]].tolist(), "objective units", float(thresholds[i]),
            )
        else:
            scale, units, tol = ((np.rad2deg(1.), "degrees", ANGLE_TOL_DEG) if name == "ang"
                                 else (model.base, "MW/MVAr projection", MW_TOL))
            checks[f"linear:{name}"] = residual_check(scale * values, rows.tolist(), units, tol)
    h, g, _, _ = model.gh(x)
    for name, values in zip(("real_balance", "reactive_balance"), np.split(g, 2), strict=True):
        checks[name] = residual_check(model.base * values[model.bus_cols],
                                      model.case["bus"][:, 0].astype(int).tolist(),
                                      "MW" if name == "real_balance" else "MVAr", MW_TOL)
    rating = np.tile(model.ppc["branch"][:, 5], 2)
    flow = np.sqrt(np.maximum(0., model.base**2 * h + rating**2))
    checks["thermal"] = residual_check(
        np.maximum(0., flow - rating),
        [f"{row}:{end}" for end in ("from", "to") for row in model.branch_rows], "MVA", MW_TOL,
    )
    return checks


def solve_cvx(model, point, options, directory):
    import cvxpy as cp
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT

    model.evaluate(point)  # Populate every original leaf with the same physical start.
    stock_solve, stock_invert = IPOPT.solve_via_data, IPOPT.invert
    received, handoff = {}, {}

    def observe(adapter, data, warm_start, verbose, solver_opts, solver_cache=None):
        require(not handoff, "more than one IPOPT call for one benchmark arm")
        handoff.update(data)
        atomic_immutable_json(directory / "native_start.json", serializable({
            "x0": data["x0"], "warm_start": warm_start, "options": solver_opts,
            "layout": [{"name": var.name(), "shape": var.shape}
                       for var in data["_bounds"].main_var],
        }))
        native = stock_solve(adapter, data, warm_start, verbose, solver_opts, solver_cache)
        atomic_immutable_json(directory / "native_return.json", serializable(native))
        received.update(native)
        return native

    def observe_invert(adapter, solution, inverse_data):
        handoff["objective_offset"] = float(inverse_data.offset)
        return stock_invert(adapter, solution, inverse_data)

    error = None
    began = time.monotonic()
    with patch.object(IPOPT, "solve_via_data", observe), patch.object(IPOPT, "invert", observe_invert):
        try:
            model.build.solve(solver=cp.IPOPT, nlp=True, warm_start=False, verbose=False, **options)
        except cp.error.SolverError as exc:
            if not received:
                raise
            error = str(exc)
    seconds = time.monotonic() - began
    status = int(received["status"])
    native_objective = received.get("obj_val")
    objective = None if native_objective is None else native_objective + handoff["objective_offset"]
    primal = point_out = None
    native_checks = {}
    x = np.asarray(received.get("x"), dtype=float)
    if x.shape == handoff["x0"].shape and np.isfinite(x).all():
        mapped, native_checks = cvx_native_checks(handoff, x, options["constr_viol_tol"])
        if all(var.id in mapped and mapped[var.id].shape == var.shape for var in model.variables.values()):
            leaves = {key: mapped[var.id] for key, var in model.variables.items()}
            primal = serializable(leaves)
            order = [model.build.data["ext_to_int"][bus] for bus in point["bus_ids"]]
            base = model.case["baseMVA"]
            point_out = solution_point(
                model.case, leaves["theta"][order], leaves["v"][order],
                base * leaves["Pg"], base * leaves["Qg"],
                zip(leaves.get("p_hvdc_in", []), leaves.get("p_hvdc_out", []), strict=True),
            )
    return dict(native_success=status in (0, 1) and error is None, native_status=status,
                api_status=model.build.prob.status, api_error=error,
                objective=serializable(objective), native_objective=serializable(native_objective),
                objective_offset=handoff["objective_offset"],
                api_objective=serializable(model.build.prob.value),
                point=point_out, named_primal=primal, native_checks=native_checks,
                solver_call_seconds=seconds)


def solve_pyp(model, point, options, directory):
    from pypower.pips import pips

    theta, vm, pg, qg = coordinates(model.case, point)
    x0 = model.dispatch(theta, vm, pg, qg,
                        [(link["net_from_mw"], link["net_to_mw"]) for link in point["links"]])
    effective = {**model.callbacks["options"], **options}
    atomic_immutable_json(directory / "native_start.json", serializable({
        "x0": x0, "options": effective, "variable_blocks": model.vv,
        "bus_ids_internal_order": model.ids, "internal_columns_for_source_gen_rows": model.gen_cols,
    }))
    began = time.monotonic()
    native = pips(model.f, x0, model.A, model.lower, model.upper, model.xmin, model.xmax,
                  model.gh, model.hess, effective)
    seconds = time.monotonic() - began
    atomic_immutable_json(directory / "native_return.json", serializable(native))
    x = np.asarray(native["x"])
    point_out = dummy_q = None
    native_checks = {}
    if x.shape == x0.shape and np.isfinite(x).all():
        real_pg = model.base * x[model.cols("Pg")[model.gen_cols]]
        real_qg = model.base * x[model.cols("Qg")[model.gen_cols]]
        ng, nd = model.ng, len(selected_links(model.case))
        point_out = solution_point(
            model.case, x[model.cols("Va")[model.bus_cols]], x[model.cols("Vm")[model.bus_cols]],
            real_pg[:ng], real_qg[:ng],
            ((real_pg[ng + k], real_pg[ng + nd + k] + dc[15])
             for k, dc in enumerate(model.links.values())),
        )
        dummy_q = real_qg[ng:].tolist()
        native_checks = pyp_native_checks(model, x)
    return dict(native_success=bool(native["eflag"]), native_status=serializable(native["output"]),
                objective=serializable(native["f"]), point=point_out, dummy_q_mvar=dummy_q,
                native_checks=native_checks, solver_call_seconds=seconds)


def worker(root, attempt):
    """Refuse a direct worker invocation unless it is the bound pending attempt."""
    directory = root / "attempts" / attempt
    request = read(directory / "request.json")
    binding_path = root / "binding.json"
    digest = sha256_path(binding_path)
    require(read(root / "authority.json") == {"binding_sha256": digest, "execution_authorized": True}
            and request["binding_sha256"] == digest,
            "worker does not have matching launch authority")
    binding = read(binding_path)
    side, cell = request["solver"], request["cell"]
    require([cell, side] in binding["arms"], "worker not in frozen matrix")
    # One use even if the worker is invoked directly twice. A worker may not
    # start after the parent already finalized supervision.
    with (root / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not (directory / "supervision.json").exists(), "attempt already finalized")
        atomic_immutable_json(directory / "worker_started.json", {"attempt": attempt})
        cell_data = binding["cells"][cell]
        began = time.monotonic()
        atomic_json(directory / "phase.json", {"phase": "building"})
        model = build_model(side, cell_data)
        build_seconds = time.monotonic() - began
        atomic_json(directory / "phase.json", {"phase": "solver_call"})
        solve = solve_cvx if side == "cvxopf" else solve_pyp
        raw = solve(model, cell_data["start"], binding["settings"][side], directory)
        atomic_json(directory / "phase.json", {"phase": "publishing"})
        atomic_immutable_json(directory / "return.json", {
            **raw, "cell": cell, "solver": side, "binding_sha256": digest,
            "build_seconds": build_seconds,
            "native_artifacts": {
                name: sha256_path(directory / name) for name in ("native_start.json", "native_return.json")
            },
        })


def guard(*args, **kwargs):
    raise RuntimeError("benchmark cross-audit forbids optimization")


def audit(side, request_path, output):
    request = read(request_path)
    # The PYPOWER build itself intercepts PIPS to capture callbacks without a
    # solve. Neither audit branch calls a native or public solve entry point.
    if side == "cvxopf":
        import cvxpy as cp
        from cvxopf.problem import OPFBuild
        with patch.object(cp.Problem, "solve", guard), patch.object(OPFBuild, "solve", guard):
            report = build_model(side, request["cell"]).evaluate(request["point"], numerical=True)
    else:
        report = build_model(side, request["cell"]).evaluate(request["point"], numerical=True)
    atomic_immutable_json(output, {"solver": side, "request_sha256": sha256_path(request_path),
                                   "evaluation": serializable(report)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    work = sub.add_parser("solve")
    work.add_argument("root", type=Path)
    work.add_argument("attempt")
    check = sub.add_parser("audit")
    check.add_argument("side", choices=("cvxopf", "pypower"))
    check.add_argument("request", type=Path)
    check.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.action == "solve":
        worker(args.root, args.attempt)
    else:
        audit(args.side, args.request, args.output)
