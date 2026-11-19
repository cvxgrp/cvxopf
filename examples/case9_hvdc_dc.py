# examples/case9_hvdc_dc.py
"""
Lossy DC OPF with HVDC transmission links on case9_dcline (single step).

The three active links allow forward transfers as follows:
  link 0:  bus 30 -> bus  4, 1–10 MW sent, 1% proportional loss
  link 1:  bus  7 -> bus  9, 2–10 MW sent, lossless
  link 2:  bus  5 -> bus  9, 0–10 MW sent, 5% proportional loss

``hvdc_from_dcline`` converts these limits to from-bus injection bounds.
The printed values are signed injections into the buses, in MW. Sending
10 MW on the 5 -> 9 link gives -10 MW at bus 5 and +9.5 MW at bus 9.

Unlike the AC example, the DC formulation has no reactive power and no bus
voltages, so results carry no ``Qg``/``Vm``/``Va_deg`` (only ``Pg``,
``p_flows``, ``p_net``, plus the HVDC keys). The HVDC model itself is
identical to AC (signed injections, proportional loss).

The import warns about omitted converter terms, including the fixed loss
on 30 -> 4 and reactive/voltage settings. The model uses unity power factor.

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
    print("Positive injection supplies a bus; negative injection draws from it.")
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
    print(f"Objective:  {r['objective']:.4f} $")
    print()

    print(
        f"{'Link':>4}  {'from->to':>10}  {'From inj. (MW)':>15}  "
        f"{'To inj. (MW)':>13}  {'loss (MW)':>10}  {'loss %':>7}"
    )
    print("-" * 69)
    for k, link in enumerate(links):
        print(
            f"{k:>4}  {f'{link.from_bus}->{link.to_bus}':>10}  "
            f"{r['p_hvdc_in'][k]:>15.4f}  {r['p_hvdc_out'][k]:>13.4f}  "
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
    print("Direction-specific loss-law verification:")
    for k, link in enumerate(links):
        loss_frac = link.loss_percent / 100.0
        if link.p_min_mw >= 0:
            expected_out = -r["p_hvdc_in"][k] / (1.0 - loss_frac)
        elif link.p_max_mw <= 0:
            expected_out = -(1.0 - loss_frac) * r["p_hvdc_in"][k]
        else:
            expected_out = -r["p_hvdc_in"][k]
        residual = abs(r["p_hvdc_out"][k] - expected_out)
        print(
            f"  link {k}: to-bus injection={r['p_hvdc_out'][k]:.4f} MW  "
            f"expected={expected_out:.4f}  residual={residual:.2e}"
        )
        assert residual <= 1e-6, (
            f"HVDC link {k} loss-law residual {residual:.3e} MW "
            "exceeds tolerance"
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
