# Four-way hour-6047 repeat with automatic sparse conversion disabled

Owner authorized on 2026-09-22 to evaluate retaining the new packages with a
compatibility policy. This is an experiment-only evaluation, not adoption of a
project-wide workaround.

Repeat `none`, `time_only`, `spatial_only`, and `both` in that order. All use sparse
P/Q storage. Each condition gets one fresh sequential worker, the frozen three-hour
hour-6047 primary request, causal start and SoC endpoints, unchanged numerical
options and acceptance criteria, and no helpers/recovery/retries. Ordinary solver
rejection permits the next condition; a process or structured execution error stops
the run. No timeout or iteration override is added.

Use current checkout code and CVXPY 1.9.3/sparsediffpy 0.6.1. In each worker set
`cvxpy.settings.SPARSE_DENSITY_THRESHOLD=0.0` before model/oracle construction.
That is the sole mathematical-representation policy change from the previous
four-way test. As before, `print_level=5` is forwarded only at the native boundary
to retain IPOPT iteration diagnostics. No installed dependency files are edited.

Use `diagnose_primary.prepare` unchanged to reconstruct and verify each physical
start and build the selected representation. Compare the complete canonical x0,
normalized layout, raw/assigned named starts, and request against the corresponding
retained four-way condition. Never compare native coordinates between different
temporal representations without their explicit mapping.

Bind the current source contents, plan, runner, request, dependency inventory, and
native library hashes. Computational source files must still match the prior
four-way binding; record intervening relocation/reporting changes separately.
This new authorized experiment follows the content-hash binding used for the
subsequent environment comparison; it does not modify or bypass the older runner's
clean-commit launch gate. Leave new work unstaged for the owner.

Before solving, prepare all four models through actual canonicalization/oracle
initialization to a mocked native solve boundary. Check all full starts and layouts
against the prior run; retain bounds and effective options for real-launch checks.
Review the harness and run Ruff. Verify process/thermal telemetry with the actual
launch permissions. The owner's external-fan confirmation remains in effect.
Capture telemetry before the first solve, and keep bound source files frozen until
all workers finish. The supervisor records process observations per condition.

Store raw evidence in a fresh
`results/case118_6047_four_way_dense_control/` directory. Report statuses, acceptance,
iterations, native and solve-phase timings, Jacobian/Hessian stored structure sizes,
physical audits and native termination quantities. Compare against the earlier
default-dispatch four-way results, clearly distinguishing successful solves from
times to rejection. This is one observation per condition, not a timing benchmark
or proof of general robustness. Use the results to discuss stabilization before
changing the main project's dependency constraints, defaults, or execution policy.
