"""Native-evidence serialization regression; no license or numerical solve."""

from array import array
import json
from types import SimpleNamespace
from unittest.mock import Mock

from cvxpy import settings

from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning.mosek_check import capture_task


def test_native_array_vectors_are_strict_json_serializable(monkeypatch):
    fake = SimpleNamespace(
        soltype=SimpleNamespace(itr="itr"),
        iinfitem=SimpleNamespace(intpnt_iter="iterations"),
        dinfitem=SimpleNamespace(
            optimizer_time="seconds", intpnt_primal_feas="p", intpnt_dual_feas="d"
        ),
        dparam=SimpleNamespace(intpnt_co_tol_pfeas="tolerance"),
        iparam=SimpleNamespace(num_threads="threads"),
    )
    monkeypatch.setitem(__import__("sys").modules, "mosek", fake)
    task = Mock()
    task.getxx.return_value = array("d", [1, 2])
    task.gety.return_value = array("d", [3])
    task.getprimalobj.return_value = 4.0
    task.getdualobj.return_value = 4.00001
    task.getintinf.return_value = 30
    task.getdouinf.return_value = 0.1
    task.getdouparam.return_value = 1e-8
    task.getintparam.return_value = 1
    evidence = capture_task(task, {"dualized": True, settings.OBJ_OFFSET: 0.0})
    retained = json.loads(json.dumps(d.json_value(evidence), allow_nan=False))
    assert retained["task_x"] == [1.0, 2.0]
    assert retained["task_y"] == [3.0]
    assert retained["threads"] == 1
    assert retained["default_tolerances"] == {"intpnt_co_tol_pfeas": 1e-8}
