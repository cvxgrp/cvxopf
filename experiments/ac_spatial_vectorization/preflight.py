"""Resolve the seven benchmark inputs and construct their AC graphs; never solve."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import numpy as np

from cvxopf import build_opf, build_opf_multistep
from cvxopf.data import validate_case
from experiments.case118_tracy_2021 import e3, e3_plus, e3_prepared


HERE = Path(__file__).resolve().parent
SELECTION = e3.HERE / "e3_selection/selection.json"
CASE500_NAME = "pglib_opf_case500_goc"
CASE500_SHA256 = "36c298d571605019ef16c17dd74680adca1386d91ed47d69a0d909aebc90a1b6"
CASE500 = HERE / "results/inputs" / f"{CASE500_NAME}.m"

# Stable benchmark identities from PES2027 Appendix A1. The first five
# entries use the committed selection; the sixth uses the E3+ input binding.
ORDINARY_CASES = {
    ("Large surplus", "energy_neutral", 3308, 3332): "large-surplus",
    ("Large deficit", "energy_neutral", 1165, 1189): "large-deficit",
    ("Large deficit", "deficit_depletion", 1165, 1189): "deficit-depletion",
    ("Surplus to deficit", "energy_neutral", 8580, 8604): "surplus-to-deficit",
    ("Deficit to surplus", "energy_neutral", 2439, 2463): "deficit-to-surplus",
}


def select_pes_arms(selection):
    """Pick each published primary AC fixture once, without rerunning selection."""
    ordinary = {}
    for arm in e3.make_study(selection)["arms"]:
        if arm["formulation"] != "ac":
            continue
        key = (arm["window"], arm["leg"], arm["start"], arm["stop"])
        if key not in ORDINARY_CASES or ORDINARY_CASES[key] in ordinary:
            raise ValueError(f"Unexpected or duplicate PES2027 AC fixture: {key}")
        ordinary[ORDINARY_CASES[key]] = arm
    if set(ordinary) != set(ORDINARY_CASES.values()):
        raise ValueError("The PES2027 selection must supply all five ordinary AC cases")
    return {
        **ordinary,
        "forced-shedding": dict(ordinary["large-deficit"], window="Forced shedding"),
    }


def load_case500(path: Path) -> dict:
    """Read only the admitted numeric MATPOWER source, with no MATLAB execution."""
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CASE500_SHA256:
        raise ValueError(f"PGLib case SHA-256 mismatch: {path}")
    source = raw.decode("utf-8")
    case = {"version": "2", "baseMVA": 100.0}
    for name, expected in {
        "bus": (500, 13), "gen": (224, 10), "gencost": (224, 7), "branch": (733, 13),
    }.items():
        match = re.search(rf"^mpc\.{name}\s*=\s*\[(.*?)^\s*\];", source, re.MULTILINE | re.DOTALL)
        if match is None:
            raise ValueError(f"Missing PGLib matrix: {name}")
        rows = (line.split("%", 1)[0].strip().removesuffix(";")
                for line in match[1].splitlines())
        array = np.loadtxt([line for line in rows if line])
        if array.shape != expected or not np.isfinite(array).all():
            raise ValueError(f"Invalid PGLib {name} array, expected finite data of shape {expected}")
        case[name] = array
    # As with the repository's pinned Case118 importer, only pad unused
    # generator columns required by the 21-column cvxopf case contract.
    case["gen"] = np.pad(case["gen"], ((0, 0), (0, 11)))
    validate_case(case)
    return case


def benchmark_inputs(case500_path: Path):
    """Yield the fixture ID, selection metadata, and exact public build inputs."""
    case500 = load_case500(case500_path)
    selection = json.loads(SELECTION.read_text())
    ids = selection["storage_device_ids"]
    cases = select_pes_arms(selection)
    prepared = e3_prepared.verified_inputs()
    for name, arm in cases.items():
        model = e3_plus if name == "forced-shedding" else e3_prepared
        kwargs = model.kwargs_for_arm(prepared, arm, ids, role="primary", assembly="component_first")
        yield name, {"start": arm["start"], "stop": arm["stop"], "T": 24}, kwargs
    yield "pglib-500-goc", {"T": 1}, {"case": case500, "formulation": "ac"}


def preflight(case500_path: Path):
    for name, metadata, kwargs in benchmark_inputs(case500_path):
        input_sha256 = e3.input_digest(kwargs)
        builder = build_opf_multistep if "T" in kwargs else build_opf
        build = builder(**kwargs)
        yield dict(
            fixture=name, **metadata, input_sha256=input_sha256,
            buses=build.data["nb"], branches=build.data["nl"],
            modeled_variables=sum(v.size for v in build.prob.variables()),
            modeled_constraints=sum(c.size for c in build.prob.constraints),
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case500", type=Path, default=CASE500, help="pinned PGLib source file")
    args = parser.parse_args()
    try:
        for receipt in preflight(args.case500):
            print(json.dumps(receipt, sort_keys=True), flush=True)
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(1, f"Preflight failed: {exc}\n")


if __name__ == "__main__":
    main()
