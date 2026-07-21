"""Probe: explain C4 = -2.824e+02 (large Qg-bound slack) in EX12.

C4 = max(Qgmin - Qg,  Qg - Qgmax). Negative => interior (no violation). But
-282 MVAr is a suspiciously wide slack. Dump Qg (C+), Qgmin, Qgmax and the two
one-sided slacks per gen to see whether it is a genuinely wide bound (benign)
or a scaling/column bug in the readout (would undermine 'feasible').

Re-solves C+ exactly as EX12 does (loss0 grafted) so Qg is the C+ value.
"""
import warnings

import cvxpy as cp
import numpy as np

from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf
from cvxopf.results import extract_results
from cvxopf.testcases.case9_dcline import case9_dcline

case = case9_dcline()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    links = hvdc_from_dcline(case["dcline"])
    build = build_opf(case, formulation="ac", hvdc=links)

p_in = build.variables["p_hvdc_in"]
p_out = build.variables["p_hvdc_out"]
_STATUS, _LOSS0, _LOSS1 = 2, 15, 16
DC = case["dcline"]
dc_on = DC[DC[:, _STATUS] > 0, :]
L0 = dc_on[:, _LOSS0].astype(float)
L1 = dc_on[:, _LOSS1].astype(float)
coeff = -(1.0 - L1)

# graft loss0 on link0 (locator: only p_in/p_out equality)
coupling_idx = next(
    i
    for i, con in enumerate(build.prob.constraints)
    if isinstance(con, cp.constraints.Equality)
    and {v.id for v in con.variables()} == {p_in.id, p_out.id}
)
new = list(build.prob.constraints)
del new[coupling_idx]
for k in range(build.data["n_hvdc"]):
    off = L0[k] if k == 0 else 0.0
    new.append(p_out[k] == coeff[k] * p_in[k] + off)
build.prob = cp.Problem(build.prob.objective, new)
build.solve()

res = extract_results(build)
Qg = np.asarray(res["Qg"])  # MVAr (engineering units, already * baseMVA)

d = build.data
baseMVA = d["baseMVA"]
Qgmin = np.asarray(d["Qgmin"]) * baseMVA
Qgmax = np.asarray(d["Qgmax"]) * baseMVA

print(f"baseMVA = {baseMVA}")
print(f"n gens in data Qg bounds: {Qgmin.shape}   Qg (extracted, MVAr): {Qg.shape}")
print(f"raw d['Qgmin'] = {np.asarray(d['Qgmin']).tolist()}")
print(f"raw d['Qgmax'] = {np.asarray(d['Qgmax']).tolist()}")
print()
print(f"{'gen':>4} {'Qg':>10} {'Qgmin':>10} {'Qgmax':>10} {'min-slack':>12} {'max-slack':>12}")
n = min(len(Qg), len(Qgmin))
for i in range(len(Qgmin)):
    qg_i = Qg[i] if i < len(Qg) else float("nan")
    lo = Qgmin[i] - qg_i
    hi = qg_i - Qgmax[i]
    print(f"{i:>4} {qg_i:>10.3f} {Qgmin[i]:>10.3f} {Qgmax[i]:>10.3f} {lo:>12.3f} {hi:>12.3f}")
print()
print("NOTE: if len(Qgmin) > len(Qg), the extra rows are the dcline DUMMY gens")
print("(their Qg is not in the real-gen Qg result), which would explain a huge")
print("one-sided slack from comparing a dummy bound against a misaligned/zero Qg.")
