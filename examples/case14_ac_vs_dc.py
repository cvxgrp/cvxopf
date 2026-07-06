"""
Comparison of AC-OPF and lossy DC OPF for the IEEE 14-bus test case.

Solves the same case14 problem with both formulations and prints a
side-by-side comparison of generator dispatch and key differences.

Observations to expect:
- DC objective is lower than AC: the DC formulation ignores reactive
  power and voltage constraints, so the feasible set is larger.
- DC Pg values differ from AC: without voltage/reactive constraints the
  generator dispatch is redistributed.
- AC total Pg exceeds load due to resistive losses. DC total Pg equals
  load exactly because DC losses appear in the objective but not in the
  flow conservation constraint.
- AC returns Vm and Va_deg; DC does not model voltages.
- AC returns Qg; DC does not model reactive power.

Run from the repository root:
    python examples/case14_ac_vs_dc.py
"""

import time

from cvxopf.testcases import case14
from cvxopf.problem import build_opf, OPFOptions
from cvxopf.results import extract_results


def main():
    print("=" * 70)
    print("cvxopf — case14 AC-OPF vs lossy DC OPF comparison")
    print("=" * 70)

    ppc       = case14()
    total_load = ppc["bus"][:, 2].sum()

    # ------------------------------------------------------------------
    # Solve AC-OPF
    # ------------------------------------------------------------------
    print("\nBuilding AC-OPF ...")
    build_ac = build_opf(ppc, formulation="ac", options=OPFOptions(init_flat=True))
    print(f"  Named variables : {len(build_ac.prob.variables())}")
    print(f"  Constraints     : {len(build_ac.prob.constraints)}")

    print("Solving AC-OPF with IPOPT ...")
    t0   = time.perf_counter()
    build_ac.solve()
    t_ac = time.perf_counter() - t0
    r_ac = extract_results(build_ac)
    print(f"  Status : {r_ac['status']}  ({t_ac:.3f}s)")

    # ------------------------------------------------------------------
    # Solve lossy DC OPF
    # ------------------------------------------------------------------
    print("\nBuilding lossy DC OPF ...")
    build_dc = build_opf(ppc, formulation="lossy_dc")
    print(f"  Named variables : {len(build_dc.prob.variables())}")
    print(f"  Constraints     : {len(build_dc.prob.constraints)}")

    print("Solving lossy DC OPF with CLARABEL ...")
    t0   = time.perf_counter()
    build_dc.solve()
    t_dc = time.perf_counter() - t0
    r_dc = extract_results(build_dc)
    print(f"  Status : {r_dc['status']}  ({t_dc:.3f}s)")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)

    print(f"\n  {'':22}  {'AC-OPF':>14}  {'Lossy DC OPF':>14}  {'Diff':>10}")
    print(f"  {'-'*22}  {'-'*14}  {'-'*14}  {'-'*10}")
    print(f"  {'Objective ($/hr)':<22}  {r_ac['objective']:>14.4f}  "
          f"{r_dc['objective']:>14.4f}  "
          f"{r_dc['objective'] - r_ac['objective']:>+10.4f}")
    print(f"  {'Solve time (s)':<22}  {t_ac:>14.3f}  {t_dc:>14.3f}")
    print(f"  {'Solver':<22}  {'IPOPT':>14}  {'CLARABEL':>14}")
    print(f"  {'nlp=True required':<22}  {'yes':>14}  {'no':>14}")
    print(f"  {'Named variables':<22}  "
          f"{len(build_ac.prob.variables()):>14}  "
          f"{len(build_dc.prob.variables()):>14}")
    print(f"  {'Constraints':<22}  "
          f"{len(build_ac.prob.constraints):>14}  "
          f"{len(build_dc.prob.constraints):>14}")

    # ------------------------------------------------------------------
    # Generator dispatch comparison
    # ------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("Generator dispatch (MW)")
    print("-" * 70)
    print(f"  {'Gen':>4}  {'AC Pg':>10}  {'DC Pg':>10}  {'Diff':>10}  "
          f"{'AC Qg (MVAr)':>14}")
    print(f"  {'-'*4}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*14}")
    ng = build_ac.data["ng"]
    for k in range(ng):
        diff = r_dc["Pg"][k] - r_ac["Pg"][k]
        print(f"  {k:>4}  {r_ac['Pg'][k]:>10.4f}  {r_dc['Pg'][k]:>10.4f}  "
              f"{diff:>+10.4f}  {r_ac['Qg'][k]:>14.4f}")

    print(f"  {'':>4}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*14}")
    print(f"  {'Total':<4}  {r_ac['Pg'].sum():>10.4f}  "
          f"{r_dc['Pg'].sum():>10.4f}  "
          f"{r_dc['Pg'].sum() - r_ac['Pg'].sum():>+10.4f}  "
          f"{r_ac['Qg'].sum():>14.4f}")

    print(f"\n  Note: AC total Pg ({r_ac['Pg'].sum():.2f} MW) > load "
          f"({total_load:.1f} MW) due to resistive losses.")
    print(f"  DC total Pg ({r_dc['Pg'].sum():.2f} MW) equals load exactly")
    print(f"  because DC losses appear in the objective but not in the")
    print(f"  flow conservation constraint (A @ p_flows + p_gen = Pd).")

    # ------------------------------------------------------------------
    # Bus voltages (AC only)
    # ------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("Bus voltages — AC only (DC formulation does not model voltages)")
    print("-" * 70)
    print(f"  {'Bus':>4}  {'Vm (p.u.)':>10}  {'Va (deg)':>10}")
    print(f"  {'-'*4}  {'-'*10}  {'-'*10}")
    nb = build_ac.data["nb"]
    for i in range(nb):
        print(f"  {i:>4}  {r_ac['Vm'][i]:>10.6f}  {r_ac['Va_deg'][i]:>10.4f}")

    # ------------------------------------------------------------------
    # Branch flows (DC only)
    # ------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("Branch real power flows — DC only")
    print("(AC branch flows not extracted by extract_results in this version;")
    print(" use build.variables['P'] to access the full (nb x nb) flow matrix)")
    print("-" * 70)
    f_max_MW = build_dc.data["f_max"] * build_dc.data["baseMVA"]
    branch   = ppc["branch"]
    print(f"  {'Branch':>6}  {'From':>5}  {'To':>5}  "
          f"{'Flow (MW)':>10}  {'Limit (MW)':>10}  {'At limit':>8}")
    print(f"  {'-'*6}  {'-'*5}  {'-'*5}  "
          f"{'-'*10}  {'-'*10}  {'-'*8}")
    for e in range(build_dc.data["nl"]):
        flow     = r_dc["p_flows"][e]
        limit    = f_max_MW[e]
        at_limit = abs(flow) >= 0.99 * limit
        f_bus    = int(branch[e, 0])
        t_bus    = int(branch[e, 1])
        marker   = " <--" if at_limit else ""
        print(f"  {e:>6}  {f_bus:>5}  {t_bus:>5}  "
              f"{flow:>10.4f}  {limit:>10.1f}{marker}")

    # ------------------------------------------------------------------
    # Key differences summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("Key differences")
    print("=" * 70)
    obj_diff_pct = (r_dc["objective"] - r_ac["objective"]) \
                   / r_ac["objective"] * 100
    print(f"""
  DC objective is {abs(obj_diff_pct):.2f}% {'lower' if obj_diff_pct < 0 else 'higher'} than AC.
  This is expected: the DC formulation relaxes voltage and reactive
  power constraints, enlarging the feasible set and allowing a lower
  (or equal) optimal cost.

  AC models:   Pg, Qg, Vm, Va_deg   — full nonlinear AC power flow
  DC models:   Pg, p_flows, p_net   — flow conservation + quadratic losses
  DC ignores:  Qg, Vm, Va_deg       — not present in DC results
""")


if __name__ == "__main__":
    main()