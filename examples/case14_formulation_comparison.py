"""
Compare generator dispatch on the IEEE 14-bus case across all three
formulations: full AC-OPF, lossy DC OPF, and single-node DC dispatch.

The three formulations model progressively less of the network physics:

    ac            Full nonlinear AC power flow (voltages, reactive power,
                  real losses). Nonconvex; solved by IPOPT.
    lossy_dc      Convex DC approximation with quadratic line losses, but
                  no voltage/reactive modelling. Solved by CLARABEL.
    singlenode_dc Copper-plate: one bus, no branch flows, no losses.
                  Only enforces total generation == total load. CLARABEL.

Because each formulation optimizes a different objective (the DC losses
term is absent from AC's objective, and singlenode has no losses at all),
the objective values are not directly comparable. The interesting
comparison is the *dispatch* (per-generator Pg) and the *implied losses*
(total generation minus total load), which shrink to exactly zero in the
single-node model.

Run from the repository root:
    uv run python examples/case14_formulation_comparison.py
"""

import warnings

import numpy as np

from cvxopf.testcases import case14
from cvxopf.problem import build_opf
from cvxopf.results import extract_results


FORMULATIONS = ["ac", "lossy_dc", "singlenode_dc"]


def _solve(formulation, case):
    """Build and solve one formulation, suppressing the DC df_Q warnings."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf(case, formulation=formulation)
    build.solve()
    return build, extract_results(build)


def main():
    ppc      = case14()
    baseMVA  = ppc["baseMVA"]
    gen_bus  = ppc["gen"][:, 0].astype(int)     # external bus IDs
    ng       = ppc["gen"].shape[0]
    total_ld = ppc["bus"][:, 2].sum()

    print("=" * 64)
    print("cvxopf — case14 dispatch comparison across formulations")
    print("=" * 64)
    print(f"\nBuses       : {ppc['bus'].shape[0]}")
    print(f"Generators  : {ng}  (at buses {gen_bus.tolist()})")
    print(f"Total load  : {total_ld:.1f} MW")

    # Solve all three formulations
    results = {}
    for f in FORMULATIONS:
        _, r = _solve(f, case14())
        results[f] = r
        print(f"\n  [{f}] status={r['status']}, objective={r['objective']:.2f} $/hr")

    # ------------------------------------------------------------------
    # Per-generator dispatch table
    # ------------------------------------------------------------------
    print("\n" + "-" * 64)
    print("Per-generator dispatch, Pg (MW)")
    print("-" * 64)
    header = f"  {'Gen':>3}  {'Bus':>3}"
    for f in FORMULATIONS:
        header += f"  {f:>14}"
    print(header)
    print(f"  {'-'*3}  {'-'*3}" + "".join(f"  {'-'*14}" for _ in FORMULATIONS))
    for k in range(ng):
        row = f"  {k:>3}  {gen_bus[k]:>3}"
        for f in FORMULATIONS:
            row += f"  {results[f]['Pg'][k]:>14.3f}"
        print(row)

    # Totals row
    print(f"  {'-'*3}  {'-'*3}" + "".join(f"  {'-'*14}" for _ in FORMULATIONS))
    total_row = f"  {'sum':>3}  {'':>3}"
    for f in FORMULATIONS:
        total_row += f"  {results[f]['Pg'].sum():>14.3f}"
    print(total_row)

    # ------------------------------------------------------------------
    # Implied losses: total generation minus total load
    # ------------------------------------------------------------------
    print("\n" + "-" * 64)
    print("Total generation, implied losses, and objective")
    print("-" * 64)
    print(f"  {'Formulation':>14}  {'Total Pg (MW)':>14}  "
          f"{'Losses (MW)':>12}  {'Objective':>12}")
    print(f"  {'-'*14}  {'-'*14}  {'-'*12}  {'-'*12}")
    for f in FORMULATIONS:
        total_pg = results[f]["Pg"].sum()
        losses   = total_pg - total_ld
        print(f"  {f:>14}  {total_pg:>14.3f}  {losses:>12.3f}  "
              f"{results[f]['objective']:>12.2f}")

    print("\nNotes:")
    print("  - Losses = total generation - total load. Only the AC model")
    print("    physically routes power through the network, so only AC")
    print("    requires generation to exceed load (the real losses).")
    print("  - lossy_dc and singlenode_dc both enforce a LOSSLESS nodal")
    print("    balance (total Pg == total load). lossy_dc differs from")
    print("    singlenode_dc only by a quadratic loss PENALTY in its")
    print("    objective, which nudges the dispatch and raises the")
    print("    objective slightly (here by "
          f"{results['lossy_dc']['objective'] - results['singlenode_dc']['objective']:.2f} $/hr).")
    print("  - Objective values are NOT directly comparable across all")
    print("    three: each optimizes a different quantity.")


if __name__ == "__main__":
    main()
