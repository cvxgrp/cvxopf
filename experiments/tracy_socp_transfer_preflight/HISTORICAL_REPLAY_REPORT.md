# Historical replay / execution-authorization separation

The P2 finding is fixed. Read-only slice and feasibility validation accepts a
later Git commit and newly committed analysis-package files without changing
the original execution binding. Numerical worker and supervisor launch paths
still require exact equality with their frozen execution context.

`historical_replay.py` shares the previous preflight comparison across these
readers. Commit/clean metadata is not mathematical evidence. All previously
bound mathematical sources, package/native-library hashes, thread settings,
protocol, inputs, reconstructed starts, independent audits, manifests,
supervision and resource checks remain enforced. Deleted/changed bound sources
and newly tracked production code remain invalid.

The reader fix itself changes two runner files and the preflight comparison.
Those changes are explicitly recorded by exact before/after hashes in
`REPLAY_SOURCE_CHANGES.json`; original source and provenance-test bytes are
preserved in `replay_source_checkpoint/*.txt`. This is not a path ignore list:
an unreviewed later edit fails validation. Existing selected certificate and
back-test evidence files are unchanged; their regression checks recognize only
the recorded source transition. The loop interval implementation is unchanged,
and its existing `run.verify(root)` call now selects read-only validation.

## Verification

Before the fix, changing only the reported commit reproduced rejection in both
validators. Post-commit regressions simulate a different commit, clean state,
and acquisition of the formerly untracked analysis files by `git ls-files`;
they do not stage files or create a real commit. Full read-only replay passes,
while execution validation still rejects that changed context.

122 tests passed across historical replay, slice, feasibility, loop analysis,
original preflight, automatic discovery and multigraph suites. Tests block
optimization and retain negative source/environment/artifact/audit checks.
AST comparisons against the source checkpoint confirm changes are confined to
reader comparison/control flow, not the numerical construction/audit methods.
Changed formatted modules pass Ruff; the legacy feasibility runner retains its
two pre-existing E702 semicolon warnings (its other lint checks pass).

The actual documented commands were exercised without optimization:

```bash
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python \
  -m experiments.tracy_battery_transfer_slice.run --status \
  --output experiments/tracy_battery_transfer_slice/results/slice_002

UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python \
  -m experiments.tracy_battery_transfer_feasibility.run --status \
  --output experiments/tracy_battery_transfer_feasibility/results/feasibility_001

OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache \
uv run --extra dev python -m experiments.tracy_battery_transfer_loop.analyze
```

Slice replay preserves one accepted control and three native rejections across
four finalized calls. Feasibility replay preserves six finalized wall limits
and zero accepted feasible cases. Loop reproduction preserves all three named
exclusions and exact reported margins. Rejection/timeout outcomes are not
reclassified as physical incompatibility by this bookkeeping fix.

All 131 files in `slice_001`, `slice_002`, and `feasibility_001` were hashed
before/after and remained byte-identical, including bindings, protocols,
reports, invocations, starts, native archives, logs and resource evidence.
Predecessor/recovery/transfer integrity checks also remain in the replay paths.
No solve, retry, tolerance/model change, binding rewrite, staging or commit.

This is a scientific evidence-preservation correction, not new execution
authorization or an expanded five-/six-bus loop back-test.
