"""Serial three-solver surplus ablation: fixed loads, no shedding variables/cost."""

from __future__ import annotations

import argparse
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
import signal
from unittest.mock import patch

import numpy as np

from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import copt_check, mosek_check


SOLVERS = ("clarabel", "mosek", "copt")
BASE_CONTEXT = d.context
LOAD_CASE = d.load_case
BUILD_VARIANT = d.build_variant
ORIGINAL_AUDIT = d.original_audit


def fixed_inputs(kwargs):
    """Remove the optional feature, not merely fix its variable to zero."""
    return kwargs | {
        "loads": [replace(u, shedding_cost_per_mwh=None) for u in kwargs["loads"]]
    }


def context():
    return dict(
        **BASE_CONTEXT(),
        ablation="all_loads_fixed_no_shedding_variables_or_penalty",
        runners={
            str(Path(p).relative_to(d.ROOT)): d.sha(p)
            for p in (
                __file__,
                mosek_check.__file__,
                copt_check.__file__,
            )
        },
        extra_packages={k: version(k) for k in ("mosek", "coptpy")},
    )


def load_case(name, reference):
    kwargs, historical = LOAD_CASE(name, reference)
    original_digest = d.input_digest(kwargs)
    fixed = fixed_inputs(kwargs)
    return fixed, dict(
        original_reference=historical,
        with_shedding_input_sha256=original_digest,
        fixed_load_input_sha256=d.input_digest(fixed),
        intervention="shedding_cost_per_mwh=None for every load; all else unchanged",
    )


def build_variant(kwargs, variant):
    build, original, divisor = BUILD_VARIANT(kwargs, variant)
    if any("shed" in k for k in build.variables):
        raise ValueError("fixed-load model unexpectedly contains shedding variables")
    if "load_shedding_cost" in build.expressions:
        raise ValueError("fixed-load model unexpectedly contains shedding cost")
    return build, original, divisor


def zero_shedding_projection(result, T, n):
    """Adapt only absent fixed-load fields to the historical all-sheddable audit.

    Raw public results remain untouched. Actual served P/Q are never replaced;
    the inherited audit must establish that they equal exogenous demand.
    """
    zeros = {
        "p_load_shed": np.zeros((T, n)),
        "q_load_shed": np.zeros((T, n)),
        "load_shed_fraction": np.zeros((T, n)),
        "p_load_shed_total": np.zeros(T),
        "energy_not_served_by_load": np.zeros(n),
        "energy_not_served": 0.0,
        "load_shedding_cost": 0.0,
    }
    if set(zeros) & result.keys():
        raise ValueError("cannot project a result containing shedding outputs")
    return result | zeros


def audit(case_name, build, result, kwargs, named):
    if any(u.shedding_cost_per_mwh is not None for u in kwargs["loads"]):
        raise ValueError("fixed-load audit requires no shedding policy")
    projected = zero_shedding_projection(result, kwargs["T"], len(kwargs["loads"]))
    # The historical audit assumes numeric shedding costs. A positive dummy
    # coefficient multiplies exact zero only inside the audit, never the model.
    audit_kwargs = kwargs | {
        "loads": [replace(u, shedding_cost_per_mwh=1.0) for u in kwargs["loads"]]
    }
    checked = ORIGINAL_AUDIT(
        case_name, build, projected, audit_kwargs, named | {"load_shedding_cost": 0.0}
    )
    checked["fixed_load_projection"] = (
        "absent shedding/ENS/cost fields are exact zero; served P/Q unchanged"
    )
    return checked


def worker(directory, reference, solver):
    with (
        patch.object(d, "context", context),
        patch.object(d, "load_case", load_case),
        patch.object(d, "build_variant", build_variant),
        patch.object(d, "original_audit", audit),
    ):
        if solver == "clarabel":
            d.worker(directory, "surplus", "cones", reference)
        elif solver == "mosek":
            mosek_check.worker(directory, reference)
        else:
            copt_check.worker(directory, reference)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--solver", choices=SOLVERS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    output, reference = args.output.resolve(), args.reference.resolve()
    if args.worker:
        if args.solver is None:
            parser.error("worker requires solver")
        worker(output, reference, args.solver)
    else:
        output.mkdir(parents=True, exist_ok=False)
        d.publish(output / "binding.json", context())
        for solver in SOLVERS:
            mosek_check.supervise(
                output / solver,
                reference,
                worker_module=__spec__.name,
                context_factory=context,
                worker_arguments=("--solver", solver),
            )
            import json

            supervision = json.loads((output / solver / "supervision.json").read_text())
            if supervision["classification"] != "completed":
                raise RuntimeError("stop after process/resource failure")


if __name__ == "__main__":
    main()
