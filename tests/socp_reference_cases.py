"""Public SOCP reference inputs and an analytic oracle; no builder imports."""

import hashlib
import json
from pathlib import Path

import numpy as np

from cvxopf.testcases import case9, case14


SETTINGS = dict(max_iter=200, time_limit=20.0, tol_gap_abs=1e-9,
                tol_gap_rel=1e-9, tol_feas=1e-9)
POLICY = dict(sparsity_tol=0, enforce_branch_limits=True, delta=1.0,
              angle_constraints=False, angle_cuts=False, product_bounds=False,
              optional_devices=False, loss_proxy=False)


def verify_reference_sources(reference):
    """Check recorded hashes against maintained or byte-preserved sources.

    The accepted E1 generator snapshot predates path-only CLI changes. Its
    original hash remains authoritative for those historical fixtures.
    """
    tools = Path(__file__).resolve().parents[1] / "scripts" / "socp_reference"
    generators = (tools / "generate_references.jl",
                  tools / "provenance" / "e1_generate_references.jl")
    assert reference["generator_sha256"] in {
        hashlib.sha256(path.read_bytes()).hexdigest() for path in generators
    }
    manifest = tools / "reference_env" / "Manifest.toml"
    assert reference["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()


def two_bus():
    """r=0.1 pu, P_load=0.1 pu, Q_load=0; source |V|=1 pu."""
    return dict(version="2", baseMVA=100.0,
        bus=np.array([
            [1, 3, 0, 0, 0, 0, 1, 1, 0, 100, 1, 1.1, 0.9],
            [2, 1, 10, 0, 0, 0, 1, 1, 0, 100, 1, 1.1, 0.9],
        ], dtype=float),
        gen=np.array([[1, 0, 0, 100, -100, 1, 100, 1, 100, 0,
                       0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]], dtype=float),
        branch=np.array([[1, 2, .1, 0, 0, 100, 0, 0, 0, 0, 1, -360, 360]], dtype=float),
        gencost=np.array([[2, 0, 0, 3, .01, 1, 0]], dtype=float))


def cases():
    return {"two_bus": two_bus(), "case9": case9(), "case14": case14()}


def payload(case):
    """Hash the exact mathematical MATPOWER inputs, preserving table row order."""
    return {key: (np.asarray(case[key], dtype=float).tolist()
                  if key in ("bus", "gen", "branch", "gencost") else case[key])
            for key in ("version", "baseMVA", "bus", "gen", "branch", "gencost")}


def input_hash(case):
    encoded = json.dumps(payload(case), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def policy(name):
    return {**POLICY, "enforce_vset": name == "two_bus"}


def analytic_two_bus():
    # Q balance gives Im(W12)=0; P2=-p gives c=w2+r*p. With w1=1,
    # (w2+r*p)^2 <= w2. Pg=(1-c)/r decreases with w2, and generation
    # cost is strictly increasing on Pg>=0. Thus the upper quadratic root
    # is optimal, lies inside voltage/dispatch/rating bounds, and is tight.
    r, p, base = .1, .1, 100.
    v2 = (1 + np.sqrt(1 - 4*r*p))/2
    c = v2
    pg = base*(1-c)/r
    return dict(w=[1., v2**2], W_re=[c], W_im=[0.], Pg=[pg], Qg=[0.],
                bus_ids=[1, 2], voltage_product_pairs=[[1, 2]],
                branch_p_from=[pg], branch_p_to=[-base*p],
                branch_q_from=[0.], branch_q_to=[0.],
                objective=.01*pg**2 + pg, branch_loss_mw=pg-base*p)
