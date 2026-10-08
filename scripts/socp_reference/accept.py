"""Audit offline outputs before publishing static SOCP fixtures; never solves.

Run after generate_references.jl. Refuses to overwrite existing fixtures.
"""

import argparse
import json
from pathlib import Path

import numpy as np

from tests.socp_reference_audit import audit
from tests.socp_reference_cases import analytic_two_bus, cases, input_hash, policy, SETTINGS, verify_reference_sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compare regenerated outputs without replacing fixtures")
    parser.add_argument("--raw-dir", type=Path, required=True, help="Directory containing recorded generator outputs")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    destination = root.parents[1] / "tests" / "fixtures"
    records = []
    for name, case in cases().items():
        record = json.loads((args.raw_dir / f"{name}_powermodels.json").read_text())
        assert record["input_sha256"] == input_hash(case)
        assert record["policy"] == policy(name)
        assert record["reference"]["settings"] == SETTINGS
        assert record["reference"]["parser_verified"] is True
        assert record["termination"] == "OPTIMAL" and record["primal_status"] == "FEASIBLE_POINT"
        verify_reference_sources(record["reference"])
        checked = audit(case, record["primal"], enforce_vset=policy(name)["enforce_vset"])
        np.testing.assert_allclose(checked["objective"], record["objective"], rtol=2e-6, atol=2e-5)
        # PowerModels' conic cost epigraph can be slightly slack. Retain solver
        # objective separately; physical cost accounting uses the actual primal.
        record["primal_objective"] = checked["objective"]
        record["objective_components"] = {"generator_cost": checked["objective"]}
        record["independent_audit"] = checked
        record["units"] = dict(w="pu^2", W_re="pu^2", W_im="pu^2", Pg="MW", Qg="MVAr",
                               branch_p="MW", branch_q="MVAr", objective="cost for delta=1 hour",
                               max_cone_violation="pu^2", max_determinant_violation="pu^4")
        if name == "two_bus":
            analytic = analytic_two_bus()
            for key in ("w", "W_re", "W_im", "Pg", "Qg"):
                np.testing.assert_allclose(record["primal"][key], analytic[key], rtol=0,
                                           atol=1e-5 if key in ("Pg", "Qg") else 1e-6)
            np.testing.assert_allclose(checked["objective"], analytic["objective"], rtol=2e-6, atol=2e-5)
        records.append((destination / f"{name}_socp_powermodels_reference.json", record, case))
    if args.check:
        for path, record, case in records:
            existing = json.loads(path.read_text())
            for key in ("input_sha256", "policy"):
                assert existing[key] == record[key]
            # Both source hashes are verified, but a path-only maintained
            # generator may differ from the byte-preserved historical source.
            for key in ("manifest_sha256", "settings"):
                assert existing["reference"][key] == record["reference"][key]
            verify_reference_sources(existing["reference"])
            existing_checked = audit(case, existing["primal"], enforce_vset=existing["policy"]["enforce_vset"])
            np.testing.assert_allclose(existing_checked["objective"], existing["objective"], rtol=2e-6, atol=2e-5)
            np.testing.assert_allclose(existing["objective"], record["objective"], rtol=2e-6, atol=2e-5)
            # Each primal passes the independent audit's declared tolerances.
            # Cross-run roundoff and nonunique primals need not match residuals.
            for key in ("max_cone_violation", "max_determinant_violation"):
                assert existing["units"][key] == record["units"][key]
            print(path.name, "regeneration check passed; fixture unchanged")
        return
    # All checks pass before publishing any fixture; no replacement by accident.
    assert not any(path.exists() for path, _, _ in records), "Refusing to replace reference fixtures"
    for path, record, _ in records:
        path.write_text(json.dumps(record, sort_keys=True, indent=2, allow_nan=False)+"\n")
        print(path.name, record["objective"], record["independent_audit"])


if __name__ == "__main__":
    main()
