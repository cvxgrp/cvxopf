Subject: Case118 AC solve regression traced to CVXPY sparse dispatch

Hi all,

We tracked down a convergence regression in a three-hour Case118 AC-OPF problem.
With the same application code, physical inputs, complete starting point, and
native solver libraries, CVXPY 1.9.2 / sparsediffpy 0.3.0 converges in 70 IPOPT
iterations. Updating to 1.9.3 / 0.6.1 makes it hit the 3,000-iteration limit.
Both tests used time vectorization only, with no spatial P/Q batching.

The interesting part is that the objective, constraints, gradient, Jacobian values,
and three tested Lagrangian Hessians agree exactly at the starting point. What
changes is the Jacobian structure reported to IPOPT: the old stack stores 412,439
entries and the new one 36,167, largely because the new CVXPY conversion routes
mostly-zero dense constant matrices through sparse operations. After removing
exact zeros, the Jacobians are identical at this point.

We then kept both new package versions and disabled that automatic conversion in
one diagnostic process. This restored the old Jacobian structure and convergence
in 70 iterations, with exactly the historical accepted solution. This points to
sensitivity to the derivative representation, potentially through sparse linear
algebra ordering, rather than a demonstrated derivative-value bug. We have not
yet identified the mechanism inside IPOPT/MUMPS or checked derivatives along the
whole failed trajectory.

We also repeated the interval with the current application in all four
time/spatial vectorization combinations, retaining the new packages and disabling
automatic sparse conversion. All four converged and passed our acceptance checks:
339 iterations with neither, 70 with time only, 190 with spatial only, and 76 with
both. Time-only and both took about 32 seconds in IPOPT. All four had failed at
3,000 iterations under default dispatch. All modes use sparse P/Q variables;
the flags control expression batching. Their objectives are very close, but
reactive allocations differ, so these are not identical operating points.

These results support retaining the new packages and spatial batching with an
explicit compatibility policy. We have not adopted a project-wide setting or
validated robustness across a broader interval sample yet. We are preparing a CVXPY issue
with the controlled results, logs, and a small example illustrating the sparsity
change. The small example does not itself reproduce the convergence failure;
the full failure still uses our retained Case118 fixture and causal start.

Has anyone seen a similarly large IPOPT convergence change after removing explicit
zeros from the Jacobian structure, or found a supported way to retain a chosen
derivative representation for a problem like this?

Best,
Bennet

---
Draft only; not sent. Update the project-configuration paragraph once the owner
has selected and validated the stabilization approach.
