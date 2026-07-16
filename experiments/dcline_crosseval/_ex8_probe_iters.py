# /// script
# requires-python = ">=3.10"
# dependencies = ["pypower==5.1.19", "numpy==2.2.6", "scipy==1.18.0"]
# ///
"""Throwaway: what iteration/convergence info does runopf's result expose, and
does it DIFFER between a cold start and a warm start seeded at the optimum P*?

If iteration counts are ~identical, the seed is inert (warm-start did diddly at
the solver level) and EX8 is void. If seed-at-optimum converges in ~0-1 iters
vs many for cold, the seed bites.
"""
import importlib.util
import json
from pathlib import Path

import numpy as np

_here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ex", _here / "_ex_crosseval.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def show(tag, res):
    print(f"=== {tag} ===")
    print("top keys:", list(res.keys()))
    raw = res.get("raw", {})
    print("raw keys:", list(raw.keys()) if isinstance(raw, dict) else type(raw))
    out = raw.get("output", {}) if isinstance(raw, dict) else {}
    if isinstance(out, dict):
        for k, v in out.items():
            if isinstance(v, dict):
                print(f"  output[{k}] -> dict keys {list(v.keys())}")
            elif hasattr(v, "shape"):
                print(f"  output[{k}] -> array {v.shape}")
            else:
                print(f"  output[{k}] = {v}")
    # common places pips stashes iteration count
    for path in [("raw", "output", "iterations"), ("raw", "output", "iter")]:
        cur = res
        ok = True
        for p in path:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                ok = False; break
        if ok:
            print(f"  {'.'.join(path)} = {cur}")
    print(f"  et (elapsed) = {res.get('et')}")
    print()


# cold
res_cold, _ = m.solve_neutralized()
show("COLD start", res_cold)

# warm at P*
pv = json.loads((_here / "results" / "pstar_full.json").read_text())
seed = {
    "bus_vm": np.array(pv["Vm"]),
    "bus_va": np.array(pv["Va_deg"]),
    "gen_pg": np.concatenate([pv["real_Pg"], pv["from_dummy_Pg"], pv["to_dummy_Pg"]]),
    "gen_qg": np.concatenate([pv["real_Qg"], pv["dummy_Qg"]]),
}
res_warm, _ = m.solve_neutralized(seed=seed)
show("WARM start (seed = P*)", res_warm)
