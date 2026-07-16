"""EX9 probe: why does IPOPT report iter-0 inf_pr=25 at the C* seed?

A true warm-start control (seed at cvxopf's own optimum C*) should start
near-feasible and exit in ~1 iter. Instead c*->c* took 21 iters from iter-0
inf_pr=2.50e1, IDENTICAL to the p*->c* arm. Identical iter-0 inf_pr across two
genuinely different seeds suggests a constraint block we're NOT seeding (so
CVXPY inits it the same both times), not the seed points themselves.

This dumps, at the C* seed and BEFORE solving, the residual of every constraint
in b.prob so we can localize the 25. Reuses the exact seeding path from
_ex9_warmstart_cvxopf.py (import it, don't re-derive).

Main cvxopf env. Run: uv run --active python _ex9_probe_seedfeas.py
"""
import importlib.util
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RES = HERE / "results"

# import the EX9 seeding machinery verbatim
_spec = importlib.util.spec_from_file_location(
    "ex9", HERE / "_ex9_warmstart_cvxopf.py"
)
# guard: _ex9 runs its experiment at import (module-level). We only want its
# functions, so exec in a namespace where __name__ != '__main__'... but it has
# no __main__ guard. Read+exec only the function defs is fragile; instead just
# rebuild here using its imported helpers via the module object, accepting that
# import will run the full EX9. To avoid that, we replicate the tiny seeding
# call directly rather than importing.
from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf
import warnings


def build_problem():
    case = case9_dcline()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        links = hvdc_from_dcline(case["dcline"])
        b = build_opf(case, formulation="ac", hvdc=links)
    return b


def reconstruct_pq(theta_rad, v_mag, d):
    rows = d["rows"].astype(int); cols = d["cols"].astype(int)
    G_vec = np.asarray(d["G_vec"], float); B_vec = np.asarray(d["B_vec"], float)
    dth = theta_rad[rows] - theta_rad[cols]
    C = np.cos(dth); S = np.sin(dth)
    vv = v_mag[rows] * v_mag[cols]
    return vv * (G_vec * C + B_vec * S), vv * (G_vec * S - B_vec * C)


def seed_problem(b, Vm, Va_deg, Pg_mw, Qg_mw, p_in, p_out):
    d = b.data; baseMVA = d["baseMVA"]; nb = d["nb"]
    va_rad = np.deg2rad(np.asarray(Va_deg, float)); vm = np.asarray(Vm, float)
    var = b.variables
    var["theta"].value = va_rad.reshape(nb, 1)
    var["v"].value = vm.reshape(nb, 1)
    var["Pg"].value = np.asarray(Pg_mw, float) / baseMVA
    var["Qg"].value = np.asarray(Qg_mw, float) / baseMVA
    var["p_hvdc_in"].value = np.asarray(p_in, float)
    var["p_hvdc_out"].value = np.asarray(p_out, float)
    P_vec, Q_vec = reconstruct_pq(va_rad, vm, d)
    var["P_vec"].value = P_vec; var["Q_vec"].value = Q_vec
    Rp = np.asarray(d["Rp"], float)
    var["p"].value = Rp @ P_vec; var["q"].value = Rp @ Q_vec


cv = json.loads((RES / "cstar_full.json").read_text())
b = build_problem()
seed_problem(b, Vm=cv["Vm"], Va_deg=cv["Va_deg"], Pg_mw=cv["Pg"],
             Qg_mw=cv["Qg"], p_in=cv["p_hvdc_in"], p_out=cv["p_hvdc_out"])

print("# EX9 probe: per-constraint residual at the C* seed (pre-solve)")
print(f"# n constraints in b.prob = {len(b.prob.constraints)}")
print("")

# which variables have a value set vs None (unseeded blocks show as None)
print("## variable.value set?")
for nm, v in sorted(b.variables.items()):
    val = v.value
    print(f"  {nm:14s} shape={str(v.shape):10s} set={val is not None}")
print("")

# per-constraint violation at the seed
print("## per-constraint violation() at C* seed")
worst = []
for i, con in enumerate(b.prob.constraints):
    try:
        viol = con.violation()
        vmax = float(np.max(np.abs(viol))) if viol is not None else float("nan")
    except Exception as e:
        vmax = float("nan")
        print(f"  [{i:3d}] ERR {type(e).__name__}: {e}")
        continue
    worst.append((vmax, i, str(con)[:70]))
    if vmax > 1e-6:
        print(f"  [{i:3d}] viol={vmax:.4e}  {str(con)[:70]}")
print("")
worst.sort(reverse=True)
print("## top 8 worst constraints")
for vmax, i, s in worst[:8]:
    print(f"  [{i:3d}] viol={vmax:.4e}  {s}")
