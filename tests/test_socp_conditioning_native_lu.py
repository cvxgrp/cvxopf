"""Small linear algebra and lifecycle checks; no OPF optimization calls."""

import json
import os
import subprocess
import sys

import numpy as np
import pytest

from experiments.socp_conditioning import lu_bridge as lu
from experiments.socp_conditioning import native_lu_probe as p


def matrix(diagonal=4.0):
    return dict(
        op="factor",
        m=2,
        n=2,
        colptr=[0, 1, 3],
        rowval=[0, 0, 1],
        nzval=[diagonal, 1.0, -2.0],
    )


def test_full_symmetric_matrix_and_multiple_rhs_factor_reuse():
    bridge = lu.Bridge()
    a = bridge.handle(matrix())
    assert a["matrix_nnz"] == 4
    for rhs in ([1.0, 2.0], [3.0, -1.0]):
        result = bridge.handle(dict(op="solve", rhs=rhs))
        np.testing.assert_allclose(
            np.array([[4, 1], [1, -2]]) @ result["x"], rhs, atol=1e-14
        )
        assert result["generation"] == 1
    assert bridge.handle(matrix(8.0))["generation"] == 2
    x = bridge.handle(dict(op="solve", rhs=[1.0, 2.0]))["x"]
    np.testing.assert_allclose(np.array([[8, 1], [1, -2]]) @ x, [1, 2], atol=1e-14)


def test_failed_refactor_cannot_reuse_previous_factor():
    bridge = lu.Bridge()
    bridge.handle(matrix())
    with pytest.raises(Exception):
        bridge.handle(matrix() | dict(nzval=[0.0, 0.0, 0.0]))
    with pytest.raises(ValueError, match="no valid"):
        bridge.handle(dict(op="solve", rhs=[1.0, 2.0]))


@pytest.mark.parametrize("rhs", [[1.0], [float("nan"), 2.0]])
def test_bad_rhs_rejected(rhs):
    bridge = lu.Bridge()
    bridge.handle(matrix())
    with pytest.raises(ValueError, match="invalid RHS"):
        bridge.handle(dict(op="solve", rhs=rhs))


def test_real_pipe_server_retains_failure_and_exits_at_eof():
    requests = [matrix(), dict(op="solve", rhs=[1.0, 2.0]), dict(op="unknown")]
    result = subprocess.run(
        [sys.executable, "-B", "-m", lu.__name__],
        input="".join(json.dumps(r) + "\n" for r in requests),
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["ok"] for r in responses] == [True, True, False]
    assert "TRACE SUPERLU" in result.stderr


def test_environment_excludes_all_previous_experimental_switches(monkeypatch):
    for name in ("CVXOPF_LU_PYTHON", "CVXOPF_KKT_SCALING", "CVXOPF_KKT_DIR"):
        monkeypatch.setenv(name, "inherited")
    with p.environment("control"):
        assert "CVXOPF_LU_PYTHON" not in os.environ
        assert "CVXOPF_KKT_SCALING" not in os.environ
    with p.environment("superlu"):
        assert os.environ["CVXOPF_LU_PYTHON"] == sys.executable
        assert "CVXOPF_KKT_SCALING" not in os.environ
    assert os.environ["CVXOPF_LU_PYTHON"] == "inherited"


def test_rss_counts_bridge_without_double_counting_worker(monkeypatch):
    monkeypatch.setattr(p.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        p.subprocess,
        "check_output",
        lambda *a, **k: "10 1 1024\n11 10 2048\n12 11 4096\n13 12 512\n99 1 8000\n",
    )
    assert p.process_rss(10) == 1.0
    assert p.process_rss(11) == 6.5


def test_bridge_time_separates_factor_work_from_transport():
    log = """TRACE SUPERLU {"ok":true,"event":"factor","seconds":1.0,"assembly_seconds":0.2,"L_nnz":4,"U_nnz":5}
TRACE LU_BRIDGE event=factor elapsed_seconds=1.5
TRACE SUPERLU {"ok":true,"event":"solve","seconds":0.1}
TRACE LU_BRIDGE event=solve elapsed_seconds=0.3
"""
    result = p.bridge_summary(log)
    assert result["factors"] == result["solves"] == 1
    assert result["serialization_transport_and_other_seconds"] == pytest.approx(0.5)
    assert result["peak_factor_nnz"] == 9


def test_execution_context_enumerates_only_declared_solver(monkeypatch):
    # Provenance collection is not the behavior under test. Build options from
    # the live configuration so this still detects incorrect solver selection.
    monkeypatch.setattr(
        p.n,
        "context",
        lambda: {
            "additional_sources": {},
            "options": {solver: p.t.options(solver) for solver in p.t.SOLVERS},
        },
    )
    monkeypatch.setattr(p.d, "sha", lambda path: "unit-test-provenance")
    previous = p.t.SOLVERS
    previous_options, previous_context = p.t.options, p.t.context
    with p.execution_configuration():
        assert p.t.SOLVERS == ("CLARABEL",)
        assert set(p.context()["options"]) == {"CLARABEL"}
    assert p.t.SOLVERS == previous
    assert p.t.options is previous_options
    assert p.t.context is previous_context
