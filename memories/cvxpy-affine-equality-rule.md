---
name: cvxpy-affine-equality-rule
description: CVXPY/DCP rule — equality constraints must be affine on both sides; convex atoms (cp.abs, cp.square) are legal only in the objective and inequalities
metadata:
  type: reference
---

In CVXPY, an **equality constraint must be affine on both sides**. A convex atom (`cp.abs`, `cp.square`, ...) in an equality fails the DCP check and is rejected — there is no epigraph trick for an equality (epigraph reformulation only moves a convex term into an *inequality*). The same expression is fine in the **objective** or an **inequality**.

Corollary hit during HVDC planning: a loss law like `p_out == coeff * cp.abs(p_in)` is illegal both convex (non-affine equality → CLARABEL/DCP rejects) and nonconvex (`abs` non-smooth at 0 → IPOPT/DNLP invalid). Fix: fix the flow direction *before* building the problem so the branch coefficient is a known numpy scalar, making `p_out == coeff * p_in` affine. Also: `(|x|)^2 == x^2`, so a quadratic cost on a magnitude uses `cp.square(p_in)` directly (no abs).

See also the existing repo convention (CLAUDE.md): `poly_cost_expr` uses an explicit monomial sum, not Horner, so the DCP checker accepts quadratic costs. Related planning context: [[hvdc-plan-mvp-scope]].