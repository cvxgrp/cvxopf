# Report-only SOCP transfer preflight

Authorized scope: implement non-solving SOCP point screens, bounded local
battery-schedule certificates, and a retrospective back-test against the
18 retained original Tracy transfers. No new OPF solves, retries, repairs,
physical constraint changes, numerical-policy changes or historical writes.

Hourly point screens reuse `audit_socp_relaxation` and
`recover_socp_voltage`, their existing rank/cycle tolerances, a complete
fundamental-cycle check and deterministic short cycles of lengths 3–4.
Undefined phases remain unavailable. Cone/cycle failures describe the
retained point, not the existence of another AC realization at its battery pins.

The schedule certificate reuses the frozen `tracy_battery_transfer_loop`
implementation on all 24 hourly input slices. It is explicitly a single
topology-specific template, not a generic infeasibility solver. A root box
checks necessary exports; positive export on both forward legs triggers at
most 64 interval boxes. Non-applicability, absence of this directional trigger
and exhausted bounds remain unresolved, never accepted or feasible.

The three report categories are `audited_recovered_witness`,
`certified_incompatible` and `unresolved`. Recovered witnesses require an
independent complete physical audit, matched source/target physical inputs
and original battery/energy/endpoint checks. They are not new optimizer
acceptances or dispatch authorizations. Existing audited AC results are
separate historical labels, not manufactured preflight witnesses.

Back-test all 18 retained donor transfers and six retained AC controls.
Reconstruct/replay completed native evidence without optimization, verify
finalized supervision, resources, manifests, starts and source hashes.
Preserve native rejections, timeouts and source E3+ numerical flags separately.
Any contradiction against an audited AC witness is a diagnostic failure and
halts reporting. Compare original historical bytes before and after.

This template was selected after observing hour 2452. Back-test results are
retrospective consistency evidence, not holdout accuracy or a global guarantee.
No threshold is tuned to make failed transfers separable. Only a rigorous
certificate rules out a numerical initialization search; a warning alone does not.

Run from the repository root:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache \
uv run --extra dev python -m experiments.tracy_socp_transfer_preflight.preflight --backtest
```

The CLI emits reports to stdout only and blocks `cvxpy.Problem.solve`.
Production models, historical experiment sources and bound evidence remain
untouched. New code and selected evidence stay unstaged in this directory.
