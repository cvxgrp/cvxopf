"""EX9: two-arm warm-start control test on the CVXOPF (cvxpy/IPOPT) side.

The Pypower-side warm-start (EX8) did nothing at the solver level -- runopf
discards the case VM/VA/PG/QG seed and cold-inits x0 (proven: identical 24-iter
history for cold vs seed-at-optimum, see _ex8_probe_iters.py). CVXPY, by
contrast, passes variable.value straight to IPOPT as x0, so here we CAN warm
start. Declare the problem once, set every variable.value to a seed, solve.

Two arms, seeding cvxopf's case9_dcline AC problem at each recorded optimum:
  C-arm (CONTROL):     seed C* (cvxopf's own optimum) -> expect it to STAY
                       (obj ~5490, ~0-1 IPOPT iters). Trivial control.
  P-arm (INFORMATIVE): seed P* (neutralized-Pypower optimum) -> does cvxopf
                       STAY at P* (obj ~6250, a second local optimum it can
                       hold) or FALL to C* (obj ~5490, its global-ish basin)?

Decisive signal, learned from EX8: the IPOPT ITERATION COUNT and objective
trajectory (verbose=True), not just output-minus-seed drift. Seeding at a true
local optimum should converge in very few iters; a large iter count + objective
move means the seed was left.

Full, self-consistent x0 (per the EX9 decision -- seed everything
reconstructable). Variables (single-step AC + HVDC, no storage/ND):
  theta (nb,1) = Va[rad]      v (nb,1) = Vm
  Pg (ng,) = Pg_MW/baseMVA    Qg (ng,) = Qg_MW/baseMVA     [per-unit]
  p_hvdc_in/out (n_hvdc,) = p_in/p_out [MW, engineering units, NOT per-unit]
  P_vec/Q_vec (nnz,) reconstructed from V via the exact defining trig:
     C_k=cos(th_i-th_j), S_k=sin(th_i-th_j), vv_k=v_i*v_j
     P_vec[k]=vv_k*(G_vec[k]*C_k + B_vec[k]*S_k)
     Q_vec[k]=vv_k*(G_vec[k]*S_k - B_vec[k]*C_k)         [per-unit]
  p/q (nb,) = Rp@P_vec / Rp@Q_vec                         [per-unit]

Units (CLAUDE.md): gen & flow vars per-unit; only HVDC in engineering units.
G_vec/B_vec are per-unit admittances and v is per-unit, so P_vec/Q_vec come out
per-unit directly -- no baseMVA scaling on them. Only Pg/Qg need MW->pu.

Main cvxopf env. Run: uv run --active python _ex9_warmstart_cvxopf.py
Writes results/ex9_warmstart_cvxopf.txt.
"""
import warnings
import json
from pathlib import Path

import numpy as np

from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf

HERE = Path(__file__).resolve().parent
RES = HERE / "results"

P_STAR_OBJ = 6249.8659
C_STAR_OBJ = 5490.1038


def build_problem():
    """Fresh cvxopf case9_dcline AC build, no solve."""
    case = case9_dcline()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        links = hvdc_from_dcline(case["dcline"])
        b = build_opf(case, formulation="ac", hvdc=links)
    return b


def reconstruct_pq(theta_rad, v_mag, d):
    """Closed-form P_vec/Q_vec (per-unit) from V, exactly matching Section 2."""
    rows = d["rows"].astype(int)
    cols = d["cols"].astype(int)
    G_vec = np.asarray(d["G_vec"], float)
    B_vec = np.asarray(d["B_vec"], float)
    dth = theta_rad[rows] - theta_rad[cols]
    C = np.cos(dth)
    S = np.sin(dth)
    vv = v_mag[rows] * v_mag[cols]
    P_vec = vv * (G_vec * C + B_vec * S)
    Q_vec = vv * (G_vec * S - B_vec * C)
    return P_vec, Q_vec


def seed_problem(b, Vm, Va_deg, Pg_mw, Qg_mw, p_in, p_out):
    """Set every variable.value to a fully self-consistent x0."""
    d = b.data
    baseMVA = d["baseMVA"]
    nb = d["nb"]
    va_rad = np.deg2rad(np.asarray(Va_deg, float))
    vm = np.asarray(Vm, float)
    var = b.variables
    var["theta"].value = va_rad.reshape(nb, 1)
    var["v"].value = vm.reshape(nb, 1)
    var["Pg"].value = np.asarray(Pg_mw, float) / baseMVA          # MW -> pu
    var["Qg"].value = np.asarray(Qg_mw, float) / baseMVA          # MVAr -> pu
    var["p_hvdc_in"].value = np.asarray(p_in, float)              # MW (eng)
    var["p_hvdc_out"].value = np.asarray(p_out, float)            # MW (eng)
    P_vec, Q_vec = reconstruct_pq(va_rad, vm, d)                  # pu
    var["P_vec"].value = P_vec
    var["Q_vec"].value = Q_vec
    Rp = np.asarray(d["Rp"], float)
    var["p"].value = Rp @ P_vec                                   # pu
    var["q"].value = Rp @ Q_vec                                   # pu


def snapshot(b):
    """Read solved dispatch back in engineering units for comparison."""
    d = b.data
    baseMVA = d["baseMVA"]
    var = b.variables
    return {
        "obj": float(b.prob.value),
        "Vm": np.asarray(var["v"].value).ravel(),
        "Va_deg": np.rad2deg(np.asarray(var["theta"].value).ravel()),
        "Pg": np.asarray(var["Pg"].value).ravel() * baseMVA,
        "Qg": np.asarray(var["Qg"].value).ravel() * baseMVA,
        "p_in": np.asarray(var["p_hvdc_in"].value).ravel(),
        "p_out": np.asarray(var["p_hvdc_out"].value).ravel(),
    }


def drift(seed, out):
    """Per-block L2 drift, engineering units."""
    return {
        "Vm": float(np.linalg.norm(out["Vm"] - seed["Vm"])),
        "Va": float(np.linalg.norm(out["Va_deg"] - seed["Va_deg"])),
        "Pg": float(np.linalg.norm(out["Pg"] - seed["Pg"])),
        "Qg": float(np.linalg.norm(out["Qg"] - seed["Qg"])),
        "p_in": float(np.linalg.norm(out["p_in"] - seed["p_in"])),
        "p_out": float(np.linalg.norm(out["p_out"] - seed["p_out"])),
    }


# --- load seeds ---
pv = json.loads((RES / "pstar_full.json").read_text())
cv = json.loads((RES / "cstar_full.json").read_text())

# P* is in Pypower external order [1,2,30,4,5,6,7,8,9]; cvxopf internal order is
# the SAME (verified in EX7b: identity permutation). Both JSONs share this order.
P_seed = dict(Vm=pv["Vm"], Va_deg=pv["Va_deg"], Pg_mw=pv["real_Pg"],
              Qg_mw=pv["real_Qg"], p_in=[-x for x in pv["from_dummy_Pg"]],
              p_out=[-x for x in pv["to_dummy_Pg"]])
C_seed = dict(Vm=cv["Vm"], Va_deg=cv["Va_deg"], Pg_mw=cv["Pg"],
              Qg_mw=cv["Qg"], p_in=cv["p_hvdc_in"], p_out=cv["p_hvdc_out"])

lines = []
def emit(s):
    lines.append(s)

emit("# EX9: two-arm warm-start control on the cvxopf/cvxpy/IPOPT side")
emit(f"# C*_obj={C_STAR_OBJ} (cvxopf's own optimum)  P*_obj={P_STAR_OBJ}")
emit("")

for tag, seed_in, target in [
    ("C-arm CONTROL (seed C*)", C_seed, C_STAR_OBJ),
    ("P-arm INFORMATIVE (seed P*)", P_seed, P_STAR_OBJ),
]:
    b = build_problem()
    seed_problem(b, **seed_in)
    seed_snap = {
        "obj": None,
        "Vm": np.asarray(seed_in["Vm"], float),
        "Va_deg": np.asarray(seed_in["Va_deg"], float),
        "Pg": np.asarray(seed_in["Pg_mw"], float),
        "Qg": np.asarray(seed_in["Qg_mw"], float),
        "p_in": np.asarray(seed_in["p_in"], float),
        "p_out": np.asarray(seed_in["p_out"], float),
    }
    print(f"\n===== solving {tag} (verbose) =====")
    b.solve(verbose=True)
    out = snapshot(b)
    dd = drift(seed_snap, out)
    stacked = float(np.sqrt(sum(x * x for x in dd.values())))
    emit(f"## {tag}")
    emit(f"status={b.prob.status}  obj={out['obj']:.4f}  (seed target {target})")
    emit(f"drift Vm={dd['Vm']:.3e} Va={dd['Va']:.3e} Pg={dd['Pg']:.3e} "
         f"Qg={dd['Qg']:.3e} p_in={dd['p_in']:.3e} p_out={dd['p_out']:.3e}")
    emit(f"stacked drift = {stacked:.3e}")
    emit(f"solved p_in  = {np.round(out['p_in'],4).tolist()}")
    emit(f"solved p_out = {np.round(out['p_out'],4).tolist()}")
    emit(f"solved Pg    = {np.round(out['Pg'],4).tolist()}")
    emit(f"|obj - C*|={abs(out['obj']-C_STAR_OBJ):.3e}  |obj - P*|={abs(out['obj']-P_STAR_OBJ):.3e}")
    emit("")

emit("## READ")
emit("C-arm is the control: seeded at cvxopf's own optimum, it must stay (obj")
emit("~5490, tiny drift). The P-arm is the question: if cvxopf STAYS at P*")
emit("(obj ~6250), then P* is a genuine second local optimum cvxopf can hold ->")
emit("different-local-optima confirmed from the warm-start side. If cvxopf FALLS")
emit("from P* to C* (obj ~5490), then from cvxopf's solver P* is not a stable")
emit("optimum -- cvxopf's basin dominates. Compare against the EX8 finding that")
emit("Pypower's solver would not hold C*. Watch the IPOPT iteration counts in the")
emit("verbose logs above: few iters = seed held; many = seed left.")

(RES / "ex9_warmstart_cvxopf.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
