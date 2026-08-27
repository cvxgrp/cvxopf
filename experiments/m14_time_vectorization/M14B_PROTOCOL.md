# M14b vectorized horizon-assembly protocol

## Status and authority

M14b is open. It is authorized by the immutable M14a legacy baseline and the
M14a.1 leaf-bound qualification record. M14b introduces the internal
time-last horizon assembly contract while keeping the public stepwise/CPP path
available and unchanged by default.

The frozen representation decisions are:

- lossy DC: leaf bounds are authorized for dispatchable `Pg` and network
  `p_flows`;
- single-node DC: leaf bounds are authorized for dispatchable `Pg`;
- AC: M14a.1 is isolated compatibility evidence only. No new AC leaf-bound
  migration is authorized. The existing production voltage leaf attribute is
  preserved, while generator and component operating boxes remain explicit;
  and
- all non-box equations and coupled feasible sets remain explicit constraints.

Every retained build records the temporal assembly mode, canonicalization
backend, and representation selected for each variable family.

## Temporal assembly contract

The vectorized path owns one CVXPY object per logical horizon variable or
expression, with time on the final axis. Per-step scalar, vector, and matrix
objects become `(T,)`, `(n, T)`, and `(m, n, T)` respectively. Storage SoC is
a boundary variable with shape `(n_storage, T + 1)`. Public inputs and results
remain time first; preparation and extraction transpose the time axis exactly
once.

Every prepared field declares one of `static`, `interval`, or `boundary`.
Static data remain native-size and may be exposed through zero-stride broadcast
views. Interval data append `T`; boundary data append `T + 1`. Temporal class
is schema-owned and is never inferred from coincidentally constant values.
Lower and upper box faces declare temporal class independently; mixed boxes
retain a zero-stride static face while moving only the dynamic face from
time-first input to time-last model layout.

The vectorized path uses SCIPY canonicalization. The stepwise path continues to
use CPP. Backend selection is explicit provenance, not an automatic heuristic.

## Component-specific box qualification

M14a.1 qualified formulation-owned generator and network boxes only. The
following component boxes are not implicitly covered:

| Family | Convex formulations requiring a focused gate | Required cases |
|---|---|---|
| Storage `b` and `soc` | lossy DC and single-node DC separately | lower/upper power and energy faces; recurrence; initial state; equality, shortfall, and soft terminal policies |
| Nondispatchable `p_nd` | lossy DC and single-node DC separately | zero, availability-limited, and rating-limited coordinates; time-varying availability; identity alignment |
| HVDC `p_hvdc_in` | lossy DC only | positive-only, negative-only, zero-straddling, degenerate, and time-varying boxes; unchanged affine loss branch |
| `load_shed_fraction` | lossy DC and single-node DC separately | ineligible zero-width entries; binding upper faces; time-varying eligibility; served-load and cost reconstruction |

Until its gate passes, each family uses explicit vectorized inequalities. A
failed or neutral leaf-bound result does not block M14b: explicit inequalities
are a valid vectorized representation.

AC component boxes remain explicit during M14. AC storage real power is part of
the coupled `b`/`b_q` inverter circle and is not itself a box. Single-node HVDC
is an intentional null capability and has no variable to qualify. Inverter
circles, branch apparent-power limits, network equations, storage recurrence
and terminal obligations, and HVDC coupling are coupled or equality
constraints, not leaf-bound candidates.

Each focused gate must compare explicit and leaf encodings with identical
prepared arrays, equations, costs, solver configuration, and SCIPY backend. It
must retain binding-face evidence, public results, independent residuals,
canonical dimensions/nonzeros, solver classification, and a local selection
decision. No convex result authorizes AC.

## Delivery order

1. Freeze typed temporal-field and variable-representation schemas.
2. Add vectorized component request/contribution contracts without calling
   scalar hooks `T` times.
3. Implement compatibility publication and extraction without recreating a
   length-`T` CVXPY object list.
4. Run the focused component-box gates and freeze their local decisions.
5. Hand the completed assembly contract to M14c's vectorized lossy-DC builder.

Structural tests cover time-last shapes, `T=1`, static broadcasting, interval
identity alignment, boundary indexing, DCP validity of every component term,
duplicate-name rejection, explicit backend selection, and unchanged stepwise
behavior.
