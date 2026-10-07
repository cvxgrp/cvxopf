"""Offline reconstruction of all retained iterates, including MOSEK UNKNOWN.

No optimizer invocation. Numerical feasibility is distinct from native success.
Source artifacts are read-only; analysis is published as a separate supplement.
"""

import argparse
import gzip
import json
from pathlib import Path

import cvxpy as cp
import numpy as np

from cvxopf import extract_results
from experiments.socp_conditioning import cone_scaling as c
from experiments.socp_conditioning import cone_scaling_analysis as ca
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import fixed_substitution as f
from experiments.socp_conditioning import joint_scaling as j
from experiments.socp_conditioning import no_shedding as n
from experiments.socp_conditioning import optimality_audit as oa
from experiments.socp_conditioning import termination_probe as t


def numerical_checks(audit):
    failed = {
        k: dict(value=v, limit=audit["limits"][k])
        for k, v in audit["residuals"].items()
        if not np.isfinite(v) or v > audit["limits"][k]
    }
    return dict(
        residual_checks_passed=not failed,
        failed_checks=failed,
        status_inclusive_audit_passed=audit["passed"],
    )


def verify_root(root):
    summary = json.loads((root / "summary.json").read_text())
    binding = json.loads((root / "binding.json").read_text())
    if summary["context"] != binding or [a["solver"] for a in summary["arms"]] != list(
        t.SOLVERS
    ):
        raise ValueError("root identity mismatch")
    for name, digest in (binding["sources"] | binding["additional_sources"]).items():
        if d.sha(d.ROOT / name) != digest:
            raise ValueError(f"reconstruction source changed: {name}")
    records = {}
    for arm in summary["arms"]:
        folder = root / arm["solver"].lower()
        for key, name in (
            ("arm_sha256", "arm.json.gz"),
            ("supervision_sha256", "supervision.json"),
        ):
            if d.sha(folder / name) != arm[key]:
                raise ValueError("root artifact mismatch")
        sup = json.loads((folder / "supervision.json").read_text())
        for name, digest in sup["artifacts"].items():
            if d.sha(folder / name) != digest:
                raise ValueError("supervised artifact mismatch")
        r = json.load(gzip.open(folder / "arm.json.gz", "rt"))
        if (
            sup["classification"] != "completed"
            or sup["returncode"] != 0
            or r["context"] != binding
            or r["context_after"] != binding
            or r["optimizer_calls"] != 1
            or r["exception"] is not None
        ):
            raise ValueError("execution evidence mismatch")
        records[arm["solver"]] = r
    return records


def analyze(root, reference):
    records = verify_root(root)
    kwargs, _ = d.load_case("surplus", reference)
    kwargs = n.fixed_inputs(kwargs)
    build, original, _ = d.build_variant(kwargs, "fixed_boxes")
    data, _, inverse = build.prob.get_problem_data("CLARABEL", canon_backend="SCIPY")
    c.verify_reference(data, inverse, d.input_digest(kwargs), True, directory=t.PRIOR)
    reduction = f.reduce(data, inverse)
    layout = oa.cone_layout(str(reduction.data["dims"]), reduction.data["A"].shape[0])
    R, D = j.joint_scales(reduction.data, layout)
    scaled = c.transform(reduction.data, R, D)
    t.exact_matrices(scaled, ca.matrices(t.PRIOR / "transformed.npz"))
    results = []
    for solver, r in records.items():
        extra = {}
        if solver == "MOSEK":
            # CVXPY dualizes the continuous conic task. Its equality multipliers
            # are the original conic primal, even when native status is UNKNOWN.
            # Retain that status; reading an iterate is not accepting a solution.
            prob, y = t.replay_problem(scaled, layout)
            prob = cp.Problem(
                cp.Minimize(t.conic_objective(scaled, y)), prob.constraints
            )
            md, _, _ = prob.get_problem_data("MOSEK", canon_backend="SCIPY")
            pp = md["param_prob"]
            native_y = np.asarray(r["native"]["task_y"])
            native_x = np.asarray(r["native"]["task_x"])
            assert md["A"].shape == (len(native_y), len(native_x))
            assert abs(md["b"] @ native_y - r["native"]["task_dual_objective"]) < 1e-8
            assert abs(md["c"] @ native_x - r["native"]["task_primal_objective"]) < 1e-8
            start = pp.var_id_to_col[y.id]
            xhat = native_y[start : start + y.size]
            row, by_id = 0, {}
            for con in pp.constraints:
                by_id[con.id] = native_x[row : row + con.size]
                row += con.size
            assert row == len(native_x)
            zhat = np.concatenate([by_id[con.id] for con in prob.constraints])
            extra = dict(
                interpretation="offline UNKNOWN-iterate audit; no solver acceptance",
                native_return_code=r["native_return_code"],
                task_primal_cost_check=float(md["c"] @ native_x),
                task_dual_cost_check=float(md["b"] @ native_y),
                replay_variable_offset=start,
            )
        else:
            with np.load(
                root / solver.lower() / "vectors.npz", allow_pickle=False
            ) as v:
                xhat, zhat = v["x"], v["z"]
        full_x = np.zeros(len(data["c"]))
        full_x[reduction.fixed], full_x[reduction.free] = reduction.values, D * xhat
        for v in data["param_prob"].variables:
            offset = inverse[-2].var_offsets[v.id]
            v.save_value(full_x[offset : offset + v.size].reshape(v.shape, order="F"))
        result = extract_results(build)
        result.update(
            objective=float(original.value),
            status=r["cvxpy_status"] or "native_rejected_iterate",
        )
        named = {
            k: float(v.value)
            for k, v in build.expressions.items()
            if k.endswith("_cost")
        }
        audit = n.audit("surplus", build, result, kwargs, named)
        slack = scaled["b"] - scaled["A"] @ xhat
        primal = float(0.5 * xhat @ (scaled["P"] @ xhat) + scaled["c"] @ xhat)
        dual = float(-0.5 * xhat @ (scaled["P"] @ xhat) - scaled["b"] @ zhat)
        check = dict(
            solver=solver,
            native_success=r["native_success"],
            accepted=r["accepted"],
            physical_objective=result["objective"],
            named_costs=named,
            canonical_primal=primal,
            canonical_dual_expression=dual,
            signed_gap_expression=primal - dual,
            dual_expression_note="not a certified lower bound without dual feasibility/stationarity",
            original_objective_discrepancy=abs(
                primal
                + float(inverse[-1]["offset"])
                + reduction.offset
                - result["objective"]
            ),
            common_primal_cones=oa.cone_errors(slack, layout),
            common_dual_cones=oa.cone_errors(zhat, layout, dual=True),
            common_stationarity_inf=oa.inf(
                scaled["P"] @ xhat + scaled["c"] + scaled["A"].T @ zhat
            ),
            complementarity=float(slack @ zhat),
            audit=audit,
            **numerical_checks(audit),
            **extra,
        )
        if solver == "CLARABEL":
            prior = json.load(gzip.open(t.PRIOR / "arm.json.gz", "rt"))
            check["prior_iterate_exact"] = all(
                np.array_equal(
                    prior["transformed_native_solution"][k], r["raw_clarabel"][k]
                )
                for k in ("x", "s", "z")
            )
        results.append(check)
    return dict(
        analysis_source_sha256=d.sha(__file__),
        analysis_context=d.context(),
        root_summary_sha256=d.sha(root / "summary.json"),
        solves_performed=0,
        promotional=False,
        arms=results,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.root.resolve(), args.reference.resolve())
    d.publish(args.output.resolve(), d.e3.encode(result))
    print(
        json.dumps(
            [
                {
                    k: a[k]
                    for k in (
                        "solver",
                        "native_success",
                        "residual_checks_passed",
                        "physical_objective",
                        "failed_checks",
                    )
                }
                for a in result["arms"]
            ],
            indent=2,
        )
    )
