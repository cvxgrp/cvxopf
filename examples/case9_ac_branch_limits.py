"""
Binding AC branch-terminal limit on the 9-bus test case.

The first branch is tightened to 80 MVA. The default AC formulation enforces
that rating independently at both terminals. The two terminal apparent powers
need not be equal because the branch exchanges reactive power and terminal
voltages differ.

Run from the repository root:
    uv run python examples/case9_ac_branch_limits.py
"""

import numpy as np

from cvxopf.problem import build_opf
from cvxopf.results import extract_results
from cvxopf.testcases import case9


def main():
    case = case9()
    branch_row = 0
    rating_mva = 80.0
    case["branch"][branch_row, 5] = rating_mva

    build = build_opf(case, formulation="ac")
    build.solve()
    results = extract_results(build)

    s_from = results["branch_s_from"]
    s_to = results["branch_s_to"]
    assert results["status"] == "optimal"
    assert np.all(
        s_from[build.data["constrained_branch_indices"]]
        <= build.data["branch_rate_a_mva"][
            build.data["constrained_branch_indices"]
        ]
        + 1e-4
    )
    assert np.all(
        s_to[build.data["constrained_branch_indices"]]
        <= build.data["branch_rate_a_mva"][
            build.data["constrained_branch_indices"]
        ]
        + 1e-4
    )
    assert np.isclose(s_from[branch_row], rating_mva, atol=1e-4)
    assert s_to[branch_row] < s_from[branch_row]

    print(f"Status: {results['status']}")
    print(f"Branch {branch_row} rating: {rating_mva:.1f} MVA")
    print(f"From-terminal apparent power: {s_from[branch_row]:.4f} MVA")
    print(f"To-terminal apparent power:   {s_to[branch_row]:.4f} MVA")


if __name__ == "__main__":
    main()
