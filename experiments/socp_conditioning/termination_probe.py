"""Serial native-termination diagnostic on one identical reduced/scaled SOCP.

Re-express the frozen canonical QP/SOC in CVXPY solely for solver delivery.
CLARABEL must receive exactly the original matrices; MOSEK/COPT may introduce
objective epigraph cones and their own canonical transformations. No tuning loop.
"""

import argparse
from contextlib import contextmanager
from copy import copy
from importlib.metadata import version
from itertools import groupby
import json
from pathlib import Path
import signal
import time
from unittest.mock import patch

import cvxpy as cp
import numpy as np
from scipy import sparse

from cvxopf import extract_results
from experiments.socp_conditioning import cone_scaling as c
from experiments.socp_conditioning import cone_scaling_analysis as ca
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import fixed_substitution as f
from experiments.socp_conditioning import joint_scaling as j
from experiments.socp_conditioning import matrix_conditioning as m
from experiments.socp_conditioning import mosek_check as supervision
from experiments.socp_conditioning import no_shedding as n
from experiments.socp_conditioning import optimality_audit as oa

SOLVERS = ("CLARABEL", "MOSEK", "COPT")
PRIOR = d.HERE / "results/fixed_substitution_001/fixed_surplus_scaled"
PRIOR_SHA = "7f33b6dda454586be49036c3fa7059bb23fc1512d6e5a391fd3a4073878c0fe4"


def options(solver):
    if solver == "CLARABEL":
        # Reduced tolerances are used only in final post-processing. Equalizing
        # them to full tolerances exposes the underlying failed termination.
        return dict(d.e3.DEFAULT_PROTOCOL["convex_options"]) | dict(
            reduced_tol_gap_abs=1e-10,
            reduced_tol_gap_rel=1e-10,
            reduced_tol_feas=1e-10,
            reduced_tol_infeas_abs=1e-10,
            reduced_tol_infeas_rel=1e-10,
            reduced_tol_ktratio=1e-6,
        )
    if solver == "MOSEK":
        return dict(
            mosek_params=dict(
                MSK_IPAR_NUM_THREADS=1,
                MSK_IPAR_INTPNT_MAX_ITERATIONS=5000,
                MSK_DPAR_INTPNT_CO_TOL_PFEAS=1e-10,
                MSK_DPAR_INTPNT_CO_TOL_DFEAS=1e-10,
                MSK_DPAR_INTPNT_CO_TOL_REL_GAP=1e-10,
            )
        )
    if solver == "COPT":
        # These are the supported lower limits, not a claim of equal criteria.
        return dict(
            Threads=1,
            FeasTol=1e-9,
            DualTol=1e-9,
            BarIterLimit=5000,
            LogLevel=3,
            reoptimize=False,
        )
    raise ValueError("unknown solver")


def context():
    base = f.context()
    paths = [Path(__file__), Path(__file__).with_name("TERMINATION_PROTOCOL.md")]
    return base | dict(
        experiment="three_solver_native_termination_fixed_surplus",
        additional_sources=base["additional_sources"]
        | {str(p.relative_to(d.ROOT)): d.sha(p) for p in paths},
        extra_packages={k: version(k) for k in ("mosek", "coptpy")},
        prior_arm_sha256=PRIOR_SHA,
        options={s: options(s) for s in SOLVERS},
    )


def replay_problem(data, layout):
    """Preserve row order; batch only consecutive SOCs of the same size."""
    zero, nonneg, soc = layout
    y = cp.Variable(len(data["c"]), name="scaled_free_coordinates")
    A, b = data["A"].tocsr(), data["b"]
    constraints = []
    if zero:
        constraints.append(A[:zero] @ y == b[:zero])
    end = zero + nonneg
    if nonneg:
        constraints.append(A[zero:end] @ y <= b[zero:end])
    for size, group in groupby(soc):
        count = sum(1 for _ in group)
        stop = end + size * count
        slack = cp.reshape(b[end:stop] - A[end:stop] @ y, (size, count), order="F")
        constraints.append(cp.SOC(slack[0], slack[1:], axis=0))
        end = stop
    if end != A.shape[0]:
        raise ValueError("incomplete cone layout")
    objective = 0.5 * cp.quad_form(y, cp.psd_wrap(data["P"])) + data["c"] @ y
    return cp.Problem(cp.Minimize(objective), constraints), y


def exact_matrices(expected, actual):
    for key in ("A", "P"):
        if (
            expected[key].shape != actual[key].shape
            or (expected[key] != actual[key]).nnz
        ):
            raise ValueError(f"canonical replay changed {key}")
    for key in ("b", "c"):
        np.testing.assert_array_equal(expected[key], actual[key])


def conic_objective(data, y):
    """Exact diagonal factor, avoiding dense LDL of a large singular P.

    Commercial conic adapters need a quadratic objective epigraph. Only the
    nonzero diagonal coordinates enter its sum-of-squares cone.
    """
    diagonal = data["P"].diagonal()
    if (data["P"] - sparse.diags(diagonal)).nnz or np.any(diagonal < 0):
        raise ValueError("expected nonnegative diagonal quadratic objective")
    indices = np.flatnonzero(diagonal)
    factor = np.sqrt(0.5 * diagonal[indices])
    np.testing.assert_allclose(2 * factor**2, diagonal[indices], rtol=5e-16, atol=0)
    return cp.sum_squares(cp.multiply(factor, y[indices])) + data["c"] @ y


def info_snapshot(info):
    result = {
        k: getattr(info, k)
        for k in (
            "cost_dual",
            "cost_primal",
            "gap_abs",
            "gap_rel",
            "iterations",
            "ktratio",
            "mu",
            "res_dual",
            "res_primal",
            "sigma",
            "solve_time",
            "step_length",
        )
    }
    return result | dict(status=str(info.status), linsolver=str(info.linsolver))


@contextmanager
def instrumentation(solver, directory, record):
    """Capture native state without changing iterates or starting a second solve."""
    if solver == "CLARABEL":
        import clarabel

        factory = clarabel.DefaultSolver
        handles = []
        trace = []

        def create(*args):
            handle = factory(*args)

            def callback(info):
                trace.append(info_snapshot(info))
                return False

            handle.set_termination_callback(callback)
            handles.append(handle)
            return handle

        with patch.object(clarabel, "DefaultSolver", create):
            try:
                yield
            finally:
                record["optimizer_calls"] = len(handles)
                record["trace"] = trace
                if handles:
                    handle = handles[0]
                    record["native"] = info_snapshot(handle.get_info())
                    record["raw_clarabel"] = c.snapshot(handle.get_solution())
                    record["native_settings"] = str(handle.get_settings())
    elif solver == "MOSEK":
        import mosek
        from cvxpy.reductions.solvers.conic_solvers.mosek_conif import MOSEK

        invert0 = MOSEK.invert
        trace = []

        def solve(self, data, warm_start, verbose, solver_opts, solver_cache=None):
            if warm_start or not data.get("dualized"):
                raise ValueError("expected fresh continuous dualized MOSEK task")
            task = mosek.Task()
            opts = MOSEK.handle_options(task, verbose, solver_opts)
            task = MOSEK._build_dualized_task(task, data)

            def callback(code, dinfo, iinfo, linfo):
                if code == mosek.callbackcode.intpnt:
                    trace.append(
                        dict(
                            iterations=iinfo[mosek.iinfitem.intpnt_iter],
                            **{
                                k: dinfo[getattr(mosek.dinfitem, k)]
                                for k in (
                                    "intpnt_primal_obj",
                                    "intpnt_dual_obj",
                                    "intpnt_primal_feas",
                                    "intpnt_dual_feas",
                                    "intpnt_time",
                                )
                            },
                        )
                    )
                return 0

            task.set_InfoCallback(callback)
            task.writedata(str(directory / "task.ptf.gz"))
            record["optimizer_calls"] += 1
            code = task.optimize()
            record["native_return_code"] = str(code)
            task.solutionsummary(mosek.streamtype.msg)
            return dict(task=task, solver_options=opts)

        def invert(self, output, inverse):
            task = output["task"]
            record["native"] = supervision.capture_task(task, inverse)
            record["native"]["linear_algebra"] = {
                k: task.getdouinf(getattr(mosek.dinfitem, k))
                for k in ("intpnt_factor_num_flops", "intpnt_order_time")
            }
            record["trace"] = trace
            return invert0(self, output, inverse)

        with (
            patch.object(MOSEK, "solve_via_data", solve),
            patch.object(MOSEK, "invert", invert),
        ):
            yield
    else:
        import coptpy
        from cvxpy.reductions.solvers.conic_solvers.copt_conif import COPT

        invert0 = COPT.invert
        solve0 = COPT.solve_via_data

        def solve(self, *args, **kwargs):
            record["optimizer_calls"] += 1
            return solve0(self, *args, **kwargs)

        def invert(self, solution, inverse):
            model = solution["model"]
            record["native"] = dict(
                status=model.status,
                optimal=model.status == coptpy.COPT.OPTIMAL,
                solver_output={k: v for k, v in solution.items() if k != "model"},
                parameters={
                    k: model.getParam(k)
                    for k in (
                        "Threads",
                        "FeasTol",
                        "DualTol",
                        "BarIterLimit",
                        "LogLevel",
                    )
                },
                trace_location="worker.log: native barrier iteration table",
            )
            attrs = {}
            for key in (
                "BarrierIter",
                "BestObj",
                "ObjBound",
                "LpObjVal",
                "SolvingTime",
                "HasLpSol",
            ):
                try:
                    attrs[key] = model.getAttr(key)
                except Exception as exc:
                    attrs[key] = dict(unavailable=str(exc))
            record["native"]["attributes"] = attrs
            return invert0(self, solution, inverse)

        with (
            patch.object(COPT, "solve_via_data", solve),
            patch.object(COPT, "invert", invert),
        ):
            yield


def dual_vector(problem):
    blocks = []
    for constraint in problem.constraints:
        value = constraint.dual_value
        if isinstance(constraint, cp.constraints.second_order.SOC):
            if value is None or any(v is None for v in value):
                return None
            blocks.append(
                np.vstack([np.asarray(value[0]).reshape(1, -1), value[1]]).ravel(
                    order="F"
                )
            )
        else:
            if value is None:
                return None
            blocks.append(np.asarray(value).ravel(order="F"))
    return np.concatenate(blocks)


def worker(directory, reference, solver):
    began = time.monotonic()
    record = dict(
        schema=1,
        solver=solver,
        context=context(),
        exception=None,
        solver_exception=None,
        optimizer_calls=0,
        audit=None,
        native={},
        accepted=False,
    )
    try:
        if d.sha(PRIOR / "arm.json.gz") != PRIOR_SHA:
            raise ValueError("prior artifact changed")
        prior = m.verified_record(PRIOR)
        kwargs, record["historical_reference"] = d.load_case("surplus", reference)
        kwargs = n.fixed_inputs(kwargs)
        record["input_sha256"] = d.input_digest(kwargs)
        build, original, _ = d.build_variant(kwargs, "fixed_boxes")
        data, _, inverse = build.prob.get_problem_data(
            "CLARABEL", canon_backend="SCIPY"
        )
        c.verify_reference(data, inverse, record["input_sha256"], True, directory=PRIOR)
        reduction = f.reduce(data, inverse)
        layout = oa.cone_layout(
            str(reduction.data["dims"]), reduction.data["A"].shape[0]
        )
        R, D = j.joint_scales(reduction.data, layout)
        scaled = c.transform(reduction.data, R, D)
        if d.sha(PRIOR / "transformed.npz") != prior["transformed_archive"]["sha256"]:
            raise ValueError("prior transformed artifact changed")
        exact_matrices(scaled, ca.matrices(PRIOR / "transformed.npz"))
        problem, y = replay_problem(scaled, layout)
        replay, _, _ = problem.get_problem_data("CLARABEL", canon_backend="SCIPY")
        exact_matrices(scaled, replay)
        if str(scaled["dims"]) != str(replay["dims"]):
            raise ValueError("cone order changed")
        if solver != "CLARABEL":
            problem = cp.Problem(
                cp.Minimize(conic_objective(scaled, y)), problem.constraints
            )
        record["common_problem"] = dict(
            transformed_sha256=d.sha(PRIOR / "transformed.npz"),
            original_sha256=d.sha(PRIOR / "canonical.npz"),
            original_offset=float(inverse[-1]["offset"]),
            substitution_offset=reduction.offset,
            matrix_shape=scaled["A"].shape,
            exact_replay_verified=True,
            zero=layout[0],
            nonnegative=layout[1],
            soc_counts=dict((str(k), layout[2].count(k)) for k in set(layout[2])),
        )
        solve_build = copy(build)
        solve_build.prob = problem
        opts = options(solver)
        if solver == "COPT":
            opts = opts | dict(save_file=str(directory / "task.mps"))
        record["solver_options"] = opts
        start = time.monotonic()
        try:
            with instrumentation(solver, directory, record):
                solve_build.solve(solver=solver, warm_start=False, verbose=True, **opts)
        except Exception as exc:
            record["solver_exception"] = f"{type(exc).__name__}: {exc}"
        record["solve_interface_seconds"] = time.monotonic() - start
        record["cvxpy_status"] = problem.status
        if solver == "CLARABEL" and "raw_clarabel" in record:
            raw = record["raw_clarabel"]
            xhat, zhat, shat = (np.asarray(raw[k]) for k in ("x", "z", "s"))
        else:
            xhat = y.value
            zhat = dual_vector(problem)
            shat = None if xhat is None else scaled["b"] - scaled["A"] @ xhat
        if xhat is not None and np.isfinite(xhat).all():
            full_x = np.zeros(len(data["c"]))
            full_x[reduction.fixed] = reduction.values
            full_x[reduction.free] = D * xhat
            # Assign values only, without claiming the unsolved source graph solved.
            for v in data["param_prob"].variables:
                offset = inverse[-2].var_offsets[v.id]
                v.save_value(
                    full_x[offset : offset + v.size].reshape(v.shape, order="F")
                )
            result = extract_results(build)
            result.update(
                objective=float(original.value),
                status=problem.status or "native_rejected_iterate",
            )
            named = {
                k: float(v.value)
                for k, v in build.expressions.items()
                if k.endswith("_cost")
            }
            record["result"], record["named_costs"] = result, named
            record["audit"] = n.audit("surplus", build, result, kwargs, named)
            objective = float(0.5 * xhat @ (scaled["P"] @ xhat) + scaled["c"] @ xhat)
            record["canonical_objective"] = objective
            record["objective_reconstruction_error"] = abs(
                objective
                + reduction.offset
                + float(inverse[-1]["offset"])
                - float(original.value)
            )
            record["original_primal_cones"] = oa.cone_errors(
                data["b"] - data["A"] @ full_x,
                oa.cone_layout(str(data["dims"]), data["A"].shape[0]),
            )
            vectors = dict(x=xhat, full_x=full_x, s=shat)
            if zhat is not None and np.isfinite(zhat).all():
                vectors["z"] = zhat
                record["common_kkt"] = dict(
                    primal_inf=oa.inf(scaled["A"] @ xhat + shat - scaled["b"]),
                    dual_inf=oa.inf(
                        scaled["P"] @ xhat + scaled["c"] + scaled["A"].T @ zhat
                    ),
                    complementarity=float(shat @ zhat),
                    dual_objective=float(
                        -0.5 * xhat @ (scaled["P"] @ xhat) - scaled["b"] @ zhat
                    ),
                    dual_cones=oa.cone_errors(zhat, layout, dual=True),
                )
            with (directory / "vectors.npz").open("xb") as stream:
                np.savez_compressed(stream, **vectors)
            record["vectors_sha256"] = d.sha(directory / "vectors.npz")
        record["native_success"] = (
            record["native"].get("status") == "Solved"
            if solver == "CLARABEL"
            else record["native"].get("solution_status") == "solsta.optimal"
            if solver == "MOSEK"
            else record["native"].get("optimal") is True
        )
        record["accepted"] = bool(
            record["native_success"]
            and record["solver_exception"] is None
            and (record["audit"] or {}).get("passed") is True
            and record.get("objective_reconstruction_error", np.inf) <= 1e-4
        )
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
        record["accepted"] = False
    record["context_after"] = context()
    if record["context_after"] != record["context"]:
        record["exception"], record["accepted"] = "context changed", False
    record["worker_seconds"] = time.monotonic() - began
    d.publish(directory / "arm.json.gz", d.e3.encode(record))
    d.publish(
        directory / "completion.json",
        dict(
            accepted=record["accepted"],
            arm_sha256=d.sha(directory / "arm.json.gz"),
            worker_wall_seconds=time.monotonic() - began,
        ),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--solver", choices=SOLVERS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()

    def interrupt(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    output, reference = args.output.resolve(), args.reference.resolve()
    if args.worker:
        worker(output, reference, args.solver)
        return
    output.mkdir(parents=True, exist_ok=False)
    binding = context()
    d.publish(output / "binding.json", binding)
    arms = []
    for solver in SOLVERS:
        folder = output / solver.lower()
        supervision.supervise(
            folder,
            reference,
            worker_module=__spec__.name,
            context_factory=context,
            worker_arguments=("--solver", solver),
        )
        sup = json.loads((folder / "supervision.json").read_text())
        if sup["classification"] != "completed":
            raise RuntimeError("stop after resource/process failure")
        record = m.verified_record(folder)
        if (
            record["exception"]
            or record["context"] != binding
            or record["context_after"] != binding
        ):
            raise RuntimeError(
                f"diagnostic construction/audit failure: {record['exception']}"
            )
        arms.append(
            dict(
                solver=solver,
                accepted=record["accepted"],
                status=record["native"].get(
                    "status", record["native"].get("solution_status")
                ),
                audit_passed=(record["audit"] or {}).get("passed"),
                objective=(record.get("result") or {}).get("objective"),
                kkt=record.get("common_kkt"),
                solver_exception=record["solver_exception"],
                solve_interface_seconds=record["solve_interface_seconds"],
                arm_sha256=d.sha(folder / "arm.json.gz"),
                supervision_sha256=d.sha(folder / "supervision.json"),
            )
        )
    d.publish(
        output / "summary.json", dict(context=binding, arms=arms, promotional=False)
    )


if __name__ == "__main__":
    main()
