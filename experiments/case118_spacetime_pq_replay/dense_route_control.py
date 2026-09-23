"""Bounded diagnostic: new dependencies with density-based sparse dispatch disabled.

This changes one CVXPY setting in this fresh process only. It is not a proposed
project-wide workaround. The historical model and solver options remain frozen.
Pass --dry-run to validate the call without native optimization.
"""

import json
from pathlib import Path
import sys

import prepare_historical as prep


def main():
    sys.path[:0] = [str(prep.SOURCE / "src"), str(prep.SOURCE)]
    from experiments.case118_vectorization_replay import worker  # noqa: F401
    import cvxpy as cp
    import solve_historical

    if "--arm" not in sys.argv or sys.argv[sys.argv.index("--arm") + 1] != "b":
        raise ValueError("This control requires the new dependency environment")
    original = cp.settings.SPARSE_DENSITY_THRESHOLD
    if original != 0.05:
        raise ValueError("Unexpected original density threshold")
    cp.settings.SPARSE_DENSITY_THRESHOLD = 0.0
    solve_historical.main()
    output = Path(sys.argv[sys.argv.index("--output") + 1])
    (output / "density_control.json").write_text(json.dumps(dict(
        setting="cvxpy.settings.SPARSE_DENSITY_THRESHOLD", original=original,
        diagnostic_value=0.0, scope="This process only; historical time-only model",
        control_sha256=prep.digest(Path(__file__)),
    ), indent=2) + "\n")


if __name__ == "__main__":
    main()
