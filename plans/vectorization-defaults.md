# Adopt vectorized multistep defaults

Owner-authorized on 2026-09-25 after the completed 504-window Case118 study.
The owner also confirmed prior DC time-vectorization evidence supports the
default across AC, lossy DC and single-node DC public multistep builders.

- Default `build_opf_multistep` to time-vectorized assembly for all formulations.
  Keep explicit `temporal_assembly="stepwise"` and `vectorize_pq=False` available.
  Spatial P/Q batching already defaults on and applies only to AC.
- Default AC construction and solving to disabled automatic sparse dispatch.
  Expose `automatic_sparse_dispatch=True` at the public build boundary to
  retain the caller's CVXPY density threshold. This execution setting is not
  part of `OPFOptions`, preserving historical physical-input fingerprints.
- Scope CVXPY's process-global threshold to construction/solving and restore it
  on success/failure. Serialize cvxopf entry points with a reentrant lock;
  unrelated concurrent CVXPY calls require process isolation.
- Default the hierarchical lossy-DC outer plan to vectorized assembly. Its AC
  recovery path currently explicitly constructs stepwise graphs for its causal
  start protocol. Changing that representation requires a separate adaptation;
  the owner has been asked whether to include it in this change.
- Preserve extracted result shapes. Document the changed public variable layout
  and explicit stepwise compatibility path. Frozen historical graph tests must
  select their representation explicitly; do not replace retained evidence.

Validation: default/override selection, scoped-setting success/error restoration,
existing formulation/initialization/numerical tests, historical fixture hashes,
full suite, Ruff, mypy, and scientific/API review in the owner-requested
`cvxopf-review` task. Owner stages, commits and closes/merges the PR.

Evidence supports a practical default, not universal speedups or identical AC
solutions. See `experiments/case118_spacetime_pq_replay/FOUR_WAY_STUDY_REPORT.md`.

## Validation record

The full regression run completed with 3,059 passing tests and one failure in
an integrity test's temporary manifest setup. The setup was updated to bind
both current scenario and manual-runner sources, consistently with its existing
current-tree model hash. All 17 tests in that module then passed. Historical
manifests and raw evidence were not modified. Ruff passed over `src` and `tests`;
mypy passed its configured six-source contract; `git diff --check` passed.
Scientific/public-API review in `cvxopf-review` identified one compatibility
finding: historical battery and branch-characterization callers depended on
stepwise lists. Their builders now explicitly select stepwise assembly, with
six regression cases covering terminal SoC meaning, nonzero-offset restriction
assignment, and both branch-characterization modes. The reviewer independently
ran 28 passing tests and returned CLEAN with no remaining findings. No claim is
made that hierarchical inner-AC recovery has migrated to vectorized assembly;
that is separate work. The final full regression rerun passed: **3,066 tests and six subtests**,
58 warnings, in 228 seconds.
