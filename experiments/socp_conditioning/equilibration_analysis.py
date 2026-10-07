"""Read-only scientific reconstruction of the three equilibration outcomes."""

import gzip
import json
from pathlib import Path

import numpy as np
from scipy import sparse

from experiments.socp_conditioning import optimality_audit as a


def main():
    here = Path(__file__).resolve().parent
    directories = [
        here / "results" / root / ("surplus-" + variant)
        for root, variant in (
            ("equilibration_001", "equil_default"),
            ("equilibration_001", "equil_off"),
            ("equilibration_001_remaining", "equil_narrow"),
        )
    ]
    records = []
    baseline_arrays = None
    for directory in directories:
        completion = json.loads((directory / "completion.json").read_text())
        assert a.sha(directory / "arm.json.gz") == completion["arm_sha256"]
        with gzip.open(directory / "arm.json.gz", "rt") as stream:
            r = json.load(stream)
        assert a.sha(directory / "canonical.npz") == r["canonical_archive"]["sha256"]
        assert r["context"] == r["context_after"]
        supervision = json.loads((directory / "supervision.json").read_text())
        assert supervision["worker_log_sha256"] == a.sha(directory / "worker.log")
        with np.load(directory / "canonical.npz", allow_pickle=False) as archive:
            arrays = {k: archive[k] for k in archive.files}
        if baseline_arrays is None:
            baseline_arrays, baseline = arrays, r
        else:
            assert arrays.keys() == baseline_arrays.keys()
            for k in arrays:
                np.testing.assert_array_equal(arrays[k], baseline_arrays[k])
            assert r["input_sha256"] == baseline["input_sha256"]
            assert r["historical_reference"] == baseline["historical_reference"]
            assert (
                r["canonical_archive"]["cone_dimensions"]
                == baseline["canonical_archive"]["cone_dimensions"]
            )
            for path, expected in baseline["context"]["sources"].items():
                if path != "experiments/socp_conditioning/diagnostic.py":
                    assert r["context"]["sources"][path] == expected, path
        item = dict(
            variant=r["variant"],
            native_info=r["native_info"],
            status=r["native_solution"]["status"],
            accepted=r["accepted"],
            physical_audit=None if r["audit"] is None else r["audit"]["passed"],
            timings=r["timings"],
            supervision=supervision,
            arm_sha256=completion["arm_sha256"],
        )
        if r["audit"] is not None:
            item["optimality"] = a.analyze(directory)
            item["named_costs"] = r["named_costs"]
        else:
            # A DualInfeasible result is a candidate recession ray, NOT a primal.
            x, s = [np.asarray(r["native_solution"][k]) for k in ("x", "s")]
            A, P = [
                sparse.csc_matrix(
                    (
                        arrays[k + "_data"],
                        arrays[k + "_indices"],
                        arrays[k + "_indptr"],
                    ),
                    shape=arrays[k + "_shape"],
                )
                for k in ("A", "P")
            ]
            layout = a.cone_layout(
                r["canonical_archive"]["cone_dimensions"], A.shape[0]
            )
            item["candidate_ray"] = dict(
                c_dot_x=float(arrays["c"] @ x),
                P_x_inf=a.inf(P @ x),
                A_x_plus_s_inf=a.inf(A @ x + s),
                negative_A_x_cone=a.cone_errors(-A @ x, layout),
                s_cone=a.cone_errors(s, layout),
                x_norm=float(np.linalg.norm(x)),
                certified=False,
            )
        records.append(item)
    output = dict(
        schema=1,
        no_new_solves=True,
        identical_canonical_arrays=True,
        source_sha256=a.sha(Path(__file__)),
        audit_source_sha256=a.sha(Path(a.__file__)),
        arms=records,
    )
    target = here / "results/equilibration_analysis_001.json"
    with target.open("x") as stream:
        json.dump(output, stream, indent=2, allow_nan=False)
    for item in records:
        print(item["variant"], item["status"], "audit", item["physical_audit"])
        if "optimality" in item:
            print(
                {
                    k: item["optimality"][k]
                    for k in ("objective", "normalization", "storage")
                }
            )
        else:
            print(item["candidate_ray"])
    print("analysis_sha256", a.sha(target))


if __name__ == "__main__":
    main()
