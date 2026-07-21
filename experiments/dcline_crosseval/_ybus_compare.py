# /// script
# requires-python = ">=3.10"
# dependencies = ["pypower==5.1.19", "numpy==2.2.6", "scipy==1.18.0"]
# ///
r"""Stage 2: compare cvxopf Ybus vs Pypower Ybus for case9_dcline, matched by
external bus ID. Writes results/ybus_compare.txt."""
import json
from pathlib import Path
import numpy as np
from pypower.t.t_case9_dcline import t_case9_dcline
from pypower.ext2int import ext2int
from pypower.makeYbus import makeYbus
from pypower.idx_bus import BUS_I
RES = Path(__file__).resolve().parent / "results"
cv = json.loads((RES / "ybus_cvxopf.json").read_text())
e2i_cv = {int(k): int(v) for k, v in cv["ext_to_int"].items()}
Ycv = np.array(cv["Y_real"]) + 1j * np.array(cv["Y_imag"])
# pypower Ybus (internal order)
ppc = ext2int(t_case9_dcline())
Ypp, _, _ = makeYbus(ppc["baseMVA"], ppc["bus"], ppc["branch"])
Ypp = np.asarray(Ypp.todense())
# pypower internal->external
i2e_pp = ppc["order"]["bus"]["i2e"].astype(int)
# common order: sorted external ids
ext_sorted = sorted(e2i_cv.keys())
# permutation: for each ext id, its cvxopf-internal idx and pypower-internal idx
e2i_pp = {int(e): i for i, e in enumerate(i2e_pp)}
cv_perm = [e2i_cv[e] for e in ext_sorted]
pp_perm = [e2i_pp[e] for e in ext_sorted]
Ycv_c = Ycv[np.ix_(cv_perm, cv_perm)]
Ypp_c = Ypp[np.ix_(pp_perm, pp_perm)]
diff = np.abs(Ycv_c - Ypp_c)
maxdiff = float(diff.max())
lines = ["# Ybus agreement: cvxopf vs Pypower (case9_dcline), matched by ext id"]
lines.append(f"common ext order: {ext_sorted}")
lines.append(f"max abs diff = {maxdiff:.3e}")
i, j = np.unravel_index(int(diff.argmax()), diff.shape)
lines.append(f"argmax at ext ({ext_sorted[i]},{ext_sorted[j]}): cvxopf={Ycv_c[i,j]:.6f} pypower={Ypp_c[i,j]:.6f}")
verdict = "AGREE (< 1e-9)" if maxdiff < 1e-9 else "DISAGREE"
lines.append(f"VERDICT: {verdict}")
text = chr(10).join(lines) + chr(10)
(RES / "ybus_compare.txt").write_text(text)
print(text)
