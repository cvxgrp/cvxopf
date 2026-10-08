"""Independent SOCP oracles; static inputs, Python-only CI, no regeneration.

Reference generation is maintained under scripts/socp_reference/.
Meshed primal nonuniqueness and inexact voltage recovery are not test failures.
"""

import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
import pytest

from cvxopf import (
    OPFOptions, audit_socp_relaxation, build_opf, build_opf_multistep,
    extract_results, recover_socp_voltage,
)
from tests.socp_reference_audit import audit, edge_violations
from tests.socp_reference_cases import (
    analytic_two_bus, cases, input_hash, payload, policy, SETTINGS, verify_reference_sources,
)


FIXTURES = Path(__file__).parent / "fixtures"
OBJ_RTOL, OBJ_ATOL = 2e-6, 2e-5


def reference(name):
    # Missing/invalid/stale oracles must fail; never skip or generate in CI.
    record = json.loads((FIXTURES / f"{name}_socp_powermodels_reference.json").read_text())
    case = cases()[name]
    assert record["schema_version"] == 1 and record["case_name"] == name
    assert record["input_sha256"] == input_hash(case)
    assert record["case"] == payload(case) and record["policy"] == policy(name)
    ref = record["reference"]
    assert ref["implementation"] == "PowerModels.SOCWRConicPowerModel"
    assert ref["settings"] == SETTINGS and ref["parser_verified"] is True
    assert ref["packages"]["PowerModels"]["version"] == "0.21.5"
    assert ref["packages"]["PowerModels"]["git_tree_sha1"] == "b8e410e1d827b621e82e7e670967f0efc5845c30"
    verify_reference_sources(ref)
    assert record["termination"] == "OPTIMAL" and record["primal_status"] == "FEASIBLE_POINT"
    return case, record


def test_two_bus_analytic_oracle_is_feasible_and_exactly_recoverable():
    case = cases()["two_bus"]
    primal = analytic_two_bus()
    checked = audit(case, primal, enforce_vset=True)
    assert checked["objective"] == pytest.approx(primal["objective"], rel=1e-12)
    assert checked["branch_loss_mw"] == pytest.approx(primal["branch_loss_mw"], abs=1e-12)
    assert primal["Pg"][0] > case["bus"][:, 2].sum()
    build = build_opf(case, formulation="socp", options=OPFOptions(enforce_vset=True))
    result = extract_results(build)
    result.update({key: np.asarray(primal[key]) for key in ("w", "W_re", "W_im", "Pg", "Qg")})
    assert audit_socp_relaxation(build, result)["feasible"]
    recovery = recover_socp_voltage(build, result)
    assert recovery["exact_product_recovery"] and recovery["ac_feasible"]


@pytest.mark.parametrize("name", ["two_bus", "case9", "case14"])
def test_static_external_primal_is_independently_feasible(name):
    case, record = reference(name)
    checked = audit(case, record["primal"], enforce_vset=policy(name)["enforce_vset"])
    for key, units in (("max_cone_violation", "pu^2"), ("max_determinant_violation", "pu^4")):
        assert record["units"][key] == units
        assert checked[key] == pytest.approx(record["independent_audit"][key], rel=1e-6, abs=1e-15)
    assert checked["objective"] == pytest.approx(record["primal_objective"], rel=1e-10, abs=1e-7)
    assert sum(record["objective_components"].values()) == pytest.approx(checked["objective"], rel=1e-10, abs=1e-7)
    assert record["objective"] == pytest.approx(checked["objective"], rel=OBJ_RTOL, abs=OBJ_ATOL)
    # Ensure the package audit also accepts the independent primal, without
    # trusting cached reference residuals or previously reported branch powers.
    build = build_opf(case, formulation="socp", options=OPFOptions(enforce_vset=policy(name)["enforce_vset"]))
    result = extract_results(build)
    result.update({key: np.asarray(record["primal"][key]) for key in ("w", "W_re", "W_im", "Pg", "Qg")})
    assert audit_socp_relaxation(build, result)["feasible"]


def test_norm_soc_residual_is_not_determinant_violation():
    # Nonunit w values expose both the numerical and dimensional distinction.
    args = [np.array([value]) for value in (4., 1., 2.1, 0.)]
    soc, determinant = edge_violations(*args)
    assert soc == pytest.approx(np.sqrt(4.2**2+3**2)-5)
    assert determinant == pytest.approx(.41)
    scaled_soc, scaled_determinant = edge_violations(*(3*a for a in args))
    assert scaled_soc == pytest.approx(3*soc)
    assert scaled_determinant == pytest.approx(9*determinant)
    assert edge_violations(*(np.array([]) for _ in range(4))) == (0., 0.)
    assert edge_violations(*(np.array([v]) for v in (4., 1., 1.9, 0.))) == (0., 0.)


@pytest.mark.parametrize("key", ["generator_sha256", "manifest_sha256"])
def test_reference_rejects_stale_source_provenance(key):
    _, record = reference("two_bus")
    record["reference"][key] = "0"*64
    with pytest.raises(AssertionError):
        verify_reference_sources(record["reference"])


@pytest.mark.parametrize("maintained_source", [False, True])
@pytest.mark.parametrize("analytic_primal", [False, True], ids=["recorded-primal", "exact-analytic-primal"])
def test_fixture_tooling_uses_explicit_raw_directory(tmp_path, monkeypatch, capsys, maintained_source, analytic_primal):
    from scripts.socp_reference import accept, export_inputs

    monkeypatch.setattr("sys.argv", ["export_inputs", "--raw-dir", str(tmp_path)])
    export_inputs.main()
    request = json.loads((tmp_path / "request.json").read_text())
    assert request["settings"] == SETTINGS
    source = FIXTURES.parents[1] / "scripts" / "socp_reference" / "generate_references.jl"
    fixture_dir = tmp_path / "tests" / "fixtures"
    fixture_dir.mkdir(parents=True)
    monkeypatch.setattr(accept, "__file__", str(tmp_path / "scripts" / "socp_reference" / "accept.py"))
    fixture_bytes = {}
    for name, case in cases().items():
        filename = f"{name}_socp_powermodels_reference.json"
        path = fixture_dir / filename
        assert request["cases"][name] == dict(case=payload(case), input_sha256=input_hash(case), policy=policy(name))
        assert (tmp_path / f"{name}.m").is_file()
        record = json.loads((FIXTURES / filename).read_bytes())
        if analytic_primal and name == "two_bus":
            # Construct the stored candidate ourselves: preserve affine power
            # balance, but introduce a cone violation well inside tolerance.
            # The regression must not depend on a historical solver's roundoff.
            perturbed = analytic_two_bus()
            perturbation = 1e-9
            perturbed["w"][1] += perturbation
            perturbed["W_re"][0] += perturbation
            perturbed["Pg"][0] -= 1000*perturbation
            perturbed["branch_p_from"][0] = perturbed["Pg"][0]
            perturbed["objective"] = .01*perturbed["Pg"][0]**2 + perturbed["Pg"][0]
            perturbed["branch_loss_mw"] = perturbed["Pg"][0]-10.
            record["primal"] = perturbed
            record["objective"] = perturbed["objective"]
            record["primal_objective"] = perturbed["objective"]
            record["objective_components"] = {"generator_cost": perturbed["objective"]}
            record["independent_audit"] = audit(case, perturbed, enforce_vset=True)
            checked = audit(case, analytic_two_bus(), enforce_vset=True)
            for key in ("max_cone_violation", "max_determinant_violation"):
                assert checked[key] <= 1e-14  # floating-point roundoff allowance
                assert record["independent_audit"][key]-checked[key] > 1e-10
        path.write_text(json.dumps(record))
        fixture_bytes[path] = path.read_bytes()
        if analytic_primal and name == "two_bus":
            record["primal"] = analytic_two_bus()
            record["objective"] = record["primal"]["objective"]
        if maintained_source:
            record["reference"]["generator_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        (tmp_path / f"{name}_powermodels.json").write_text(json.dumps(record))
    monkeypatch.setattr("sys.argv", ["accept", "--check", "--raw-dir", str(tmp_path)])
    accept.main()
    assert capsys.readouterr().out.count("fixture unchanged") == 3
    assert all(path.read_bytes() == saved for path, saved in fixture_bytes.items())


@pytest.mark.parametrize("name", ["two_bus", "case9", "case14"])
@pytest.mark.parametrize("assembly", ["single", "vectorized", "stepwise"])
def test_socp_matches_independent_optimum(name, assembly, record_property):
    case, record = reference(name)
    options = OPFOptions(enforce_vset=policy(name)["enforce_vset"])
    start = perf_counter()
    if assembly == "single":
        build = build_opf(case, formulation="socp", options=options)
    else:
        build = build_opf_multistep(case, pd.DataFrame([case["bus"][:, 2]]),
            pd.DataFrame([case["bus"][:, 3]]), T=1, formulation="socp",
            temporal_assembly=assembly, options=options)
    built = perf_counter()
    canonical, _, _ = build.prob.get_problem_data("CLARABEL", canon_backend=build.canonicalization_backend)
    canonicalized = perf_counter()
    build.solve(**SETTINGS)
    solved = perf_counter()
    result = extract_results(build)
    assert result["status"] == "optimal"
    assert result["objective"] == pytest.approx(record["objective"], rel=OBJ_RTOL, abs=OBJ_ATOL)
    primal = {key: np.asarray(result[key] if assembly == "single" else result[key][0])
              for key in ("w", "W_re", "W_im", "Pg", "Qg", "branch_p_from",
                          "branch_q_from", "branch_p_to", "branch_q_to")}
    primal.update(bus_ids=case["bus"][:, 0].astype(int).tolist(),
                  voltage_product_pairs=record["primal"]["voltage_product_pairs"])
    # Check actual builder pair order too, rather than relabeling its products.
    actual_pairs = case["bus"][build.data["voltage_product_pairs"], 0].astype(int).tolist()
    assert actual_pairs == primal["voltage_product_pairs"]
    checked = audit(case, primal, enforce_vset=options.enforce_vset)
    assert result["objective"] == pytest.approx(checked["objective"], rel=1e-10, abs=1e-7)
    assert audit_socp_relaxation(build, result)["feasible"]
    recovery = recover_socp_voltage(build, result)
    assert recovery["available"]
    if name == "two_bus":
        analytic = analytic_two_bus()
        for key in ("w", "W_re", "W_im", "Pg", "Qg"):
            np.testing.assert_allclose(primal[key], analytic[key], rtol=0,
                                       atol=1e-5 if key in ("Pg", "Qg") else 1e-6)
        assert recovery["exact_product_recovery"] and recovery["ac_feasible"]
    record_property("socp_reference", json.dumps(dict(**checked, case=name, assembly=assembly,
        objective_estimate=result["objective"], reference_objective=record["objective"],
        build_s=built-start, canonicalize_s=canonicalized-built, solve_wall_s=solved-canonicalized,
        solver_s=build.prob.solver_stats.solve_time,
        scalar_variables=build.prob.size_metrics.num_scalar_variables,
        soc_sizes=canonical["dims"].soc,
        max_cycle_rad=recovery["cycle_summary"]["maximum"],
        exact_product_recovery=recovery["exact_product_recovery"],
        recovered_ac_feasible=recovery["ac_feasible"])))


@pytest.mark.parametrize("key", ["W_im", "Pg", "branch_p_to"])
def test_independent_oracle_audit_rejects_corrupted_reference(key):
    case, record = reference("case9")
    primal = record["primal"]
    primal[key][0] += 1.
    with pytest.raises(AssertionError):
        audit(case, primal)
