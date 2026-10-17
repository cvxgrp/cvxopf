"""Offline physical reconstruction and native acceptance checks, no solves."""

import argparse
import gzip
import json
from pathlib import Path

import numpy as np

from experiments.socp_conditioning import practical_lu as p


def convergence(info, settings):
    keys = ("gap_abs", "gap_rel", "res_primal", "res_dual", "ktratio")
    return bool(
        all(np.isfinite(info[k]) and info[k] >= 0 for k in keys)
        and info["ktratio"] <= 1
        and (
            info["gap_abs"] < settings["tol_gap_abs"]
            or info["gap_rel"] < settings["tol_gap_rel"]
        )
        and info["res_primal"] < settings["tol_feas"]
        and info["res_dual"] < settings["tol_feas"]
    )


def analyze(root, reference):
    d, t = p.d, p.t
    summary = json.loads((root / "summary.json").read_text())
    binding = json.loads((root / "binding.json").read_text())
    if (
        summary["context"] != binding
        or d.sha(root / "engine.json") != summary["engine_sha256"]
    ):
        raise ValueError("root identity mismatch")
    for name, digest in (binding["sources"] | binding["additional_sources"]).items():
        if d.sha(d.ROOT / name) != digest:
            raise ValueError(f"execution source changed: {name}")
    results = []
    for expected, case in zip(summary["arms"], p.CASES, strict=True):
        if p.analyze_arm(root, case, binding) != expected:
            raise ValueError("analysis summary changed")
        record = json.load(gzip.open(root / case / "arm.json.gz", "rt"))
        kwargs, _ = d.load_case(case, reference)
        if case == "surplus":
            kwargs = t.n.fixed_inputs(kwargs)
        if d.input_digest(kwargs) != record["input_sha256"]:
            raise ValueError("physical input changed")
        build, original, _ = d.build_variant(kwargs, "fixed_boxes")
        data, _, inverse = build.prob.get_problem_data(
            "CLARABEL", canon_backend="SCIPY"
        )
        prior = d.HERE / "results/fixed_substitution_001" / p.CASES[case][0]
        t.c.verify_reference(
            data, inverse, record["input_sha256"], case == "surplus", directory=prior
        )
        if d.sha(root / case / "vectors.npz") != record["vectors_sha256"]:
            raise ValueError("retained vectors changed")
        with np.load(root / case / "vectors.npz") as values:
            full_x = values["full_x"]
        for variable in data["param_prob"].variables:
            offset = inverse[-2].var_offsets[variable.id]
            variable.save_value(
                full_x[offset : offset + variable.size].reshape(
                    variable.shape, order="F"
                )
            )
        result = t.extract_results(build)
        result.update(objective=float(original.value), status=record["cvxpy_status"])
        for key in ("Pg", "Qg", "b", "soc", "p_nd", "q_nd"):
            np.testing.assert_array_equal(result[key], record["result"][key])
        named = {
            key: float(value.value)
            for key, value in build.expressions.items()
            if key.endswith("_cost")
        }
        audit_fn = t.n.audit if case == "surplus" else d.original_audit
        audit = audit_fn(case, build, result, kwargs, named)
        if d.e3.encode(audit) != record["audit"]:
            raise ValueError("physical audit reconstruction mismatch")
        native_pass = convergence(record["native"], record["solver_options"])
        accepted = bool(
            record["native"]["status"] == "Solved"
            and native_pass
            and audit["passed"]
            and record["solver_exception"] is None
            and record["objective_reconstruction_error"] <= 1e-4
        )
        if accepted != record["accepted"]:
            raise ValueError("acceptance classification mismatch")
        extra = {}
        if case == "surplus":
            old = json.load(gzip.open(p.OLD_INPUT.parent / "arm.json.gz", "rt"))
            extra["previous_superlu_vectors_exact"] = p.n.same_solution(
                old["raw_clarabel"], record["raw_clarabel"]
            )
        results.append(
            dict(
                case=case,
                accepted=accepted,
                native_criteria_pass=native_pass,
                audit=audit,
                objective=result["objective"],
                named_costs=named,
                **extra,
            )
        )
    return dict(
        schema=1,
        optimizer_calls=0,
        promotional=False,
        arms=results,
        summary_sha256=d.sha(root / "summary.json"),
        analysis_source_sha256=d.sha(__file__),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.root, args.reference)
    p.d.publish(args.root / "analysis.json", p.d.e3.encode(result))
    print(
        json.dumps(
            {
                "arms": [
                    {k: v for k, v in r.items() if k != "audit"} for r in result["arms"]
                ]
            },
            indent=2,
        )
    )
