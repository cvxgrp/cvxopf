# SOCP conditioning investigation transfer and integration plan

Prepared 2026-10-06 for the owner’s `socp` checkout. This checkpoint inventories
the isolated investigation and specifies its transfer, production extraction,
qualification, and prospective E3 changes. Only this plan and its two inventory
files are added now. Transfer, implementation, qualification solves, staging,
commit, execution, and worktree retirement are later steps.

## Checkout identities and preserved state

| Checkout | Location | Current state before this checkpoint |
| --- | --- | --- |
| Destination | `/Users/bmeyers/github/cvxopf` | Clean branch `socp`, seven commits ahead of local `origin/socp` |
| Investigation | `/Users/bmeyers/.codex/worktrees/socp-ac-scaling/cvxopf` | Detached HEAD; only the conditioning experiment and its tests are untracked |

Both checkouts are at `dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`.
Neither has tracked implementation changes. There are no experiment commits to
merge or cherry-pick. Integration means an explicit file transfer followed by
new implementation in the owner’s existing checkout, without switching branches.

The original E3 root remains
`experiments/case118_tracy_2021/results/e3`. Its retained progress records show
9 accepted arms, 26 launches, no active attempt, and an operator stop on
2026-10-02. Its 13 completed manifests correspond to 13 retained result
archives; those counts are not 13 accepted arms. The progress monitor remains
paused. A process-list check found no E3, conditioning, or Stage D worker.
Stage D remains held.

## Exact inventories

- [Transfer inventory](socp-conditioning-transfer-inventory.json) lists every
  selected reviewable file, its destination, byte size, SHA-256, role, and direct
  experiment imports. All 108 destination paths are currently absent.
- [Evidence inventory](socp-conditioning-evidence-inventory.json) lists every
  retained conditioning artifact and every original E3 file, with byte size and
  SHA-256. It also records the integrity checks and historical source drift.

| Material | Files | Disposition |
| --- | ---: | --- |
| Experiment Python code | 41 | Preserve under the same `experiments/socp_conditioning/` paths, not as production imports |
| Final reports | 28 | Copy exact bytes, including negative results and repaired or interrupted trials |
| Protocols | 9 | Copy exact bytes; do not retrofit prospective settings into them |
| Study index and acceptance decision | 2 | Copy exact bytes; add a separate closeout note explaining superseded conclusions |
| Experiment `.gitignore` | 1 | Copy before any raw evidence; it excludes `results/` |
| Focused investigation tests | 27 | Copy exact bytes first; separately record the narrow unit-test portability correction below before normal collection |
| Ignored conditioning evidence | 714 | Proposed byte-for-byte mirror into destination `experiments/socp_conditioning/results/`; retain the original until verified |
| Original E3 files | 219 | Leave in place without modifying, relocating, acknowledging STOP, or resuming |

The conditioning evidence is 584,590,291 bytes (about 558 MiB); a mirror adds
approximately that much file content before filesystem overhead. Original E3
contains 28,720,362 bytes. These are logical file sizes, not allocated disk space.

The selected experiment files are bounded to the one investigation directory
and its 27 named tests. The 41 Python files comprise four extraction seeds,
20 other diagnostics or analyses, and 17 native-solver experiment modules. The
native modules are preserved because they document the failed and successful
historical tests and are imported transitively by some stock-solver runners.
Their preservation does not qualify their algorithms for production.

Do not transfer bytecode, `__pycache__`, virtual environments, installed solver
packages, toolchain/cache directories, the temporary Rust checkout, or Rust
build outputs. Do not copy unrelated files or alter `pyproject.toml` or `uv.lock`
to support historical MOSEK/COPT/native experiments. The inventories use exact
file lists, not broad directory-copy commands.

## Evidence integrity and reproduction boundaries

Read-only checks found:

- All 448 checked same-directory `artifacts` map entries in conditioning JSON
  records have existing targets with matching SHA-256 values.
- All 26 parsed report-table hashes match retained artifact bytes.
- All 28 original E3 completion-artifact references match; its 13 result
  archives and 13 completion manifests agree in count.
- All nine retained native `engine.json` records include the source patch,
  Rust entrypoint and Cargo lockfile. Their canonical JSON hashes match the
  corresponding binding’s `native_engine_sha256`.
- The latest `four_conditions_001` binding’s 85 source references match current
  isolated files. Its stock extension identity remains retained evidence, not
  permission to replace or install a solver.

These are byte/provenance checks, not new numerical analyses or a fresh solver
qualification. They do not prove every implicit or external reference resolves.

Sixteen of the 28 inspected bindings refer to older versions of one or more
experiment files. The per-run differences are retained in the evidence inventory.
The affected filenames are `PROTOCOL.md`, `diagnostic.py`, `cone_scaling.py`,
`optimality_audit.py`, `native_step_probe.py`, and `compensated_cone.py`.
Production sources are not among those mismatches. Preserve each recorded hash;
do not relabel this expected historical evolution as artifact corruption or
claim that current scripts exactly reproduce every earlier source version.
The inventory itself is a current-file snapshot, not reconstruction of missing
historical source bytes.

The native checkout and executable currently exist at
`/tmp/cvxopf-clarabel-trace.qX1JkJ/Clarabel.rs`. Recorded engine snapshots preserve
the patches and build instructions; they do not preserve a compiled executable’s
bytes merely by retaining its hash. Native replay still requires a separately
reconstructed source/toolchain/environment. No Rust build or new native trial is
part of this checkpoint.

Several reports and scripts use source-root-relative paths. Some also record
absolute paths to the original worktree, original E3 root, shared `.venv`, or
temporary caches. Legacy loaders check the base commit and original production
source hashes: copying them into a later modified checkout does not make them
valid historical replay entrypoints. Keep the isolated worktree unchanged for
base-source reconstruction and distinguish artifact inspection from rerunning
an experiment under new source.

## Transfer sequence and reference resolution

1. Recheck both HEADs/statuses and the two inventory manifests. Refuse collisions
   or source drift rather than overwriting a destination. Keep source files
   unchanged throughout transfer.
2. Copy the experiment `.gitignore`, then the 80 other experiment files and 27
   tests exactly as enumerated. Compare every destination byte size and SHA-256
   with the transfer inventory. Preserve historical reports, protocols and tests
   as historical material; do not rewrite them into production tests. After this
   preservation check, permit only the separately recorded unit-test portability
   correction below; original inventory hashes remain unchanged.
3. Mirror only the 714 enumerated raw files into the ignored destination results
   root. Compare all 714 sizes/hashes. Confirm Git still ignores every raw file.
   Preserve all originals, including failed analyses and interrupted attempts.
4. Add new `experiments/socp_conditioning/INTEGRATION_CLOSEOUT.md` and
   `RESULT_LOCATIONS.json`. The latter maps original artifact roots to verified
   current roots and records expected hashes. Readers must verify the requested
   file’s hash before using a mapped location. No compatibility symlinks or
   modifications to hashed JSON, compressed archives, logs, matrices or vectors.
5. Verify copied reports’ relative artifact links and explicit historical-root
   resolution. The original E3 prefix maps to the same original E3 directory;
   the original conditioning-results prefix maps to the verified ignored mirror.
   Until a mirror is verified, the resolver may inspect only the retained source
   root, not an assumed destination. Production qualification gets its own paths
   and never invokes a legacy source-bound loader against changed production code.
6. Repeat source and original-E3 byte checks. Keep the isolated worktree until
   transfer, references, and qualification evidence have been reviewed. This plan
   does not authorize deleting or archiving it.

## Historical unit test portability correction

Before normal project test collection, correct only
`tests/test_socp_conditioning_native_lu.py::test_execution_context_enumerates_only_declared_solver`.
Its real context collector queries commercial-package metadata and historical
artifacts/native-engine provenance unrelated to the solver-selection assertion.
The owner reproduced `PackageNotFoundError: mosek` in the normal project
environment; adding MOSEK/COPT or requiring ignored artifacts and the temporary
Rust checkout is not an appropriate unit-test dependency.

First preserve and verify the original test bytes through the exact transfer
inventory. Then mock the unrelated upstream environment/provenance collector
and file-hash reads within this unit test, keeping the real
`execution_configuration()` and solver-selection assertions intact. Assert that
the declared solver/options are CLARABEL-only inside the context and that the
previous solver configuration is restored afterward. Do not mock away the
behavior under test, weaken assertions, or change experiment context collectors.

Record this post-transfer edit in a new
`experiments/socp_conditioning/TEST_PORTABILITY_EDITS.json`: test path/node ID,
original inventory SHA-256, edited SHA-256, purpose, changed dependencies, and
the normal-environment unit-test command/outcome. Link it from the new closeout
note. The source worktree, original inventory hashes, reports and scientific
evidence remain unchanged. Verify the targeted unit test without commercial
packages, historical results or the Rust engine; do not use a blanket skip as
the portability correction.

Actual environment/provenance verification belongs to optional historical
integration/replay checks with explicit prerequisites, not normal unit
collection. This correction adds no dependencies and requires no scientific
rerun. It does not authorize broad edits to the other historical tests; record
and bring back any further environment-dependent failures before expanding it.

The new closeout note must make the final stock-CLARABEL evidence easy to find.
The historical study index’s opening and acceptance-decision note predate the
latest owner decisions; retain those bytes and explain their subsequent
disposition in the new note instead of silently revising history.

## Production extraction and exact code boundaries

Extract mathematics into typed code; do not move experiment modules into
`src/cvxopf`, import them from production, or use their `unittest.mock.patch`
hooks. Proposed new private modules are `_numerical_preparation.py` for shared
policy/maps, `_convex_preparation.py` for canonical QP/SOCP transformations and
solver integration, and `_ac_preparation.py` for the separately qualified
nonlinear coordinate/start mapping. Final API details require a design checkpoint
before implementation; these names describe responsibilities, not a new plugin
framework.

| Transformation | Exact extraction source | Production destinations and constraints |
| --- | --- | --- |
| Device-rating normalization | `diagnostic.py`: `unit_cones`, `nd_cones`, `storage_cones` | Device-owned helpers in `storage.py` and `nondispatchable.py`; selection through `_component_adapters.py`. SOCP uses normalized SOC constraints; AC uses smooth normalized squared inequalities. DC does not acquire artificial capability cones. |
| Exactly fixed boxes | `diagnostic.py`: `exact_box`, `generator_fixed_boxes`, `nd_fixed_boxes` | Shared typed box handling selected by generator/ND adapters. Exact equality only; no tolerance-based near-fixed elimination. Keep dimensions, engineering-unit public fields and component ownership explicit. |
| Fixed-coordinate reduction | `fixed_substitution.py`: `Reduction` mathematics | Convex reduction/inverse maps: affine substitution, retained/free indices, objective offset and reconstructed equality multipliers. Support zero fixed coordinates as a no-op. Select coordinates through typed metadata, not experiment variable-name heuristics. |
| Joint scaling | `joint_scaling.py`: `joint_scales`; `cone_scaling.py`: `transform`, `mapped_solution`, `verify_mapping` | Five simultaneous infinity-norm square-root passes on canonical `P,A,b,c`, cumulative positive scales clipped to `[1e-6,1e6]`, a common row scale per SOC, no global objective multiplier. Restore `x=D*xhat`, `s=shat/R`, `z=R*zhat` and offsets before public extraction. |
| Verified solving and restoration | Existing `OPFBuild.solve()` and experiment inverse-chain checks | Explicit solver integration in `problem.py` and the new convex module, preserving CVXPY restoration, public statuses/results, dual mapping and failure cleanup. No process-global solver monkey-patch. |
| AC fixed-coordinate/start integration | Existing `_ac_start_mapping.py`, `_hierarchical_solver.py`; new AC preparation design | Preserve complete verified IPOPT `x0`, initialization, derivatives, coordinate layouts and restored results. Convex `P,A,b,c` scaling is not an AC implementation. |

Eliminated equality multipliers are reconstructed from stationarity, not native
solver multipliers. Native residuals/gaps describe the solver’s transformed
problem; retain them separately from original-coordinate numerical checks.
Test objective constants, quadratic cross terms, finite maps, cone membership,
stationarity, complementarity, absent devices, single-step/vectorized/stepwise
assembly, repeated solves, failure results, and the disabled path.

Excluded from production: whole-objective divisor sweeps, constraint-only
scaling, removal of shedding, altered economics/boundaries, custom SuperLU,
native QDLDL KKT scaling, compensated cone arithmetic, and experimental native
termination or iteration modifications. MOSEK/COPT adapters remain historical
diagnostics. General AC Jacobian/Hessian equilibration is a separate design,
not an implicit extension of the convex rule.

## Qualification before default changes

First add an explicit preparation policy and preserve a disabled baseline.
SOCP has the strongest evidence; DC and AC need bounded matched qualification
before any default changes. Equivalent mathematics does not guarantee better
runtime, acceptance or trajectory agreement. A timeout or numerical rejection
does not demonstrate infeasibility and does not qualify a default.

Proposed serial qualification matrix, to freeze in a protocol before solving:

| Path | Bounded cases | Fresh calls |
| --- | --- | ---: |
| Prepared SOCP with stock QDLDL | The four original neutral 24-hour Tracy windows plus the deficit-depletion arm; use retained historical controls | 5 |
| Lossy DC and single-node DC | Each formulation: device-bearing Case9 at T=1 and T=3, then the matched Tracy deficit at T=24; disabled versus prepared | 12 |
| AC | Device-bearing Case9 at T=1 and T=3: baseline, smooth normalization, normalization plus fixed-coordinate handling; then matched Case118 deficit T=3 baseline versus combined preparation | 8 |

This caps the proposed numerical matrix at 25 calls, one worker/thread at a
time, 180 seconds and 16 GiB sampled RSS per call, with at most 4,500 cumulative
worker-seconds. Verify monitoring permissions before launch. Do not silently add
retries, longer horizons, alternative starts, solvers or a new recovery campaign.
The Case9 device fixtures must include exact and near-fixed coordinates, storage,
renewables, shedding and a nontrivial terminal boundary; their deterministic
inputs belong in the qualification protocol. Failures remain outcomes, not
reasons to relax physical gates.

Keep historical investigation tests separate from new production tests, applying
the recorded portability correction before normal collection. Add
focused typed transformation/adapter/start tests, use existing component/SOCP/
hierarchical verification and Tracy runner tests, and run the proportionate full
suite before the integration handoff. Report native outcomes, original-unit
physical/component audits, canonical accounting, trajectory differences, elapsed
time and RSS. The numerical matrix and public API design are proposed here;
no tests or optimization calls have been performed in this inventory turn.

## Prospective E3 integration

Planned modifications are limited to
`experiments/case118_tracy_2021/e3.py`, `run_e3.py`, `E3_PROTOCOL.md`, their focused
runner tests, and new qualification/closeout records. Separate prepared SOCP
controls from the currently shared convex controls. DC/AC selection must follow
their qualification disposition rather than inheriting SOCP settings.

Freeze stock QDLDL for prepared SOCP with `tol_gap_rel=1e-6`,
`tol_gap_abs=tol_feas=1e-10`, one thread, 5,000 iterations and the tested `1e-8`
minimum-step cutoff. Record the remaining tested settings explicitly, including
the reduced tolerances, instead of relying on an undocumented solver default.
These are prospective E3 settings, not a global tolerance relaxation or an IPOPT
tolerance translation.

Require native full convergence and unchanged independent physical/component/
ENS gates. Add the prospective total canonical-versus-physical accounting gate
`abs(Ccanonical-Cphysical) <= 1e-4 + 1e-6*abs(Cphysical)`. Preserve the native
status/gaps, canonical discrepancy, physical cost and transformation maps as
separate evidence. Acceptance despite native failure remains deferred; relaxed
SOCP feasibility remains separate from AC realizability or certified bounds.
Update runner replay/status validation to reconstruct and check the new evidence,
not merely trust an archive’s acceptance label.

The proposed fresh execution root is
`experiments/case118_tracy_2021/results/e3_prepared`, currently absent. Prepare its
prospective run configuration after qualification; do not create misleading
binding/start/launch/completion records before authorized execution. Preserve
the original 20-arm design, inputs, costs, load shedding, boundaries, AC recovery
ladder and study resource ceilings. Never cross-source resume the old E3 tree.

## Integration handoff and stopping point

The transfer checkpoint is ready for owner review when the initial copies of all
108 reviewable files and all 714 ignored evidence files match their inventories,
the sole permitted post-transfer test edit has its original/edited hashes and
passing portable unit-test result recorded, historical references resolve
explicitly, the new closeout records identify qualifications and exclusions,
and original E3 bytes remain unchanged. Final destination hashes must match the
inventories except for that explicitly recorded test edit; retain the original
inventory instead of replacing its hash with the edited hash. Stop for review
of the typed API and qualification protocol before implementing the production
path.

The later implementation checkpoint must include a file-by-file diff, measured
qualification outcomes and explicit default dispositions, prospective E3 policy,
and a plain-English proposed commit message. The owner stages, commits and
pushes. A fresh source-bound study starts only after review, a clean execution
commit and separate execution authorization. No computation is started by this
plan, and neither monitor nor held study is resumed.
