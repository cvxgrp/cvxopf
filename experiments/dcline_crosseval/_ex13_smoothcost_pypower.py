# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""EX13 (Pypower side): neutralized case9_dcline solve with case9's SMOOTH
quadratic generator costs instead of t_case9_dcline's mixed PWL/poly costs.

Hypothesis (user, 2026-07-20): the PWL cost is what tripped Pypower into the
suboptimal P* (EX12 proved P* suboptimal). With smooth quadratic costs the two
model+solve paths (cvxopf AC vs neutralized Pypower) should converge on roughly
the same generator dispatch and DC-line flows -- unlike the PWL baseline where
C* and P* diverged sharply (Pg2 220 vs 123; link1 p_in 2 vs 10). This CLARIFIES
the DNLP-tractability thesis ([[dnlp-canonicalization-tractability-thesis]]):
DNLP's epigraph-style reformulation (cf. L1->epigraph) helps most on nonsmooth
/ kinked landscapes, so smooth-cost agreement localizes the effect to the PWL
regime rather than refuting it.

One-variable design: loss0 is held IDENTICAL on both sides (NOT neutralized).
Pypower carries loss0 natively via _make_coupling_userfcn ((1-L1)*Pgf + Pgt ==
-L0/baseMVA); the cvxopf side (companion script) imposes it via the EX12
known-direction affine graft. The ONLY thing changed vs the PWL baseline is the
generator cost representation.

SMOOTH gencost is HARDCODED below (keeps this script pypower-pure, matching the
EX7a / _ex_crosseval sandbox convention). Source: cvxopf.testcases.case9's
gencost, rows for the 3 real generators. MODEL=2 (polynomial), NCOST=3:
cost = c2*Pg^2 + c1*Pg + c0, layout [MODEL, STARTUP, SHUTDOWN, NCOST, c2, c1, c0].

Gen-row alignment: t_case9_dcline is the 9-bus case with buses renamed; its 3
real-gen rows correspond 1:1, in order, to case9's 3 gens. Asserted below via
gen count before the swap.

Sandbox script (isolated pypower env). Run: uv run _ex13_smoothcost_pypower.py
Writes results/ex13_pstar_smooth.json. Read back before interpreting.
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
from pypower.idx_bus import BUS_I, VM, VA
from pypower.idx_gen import GEN_BUS, PG, QG
from pypower.ext2int import ext2int
from pypower.makeYbus import makeYbus
from pypower.t.t_case9_dcline import t_case9_dcline

_here = Path(__file__).resolve().parent
RES = _here / "results"

# reuse EX1's neutralized solve (now with an optional gencost= override)
_spec = importlib.util.spec_from_file_location("ex_crosseval", _here / "_ex_crosseval.py")
ex1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex1)

# --- case9 SMOOTH quadratic gencost (hardcoded; source: cvxopf.testcases.case9) ---
# layout: [MODEL=2, STARTUP, SHUTDOWN, NCOST=3, c2, c1, c0]
CASE9_GENCOST = np.array(
    [
        [2.0, 1500.0, 0.0, 3.0, 0.11, 5.0, 150.0],
        [2.0, 2000.0, 0.0, 3.0, 0.085, 1.2, 600.0],
        [2.0, 3000.0, 0.0, 3.0, 0.1225, 1.0, 335.0],
    ]
)

# sanity: t_case9_dcline has exactly 3 real gens (the dcline dummies are added
# later inside _dcline_to_gens), so the 3-row smooth override aligns 1:1.
_raw = t_case9_dcline()
assert _raw["gen"].shape[0] == 3, f"expected 3 real gens, got {_raw['gen'].shape[0]}"

res, orig = ex1.solve_neutralized(gencost=CASE9_GENCOST)
assert bool(res["success"]), "neutralized smooth-cost solve did not converge"
obj = float(res["f"])

# --- gens: rows 0..2 real; 3.. dummy DC terminals (3 from-gens, then 3 to-gens) ---
gen = res["gen"]
n_real = 3
ndc = (gen.shape[0] - n_real) // 2
real_Pg = gen[:n_real, PG].tolist()
real_Qg = gen[:n_real, QG].tolist()
from_dummy_Pg = gen[n_real : n_real + ndc, PG].tolist()
to_dummy_Pg = gen[n_real + ndc : n_real + 2 * ndc, PG].tolist()
dummy_Qg = gen[n_real:, QG].tolist()
gen_bus = gen[:, GEN_BUS].astype(int).tolist()  # guard for the from/to split

bus_ids = res["bus"][:, BUS_I].astype(int).tolist()
Vm = res["bus"][:, VM].tolist()
Va_deg = res["bus"][:, VA].tolist()

# Pypower's OWN Ybus (internal order) + i2e, for a self-contained cvxopf-side check
ppc = ext2int(t_case9_dcline())
Ypp, _, _ = makeYbus(ppc["baseMVA"], ppc["bus"], ppc["branch"])
Ypp = np.asarray(Ypp.todense())
i2e_pp = ppc["order"]["bus"]["i2e"].astype(int).tolist()

out = {
    "obj": obj,
    "gencost_model": "case9 smooth quadratic (MODEL=2)",
    "real_Pg": [float(x) for x in real_Pg],
    "real_Qg": [float(x) for x in real_Qg],
    "from_dummy_Pg": [float(x) for x in from_dummy_Pg],
    "to_dummy_Pg": [float(x) for x in to_dummy_Pg],
    "dummy_Qg": [float(x) for x in dummy_Qg],
    "gen_bus": gen_bus,
    "bus_ids": bus_ids,
    "Vm": [float(x) for x in Vm],
    "Va_deg": [float(x) for x in Va_deg],
    "ndc": int(ndc),
    "pypower_Ybus_real": np.real(Ypp).tolist(),
    "pypower_Ybus_imag": np.imag(Ypp).tolist(),
    "pypower_Ybus_i2e": i2e_pp,
}
(RES / "ex13_pstar_smooth.json").write_text(json.dumps(out, indent=2))
print("EX13 wrote", RES / "ex13_pstar_smooth.json")
print("EX13 obj (smooth, Pypower):", round(obj, 4))
print("EX13 real_Pg     :", np.round(real_Pg, 4).tolist())
print("EX13 from_dummy_Pg:", np.round(from_dummy_Pg, 4).tolist(), "(raw Pg = -p_in)")
print("EX13 to_dummy_Pg  :", np.round(to_dummy_Pg, 4).tolist(), "(raw Pg = +p_out)")
print("EX13 gen_bus     :", gen_bus)
"""For comparison, the PWL baseline P* was: real_Pg [90, 106.14, 123.48],
p_in [1, 10, 10]. If smooth real_Pg lands near cvxopf's C*_smooth (companion
script) instead, the PWL cost was the tripwire."""
