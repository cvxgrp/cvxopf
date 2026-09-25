"""Prepare or launch four fresh frozen-sample replays under one execution policy."""

import argparse
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import subprocess

from experiments.case118_vectorization_replay import run as replay
from experiments.case118_vectorization_replay.sample import ROOT, read, sha
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json
from experiments.retained_paths import retained_operation
from . import run as original
from .four_way_dense_control import libraries
from .telemetry import TemperatureCollector

OUTPUT = Path(__file__).parent / "results/case118_four_way_120plus6_dense_control"
CONFIGURATIONS = {
    "none": dict(temporal_assembly="stepwise", vectorize_pq=False, sparse_density_threshold=0.0),
    "time_only": dict(temporal_assembly="vectorized", vectorize_pq=False, sparse_density_threshold=0.0),
    "spatial_only": dict(temporal_assembly="stepwise", vectorize_pq=True, sparse_density_threshold=0.0),
    "both": dict(temporal_assembly="vectorized", vectorize_pq=True, sparse_density_threshold=0.0),
}
THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS")


def runtime():
    return dict(packages={d.metadata["Name"]: d.version for d in distributions()},
                native_libraries=libraries(),
                thread_environment={k: os.environ.get(k) for k in THREAD_KEYS})


def source_paths():
    paths = set()
    for base in (ROOT / "src", ROOT / "experiments/case118_annual_hierarchy",
                 ROOT / "experiments/case118_vectorization_replay",
                 Path(__file__).parent):
        paths.update(p for p in base.rglob("*.py") if "results" not in p.parts)
    paths.update(ROOT / p for p in ("pyproject.toml", "uv.lock", "experiments/retained_paths.py"))
    paths.add(Path(__file__).with_name("FOUR_WAY_STUDY.md"))
    return sorted(paths)


@retained_operation()
def preflight(output=OUTPUT, *, commit=None, require_clean=False):
    output = Path(output).resolve()
    expected_parent = (Path(__file__).parent / "results").resolve()
    if output.parent != expected_parent:
        raise ValueError("Study root must be a fresh direct child of this experiment's results")
    binding = original.preflight(commit=commit, require_clean=require_clean, output=output)
    binding.update(conditions=CONFIGURATIONS, expected_window_evaluations=504,
                   runtime=runtime(), sources={str(p): sha(p) for p in source_paths()})
    return binding


def verify(binding):
    if runtime() != binding["runtime"]:
        raise ValueError("Study environment changed")
    for path, expected in binding["sources"].items():
        if sha(path) != expected:
            raise ValueError(f"Study source changed: {path}")
    if original.git("rev-parse", "HEAD") != binding["commit"]:
        raise ValueError("Study commit changed")


@retained_operation()
def launch(commit, *, fan_on, output=OUTPUT):
    if not fan_on:
        raise ValueError("Confirm the external fan is on before launch")
    binding = preflight(output, commit=commit, require_clean=True)
    processes = subprocess.check_output(["ps", "-axo", "pid,ppid,etime,%cpu,command"], text=True)
    if any(marker in processes for marker in (
        "case118_vectorization_replay.worker", "s5_speculative_worker",
        "four_way_dense_control --worker", "solve_historical.py --arm")):
        raise ValueError("Another experiment worker is active")
    output = Path(binding["output"])
    output.mkdir()
    binding["external_fan_confirmed_on"] = True
    atomic_immutable_json(output / "binding.json", binding)
    (output / "processes-before.txt").write_text(processes)
    completed = []
    for mode, configuration in CONFIGURATIONS.items():
        verify(binding)
        folder = output / mode
        folder.mkdir()
        (folder / "sample.json").write_bytes(Path(binding["sample"]["path"]).read_bytes())
        if sha(folder / "sample.json") != original.SAMPLE_SHA256:
            raise ValueError("Sample changed after preflight")
        atomic_immutable_json(folder / "binding.json", dict(
            **binding, condition=mode, execution_configuration=configuration,
        ))
        print(f"Starting {mode}: 126 frozen windows", flush=True)
        with TemperatureCollector(folder / "temperature_telemetry") as collector:
            if not collector.first_sample.is_set():
                raise RuntimeError("No thermal sample before solve admission")
            replay.run(folder, allowed_version_changes=original.VERSION_CHANGES,
                       provenance=binding, additional_sources=source_paths(),
                       execution_configuration=configuration)
        verify(binding)
        if read(folder / "run/finished.json")["completed"] != 126:
            raise RuntimeError(f"Incomplete condition: {mode}; study stopped")
        completed.append(mode)
        atomic_immutable_json(output / f"{mode}-complete.json", dict(condition=mode, windows=126))
    atomic_immutable_json(output / "finished.json", dict(conditions=completed, windows=504))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--commit")
    parser.add_argument("--fan-on", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.preflight:
        print(json.dumps(preflight(args.output, commit=args.commit), indent=2))
    elif args.commit:
        launch(args.commit, fan_on=args.fan_on, output=args.output)
    else:
        parser.error("Select --preflight or supply --commit and --fan-on for launch")


if __name__ == "__main__":
    main()
