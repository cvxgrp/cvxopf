"""Matched annual DC pair, sharing Stage B execution and independent audit.

No AC, retries, resume, or economic sensitivity arms. Review and commit before
launch. Numerical execution requires explicit resource-budget acknowledgement.
"""

import argparse
from dataclasses import asdict
from pathlib import Path

from . import run_stage_b as execution
from .prepare import HERE
from .stage_b import Arm, SOLVER_OPTIONS, TOLERANCES

OUTPUT = HERE / "results/stage_c"
# Owner-approved ceilings; not runtime or memory forecasts.
LIMITS = dict(wall_seconds=14400.0, rss_mib=16384.0, poll_seconds=1.0)


def study_spec():
    return dict(
        schema_version=1,
        arms=[
            asdict(Arm("Full year", 0, 8760, 1 / 3, 0.01, formulation))
            for formulation in ("singlenode_dc", "lossy_dc")
        ],
        solver="CLARABEL",
        backend="SCIPY",
        solver_options=dict(SOLVER_OPTIONS),
        tolerances=dict(TOLERANCES),
        limits=dict(LIMITS),
        annual_execution=True,
        ac_execution=False,
    )


STUDY = execution.Study(
    study_spec, "STAGE_C_PROTOCOL.md", "experiments.case118_tracy_2021.run_stage_c"
)


def run(directory: Path, commit: str, *, approve_resource_budget: bool = False):
    if not approve_resource_budget:
        raise ValueError("explicit approval of the Stage C resource budget is required")
    return execution.run(directory, commit, study=STUDY)


def analyze(directory: Path):
    return execution.analyze(directory, study=STUDY)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--commit", help="full reviewed clean execution commit")
    parser.add_argument("--approve-resource-budget", action="store_true")
    parser.add_argument("--worker", type=int, choices=(0, 1), help=argparse.SUPPRESS)
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()
    directory = args.output.resolve()
    if args.worker is not None:
        raise SystemExit(execution.worker(directory, args.worker, study=STUDY))
    if args.analyze:
        execution.atomic_immutable_json(
            directory / "analysis.json", execution.jsonable(analyze(directory))
        )
    elif args.commit:
        record = run(
            directory, args.commit, approve_resource_budget=args.approve_resource_budget
        )
        raise SystemExit(0 if record["classification"] == "complete" else 1)
    else:
        parser.error(
            "--commit is required to launch; use --analyze for retained results"
        )


if __name__ == "__main__":
    main()
