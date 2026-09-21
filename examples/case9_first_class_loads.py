"""Compare automatic MATPOWER load import with explicit first-class loads.

``loads=None`` preserves the case-file workflow: every MATPOWER bus row is
converted internally to a fixed ``Load`` with deterministic identity. Users
may instead supply explicit ``Load`` objects when device identity, policies,
or identity-aligned time series are needed. The two representations below are
numerically equivalent because they contain the same signed active/reactive
demand.

Usage:
    uv run examples/case9_first_class_loads.py
"""

import numpy as np

from cvxopf import Load, build_opf, extract_results
from cvxopf.testcases import case9


def main():
    case = case9()
    explicit_loads = [
        Load(
            bus=int(row[0]),
            p_load_mw=float(row[2]),
            q_load_mvar=float(row[3]),
            device_id=f"explicit_bus_{int(row[0])}",
        )
        for row in case["bus"]
    ]

    imported = build_opf(case, formulation="ac")
    explicit = build_opf(
        case,
        formulation="ac",
        loads=explicit_loads,
    )
    imported.solve()
    explicit.solve()
    imported_result = extract_results(imported)
    explicit_result = extract_results(explicit)

    print("case9 load representation comparison")
    print(f"automatic IDs : {', '.join(imported.data['load_device_ids'])}")
    print(f"explicit IDs  : {', '.join(explicit.data['load_device_ids'])}")
    print(f"load devices  : {explicit.data['nload']}")
    print(f"automatic obj : {imported_result['objective']:.3f}")
    print(f"explicit obj  : {explicit_result['objective']:.3f}")
    print(
        "max |Pg diff| : "
        f"{np.max(np.abs(imported_result['Pg'] - explicit_result['Pg'])):.2e} MW"
    )
    print(
        "max |Qg diff| : "
        f"{np.max(np.abs(imported_result['Qg'] - explicit_result['Qg'])):.2e} MVAr"
    )
    print(
        "max |load diff|: "
        f"{np.max(np.abs(imported_result['p_load'] - explicit_result['p_load'])):.2e} MW"
    )
    print(
        "max |Q load diff|: "
        f"{np.max(np.abs(imported_result['q_load'] - explicit_result['q_load'])):.2e} MVAr"
    )


if __name__ == "__main__":
    main()
