---
name: flow-battery-mode-dependent-standby-loss
description: User has research-code math + prototype for a lossy flow battery with on/off-state-dependent standby loss (bilinear nonconvexity); McCormick relaxation deployed, boolean-exact as oracle; low priority, post-M16/M17
metadata:
  type: project
---

# Lossy flow battery with mode-dependent standby loss

User has working math and a prototype (in research code / notebooks, **not yet
in cvxopf**) for a lossy **flow battery** whose standby / self-discharge loss
differs between **off/idle state and on/active state**. This adds a
nonconvexity beyond ordinary lossy storage.

## The nonconvexity

Standby-loss rate is mode-dependent:
`gamma_eff[t] = z[t]*gamma_on + (1 - z[t])*gamma_off`, where `z[t]` is a
discrete on/off state indicator and `gamma_on != gamma_off`. The loss applies
to the state of charge in the SoC **dynamics** equation. Written in the
**backward form** to match the existing cvxopf convention
(`_make_storage_soc_constraints`: `soc[t] = soc[t-1] - b[t]*delta`), which also
gives the clean T=1 degenerate case (dynamics collapse to a starting boundary
condition only):

```
soc[t] = (1 - gamma_eff[t])*soc[t-1] + eta_in*charge[t]*delta
                                     - (1/eta_out)*discharge[t]*delta   (t>=1)
soc[0] = (1 - gamma_eff[0])*initial_soc + eta_in*charge[0]*delta
                                        - (1/eta_out)*discharge[0]*delta
```

Bilinear (nonconvex) term: `w[t] := z[t] * soc[t-1]` (binary x continuous).
Expand: `gamma_eff[t]*soc[t-1] = gamma_off*soc[t-1] + (gamma_on - gamma_off)*w[t]`.

**INDEX-CONVENTION FLAG (user to confirm against notes):** user derived the
loss as applied to "SoC at time t" in a forward `t+1 = f(t)` statement. Under
backward indexing the decayed state entering step t's update is `soc[t-1]`, so
the bilinear term references `soc[t-1]`. If the derivation actually decays the
just-computed `soc[t]` (implicit form), that differs and must be corrected.

## McCormick envelope (deployed, convex path)

For `w = z*s`, `s = soc[t-1] in [0, S_max]`, `z in [0,1]`:
```
w >= 0
w >= s - S_max*(1 - z)
w <= S_max*z
w <= s
```
Exact at `z in {0,1}`; convex hull of `w = z*s` over the box. All affine, so
DCP-clean — the DCP-per-expression check doubles as a correctness check on the
envelope transcription (see [[cvxpy-affine-equality-rule]] and the DCP boundary
invariant in CLAUDE.md).

## Boolean-exact model as validation oracle

CVXPY **does** support `boolean=True` / `integer=True` (canonicalizes to a
MILP/MI-conic problem for a mixed-integer backend) — it is NOT a framework
limitation. The exact on/off model is deliberately **avoided in the deployed
path** (NP-hard; breaks the global-optimality and DCP-convexity guarantee;
fractures the convex->AC formulation ladder) but kept as **ground truth** to
measure the McCormick relaxation gap / tightness. Deploy the relaxation, cite
the exact model.

## Open question (unresolved, user to settle from notes)

Is `z` truly binary on/off, or is there a partial-power regime where the loss
interpolates? A continuous/partial regime would change the relaxation entirely
(the bilinear-in-binary structure and hence McCormick would not apply as-is).

## Priority and placement

**Low / not pressing (user's words).** Rides on the M16 component architecture
and the deferred lossy-storage machinery; natural home near the M12 / M15
lossy-storage milestones, **after M16 and M17**. Saved now so it resurfaces at
the lossy-storage milestone without pulling focus from current M16 work. See
[[m16-in-flight-record]].

## Unverified positioning claim

User's plausible bet — that mainstream power-system software (PyPSA,
pandapower, PowerModels.jl, MATPOWER) has not handled mode-dependent
flow-battery standby loss via McCormick envelopes — is **UNVERIFIED**. Earlier
ecosystem search did not cover flow-battery mode-dependent losses. Treat as a
hypothesis to check, not a fact.
