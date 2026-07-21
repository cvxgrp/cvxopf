# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""EX7a: regenerate P* as a first-class structured artifact (pstar_full.json).

Mirror of _ex6c_cstar_full.py, but for the neutralized-Pypower optimum P*.
EX6 had cstar_full.json (cvxopf's own p_net/q_net as an independent guardrail
witness); EX7 needs the same for P*. ex1_pstar.txt is prose only, so this
re-solves the SAME neutralized model (reusing _ex_crosseval.solve_neutralized,
no re-derivation of the neutralization) and writes P*'s full solved state.

Gate: assert obj == 6249.8659 before writing, so we know we regenerated THE P*
(the fully-neutralized basin), not a lookalike.

Records, alongside the dispatch, Pypower's OWN solved Ybus (real/imag) and its
internal->external bus map i2e, so EX7b (main env) can double-check the Ybus
agreement self-contained rather than relying only on the prior ybus_compare.txt.

Sandbox script (isolated pypower env). Run: uv run _ex7a_pstar_full.py
Writes results/pstar_full.json. Read back before interpreting.
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

# reuse EX1's neutralized solve verbatim (no re-derivation of the neutralization)
_spec = importlib.util.spec_from_file_location(
    "ex_crosseval", _here / "_ex_crosseval.py"
)
ex1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex1)

P_STAR_OBJ = 6249.8659

res, orig = ex1.solve_neutralized()
assert bool(res["success"]), "neutralized solve did not converge"
obj = float(res["f"])
assert abs(obj - P_STAR_OBJ) < 1e-2, (
    f"regenerated obj {obj:.4f} != P* {P_STAR_OBJ}; wrong basin, refusing to write"
)

# --- gens: rows 0..2 are the 3 real gens; 3.. are the dummy DC terminals ---
# (appended as np.r_[gen, fg, tg] in _dcline_to_gens: 3 from-gens then 3 to-gens)
gen = res["gen"]
n_real = 3
ndc = (gen.shape[0] - n_real) // 2
real_Pg = gen[:n_real, PG].tolist()
real_Qg = gen[:n_real, QG].tolist()
from_dummy_Pg = gen[n_real : n_real + ndc, PG].tolist()
to_dummy_Pg = gen[n_real + ndc : n_real + 2 * ndc, PG].tolist()
dummy_Qg = gen[n_real:, QG].tolist()
# record the gen bus column so EX7b can VERIFY the from/to split rather than
# trust the positional slice.
gen_bus = gen[:, GEN_BUS].astype(int).tolist()

# --- buses in solved (external-id) order ---
bus_ids = res["bus"][:, BUS_I].astype(int).tolist()
Vm = res["bus"][:, VM].tolist()
Va_deg = res["bus"][:, VA].tolist()

# --- Pypower's OWN Ybus (built on internal order) + its i2e map ---
ppc = ext2int(t_case9_dcline())
Ypp, _, _ = makeYbus(ppc["baseMVA"], ppc["bus"], ppc["branch"])
Ypp = np.asarray(Ypp.todense())
i2e_pp = ppc["order"]["bus"]["i2e"].astype(int).tolist()

out = {
    "obj": obj,
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
(RES / "pstar_full.json").write_text(json.dumps(out, indent=2))
print("EX7a wrote", RES / "pstar_full.json")
print("EX7a obj:", round(obj, 4))
print("EX7a real_Pg:", np.round(real_Pg, 4).tolist())
print("EX7a from_dummy_Pg:", np.round(from_dummy_Pg, 4).tolist())
print("EX7a to_dummy_Pg:", np.round(to_dummy_Pg, 4).tolist())
print("EX7a gen_bus:", gen_bus)
print("EX7a bus_ids:", bus_ids)
