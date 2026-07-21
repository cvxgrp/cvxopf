# examples/case9_hvdc_dc.py
"""
Lossy DC OPF with HVDC transmission links on case9_dcline (single step).

Demonstrates the HVDC MVP (Milestone 7) in the convex lossy DC formulation:
  - HVDC links imported from a MATPOWER ``dcline`` table via
    ``hvdc_from_dcline`` (the realistic entry point)
  - Signed nodal injections ``p_hvdc_in`` (from-bus) / ``p_hvdc_out``
    (to-bus), Convention B (positive = injection into the grid)
  - Proportional converter loss on fixed-direction links
    (``p_out = -(1 - loss_frac) * p_in``)

case9_dcline has three in-service DC links:
  link 0:  bus 30 -> bus  4, box [1, 10] MW, 1% loss
  link 1:  bus  7 -> bus  9, box [2, 10] MW, lossless
  link 2:  bus  5 -> bus  9, box [0, 10] MW, 5% loss

Unlike the AC example, the DC formulation has no reactive power and no bus
voltages, so results carry no ``Qg``/``Vm``/``Va_deg`` (only ``Pg``,
``p_flows``, ``p_net``, plus the HVDC keys). The HVDC model itself is
identical to AC (signed injections, proportional loss).

Note: a UserWarning is emitted at import time because link 0 carries a
nonzero MATPOWER ``loss0`` (fixed converter loss), which the MVP drops.
Full fixed-loss modelling is deferred to Milestone 15.

Usage:
    uv run examples/case9_hvdc_dc.py
"""

import warnings

import numpy as np

from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.problem import build_opf
from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.results import extract_results


def main():
    ppc = case9_dcline()

    print("Importing HVDC links from the dcline table...")
    print("(UserWarning expected: link 0 has a nonzero loss0, dropped by the MVP)")
    print()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        links = hvdc_from_dcline(ppc["dcline"])
        for w in caught:
            print(f"Warning: {w.message}")
    print()

    build = build_opf(ppc, formulation="lossy_dc", hvdc=links)
    build.solve()
    r = extract_results(build)

    # ------------------------------------------------------------------
    # Print results
    # ------------------------------------------------------------------
    print(f"Status:     {r['status']}")
    print(f"Objective:  {r['objective']:.4f} $/hr")
    print()

    print(
        f"{'Link':>4}  {'from->to':>10}  {'p_in (MW)':>10}  "
        f"{'p_out (MW)':>11}  {'loss (MW)':>10}  {'loss %':>7}"
    )
    print("-" * 62)
    for k, link in enumerate(links):
        print(
            f"{k:>4}  {f'{link.from_bus}->{link.to_bus}':>10}  "
            f"{r['p_hvdc_in'][k]:>10.4f}  {r['p_hvdc_out'][k]:>11.4f}  "
            f"{r['hvdc_loss'][k]:>10.4f}  {link.loss_percent:>7.2f}"
        )
    print()

    print(
        f"Generator dispatch Pg (MW): "
        f"{np.array2string(np.round(r['Pg'], 3), separator=', ')}"
    )
    print()

    # ------------------------------------------------------------------
    # Verify the proportional-loss law on fixed-direction links
    # ------------------------------------------------------------------
    print(
        "Loss-law verification (p_out == -(1 - loss_frac) * p_in on "
        "fixed-direction links):"
    )
    for k, link in enumerate(links):
        loss_frac = link.loss_percent / 100.0
        expected_out = -(1.0 - loss_frac) * r["p_hvdc_in"][k]
        residual = abs(r["p_hvdc_out"][k] - expected_out)
        print(
            f"  link {k}: p_out={r['p_hvdc_out'][k]:.4f}  "
            f"expected={expected_out:.4f}  residual={residual:.2e}"
        )
    print()

    print("HVDC loss non-negative (all links):", bool(np.all(r["hvdc_loss"] >= -1e-6)))
    print()

    # DC formulation carries no reactive power or bus voltages.
    print("DC results carry no reactive power / voltage keys:")
    print(f"  'Qg' in results:     {'Qg' in r}")
    print(f"  'Vm' in results:     {'Vm' in r}")
    print(f"  'Va_deg' in results: {'Va_deg' in r}")


if __name__ == "__main__":
    main()
