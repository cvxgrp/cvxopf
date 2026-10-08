"""Saved-evidence convergence and materiality checks; no optimization calls."""

import argparse
import gzip
import json
from pathlib import Path
import re

import numpy as np

from experiments.socp_conditioning import cone_scaling_analysis as ca
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import optimality_audit as oa


def stopping(record, log):
    """Reconstruct visible v0.11.1 Solved tests, not an unrecorded error branch."""
    info = record["native_info"]["native_info"]
    settings = record["native_info"]["effective_native_settings"]

    def setting(name):
        match = re.search(rf"^\s*{name}: ([^,]+),", settings, re.M)
        if match is None:
            raise ValueError(f"missing retained setting: {name}")
        return float(match[1])

    rows = [
        line.split() for line in log.splitlines() if re.match(r"^\s*\d+\s+[+-]\d", line)
    ]
    if not rows or len(rows[-1]) != 9:
        raise ValueError("missing/unsupported native iteration log")
    # Printed columns: iter pcost dcost gap pres dres k/t mu step (9 fields).
    return _criteria(info, setting, rows)


def _criteria(info, setting, rows):
    final = rows[-1]
    kt = float(final[6])
    checks = {
        "gap_abs": info["gap_abs"] < setting("tol_gap_abs"),
        "gap_rel": info["gap_rel"] < setting("tol_gap_rel"),
        "primal": info["res_primal"] < setting("tol_feas"),
        "dual": info["res_dual"] < setting("tol_feas"),
        "embedding_ratio_from_rounded_log": kt <= 1,
    }
    return dict(
        native_info=info,
        full_checks=checks,
        full_visible_tests_pass=(checks["gap_abs"] or checks["gap_rel"])
        and checks["primal"]
        and checks["dual"]
        and checks["embedding_ratio_from_rounded_log"],
        gap_abs_over_requested=info["gap_abs"] / setting("tol_gap_abs"),
        gap_rel_over_requested=info["gap_rel"] / setting("tol_gap_rel"),
        reduced_gap_pass=(
            info["gap_abs"] < setting("reduced_tol_gap_abs")
            or info["gap_rel"] < setting("reduced_tol_gap_rel")
        ),
        reduced_feasibility_pass=max(info["res_primal"], info["res_dual"])
        < setting("reduced_tol_feas"),
        last_logged_step=float(final[-1]),
        last_logged_embedding_ratio=kt,
        tail=rows[-5:],
        underlying_pre_postprocess_error="not retained; zero final step alone does not distinguish KKT failure from too-small step",
    )


def deltas(left, right):
    result = {}
    for key in ("Pg", "b", "soc", "p_nd", "Vm_relaxed"):
        a, b = np.asarray(left[key], float), np.asarray(right[key], float)
        if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
            raise ValueError(f"invalid paired field {key}")
        v = np.abs(a - b)
        result[key] = dict(max_abs=float(v.max()), mean_abs=float(v.mean()))
    return result


def analyze(root, *, deficit=False):
    labels = ("shedding_baseline", "shedding_scaled") if deficit else ca.c.ARMS
    verified = ca.analyze(root, labels=labels)
    arms, records = [], {}
    for label in labels:
        folder = root / label
        with gzip.open(folder / "arm.json.gz", "rt") as stream:
            r = json.load(stream)
        records[label] = r
        opt = oa.analyze(folder)
        audit = r["audit"]
        ratios = {k: v / audit["limits"][k] for k, v in audit["residuals"].items()}
        arms.append(
            dict(
                label=label,
                stopping=stopping(r, (folder / "worker.log").read_text()),
                physical_residuals=audit["residuals"],
                physical_gate_fractions=ratios,
                metrics=audit["metrics"],
                costs=r["named_costs"],
                stationarity_by_group=opt["groups"],
                storage_epigraph=opt["storage"],
                gap_identity=opt["gap_identity_original_units"],
            )
        )
    pairs = {
        prefix: deltas(
            records[prefix + "_baseline"]["result"],
            records[prefix + "_scaled"]["result"],
        )
        for prefix in (("shedding",) if deficit else ("shedding", "fixed"))
    }
    if not deficit:
        pairs["scaled_shedding_vs_fixed"] = deltas(
            records["shedding_scaled"]["result"], records["fixed_scaled"]["result"]
        )
    return dict(
        verified=verified,
        arms=arms,
        descriptive_trajectory_differences=pairs,
        analysis_source_sha256=d.sha(__file__),
        optimization_calls=0,
        warning="coordinate differences do not bound errors in nonunique trajectories",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deficit", action="store_true")
    args = parser.parse_args()
    d.publish(args.output, analyze(args.root, deficit=args.deficit))
