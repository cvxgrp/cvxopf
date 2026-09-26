"""One historical hour-6047 primary with verified preparation and native logging."""

import argparse
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import sys
import time

import prepare_historical as prep


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("a", "b"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    binding = prep.read(args.binding)

    def check_binding():
        for path, expected_hash in binding["files"].items():
            if prep.digest(Path(path)) != expected_hash:
                raise ValueError(f"Launch-bound file changed: {path}")

    check_binding()
    if Path(sys.prefix).resolve() != (prep.BASE / f"env-{args.arm}").resolve():
        raise ValueError("Wrong environment")
    output = args.output.resolve()
    if not output.is_relative_to(prep.BASE) or output.is_relative_to(prep.SOURCE):
        raise ValueError("Output outside reproduction results")
    output.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(prep.SOURCE / "src"), str(prep.SOURCE)]
    import numpy as np
    from experiments.case118_vectorization_replay import worker
    from experiments.case118_annual_hierarchy import run_s4b, streaming_runner
    from experiments.case118_annual_hierarchy.s5_speculative_archive import audit_candidate
    import cyipopt
    from cyipopt import ipopt_wrapper

    reference_dir = prep.BASE / binding["preparations"][args.arm]
    reference = prep.read(reference_dir / "preparation.json")
    if {d.metadata["Name"]: d.version for d in distributions()} != reference["packages"]:
        raise ValueError("Installed dependencies changed after preparation")
    if prep.native_libraries(Path(ipopt_wrapper.__file__)) != reference["native_libraries"]:
        raise ValueError("Native libraries changed after preparation")
    if {k: os.environ.get(k) for k in reference["thread_environment"]} != reference["thread_environment"]:
        raise ValueError("Thread environment changed")
    request = prep.read(prep.PRIMARY / "request.json")
    expected = prep.read(prep.PRIMARY / "start.json")
    prep.check_sources(request)
    run_s4b.S4_OUTER_ARCHIVE_PATH = (
        prep.ROOT / run_s4b.S4_OUTER_ARCHIVE_PATH.relative_to(prep.SOURCE)
    )
    if prep.digest(run_s4b.S4_OUTER_ARCHIVE_PATH) != reference["adaptations"]["outer_archive"]["sha256"]:
        raise ValueError("Outer archive changed")
    references = request["selected"]["references"]
    for item in [*references.values(), request["selected"]["archive"]]:
        if prep.digest(Path(item["path"])) != item["sha256"]:
            raise ValueError("Retained primary evidence changed")
    events = []

    def phase(name, iteration=6047, ordinal=0):
        if (iteration, ordinal) != (6047, 0):
            raise ValueError("Unexpected attempt")
        events.append(dict(phase=name, monotonic_seconds=time.monotonic()))
        worker.atomic_json(output / "phase.json", dict(events=events))

    fixture = worker.load_s4_fixture()
    if (fixture.policy_sha256 != reference["policy_sha256"]
            or fixture.solve_config_sha256 != reference["solve_config_sha256"]
            or streaming_runner.execution_input_sha256(fixture.inputs) != reference["input_fingerprint"]):
        raise ValueError("Frozen scientific inputs changed")
    outer = run_s4b._outer()
    phase("before_ac_build")
    prepared = worker.prepare(output, fixture, outer, request)
    phase("after_ac_build")
    if prepared.request_sha256 != reference["request_sha256"]:
        raise ValueError("Request changed")
    for actual, historical in [(prepared.raw, expected["raw_start"]),
                               (prepared.assigned, expected["assigned_start"])]:
        if set(actual) != set(historical):
            raise ValueError("Named coordinate inventory changed")
        for name, value in actual.items():
            np.testing.assert_array_equal(value, historical[name])
    for name, module in sys.modules.copy().items():
        if name == "cvxopf" or name.startswith(("cvxopf.", "experiments.")):
            path = getattr(module, "__file__", None)
            if path and not Path(path).resolve().is_relative_to(prep.SOURCE):
                raise ValueError(f"Import escaped snapshot: {name}")

    native_class = cyipopt.Problem
    entered = False

    class DryRunComplete(BaseException):
        pass

    class VerifiedNativeProblem:
        def __init__(self, **kwargs):
            self.kwargs, self.options = kwargs, {}
            if dict(n=kwargs["n"], m=kwargs["m"]) != reference["native_dimensions"]:
                raise ValueError("Native dimensions changed")
            for name in ("lb", "ub", "cl", "cu"):
                np.testing.assert_array_equal(kwargs[name], np.load(reference_dir / f"{name}.npy"))

        def add_option(self, name, value):
            self.options[name] = value

        def solve(self, x0):
            nonlocal entered
            if entered:
                raise ValueError("Only one native solve permitted")
            entered = True
            np.testing.assert_array_equal(x0, expected["complete_x0"])
            start = prep.read(output / "start.json")
            if start["layout_signature"] != reference["layout_signature"]:
                raise ValueError("Layout changed")
            if self.options != reference["native_options"]:
                raise ValueError("Native numerical options changed")
            check_binding()
            prep.check_sources(request)
            worker.atomic_immutable_json(output / "native_entry.json", dict(
                original_options=self.options, logging_override={"print_level": 5},
                exact_historical_start=True, exact_prepared_bounds=True,
                dry_run=args.dry_run,
            ))
            if args.dry_run:
                raise DryRunComplete()
            problem = native_class(**self.kwargs)
            for name, value in {**self.options, "print_level": 5}.items():
                problem.add_option(name, value)
            phase("before_native_solve")
            result = problem.solve(x0)
            phase("after_native_solve")
            native_info = {key: value.decode("utf-8", errors="replace")
                           if isinstance(value, bytes) else value
                           for key, value in result[1].items()}
            worker.atomic_immutable_json(output / "native_result.json", worker._json_value(native_info))
            return result

    cyipopt.Problem = VerifiedNativeProblem
    try:
        worker.execute_prepared_attempt(
            prepared, fixture.inputs, fixture.policy, fixture.solve_config, outer,
            start_path=output / "start.json", result_path=output / "result.json",
            phase_observer=phase,
        )
    except DryRunComplete:
        if not args.dry_run:
            raise
        summary = dict(dry_run=True, native_entry_verified=True, native_optimization_run=False)
    else:
        if args.dry_run or not entered:
            raise RuntimeError("Native-entry validation did not complete")
        candidate = audit_candidate(
            output, prepared.invocation, inputs=fixture.inputs, policy=fixture.policy,
            outer=outer, initial=prepared.initial, stop=prepared.stop,
            expected_request_sha256=prepared.request_sha256,
        )
        summary = dict(dry_run=False, accepted=candidate.completion.outcome == "accepted",
                       native_optimization_run=True,
                       audit=candidate.payload["attempt"]["audit"])
    prep.check_sources(request)
    check_binding()
    worker.atomic_immutable_json(output / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
