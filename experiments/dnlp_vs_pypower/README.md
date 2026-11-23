# DNLP vs Pypower on AC optimal power flow — proof by code

> **Superseded HVDC comparison.** The old runs did not solve the same
> physical problem. The importer reversed the DC-link bounds, the historical
> 5 → 9 transfer created 0.5 MW, and the cross-check did not establish parity
> with PYPOWER's limits. Neither the reported cost gaps nor the routing-versus-loss
> explanation establishes a solver advantage. The no-HVDC controls are not
> affected by these defects. See the
> [corrected four-case benchmark results](../case9_matched_comparison/REPORT.md)
> and the [repair plan](../../plans/dcline-import-and-case9-comparison.md).

This folder contains a written report (`REPORT.tex`, LaTeX source) and a demo
(`demo.py`) retained as historical evidence. The demo now stops before solving
so it cannot emit the superseded verdict against the corrected importer.

## Historical claim (withdrawn for HVDC)

On the 9-bus AC-OPF, two methods for solving the *same* problem are compared:

- **the DNLP method** — cvxopf (CVXPY, canonicalized through disciplined
  nonlinear programming, solved by IPOPT), and
- **the standard (Pypower/pips) method** — the same problem handed directly to
  Pypower's interior-point solver.

Two features are toggled on and off (piecewise-linear generator costs; HVDC
lines), giving four variants. Each feature alone: the two methods (mostly) agree.
Both together: the standard method returns a solution ~14% more expensive, and
the DNLP solution is verifiably feasible in the standard method's own problem.
## Historical procedure (do not run as a current comparison)

From the repository root, in the main cvxopf environment:

```
uv run --active python experiments/dnlp_vs_pypower/demo.py
```

The demo solves the four variants with the DNLP method live and compares against
`reference_pypower.json`. It prints (1) the 2x2 objective table, (2) the
suboptimality proof for the combined cell, (3) a PASS/FAIL check that the small
smooth-cost routing gap is a generation-cost effect rather than a loss effect,
and (4) the generator marginal-cost slopes that show why.

## Files

- `REPORT.tex` — the written report (setup, observations, conclusions). Compile with `pdflatex REPORT.tex`.
- `demo.py` — the archived exhibit; normal execution stops before any solve.
- `reference_pypower.json` — the committed Pypower reference solutions.
- `generate_pypower_reference.py` — regenerates `reference_pypower.json` in an
  isolated Pypower sandbox. **Not needed to run the demo.** The two no-DC cells
  are copied from the repository's committed, test-validated fixtures; the two
  DC cells are computed with the neutralized dcline model. Regenerate with
  `uv run generate_pypower_reference.py` (uses an inline dependency header, so
  `uv` provisions the pinned pypower/numpy automatically).

## Why the Pypower side is precomputed

cvxopf runs in the main environment; Pypower requires an isolated, pinned
environment (`pypower==5.1.19`, `numpy==2.2.6`) and the two cannot share a
Python process. The Pypower reference is therefore generated once and committed,
mirroring how the repository's own Pypower-oracle test fixtures work.
