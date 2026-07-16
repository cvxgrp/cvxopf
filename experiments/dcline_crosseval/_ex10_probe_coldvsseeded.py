# /// script
# requires-python = ">=3.10"
# dependencies = ["pypower==5.1.19", "numpy==2.2.6", "scipy==1.18.0"]
# ///
"""EX10 diagnostic: is iter-0 objective a valid 'did the seed land' signal?

I've over-read the iter-0 objective three times. Settle it directly: run pips
COLD (no seeding) and capture its iter-0 obj + trajectory, then compare to the
seeded runs (P-arm iter-0=18097, C-arm iter-0=15786).

- If COLD iter-0 ~ 18097/15786 too (i.e. pips's interior init lands there
  REGARDLESS of seed): then iter-0-obj is NOT a 'seed landed' signal -- it's just
  where the log-barrier interior start sits -- and the whole doubt I built on
  'iter-0 != seed obj' is WRONG. EX10's original verdict may stand.
- If COLD iter-0 differs from the seeded iter-0s: the seed IS moving the start,
  and we compare how.

Also dumps, for the seeded C* run, the FIRST-PRINCIPLES nodal mismatch
(V*conj(Ybus V) - Sbus) at x0 -- the trustworthy feasibility measure -- next to
gh_fcn's g, to see if the earlier 1e6 from gh_fcn was a call artifact.

Run: uv run _ex10_probe_coldvsseeded.py.  Writes results/ex10_coldvsseeded.txt.
"""
import importlib.util
import inspect
import json
from pathlib import Path

import numpy as np
import pypower.pipsopf_solver as ppsolver

_here = Path(__file__).resolve().parent
RES = _here / "results"

_spec = importlib.util.spec_from_file_location("ex", _here / "_ex_crosseval.py")
ex1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex1)

EXPECTED_NX = 38
VA_SL = slice(0, 9); VM_SL = slice(9, 18)
PG_SL = slice(18, 27); QG_SL = slice(27, 36)

cv = json.loads((RES / "cstar_full.json").read_text())
c_pin = np.array(cv["p_hvdc_in"]); c_pout = np.array(cv["p_hvdc_out"])
seed_va = np.deg2rad(np.array(cv["Va_deg"]))
seed_vm = np.array(cv["Vm"])
seed_pg = np.concatenate([cv["Pg"], -c_pin, -c_pout]) / 100.0
seed_qg = np.concatenate([cv["Qg"], np.zeros(2 * len(c_pin))]) / 100.0

_orig_pips = ppsolver.pips
_cap = {}


def _traj(sol):
    hist = sol["output"].get("hist", [])
    return [round(float(h["obj"]), 1) for h in hist]


def _cold_pips(f_fcn, x0, A, l, u, xmin, xmax, gh_fcn, hess_fcn, opt):
    # capture pips's OWN default x0 (no seeding) and its objective there
    _cap["cold_x0_head"] = np.round(x0[:12], 4).tolist()
    f0 = f_fcn(x0)
    _cap["cold_f0"] = float(f0[0]) if isinstance(f0, tuple) else float(f0)
    sol = _orig_pips(f_fcn, x0, A, l, u, xmin, xmax, gh_fcn, hess_fcn, opt)
    _cap["cold_iter0"] = float(sol["output"]["hist"][0]["obj"])
    _cap["cold_traj"] = _traj(sol)
    return sol


def _seeded_pips(f_fcn, x0, A, l, u, xmin, xmax, gh_fcn, hess_fcn, opt):
    om = inspect.currentframe().f_back.f_locals["om"]
    gen_i2e = om.ppc["order"]["gen"]["i2e"].astype(int)
    x0 = x0.copy()
    x0[VA_SL] = seed_va; x0[VM_SL] = seed_vm
    x0[PG_SL] = seed_pg[gen_i2e]; x0[QG_SL] = seed_qg[gen_i2e]
    f0 = f_fcn(x0)
    _cap["seed_f0"] = float(f0[0]) if isinstance(f0, tuple) else float(f0)
    # first-principles nodal mismatch (trustworthy feasibility measure)
    bus = inspect.currentframe().f_back.f_locals["bus"]
    gen = inspect.currentframe().f_back.f_locals["gen"]
    base = inspect.currentframe().f_back.f_locals["baseMVA"]
    Ybus_ = inspect.currentframe().f_back.f_locals["Ybus"]
    from pypower.makeSbus import makeSbus
    Vv = seed_vm * np.exp(1j * seed_va)
    mis = Vv * np.conj(Ybus_ * Vv) - makeSbus(base, bus, gen)
    _cap["seed_mis_absmax"] = float(np.max(np.abs(mis)))
    g, h, _, _ = gh_fcn(x0)
    _cap["seed_g_absmax"] = float(np.max(np.abs(np.asarray(g).ravel())))
    sol = _orig_pips(f_fcn, x0, A, l, u, xmin, xmax, gh_fcn, hess_fcn, opt)
    _cap["seed_iter0"] = float(sol["output"]["hist"][0]["obj"])
    _cap["seed_traj"] = _traj(sol)
    return sol


ppsolver.pips = _cold_pips
ex1.solve_neutralized()
ppsolver.pips = _seeded_pips
ex1.solve_neutralized()
ppsolver.pips = _orig_pips

lines = []
def emit(s): lines.append(s)

emit("# EX10 diagnostic: cold vs C*-seeded pips start")
emit("")
emit("## COLD start (no seeding -- pips's own default x0)")
emit(f"cold x0[:12] = {_cap['cold_x0_head']}")
emit(f"cold f(x0)   = {_cap['cold_f0']:.2f}")
emit(f"cold iter-0 obj (hist[0]) = {_cap['cold_iter0']:.2f}")
emit(f"cold trajectory = {_cap['cold_traj']}")
emit("")
emit("## C*-SEEDED start")
emit(f"seed f(x0)   = {_cap['seed_f0']:.2f}   (C* obj = 5490.10)")
emit(f"seed iter-0 obj (hist[0]) = {_cap['seed_iter0']:.2f}")
emit(f"seed trajectory = {_cap['seed_traj']}")
emit("")
emit("## feasibility of the C* seed (two measures)")
emit(f"first-principles |V conj(YV) - Sbus| max = {_cap['seed_mis_absmax']:.4e}")
emit(f"gh_fcn |g| max                            = {_cap['seed_g_absmax']:.4e}")
emit("")
emit("## READ")
emit("If cold iter-0 ~ seed iter-0: iter-0-obj is NOT a 'seed landed' signal.")
emit("If first-principles mismatch is small but gh_fcn |g| is 1e6: the 1e6 was")
emit("a gh_fcn call artifact, and the seed is actually feasible.")

(RES / "ex10_coldvsseeded.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
