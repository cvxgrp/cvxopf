# Case9 AC comparison: pre-solve protocol

This is a **solver-free qualification**, not permission to run the benchmark.
It implements the checks in [the comparison plan](../../plans/dcline-import-and-case9-comparison.md).
The four cells are one Case9 DC-line network crossed with quadratic or mixed
piecewise-linear generator costs and with or without the three active DC links.
Both cost variants use the DC-line case's demand, real-generator limits and
network. They are not stock Case9 comparisons.
Before building either model, check the requested cell against the frozen
generator cost rows and active DC-link source rows.

- One hour of physical operation is modeled. Power is recorded in MW/MVAr,
  voltage in p.u., and unwrapped angles in radians relative to bus 1. Match
  by external bus number, generator source row, branch source row and link
  source row. No slack redispatch is permitted during evaluation.
- CVXOPF disables its thermal ratings and voltage setpoints. PYPOWER retains
  strictly redundant apparent-power rows; their limits are computed from both
  terminal admittances and the voltage upper bounds, not from observed flows.
  Both models keep both endpoints of each selected angle row and the upper
  and lower sloped generator capability rows. PYPOWER's epigraph rows and
  CVXOPF's lifted network definitions are auxiliary representations.
- No converter reactive injection, converter voltage control, or link tariff
  is modeled. To match the assembled PYPOWER representation, the study uses
  the effective −0.000001 MW upper bound on the signed from injection at
  5 → 9. CVXOPF accounts for the 30 → 4 converter's fixed loss with a 1 MW
  nonsheddable withdrawal at bus 4, in addition to the unchanged 315 MW /
  115 MVAr ordinary demand. PYPOWER includes this loss in its coupling row.
  Store gross and net injections separately, including when the net delivery
  is negative at the lower sending bound.
- Verify actual model rows and both native objectives against common
  engineering-unit coordinates. The prescribed points include generator
  endpoints, PWL knots and between-knot dispatches, and intentional
  violations. Derive the minimum permitted cost for each PWL generator
  from the assembled epigraph rows, not merely feasibility of the
  expected cost. Send unequal power on the two links into bus 9 to
  distinguish their source buses. Check the row inventory and types in
  the final CVXOPF problem. Move each receiving terminal by ±1 MW while
  holding everything else fixed; the actual loss equality must register
  exactly 1 MW on that link and zero on the others. These are accounting
  and sensitivity probes, not solved or feasible dispatches.
- The historical controls replay only retained evidence. P* is a positive
  control in its historical PYPOWER target; C* is tested under the historical
  forward-flow interpretation for the bus-9 discrepancy and angle violation.
  The C+ archive retains a rounded from-injection, not a complete vector.
  These controls do not validate or replace prior numerical comparisons.

The pinned PYPOWER environment uses PYPOWER 5.1.19, NumPy 2.2.6 and SciPy
1.18.0. The probe uses PIPS (algorithm 560) with apparent-power branch limits.
It intercepts the driver at the optimizer call, recording the actual start
and options and evaluating the real constraint callbacks and Hessian. It
does not enter PIPS or execute the CVXOPF solve API. These callback options
and starting coordinates are recorded evidence, not approved benchmark
settings.

Exact model identities, signs and coefficients are checked up to roundoff.
The physical gates from the plan are 0.001 MW/MVAr, 0.000001 p.u., and
0.00001 degrees. Same-point objectives use an absolute tolerance of
0.0001 plus 0.000001 times the magnitude of the reference cost. These
tolerances do not require the deliberately unsolved probes to be feasible.

Before authorizing the eight benchmark solves, review this preflight and its
receipts, then freeze the benchmark solver options and start rules, timing
and resource policy, and a separate material threshold for cost differences
between distinct accepted dispatches. A preflight pass is not an optimization
result, a certificate, or launch authority.
