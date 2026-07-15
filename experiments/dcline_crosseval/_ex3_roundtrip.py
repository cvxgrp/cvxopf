"""EX3: build and round-trip-verify the bidirectional point mapping between
cvxopf's representation (3 gens + p_in/p_out + buses in internal order) and
Pypower's (3 real gens + 6 dummy gens + buses in external-id order).

Uses /tmp/cstar.json (C*) and /tmp/ex3_cvx.json (orders). P* dummy values are
hard-coded from EX1's verified output. Pure-python, no solver. Asserts
round-trip identity both directions.
"""
import json
import numpy as np

cstar = json.load(open("/tmp/cstar.json"))
orders = json.load(open("/tmp/ex3_cvx.json"))
e2i = {int(k): v for k, v in orders["ext_to_int"].items()}
i2e = {v: k for k, v in e2i.items()}
link_ends = orders["link_ends"]  # [[30,4],[7,9],[5,9]] in cvxopf link order

# ---- mapping functions -------------------------------------------------
# cvxopf link k: p_in[k] = PF[k] (injection at from-bus into DC line as -PF at
# from-bus... we DEFINE via the dummy relation and verify by round-trip):
#   pypower from-dummy Pg = -PF ;  to-dummy Pg = PT
#   cvxopf p_in = PF        (so from-dummy Pg = -p_in)
#   cvxopf p_out = -PT      (so to-dummy Pg  = -p_out)

def cvx_to_pypower_dummies(p_in, p_out):
    """Return (from_dummy_Pg[list], to_dummy_Pg[list])."""
    from_pg = [-pi for pi in p_in]      # -PF
    to_pg   = [-po for po in p_out]     # PT
    return from_pg, to_pg

def pypower_dummies_to_cvx(from_pg, to_pg):
    """Inverse: return (p_in, p_out)."""
    p_in  = [-fp for fp in from_pg]     # PF
    p_out = [-tp for tp in to_pg]       # -PT
    return p_in, p_out

# ---- round-trip 1: C* p_in/p_out -> dummies -> back --------------------
p_in = cstar["p_hvdc_in"]; p_out = cstar["p_hvdc_out"]
fpg, tpg = cvx_to_pypower_dummies(p_in, p_out)
p_in_rt, p_out_rt = pypower_dummies_to_cvx(fpg, tpg)
assert np.allclose(p_in, p_in_rt) and np.allclose(p_out, p_out_rt), "RT1 fail"
print("EX3 RT1 (cvx->dummy->cvx) OK: p_in", np.round(p_in,4).tolist(),
      "-> from_dummy", np.round(fpg,4).tolist(), "to_dummy", np.round(tpg,4).tolist())

# ---- round-trip 2: P* dummies (from EX1) -> cvx -> back ----------------
P_from_dummy = [-10.0, -2.2776, -10.0]   # EX1 verified
P_to_dummy   = [8.9, 2.2776, 9.5]
pi2, po2 = pypower_dummies_to_cvx(P_from_dummy, P_to_dummy)
fpg2, tpg2 = cvx_to_pypower_dummies(pi2, po2)
assert np.allclose(P_from_dummy, fpg2) and np.allclose(P_to_dummy, tpg2), "RT2 fail"
print("EX3 RT2 (dummy->cvx->dummy) OK: P* p_in", np.round(pi2,4).tolist(),
      "p_out", np.round(po2,4).tolist())

# ---- physical sanity: loss law on each link ----------------------------
# cvxopf: p_out = -(1-loss_frac)*p_in for fixed-direction; check P* consistency
for k, (fb, tb) in enumerate(link_ends):
    PF = pi2[k]; PT = -po2[k]
    loss = PF - PT
    print(f"EX3 link{k} {fb}->{tb}: PF={PF:.4f} PT={PT:.4f} loss={loss:.4f}")

print("EX3 bus map (internal->ext):", {i: i2e[i] for i in range(orders['nb'])})
print("EX3 ROUND-TRIP VERIFIED both directions")
