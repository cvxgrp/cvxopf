# S3 Case118 month implementation TODO

- [x] Freeze the 720-hour fixture as the exact forward extension of the S2
  start and bind its case, trajectory, input, policy, solver, and scenario
  hashes.
- [x] Freeze `recycle_every_16` in global coordinates: boundaries 16 through
  704, 44 planned restarts, 45 invocations, and `study_complete` at 720.
- [x] Freeze resource limits, abnormal-stop/review semantics, accounting, and
  the independent advancement gate in `S3_PROTOCOL.md`.
- [x] Implement the single-study S3 supervisor and worker without importing
  comparison-only or M17 experiment runners.
- [x] Bind every invocation to clean committed source, the S3 fixture, policy,
  solver configuration, outer artifact, and cumulative resource clocks.
- [x] Implement reviewed continuation from only the last verified checkpoint;
  never convert an abnormal stop into an automatic recycle.
- [x] Implement independent S3 reconstruction and compact tracked-result
  promotion.
- [x] Add synthetic tests for all 44 scheduled boundaries, final-boundary
  behavior, stale/local counter rejection, abnormal stops, provenance,
  continuation, resource accounting, and artifact corruption.
- [x] Add a short characterized lifecycle fixture that exercises planned
  recycle, reviewed stop semantics, resume bookkeeping, and completion without
  constructing the S3 scientific problem.
- [x] Run focused tests, Ruff, strict mypy, compilation/whitespace checks, and review
  source/artifact boundaries.
- [ ] Commit the reviewed pre-execution implementation, confirm a clean tree,
  record the full execution SHA, and only then authorize the month run.

No S3 scientific solve occurs during implementation or review tests.
