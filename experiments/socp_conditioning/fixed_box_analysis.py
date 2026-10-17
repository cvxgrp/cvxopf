"""Recover and audit the retained unsuccessful native primal without solving."""

import gzip
import json
from pathlib import Path

import numpy as np
from scipy import sparse

from cvxopf import extract_results
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning.optimality_audit import analyze, indices


def main():
    root = d.HERE / "results/fixed_boxes_001"
    directory = root / "surplus-fixed_boxes"
    binding = json.loads((root / "binding.json").read_text())
    with gzip.open(directory / "arm.json.gz", "rt") as stream:
        record = json.load(stream)
    assert record["context"] == record["context_after"]
    for path, expected in record["context"]["sources"].items():
        assert d.sha(d.ROOT / path) == expected, path
    kwargs, _ = d.load_case("surplus", Path(binding["reference"]))
    build, objective, divisor = d.build_variant(kwargs, "fixed_boxes")
    data, _, inverse = build.prob.get_problem_data("CLARABEL", canon_backend="SCIPY")
    assert d.sha(directory / "canonical.npz") == record["canonical_archive"]["sha256"]
    with np.load(directory / "canonical.npz") as saved:
        for key in ("A", "P"):
            retained = sparse.csc_matrix(
                (saved[key + "_data"], saved[key + "_indices"], saved[key + "_indptr"]),
                shape=saved[key + "_shape"],
            )
            difference = data[key] - retained
            assert not difference.nnz, key
        for key in ("b", "c"):
            np.testing.assert_array_equal(data[key], saved[key])
    x = np.asarray(record["native_solution"]["x"])
    saved_layout = record["canonical_archive"]["variables"]
    for var in build.prob.variables():
        match = next(v for v in saved_layout if v["name"] == var.name())
        assert tuple(match["shape"]) == var.shape
        # No solver result is invented: assign the actual retained native point.
        var.value = x[indices(match)].reshape(var.shape, order="F")
    result = extract_results(build)
    result["status"] = "offline_native_InsufficientProgress"
    result["objective"] = float(objective.value)
    named = {
        k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")
    }
    audit = d.original_audit("surplus", build, result, kwargs, named)
    reconstruction = dict(
        result=result,
        audit=audit,
        canonical_evidence=dict(canonical_offset=float(inverse[-1]["offset"])),
    )
    kkt = analyze(directory, reconstructed=reconstruction)
    output = dict(
        schema=1,
        no_new_solve=True,
        accepted=False,
        reason="native InsufficientProgress; offline audit does not promote the solve",
        source_sha256=d.sha(Path(__file__)),
        audit_source_sha256=d.sha(Path(analyze.__code__.co_filename)),
        original_arm_sha256=d.sha(directory / "arm.json.gz"),
        result=result,
        named_costs=named,
        physical_audit=audit,
        optimality=kkt,
    )
    target = root / "offline_analysis.json"
    d.publish(target, output)
    print(
        json.dumps(
            {
                k: kkt[k]
                for k in (
                    "objective",
                    "primal_after",
                    "normalization",
                    "storage",
                    "fixed_box_multipliers",
                )
            }
        )
    )
    print("physical_audit", audit["passed"], "sha256", d.sha(target))


if __name__ == "__main__":
    main()
