# /// script
# requires-python = ">=3.10"
# dependencies = ["pypower==5.1.19", "numpy==2.2.6", "scipy==1.18.0"]
# ///
"""EX10 probe: measure the pips x0 layout for the neutralized case.

EX8 was void because pypower's runopf discards the case-table seed -- proven at
the source: pipsopf_solver builds x0 = (ll+uu)/2 (bound midpoints), then only
overrides Va (flat at ref angle) and the PWL y-vars. The case bus/gen dispatch
never reaches x0.

The real warm-start hook is therefore to intercept x0 at the pips() call and
overwrite the Va/Vm/Pg/Qg blocks. To do that safely we must know the EXACT block
layout (which slice of x0 is which variable) for OUR neutralized case, measured
not assumed. This probe captures, at the pips boundary:
  - len(x0), nb, ng, ny (PWL y-var count)
  - the vv i1/iN block boundaries for Va, Vm, Pg, Qg, (y)
  - a sanity check that len(x0) == 2*nb + 2*ng + ny

Mechanism: monkeypatch pypower.pipsopf_solver.pips to capture x0 + read the vv
map off the enclosing frame, then run solve_neutralized once. Read-only probe;
it does NOT seed anything -- it only reports the layout so EX10 proper can seed
correctly and with an assert-guard.

Sandbox script. Run: uv run _ex10_probe_x0layout.py
Writes results/ex10_x0layout.txt.
"""
import importlib.util
import inspect
from pathlib import Path

import numpy as np
import pypower.pipsopf_solver as ppsolver

_here = Path(__file__).resolve().parent
RES = _here / "results"

_spec = importlib.util.spec_from_file_location("ex", _here / "_ex_crosseval.py")
ex1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex1)

_captured = {}
_orig_pips = ppsolver.pips


def _capturing_pips(f_fcn, x0, A, l, u, xmin, xmax, gh_fcn, hess_fcn, opt):
    # vv lives in the calling frame (pipsopf_solver); read it off the stack.
    frame = inspect.currentframe().f_back
    vv = frame.f_locals.get("vv")
    om = frame.f_locals.get("om")
    _captured["len_x0"] = int(len(x0))
    _captured["nb"] = int(frame.f_locals.get("nb", -1))
    _captured["ng"] = -1
    _captured["ny"] = int(frame.f_locals.get("ny", -1))
    _captured["vv"] = None
    if vv is not None:
        blocks = {}
        for name in ("Va", "Vm", "Pg", "Qg", "y"):
            if name in vv["i1"]:
                blocks[name] = (int(vv["i1"][name]), int(vv["iN"][name]))
        _captured["vv"] = blocks
    if om is not None:
        try:
            _captured["ng"] = int(om.getN("var", "Pg"))
        except Exception:
            pass
    # snapshot the default (cold) x0 blocks so EX10 can prove it overwrote them
    _captured["x0_sample"] = {
        "Va_head": np.round(x0[:3], 4).tolist(),
        "first_10": np.round(x0[:10], 4).tolist(),
    }
    return _orig_pips(f_fcn, x0, A, l, u, xmin, xmax, gh_fcn, hess_fcn, opt)


ppsolver.pips = _capturing_pips
res, _ = ex1.solve_neutralized()
ppsolver.pips = _orig_pips

lines = []
def emit(s):
    lines.append(s)

emit("# EX10 probe: pips x0 layout for the neutralized case")
emit(f"solve success = {bool(res['success'])}  obj = {float(res['f']):.4f}")
emit("")
emit(f"len(x0) = {_captured['len_x0']}")
emit(f"nb = {_captured['nb']}   ng (Pg vars) = {_captured['ng']}   ny (PWL y) = {_captured['ny']}")
emit("")
emit("## vv block boundaries [i1:iN)")
vv = _captured["vv"]
if vv:
    for name, (i1, iN) in vv.items():
        emit(f"  {name:4s} [{i1:3d}:{iN:3d})  size {iN - i1}")
emit("")
nb = _captured["nb"]; ng = _captured["ng"]; ny = _captured["ny"]
expected = 2 * nb + 2 * ng + (ny if ny > 0 else 0)
emit(f"## layout check: 2*nb + 2*ng + ny = 2*{nb} + 2*{ng} + {max(ny,0)} = {expected}")
emit(f"   len(x0) = {_captured['len_x0']}  ->  {'MATCH' if expected == _captured['len_x0'] else 'MISMATCH -- extra var blocks present'}")
emit("")
emit("## default (cold) x0 sample -- what EX8 actually fed IPOPT")
emit(f"  x0[:3] (Va block, expect flat ref angle) = {_captured['x0_sample']['Va_head']}")
emit(f"  x0[:10] = {_captured['x0_sample']['first_10']}")

(RES / "ex10_x0layout.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
