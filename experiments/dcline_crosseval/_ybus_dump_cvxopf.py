r"""Dump cvxopf Ybus for case9_dcline (cvxopf-internal order) + ext_to_int.
Stage 1 of the Ybus-agreement check. Writes results/ybus_cvxopf.json."""
import warnings, json
from pathlib import Path
import numpy as np
from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf
RES = Path(__file__).resolve().parent / "results"
case = case9_dcline()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    links = hvdc_from_dcline(case["dcline"])
    b = build_opf(case, formulation="ac", hvdc=links)
Y = np.asarray(b.data["Ybus"])
e2i = {int(k): int(v) for k, v in b.data["ext_to_int"].items()}
out = {"ext_to_int": e2i, "Y_real": Y.real.tolist(), "Y_imag": Y.imag.tolist()}
(RES / "ybus_cvxopf.json").write_text(json.dumps(out))
print("cvxopf Ybus shape", Y.shape, "nnz", int(np.count_nonzero(Y)))
