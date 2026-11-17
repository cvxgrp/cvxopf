"""WECC source-data integrity and formulation integration."""

from hashlib import sha256
from inspect import getfile
from pathlib import Path
import warnings

import numpy as np
import pytest

from cvxopf import build_opf, extract_results
from cvxopf.data import validate_case
from cvxopf.socp_diagnostics import audit_socp_relaxation, recover_socp_voltage
from cvxopf.testcases import wecc240_2018
from examples.wecc240_single_step import hvdc_links


# Whole-file hash of the pinned upstream copy; see wecc240_2018.LICENSE.
SOURCE_SHA256 = "39a0c59ff30a483c7a9dd87d779810ffafc481f285f7053f127606fe288e4a38"


def test_upstream_file_and_independent_case_instances():
    assert sha256(Path(getfile(wecc240_2018)).read_bytes()).hexdigest() == SOURCE_SHA256
    case = wecc240_2018()
    validate_case(case)
    case["bus"][0, 2] = 12345
    assert wecc240_2018()["bus"][0, 2] == 0


def test_hvdc_direction_and_source_preservation():
    case = wecc240_2018()
    before = case["dcline"].copy()
    for row, link in zip(before, hvdc_links(case), strict=True):
        assert (link.from_bus, link.to_bus) == tuple(row[:2])
        assert link.p_min_mw == -row[10]
        assert link.p_max_mw == -row[9]
        assert link.loss_percent == pytest.approx(3.5)
    np.testing.assert_array_equal(case["dcline"], before)


@pytest.mark.parametrize("formulation", ["lossy_dc", "socp"])
def test_dispatch_and_network_physics(formulation):
    case = wecc240_2018()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r"Branch .*has rateA=0; substituting")
        build = build_opf(case, formulation=formulation, hvdc=hvdc_links(case))
        build.solve(time_limit=30.0, max_iter=200)
    result = extract_results(build)
    assert result["status"] in {"optimal", "optimal_inaccurate"}
    assert np.isfinite(result["objective"])
    pin, pout = result["p_hvdc_in"], result["p_hvdc_out"]
    np.testing.assert_allclose(pout, -.965 * pin, atol=1e-6)
    assert np.all(-pin >= -1e-6)
    assert np.all(-pin <= case["dcline"][:, 10] + 1e-6)
    if formulation == "socp":
        assert audit_socp_relaxation(build, result)["feasible"]
        recovery = recover_socp_voltage(build, result)
        assert not recovery["exact_product_recovery"]
        assert not recovery["ac_feasible"]
        return
    # Independent balance reconstruction from external source bus identifiers.
    index = {int(b): i for i, b in enumerate(case["bus"][:, 0])}
    balance = -case["bus"][:, 2].copy()
    for row, pg in zip(case["gen"], result["Pg"], strict=True):
        balance[index[int(row[0])]] += pg
    for row, flow in zip(case["branch"], result["p_flows"], strict=True):
        if row[10]:
            balance[index[int(row[0])]] -= flow
            balance[index[int(row[1])]] += flow
    for row, pf, pt in zip(case["dcline"], pin, pout, strict=True):
        balance[index[int(row[0])]] += pf
        balance[index[int(row[1])]] += pt
    np.testing.assert_allclose(balance, 0, atol=1e-4)
