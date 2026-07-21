"""EX4+EX5: evaluate both optima under the shared objective (3 real-gen cost,
DC zero-cost) via direct curve evaluation; compare to each solver's reported
objective at its own point. Uses RELATIVE tolerance. Writes durable result to
results/ex45_objective.txt.

Shared objective (case9_dcline gencost, DC lines zero-cost):
  gen0 PWL (0,0)-(100,2500)-(200,5500)-(250,7250)
  gen1 poly 24.035*P - 403.5
  gen2 PWL (0,0)-(200,3000)-(300,5000)
"""
import json
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
RES = HERE / "results"

def gen0(P):
    return np.interp(P, [0, 100, 200, 250], [0, 2500, 5500, 7250])
def gen1(P):
    return 24.035 * P - 403.5
def gen2(P):
    return np.interp(P, [0, 200, 300], [0, 3000, 5000])
def total_cost(Pg):
    return float(gen0(Pg[0]) + gen1(Pg[1]) + gen2(Pg[2]))

cstar = json.load(open(RES / "cstar.json"))
C_Pg = cstar["Pg"]; C_reported = cstar["obj"]
P_Pg = [90.0, 101.9744, 127.7269]   # EX1 verified
P_reported = 6213.3937

C_direct = total_cost(C_Pg)
P_direct = total_cost(P_Pg)
RTOL = 1e-4  # relative; ~0.6 on a 6000 objective. solver/PWL noise is ~0.03.

lines = []
lines.append("# EX4/EX5 objective cross-evaluation")
lines.append(f"C_Pg = {C_Pg}")
lines.append(f"P_Pg = {P_Pg}")
lines.append("")
lines.append(f"EX4  C* direct-curve cost   = {C_direct:.4f}")
lines.append(f"EX4  cvxopf reported at C*  = {C_reported:.4f}")
lines.append(f"EX4  abs diff = {C_direct - C_reported:.6f}  rel = {abs(C_direct-C_reported)/abs(C_reported):.2e}")
lines.append("")
lines.append(f"EX5  P* direct-curve cost   = {P_direct:.4f}")
lines.append(f"EX5  pypower reported at P* = {P_reported:.4f}")
lines.append(f"EX5  abs diff = {P_direct - P_reported:.6f}  rel = {abs(P_direct-P_reported)/abs(P_reported):.2e}")
lines.append("")
agree = (abs(C_direct-C_reported)/abs(C_reported) < RTOL and
         abs(P_direct-P_reported)/abs(P_reported) < RTOL)
lines.append(f"VERDICT (rtol={RTOL:.0e}): objectives " + ("AGREE" if agree else "DISAGREE"))
lines.append(f"cross: C* costs {C_direct:.2f} vs P* costs {P_direct:.2f} under SAME objective")
lines.append(f"       -> cvxopf point cheaper by {P_direct - C_direct:.2f}")

text = "\n".join(lines) + "\n"
(RES / "ex45_objective.txt").write_text(text)
print(text)
