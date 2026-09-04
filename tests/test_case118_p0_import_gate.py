from __future__ import annotations

from pathlib import Path

import pytest

from experiments.case118_annual_hierarchy.p0_import_gate import (
    PRODUCTION_STREAMING_MODULES,
    run_import_gate,
    scan_source,
)


def test_production_streaming_modules_have_no_forbidden_dependencies():
    report = run_import_gate()

    assert report.passed, report.violations
    assert report.scanned_modules == (
        "audit.py",
        "p0_fixture.py",
        "streaming_schema.py",
        "streaming_runner.py",
        "streaming_archive.py",
        "streaming_driver.py",
    )
    assert len(report.scanned_modules) == len(PRODUCTION_STREAMING_MODULES)


@pytest.mark.parametrize(
    ("source", "expected_module", "expected_form"),
    [
        (
            "import cvxopf._hierarchical_solver as solver\n",
            "cvxopf._hierarchical_solver",
            "static",
        ),
        (
            "from cvxopf import _hierarchical_solver as solver\n",
            "cvxopf._hierarchical_solver",
            "static",
        ),
        (
            "from experiments.hierarchical_battery_resilience "
            "import manual_runner\n",
            "experiments.hierarchical_battery_resilience.manual_runner",
            "static",
        ),
        (
            "from ..hierarchical_battery_resilience import manual_runner\n",
            "experiments.hierarchical_battery_resilience.manual_runner",
            "static",
        ),
        (
            "import experiments.hierarchical_battery_resilience."
            "causal_recovery_runner as recovery\n",
            "experiments.hierarchical_battery_resilience."
            "causal_recovery_runner",
            "static",
        ),
        (
            "from importlib import import_module as loader\n"
            "loader('cvxopf._hierarchical_solver')\n",
            "cvxopf._hierarchical_solver",
            "dynamic_literal",
        ),
        (
            "import importlib as loader\n"
            "loader.import_module("
            "'experiments.hierarchical_battery_resilience.manual_runner')\n",
            "experiments.hierarchical_battery_resilience.manual_runner",
            "dynamic_literal",
        ),
        (
            "__import__('cvxopf._hierarchical_solver')\n",
            "cvxopf._hierarchical_solver",
            "dynamic_literal",
        ),
    ],
)
def test_gate_rejects_static_aliased_and_literal_dynamic_imports(
    source, expected_module, expected_form
):
    violations = scan_source(source, path="synthetic.py")

    assert len(violations) == 1
    assert violations[0].path == "synthetic.py"
    assert violations[0].imported_module == expected_module
    assert violations[0].import_form == expected_form
    assert violations[0].line >= 1


def test_gate_allows_public_cvxopf_and_case118_experiment_dependencies():
    source = """
from cvxopf import solve_hierarchical_opf
from cvxopf.hierarchical import HierarchicalPolicy
from experiments.case118_annual_hierarchy import streaming_schema
"""

    assert scan_source(source) == ()


def test_gate_rejects_paths_outside_the_frozen_module_root(tmp_path):
    source = tmp_path / "stream.py"
    source.write_text("from cvxopf import OPFBuild\n")

    with pytest.raises(ValueError, match="outside gate root"):
        run_import_gate((source,))


def test_gate_rejects_missing_registered_module():
    missing = Path(PRODUCTION_STREAMING_MODULES[0].parent / "missing.py")

    with pytest.raises(ValueError, match="does not exist"):
        run_import_gate((missing,))
