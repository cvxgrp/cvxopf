# `scripts/` — developer/maintenance scripts

These are **maintenance scripts**, not part of the `cvxopf` package. Each is a
self-contained [uv](https://docs.astral.sh/uv/) *inline-dependency script*: it
declares its own pins in a `# /// script` header and runs in an isolated
environment. Always run them with `uv run` from the repo root, e.g.

```bash
uv run scripts/generate_testcases.py
```

Do **not** run them against the main package environment, and do **not** run
them in CI — CI consumes their committed output artifacts, it does not
regenerate them.

| Script | Purpose | Output |
|---|---|---|
| `generate_testcases.py` | Import MATPOWER/Pypower cases into static Python case files | `src/cvxopf/testcases/case*.py` |
| `generate_pypower_fixtures.py` | Run Pypower AC-OPF to produce reference fixtures | `tests/fixtures/*_pypower_reference.json` |
| `generate_examples_readme.py` | Build `examples/README.md` from example docstrings/output | `examples/README.md` |
| `_probe_dcline_transform.py` | **Throwaway** validation probe for the DC-line transform (Gate 0b-iii) | none (asserts) |

> **Why Pypower is pinned to `numpy==2.2.6`.** `pypower==5.1.19` uses
> `numpy.in1d`, removed in numpy 2.3. The pin lives only in these scripts'
> headers — never add it to `pyproject.toml`. The DC-line case surfaces
> *additional* numpy-2.x incompatibilities in Pypower; see below.

---

## The `case9_dcline` case (Milestone 7 / HVDC)

Bringing Pypower's `t_case9_dcline` into the repo takes **two independent
artifacts**, produced by two scripts. Both ultimately derive from the same
upstream `pypower.t.t_case9_dcline`, but by separate paths, so one can serve as
an oracle for the other:

1. **Static case file** — `src/cvxopf/testcases/case9_dcline.py`
   (via `generate_testcases.py`). This is the *input* cvxopf solves in its own
   tests.
2. **Solved reference fixture** — `tests/fixtures/case9_dcline_pypower_reference.json`
   (via `generate_pypower_fixtures.py`). This is the *oracle* cvxopf is compared
   against.

Keeping these two paths independent is deliberate: if the fixture were built
from the local case file, comparing cvxopf against it would be circular.

### 1. Importing the case — `generate_testcases.py`

`generate_testcases.py` reads a Pypower case **dict** and emits a static
`case*.py` file (same format as the hand-written `case9.py`). It was extended to
emit the `dcline` / `dclinecost` tables when present (conditional, like the
`areas` block), and `t_case9_dcline` was added to its `CASES` list, producing
`case9_dcline()`.

This step reads the case dict only — it **never** calls `toggle_dcline` or
`runopf`, so it does not trigger the Pypower bugs described below. The
reactive/voltage/`loss0` columns are preserved faithfully in the case file;
they are only dropped later, at cvxopf's `hvdc_from_dcline` import boundary.

### 2. Generating the fixture — `generate_pypower_fixtures.py`

The fixture must be a *solved* Pypower AC-OPF. Normally you would activate the
DC lines with `toggle_dcline(ppc, 'on')` and call `runopf`. **That path is
broken under numpy 2.x**, so we do not use it.

#### Why `toggle_dcline` cannot be used here

`toggle_dcline` installs three `userfcn` callbacks. Two of the three are broken
under numpy 2.x + a dict `ppc`:

- **`userfcn_dcline_ext2int`** (builds the dummy generators): float index
  arrays; a `ppc.gencost = …` attribute-set on a dict; a wrong `np.zeros(a, b)`
  call; a float `nc` in `range()`; and an off-by-one gencost pad width.
- **`userfcn_dcline_int2ext`** (restores results): a shape-mismatched
  `zeros((ndc, 6))` concatenation onto the 4-row external `dcline` table.

Only the middle stage — **`userfcn_dcline_formulation`**, which adds the
terminal-coupling constraint — is clean. Monkeypatching the broken chain was
attempted and abandoned: each fix exposed the next.

#### What we do instead (self-contained solve)

We reproduce Pypower's dcline model directly, touching **none** of the broken
stages. A DC line is modelled the same way Pypower (and pandapower) model it:
**two dummy generators**, one at each terminal.

- **`_dcline_to_gens(ppc)`** — converts each in-service DC line into a pair of
  dummy generators (a "from" extraction gen, `PG = -Pf`, and a "to" injection
  gen, `PG = Pt`), sets the terminal buses to PV, and removes the
  `dcline`/`dclinecost` tables. The result is a standard MATPOWER case that a
  plain `runopf` can solve.
- **`_make_coupling_userfcn(orig)`** — re-adds Pypower's *clean* coupling
  constraint as our own `formulation`-stage userfcn:

  ```
  (1 - L1) * Pgf + Pgt == -L0 / baseMVA
  ```

  where `L0`/`L1` are the DC line's fixed/proportional loss coefficients. Unlike
  Pypower's version (which assumes the dummy gens are the last `2*ndc` rows), we
  locate each dummy gen's internal `Pg` column via `order['gen']['i2e']`, because
  `_dcline_to_gens` appends the dummies *before* `ext2int` reorders gens by bus.
  Without this constraint the "to" gens are unbounded zero-cost injections and
  the OPF is singular.
- **`dclinecost` is dropped before solving**, matching Pypower's own
  `pypower/t/t_dcline.py` (which does `del ppc0['dclinecost']`). The DC lines are
  therefore zero-cost dispatchable resources, and the broken ext2int cost branch
  is never needed.

#### How we know it is correct

Two layers of validation:

- **Gate 0b-iii — `_probe_dcline_transform.py`** (throwaway, run manually):
  proves `_dcline_to_gens` reproduces a *real* (float-coercion-patched)
  `toggle_dcline` run **row-for-row on the gen and bus tables** (BUS exact; GEN
  exact as a multiset, since `ext2int` reorders gens). See that file's docstring
  for the one deliberate gencost divergence (Pypower's sign-flip flips the wrong
  coefficient; ours is physically correct) — moot here since the fixture drops
  `dclinecost`.
- **In-script self-check — `_check_dcline_against_pypower`**: after solving, the
  script asserts the dummy-gen terminal quantities `[PF, PT, QF, QT, VF, VT]`
  match Pypower's own **hardcoded expected array** from `t_dcline.py`
  (`atol=1e-3`, absorbing solver tolerance). Generation fails loudly if the
  solve ever drifts from Pypower's published answer.

#### Approximations (important for the consuming test)

The fixture is a faithful Pypower AC-OPF, but it is an **approximate** oracle for
cvxopf's HVDC MVP, for two reasons the MVP does not model:

1. **Reactive terminal injections** — Pypower's AC dcline optimizes `QF`/`QT`
   within the terminal `Qmin`/`Qmax` bounds (e.g. row 0 solves to `QF=-10`,
   `QT=10`). The unity-PF MVP has no reactive HVDC term.
2. **`loss0` (fixed converter loss)** — row 0 has `loss0=1`, modelled here but
   dropped by the MVP (proportional-loss only).

Also note: the fixture's `Pg`/`Qg` arrays include the **dummy DC-line terminal
gens** (real gens first, then from-gens, then to-gens), so `Pg` has length
`ng + 2 * n_dcline_on`, not `ng`.

### When to delete these workarounds

`_probe_dcline_transform.py`, `_dcline_to_gens`, and `_make_coupling_userfcn`
exist solely to work around Pypower's numpy-2.x breakage. If a future
Pypower/numpy combination fixes `toggle_dcline`, this whole path can be replaced
with a plain `toggle_dcline(ppc, 'on')` + `runopf`, and the probe deleted.
Until then, the self-checks above are what make the generated fixture
trustworthy.
