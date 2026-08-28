# M14b vectorized horizon-assembly protocol

## Status and authority

M14b is complete. It is authorized by the immutable M14a legacy baseline and
the M14a.1 leaf-bound qualification record. The typed horizon, one-call
assembly, aggregation/publication, compatibility-result projection, and
component-box qualification slices are complete. M14b keeps the public
stepwise/CPP path available and unchanged by default, and hands the frozen
assembly contract to M14c.

The authoritative component-box record was executed from commit
`028b46e2f0af16fbff90a9bc991770f0267280a5`, has source fingerprint
`45e2e91e95594b3418065c256ef476c70d14f7f679d1606a71f2fc34c57ac03b`, and
is promoted as `M14B_COMPONENT_BOX_RESULTS.json` with SHA-256
`2bdf5eda5d545a49e66afd01eeca7083bd3d81d54dfb5604ba62667c270815bf`
in result commit `252b8c8d0cf243c4661c7aa0700f28f039afb8d2`.

The frozen representation decisions are:

- lossy DC: leaf bounds are authorized for dispatchable `Pg` and network
  `p_flows`;
- single-node DC: leaf bounds are authorized for dispatchable `Pg`;
- lossy DC: leaf bounds are also authorized for storage real power and SoC,
  nondispatchable real power, load-shed fraction, and HVDC from-terminal power;
- single-node DC: leaf bounds are also authorized for storage real power and
  SoC, nondispatchable real power, and load-shed fraction;
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

Convex vectorized paths use SCIPY canonicalization. Convex stepwise paths
continue to use CPP, while AC remains on its separately gated DNLP/IPOPT path.
Backend selection is explicit build provenance, not an automatic heuristic.

## Public result compatibility

Every vectorized result source carries an immutable typed projection in its
`OPFBuild`. The projection declares the source's internal native shape, public
native shape, and whether it is interval-valued, boundary-valued, or a
once-per-horizon quantity. Extraction never infers a temporal axis from a
field name or an observed shape.

Interval values move the final axis to the first public axis exactly once.
Storage SoC explicitly projects the `(n_storage, T + 1)` boundary state to the
existing `(T, n_storage)` post-step result by omitting the retained initial
boundary. AC column variables may explicitly flatten their native singleton
axis while preserving the time axis; arbitrary reshaping or axis permutation
is rejected. Component model expressions are interval-valued, while terminal
expressions and integrated component costs are horizon-valued. Horizon totals
remain native scalars or arrays. In particular, `T=1` multistep results remain
unsqueezed.

The projection registry is source-specific because a variable and a reporting
expression may deliberately share a public name. Missing declarations and
required sources, including on an unavailable-primal path, and shape drift are
extraction errors. Publication and extraction retain the one horizon CVXPY
object and do not reconstruct a length-`T` object list. The existing stepwise
extraction path remains unchanged.

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

The implemented matrix contains seven paired gates and emits nine local box
decisions because each storage gate qualifies power and SoC separately. Every
arm records an encoding-independent fixture digest, complete public component
results, actual component costs, independently reconstructed component/network
residuals, source and canonical structure, binding probes, timing, RSS, and
solver classification. Synthetic preferences used only to select informative
probe points are labeled and retained separately from component costs; in
particular, nondispatchable generation retains an empty component-cost schema.
Solver failures remain complete arm records with unavailable solver statistics
rather than aborting the matrix. Each authoritative arm runs in a fresh process
under a stable execution commit and source fingerprint. The immutable promoted
record is `M14B_COMPONENT_BOX_RESULTS.json`.

The selection rule is fixed before execution: a pair selects leaf bounds only
when both arms and all binding probes are accepted and their objective, costs,
and public results agree within the frozen tolerances. Any neutral, failed, or
mismatched leaf arm selects explicit inequalities for that formulation/family
without blocking other gates or M14b. Preliminary dirty-tree runs are
diagnostic only and cannot change the production decision registry.

The authoritative run accepted all seven formulation-local pairs and qualified
all nine formulation/family decisions for leaf bounds. This result applies only
to the tested lossy-DC and single-node component boxes under SCIPY
canonicalization and CLARABEL. It does not authorize AC leaf-bound migration
and is not a comparative runtime or RSS result.

## Delivery order

1. **Complete:** freeze typed temporal-field and variable-representation
   schemas.
2. **Complete:** add vectorized component request/contribution contracts
   without calling scalar hooks `T` times.
3. **Complete:** implement compatibility publication and typed extraction
   without recreating a length-`T` CVXPY object list.
4. **Complete:** the focused component-box matrix was executed from a clean
   commit, its immutable record was promoted, and all nine local decisions were
   frozen.
5. **Complete:** the frozen assembly contract is handed to the open M14c
   vectorized lossy-DC stage.

Structural tests cover time-last shapes, `T=1`, static broadcasting, interval
identity alignment, boundary indexing, DCP validity of every component term,
duplicate-name rejection, explicit backend selection, and unchanged stepwise
behavior.
