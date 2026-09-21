# M17-S3 interval-35 hard-replanning diagnostic

## Status and conclusion

The exact interval-35 AC problem is modeled-feasible. IPOPT returned
`infeasible` from the project flat start, but all six predeclared alternate
initializations solved the identical archived problem with accepted primals,
exact terminal SoC, and residuals inside the frozen tolerances.

IPOPT's flat-start status is not a global infeasibility certificate. These
results demonstrate initialization sensitivity in this particular
formulation, CVXPY DNLP interface, IPOPT version, and solver configuration;
they do not establish a universal IPOPT defect.

The complete 14-record protocol remains formally incomplete because the
rebuilt preceding-window source reached a different accepted public solution
basin than its archived counterpart. The shifted-preceding attempt therefore
has new-source-basin provenance. This does not weaken the feasibility
conclusion established independently by the other accepted exact-problem
attempts.

The tracked [result manifest](S3_HARD_REPLAN_DIAGNOSTIC_RESULTS.json) records
the execution commit, environment, source fingerprints, summary, and hashes of
the ignored detailed artifacts.

## Execution record

The diagnostic ran from clean commit
`8dff8d1005a4773ce695e51e71d8cd466b5ccc47`. The Git tree and participating
source fingerprints were unchanged before and after execution. The frozen
solver stack was Python 3.13.2, CVXPY 1.9.2, cyipopt 1.7.0, and IPOPT 3.14.19.

All 14 canonical records were retained, all 14 solver calls executed, and all
14 starting points passed the production `x0` gate. Each IPOPT vector contained
745 model-owned coordinates and 185 CVXPY reduction auxiliaries.

## Exact archived problem

The primary problem used intervals `[35, 40)`, initial SoC
515.0979097002988 MWh, and the exact archived endpoint
849.9999996548939 MWh.

| Initialization | IPOPT status | Accepted | Objective | Maximum balance residual |
|---|---|---:|---:|---:|
| A: project flat start | `infeasible` | no | — | — |
| B: successful frozen-window solution | `optimal` | yes | 19,300.1523 | $3.6\times10^{-16}$ p.u. |
| C: target-free solution | `optimal` | yes | 19,300.1527 | $2.8\times10^{-16}$ p.u. |
| D: shifted preceding-window solution | `optimal` | yes | 19,300.1612 | $3.5\times10^{-14}$ p.u. |
| E: frozen perturbation $10^{-4}$ | `optimal` | yes | 19,300.1523 | $3.2\times10^{-16}$ p.u. |
| E: frozen perturbation $10^{-3}$ | `optimal` | yes | 19,300.1524 | $2.5\times10^{-16}$ p.u. |
| E: frozen perturbation $10^{-2}$ | `optimal` | yes | 19,300.1523 | $5.7\times10^{-16}$ p.u. |

Every accepted solve had zero reported terminal error, no voltage-bound
violation, and no thermal-limit residual. Five accepted objectives agree within
about $4\times10^{-4}$ objective units; the shifted-preceding solution is about
0.009 objective units higher and remains a valid feasibility witness.

Because A executed with a verified starting vector and was not accepted, while
B–E used the same variables, constraints, data, exact target, solver settings,
and acceptance gate, the scientific classification is
`modeled_feasible_initialization_dependent`.

## Target-rounding sensitivity

The matched-state matrix exposed an additional numerical sensitivity. At the
same replanned initial state and project flat initialization:

- the exact archived target, 849.9999996548939 MWh, returned `infeasible`;
- the canonical target, 850.0 MWh, returned an accepted optimum.

The named model-owned starting arrays, complete 930-coordinate IPOPT `x0`, and
reduced-layout signature were identical; no `x0` coordinate differed. Only the
terminal target parameter changed, by $3.45\times10^{-7}$ MWh. This is not a
meaningful physical change and provides strong nearby-problem sensitivity
evidence for this numerical stack. The exact-target alternate successes are
the decisive feasibility proof because they solve the original problem rather
than a nearby one.

## Incomplete source-reproduction gate

The frozen source and target-free source reproduced their authoritative public
solutions. The preceding-window reconstruction was accepted and satisfied all
residual gates, but differed from the archived public solution in objective and
multiple primal arrays. Its initial state also differed at floating-point
roundoff: 379.5262446425310 MWh in the frozen specification versus
379.52624464253097 MWh in the artifact.

The reconstructed initial state differs from the serialized archived value by
5.7e-14 MWh due to decimal representation; this is treated as numerically
identical and is not the basis for the source classification.

The reconstructed preceding objective was 18,373.6871, compared with
18,373.7470 in the archived run. It is correctly labeled
`new_accepted_source_basin`, and the shifted D record inherits that provenance.
Here, “new accepted source basin” is an empirical label for a materially
different accepted public solution in the observed solver run; it is not proof
that the two solutions occupy globally distinct mathematical basins. The label
makes the complete-protocol flag false under the predeclared rule. It does not
affect B, C, or E, each of which independently solved the exact problem from a
reproduced source.

## Implications for M17-S4

The experiment strengthens three controller requirements:

1. A local AC `infeasible` status cannot by itself be treated as proof that a
   communicated energy endpoint is physically impossible.
2. Initialization policy is part of the operational AC-solve contract for the
   hierarchical controller, not merely a performance option.
3. Failure handling must preserve the first attempt and may use an explicit,
   predeclared alternate-initialization sequence; it must not silently relax the
   target or execute an unaccepted action.

The result does not imply that every hard DC endpoint is AC-realizable. It
establishes only that this previously unresolved endpoint is realizable and
that the observed failure was caused by the local numerical solve in the
frozen stack. The separate soft-policy recursive-feasibility finding from S3
is unchanged.
