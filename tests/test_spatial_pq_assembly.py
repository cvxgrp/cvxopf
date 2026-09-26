"""Independent spatial assembly control, with fixed variable layouts/physics."""

from dataclasses import asdict, replace
from hashlib import sha256
import json

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import OPFOptions, build_opf, build_opf_multistep, extract_results
from cvxopf.testcases import case9
from experiments.case118_annual_hierarchy import streaming_runner as streaming
from experiments.case118_annual_hierarchy.p0_fixture import load_p0_fixture


ASSEMBLIES = [("single", 1), ("stepwise", 1), ("stepwise", 3),
              ("vectorized", 1), ("vectorized", 3)]


def build_case(assembly, horizon, sparse, vectorize):
    case = case9()
    options = OPFOptions(sparse_pq=sparse, vectorize_pq=vectorize)
    if assembly == "single":
        return build_opf(case, options=options)
    scales = np.linspace(.8, 1.1, horizon)[:, None]
    return build_opf_multistep(
        case, pd.DataFrame(scales * case['bus'][:, 2]),
        pd.DataFrame(scales * case['bus'][:, 3]), T=horizon,
        temporal_assembly=assembly, options=options,
    )


@pytest.mark.parametrize("assembly,horizon", ASSEMBLIES)
@pytest.mark.parametrize("sparse", [True, False])
def test_disabled_spatial_assembly_uses_separate_entry_expressions(
    assembly, horizon, sparse, monkeypatch,
):
    import cvxopf.ac_problem as ac

    def disallow_gather(*args):
        raise AssertionError("Spatially vectorized expression must not be constructed")

    monkeypatch.setattr(ac, "_pq_flow_expressions", disallow_gather)
    build = build_case(assembly, horizon, sparse, False)
    expected_shape = (horizon,) if assembly == "vectorized" else ()
    for key in (("P_vec", "Q_vec") if sparse else ("P", "Q")):
        variables = build.variables[key]
        if assembly != "stepwise":
            variables = [variables]
        for variable in variables:
            defining, zero = [], []
            for c in build.prob.constraints:
                if (isinstance(c, cp.constraints.Equality)
                        and {v.id for v in c.args[0].variables()} == {variable.id}):
                    if not c.args[1].is_affine():
                        defining.append(c)
                    elif c.args[1].is_constant():
                        zero.append(c)
            assert len(defining) == len(build.data['rows'])
            assert all(c.shape == expected_shape for c in defining)
            assert len(zero) == (0 if sparse else len(build.data['Z'][0]))
            assert all(c.shape == expected_shape for c in zero)


@pytest.mark.parametrize("assembly,horizon", ASSEMBLIES)
@pytest.mark.parametrize("sparse", [True, False])
def test_spatial_toggle_preserves_layout_starts_and_numerical_solution(
    assembly, horizon, sparse,
):
    on, off = [build_case(assembly, horizon, sparse, flag) for flag in (True, False)]
    starts = [streaming.complete_flat_start(b) for b in (on, off)]
    assert starts[0].keys() == starts[1].keys()
    for key in starts[0]:
        np.testing.assert_array_equal(starts[0][key], starts[1][key])
    for build, start in zip((on, off), starts, strict=True):
        streaming.assign_start(build, start)
        build.solve(max_iter=400)
        assert build.prob.status == 'optimal'
    a, b = extract_results(on), extract_results(off)
    assert a.keys() == b.keys()
    assert a['objective'] == pytest.approx(b['objective'], rel=1e-7)
    for key in ('Pg', 'Qg', 'Vm', 'Va_deg', 'p_net', 'q_net', 'branch_s_from'):
        np.testing.assert_allclose(a[key], b[key], atol=3e-4)


@pytest.mark.parametrize("formulation", ['lossy_dc', 'singlenode_dc'])
def test_spatial_pq_option_does_not_change_dc_graph(formulation):
    on, off = [build_opf(case9(), formulation=formulation,
                        options=OPFOptions(vectorize_pq=flag)) for flag in (True, False)]
    assert str(on.prob.objective) == str(off.prob.objective)
    assert list(map(str, on.prob.constraints)) == list(map(str, off.prob.constraints))


def test_default_and_frozen_input_fingerprint_compatibility():
    assert OPFOptions().vectorize_pq is True
    inputs = load_p0_fixture(6).inputs
    # Independently reconstruct the legacy payload, which had no new field.
    payload = {key: getattr(inputs, key) for key in (
        'case', 'horizon_steps', 'delta', 'generators', 'loads', 'storage',
        'nondispatchable', 'hvdc', 'df_load_p', 'df_load_q', 'df_nd',
        'df_hvdc_min', 'df_hvdc_max',
    )}
    payload['options'] = asdict(inputs.options)
    del payload['options']['vectorize_pq']
    legacy = sha256(json.dumps(streaming._fingerprint_value(payload), sort_keys=True,
                               separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert streaming.execution_input_sha256(inputs) == legacy
    scalar = replace(inputs, options=replace(inputs.options, vectorize_pq=False))
    assert streaming.execution_input_sha256(scalar) != legacy
