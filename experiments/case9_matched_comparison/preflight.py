"""Run, resume, or inspect the four-cell solver-forbidden qualification."""

import argparse
import fcntl
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import cvxpy as cp
import numpy as np

from cvxopf.problem import OPFBuild
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json, sha256_path

from .contract import (
    ANGLES, ANGLE_TOL_DEG, CELLS, MW_TOL, PYPINS, V_TOL, audit_link, audit_loss, capability, close,
    cost_close, encode_case, generator_cost, native_bounds, point_suite, require, selected_links,
    validate_cell,
)
from .cvx_side import CvxModel, make_case

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUTPUTS = HERE / "outputs"
HISTORICAL = REPO / "experiments/dcline_crosseval/results"
ARCHIVES = ("pstar_full.json", "cstar_full.json", "ex12_cplus.txt")
MODULE = "experiments.case9_matched_comparison"
ARTIFACTS = ("request.json", "pypower.stdout", "pypower.stderr")
# Model-building and lift dependencies for the actual Case9 inventory:
# generators, fixed loads and HVDC only. No storage, other formulations, or
# test cases are exercised. Extend this list if the experiment scope changes.
CVX_CASE9_SOURCES = (
    "_ac_start_mapping.py", "_admittance_preparation.py", "_component_adapter.py",
    "_component_adapters.py", "_component_assembly.py", "_cost_coordinates.py",
    "_cvxpy_dispatch.py", "_numerical_preparation.py", "_temporal_assembly.py",
    "ac_problem.py", "cost.py", "data.py", "generator.py", "hvdc.py", "load.py",
    "network.py", "problem.py", "testcases/case9.py", "testcases/case9_dcline.py",
)


def utc():
    return datetime.now(timezone.utc).isoformat()


def load(path):
    with path.open() as stream:
        return json.load(stream)


def rejected(label, phrase, check):
    try:
        check()
    except ValueError as exc:
        require(phrase in str(exc), f"{label} failed for the wrong reason: {exc}")
        return {"expected_rejection": label, "reason": str(exc)}
    raise ValueError(f"{label} was incorrectly admitted")


def compare_point(cvx, pyp, case, point):
    ids, theta = point["bus_ids"], np.asarray(point["angle_rad"])
    voltage = np.asarray(point["voltage_pu"]) * np.exp(1j * theta)
    injection = -(case["bus"][:, 2] + 1j * case["bus"][:, 3])
    for gen, pg, qg in zip(case["gen"], point["pg_mw"], point["qg_mvar"], strict=True):
        injection[ids.index(gen[0])] += pg + 1j * qg
    for link in point["links"]:
        injection[ids.index(link["from_bus"])] += link["net_from_mw"]
        injection[ids.index(link["to_bus"])] += link["net_to_mw"]
    balance = case["baseMVA"] * voltage * (cvx.ybus @ voltage).conj() - injection
    expected_angle = []
    for _, f, t, lower, upper in ANGLES:
        diff = theta[ids.index(f)] - theta[ids.index(t)]
        expected_angle.extend([np.deg2rad(lower) - diff, diff - np.deg2rad(upper)])
    expected_faces = [
        float(normal @ [point["pg_mw"][i], point["qg_mvar"][i]] - rhs)
        for _, i, normal, rhs, _ in capability(case)
    ]
    costs = [generator_cost(row, pg) for row, pg in zip(case["gencost"], point["pg_mw"], strict=True)]
    cvx_values = cvx.evaluate(point)
    for name, rows in (("CVXOPF", cvx_values), ("PYPOWER", pyp)):
        close(rows["p_mw"], balance.real, f"{name} nodal P sign/units")
        close(rows["q_mvar"], balance.imag, f"{name} nodal Q sign/units")
        close(rows["angle_rad"], expected_angle, f"{name} complete angle bounds")
        close(rows["capability"], expected_faces, f"{name} generator face margins")
        cost_close(rows["objective"], sum(costs), f"{name} common objective")
    cost_close(cvx_values["objective"], pyp["objective"], "same-point native objectives")
    if point["links"]:
        close(pyp["native_link_roundtrip_mw"], [
            [link["native_from_mw"], link["native_to_mw"]] for link in point["links"]
        ], "inverse terminal mapping from assembled PYPOWER Pg")
    return {"point": point, "common_generator_costs": costs, "cvxopf": cvx_values, "pypower": pyp}


def negative_controls(case, comparisons):
    by_name = {entry["point"]["name"]: entry for entry in comparisons}
    tests = []
    for tool in ("cvxopf", "pypower"):
        angle = by_name["violated_angle"][tool]["angle_rad"][2]
        tests.append(rejected(
            f"{tool} 9→4 angle floor", "angle floor",
            lambda: require(np.rad2deg(angle) <= ANGLE_TOL_DEG, "angle floor at branch row 8"),
        ))
        for name, index in (("violated_opposite_lower", 0), ("violated_opposite_upper", 3)):
            margin = by_name[name][tool]["angle_rad"][index]
            tests.append(rejected(
                f"{tool} {name}", "unwrapped ±360° endpoint",
                lambda: require(np.rad2deg(margin) <= ANGLE_TOL_DEG, "unwrapped ±360° endpoint"),
            ))
        face = by_name["violated_capability"][tool]["capability"][0]
        tests.append(rejected(
            f"{tool} upper P–Q face", "P–Q face",
            lambda: require(face <= MW_TOL, "P–Q face of generator at bus 2"),
        ))
        base = by_name["accounting"]
        cost_row = case["gencost"][1].copy()  # Polynomial in both variants.
        cost_row[3 + int(cost_row[3])] += 1  # Change only the constant.
        bad_cost = sum(base["common_generator_costs"]) - base["common_generator_costs"][1]
        bad_cost += generator_cost(cost_row, base["point"]["pg_mw"][1])
        tests.append(rejected(
            f"{tool} changed generator cost", "common objective",
            lambda: cost_close(base[tool]["objective"], bad_cost, "common objective"),
        ))
    if selected_links(case):
        link0, link1, _ = by_name["accounting"]["point"]["links"]
        close([link0["native_to_mw"], link0["pt_mw"], link0["physical_loss_mw"]],
              [9.9, 8.9, 1.1], "10 MW converter probe")
        low = by_name["low_send"]["point"]["links"][0]
        close([low["native_to_mw"], low["pt_mw"], low["physical_loss_mw"]],
              [.99, -.01, 1.01], "1 MW converter probe")
        dc = selected_links(case)
        reversed_link = dict(link1, native_from_mw=-link1["native_from_mw"],
                             native_to_mw=-link1["native_to_mw"])
        tests.append(rejected("reversed 7→9", "direction/box",
                              lambda: audit_link(1, dc[1], reversed_link)))
        tests.append(rejected(
            "reversed fixed charge", "fixed withdrawal",
            lambda: audit_link(0, dc[0], dict(link0, fixed_withdrawal_mw=-1)),
        ))
        tests.append(rejected(
            "double-counted charge", "converter accounting",
            lambda: audit_link(0, dc[0], dict(link0, pt_mw=link0["pt_mw"] - 1)),
        ))
    return tests


def compare_network(cvx, record):
    arrays = {k: np.asarray(v["real"]) + 1j * np.asarray(v["imag"])
              for k, v in record.items()}
    close(arrays["ybus"], cvx.ybus, "built Ybus by external bus")
    adm = cvx.adm
    f, t = adm.from_bus, adm.to_bus
    r = np.arange(len(f))
    yf, yt = (np.zeros((len(f), len(cvx.ybus)), dtype=complex) for _ in range(2))
    yf[r, f], yf[r, t], yt[r, f], yt[r, t] = adm.yff, adm.yft, adm.ytf, adm.ytt
    close(arrays["yf"], yf, "assembled Yf in branch row order")
    close(arrays["yt"], yt, "assembled Yt in branch row order")
    vmax, base = cvx.case["bus"][:, 11], cvx.case["baseMVA"]
    from_bounds = base * vmax[f] * (abs(arrays["yf"]) @ vmax)
    to_bounds = base * vmax[t] * (abs(arrays["yt"]) @ vmax)
    for proof, bound_f, bound_t in zip(cvx.ratings, from_bounds, to_bounds, strict=True):
        close([bound_f, bound_t], [proof["from_bound_mva"], proof["to_bound_mva"]],
              f"terminal rating proof for branch {proof['row']}")
        require(max(bound_f, bound_t) < proof["rating_mva"] < 1e10, "PYPOWER rating redundancy")


def historical_inputs():
    data = {name: load(HISTORICAL / name) for name in ARCHIVES[:2]}
    log = (HISTORICAL / ARCHIVES[2]).read_text()
    match = re.search(r"^link1 p_in: .*? C\+=([+-]?\d+\.\d+)  P\*=", log, flags=re.MULTILINE)
    require(match is not None, "C+ retained from-injection missing")
    data["cplus_from_mw_rounded"] = float(match[1])
    return data


def historical_controls(case, archive, results):
    cost_close(results["pstar"]["objective_delta"] + archive["pstar_full.json"]["obj"],
               archive["pstar_full.json"]["obj"], "P* historical objective")
    pstar = results["pstar"]
    for field, tol in (
        ("largest_mw_or_mvar_violation", MW_TOL),
        ("largest_voltage_pu_violation", V_TOL),
        ("largest_angle_deg_violation", ANGLE_TOL_DEG),
        ("reference_angle_deg_violation", ANGLE_TOL_DEG),
        ("largest_mva_violation", MW_TOL),
        ("largest_epigraph_violation", 1e-4),
    ):
        require(pstar[field] <= tol, f"P* historical target {field}: {pstar[field]}")
    close(results["cstar"]["bus9_mw"], -23., "C* bus-9 residual", atol=MW_TOL)
    require(results["cstar"]["angle_floor_violation_deg"] > 1.7, "C* angle floor rejection")
    bounds = native_bounds(1, selected_links(case)[1])
    cplus = archive["cplus_from_mw_rounded"]
    close(cplus, 2., "C+ retained injection")
    cstar = archive["cstar_full.json"]
    sent, delivered = -cstar["p_hvdc_out"][2], cstar["p_hvdc_in"][2]
    close([sent, delivered], [9.5, 10.], "C* archived gain terminals", atol=MW_TOL)
    return {
        "source_replay": results,
        "expected_rejections": [
            rejected("archived active-power gain alone", "negative physical loss",
                     lambda: audit_loss(sent, delivered, sent - delivered)),
            rejected("archived gain reported as a loss", "reported loss sign",
                     lambda: audit_loss(sent, delivered, delivered - sent)),
            rejected("C+ retained from-injection", "C+ outside", lambda: require(
                bounds[0] <= cplus <= bounds[1],
                f"C+ outside 7→9 effective from box by {cplus - bounds[1]:g} MW")),
        ],
    }


def guard(*args, **kwargs):
    raise RuntimeError("preflight forbids numerical optimization")


def child_env():
    return {**os.environ, **dict.fromkeys((
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
    ), "1"), "PYTHONNOUSERSITE": "1"}


def cell_check(cell, attempt, python):
    case = make_case(cell)
    validate_cell(cell, case)
    points = point_suite(case)
    with patch.object(cp.Problem, "solve", guard), patch.object(OPFBuild, "solve", guard):
        cvx = CvxModel(case)
        request = {"cell": cell, "case": encode_case(case), "ratings": cvx.ratings, "points": points}
        if cell == "pwl_links":
            request["historical"] = historical_inputs()
        path = attempt / "request.json"
        atomic_immutable_json(path, request)
        stdout, stderr = attempt / "pypower.stdout", attempt / "pypower.stderr"
        with stdout.open("xb") as out, stderr.open("xb") as err:
            subprocess.run(
                [str(python), "-m", f"{MODULE}.pypower_side", str(path)], cwd=REPO,
                env=child_env(), stdout=out, stderr=err, check=True, timeout=30,
            )
        pyp = load(stdout)
        compare_network(cvx, pyp["network"])
        comparisons = [compare_point(cvx, rows, case, point)
                       for rows, point in zip(pyp["points"], points, strict=True)]
        loss_probes = cvx.probe_link_losses(points[0])
        controls = negative_controls(case, comparisons)
        history = (historical_controls(case, request["historical"], pyp["historical"])
                   if "historical" in request else None)
    return {
        "cell": cell, "no_solver_iterations": True, "callback_gate": pyp["callbacks"],
        "pypower_versions": pyp["versions"], "pypower_python": pyp["python"],
        "point_comparisons": comparisons, "rating_proof": cvx.ratings, "negative_controls": controls,
        "historical_controls": history, "cvxopf_demand_by_bus": cvx.load_by_bus,
        "cvxopf_link_loss_probes": loss_probes,
        "pypower_linear_rows": pyp["linear_rows"],
    }


def bound_files():
    return sorted([
        *(HERE / name for name in (
            "__init__.py", "contract.py", "cvx_side.py", "pypower_side.py", "preflight.py", "PROTOCOL.md",
        )),
        REPO / "scripts/generate_pypower_fixtures.py",
        REPO / "experiments/case118_annual_hierarchy/streaming_schema.py",
        *(HISTORICAL / name for name in ARCHIVES),
        *(REPO / "src/cvxopf" / name for name in CVX_CASE9_SOURCES),
    ])


def provenance(python):
    pinned = subprocess.check_output(
        [str(python), "-c", "import sys; from importlib.metadata import version; "
         "import json; print(json.dumps([sys.version, "
         "{p: version(p) for p in ('PYPOWER', 'numpy', 'scipy')}]))"],
        cwd=REPO, env=child_env(), text=True, timeout=15,
    )
    python_version, pins = json.loads(pinned)
    require(pins == PYPINS, f"unexpected PYPOWER dependencies: {pins}")
    return {
        "schema": 1, "purpose": "solver-free preflight", "cells": list(CELLS),
        "files": {str(p.relative_to(REPO)): sha256_path(p) for p in bound_files()},
        "cvxopf_python": sys.version, "cvxopf_executable": sys.executable,
        "cvxopf_dependencies": {
            name: importlib.metadata.version(name) for name in ("cvxpy", "numpy", "scipy", "pandas")
        },
        "pypower": {"executable": str(python), "python": python_version, "dependencies": pins},
    }


def checked_attempt(root, cell, directory):
    require((directory / "started.json").is_file() and not (directory / "failure.json").exists(),
            f"invalid finalized attempt: {directory}")
    path = directory / "receipt.json"
    receipt = load(path)
    require(receipt["cell"] == cell and receipt["no_solver_iterations"] is True,
            f"incorrect receipt identity in {path}")
    require(receipt["binding_sha256"] == sha256_path(root / "binding.json"), "binding hash mismatch")
    require(set(receipt["artifact_sha256"]) == set(ARTIFACTS), f"incomplete finalized attempt: {path}")
    for name, digest in receipt["artifact_sha256"].items():
        require(sha256_path(directory / name) == digest, f"published attempt hash mismatch: {name}")
    return receipt


def cell_receipt(root, cell):
    marker = root / cell / "passed.json"
    if not marker.exists():
        return None
    entry = load(marker)
    attempt = entry["attempt"]
    require(len(attempt) == 32 and all(c in "0123456789abcdef" for c in attempt),
            f"invalid attempt identity in {marker}")
    directory = root / cell / "attempts" / attempt
    require(sha256_path(directory / "receipt.json") == entry["receipt_sha256"],
            f"published receipt hash mismatch: {directory}")
    return checked_attempt(root, cell, directory)


def publish(root, cell, attempt):
    checked_attempt(root, cell, attempt)
    atomic_immutable_json(root / cell / "passed.json", {
        "attempt": attempt.name, "receipt_sha256": sha256_path(attempt / "receipt.json"),
    })


def attempts_for(root, cell):
    return sorted((root / cell / "attempts").glob("*/started.json"))


def status(root):
    require((root / "binding.json").exists(), f"no preflight binding at {root}")
    counts = {}
    for cell in CELLS:
        children = [path.parent for path in attempts_for(root, cell)]
        passed = cell_receipt(root, cell) is not None
        counts[cell] = {
            "preflight_passed": passed,
            "attempts": len(children),
            "failed_or_interrupted": sum((p / "failure.json").is_file() for p in children),
            "ready_to_publish": not passed and any((p / "receipt.json").is_file() for p in children),
            "unpublished": sum(not any((p / marker).is_file()
                                       for marker in ("receipt.json", "failure.json")) for p in children),
        }
    print(json.dumps(counts, indent=2))


def run(root, python, resume):
    if resume:
        require(root.is_dir() and (root / "binding.json").is_file(), "no bound preflight to resume")
    elif root.exists():
        raise ValueError(f"{root} exists; use --resume or choose a different run ID")
    binding = provenance(python)
    if not resume:
        root.mkdir(parents=True)
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
        atomic_immutable_json(root / "binding.json", {"commit": revision, "inputs": binding})
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(load(root / "binding.json")["inputs"] == binding, "bound preflight inputs changed")
        for cell in CELLS:
            if cell_receipt(root, cell):
                print(f"{cell}: reusing verified receipt", flush=True)
                continue
            finalized = [path.parent for path in attempts_for(root, cell)
                         if (path.parent / "receipt.json").is_file()]
            require(len(finalized) <= 1, f"multiple finished receipts for {cell}")
            if finalized:
                publish(root, cell, finalized[0])
                print(f"{cell}: published verified receipt from before interruption", flush=True)
                continue
            attempt = root / cell / "attempts" / uuid4().hex
            start = time.monotonic()
            atomic_immutable_json(attempt / "started.json", {"cell": cell, "started_utc": utc()})
            print(f"{cell}: building and probing callbacks ({attempt.name})", flush=True)
            try:
                receipt = cell_check(cell, attempt, python)
                receipt.update(
                    finished_utc=utc(), elapsed_s=time.monotonic() - start,
                    binding_sha256=sha256_path(root / "binding.json"),
                    artifact_sha256={name: sha256_path(attempt / name) for name in ARTIFACTS},
                )
                atomic_immutable_json(attempt / "receipt.json", receipt)
            except BaseException as exc:
                if not (attempt / "receipt.json").is_file():
                    atomic_immutable_json(attempt / "failure.json", {
                        "type": type(exc).__name__, "message": str(exc), "finished_utc": utc(),
                        "elapsed_s": time.monotonic() - start,
                    })
                raise
            publish(root, cell, attempt)
            print(f"{cell}: passed ({len(receipt['point_comparisons'])} unsolved points)", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="Single directory name under local outputs/")
    parser.add_argument("--pypower-python", type=Path, help="Python from the pinned PYPOWER environment")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true")
    mode.add_argument("--status", action="store_true")
    args = parser.parse_args()
    require(args.run_id.isascii() and args.run_id.replace("-", "_").replace("_", "").isalnum(),
            "invalid run ID")
    root = OUTPUTS / args.run_id
    require(not root.is_symlink() and not OUTPUTS.is_symlink(), "symlinked output path")
    if args.status:
        status(root)
    else:
        require(args.pypower_python is not None, "--pypower-python is required")
        run(root, args.pypower_python.absolute(), args.resume)


if __name__ == "__main__":
    main()
