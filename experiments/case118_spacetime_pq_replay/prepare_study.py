"""Verify the fresh study worker at native entry without running IPOPT."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

import cvxpy as cp
import cyipopt
import numpy as np

from experiments.case118_vectorization_replay import worker
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json
from experiments.retained_paths import retained_operation
from . import diagnose_primary as diagnostic
from .study import CONFIGURATIONS, source_paths

REFERENCE = Path(__file__).parent / "results/case118_6047_four_way_dense_control"


@retained_operation()
def prepare(folder, mode):
    folder.mkdir(parents=True, exist_ok=False)
    request = diagnostic.read(diagnostic.REQUEST)
    request["execution_sources"] = {str(p): diagnostic.sha(p) for p in source_paths()}
    request["execution_configuration"] = CONFIGURATIONS[mode]
    atomic_immutable_json(folder / "request.json", request)
    expected = diagnostic.read(REFERENCE / "run" / mode / "start.json")

    class Complete(BaseException):
        pass

    class NativeBoundary:
        def __init__(self, **kwargs):
            self.kwargs, self.options = kwargs, {}
            for key in ("lb", "ub", "cl", "cu"):
                np.testing.assert_array_equal(kwargs[key], np.load(REFERENCE / "preparation" / mode / f"{key}.npy"))

        def add_option(self, key, value):
            self.options[key] = value

        def solve(self, x0):
            assert cp.settings.SPARSE_DENSITY_THRESHOLD == 0.0
            np.testing.assert_array_equal(x0, expected["complete_x0"])
            actual = diagnostic.read(folder / "start.json")
            for key in ("raw_start", "assigned_start", "layout_signature"):
                assert actual[key] == expected[key], key
            oracle = self.kwargs["problem_obj"]
            entry = dict(options=self.options, n=self.kwargs["n"], m=self.kwargs["m"],
                         jacobian_entries=len(oracle.jacobianstructure()[0]),
                         hessian_entries=len(oracle.hessianstructure()[0]),
                         density_threshold=cp.settings.SPARSE_DENSITY_THRESHOLD,
                         exact_previous_full_x0=True)
            assert entry == diagnostic.read(REFERENCE / "preparation" / mode / "native_entry.json")
            atomic_immutable_json(folder / "native_entry.json", entry)
            raise Complete()

    cyipopt.Problem = NativeBoundary
    try:
        worker.execute(folder)
    except Complete:
        for path, digest in request["execution_sources"].items():
            assert diagnostic.sha(path) == digest
        atomic_immutable_json(folder / "verified.json", dict(
            condition=mode, native_optimization_run=False, full_start_exact=True,
            bounds_options_structure_exact=True, configuration=CONFIGURATIONS[mode],
        ))
    else:
        raise RuntimeError("Mocked native boundary not reached")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--worker", choices=CONFIGURATIONS)
    args = parser.parse_args()
    if args.worker:
        prepare(args.output, args.worker)
        return
    args.output.mkdir(parents=True, exist_ok=False)
    for mode in CONFIGURATIONS:
        with (args.output / f"{mode}.log").open("x") as log:
            subprocess.run([sys.executable, "-m", __spec__.name, "--worker", mode,
                            "--output", str(args.output / mode)], stdout=log,
                           stderr=subprocess.STDOUT, check=True)
        print(json.dumps(diagnostic.read(args.output / mode / "verified.json")), flush=True)


if __name__ == "__main__":
    main()
