# Milestone 4 — AC branch terminal flows and thermal limits

**Status:** approved for implementation — modeling and API decisions locked
2026-07-30

## 1. Goal

Complete the AC network operating set by computing the complex power entering
every in-service AC branch at both terminals and, when enabled, enforcing the
MATPOWER `rateA` apparent-power rating at both ends:

```math
\left(\frac{P_{f,e}}{S_{\max,e}}\right)^2
+ \left(\frac{Q_{f,e}}{S_{\max,e}}\right)^2 \leq 1,
\qquad
\left(\frac{P_{t,e}}{S_{\max,e}}\right)^2
+ \left(\frac{Q_{t,e}}{S_{\max,e}}\right)^2 \leq 1,
```

where $S_{\max,e}=\operatorname{rateA}_e/\operatorname{baseMVA}$ in the
per-unit model.

The implementation must:

- reproduce the MATPOWER branch model, including series impedance, line
  charging, off-nominal tap ratios, phase shifts, branch status, and distinct
  from- and to-terminal flows;
- preserve individual flows for parallel branches;
- work identically with sparse and dense AC `P`/`Q` representations;
- support single-step, multistep, and intentional multistep `T=1` builds;
- expose terminal flows through the stable result interface whether or not
  limits are enforced;
- retain formulation ownership of network physics and avoid introducing a
  component adapter for AC branches; and
- provide the AC network-executability prerequisite for Milestone 17.

This milestone implements the standard apparent-power interpretation of
`rateA`. Active-power-only, current-magnitude, soft, contingency, emergency,
and angle-difference limits are separate model choices and are out of scope.

## 2. Scientific model

### 2.1 Branch terminal admittances

For branch $e$ from bus $f$ to bus $t$, let

```math
\begin{bmatrix} I_{f,e} \\ I_{t,e} \end{bmatrix}
=
\begin{bmatrix}
Y_{ff,e} & Y_{ft,e} \\
Y_{tf,e} & Y_{tt,e}
\end{bmatrix}
\begin{bmatrix} V_f \\ V_t \end{bmatrix}.
```

Using the existing MATPOWER conventions in `make_ybus_matpower`,

```math
\begin{aligned}
y_e &= \frac{1}{r_e + \mathrm{j}x_e}, &
y_{sh,e} &= \mathrm{j}\frac{b_e}{2}, \\
\tau_e &= t_e \exp(\mathrm{j}\phi_e), &
Y_{ff,e} &= \frac{y_e+y_{sh,e}}{\tau_e\overline{\tau_e}}, \\
Y_{ft,e} &= -\frac{y_e}{\overline{\tau_e}}, &
Y_{tf,e} &= -\frac{y_e}{\tau_e}, \\
Y_{tt,e} &= y_e+y_{sh,e}.
\end{aligned}
```

A zero tap entry means $t_e=1$, and the phase-shift angle is converted from
degrees to radians. An out-of-service branch has zero terminal admittances and
is never constrained.

The terminal complex powers are

```math
S_{f,e}=V_f\overline{I_{f,e}}=P_{f,e}+\mathrm{j}Q_{f,e},
\qquad
S_{t,e}=V_t\overline{I_{t,e}}=P_{t,e}+\mathrm{j}Q_{t,e}.
```

Positive terminal power is injection from the terminal bus into the branch.
Consequently, a normal lossy flow from $f$ to $t$ has $P_f>0$,
$P_t<0$, and $P_f+P_t>0$.

### 2.2 Real-valued DNLP expressions

CVXPY/DNLP expressions should remain real-valued. For a coefficient
$Y_{ij}=G_{ij}+\mathrm{j}B_{ij}$, the contribution to terminal power is

```math
\begin{aligned}
P_{ij} &=
v_i v_j\left(G_{ij}\cos(\theta_i-\theta_j)
            +B_{ij}\sin(\theta_i-\theta_j)\right), \\
Q_{ij} &=
v_i v_j\left(G_{ij}\sin(\theta_i-\theta_j)
            -B_{ij}\cos(\theta_i-\theta_j)\right).
\end{aligned}
```

The self terms use $i=j$; the cross terms use the branch-specific
`Yft` or `Ytf`. This gives four scalar expressions per branch:
`p_from`, `q_from`, `p_to`, and `q_to`.

The thermal constraints use normalized squared magnitudes and do not
introduce a square root:

```math
\left(P/S_{\max}\right)^2
+\left(Q/S_{\max}\right)^2\leq 1.
```

The unit right-hand side is mathematically equivalent to
$P^2+Q^2\leq S_{\max}^2$, is better scaled across heterogeneous ratings, and
avoids squaring a very large but finite rating. This is important because the
approved project rule treats every finite positive rating as active, including
values at or above `1e10`.

### 2.3 Why nodal `P` and `Q` are not the branch-flow source

The old placeholder proposed deriving branch flows from the AC `P` and `Q`
matrices. That is not a correct general boundary:

- their diagonal terms contain the sum of all incident branch self
  admittances and bus shunts;
- a terminal flow requires the self and cross terms of one particular branch;
- parallel branches share the same bus pair and are aggregated in `Ybus`;
- tap ratios, phase shifts, charging, and losses make the two terminal flows
  distinct.

M4 must therefore use branch-terminal admittances as the authoritative source.
The resulting expressions depend only on bus voltage magnitude and angle, so
they are independent of the sparse-versus-dense nodal `P`/`Q` storage choice.

### 2.4 MATPOWER compatibility

MATPOWER forms branch currents with `Yf` and `Yt`, then constrains apparent
power at both terminals. `rateA=0` means no limit is defined. MATPOWER also
treats `rateA >= 1e10` as unconstrained when selecting limited branches.

M4 deliberately adopts a cleaner project rule rather than reproducing that
large-number sentinel:

- constrain every in-service row with finite `rateA > 0`, including values at
  or above `1e10`;
- retain zero-valued terminal flows for out-of-service rows so result shapes
  continue to follow the case branch table;
- when enforcement is enabled, treat negative or non-finite `rateA` on an
  in-service branch as invalid input;
- do not apply the lossy-DC `branch_limit_sentinel` policy to AC.

This is an intentional MATPOWER compatibility difference. It avoids assigning
a hidden second meaning to a positive finite engineering rating. A dedicated
test must lock the behavior for a rating at or above `1e10`.

Authoritative references:

- MATPOWER `makeYbus`: <https://matpower.org/documentation/ref-manual/legacy/functions/makeYbus.html>
- MATPOWER constrained-branch selection:
  <https://matpower.org/docs/ref/matpower7.1/lib/opf_setup.html>
- MATPOWER AC branch-flow constraint implementation:
  <https://matpower.org/docs/ref/matpower7.1/lib/opf_branch_flow_fcn.html>

### 2.5 Sparsity tolerance and physical consistency

The current AC nodal equations may discard aggregate `Ybus` entries below a
positive `sparsity_tol`. Branch-terminal reporting and limits must always use
the exact branch coefficients. Thresholding individual branch coefficients is
not acceptable: parallel branches can cancel or reinforce in the aggregate,
and independently dropping their terms corrupts circuit-level accounting.

The approved contract is:

- branch-terminal flows always retain exact physical coefficients;
- `enforce_branch_limits=True` requires `sparsity_tol == 0`;
- with enforcement disabled, a positive tolerance remains available for the
  existing approximate nodal model, while exact terminal flows are explicitly
  diagnostic and need not close exactly against the thresholded nodal model.

Stage 0 must quantify whether positive tolerance provides a material
construction or solve benefit and measure the resulting nodal/terminal
inconsistency. Any fallback that permits positive tolerance with enforced
limits requires project-owner review; it must not arise by thresholding branch
coefficients.

## 3. Ownership and layer boundaries

### `network.py`

Own the numerical branch primitive. Add one helper that computes
branch-specific terminal admittances and endpoint indices from the reindexed
MATPOWER case. Refactor `make_ybus_matpower` to assemble `Ybus` from that same
primitive, rather than maintaining a second copy of the branch equations.

The helper contains no CVXPY objects and no rating-policy validation. It
preserves one row per input branch, including explicit zero coefficients for
out-of-service branches. Reassembly of `Ybus` must use duplicate-safe additive
scatter semantics; ordinary advanced-index `+=` is not sufficient for
repeated bus pairs.

### `ac_problem.py`

Own the formulation-specific nonlinear terminal-flow expressions, lifted
representation, and thermal constraints through three explicit
responsibilities:

1. construct the direct `p_from`, `q_from`, `p_to`, and `q_to` voltage
   equations for all branch rows;
2. create the four lifted variables and their defining equalities, reusing the
   direct expressions as the right-hand sides;
3. add both terminal apparent-power constraints for the constrained branch
   index set using those same lifted variables.

These responsibilities may be implemented as small cooperating helpers. They
must not be collapsed into an opaque routine that rebuilds terminal
expressions separately for reporting and enforcement.

These are network expressions, not component contributions. They must not be
added to `_component_adapter.py`, `_component_adapters.py`, or a device module.

### `results.py`

Own engineering-unit extraction. Per-unit terminal powers are published in
`OPFBuild.expressions` and multiplied by `baseMVA` exactly once during result
extraction. Result initialization must retain these keys with `None` when no
usable core primal solution exists.

### Public API

Retain `OPFOptions.enforce_branch_limits` as the switch for applying thermal
constraints. The terminal-flow expressions and results exist independently of
that switch.

`branch_limit_sentinel` remains a lossy-DC-only option in M4. Harmonizing the
DC zero-rating policy with MATPOWER is a separate review item, not a hidden
part of this milestone.

Branch-status validation is structural network-input validation. Because
current code treats any nonzero integer status as active, strict `{0, 1}`
validation is a deliberate cross-formulation hardening change. Stage 0 audits
the bundled data first; after the expected binary convention is confirmed,
the validation lands separately with explicit AC, lossy-DC, and single-node
tests.

Rating validation belongs to AC case preparation, not the admittance helper,
and applies when branch-limit enforcement is enabled. With enforcement
disabled, ratings are inert metadata and do not block terminal-flow reporting.

### Implementation footguns and load-bearing invariants

The following requirements are explicit implementation constraints:

1. **One equation graph, one published variable set.** Construct each step's
   four direct voltage-based terminal-flow expression vectors once and use
   them as the right-hand sides of four lifted-variable equalities. Publish
   and constrain the identical lifted CVXPY variables. Do not rebuild either
   the branch-equation graph or a separate reporting graph.
2. **Start with scalar DNLP indexing.** Use scalar `theta[i, 0]` and `v[i, 0]`
   expressions for each branch, then assemble the branch vectors. Do not use
   gathered nonlinear indexing until the Stage 0 Hessian-analysis spike proves
   it safe.
3. **Handle `nl == 0` explicitly.** When `nl > 0`, create four lifted variables
   and their defining equalities. When `nl == 0`, create no terminal-flow
   variables or equalities and publish four empty constants. Never call
   `cp.hstack([])` or attempt to create `cp.Variable(0)`. Verify single-step
   extraction and multistep `(T, 0)` stacking.
4. **Skip before division.** Test branch status before evaluating
   `1 / (r + j*x)`. An out-of-service row may contain unusable electrical data;
   it produces four exact zero terminal coefficients without impedance
   evaluation.
5. **Apply status once.** Zero terminal coefficients in the numerical branch
   primitive. Do not also multiply terminal expressions by status in the AC
   expression layer.
6. **Preserve branch-table row order.** Admittances, external and internal
   endpoints, status, ratings, expressions, constraints, and results all
   retain original MATPOWER row order. `constrained_branch_indices` selects
   rows for constraint construction without compacting published arrays.
7. **Accumulate every `Ybus` position safely.** Duplicate-safe addition applies
   to `Yff`, `Yft`, `Ytf`, and `Ytt`; parallel circuits can duplicate diagonal
   as well as off-diagonal positions.
8. **Keep bus shunts out of branch flows.** The shared numerical primitive
   returns branch-only terminal admittances. `make_ybus_matpower()` adds bus
   `GS`/`BS` shunts afterward, solely to `Ybus`.
9. **Respect transformer orientation.** `Yft` and `Ytf` are not
   interchangeable for a phase-shifting transformer. Tests include the
   original representation and a deliberately reversed, analytically
   equivalent representation; merely swapping endpoint columns is not assumed
   to produce that equivalent.
10. **Preserve constraint ordering.** Add one formulation-owned network
    operating-set section to `_make_step_constraints()` and update its
    documented section contract. Do not append thermal constraints separately
    in the single- and multistep builders.
11. **Normalize every enforced inequality.** Construct
    `(P/S_max)**2 + (Q/S_max)**2 <= 1` using a strictly positive finite
    per-unit rating. Do not form `S_max**2`.
12. **Verify independently.** A solver success status is not evidence that the
    intended branch equation or limit was implemented. Binding tests
    independently recompute both terminal powers from solved complex voltages
    and branch-terminal admittances.

## 4. Proposed public and build contracts

### 4.1 Build data

Publish the minimum network-owned data needed to interpret branch results and
test the model:

- `nl`: number of MATPOWER AC branch rows;
- `branch_from_bus_internal`, `branch_to_bus_internal`: formulation-internal
  endpoint indices;
- `branch_from_bus_external`, `branch_to_bus_external`: original MATPOWER bus
  IDs in branch-table row order;
- `branch_status`: Boolean in-service mask derived from validated `{0, 1}`
  input;
- `branch_rate_a_mva`: original `rateA` values in MVA;
- `constrained_branch_indices`: in-service rows with finite `rateA > 0`.

Branch-terminal admittance arrays may remain parser-internal unless a concrete
debugging or result-consumer need justifies making them part of
`OPFBuild.data`.

### 4.2 Modeled expressions

Publish the four lifted per-step terminal-flow variables—or four empty
constants when `nl == 0`—through the modeled expression namespace:

- `branch_p_from_pu`;
- `branch_q_from_pu`;
- `branch_p_to_pu`;
- `branch_q_to_pu`.

Single-step builds store one expression of shape `(nl,)`; multistep builds
store ordered lists of `T` expressions, following the existing expression
contract.

The `_pu` suffix is proposed for internal `OPFBuild.expressions` because the
values have not yet crossed the result-extraction unit boundary.

### 4.3 Public results

Recommended public names:

- `branch_p_from` in MW;
- `branch_q_from` in MVAr;
- `branch_p_to` in MW;
- `branch_q_to` in MVAr;
- `branch_s_from` in MVA, derived after extraction;
- `branch_s_to` in MVA, derived after extraction.

Shapes are `(nl,)` for single-step and `(T, nl)` for multistep. Keeping both
signed real/reactive terminal powers and apparent magnitudes makes direction,
loss, charging, and thermal utilization observable without requiring users to
reconstruct the model.

The names deliberately avoid overloading lossy DC `p_flows`, whose single
signed branch-flow variable has different physics and no distinct receiving
terminal.

Apparent magnitudes are computed only after all four signed terminal channels
are available, using `np.hypot(p, q)` rather than
`np.sqrt(p**2 + q**2)`.

## 5. Approved decisions

### Decision 1 — Default enforcement policy

The milestone's final state uses branch-limit enforcement by default, with
`enforce_branch_limits=False` retained as an explicit escape hatch for
unconstrained studies and compatibility comparisons.

The feature and default change remain deliberately decomposed:

1. Implement and verify terminal flows and constraints while retaining
   `False`;
2. Record before/after solve evidence for all bundled AC cases;
3. Change the default in its own commit with explicit migration notes.

This preserves attribution if either the new feasible set or the compatibility
change exposes a regression. It also makes the final default consistent with
the ordinary meaning of AC OPF and the existing lossy-DC behavior.

### Decision 2 — Public result names

Use the descriptive `branch_*` names proposed in §4.3. They are harder to
confuse with the existing nodal `p`/`q`, dense `P`/`Q`, and HVDC terminal
quantities. Documentation records their direct mapping to MATPOWER
`PF`, `QF`, `PT`, and `QT`.

### Decision 3 — Lifted enforcement and reporting path

Stage 0 tested direct terminal expressions first and found a concrete DNLP
performance failure. In the refreshed case57 run, three alternating trials
required `18.4–19.3 s` with direct nonlinear expressions inside the thermal
inequalities, versus `0.671–0.688 s` with four lifted terminal-flow variables.
Both formulations returned the same objective, and all 80 ratings were
nonbinding, so the difference is structural rather than congestion-driven.

The approved production path is:

1. Construct scalar-indexed direct terminal-flow expressions from `theta` and
   `v` exactly once.
2. Create four network-owned lifted terminal-flow variables per branch and
   time step.
3. Tie those variables to the direct expressions with four vector equalities.
4. Publish and thermally constrain the identical lifted variable objects.

The added $4n_lT$ variables and equalities are accepted because they preserve
the authoritative physical equation while avoiding the severe direct
constraint penalty. Gathered nonlinear indexing remains prohibited until
separately proven safe.

### Decision 4 — Independent reference data

Use both forms of reference evidence:

- a small fixed-voltage oracle locks the branch equations independently of
  optimization;
- extended solved Pypower fixtures validate terminal flows at an optimum;
- a deliberately binding case validates the new feasible-set behavior.

### Decision 5 — Positive `sparsity_tol` with enforced limits

Require `sparsity_tol == 0` whenever branch limits are enforced. Stage 0 still
measures the value and inconsistency of positive tolerances. Any proposal to
relax the rule requires evidence of a material need, a quantitatively
negligible and well-characterized inconsistency, and renewed project-owner
review. Exact branch coefficients remain non-negotiable.

### Decision 6 — Scope of strict branch-status validation

MATPOWER branch status is conventionally binary, while current shared code
treats any integer other than zero as in service.

After confirming all bundled cases use `{0, 1}`, adopt strict validation as a
deliberate cross-formulation hardening change. Put it in an isolated commit and
test AC, lossy DC, and single-node builds with valid and invalid statuses. If
the audit uncovers a legitimate nonbinary use, stop and return the finding for
review rather than silently narrowing validation to AC.

## 6. Implementation stages

### Stage 0 — Characterization and DNLP expression spike

**Status:** complete — evidence recorded in
`experiments/branch_limits_s0/`

1. Record current default AC results and schemas with limits disabled.
2. Construct direct terminal-flow expressions for a small case without
   changing production builders.
3. Verify DNLP construction and solve behavior for:
   - sparse and dense `P`/`Q`;
   - one transformer with non-unity tap and phase shift;
   - line charging;
   - two parallel branches with different tap, shift, and charging data;
   - an out-of-service branch.
4. Compare direct expressions with terminal flows computed independently from
   complex voltages and branch admittances.
5. Compare `sparsity_tol=0` with representative positive tolerances:
   - record any construction or solve benefit;
   - measure nodal-balance versus exact terminal-flow inconsistency;
   - confirm that individual branch thresholding is never used.
6. Compare direct and lifted formulations using:
   - Python expression-construction time;
   - DNLP canonicalization/setup time;
   - variable and constraint counts;
   - Jacobian and Hessian nonzero counts when exposed;
   - IPOPT iterations and solve time;
   - peak or approximate memory where practical;
   - single-step and a representative multistep horizon.
   Report four distinct configurations where applicable:
   - the pre-M4 baseline;
   - unused direct reporting expressions;
   - lifted reporting with enforcement disabled;
   - lifted reporting with limits enforced.
   This separates universal observability overhead from the incremental
   constraint and canonicalization cost.
7. Decide direct expressions versus lifted variables from evidence.
8. Run all bundled AC cases with ratings enabled to establish evidence for the
   approved default change.
9. Audit branch-status values across every bundled case and evaluate the
   cross-formulation behavior of strict `{0, 1}` validation before landing the
   approved shared hardening change.

No public contract changes land in this stage.

**Result:** Direct expressions match independent complex-current arithmetic
within `4.5e-14` p.u. across the measured cases. The lifted structure solved
all bundled AC cases through case118 both with and without thermal
enforcement. Relative to the pre-M4 baseline, lifted reporting increased local
solve wall time from `0.030 s` to `0.049 s` on case9, `0.295 s` to `0.509 s`
on case57, and `1.734 s` to `2.547 s` on case118. Positive case57 sparsity
tolerances through `0.1` removed no entries, while `1.0` removed eight entries
and made the approximate model infeasible. All bundled branch statuses were
binary. Decision 3 was updated to the project-owner-approved lifted path.

### Stage 1 — Authoritative numerical branch primitive

1. Add named MATPOWER branch column constants to `network.py`, including
   `RATE_A`.
2. Add the branch-terminal admittance helper.
3. Refactor `make_ybus_matpower` to consume it without changing `Ybus`.
4. Skip out-of-service rows before impedance division and return exact zero
   terminal coefficients for them.
5. Use duplicate-safe additive scatter for all four branch contributions when
   rebuilding `Ybus`, preserving original row order.
6. Add bus `GS`/`BS` shunts only after branch assembly; do not include them in
   terminal admittances.
7. Add focused unit tests comparing the refactored `Ybus` byte-for-byte or at
   tight numerical tolerance with the existing implementation/reference.
8. Add a parallel-circuit test with different tap, shift, and charging
   parameters that would fail under non-accumulating advanced indexing.
9. Add terminal-admittance tests for taps, shifts, charging, parallel rows,
   and branch status.
10. Add an orientation-sensitive phase-shifting-transformer test using an
    original row and a deliberately reversed, analytically equivalent
    representation.
11. Implement strict shared `{0, 1}` status validation outside the admittance
   arithmetic, in a separate commit covering all three formulations. Stop for
   review if the Stage 0 audit finds a legitimate nonbinary case.

### Stage 2 — AC terminal-flow expressions and reporting

1. Copy external branch endpoint arrays from the original case before calling
   `reindex_case_to_consecutive()`, then parse the internal endpoints, status,
   rating, and constrained-index arrays from the reindexed case.
2. Add the real-valued DNLP terminal-flow expression helper using scalar
   `theta[i, 0]` and `v[i, 0]` indexing.
3. Handle `nl == 0` explicitly: create no lifted variables or defining
   equalities and publish four empty constants without calling
   `cp.hstack([])` or `cp.Variable(0)`.
4. Invoke the helper exactly once per AC time step.
5. For `nl > 0`, create four network-owned lifted variables and tie them to the
   direct expressions with four vector equalities.
6. Publish the identical lifted variables through the four per-unit expression
   channels in single- and multistep form without compacting or reordering
   branch rows.
7. Extend result initialization and extraction with signed terminal powers and
   apparent magnitudes derived using `np.hypot`.
8. Keep direct equation and lifted-variable construction independent of
   `enforce_branch_limits`, so disabling limits does not disable observability.

### Stage 3 — Thermal operating constraints

1. Remove the single- and multistep `NotImplementedError` stubs.
2. For each constrained branch and time step, reuse the published lifted
   variables in both normalized unit-right-hand-side apparent-power limits.
3. Skip out-of-service and zero-rated rows without a sentinel constraint.
4. Treat every finite positive rating as active, including `rateA >= 1e10`,
   and reject negative or non-finite ratings on in-service branches before or
   while constructing `constrained_branch_indices`.
5. Enforce the approved `sparsity_tol` compatibility rule before constructing
   limits.
6. Add one explicit formulation-owned network operating-set section to
   `_make_step_constraints()`, preserving the documented component-constraint
   ordering and avoiding builder-local appends.
7. Confirm no objective term, penalty, or softening is introduced.

### Stage 4 — Numerical and behavioral verification

1. Add a nonbinding-limit regression: enabling limits leaves the solution
   unchanged within existing AC tolerances.
2. Add a binding-limit case that redispatches and has at least one terminal
   magnitude active at `rateA`.
3. Add a case where from- and to-terminal magnitudes differ, proving that both
   ends are computed and constrained rather than one being inferred by sign.
4. Add an infeasible case with physically unreachable ratings.
5. Verify all constrained terminal magnitudes at every time step.
6. Verify sparse/dense equivalence with limits enabled.
7. Verify single-step versus multistep `T=1` equivalence.
8. Verify multistep result ordering and shapes.
9. Verify unsuccessful-result schema retention.
10. Compare reported terminal flows with independent Pypower fixtures only
    after confirming voltage-magnitude and angle agreement. Keep the
    fixed-voltage equation oracle as the primary branch-physics comparison so
    different nonconvex local solutions are not mistaken for equation errors.
11. Verify a finite positive rating at or above `1e10` remains active under
    the documented project rule.
12. Verify AC branch fields are present regardless of enforcement and absent
    from lossy-DC and single-node result schemas.
13. Verify a zero-row branch table produces `(0,)` single-step arrays and
    `(T, 0)` multistep arrays.
14. Verify all six branch fields are `None` when no usable core primal exists,
    and that apparent magnitudes are not partially derived.
15. For the binding case, independently recompute `S_f` and `S_t` from solved
    complex voltages, assert the intended terminal binds, and verify the other
    terminal and every unrelated constrained branch remain within their
    ratings.
16. Apply two documented tolerances:
    - an engineering-unit MVA tolerance on independently recomputed and
      reported terminal magnitudes;
    - a dimensionless tolerance on the normalized squared constraint residual.

### Stage 5 — Default-policy migration

1. Review the Stage 0 and Stage 4 all-case evidence.
2. Change the default from `False` to `True` in an isolated commit.
3. Record the compatibility change, the `False` escape hatch, and before/after
   solver evidence.

### Stage 6 — Documentation and examples

1. Update `OPFOptions`, `OPFBuild`, and result docstrings.
2. Update the README formulation table and remove the AC branch-limit caveat.
3. Update `CLAUDE.md`, including the option default and branch-result schema.
4. Update the interactive notebook text: its branch-limit controls now affect
   the AC feasible set rather than visualization only.
5. Add or adapt one compact AC example that demonstrates a binding thermal
   constraint and verifies both terminals.
6. Regenerate `examples/README.md` if an example changes.
7. Update the M4 and M17 status/dependency records after verification.

## 7. Test matrix and acceptance gates

### Gate 1 — branch physics

- Terminal admittances match the MATPOWER equations.
- Terminal powers match independent complex-arithmetic evaluation.
- Tap ratio, phase shift, line charging, losses, parallel branches, and status
  are covered explicitly.
- Parallel-circuit coverage uses distinct tap, shift, and charging parameters
  and proves duplicate-safe additive assembly.
- Phase-shifting-transformer coverage detects orientation, conjugation, and
  `Yft`/`Ytf` swaps.
- Out-of-service rows are skipped before impedance division and produce exact
  zero terminal coefficients.
- Bus shunts enter `Ybus` but never branch-terminal flows.
- `Ybus` is unchanged by the internal refactor.

### Gate 2 — limit semantics

- Both from- and to-terminal apparent powers are constrained.
- Only in-service rows with finite `rateA > 0` are constrained.
- `rateA=0` means unconstrained in AC.
- A finite positive `rateA >= 1e10` remains constrained as an intentional
  departure from MATPOWER's large-number sentinel.
- Every thermal inequality is normalized to a unit right-hand side without
  forming `S_max**2`.
- Invalid ratings on in-service branches fail clearly when enforcement is
  enabled.
- Branch status accepts exactly `{0, 1}` and is normalized to a Boolean mask.
- Enforced limits use exact nodal coefficients under the approved
  `sparsity_tol` contract.
- `rateB`, `rateC`, angle bounds, and soft limits do not enter silently.

### Gate 3 — formulation structure

- Sparse and dense AC paths use the same branch primitive and terminal-flow
  helper.
- Branch expressions do not depend on nodal `P`/`Q` indexing.
- Network limits remain formulation-owned and do not enter component
  adapters.
- Single-step and multistep builders share the same per-step implementation.
- Each direct terminal equation is constructed once; each lifted variable is
  created once and the identical lifted object is reused for publication and
  enforcement.
- The first implementation uses scalar DNLP indexing and handles `nl == 0`
  explicitly.
- Network operating constraints occupy one documented
  `_make_step_constraints()` section without disturbing component ordering.

### Gate 4 — behavior

- A nonbinding case preserves the prior optimum.
- A binding case changes dispatch for the expected network reason.
- A sufficiently tight case is infeasible.
- Independently recomputed terminal powers prove which terminal binds and that
  the other terminal and unrelated constrained branches remain feasible.
- Every solved constrained terminal satisfies both a documented
  engineering-unit MVA tolerance and a normalized squared-residual tolerance.

### Gate 5 — result contract

- Signed from/to real and reactive powers and apparent magnitudes are present
  with stable names and units.
- Single-step shapes are `(nl,)`; multistep shapes are `(T, nl)`.
- Values are available even when enforcement is disabled.
- External endpoint IDs preserve direct correspondence with the original
  MATPOWER branch table; internal endpoint indices are named unambiguously.
- Unsuccessful AC solves retain all six keys with `None`.
- Lossy-DC and single-node schemas do not gain AC terminal-flow fields.
- Empty branch tables produce `(0,)` and `(T, 0)` arrays.
- Apparent magnitudes use `np.hypot` and are derived only when both signed
  channels for that terminal are available.

### Gate 6 — regression and performance

- Full pytest, Ruff, mypy, and diff checks pass.
- Existing Pypower objective, generation, voltage, and angle comparisons
  remain within their established tolerances when limits are nonbinding.
- Case57 and case118 measurements cover sparse and dense single-step builds
  and a representative multistep build.
- Measurements record Python construction, DNLP setup/canonicalization,
  variables, constraints, derivative nonzero counts when exposed, IPOPT
  iterations and solve time, and practical memory observations.
- Measurements distinguish the pre-M4 baseline, unused direct reporting
  expressions, lifted reporting without enforcement, and lifted enforcement.
- Any material regression is reviewed rather than hidden by changing
  tolerances.

## 8. Non-goals

- lossy-DC branch-model or zero-rating policy changes; the separately
  committed and tested shared branch-status validation change is the sole
  approved exception;
- single-node transmission constraints;
- `rateB` or `rateC`;
- branch voltage-angle-difference limits;
- current-magnitude or active-power-only AC flow limits;
- soft thermal limits or overload penalties;
- contingency-dependent or time-varying ratings;
- topology switching;
- transformer control variables;
- AC branch losses in the objective; and
- moving physical network branches into the component adapter registry.

## 9. Proposed commit sequence

1. `test: characterize AC branch terminal flow expressions`
2. `fix: validate MATPOWER branch status values` — after the bundled-case
   audit confirms binary status data
3. `refactor: centralize MATPOWER branch admittance data`
4. `feat: publish AC branch terminal flows`
5. `feat: enforce AC branch apparent-power limits`
6. `test: verify AC branch-limit behavior and references`
7. `change: enforce AC branch limits by default` — after all-case
   characterization
8. `docs: document AC branch limits and terminal flows`

Keep commits clean by file group where practical, but do not split an
implementation from the focused tests that establish its contract.
