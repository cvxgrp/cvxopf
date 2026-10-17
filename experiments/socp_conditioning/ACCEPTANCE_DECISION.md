# Pending solver independent acceptance decision

Owner discussion, 2026-10-06. Before production adoption, decide whether
cvxopf should accept a returned solution using an independent numerical
assessment rather than requiring a native success label. No new acceptance
policy is selected here; existing experiment gates and historical records
remain unchanged.

Keep four separate assessments:

- Primal feasibility and accounting, independently reconstructed in original
  physical units, with explicit tolerances and complete finite result fields.
- Optimality evidence: independently checked dual feasibility, stationarity,
  complementarity and gap, with correctly mapped objective offsets and scaling.
  Distinguish approximate numerical evidence from a rigorous bound/certificate.
- The original native termination status and diagnostics, never overwritten.
- For SOCP, relaxation feasibility versus full AC realizability, assessed
  separately. For nonconvex AC, local stationarity is not global optimality.

Freeze intended use, tolerances and rules prospectively: a usable feasible
dispatch need not be an adequate lower bound or infeasibility certificate.
Specify handling of incomplete dual evidence and solver exceptions, and test
that a favorable native label cannot override failed physical checks.

The immediate motivation is concrete. On the prepared fixed-load surplus case,
the pre-SuperLU QDLDL result and the accepted SuperLU result both pass physical
residual checks. Their objective difference is 1.11e-8 cost units; maximum
battery power/SoC differences are 0.0173 MW/MWh, and renewable real-power
difference is 0.0249 MW. Native relative gaps differ substantially, about
9.36e-8 versus 6.74e-10. This supports separating practical primal usefulness
from requested optimality accuracy, not assuming every rejected solve is good.

References: [practical check](PRACTICAL_LU_REPORT.md),
[three solver diagnostic](TERMINATION_REPORT.md). The latter includes COPT
reporting Optimal while failing original-unit checks. Revisit this decision
when packaging the numerical preparation/backend options into cvxopf.
