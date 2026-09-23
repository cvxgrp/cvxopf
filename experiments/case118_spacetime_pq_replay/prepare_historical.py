"""Prepare the frozen historical hour-6047 primary; never run native IPOPT.

Run separately with each isolated environment. Historical Python sources stay
unchanged. The only data-path adaptation is the outer archive's snapshot-relative
location; primary references already use retained annual-study absolute paths.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent
BASE = EXPERIMENT / "results/hour6047_environment_reproduction"
SOURCE = BASE / "source"
HISTORICAL = (
    ROOT / "experiments/case118_vectorization_replay/results/"
    "case118_vectorization_replay/run"
)
PRIMARY = HISTORICAL / "s4b-shard-008/ac-006047-spec-00"


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_sources(request):
    for original, expected in request["execution_sources"].items():
        snapshot_path = SOURCE / Path(original).relative_to(ROOT)
        if digest(snapshot_path) != expected:
            raise ValueError(f"Historical source differs: {snapshot_path}")


def native_libraries(extension):
    """Hash the extension and recursively linked non-system absolute dylibs."""
    pending = [extension]
    records = {}
    while pending:
        path = Path(pending.pop()).resolve()
        if str(path) in records:
            continue
        linked = subprocess.check_output(["otool", "-L", str(path)], text=True)
        records[str(path)] = dict(sha256=digest(path), linkage=linked)
        for line in linked.splitlines()[1:]:
            name = line.strip().split(" (", 1)[0]
            if name.startswith("/opt/homebrew/"):
                pending.append(Path(name))
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("a", "b"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if Path(sys.prefix).resolve() != (BASE / f"env-{args.arm}").resolve():
        raise ValueError("Use the selected isolated environment")
    output = args.output.resolve()
    if not output.is_relative_to(BASE) or output.is_relative_to(SOURCE):
        raise ValueError("Output must be a fresh directory within the reproduction root")
    output.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(SOURCE / "src"), str(SOURCE)]

    import numpy as np
    from experiments.case118_vectorization_replay import worker
    from experiments.case118_annual_hierarchy import run_s4b, streaming_runner
    import cyipopt
    from cyipopt import ipopt_wrapper

    request = read(PRIMARY / "request.json")
    if request["replay_start"] is not None or request["target_free_directory"] is not None:
        raise ValueError("Expected the original primary without recovery")
    if request["invocation"] != dict(
        order=0, replay_of=None, source_slot=0,
        window=dict(iteration=6047, shard_id="s4b-shard-008"),
    ):
        raise ValueError("Wrong historical invocation")
    check_sources(request)
    references = request["selected"]["references"]
    for reference in [*references.values(), request["selected"]["archive"]]:
        if digest(Path(reference["path"])) != reference["sha256"]:
            raise ValueError(f"Historical evidence changed: {reference['path']}")

    # The archive is immutable retained data, outside the Git source snapshot.
    archived_path = run_s4b.S4_OUTER_ARCHIVE_PATH
    run_s4b.S4_OUTER_ARCHIVE_PATH = ROOT / archived_path.relative_to(SOURCE)
    adaptations = {
        "outer_archive": dict(
            snapshot_default=str(archived_path),
            retained_path=str(run_s4b.S4_OUTER_ARCHIVE_PATH),
            sha256=digest(run_s4b.S4_OUTER_ARCHIVE_PATH),
        ),
        "source_verification": "Original absolute paths mapped to snapshot-relative files",
    }
    captured = {}

    class PreparationComplete(BaseException):
        """Escape the historical solver's Exception handler without a solve."""

    class MockNativeProblem:
        def __init__(self, **kwargs):
            captured["native_dimensions"] = dict(n=kwargs["n"], m=kwargs["m"])
            captured["native_options"] = {}
            for name in ("lb", "ub", "cl", "cu"):
                np.save(output / f"{name}.npy", kwargs[name])

        def add_option(self, name, value):
            captured["native_options"][name] = value

        def solve(self, x0):
            np.testing.assert_array_equal(x0, expected["complete_x0"])
            captured["mock_solve_entry_reached"] = True
            raise PreparationComplete()

    # Installed before fixture/model preparation: native IPOPT is never created.
    cyipopt.Problem = MockNativeProblem
    expected = read(PRIMARY / "start.json")
    fixture = worker.load_s4_fixture()
    historical_request = read(Path(references["primary_request.json"]["path"]))
    context = historical_request["execution_context"]
    if (fixture.policy_sha256 != context["policy_sha256"]
            or fixture.solve_config_sha256 != context["solve_config_sha256"]):
        raise ValueError("Frozen policy or solver configuration changed")
    outer = run_s4b._outer()
    prepared = worker.prepare(output, fixture, outer, request)
    for name, value in prepared.assigned.items():
        np.testing.assert_array_equal(value, expected["assigned_start"][name])
    if set(prepared.assigned) != set(expected["assigned_start"]):
        raise ValueError("Assigned variable inventory differs")
    for name, value in prepared.raw.items():
        np.testing.assert_array_equal(value, expected["raw_start"][name])
    if set(prepared.raw) != set(expected["raw_start"]):
        raise ValueError("Raw variable inventory differs")
    if prepared.request_sha256 != expected["request_sha256"]:
        raise ValueError("Request differs")

    def observe(evidence):
        np.testing.assert_array_equal(evidence.complete_x0, expected["complete_x0"])
        if evidence.layout_signature != expected["layout_signature"]:
            raise ValueError("Canonical layout differs")
        captured["layout_signature"] = evidence.layout_signature
        captured["model_coordinates"] = evidence.model_coordinate_count
        captured["auxiliary_coordinates"] = evidence.auxiliary_coordinate_count
        np.save(output / "x0.npy", evidence.complete_x0)

    try:
        result = streaming_runner.solve_ac_with_verified_x0(
            prepared.build, fixture.solve_config, start_observer=observe,
        )
    except PreparationComplete:
        pass
    else:
        raise RuntimeError(f"Mock native boundary was not reached: {result}")
    check_sources(request)
    imports = {}
    for name, module in sys.modules.copy().items():
        if name == "cvxopf" or name.startswith(("cvxopf.", "experiments.")):
            path = getattr(module, "__file__", None)
            if path:
                path = Path(path).resolve()
                if not path.is_relative_to(SOURCE):
                    raise ValueError(f"Import escaped historical snapshot: {name}: {path}")
                imports[name] = str(path.relative_to(SOURCE))
    report = dict(
        arm=args.arm, python=platform.python_version(), interpreter=sys.executable,
        packages={d.metadata["Name"]: d.version for d in distributions()},
        snapshot_sources_verified=len(request["execution_sources"]),
        exact_historical_named_starts=True, exact_historical_full_x0=True,
        request_sha256=prepared.request_sha256,
        policy_sha256=fixture.policy_sha256,
        solve_config_sha256=fixture.solve_config_sha256,
        input_fingerprint=streaming_runner.execution_input_sha256(fixture.inputs),
        adaptations=adaptations, imports=imports,
        native_libraries=native_libraries(Path(ipopt_wrapper.__file__)),
        thread_environment={k: os.environ.get(k) for k in (
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
        )},
        harness_sha256=digest(Path(__file__)), native_optimization_run=False,
        **captured,
    )
    (output / "preparation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in (
        "arm", "python", "exact_historical_full_x0", "native_dimensions",
        "native_options", "mock_solve_entry_reached", "native_optimization_run",
    )}, indent=2))


if __name__ == "__main__":
    main()
