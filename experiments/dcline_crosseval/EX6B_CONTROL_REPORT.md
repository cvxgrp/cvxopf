# EX6-(B) control: the runpf drift test is invalid as a feasibility instrument

**Date:** 2026-07-14
**Status:** Negative result. EX6-(B) and its control are DISCARDED as a
feasibility test. They cannot distinguish a feasible point from an infeasible
one. Do not read results/ex6b_powerflow.txt as evidence about C* feasibility.

## Question

EX6-(B) (results/ex6b_powerflow.txt) fed cvxopf C-star through Pypower runpf on
the neutralized dcline case and measured how far the solved voltages drifted
from C-star voltages. C-star drifted max|dVm|=0.0108 pu, max|dVa|=0.87 deg,
slack Pg +0.54 MW. Question: is that ~1pct drift a real signal that C-star is
infeasible in Pypower network, or an artifact of the test harness?

## Method (the control)

Run the IDENTICAL harness on P-star, the native runopf optimum of the same
neutralized case. P-star is by construction a converged solution of that case,
so it MUST be a runpf fixed point to solver tolerance (~1e-8) IF the harness
is faithful. Feeding a solver its own solution and getting it back unchanged
is the definition of consistency. Any drift on P-star is pure harness error.

Harness (both runs identical): neutralized case (branch rateA->1e5, dummy
Qmin=Qmax=0, terminals PV->PQ), real-gen buses set PV at the point Vm with
VG pinned, dummy gens set as fixed P injections, coupling userfcn active,
runpf, then compare solved Vm/Va back to the point. Only the input dispatch
differs: C-star (mapped) vs P-star (native Pypower rep).
Scripts: _ex6b_powerflow.py (C-star), _ex6b_control_pstar.py (P-star).

## Results

| metric | C-star (EX6-B) | P-star (control) |
|---|---|---|
| max abs dVm | 0.0108 pu | 0.0411 pu (bus 30) |
| max abs dVa | 0.87 deg | 0.56 deg |
| slack Pg shift | +0.54 MW | -0.06 MW |
| runpf success | True | True |

Raw outputs: results/ex6b_powerflow.txt, results/ex6b_control_pstar.txt.

P-star, a KNOWN-feasible native runopf solution, drifts 0.0411 pu at bus 30 --
about 4x C-star drift. The largest drift is at bus 30 (a DC from-terminal) in
both runs.

## Interpretation

The control is dispositive: the harness manufactures large drift even on a
known-feasible point. Since P-star (feasible by construction) drifts MORE than
C-star, the harness cannot be used to argue C-star is infeasible. Both prior
readings of the C-star drift were therefore wrong -- including the second
(the drift is not a real infeasibility signal).

### Why the harness lies

runpf and runopf freeze different variable sets. The OPF optimized generator Q
and terminal voltages jointly; the PF harness instead pins real-gen buses PV at
the OPF Vm (Q free), holds dummy gens as fixed P (terminal V free), and lets
the slack absorb real mismatch. Feeding a PF solver this SUBSET of the OPF
solution does not reproduce the OPF operating point, because the PF has
different fixed/free variables than the OPF did. So even the native P-star does
not return. This is a runpf-vs-runopf variable-freedom mismatch.

## Conclusion and next step

- EX6-(B) and its control are DISCARDED as a feasibility instrument. Keep the
  files (lab-notebook discipline) but do not cite ex6b_powerflow.txt as
  evidence about C-star feasibility.
- The EX6 question (is C-star feasible in neutralized Pypower) is still open.
- Correct instrument: direct nodal-balance residual S = V conj(Ybus V) at
  C-star, with NO solver and NO PV/slack/terminal freedom -- just check whether
  C-star dispatch satisfies the network equations pointwise. cstar_full.json
  (now includes Qg, p_net, q_net) is prepared for this. Cross-check the
  reconstructed injections against cvxopf own p_net/q_net for the DC sign
  (LOG B.4), do not re-derive it.

## Methodological note

A control saved us from publishing a wrong verdict. Two rounds of armchair
reasoning about the drift (dismiss / believe) both failed; running the same
test on a known-feasible point settled it empirically in one shot. When a test
produces a number whose meaning is unclear, run it on a known-answer control
before interpreting.
