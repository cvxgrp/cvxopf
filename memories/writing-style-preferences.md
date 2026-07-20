---
name: writing-style-preferences
description: How the user wants technical reports and prose written — learned from their edit pass on the DNLP-vs-Pypower report (2026-07-20). Blunt factual claims, minimal hedging, separate what-we-showed from what-we-think, few colons/em-dashes, name approaches vs packages deliberately.
metadata:
  type: feedback
---

Distilled from the user's edits to
`experiments/dcline_crosseval/DNLP_ROUTING_AND_PWL_REPORT.md` — their version vs
my original draft. These are DURABLE prose preferences, apply to future reports/
writeups.

**Why:** the user rewrites for a technical-peer audience (this report is headed
to Stephen Boyd + Dan). Mushy or over-hedged prose reads as unrigorous to that
audience; they want claims stated at exactly the strength the evidence supports
and no softer.

**How to apply:**
- **Cut hedging to one clean caveat.** Delete layered qualifiers ("roughly,"
  "it seems," "well-supported"). Keep the ONE honest epistemic caveat that
  matters and state it plainly (e.g. "we present this as the *likely* mechanism
  rather than a measured one"). Don't sprinkle hedges throughout.
- **State results as blunt claims.** "We've proved the Pypower solution is not
  [optimal]" over "the answer is demonstrably suboptimal." Prefer the harder,
  shorter verb. But never overclaim past the evidence — bluntness serves
  accuracy, it doesn't replace it (they explicitly separate "can't prove ours
  is global optimum" from "proved theirs isn't").
- **Separate what-we-showed from what-we-think, structurally.** They split a
  combined section into `Observations` (raw results) -> `Analysis` (pattern +
  mechanism/interpretation) -> `Conclusion` (facts only, with an explicit
  scope-limits paragraph). Keep the interpretive/mechanistic claims OUT of the
  factual conclusion. When asked for a "factual" conclusion they mean: what the
  experiments showed, with scope limits — not the inferred mechanism.
- **Few colons, few em-dashes.** Standing preference (stated directly). Break
  colon-heavy sentences into separate sentences.
- **Name the approach vs. the package deliberately.** For AC-OPF comparison:
  "the DNLP method" / "the standard (Pypower/pips) method" name the APPROACH
  (what's under test = problem preparation); "cvxopf" / "Pypower" name the
  PACKAGES (use in concrete-results sentences, and for readability variation).
  Do NOT use package names where it would imply the solver backend is the
  variable under test. "the disciplined solver" / "raw NLP solver" are BANNED —
  mushy, and Boyd will not appreciate them.
- **Include provenance/context.** They ADD notes I'd omit: "this is an official
  test case, one of the first written," where a value comes from, odd-data
  asides. Surface where things come from rather than presenting numbers bare.
- **Vary package/method names across the doc** for readability once the
  conceptual/concrete split above is respected (~even balance).

Related: the report is the [[dnlp-canonicalization-tractability-thesis]]
writeup; the "proof by code" demo script for it is still pending.
