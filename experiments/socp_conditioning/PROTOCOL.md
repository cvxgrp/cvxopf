# SOCP numerical-conditioning diagnostic

Predeclared 2026-10-02, before diagnostic solves. This is an isolated,
non-promotional diagnostic based on `dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`.
The ongoing Tracy E3 run, its checkout, inputs and artifacts remain read-only.
No AC solve or production-code change is included in this checkpoint.

## Fixed comparison

Run serially, with at most one additional solver process and one solver thread.
Each arm uses a fresh subprocess. First run a three-step Case9 correctness
fixture containing storage, renewable generation and sheddable loads, with
delta=0.5 h and an equality storage endpoint. Then run the exact E3 Large surplus
SOCP arm 002, 24 intervals [3308,3332), from the retained binding and verified
Tracy source. In both fixtures use this fixed order:

1. `baseline`: unchanged squared-MVA device constraints and objective.
2. `cones`: replace storage and renewable capability circles only with
   `norm([p/S,q/S],2) <= 1`, one cone per device and interval.
3. `objective`: baseline constraints, entire objective divided by 1,000,000.
4. `combined`: normalized device cones and entire objective divided by 1,000,000.

Variables retain their existing units. Injection maps, availability/energy
bounds, dynamics, terminal equality, network constraints and relative costs do
not change. Experiment-local temporary device hooks implement the cone variants;
hooks are restored immediately after construction. Public SOCP construction and
`build.solve()` remain the execution path. The original objective expression and
all named component costs are evaluated in original cost units for auditing.

Use the E3 CLARABEL controls unchanged: `tol_gap_abs=tol_gap_rel=tol_feas=1e-10`,
`max_iter=5000`, `max_threads=1`, SCIPY canonicalization, no warm start, verbose
logging. Unchanged absolute solver tolerances refer to scaled objective units
in the objective-scaled arms; retain both native and original-unit objective
gaps. This is not a claim that objective scaling preserves finite-tolerance
termination. No tuning or retries after observing results.

Use the original E3 physical/accounting audit on Case118. Case9 uses the public
independent SOCP audit, plus independently reconstructed generator, storage and
load-shedding costs. Smoke objective agreement uses absolute 1e-4 plus relative
1e-6, without requiring nonunique device coordinates to agree. All four smoke
arms must pass before Case118. Native `Solved`, a passing original-unit audit,
and native/original objective agreement within 1e-4 + 1e-8*abs(original objective)
are required for a successful arm. The objective comparison includes the
canonical constant offset.

After surplus, select the first successful modified configuration in the fixed
preference order `cones`, `objective`, `combined` (not by observed runtime).
If none succeeds, stop and report. Otherwise run the exact Large deficit E3
arm 006, [1165,1189), as a fresh baseline followed by that configuration. No
additional variants or AC tests are automatically authorized by these results.

## Evidence and operational boundaries

Each immutable arm retains input identity, source hashes, commit and dirty-state
qualification (new experiment code is intentionally uncommitted), package and
machine context, settings, original-unit result and audits, full native x/s/z,
native solution and info, canonical sparse A/P and b/c, variable/constraint
layout, reconstructed primal/dual residuals and objective, phase times, and peak
supervised RSS. Canonical artifacts permit independent offline reconstruction.
Original E3 result/completion hashes bind the historical reference; timings in
this diagnostic are descriptive because another study runs on the same machine.

Canonicalization is timed separately by calling `get_problem_data` before
`build.solve`; solve wall time still includes cached interface overhead. Record
extraction/audit and archive times separately. Parent wall budget is 180 s per
arm, RSS budget 4 GiB, polled every 0.5 s. Resource crossing, interruption or
process failure terminates/joins the diagnostic child and stops the ladder,
retaining a partial record. Scientific rejection is retained, not repaired.
Outputs live only in this experiment's ignored `results/`; no authoritative
record is promoted and no live-run sources or environment are changed.

## Reviewed amendment: descriptive Case118 continuation

Approved by the owner after reviewing `diagnostic_001` on 2026-10-02. Its
Case9 objective-only arm failed the cross-arm objective agreement threshold,
although all four physical/accounting audits passed. Retain that result and
threshold unchanged. The summary SHA-256 is
`158c5f3c04f55a367db99514d1a6bb2c3ea337bd5df03c0655b2a7c8f06bf0a2`.

The owner authorizes the Case118 matrix as descriptive diagnostic evidence despite
that smoke discrepancy. `--smoke-reference` must verify the frozen summary and
its four complete arm/archive/supervision chains before launching. Do not repeat
the Case9 solves. Bind this reviewed exception into the new run's binding and
summary. The original "all four smoke arms must pass" prerequisite is superseded
only for this explicitly labeled continuation; the original record remains a
failed gate, not retroactively qualified evidence.

Run the four surplus arms in the unchanged order, with the same factors, options,
physical/accounting gates and resource limits. If a modified arm meets the existing
successful-arm conditions, select it using the original fixed preference and run
the deficit baseline and selected arm. Otherwise stop after surplus. This does not
authorize any other variants, tolerance changes, AC solves or production edits.

## Reviewed amendment: modest objective-divisor sweep

Owner-approved on 2026-10-02 after the surplus matrix. Run only the exact same
24-hour surplus case, using normalized device cones in all arms and objective
divisors **1, 10, 100, 1000**, in that order, once each in fresh serial workers.
Repeating divisor 1 provides a contemporaneous reproduction check. No additional
factors, retries, deficit or AC solves follow automatically. This is a descriptive
sweep, not promotion or a change to the ongoing E3 protocol.

Keep the complete physical problem, costs, solver settings, audit thresholds,
resource limits, native-vector retention, and successful-arm predicate unchanged.
Compare native statuses alongside original-unit objectives, physical residuals,
native/original objective discrepancies, and reconstructed dual stationarity in
original objective units. Retain native gaps converted back to original cost units;
do not interpret them alone as objective-error certificates. The prior smoke
discrepancy remains explicitly bound, not waived retroactively.

## Reviewed amendment: exact fixed-box test

Owner-authorized 2026-10-02 after the offline canonical optimality audit.
Run one fresh supervised Case118 surplus worker, variant `fixed_boxes`.
Use normalized storage/renewable cones, objective divisor 1, unchanged solver
settings, physical inputs and audits, 180 s/4 GiB limits, and one thread.
Replace only exact coincident **real-power** generator/renewable lower and upper
bounds with equalities. Leave reactive variables/limits, capability cones,
nonfixed boxes, costs, dynamics and terminal targets untouched. No near-equality
tolerance, removal of devices, production edits or automatic follow-on runs.
The comparator is the retained `sweep_001/surplus-combined-divisor-1` arm,
whose objective and iterations exactly reproduced the earlier cones-only arm.
Retain the full native/canonical evidence and compare original-unit physical
feasibility, objective/epigraph agreement, dual norms and local stationarity.
A better result supports this particular representation hypothesis; it does not
qualify a generic production migration or establish global numerical robustness.

## Reviewed amendment: equilibration-only comparison

Owner-authorized 2026-10-02 following Clarabel.rs issue 96. Run exactly three
fresh serial surplus workers with normalized cones, the **original paired box
inequalities**, and unscaled objective. This isolates solver equilibration; do
not combine it with the fixed-box intervention. Ordered settings:

1. `equil_default`: enabled, scaling bounds 1e-4 to 1e4 (current defaults).
2. `equil_off`: disabled; leave the same inactive scaling bounds recorded.
3. `equil_narrow`: enabled, scaling bounds 1e-2 to 1e2.

Keep every other option, max iterations, acceptance tolerance and 180 s/4 GiB
resource boundary unchanged. One worker/one solver thread at a time. Preserve
failed native outcomes and continue the declared matrix after scientific
rejection, but stop on resource/process failure. Retain raw canonical matrices
and native vectors; verify identical model data across all arms. No adaptive
settings, retries, additional scaling values or production changes follow.

## Reviewed amendment: one MOSEK comparison

Owner-authorized 2026-10-02: one fresh surplus worker, normalized device cones,
original paired box inequalities, and unscaled objective. Use MOSEK 11.2.5 with
default numerical tolerances, one thread, verbose logging, and the same external
180-second/4-GiB limits. Load MOSEK through a temporary `uv --with` overlay;
do not change the main environment or lockfile. Retain the task, native status,
primal/dual objectives, feasibility statistics, effective conic tolerances,
public results, original-unit physical audits, costs, timing and provenance.
Canonicalization is included in reported solve-path time; native optimizer time
is reported separately. CVXPY dualizes the conic task for MOSEK, so this compares
solver/interface paths, not identical canonical matrices or algorithm alone.
The comparator is `equilibration_001/surplus-equil_default`. Require native
optimal status, the existing physical gate, and original/canonical objective
agreement (1e-4 + 1e-8 absolute objective). No tolerance tuning or follow-on solves.

Publication repair: the first MOSEK solve completed, but strict JSON rejected its
native `array.array` vectors. Preserve that attempt's task/log/supervision and
repeat once with identical model/settings after converting these vectors to lists.
This is an artifact-repair repeat, not an additional solver-settings arm.

## Reviewed amendment: one COPT comparison

Owner-authorized 2026-10-02 after installation of a local trial license.
Run the same surplus case, normalized cones, original paired inequalities and
unscaled objective with COPT 8.0.7 in a temporary `uv --with` overlay. Default
numerical tolerances, one thread, verbose logging, 180 seconds/4 GiB externally
supervised. Disable CVXPY's automatic infeasible-or-unbounded reoptimization
to retain exactly one solve. Preserve task, native result/status/parameters,
primal/dual arrays when returned, costs, original-unit audit and provenance.
Apply the same physical and canonical/original objective-agreement gate, with
native COPT optimal status required. Time canonicalization and solve together;
retain native solve time separately. Compare solver/interface paths rather than
claim identical solver standard forms. No further cases or tolerance tuning.

Publication repair: COPT's CVXPY adapter puts its live Model handle in
`solver_stats.extra_stats`, which the relaxation audit carries into its result.
The first solve completed but strict JSON could not serialize that handle.
Preserve the task/log/supervision; repeat once after explicitly omitting only
the live handle, retaining native numerical evidence and all other statistics.

## Reviewed amendment: no-shedding comparison

Owner-authorized 2026-10-02. Repeat the same 24-hour surplus model with all loads
fixed: set each Load.shedding_cost_per_mwh=None, eliminating the fraction variables
and penalty rather than introducing zero-width bounds. Keep P/Q profiles, other
devices, endpoints, normalized cones, paired bounds, and unscaled costs unchanged.
Run CLARABEL, MOSEK, COPT serially with their preceding settings (CLARABEL 1e-10;
MOSEK/COPT default numerical tolerances), one thread and 180 seconds/4 GiB each.
This is a same-solver ablation, not an equal-tolerance solver benchmark.

Use the existing native-status, physical/accounting and canonical/physical cost
gates. The raw fixed-load result schema omits shedding outputs; only for the
historical audit, project these absent quantities to exact zeros, keeping actual
served P/Q untouched. Verify served demand against input and graph-level absence
of shedding variables/cost. Preserve original and modified input hashes, native
evidence and immutable artifacts. Continue after scientific rejection; stop on
process/resource failure. No new scenario, production change or automatic tuning.

## Offline matrix conditioning inspection

Owner-authorized: inspect the six retained CLARABEL/MOSEK/COPT surplus inputs,
with and without shedding. No optimizer calls. Read CLARABEL matrices and MOSEK
tasks directly. Reconstruct COPT's native matrix without optimizing, requiring
byte-identical re-export to the retained MPS (the export rewrites cones as QCs).
Retain hashes, shapes, sparsity, coefficient and row/column scales, exact duplicate
or opposite rows, structural rank bounds, and bounded sparse singular estimates
for full A and equality rows. Compare raw versus five algebraic row/column
2-norm scaling passes; this is not a solver equilibration replay or a cone-valid
model transformation. The initial PROPACK attempt was stopped because its
Fortran routine did not promptly honor the Python alarm. The replacement uses
ARPACK with direct sparse A/A.T callbacks, seed 0, 160 iteration limit, 1e-6
tolerance and an eight-second/4,000-product budget checked inside each callback.
Unfinished estimates remain unavailable, not evidence of a finite condition.
One offline follow-up uses sparse Gram shift-invert factorization when the
symbolic product bound is at most ten million entries, under the same worker
resource limits. This squares conditioning and is explicitly an estimate, not
a certificate; check its singular triplet against the original matrix. The
iterative phase has an eight-second budget including elapsed factorization time.
Each serial analysis worker retains the 180-second/4-GiB external limits.
P is summarized separately; structural zero curvature is not itself a defect.
No claims about proprietary internal Newton/KKT conditioning follow from these
pre-presolve input matrices. No source changes in the live study.

## Cone-preserving canonical scaling smoke test

Owner-authorized 2026-10-05 following the matrix inspection. Four fresh serial
CLARABEL workers, exactly: shedding baseline, shedding scaled, fixed-load
baseline, fixed-load scaled. Each reconstructs the previously retained surplus
input and requires exact original canonical A/P/b/c arrays, cone layout,
objective offset and physical-input identity before solving. Keep normalized
device cones, original paired boxes, all costs, E3 numerical tolerances, solver
internal equilibration, one thread, verbose output, and 180 s/4 GiB limits.
No objective divisor, regularization, tolerance adjustment, retries or sweep.

For Ax+s=b and x=D*xhat, set Ahat=R*A*D, bhat=R*b, Phat=D*P*D,
chat=D*c, shat=R*s. R and D are positive diagonal. Every coordinate of one SOC
shares one R multiplier; equality and nonnegative-cone rows scale individually.
Recover x=D*xhat, s=shat/R, z=R*zhat. Objective values are unchanged.

Freeze five passes using the current A only: divide each row by its 2-norm,
except each SOC block uses the largest row norm in that block; then divide each
column by its 2-norm. Zero rows/columns retain their scale. Clip cumulative R
and D to [1e-6,1e6] after each update. No adjustments based on observed solves.
Verify cone-block scales, round trips, objective, primal/dual residual and
complementarity identities before optimization. Tests include a canonicalized
production build without solving. This is a cone-preserving transformation,
unlike the unrestricted algebraic row normalization in the preceding inspection.

An experiment-local solver-interface hook substitutes the transformed matrices
inside build.solve(); the unchanged CVXPY inverse chain receives mapped-back
vectors. Record raw transformed and mapped native vectors separately. Native
normalized residuals remain labeled as transformed-coordinate measurements.
Retain both matrix archives, scale vectors/hashes, original-unit public and
relaxation audits, objective/component costs, stationarity, complementarity,
cone residuals and storage-epigraph excess. Preserve unsuccessful solver output;
do not infer better accuracy from native status or gap alone.

Measure raw/transformed A condition estimates and A/P/b/c scales. Separate
construction, canonicalization, external scaling, solve-interface/native solve,
extraction/audit, offline condition estimation and worker wall times. Timings
are descriptive, not a statistically established speedup. Use the unchanged
successful-arm predicate, and separately compare original-unit optimality
evidence. Lower condition numbers without improved original-unit accuracy do
not establish a solution to the numerical issue. Scientific rejection does not
stop the four arms; process/resource or input/provenance failure does. No
production migration, live-study change or further numerical run is authorized.

## Joint data scaling smoke test

Owner-authorized 2026-10-05 after the unsuccessful A-only scaling test. Reuse
its four-arm order, exact retained 24-hour surplus data, both load policies,
canonical identity checks, solver options, original-unit acceptance/audits,
one-thread serial workers, and 180-second/4-GiB limits. There are exactly four
new solves and no automatic retry, extra settings or production changes.

Replace only the external scaling rule. Use five simultaneous damped
infinity-norm passes considering the symmetric data layout
[P,A.T,c; A,0,b; c.T,b.T,0], without constructing it. This is not the solver's
barrier-dependent KKT matrix. Keep the last augmented coordinate fixed at one,
so the global objective multiplier remains one. At each pass compute:

- variable maxima = max(row max abs(P), column max abs(A), abs(c));
- constraint maxima = max(row max abs(A), abs(b)), with one shared maximum
  across every SOC block;
- divide cumulative D and R by the square roots of their corresponding
  maxima, treating exact zero maxima as one; clip cumulative scales to
  [1e-6,1e6]; then apply both updates simultaneously to P,A,c,b.

Record the same positive-diagonal equivalence maps and full native/mapped
evidence as before. Exact feasible-set and objective equivalence do not imply
equivalent finite-tolerance termination. Inspect all P/A/c/b ranges, A condition
estimates, original-unit primal/dual/cone/accounting residuals, complementarity,
epigraph slack, iterations and separately timed overhead. Reject apparent
improvements supported only by native gap/status. Keep scientific rejection,
stop for process/resource/input/provenance failure, and retain immutable results
separately from the prior A-only test. No post-result scale selection is allowed.

## Joint scaling stopping audit and contrasting deficit

Owner-authorized 2026-10-05. First inspect the retained surplus scaled arms
without another solve: compare effective native full/reduced convergence tests,
last logged steps and embedding ratio, original-unit physical errors, named
stationarity groups, component costs, storage epigraph slack and trajectories.
The overwritten pre-postprocessing error status/internal KKT state was not
retained; report any remaining ambiguity rather than invent an exact cause.
Small approximate gaps are not certified objective-error bounds.

Run exactly two fresh serial workers on the existing E3 Large deficit arm 006,
24 hourly intervals [1165,1189): normalized cones baseline, then normalized cones
with the unchanged joint rule. Keep shedding enabled, the original paired boxes,
all costs, settings, audits and 180-second/4-GiB limits unchanged. No fixed-load
deficit variant, retry, tolerance adjustment or new scaling factor. Continue
after scientific rejection; stop after resource/process/provenance failure.
The joint_scaling.py source hash must match the surplus execution. Reconstruct
physical inputs from verified E3 evidence; require the new pair's original
canonical arrays, cone dimensions and objective offset to agree exactly.
E3's squared-cone canonical arrays are not claimed identical to these normalized
cones. Retain all original/transformed/mapped evidence in a new immutable root.
This is a contrasting-case diagnostic, not production qualification or a claim
of AC feasibility. Main checkout and live execution remain untouched.

## Joint scaling with exact fixed box cleanup

Owner-authorized 2026-10-05. Run exactly three additional fresh serial workers:
surplus with shedding, surplus with fixed load, and deficit with shedding.
Comparators are the corresponding retained joint-scaling arms in
joint_scaling_001 and joint_deficit_001; do not repeat them. Retain normalized
device cones and the byte-identical joint_scaling.py rule. Replace only exactly
coincident real-power generator/renewable box inequalities with equalities,
using the previously tested fixed_boxes construction. No near-fixed collapse,
coordinate elimination, reactive-bound change, objective modification, solver
parameter change, retry or sweep is included.

Verify each original physical-input identity against its comparator before
solving. Independently compare canonical variable identities and ordering,
P/c/objective offset, all unaffected equality/inequality rows and all SOC rows.
Require that only exact opposite unary Pg/p_nd bounds disappear and the matching
fixed-value equalities appear, with the correct counts. Then apply and verify
the unchanged positive-diagonal joint scaling and all inverse maps.

Reuse native-Solved, original physical/accounting and objective-agreement gates,
1e-10 CLARABEL tolerances, one thread, and 180-second/4-GiB limits. Retain failed
native points without promoting them. Continue after scientific rejection,
stop after process/resource/input/provenance failure. Preserve all matrices,
vectors, settings, audits, source contexts and logs in a new immutable root.
No production migration, live checkout edit or automatic follow-on solve.

## Exact fixed-coordinate substitution after joint scaling qualification

Owner-authorized follow-up: exactly three serial workers, the same surplus
with shedding, surplus fixed-load, and deficit with shedding. Compare against
joint_fixed_boxes_001 without repeating those solves. Start from the identical
normalized-cone model with exact Pg/p_nd fixed equalities; eliminate only these
unary fixed coordinates and their defining equality rows by affine substitution.
Retain every other row, including constant rows/cones after substitution. Do not
eliminate storage boundaries, change nearly fixed bounds, or add presolve rules.

For x_F=v and free x_U, retain P_UU, c_U+P_UF v, the constant
0.5 v' P_FF v+c_F'v, A_U, and shifted right-hand side b-A_F v. Recompute scales
with the unchanged joint_scaling.py rule on this reduced problem. Reconstruct
all original primal coordinates/slacks for CVXPY inversion and physical audits.
Reconstruct the eliminated equality multipliers from stationarity, labeling
them as reconstructed, not native; preserve raw reduced/scaled x/s/z separately.
Retain original/reduced/transformed matrices and the full coordinate/row maps.

Before any solve, prove exact coefficient slicing/substitution against the
retained comparator and test objective, primal, free-stationarity and
complementarity mappings, including nonzero fixed values/quadratic cross-terms.
Use unchanged 1e-10 CLARABEL controls, one thread, and 180-second/4-GiB limits.
Run no retries, new solver arms, production changes, or automatic follow-ups.
Native-Solved and original physical/accounting acceptance gates remain unchanged.
