"""Probe: which equality constraints involve BOTH p_in and p_out?

The EX12 locator matched 'involves both p_in and p_out', but the NODAL BALANCE
constraint also contains both HVDC vars (via Ch_from@p_in + Ch_to@p_out). If the
locator hit nodal balance instead of the coupling, deleting it explains the
local-infeasibility. This enumerates every matching equality so the locator can
be made specific (the coupling is the small one: only p_in and p_out, no p/q/V).
"""
import warnings

import cvxpy as cp

from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf
from cvxopf.testcases.case9_dcline import case9_dcline

case = case9_dcline()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    links = hvdc_from_dcline(case["dcline"])
    b = build_opf(case, formulation="ac", hvdc=links)

pin = b.variables["p_hvdc_in"]
pout = b.variables["p_hvdc_out"]
print(f"p_in.id={pin.id} shape={pin.shape}  p_out.id={pout.id} shape={pout.shape}")
print(f"total constraints: {len(b.prob.constraints)}")
for i, con in enumerate(b.prob.constraints):
    if not isinstance(con, cp.constraints.Equality):
        continue
    vids = {v.id for v in con.variables()}
    if pin.id in vids and pout.id in vids:
        print(
            f"[{i}] nvars={len(con.variables())} shape={con.shape} "
            f"only_hvdc={vids == {pin.id, pout.id}}  {repr(con)[:80]}"
        )
