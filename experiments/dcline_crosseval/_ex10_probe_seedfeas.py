# /// script
# requires-python = ">=3.10"
# dependencies = ["pypower==5.1.19", "numpy==2.2.6", "scipy==1.18.0"]
# ///
"""EX10 probe: does the seeded x0 actually LAND FEASIBLY at the pips boundary?

EX10's iter-0 objectives (18097 P-arm, 15786 C-arm) were NOT near the seed
objectives (6250, 5490), and both arms then shot up to ~1e5 before descending to
P*. That looks like an inconsistent seed being restored, not a warm start that
took -- i.e. a subtler EX8 failure. Two confounds to separate before trusting
EX10's "Pypower leaves C*" verdict:

  (1) pips history obj is f/cost_mult (SCALED, pips.py:337), so iter-0 obj != seed
      obj does NOT by itself prove the seed didn't land.
  (2) the real test is the CONSTRAINT VIOLATION at the seeded x0. If our seeded
      point is grossly infeasible in pips's equality system (nodal balance via
      the dcline userfcn + PWL cost-epigraph y relations), pips restores away
      from it regardless of basin -- verdict void.

This probe intercepts pips, and BEFORE iterating evaluates gh_fcn(x0) (the same
constraint fn pips uses: g = equalities, h = inequalities) at the seeded x0, plus
f_fcn(x0) for the true (unscaled) objective. It also dumps the seeded x0 blocks
to confirm Va/Vm/Pg/Qg actually hold our values and shows the default y-block.

Prime suspect: y[36:38] (PWL cost helper) left at pypower default, inconsistent
with seeded Pg -> cost-epigraph equality blown at iter-0.

Seeds C* (one arm is enough to diagnose). Read-only w.r.t. the seed logic
(reuses EX10's slices). Run: uv run _ex10_probe_seedfeas.py
Writes results/ex10_seedfeas.txt.
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
PG_SL = slice(18, 27); QG_SL = slice(27, 36); Y_SL = slice(36, 38)

cv = json.loads((RES / "cstar_full.json").read_text())
c_pin = np.array(cv["p_hvdc_in"]); c_pout = np.array(cv["p_hvdc_out"])
seed_va = np.deg2rad(np.array(cv["Va_deg"]))
seed_vm = np.array(cv["Vm"])
seed_pg = np.concatenate([cv["Pg"], -c_pin, -c_pout]) / 100.0
seed_qg = np.concatenate([cv["Qg"], np.zeros(2 * len(c_pin))]) / 100.0

_orig_pips = ppsolver.pips
_cap = {}


def _probing_pips(f_fcn, x0, A, l, u, xmin, xmax, gh_fcn, hess_fcn, opt):
    assert len(x0) == EXPECTED_NX
    # ext2int REORDERS gens (sorts by bus): pips-internal gen order != our
    # append order [3 real, 3 from, 3 to]. Read the gen i2e map off the solver
    # frame (single source of truth) and permute Pg/Qg into internal order.
    # Bus order is identity here ([1,2,30,4,5,6,7,8,9]) so Va/Vm need no perm --
    # asserted, not assumed.
    fr = inspect.currentframe().f_back
    om = fr.f_locals["om"]
    gen_i2e = om.ppc["order"]["gen"]["i2e"].astype(int)
    bus_i2e = om.ppc["order"]["bus"]["i2e"].astype(int)
    assert list(bus_i2e) == [1, 2, 30, 4, 5, 6, 7, 8, 9], (
        f"bus order not identity: {bus_i2e.tolist()}; Va/Vm would need permuting"
    )
    x0 = x0.copy()
    _cap["gen_i2e"] = gen_i2e.tolist()
    _cap["y_default"] = x0[Y_SL].tolist()          # pypower's default y before we touch it
    x0[VA_SL] = seed_va; x0[VM_SL] = seed_vm
    x0[PG_SL] = seed_pg[gen_i2e]; x0[QG_SL] = seed_qg[gen_i2e]
    # y left at default; (b) proved eq idx 1 is nodal balance, not the cost row
    _cap["x0_seeded"] = x0.copy()
    # evaluate objective + constraints AT the seeded x0, before iterating
    f0 = f_fcn(x0)
    _cap["f0"] = float(f0[0]) if isinstance(f0, tuple) else float(f0)
    g, h, _, _ = gh_fcn(x0)      # g: equalities (==0), h: inequalities (<=0)
    g = np.asarray(g).ravel(); h = np.asarray(h).ravel()
    _cap["eq_absmax"] = float(np.max(np.abs(g))) if g.size else 0.0
    _cap["eq_argmax"] = int(np.argmax(np.abs(g))) if g.size else -1
    # localize the 1e6: split g into real (first nb) and imag (next nb) mismatch,
    # and reconstruct Sbus vs Ybus*V per bus to see which side is off.
    nbb = len(seed_vm)
    _cap["g_real"] = np.round(g[:nbb], 4).tolist()
    _cap["g_imag"] = np.round(g[nbb:2 * nbb], 4).tolist()
    from pypower.makeSbus import makeSbus
    from pypower.makeYbus import makeYbus
    bus = fr.f_locals["bus"]; gen = fr.f_locals["gen"]; base = fr.f_locals["baseMVA"]
    Ybus_ = fr.f_locals["Ybus"]
    Vv = seed_vm * np.exp(1j * seed_va)
    Sb = makeSbus(base, bus, gen)
    _cap["Sbus_real"] = np.round(Sb.real, 4).tolist()
    _cap["YV_real"] = np.round((Vv * np.conj(Ybus_ * Vv)).real, 4).tolist()
    _cap["ineq_max"] = float(np.max(h)) if h.size else 0.0   # >0 = violated
    _cap["n_eq"] = int(g.size); _cap["n_ineq"] = int(h.size)
    return _orig_pips(f_fcn, x0, A, l, u, xmin, xmax, gh_fcn, hess_fcn, opt)


ppsolver.pips = _probing_pips
res, _ = ex1.solve_neutralized()
ppsolver.pips = _orig_pips

lines = []
def emit(s): lines.append(s)

emit("# EX10 probe: feasibility of the seeded x0 at the pips boundary (C* seed)")
emit("")
emit(f"true (unscaled) objective f(x0_seed) = {_cap['f0']:.4f}")
emit(f"  (C* obj = 5490.10; near it => seed's objective landed)")
emit("")
emit("## constraint violation AT the seeded x0 (before pips iterates)")
emit(f"n_eq={_cap['n_eq']}  n_ineq={_cap['n_ineq']}")
emit(f"equality |g| max   = {_cap['eq_absmax']:.4e}  (at eq idx {_cap['eq_argmax']})")
emit(f"inequality h max    = {_cap['ineq_max']:.4e}  (>0 = violated)")
emit(f"g_real (per-bus real mismatch) = {_cap['g_real']}")
emit(f"g_imag (per-bus reactive mism) = {_cap['g_imag']}")
emit(f"Sbus.real (injections, pu)     = {_cap['Sbus_real']}")
emit(f"(Ybus*V).real                  = {_cap['YV_real']}")
emit("  equality |g| ~0 => seed is feasible in pips's system (landed).")
emit("  equality |g| large => inconsistent seed; pips restores away -> EX10 void.")
emit("")
emit("## seeded x0 blocks (confirm our values actually hold)")
x0 = _cap["x0_seeded"]
emit(f"Va (rad) = {np.round(x0[VA_SL],4).tolist()}")
emit(f"Vm       = {np.round(x0[VM_SL],4).tolist()}")
emit(f"Pg (pu)  = {np.round(x0[PG_SL],4).tolist()}")
emit(f"Qg (pu)  = {np.round(x0[QG_SL],4).tolist()}")
emit(f"y (default, NOT seeded) = {np.round(x0[Y_SL],4).tolist()}")
emit(f"  pypower default y before seeding = {np.round(_cap['y_default'],4).tolist()}")

(RES / "ex10_seedfeas.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
