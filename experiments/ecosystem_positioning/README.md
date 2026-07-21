# cvxopf in the OPF / energy-optimization software ecosystem

**Purpose.** A durable, citation-backed positioning of cvxopf against the
established open OPF / power-system tools, written for a scientifically
literate audience asking "is this reinventing wheels?". Distilled from a
web-search literature/ecosystem review (2026-07) plus direct inspection of
cvxopf's own tests and examples.

**Status of claims.** Claims about *cvxopf* are verified against the repo
(tests/examples inspected, cited by path). Claims about *competitors* are from
their official docs/papers as of the versions reachable in the review; cells
marked UNVERIFIED need direct confirmation before external use. This is a
snapshot — all these tools move; date-stamp before circulating.

---

## 1. Verified cvxopf capabilities (from the repo, not marketing)

- **Fidelity ladder, one entry point.** `examples/case14_formulation_comparison.py`
  runs `ac`, `lossy_dc`, `singlenode_dc` on one case and is explicit that they
  model "progressively less network physics." It is also honest that objective
  values are NOT comparable across formulations (different objectives), and
  compares dispatch + implied losses instead. Honesty is in the code, not just
  the README.
- **AC-OPF validated against Pypower.** `tests/test_vs_pypower_reference.py`
  checks cvxopf AC-OPF against committed Pypower fixtures on case9/case14/
  case57 (+ case9_pwl) with documented tolerances: objective 1e-4 relative,
  Pg/Qg 0.1 MW/MVAr, Vm 1e-3 p.u., Va 1e-2 deg. Known discrepancies
  (IPOPT ~2e-9-at-bounds; case57 enforce_vset infeasibility) are documented
  with root cause rather than hidden behind loose tolerances.
- **Test discipline.** ~719 test functions, ~1:1 test-to-source line ratio,
  risk-weighted (HVDC and storage carry the heaviest coverage).
- **Scientific-honesty culture, verified in two independent places.** The
  Pypower-validation discrepancy documentation and the HVDC Gate-6b
  "consistency-based, not value-match" episode (experiments/dnlp_vs_pypower/)
  show the same discipline: surface the discrepancy, explain it, don't fudge
  the tolerance.

## 2. Landscape table

(See the conversation-of-record for the full matrix; key axes below. Legend:
Y = core/documented, ~ = partial/limited/roadmapped/unverified, N = absent.)

| Capability | cvxopf | PyPSA | pandapower | PowerModels.jl | MATPOWER/PYPOWER |
|---|---|---|---|---|---|
| Nonconvex AC-OPF (optimizing) | Y (DNLP->IPOPT) | N (AC = sim only) | Y | Y | Y |
| DC / linear OPF | Y (lossy DC) | Y | Y | Y | Y |
| Convex relaxations (SOCP/SDP/QC) | ~ (M11 roadmap) | N | N | Y (reference) | N |
| Multi-period *optimization* | Y | Y (core, large scale) | ~ | ~ (multi-network) | N |
| **Multi-step AC-*OPF*** | Y (M17 target) | N | N | ~ UNVERIFIED | N |
| Storage w/ SoC dynamics | Y (ideal; lossy roadmap) | Y (asym eta + standing loss) | ~ | ~ | N |
| Hierarchical convex->AC SoC coupling | Y (M17 thesis) | N (open-loop verify) | N | N | N |
| Formulation transparency | Y (CVXPY/DCP) | ~ (Linopy) | N | Y (JuMP) | N |
| Scale (thousands of buses) | N (research) | Y | Y | Y | ~ |
| Maturity / community | new/small | very high | high | high | foundational |

## 3. Per-competitor summary

**PyPSA.** Field-standard multi-period optimization (Linopy, >1000 cites). Its
optimizer uses *linearized* network physics; nonlinear AC is a post-hoc
*simulation* (`n.pf()`), not an optimization — "the power flow calculation is
independent of the optimisation." No AC-OPF at all, hence no multi-step AC-OPF.
Storage IS lossy (asymmetric efficiency + standing loss) — this is a shipped
feature, not a cvxopf differentiator. Overlap: multi-period + lossy storage
(PyPSA stronger, larger scale). Non-overlap: optimizing, temporally-coupled AC
layer.

**pandapower.** Engineering-grade; does genuine single-period AC-OPF (unlike
PyPSA). Calculation engine, not a formulation lab; multi-period + storage
limited. Overlap: single-period AC-OPF. Non-overlap: multi-step AC-OPF,
relaxations, hierarchical coupling, transparency.

**PowerModels.jl.** The closest comparator and the one needing the most
hedging. JuMP-native (transparent like cvxopf), ships the full relaxation
hierarchy (SOCP/SDP/QC) benchmarked on PGLib-OPF, does genuine AC-OPF, supports
multi-network problems. cvxopf's sharp claim is NOT "AC-OPF" or "relaxations"
(PowerModels has both) but specifically the hierarchical convex-plan ->
receding-horizon multi-step-AC-OPF with SoC-signpost coupling + lossy storage
under AC feasibility. Whether PowerModels' multi-network machinery has been
used for that exact coupled storage-AC scheme is UNVERIFIED and must be checked
before any novelty claim is published.

**MATPOWER / PYPOWER.** Foundational reference; origin of the case format and
AC-OPF benchmarks. PYPOWER (Python port) is NOT actively maintained; cvxopf
uses it only to generate static reference fixtures, not at runtime. Single-
period, engine-style, no storage/relaxations/multi-period. Serves as cvxopf's
correctness oracle (Section 1).

## 4. Honest positioning verdict

- cvxopf is **not dominant on any single axis**: PyPSA wins scale + multi-period
  maturity; PowerModels wins shipped relaxations; pandapower/MATPOWER win
  engineering robustness.
- The **defensible niche is the intersection**: CVXPY-transparent,
  nonconvex-AC-faithful, *multi-step AC-OPF* knit to a convex long-horizon plan
  through SoC targets in a receding-horizon scheme (M17). No incumbent
  implements that combination.
- **Two distinct value propositions, keep them separate:**
  1. *Engineering/architecture* — the composable, DCP-clean, formulation-ladder
     substrate (checkable today; see [[m16-in-flight-record]] for the
     component-unification work making "model once, compose everywhere" real).
  2. *Methods frontier* — the DNLP-canonicalization result
     (experiments/dnlp_vs_pypower/): canonicalization empirically improves
     AC-OPF solves on nondifferentiable-cost / flat-routing-landscape
     instances. **Empirically demonstrated; mechanism (reformulation vs solver)
     NOT yet isolated** — see [[dnlp-canonicalization-tractability-thesis]].
     State the empirical result plainly; mark the mechanism as open.
- **The framing that survives scrutiny:** a solid engineering niche PLUS an open
  methods question with early positive evidence, in a space (AC-OPF solve
  performance on pathological landscapes) the field-standard tools do not study.
  Do NOT claim feature parity or breadth superiority over the incumbents.

## 5. Over-claims to avoid

- "cvxopf does multi-period optimization PyPSA can't" — FALSE; PyPSA's linear
  multi-period optimizer is more scalable. Correct: "AC-fidelity multi-step
  optimization with convex->AC SoC coupling, which PyPSA's architecture does
  not attempt."
- "lossy battery is a capability PyPSA lacks" — FALSE; PyPSA has asymmetric-
  efficiency + standing-loss storage. The differentiator is the *layer it lives
  in* (AC feasibility + convex-relaxation hierarchy), not the device.
- "no one handles mode-dependent flow-battery standby loss via McCormick" —
  UNVERIFIED bet; see [[flow-battery-mode-dependent-standby-loss]].

## 6. Verification TODO before external/management use

- [ ] Confirm PowerModels.jl multi-network AC-OPF + storage coupling status.
- [ ] Run the planned cvxopf-vs-PowerModels 2x2 (DC line x PWL cost) — see
      `PLANNED_powermodels_2x2.md`. Highest-value addition to the methods
      claim; upgrades it from "beats legacy Pypower" to "differs from the field
      standard" and gives a same-solver handle on the open reformulation-vs-
      solver mechanism. Parked behind M16/M17; value conditional on confound
      control (pinned solver/options/x0/formulation).
- [ ] Confirm pandapower multi-period optimization cell.
- [ ] Re-read experiments/dnlp_vs_pypower/REPORT for exact measured numbers
      before quoting any performance figure.
- [ ] Date-stamp the table to the specific tool versions cited.
