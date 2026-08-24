# Worker-recycling comparison implementation TODO

- [x] Freeze `RECYCLE_COMPARISON_PROTOCOL.md` from the approved revision-8 plan.
- [x] Add the tracked S2 outer plan and verify its exact byte identity.
- [x] Implement the tiered S2 first-64 reference extractor and clean-checkout verifier.
- [x] Generate the deterministic reference JSON and SHA-256 sidecar.
- [x] Implement the comparison supervisor, worker, observer, provenance registry,
  one-time outer seeding, and external RSS/stall/cumulative-wall limits.
- [x] Implement explicit reviewed interruption resume with checkpoint/provenance
  validation, stale-process protection, noncolliding invocation identities, and
  retained wall accounting.
- [x] Implement independent detailed reconstruction and immutable compact-result
  promotion.
- [x] Report raw trajectory and actual-start residuals with cross-process layout/
  object-identity normalization and no automatic scientific gate.
- [x] Add focused tests for reference identity/regeneration/tampering, provenance,
  solver-stack metadata, checkpoint-free startup, planned lifecycle, reviewed
  resume, restart timing, resource chains, compact promotion, partial prefixes,
  causal evidence, and numerical negative controls.
- [x] Run explicit Ruff and configured mypy checks.
- [x] Run all 2,155 collected repository tests in bounded groups; all passed.
- [x] Verify tracked reference identity both cleanly and by source-backed
  regeneration; manually verify the SHA-256 sidecar.
- [x] Review artifact sizes/modes, ignored raw-output boundary, provenance registry,
  protocol identities, imports, compilation, and absence of experiment output.
- [x] Close final pre-run analysis contract: frozen reference digest, matched RSS,
  causal-source/start residuals, restart timing baseline, and compact provenance.
- [ ] Review and commit the implementation and reference artifacts.
- [ ] Confirm `git status --porcelain` is empty before authorizing the expensive
  comparison run.

Implementation is complete for review. The expensive comparison has not been
started, no compact result has been promoted, and commits remain user-controlled.
