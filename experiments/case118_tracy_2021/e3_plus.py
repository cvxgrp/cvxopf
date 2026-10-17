"""Four-arm forced-shedding extension using prepared E3 supervision/replay.

The retained T=24 variant is a fixture reference, never a seed or an accepted
result source. Importing this module performs no optimization or writes.
"""

from copy import deepcopy
from functools import lru_cache
import sys

import numpy as np

from experiments.ac_cost_qualification.fixture import shortage
from experiments.numerical_preparation.fixture import mathematical_inputs
from . import e3, e3_prepared as prepared_model
from .prepare import HERE, ROOT, digest
from .stage_d import read

COHORT = "prepared_plus"
FIXTURE_PATH = ROOT / "experiments/objective_assembly_qualification/results/qualification_002/binding.json"
FIXTURE_SHA256 = "4d4ec1cb451207f4d0b0a17047258f902110c886bc73513ec3a1a152248b2d1f"
WITNESS_ALLOWANCE_MWH = 1e-4
DEFAULT_PROTOCOL = dict(prepared_model.DEFAULT_PROTOCOL, wall_seconds=3600., max_launches=28)

verified_inputs = e3.verified_inputs
encode = e3.encode
ROLES = e3.ROLES
request_for = prepared_model.request_for
policy = prepared_model.policy
policy_record = prepared_model.policy_record
solver_options = prepared_model.solver_options
physical_input_digest = prepared_model.physical_input_digest
prepared_start = prepared_model.prepared_start
verify_prepared_start = prepared_model.verify_prepared_start
monitoring = prepared_model.monitoring


def validate_protocol(value):
    return prepared_model.validate_protocol(value)


@lru_cache(maxsize=1)
def _fixture_rows():
    """Decode only fixture rows, discarding canonical vectors and old starts."""
    binding = read(FIXTURE_PATH)
    rows = [r for r in binding["rows"] if r["arm"]["T"] == 24
            and r["arm"]["start"] == 1165 and r["arm"]["forced_shedding"]]
    if len(rows) != 8 or {r["arm"]["formulation"] for r in rows} != set(e3.FORMULATIONS):
        raise ValueError("pinned forced fixture matrix differs")
    selected = {}
    for formulation in e3.FORMULATIONS:
        pair = [r for r in rows if r["arm"]["formulation"] == formulation]
        if (len(pair) != 2 or {r["arm"]["assembly"] for r in pair} != {"hourly", "component_first"}
                or pair[0]["stress"] != pair[1]["stress"]
                or pair[0]["frozen"]["mathematical_inputs"] != pair[1]["frozen"]["mathematical_inputs"]):
            raise ValueError("historical forced fixture pair is not matched")
        selected[formulation] = dict(stress=pair[0]["stress"],
                                    inputs=pair[0]["frozen"]["mathematical_inputs"])
    reference = selected["ac"]["stress"]
    if any(r["stress"] != reference for r in selected.values()):
        raise ValueError("historical formulations have different stress inputs")
    return selected


def fixture_reference():
    # Recheck even when the decoded reference is cached.
    if digest(FIXTURE_PATH) != FIXTURE_SHA256:
        raise ValueError("retained forced-shedding fixture binding changed")
    return _fixture_rows()


def _physical_only(value):
    value = deepcopy(value)
    value["options"].pop("numerical_preparation")
    return value


def validate_fixture(kwargs, role):
    reference = fixture_reference()[kwargs["formulation"]]
    expected = _physical_only(reference["inputs"])
    if role == "target_free":
        for storage in expected["storage"]:
            storage.update(terminal_soc=None, terminal_constraint=None)
    actual = _physical_only(mathematical_inputs(kwargs))
    if actual != expected:
        raise ValueError("E3+ physical fixture differs from the retained T=24 variant")
    if shortage(kwargs) != reference["stress"]["modified"]:
        raise ValueError("E3+ shortage witness differs from retained inputs")


def kwargs_for_arm(prepared, arm, ids, role="primary", *, assembly="hourly"):
    kwargs = prepared_model.kwargs_for_arm(prepared, arm, ids, role, assembly=assembly)
    factor = fixture_reference()[arm["formulation"]]["stress"]["demand_multiplier"]
    kwargs["df_load_p"] = kwargs["df_load_p"] * factor
    kwargs["df_load_q"] = kwargs["df_load_q"] * factor
    validate_fixture(kwargs, role)
    return kwargs


def specification():
    source = prepared_model.specification()
    selected = [a for a in source["arms"] if a["start"] == 1165 and a["stop"] == 1189
                and a["leg"] == "energy_neutral"]
    if [a["formulation"] for a in selected] != list(e3.FORMULATIONS):
        raise ValueError("ordinary deficit reference is not the four-formulation case")
    arms = [dict(a, id=i, window="Forced shedding") for i, a in enumerate(selected)]
    study = dict(source, cohort=COHORT, arms=arms, fixture=dict(
        path=str(FIXTURE_PATH.relative_to(ROOT)), sha256=FIXTURE_SHA256,
        stress=deepcopy(fixture_reference()["ac"]["stress"]),
        witness_allowance_mwh=WITNESS_ALLOWANCE_MWH))
    inputs = verified_inputs()
    for arm in arms:
        # Check exact physical preservation before artifacts; no graph/solve.
        kwargs_for_arm(inputs, arm, study["storage_device_ids"],
                       assembly="component_first" if arm["formulation"] == "ac" else "hourly")
    return study


def validate_output(root):
    root = root.resolve()
    for directory in (HERE / "results/e3", HERE / "results/e3_prepared", FIXTURE_PATH.parent):
        directory = directory.resolve()
        if root == directory or directory in root.parents or root in directory.parents:
            raise ValueError("E3+ cannot write into or above retained evidence")


def context():
    value = prepared_model.context()
    fixture_reference()
    value.update(cohort=COHORT, protocol_sha256=digest(HERE / "E3_PLUS_PROTOCOL.md"),
                 fixture_binding_sha256=FIXTURE_SHA256,
                 forced_shedding_witness_allowance_mwh=WITNESS_ALLOWANCE_MWH)
    for path in (ROOT / "experiments/ac_cost_qualification/fixture.py",
                 ROOT / "experiments/numerical_preparation/fixture.py"):
        value["code_sha256"][str(path.relative_to(ROOT))] = digest(path)
    return value


def verify_context(expected):
    if not expected["clean"] or context() != expected:
        raise ValueError("E3+ source/environment/fixture changed")


def audit(build, result, kwargs, named):
    common, relaxation = prepared_model.audit(build, result, kwargs, named)
    witness = shortage(kwargs)
    minimum = witness["minimum_ens_mwh"]
    if min(witness["minimum_shed_mw"]) <= 0 or minimum <= WITNESS_ALLOWANCE_MWH:
        raise ValueError("forced fixture lacks a material unavoidable-shedding witness")
    measured = float(result["energy_not_served"])
    shortfall = max(minimum - measured, 0.)
    common["metrics"]["forced_shedding_minimum_ens_mwh"] = minimum
    common["limits"]["forced_shedding_witness_mwh"] = WITNESS_ALLOWANCE_MWH
    common["residuals"]["forced_shedding_witness_mwh"] = shortfall
    common["passed"] = bool(common["passed"] and np.isfinite(measured)
                            and measured > 0 and shortfall <= WITNESS_ALLOWANCE_MWH)
    return common, relaxation


def assess(arm, role, record, kwargs, start=None, captured=None):
    return prepared_model.assess(arm, role, record, kwargs, start, captured, audit_fn=audit)


def independent_auditor(root, study):
    return prepared_model.independent_auditor(root, study, cohort=sys.modules[__name__])


def worker(directory, root):
    return e3.worker(directory, root, cohort=sys.modules[__name__])
