# Hour 6047 historical reproduction: provenance and proposed setup

Initial investigation dated 2026-09-21, checkout HEAD `93294af12`.
The owner subsequently approved source/environment preparation only. Preparation
completed on 2026-09-22 without native optimization. The initial provenance
comparisons are in `artifacts/provenance/summary.json`; preparation results are in
`artifacts/provenance/preparation_summary.json`.

Subsequent update: the owner authorized the solve harness and convergence test.
Both arms are complete; see `ENVIRONMENT_COMPARISON_REPORT.md`. The preparation
authorization statements below describe the earlier checkpoint, before this
additional authorization.

## Completed preparation, 2026-09-22

The Git-free source copy and both Python 3.11.15 environments now exist under
`results/hour6047_environment_reproduction/`. All 105 historical source hashes
verify. Each environment contains 49 installed packages; their inventories differ
only in CVXPY (1.9.2 / 1.9.3) and sparsediffpy (0.3.0 / 0.6.1). The historical
cvxopf package and experiment modules are imported directly from the snapshot;
all 51 imported cvxopf/experiment modules were checked for snapshot origin.
Neither the main `.venv` nor the owner branch was changed.

`prepare_historical.py` is the shared, preparation-only harness. It has no solve
mode. It calls the historical worker's unchanged `prepare` routine and historical
canonicalization/solver adapter. Before preparation, it replaces `cyipopt.Problem`
with a mock that captures bounds and options and exits at `solve(x0)` without
constructing a native IPOPT problem. CVXPY's real oracle construction and its
initial objective/constraint evaluations complete before that exit. This is not
a derivative-consistency test or a convergence result.

Both arms passed the following checks:

- Exact historical raw/assigned named starts and all 9,124 canonical x0 entries.
- Exact historical normalized layout; 6,880 model and 2,244 auxiliary coordinates.
- Identical native dimensions: 9,124 variables and 10,601 constraints.
- Identical variable lower/upper and constraint lower/upper bound arrays.
- Identical input fingerprint, request, policy, solve-configuration, and harness
  hashes, with frozen fixture validation retained.
- Identical forwarded options: adaptive mu strategy, tolerance `1e-7`, zero bound
  relaxation, exact Hessian, no derivative test, no least-square dual initialization,
  `print_level=0`, and `sb=yes`. No `max_iter` override is supplied; the native
  default remains implicit and was not exercised by these preparation checks.
- Identical cyipopt extension and nine recursively linked Homebrew binary hashes.
  System-library paths are recorded in linkage output but system binaries are not
  separately hashed. Both arms used the same host; the five recorded thread-count
  environment variables were unset in both.

The only runtime data-path adaptation was redirecting the snapshot-relative outer
archive path to the original retained archive. Primary evidence references already
point to intact annual-study files. The replay request and expected replay start
are read directly at their relocated locations by the harness. Source verification
maps original absolute paths to the corresponding snapshot files. No historical
source or evidence bytes were edited, and no filesystem aliases were created.

Final paired records are `preparation-a-02/preparation.json` and
`preparation-b-01/preparation.json` under the reproduction root. The earlier
`preparation-a-01` is retained: it also passed, before the harness recorded the
input fingerprint explicitly and restored the historical package import order.
Both final records bind the same final harness hash. Ruff passed for the harness;
the paired real-model preparation checks provide the relevant functional evidence.

To repeat preparation, use a new output directory for each arm:

```sh
UV_CACHE_DIR=/tmp/cvxopf-provenance-uv uv run --no-project \
  --python experiments/case118_spacetime_pq_replay/results/hour6047_environment_reproduction/env-a/bin/python \
  python experiments/case118_spacetime_pq_replay/prepare_historical.py \
  --arm a --output experiments/case118_spacetime_pq_replay/results/hour6047_environment_reproduction/preparation-a-03
```

For B, substitute `env-b`, `--arm b`, and a new B output directory. The environments
were populated from the anchor lock exported with the dev extra; requirements files
are retained in the reproduction root. B changes exactly the two package pins.
Runtime package inventories, rather than only requirement declarations, were compared.

Preparation authorization is fulfilled. Actual optimization remains unauthorized;
there is no launch-capable harness yet. A solve implementation must retain these
checks, bind reviewed sources, and pass process/thermal preflight before launch.
Historical native binary identity remains unknown even though the two new arms
share verified binary hashes.

## Source identity

The historical environment and hour-6047 request contain the same 105 source
hashes. Of these, 104 match Git blobs at
`2ac05b039780efd5f2b04465b2b6b42a7405ada9` exactly. The remaining file,
`experiments/case118_annual_hierarchy/results/s4b_annual_ac/operator-intervention-002448/replay.py`,
is ignored, absent from that commit, and still exists with its recorded hash.
This inventory includes files beyond the worker's actual imports; it is not an
import trace. All ten selected primary/winner reference hashes and the selected
compressed archive hash verify against their retained files.

Do not call `2ac05b039` the execution HEAD. The run started at
2026-09-20 20:13:49 UTC (13:13:49 PDT), before that commit at 15:04:19 PDT.
The local HEAD reflog places `ca4015527` at execution time. Its tracked sources
match the recorded hashes where present, but the four replay Python files are
absent there. The evidence therefore supports a working-tree source snapshot
later captured in `2ac05b039`, with an ignored source retained separately.
`d2546451d` and `671c0d273` also match the 104 tracked source hashes, so those
hashes alone cannot uniquely identify HEAD. No archived run-time Git status was
found in the inspected environment/request/launch records.

Use `2ac05b039` as a reproducible source anchor, verified file by file, rather
than treating its commit timestamp as proof of when the experiment ran.

## Initialization is identical in the historical and failed time-only runs

Compared the successful replay's `run/s4b-shard-008/ac-006047-spec-00/start.json`
with the four-way diagnostic's `time_only/start.json`:

- Raw and assigned named starts agree exactly.
- All 9,124 canonical x0 coordinates agree exactly (maximum difference zero).
- Both contain 6,880 model and 2,244 auxiliary coordinates.
- Normalized layout signature agrees:
  `632d0a094bbed7a9cce13ce9f7f3ce1b779857254ddc2636b8759565afe7cf48`.
- Request hash, source kind, and source attempt ID agree exactly.

Generated auxiliary variable names differ, as expected for separate builds;
the normalized signature and full vector agree. Equal starts/layouts do not
establish equal constraint graphs, derivatives, scaling, or solver trajectories.
The historical request is order 0 with no replay start and no target-free source.
Its recorded numerical AC options are `{}`; effective defaults must also be
captured during reproduction.

## Dependency and native-library evidence

Historical runtime evidence records Python 3.11.15, CVXPY 1.9.2, cyipopt 1.7.0,
IPOPT 3.14.19, NumPy 2.4.6, pandas 3.0.3, and CLARABEL 0.11.1. The source anchor's
lockfile additionally pins sparsediffpy 0.3.0 and SciPy 1.17.1 for Python 3.11.
The runtime record omits those last two packages and does not hash its lockfile;
their historical installed versions remain lock-supported reconstruction values,
not direct runtime observations. The lock at the reflog HEAD agrees with the
anchor on the unchanged lock history.

Comparing package name/version pairs in the anchor and current lockfiles finds
only two changes: CVXPY 1.9.2 to 1.9.3 and sparsediffpy 0.3.0 to 0.6.1.
The current installed environment reports those new versions and SciPy 1.17.1;
the other reported numerical packages match the historical runtime record.
Current CVXPY package metadata requires `sparsediffpy>=0.6.0,<0.7.0`.

The failed four-way log directly identifies IPOPT 3.14.19 with MUMPS 5.6.2.
The successful historical worker log is empty, so it supplies no corresponding
MUMPS banner. Today's cyipopt extension links Homebrew `libipopt.3.dylib`; that
library links MUMPS, OpenBLAS, and Accelerate, among other libraries. Historical
binary hashes and the historical linear-solver version were not retained in the
inspected records. A matching IPOPT version is insufficient to prove native
binary identity. Record resolved paths, hashes, linked libraries, and thread
environment for both new arms and keep these identical between arms.

## Implementation differences and compatibility

Four source files differ between the anchor and the four-way execution commit:
`src/cvxopf/_component_adapters.py`, `src/cvxopf/ac_problem.py`,
`src/cvxopf/problem.py`, and the annual experiment's `streaming_runner.py`.
In addition to P/Q assembly changes and its option plumbing, `f07ad9232`
changed static load constants to symbolic broadcasts. This file must be held
fixed too. The fixture passes explicit active/reactive time-series frames;
the existence of the static-load change is not evidence that it caused this
window's failure. Its relevance requires inspecting the constructed graph.

Historical package metadata permits `cvxpy>=1.9`, so both dependency pairs fit
that declared requirement. Historical AC code uses the pre-spatial-batching
construction. Both isolated environments now pass imports, canonicalization,
oracle initialization, and the mocked native call as recorded above. Actual
native optimization compatibility remains untested. No model or initializer
code was modified to make either arm prepare.

The historical worker cannot simply be launched unchanged at today's paths:
it references relocated `outputs/` records and checks source hashes at original
absolute paths. Current readers contain explicit relocation support, added
after execution. A reproduction needs a separately recorded harness adaptation
for path resolution and source verification. Keep archived evidence bytes and
hashes unchanged; do not recreate root output aliases or bypass provenance checks.

## Proposed narrow reproduction

1. After owner approval, materialize a Git-free historical source snapshot under
   `results/hour6047_environment_reproduction/source/` in this experiment.
   Verify the 104 recorded Git-source hashes and retain the ignored source's
   identity separately. Keep the owner checkout and branch unchanged. This is
   an isolated executable source copy, so its location needs explicit approval
   under the repository's checkout rule.
2. Create two isolated Python 3.11.15 environments under that same result root.
   Use the historical lock's Python-3.11 package set for A; vary only CVXPY and
   sparsediffpy to 1.9.3/0.6.1 for B. Do not sync or downgrade the main `.venv`.
   Verify actual package inventories and native binary hashes rather than relying
   only on requirements. Verify imports resolve to the historical snapshot.
3. Prepare one shared, reviewable harness in this experiment. Restrict adaptations
   to relocation-aware reading, mapping original source paths to verified snapshot
   files, output paths, and provenance/logging. Separate harness hashes from
   historical model hashes. Reuse the original primary preparation and causal
   source, not the historical successful solution. Preserve input, policy,
   solver-configuration, and request checks.
4. Before native solves, canonicalize in each environment to a mocked native
   entry and check exact named physical starts, full x0, normalized layout,
   dimensions, and effective solver options against the successful archive.
   Report any difference and resolve it before running. Preserve the 3,000
   iteration default and tolerances; the only proposed solver-option addition
   is iteration logging. Prepare a concrete launch command after this harness
   exists and has been reviewed.
5. After preparation/review and owner launch authorization, run A once in a fresh
   process: historical code with the historical dependency pair. Confirm process
   and thermal monitoring permissions before launch, no competing solves, and
   the agreed cooling setup. Retain phase records, native logs, results, and the
   existing acceptance audit. Freeze source files throughout execution.
6. Only if A passes the original acceptance criteria, run B once with the same
   historical code, frozen physical request/start, and native library stack.
   No helpers, retries, tuning, or broader window sample. If A fails, stop and
   examine reconstruction/native-stack differences. If B fails after A succeeds,
   the package-pair transition is implicated under the controlled setup, but
   this alone does not distinguish CVXPY from sparsediffpy or prove bad derivatives.

Further dependency bisection, current-code/old-environment controls, or derivative
checks require a subsequent evidence-based proposal. Successful timings here
would be single diagnostic observations, not replicated performance estimates.

## Verification and remaining gaps

This investigation used Git blob SHA-256 comparisons, retained-reference checks,
exact start comparisons, TOML lock parsing, installed metadata, and native linkage
inspection. No solver was invoked and no production code was changed. Full tests
are not warranted for these evidence and documentation additions.

The launch prerequisites listed at the preparation checkpoint were subsequently
fulfilled, and the authorized comparison is complete. See the comparison report
for results, exact source bindings, monitoring evidence, and remaining scientific
limits. Historical native binary identity remains unverified.
