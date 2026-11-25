"""Non-solving coverage for source selection, DNLP boundaries, and audit."""

from pathlib import Path
import subprocess
import sys

import cvxpy as cp
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
import numpy as np
import pandas as pd
import pytest

from cvxopf import NumericalPreparation, OPFOptions, StorageUnitIdeal
from cvxopf.testcases import case9
from . import binding, worker
from .run import command_for


@pytest.mark.parametrize("prepared", [False, True])
def test_compilation_builds_exact_oracles_without_native_entry(monkeypatch, prepared):
    def no_optimizer(*args, **kwargs):
        raise AssertionError("Unexpected optimizer entry")

    monkeypatch.setattr(cp.Problem, "solve", no_optimizer)
    monkeypatch.setattr(IPOPT, "solve_via_data", no_optimizer)
    kwargs = dict(case=case9())
    if prepared:
        case = kwargs["case"]
        kwargs.update(T=2, df_P=pd.DataFrame(np.tile(case["bus"][:, 2], (2, 1))),
                      df_Q=pd.DataFrame(np.tile(case["bus"][:, 3], (2, 1))),
                      options=OPFOptions(numerical_preparation=NumericalPreparation(
                          normalize_device_limits=True, exact_fixed_boxes=True, cost_coordinates=True,
                          objective_assembly="component_first",
                      )))
    build = worker.build_model(kwargs)
    worker.named_start(build)
    metrics = worker.compiled(build)
    assert metrics["stored_hessian_entries"] > 0 and metrics["stored_jacobian_entries"] > 0
    assert metrics["native_variables"] <= metrics["canonical_variables"]
    assert all(metrics[k] >= 0 for k in ("preparation_seconds", "reduction_seconds", "derivative_seconds"))


def test_fresh_worker_environment_imports_each_exact_archived_builder(tmp_path):
    loaded = []
    for side, rev in binding.REVISIONS.items():
        expected = binding.source_snapshot(tmp_path, side, rev)
        _, env = command_for(tmp_path, tmp_path, {"revision": side})
        filename, digest = subprocess.check_output([sys.executable, "-c", """
from cvxopf import ac_problem
from pathlib import Path
from hashlib import sha256
p = Path(ac_problem._make_branch_terminal_flow.__code__.co_filename).resolve()
print(p)
print(sha256(p.read_bytes()).hexdigest())
"""], cwd=binding.ROOT, env=env, text=True, timeout=20).splitlines()
        rel = "src/cvxopf/ac_problem.py"
        assert Path(filename) == tmp_path / "sources" / side / rel
        assert digest == expected[rel]
        loaded.append(digest)
    assert loaded[0] != loaded[1]


def test_snapshot_audit_rejects_tampered_network_and_cost_without_a_solve():
    case = case9()
    # A flat, zero-injection physical point on the original connected topology.
    case["bus"][:, 2:6] = 0.
    case["branch"][:, 4] = 0.
    case["gen"][:, 9] = 0.
    nb, ng, nl = (len(case[k]) for k in ("bus", "gen", "branch"))
    cost = sum(np.polyval(row[4:4+int(row[3])], 0.) for row in case["gencost"])
    result = dict(Vm=np.ones(nb), Va_deg=np.zeros(nb), p_net=np.zeros(nb), q_net=np.zeros(nb),
                  Pg=np.zeros(ng), Qg=np.zeros(ng), objective=cost)
    result.update({f"branch_{key}_{side}": np.zeros(nl) for key in ("p", "q", "s")
                   for side in ("from", "to")})
    tolerances = dict(power=1e-4, voltage=1e-6, angle=1e-6, cost_abs=1e-4, cost_rel=1e-8)
    assert worker.snapshot_audit(case, result, tolerances)["passed"]
    result["branch_q_to"][0] = .5
    result["objective"] += 1.
    checked = worker.snapshot_audit(case, result, tolerances)
    assert not checked["passed"]
    assert checked["residuals"]["branch_q_to"] == .5
    assert checked["residuals"]["objective"] == 1.


@pytest.mark.parametrize("prepared", [False, True])
def test_native_entry_receipt_has_matched_physical_coordinates_and_applied_options(monkeypatch, prepared):
    class FakeNativeProblem:
        def __init__(self, *args, **kwargs):
            pass

        def add_option(self, key, value):
            pass

        def solve(self, x0):
            return x0, dict(x=x0.copy(), obj_val=0., status=2, status_msg="stub native rejection")

    monkeypatch.setattr(worker.cyipopt, "Problem", FakeNativeProblem)
    case = case9()
    kwargs = {"case": case}
    if prepared:
        case["gen"][0, 8:10] = 50.
        kwargs.update(T=2, df_P=pd.DataFrame(np.tile(case["bus"][:, 2], (2, 1))),
                      df_Q=pd.DataFrame(np.tile(case["bus"][:, 3], (2, 1))),
                      storage=[StorageUnitIdeal(bus=1, apparent_power_rating=20., capacity=30., initial_soc=10.)],
                      options=OPFOptions(numerical_preparation=NumericalPreparation(
                          normalize_device_limits=True, exact_fixed_boxes=True, cost_coordinates=True,
                          objective_assembly="component_first",
                      )))
    build = worker.build_model(kwargs)
    if prepared:
        build.variables["b"].value = np.array([[1., -2.]])
    physical = worker.named_start(build)
    seen = []
    metrics = worker.solve(build, dict(tol=1e-7, max_iter=3000, mu_strategy="adaptive"),
                           lambda *args: seen.append(worker.start_receipt(*args)))
    assert metrics["native_status"] == 2
    (receipt,) = seen
    assert receipt["native_add_options"]["bound_relax_factor"] == 0.0
    assert "bound_relax_factor" not in receipt["explicit_adapter_options"]
    verified = worker.verify_start(build, physical, receipt)
    if prepared:
        assert receipt["fixed"]["fixed"].size > 0
        assert verified["cost_coordinate_maps"]
        assert len(receipt["x0"]) < len(verified["assigned_full_x0"])
        np.testing.assert_allclose(verified["effective_physical_start"]["b"], physical["b"], atol=0, rtol=1e-14)
    with pytest.raises(ValueError, match="start.*changed|does not match"):
        worker.verify_start(build, dict(physical, theta=physical["theta"]+.125), receipt)


def test_harness_binding_excludes_reports_and_detects_audit_edits(monkeypatch, tmp_path):
    from . import report  # noqa: F401 -- show that an imported report remains unbound

    sources = binding.harness_sources()
    assert not any(name.endswith("ac_spatial_vectorization/report.py") for name in sources)
    audit = "experiments/case118_tracy_2021/stage_d_physics.py"
    assert audit in sources
    monkeypatch.setattr(binding, "ROOT", tmp_path)
    source = tmp_path / audit
    source.parent.mkdir(parents=True)
    source.write_text("frozen audit source")
    expected = {audit: binding.sha256_path(source)}
    (tmp_path / "report.py").write_text("unrelated new report")
    binding.verify_harness(expected)
    source.write_text("changed audit source")
    with pytest.raises(ValueError, match="Relevant benchmark code changed"):
        binding.verify_harness(expected)


def test_graph_evidence_retains_warning_roles_and_cross_constraint_sharing():
    build = worker.build_model({"case": case9()})
    evidence = worker.graph_evidence(build, ["Constraint #5 contains too many subexpressions."])
    assert {row["role"] for row in evidence["constraints"]} >= {
        "p balance", "q balance", "p row sum", "q row sum", "from thermal limit", "to thermal limit",
    }
    (warned,) = [row for row in evidence["constraints"] if row["warned"]]
    assert warned["role"] == "branch_p_from_pu definition" and warned["shape"] == [9]
    assert 0 < evidence["unique_nodes_across_groups"] < sum(r["unique_nodes"] for r in evidence["constraints"])


def test_native_solver_identity_is_linked_library_hash():
    info = binding.native_solver_identity()
    path = Path(info["library"])
    assert path.name.startswith("libipopt") and info["version"]
    assert info["sha256"] == binding.sha256_path(path)
