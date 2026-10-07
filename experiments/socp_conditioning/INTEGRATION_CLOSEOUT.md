# SOCP conditioning preservation checkpoint

The investigation is preserved on `socp` for review, without production adoption
or a new study launch. All 108 initial reviewable copies and 714 ignored raw
artifacts matched the approved size and SHA-256 inventories. The isolated
checkout and its originals remain intact. The original E3 run remains paused;
its 219 inventoried files are unchanged.

## Final stock solver evidence

[Four condition results](FOUR_CONDITIONS_REPORT.md) and its
[protocol](FOUR_CONDITIONS_PROTOCOL.md) are the final stock-CLARABEL evidence.
All eight QDLDL/Faer neutral 24-hour arms returned native `Solved` and passed
physical and component-cost checks at relative-gap tolerance `1e-6`, retaining
optional shedding. Four still fail the historical absolute canonical accounting
gate. None of those classifications has been rewritten.

The opening synopsis in [the historical index](STUDY_INDEX.md) predates its
completed shared-primitive, practical-LU and stock-backend results. Its bytes
remain preserved; the final four-condition report supersedes that synopsis.
[The historical acceptance decision](ACCEPTANCE_DECISION.md) records the earlier
open question. The approved prospective direction now requires native full
convergence and unchanged independent physical/component/ENS gates, with a
scale-aware total accounting gate. Acceptance despite native failure remains
deferred. SOCP feasibility is not AC realizability or a certified bound.

## Preserved locations and test correction

[RESULT_LOCATIONS.json](RESULT_LOCATIONS.json) maps historical raw roots to their
verified current locations. Expected sizes and hashes remain in the immutable
[evidence inventory](../../plans/socp-conditioning-evidence-inventory.json), whose
hash is also bound by the map. [The reader](artifact_locations.py) verifies the
inventory and requested artifact before returning a mapped path. For example:

```text
uv run --offline --no-sync --extra dev python -m experiments.socp_conditioning.artifact_locations experiments/socp_conditioning/results/four_conditions_001/summary.json
```

Only the approved solver-enumeration unit test differs from its initial copy.
[TEST_PORTABILITY_EDITS.json](TEST_PORTABILITY_EDITS.json) records original and
edited hashes, mocks, the reproduced failure and passing portable check. Real
solver selection and restoration remain tested. Experiment context collectors,
reports, protocols and raw evidence are unchanged.

Sixteen historical bindings refer to older experiment-source versions; the
inventory records that evolution rather than claiming exact replay from current
scripts. Native replay still needs the separately reconstructed Rust environment
and commercial diagnostics need their optional packages. Neither dependency is
added to the project. The location reader enables artifact inspection, not
historical numerical replay from modified production sources.

## Checkpoint verification

Normal-environment checks used
`UV_CACHE_DIR=/private/tmp/cvxopf-integration-uv uv run --offline --no-sync --extra dev`;
the alternate cache avoids sandbox access to the default UV cache and installs
no dependencies.

- `pytest tests/test_socp_conditioning_*.py -q`: 180 passed, including 171
  transferred tests and nine portable location-reader checks. Only the known
  OpenMP import and zero-norm constraint warnings remain.
- `pytest tests/ --collect-only -q`: 3,636 tests collected without error. Full
  suite execution is deferred to the production implementation checkpoint.
- Ruff lint passes for all 70 reviewable Python files; formatting checks pass
  for those files and 40 Markdown files (110 total).
- All 933 historical artifact paths resolve through inventory/hash checks;
  69 relative Markdown links in the transferred reports/protocols exist.
- Source hashes still match all 108 inventory entries; destination hashes match
  except for the single recorded test edit. All 714 raw copies remain ignored.
- Read-only process inspection found no E3, conditioning or Stage D worker.
  Both HEADs remain at the bound base commit; no tracked diff or staged file exists.

These checks verify preservation and portable collection, not a new scientific
replay or production qualification.

## Next owner review gate

Production extraction, qualification and prospective E3 preparation remain the
next segment of [the integration plan](../../plans/socp-conditioning-integration-transfer.md).
Review the typed API and frozen qualification protocol first. Reuse existing
`PreparedBoxBounds`, `VariableBoxFamily` and representation-policy machinery;
define changed-parameter, repeated-solve, warm-start and unsupported solver/cone
behavior so transformations and inverse maps cannot become stale.

No production import of historical modules or monkey-patching is permitted.
DC and AC defaults remain unqualified; AC needs its own smooth constraints,
fixed-coordinate and verified-start design. Custom SuperLU, native KKT scaling
and compensated arithmetic remain historical diagnostics. No qualified speedup
or AC recovery improvement is claimed.

The proposed 25-call qualification matrix and new `e3_prepared` directory remain
unexecuted and uncreated. The original E3 tree is not resumed or cross-sourced.
Stage D remains held. Owner review, a clean commit and separate execution
authorization precede any fresh E3 run. This checkpoint stages and commits
nothing and does not retire the isolated worktree.
