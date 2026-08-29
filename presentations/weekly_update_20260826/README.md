# CVXOPF project update — 2026-08-26

This directory contains the Beamer source for the August 26, 2026 project
meeting. Its visual structure follows the August 20 PACT weekly-update deck and
uses the accompanying `talk_defs.tex`.

Build from this directory with:

```sh
uv run --with matplotlib --with pandas python make_figures.py
latexmk -pdf cvxopf_project_update.tex
```

`make_figures.py` reconstructs presentation-specific figures from the retained
battery-terminal, M17, and Case118 experiment artifacts. The deck also uses the
curated 96-hour storage-control figure from the battery-terminal experiment.

Generated PDFs and LaTeX build products are ignored; commit the Beamer source,
figure-generation code, and supporting source files instead.

The deck includes the August 25 S4 annual-outer resource-boundary result:
macOS terminated the detached worker during the repeated
construction/canonicalization phase after reporting extreme compressed-memory
pressure. The Case118 annual experiment is therefore paused pending M14
time-vectorized multistep construction; the result is not classified as OPF
infeasibility.
