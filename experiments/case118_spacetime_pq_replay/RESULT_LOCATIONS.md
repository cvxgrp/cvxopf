# Experiment-local results

On 2026-09-21 the owner corrected the root scratch-directory convention.
The P/Q result moves were approved at that time; retaining the broader historical
moves was approved separately during the filesystem repair.
All files belonging to this experiment now live here. Raw runs and checks are
under ignored `results/`; the report and analysis are reviewable source files.

- `results/case118_spacetime_pq_replay/`: interrupted 120+6 combined replay.
- `results/case118_spacetime_pq_replay-startup-failed-20260921T060913Z/`: initial failed startup.
- `results/case118_6047_primary_diagnostic/`: failed two-way startup.
- `results/case118_6047_primary_diagnostic_retry/`: completed isolated two-way run.
- `results/case118_6047_four_way/`: completed four-way run.
- The corresponding preparation, boundary-check, launch, telemetry, and PR
  validation records are also retained under `results/` with their original names.

`RESULT_LOCATIONS_20260921.json` records original/current paths, byte counts,
and SHA-256 hashes for all 888 files moved for this experiment. No raw evidence
was rewritten. Historical paths inside hashed records remain unchanged;
`experiments.retained_paths` resolves exact file references using this experiment's
relocation manifest and the time-only replay's manifest. Each analysis operation
loads and indexes those manifests once; nested operations reuse the index.
Other experiments retain their own established readers. There are no filesystem
links through root `outputs/` or `not-tracked/`.

The preceding time-only replay lives in
`../case118_vectorization_replay/results/case118_vectorization_replay/`.
Older annual and time-vectorization records likewise moved into their owning
experiments, each with its own checked relocation manifest. The prior replay's
stale temperature collector was stopped cleanly before inventorying its files;
the original solve-time telemetry snapshot remains unchanged.

Verify preservation of relocated bytes and link targets without solving or modifying files:

```sh
uv run --locked --extra dev python -m experiments.retained_paths
```

Scientific reconstruction is a separate check: `analyze_primary` without
`--write` reconstructs the four-way comparison without publishing changes.
Filesystem preservation checks alone do not validate its scientific conclusions.

The completed-run source binding describes the original execution. Analysis
verifies unchanged sources or their recorded Git revision; it does not claim
that today's path-only edits produced the historical solve. Existing launch
checks still reject changed execution sources: another solve requires a newly
reviewed source binding and owner authorization.

Root `not-tracked/` retains the unrelated spreadsheet task only. It is not an
experiment output location. Raw `results/` remain Git-ignored and must not be
staged or committed.
