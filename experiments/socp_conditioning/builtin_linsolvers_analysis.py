"""Offline physical audits and comparison to accepted SuperLU primals."""

import argparse
import gzip
import json
from pathlib import Path

import numpy as np

from experiments.socp_conditioning import builtin_linsolvers as b
from experiments.socp_conditioning.practical_lu_analysis import convergence


def analyze(root, reference):
    d, t = b.d, b.t
    summary = json.loads((root / "summary.json").read_text())
    binding = json.loads((root / "binding.json").read_text())
    if (
        summary["context"] != binding
        or d.sha(root / "availability.json") != summary["availability_sha256"]
    ):
        raise ValueError("root identity mismatch")
    for name, digest in (binding["sources"] | binding["additional_sources"]).items():
        if d.sha(d.ROOT / name) != digest:
            raise ValueError(f"execution source changed: {name}")
    if d.sha(b.PRIOR / "summary.json") != b.PRIOR_SHA:
        raise ValueError("comparison root changed")
    previous = json.loads((b.PRIOR / "summary.json").read_text())
    output = []
    pairs = [(case, method) for case in b.p.CASES for method in b.METHODS]
    for saved, (case, method) in zip(summary["arms"], pairs, strict=True):
        if b.analyze_arm(root, case, method, binding) != saved:
            raise ValueError("arm summary mismatch")
        folder = root / f"{case}-{method}"
        record = json.load(gzip.open(folder / "arm.json.gz", "rt"))
        previous_entry = next(v for v in previous["arms"] if v["case"] == case)
        prior_path = b.PRIOR / case / "arm.json.gz"
        if d.sha(prior_path) != previous_entry["arm_sha256"]:
            raise ValueError("comparison arm changed")
        prior = json.load(gzip.open(prior_path, "rt"))
        kwargs, _ = d.load_case(case, reference)
        if case == "surplus":
            kwargs = t.n.fixed_inputs(kwargs)
        if d.input_digest(kwargs) != record["input_sha256"]:
            raise ValueError("physical inputs changed")
        build, original, _ = d.build_variant(kwargs, "fixed_boxes")
        result = record.get("result")
        audit, differences = None, None
        if result is not None:
            # Restore canonical values before extraction, preserving the source
            # arrays' layout and reduction order (JSON lists do not retain it).
            data, _, inverse = build.prob.get_problem_data(
                "CLARABEL", canon_backend="SCIPY"
            )
            if d.sha(folder / "vectors.npz") != record["vectors_sha256"]:
                raise ValueError("saved full primal changed")
            with np.load(folder / "vectors.npz") as values:
                full_x = values["full_x"]
            for variable in data["param_prob"].variables:
                start = inverse[-2].var_offsets[variable.id]
                variable.save_value(
                    full_x[start : start + variable.size].reshape(
                        variable.shape, order="F"
                    )
                )
            result = t.extract_results(build)
            result.update(
                objective=float(original.value), status=record["result"]["status"]
            )
            for key in ("Pg", "Qg", "b", "soc", "p_nd", "q_nd"):
                np.testing.assert_array_equal(result[key], record["result"][key])
            audit_fn = t.n.audit if case == "surplus" else d.original_audit
            audit = audit_fn(case, build, result, kwargs, record["named_costs"])
            if d.e3.encode(audit) != record["audit"]:
                raise ValueError("physical audit reconstruction mismatch")
            differences = {
                key: float(
                    np.max(
                        np.abs(
                            np.asarray(result[key]) - np.asarray(prior["result"][key])
                        )
                    )
                )
                for key in ("Pg", "Qg", "b", "b_q", "soc", "p_nd", "q_nd", "Vm_relaxed")
            }
        native_pass = convergence(record["native"], record["solver_options"])
        accepted = bool(
            native_pass
            and record["native"]["status"] == "Solved"
            and record["solver_exception"] is None
            and (audit or {}).get("passed")
            and record.get("objective_reconstruction_error", np.inf) <= 1e-4
        )
        if accepted != record["accepted"]:
            raise ValueError("acceptance classification mismatch")
        output.append(
            dict(
                case=case,
                method=method,
                accepted=accepted,
                native_criteria_pass=native_pass,
                audit=audit,
                objective_difference_vs_superlu=None
                if result is None
                else result["objective"] - prior["result"]["objective"],
                max_abs_difference_vs_superlu=differences,
            )
        )
    return dict(
        schema=1,
        optimizer_calls=0,
        promotional=False,
        arms=output,
        summary_sha256=d.sha(root / "summary.json"),
        analysis_source_sha256=d.sha(__file__),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.root, args.reference)
    b.d.publish(args.root / "analysis.json", b.d.e3.encode(result))
    print(
        json.dumps(
            {
                "arms": [
                    {k: v for k, v in a.items() if k != "audit"} for a in result["arms"]
                ]
            },
            indent=2,
        )
    )
