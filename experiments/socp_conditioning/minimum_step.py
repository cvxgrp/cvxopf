"""One predeclared CLARABEL small-step ablation, not a tuning sweep.

Change only min_terminate_step_length from the retained default 1e-4 to 1e-8.
Keep normalized cones, exact substitution, joint scaling, full and reduced
termination tolerances, 5000 iterations and one thread unchanged. One fresh
worker, 180 seconds / 4096 MiB; no retry. Retain callback and rejected-iterate
evidence using the existing native-termination runner. This experiment neither
relaxes the physical/optimality acceptance gate nor changes production sources.
"""

import gzip
import json
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np

from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import termination_probe as t
from experiments.socp_conditioning import termination_analysis as a

BASE_OPTIONS, BASE_CONTEXT = t.options, t.context
BASELINE = d.HERE / "results/termination_probe_001/clarabel"
BASELINE_SHA = "ae37e20f1f97a43eee0ebaa62ee63e5e551673976221016c0074a428aa412677"
MINIMUM_STEP = 1e-8


def options(solver):
    if solver != "CLARABEL":
        raise ValueError("this one-arm ablation is CLARABEL only")
    return BASE_OPTIONS(solver) | dict(min_terminate_step_length=MINIMUM_STEP)


def context():
    if d.sha(BASELINE / "arm.json.gz") != BASELINE_SHA:
        raise ValueError("baseline changed")
    old = json.load(gzip.open(BASELINE / "arm.json.gz", "rt"))
    base = BASE_CONTEXT()
    # Every original execution dependency must still match. New wrapper and
    # analysis code are bound separately, without rewriting prior provenance.
    for key in ("sources", "additional_sources"):
        for name, digest in old["context"][key].items():
            if d.sha(d.ROOT / name) != digest:
                raise ValueError(f"baseline source changed: {name}")
    return base | dict(
        experiment="clarabel_single_minimum_step_ablation",
        baseline_arm_sha256=BASELINE_SHA,
        intervention=dict(
            parameter="min_terminate_step_length", before=1e-4, after=MINIMUM_STEP
        ),
        additional_sources=base["additional_sources"]
        | {str(Path(p).relative_to(d.ROOT)): d.sha(p) for p in (__file__, a.__file__)},
    )


def compare(root):
    """Verify the matched artifacts and report outcome without optimizing."""
    records = a.verify_root(root)
    new = records["CLARABEL"]
    old = json.load(gzip.open(BASELINE / "arm.json.gz", "rt"))
    if new["common_problem"] != old["common_problem"]:
        raise ValueError("common problem changed")
    if new["input_sha256"] != old["input_sha256"]:
        raise ValueError("physical inputs changed")
    expected = old["solver_options"] | dict(min_terminate_step_length=MINIMUM_STEP)
    if new["solver_options"] != expected:
        raise ValueError("more than the declared option changed")
    raw0, raw1 = old["raw_clarabel"], new["raw_clarabel"]
    keys = set(old["trace"][0]) - {"solve_time"}
    prefix_matches = len(new["trace"]) >= len(old["trace"]) and all(
        all(before[k] == after[k] for k in keys)
        for before, after in zip(old["trace"], new["trace"])
    )
    summary = dict(
        analysis_source_sha256=d.sha(__file__),
        baseline_arm_sha256=BASELINE_SHA,
        arm_sha256=d.sha(root / "clarabel/arm.json.gz"),
        root_summary_sha256=d.sha(root / "summary.json"),
        common_problem_exact=True,
        only_declared_solver_option_changed=True,
        previous_callback_prefix_exact_except_time=prefix_matches,
        final_vectors_exact={
            k: np.array_equal(raw0[k], raw1[k]) for k in ("x", "s", "z")
        },
        previous_native=old["native"],
        new_native=new["native"],
        previous_physical_objective=old["result"]["objective"],
        new_physical_objective=new["result"]["objective"],
        solver_exception=new["solver_exception"],
        accepted=new["accepted"],
        numerical_checks=a.numerical_checks(new["audit"]),
        common_kkt=new.get("common_kkt"),
        promotional=False,
        additional_optimizer_calls=0,
    )
    d.publish(root / "comparison.json", d.e3.encode(summary))
    print(json.dumps(d.e3.encode(summary), indent=2))


if __name__ == "__main__":
    with (
        patch.object(t, "options", options),
        patch.object(t, "context", context),
        patch.object(t, "SOLVERS", ("CLARABEL",)),
    ):
        # t.main launches children using its __spec__; provide this wrapper's
        # module identity so they receive exactly the same scoped intervention.
        with patch.object(t, "__spec__", __spec__):
            t.main()
        if "--worker" not in sys.argv:
            compare(Path(sys.argv[sys.argv.index("--output") + 1]).resolve())
