"""Post-hoc test of uniform quadratic generator-cost conditioning."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping, cast
from unittest.mock import patch

import cvxpy as cp
from cvxpy.reductions.solvers.conic_solvers.clarabel_conif import CLARABEL
import numpy as np

from cvxopf import extract_results
from cvxopf.generator import gen_cost_expr, generator_gencost
from experiments.case118_annual_hierarchy import streaming_runner
from experiments.case118_annual_hierarchy.audit import audit_probe
from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.streaming_runner import execution_input_sha256
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    atomic_json,
    sha256_path,
)
from experiments.m14_time_vectorization.m14c_prefix_fixture import load_prefix_fixture
from experiments.m14_time_vectorization.m14c_tight_tolerance_diagnostic import (
    TIGHT_CLARABEL_OPTIONS,
    _capture_clarabel_solution,
    _json_value,
    _maximum_difference,
    _solver_statistics,
    full_bounds_audit,
)


OUTPUT = Path(
    os.environ.get("CVXOPF_CONDITION_OUTPUT", str(Path(__file__).resolve().parent))
).resolve()
SCRIPT = Path(__file__).resolve()
HORIZONS = (24, 168)
REPRESENTATIONS = (("stepwise", "CPP"), ("vectorized", "SCIPY"))
GENERATOR_QUADRATIC_COEFFICIENT = float(
    os.environ.get("CVXOPF_GENERATOR_C2", "1e-4")
)
CONDITION_SCOPE = os.environ.get("CVXOPF_CONDITION_SCOPE", "all")
BR_R = 2


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _conditioned(horizon: int) -> tuple[Any, tuple[Any, ...]]:
    fixture = load_prefix_fixture(horizon)
    generators = tuple(
        replace(
            unit,
            cost_coeffs=(
                float(unit.cost_coeffs[0]),
                float(unit.cost_coeffs[1]),
                GENERATOR_QUADRATIC_COEFFICIENT,
            ),
        )
        if CONDITION_SCOPE == "all" or unit.bus == 69
        else replace(unit)
        for unit in fixture.inputs.generators
    )
    return fixture, generators


def _accounting(
    fixture: Any, generators: tuple[Any, ...], result: Mapping[str, object]
) -> Mapping[str, object]:
    pg = np.asarray(result["Pg"], dtype=float)
    gencost = generator_gencost(list(generators))
    generation = fixture.inputs.delta * sum(
        float(gen_cost_expr(gencost, cp.Constant(row)).value) for row in pg
    )
    resistance = np.asarray(fixture.inputs.case["branch"], dtype=float)[:, BR_R]
    flow = np.asarray(result["p_flows"], dtype=float) / float(
        fixture.inputs.case["baseMVA"]
    )
    loss = (
        fixture.inputs.delta
        * fixture.inputs.options.loss_weight
        * float(np.sum(resistance * np.square(flow)))
    )
    storage = float(cast(float, result.get("storage_cost", 0.0)))
    objective = float(cast(float, result["objective"]))
    components = {
        "generation_cost": generation,
        "dc_loss_cost": loss,
        "storage_cost": storage,
    }
    return {
        "objective": objective,
        "components": components,
        "accounting_residual_abs": abs(objective - sum(components.values())),
    }


def _worker(horizon: int, assembly: str, directory: Path) -> int:
    fixture, generators = _conditioned(horizon)
    inputs = replace(fixture.inputs, generators=generators)
    storage = tuple(replace(unit) for unit in inputs.storage)
    started = time.perf_counter()
    build = streaming_runner.build_window(
        inputs,
        "lossy_dc",
        0,
        horizon,
        storage,
        temporal_assembly=cast(Any, assembly),
    )
    captured: dict[str, object] = {}
    _, wrapper = _capture_clarabel_solution(captured)
    exception = None
    with patch.object(CLARABEL, "solve_via_data", wrapper):
        try:
            build.solve(solver="CLARABEL", nlp=False, **TIGHT_CLARABEL_OPTIONS)
        except Exception as exc:
            exception = f"{type(exc).__name__}: {exc}"
    result = extract_results(build)
    audit = audit_probe(
        inputs.case,
        build,
        result,
        generators=generators,
        loads=inputs.loads,
        nondispatchable=inputs.nondispatchable,
        storage=storage,
        delta=inputs.delta,
        branch_limit_sentinel=inputs.options.branch_limit_sentinel,
        tolerances=fixture.annual.policy.tolerances,
    )
    bounds = full_bounds_audit(build, result)
    accounting = _accounting(fixture, generators, result)
    accepted = (
        exception is None
        and result.get("status") == cp.OPTIMAL
        and captured.get("status") == "Solved"
        and audit.accepted_primal
        and bounds["accepted"] is True
        and accounting["accounting_residual_abs"] <= 1e-5
    )
    payload = {
        "classification": "accepted" if accepted else "rejected",
        "exception": exception,
        "commit": _git("rev-parse", "HEAD"),
        "clean": _git("status", "--porcelain") == "",
        "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
        "horizon_steps": horizon,
        "temporal_assembly": assembly,
        "canonicalization_backend": dict(REPRESENTATIONS)[assembly],
        "conditioning": {
            "scope": CONDITION_SCOPE,
            "quadratic_coefficient": GENERATOR_QUADRATIC_COEFFICIENT,
            "generator_count": len(generators),
            "input_sha256": execution_input_sha256(inputs),
        },
        "wall_seconds": time.perf_counter() - started,
        "solver_statistics": _solver_statistics(build, captured),
        "audit": {
            "accepted_primal": audit.accepted_primal,
            "status": audit.status,
            "residuals": dict(audit.residuals),
            "missing_or_nonfinite_fields": list(audit.missing_or_nonfinite_fields),
            "identity_error": audit.identity_error,
        },
        "bounds_audit": bounds,
        "objective_accounting": accounting,
        "result": result,
    }
    atomic_immutable_json(directory / "arm-result.json", _json_value(payload))
    return 0 if accepted else 1


def _compare(left: Mapping[str, object], right: Mapping[str, object]) -> object:
    la = cast(Mapping[str, object], left["objective_accounting"])
    ra = cast(Mapping[str, object], right["objective_accounting"])
    lc = cast(Mapping[str, object], la["components"])
    rc = cast(Mapping[str, object], ra["components"])
    lr = cast(Mapping[str, object], left["result"])
    rr = cast(Mapping[str, object], right["result"])
    return {
        "objective_absolute_difference": abs(float(la["objective"]) - float(ra["objective"])),
        "objective_relative_difference": abs(float(la["objective"]) - float(ra["objective"]))
        / max(1.0, abs(float(ra["objective"]))),
        "component_absolute_differences": {
            name: abs(float(lc[name]) - float(rc[name]))
            for name in sorted(lc.keys() & rc.keys())
        },
        "coordinate_maximum_absolute_differences": {
            name: _maximum_difference(lr[name], rr[name])
            for name in ("Pg", "b", "soc", "p_net", "p_flows")
        },
        "stepwise_clarabel": cast(Mapping[str, object], left["solver_statistics"])["clarabel"],
        "vectorized_clarabel": cast(Mapping[str, object], right["solver_statistics"])["clarabel"],
    }


def _parent() -> int:
    if _git("status", "--porcelain") != "":
        raise ValueError("conditioning diagnostic requires a clean tracked tree")
    records: list[Mapping[str, object]] = []
    payloads: dict[tuple[int, str], Mapping[str, object]] = {}
    for horizon in HORIZONS:
        for assembly, backend in REPRESENTATIONS:
            arm = OUTPUT / f"{horizon:04d}-{assembly}"
            arm.mkdir()
            log = arm / "worker.log"
            command = [
                sys.executable,
                str(SCRIPT),
                "--worker",
                "--horizon",
                str(horizon),
                "--assembly",
                assembly,
                "--directory",
                str(arm),
            ]
            with log.open("wb") as stream:
                completed = subprocess.run(
                    command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT
                )
            result_path = arm / "arm-result.json"
            payload = (
                cast(Mapping[str, object], json.loads(result_path.read_text()))
                if result_path.is_file()
                else None
            )
            accepted = (
                completed.returncode == 0
                and payload is not None
                and payload.get("classification") == "accepted"
            )
            records.append(
                {
                    "horizon_steps": horizon,
                    "temporal_assembly": assembly,
                    "canonicalization_backend": backend,
                    "classification": "accepted" if accepted else "failed",
                    "returncode": completed.returncode,
                    "arm_result_sha256": sha256_path(result_path) if payload else None,
                    "worker_log_sha256": sha256_path(log),
                }
            )
            atomic_json(OUTPUT / "progress.json", {"records": records})
            if not accepted or payload is None:
                atomic_immutable_json(
                    OUTPUT / "conditioning-result.json",
                    {"classification": "stopped", "records": records},
                )
                return 1
            payloads[(horizon, assembly)] = payload
    comparisons = [
        {
            "horizon_steps": horizon,
            **cast(
                Mapping[str, object],
                _compare(
                    payloads[(horizon, "stepwise")],
                    payloads[(horizon, "vectorized")],
                ),
            ),
        }
        for horizon in HORIZONS
    ]
    atomic_immutable_json(
        OUTPUT / "conditioning-result.json",
        _json_value(
            {
                "classification": "accepted",
                "execution_complete": True,
                "commit": _git("rev-parse", "HEAD"),
                "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
                "conditioning": {
                    "scope": CONDITION_SCOPE,
                    "quadratic_coefficient": GENERATOR_QUADRATIC_COEFFICIENT,
                },
                "clarabel_options": dict(TIGHT_CLARABEL_OPTIONS),
                "records": records,
                "comparisons": comparisons,
                "diagnostic_only": True,
                "annual_execution_authorized": False,
            }
        ),
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--horizon", type=int)
    parser.add_argument("--assembly", choices=dict(REPRESENTATIONS))
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.worker:
        if args.horizon is None or args.assembly is None or args.directory is None:
            parser.error("worker requires horizon, assembly, and directory")
        raise SystemExit(_worker(args.horizon, args.assembly, args.directory.resolve()))
    raise SystemExit(_parent())


if __name__ == "__main__":
    main()
