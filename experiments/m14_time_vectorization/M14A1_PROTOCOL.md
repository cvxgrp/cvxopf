# M14a.1 leaf-bound qualification protocol

M14a.1 qualifies CVXPY `Variable(bounds=...)` separately for AC, lossy DC,
and single-node DC before M14b can select it for production vectorized
assembly. A convex result never authorizes AC.

The isolated Case9 fixture uses genuine time-last variables over three hourly
intervals. For each formulation it executes a fixed 2 × 2 matrix:

- explicit inequalities versus leaf bounds; and
- static bounds represented by zero-copy NumPy broadcast views versus fully
  time-varying bound arrays.

Both encodings receive identical numerical arrays, objective terms, equations,
initial values, canonicalization backend, and solver options. Lossy DC and
single-node DC use SCIPY canonicalization and CLARABEL. AC uses its own dense
nonlinear nodal equations and IPOPT; no inference is made from the convex
paths. This isolated AC graph does not reproduce the production lifted P/Q,
branch-terminal, component-assembly, or storage-terminal-policy graph in which
a Qg leaf bound previously changed solver behavior. AC therefore retains
explicit inequalities regardless of the isolated result until a production-
structure test retires that risk. The fixture covers generator real-power
boxes in all formulations, lossy-DC branch-flow boxes, and isolated AC
reactive-generation and voltage boxes. Component-specific boxes introduced
during M14b remain subject to focused equivalence tests; this gate does not
silently authorize untested boxes.

Each arm runs in a fresh process so solver-stack state and process-lifetime
peak RSS are not inherited from another formulation or encoding. Each arm
retains source-object counts, explicit inequality counts, scalar
dimensions, solver behavior, objective, result arrays, independently
reconstructed residuals, and phase timings. Convex arms additionally retain
SCIPY canonical cone dimensions and sparse coefficient nonzeros. Pairwise
arms also run a controlled convex probe for every candidate variable family.
The probe alternates coordinates between lower and upper faces and drives the
unconstrained minimizer outside the box, requiring both faces to bind. Every
standalone probe is a DCP-valid box-constrained least-squares problem and uses
CLARABEL with SCIPY canonicalization; it does not route a convex QP through
DNLP. The complete AC qualification fixture separately exercises the leaf
bounds through IPOPT/DNLP.

AC active and reactive balance are independently reconstructed numerically
from retained `v`, `theta`, `Ybus`, `Pg`, `Qg`, `Pd`, and `Qd`; CVXPY's
constraint-violation evaluator is not used as the physical audit. Pairwise
qualification requires both arms to be accepted and objective/real-dispatch
absolute residuals no larger than `2e-4`; each physical residual must be no
larger than `1e-6`. Raw differences are retained for every modeled variable,
but reactive dispatch, voltage, angle, and network-flow coordinates are not
accidental equality gates when the optimum does not uniquely determine them.
These are short deterministic equivalence tolerances, not claims about all
networks or all solver stacks.

The authoritative run requires a clean committed tree, records the exact Git
commit, complete M14 source fingerprint, machine, Python, CVXPY, CLARABEL,
cyipopt, IPOPT, NumPy, and SciPy versions, and writes
`M14A1_RESULTS.json` immutably:

```bash
uv run python -m experiments.m14_time_vectorization.run_m14a1
```

Lossy DC and single-node DC select leaf bounds only if both static and
time-varying pairs and every binding probe pass. AC records the same isolated
evidence but conservatively retains explicit inequalities. Every decision is
local to this frozen gate and remains visible in M14b structural provenance.
