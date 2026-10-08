"""Fresh E3 policies and independent, non-solving native evidence replay.

The historical cohort remains distinct. Physical inputs and start recipes are
shared; preparation, phase ordering and acceptance are prospectively bound.
"""

from dataclasses import asdict, fields, replace
from importlib.metadata import distribution
from pathlib import Path

import clarabel
import cvxpy as cp
import numpy as np

from cvxopf import NumericalPreparation, build_opf_multistep, extract_results
from cvxopf import _hierarchical_solver as starts
from experiments.ac_cost_coordinates.run import monitoring as monitoring
from experiments.ac_cost_qualification.audit import canonical_objective
from experiments.ac_production_verification.audit import publication_check
from experiments.m11_socp.e1_compare import restored_result
from experiments.m11_socp.e2_v2_audit import ac_canonical
from experiments.numerical_preparation.audit import serializable
from . import e3
from .ac_archive import verify_logical
from .prepare import HERE, ROOT, digest
from .run_stage_d import require_matching_audit
from .stage_d import ROLES, read

verified_inputs = e3.verified_inputs
encode = e3.encode
DEFAULT_PROTOCOL = dict(e3.DEFAULT_PROTOCOL, schema=2, max_launches=120,
    convex_options=dict(e3.DEFAULT_PROTOCOL["convex_options"], direct_solve_method="qdldl"),
    socp_options=dict(tol_gap_abs=1e-10, tol_gap_rel=1e-6, tol_feas=1e-10,
        max_iter=5000, max_threads=1, direct_solve_method="qdldl",
        min_terminate_step_length=1e-8, reduced_tol_gap_abs=1e-10,
        reduced_tol_gap_rel=1e-10, reduced_tol_feas=1e-10,
        reduced_tol_infeas_abs=1e-10, reduced_tol_infeas_rel=1e-10,
        reduced_tol_ktratio=1e-6))


def validate_protocol(value):
    if set(value) != set(DEFAULT_PROTOCOL) or value["schema"] != 2:
        raise ValueError("prepared E3 protocol schema/fields differ")
    # Numerical controls are fixed for this cohort; only budgets may be revised.
    for key in ("ac_options", "convex_options", "socp_options"):
        if value[key] != DEFAULT_PROTOCOL[key]:
            raise ValueError("prepared numerical settings cannot change")
    legacy = {k: v for k, v in value.items() if k != "socp_options"}
    legacy.update(schema=1, convex_options=e3.DEFAULT_PROTOCOL["convex_options"])
    e3.validate_protocol(legacy)
    return value


def solver_options(formulation, protocol):
    return protocol["socp_options" if formulation == "socp" else "convex_options"]


def policy(formulation, assembly="hourly"):
    return NumericalPreparation(normalize_device_limits=formulation in {"ac", "socp"},
        exact_fixed_boxes=True, canonical_scaling="none" if formulation == "ac" else "joint5",
        cost_coordinates=formulation == "ac", objective_assembly=assembly)


def policy_record(formulation, assembly):
    return asdict(policy(formulation, assembly))


def request_for(arm, slot, causal, target_free, protocol, wall):
    ac = arm["formulation"] == "ac"
    if not 0 <= slot < (20 if ac else 1):
        raise ValueError("invalid prepared recovery slot")
    return dict(arm_id=arm["id"], slot=slot, phase=slot // 10 if ac else 0,
        phase_slot=slot % 10, role=ROLES[slot % 10] if ac else "primary",
        objective_assembly="component_first" if ac and slot < 10 else "hourly",
        causal_source=causal, target_free_source=target_free, protocol=protocol, wall_seconds=wall)


def specification():
    return dict(e3.specification(), cohort="prepared", schema=2,
                ac_objective_phases=["component_first", "hourly"])


def validate_output(root):
    historical = (HERE / "results/e3").resolve()
    root = Path(root).resolve()
    if root == historical or historical in root.parents or root in historical.parents:
        raise ValueError("prepared cohort cannot write into or above historical E3")


def context():
    value = e3.context()
    value.update(cohort="prepared", protocol_sha256=digest(HERE / "E3_PREPARED_PROTOCOL.md"),
                 policies={f: policy_record(f, "component_first" if f == "ac" else "hourly")
                           for f in e3.FORMULATIONS}, ac_objective_phases=["component_first", "hourly"])
    paths = [ROOT / "experiments/m11_socp/e1_compare.py", ROOT / "experiments/m11_socp/e2_v2_audit.py",
             ROOT / "experiments/ac_cost_coordinates/run.py", ROOT / "experiments/ac_cost_qualification/audit.py",
             ROOT / "experiments/ac_production_verification/audit.py",
             ROOT / "experiments/numerical_preparation/audit.py"]
    value["code_sha256"].update({str(p.relative_to(ROOT)): digest(p) for p in paths})
    value["resolved_clarabel_settings"] = {}
    for formulation in ("socp", "lossy_dc", "singlenode_dc"):
        settings = clarabel.DefaultSettings()
        for key, setting in solver_options(formulation, DEFAULT_PROTOCOL).items():
            setattr(settings, key, setting)
        settings.verbose = True
        # CLARABEL's unlimited time_limit is +inf. Preserve it explicitly in
        # strict JSON provenance, without changing the native solver setting.
        value["resolved_clarabel_settings"][formulation] = serializable({
            key: getattr(settings, key) for key in dir(settings)
            if not key.startswith("_") and not callable(getattr(settings, key))})
    value["native_extension_sha256"] = {}
    for package in ("clarabel", "cyipopt", "sparsediffpy"):
        installed = distribution(package)
        binaries = {str(installed.locate_file(p)): digest(installed.locate_file(p))
                    for p in installed.files or [] if str(p).endswith((".so", ".dylib"))}
        if not binaries:
            raise ValueError(f"native extension provenance unavailable: {package}")
        value["native_extension_sha256"][package] = binaries
    value["effective_ac_options"] = dict(DEFAULT_PROTOCOL["ac_options"], hessian_approximation="exact",
                                          bound_relax_factor=0., max_iter=3000)
    return value


def verify_context(expected):
    if not expected["clean"] or context() != expected:
        raise ValueError("prepared E3 source/environment changed")


def kwargs_for_arm(prepared, arm, ids, role="primary", *, assembly="hourly"):
    kwargs = e3.kwargs_for_arm(prepared, arm, ids, role)
    kwargs["options"] = replace(kwargs["options"], numerical_preparation=policy(arm["formulation"], assembly))
    return kwargs


def physical_input_digest(kwargs):
    neutral = dict(case=kwargs["case"], kwargs={k: v for k, v in kwargs.items()
                                              if k not in {"case", "formulation"}})
    neutral["kwargs"]["options"] = replace(kwargs["options"], numerical_preparation=NumericalPreparation())
    return (e3.validate_matching(neutral, neutral) if kwargs["formulation"] in {"ac", "socp"}
            else e3.input_digest(neutral))


def audit(build, result, kwargs, named):
    return e3.audit(build, result, kwargs, named,
                    total_cost_relative_tolerance=1e-6 if kwargs["formulation"] == "socp" else None)


def prepared_start(build, iteration):
    """Persist expected native handoff before solving, without optimization.

    This is a prospective start, not proof the optimizer was entered. Completed
    preparation evidence separately confirms its actual verified handoff.
    """
    _, _, bindings, _, data, inverse, mapping = ac_canonical(build)
    adjusted = data["x0"].copy()
    adjusted[mapping.fixed] = mapping.values
    return serializable(dict(iteration=iteration, evidence_kind="prospective_canonical_start",
        complete_x0=data["x0"], adjusted_x0=adjusted, reduced_x0=adjusted[mapping.free],
        coordinates={f.name: getattr(mapping, f.name) for f in fields(mapping) if not f.name.startswith("_")},
        layout=[[list(v.shape), inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size]
                for v in data["problem"].variables()],
        cost_maps=[dict(kind=b.term.kind, shape=list(b.leaf.shape), scale=b.scale, physical_start=b.physical_start)
                   for b in bindings]))


def verify_prepared_start(evidence, captured):
    from experiments.numerical_preparation.audit import evidence_record
    actual = evidence_record(evidence)
    if (captured["complete_x0"] != actual["assigned_x0"] or
            captured["adjusted_x0"] != actual["adjusted_x0"] or
            captured["reduced_x0"] != actual["reduced_x0"] or
            captured["coordinates"] != actual["coordinates"] or
            captured["layout"] != [[s, a, b] for _, s, a, b in actual["start_layout"]]):
        raise ValueError("prospective canonical start differs from actual verified handoff")


def restore_ac(build, evidence, start, captured):
    """Rebuild cost leaves, fixed map and native objective from physical x0."""
    starts._assign_start(build, {k: np.asarray(v) for k, v in start["assigned_start"].items()})
    _, solver_build, bindings, reduction, data, inverse, mapping = ac_canonical(build)
    expected = serializable({f.name: getattr(mapping, f.name) for f in fields(mapping)
                             if not f.name.startswith("_")})
    layout = [[list(v.shape), inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size]
              for v in data["problem"].variables()]
    if (evidence["coordinates"] != expected or
            [[s, a, b] for _, s, a, b in evidence["start_layout"]] != layout or
            evidence["assigned_x0"] != data["x0"].tolist() or
            captured["complete_x0"] != data["x0"].tolist() or
            captured["layout"] != layout or captured["coordinates"] != expected):
        raise ValueError("fresh AC map/layout/assigned start differs")
    adjusted = data["x0"].copy()
    adjusted[mapping.fixed] = mapping.values
    if (evidence["adjusted_x0"] != adjusted.tolist() or
            evidence["reduced_x0"] != adjusted[mapping.free].tolist() or
            captured["adjusted_x0"] != adjusted.tolist() or
            captured["reduced_x0"] != adjusted[mapping.free].tolist() or
            evidence["variable_scale"] != np.ones(mapping.free.size).tolist() or
            evidence["row_scale"] != np.ones(mapping.kept.size).tolist() or
            evidence["checks"]["objective_assembly"] != build.numerical_preparation.objective_assembly):
        raise ValueError("AC reduced start/scales/assembly differ")
    maps = evidence["checks"].get("cost_coordinate_maps", [])
    if len(maps) != len(bindings):
        raise ValueError("missing AC cost-coordinate maps")
    expected_cost_maps = serializable([dict(kind=b.term.kind, shape=list(b.leaf.shape),
        scale=b.scale, physical_start=b.physical_start) for b in bindings])
    if captured["cost_maps"] != expected_cost_maps:
        raise ValueError("prospective cost maps differ from fresh preparation")
    for old, binding in zip(maps, bindings, strict=True):
        retained = next((s, a, b) for identity, s, a, b in evidence["start_layout"]
                        if identity == old["solver_variable_id"])
        offset = inverse[-1].var_offsets[binding.leaf.id]
        if (old["kind"] != binding.term.kind or old["scale"] != binding.scale.tolist() or
                old["physical_start"] != binding.physical_start.tolist() or
                retained != (list(binding.leaf.shape), offset, offset+binding.leaf.size)):
            raise ValueError("AC cost-coordinate inverse differs")
    native = evidence["native"]
    full = mapping.expand(np.asarray(native["x"], float))
    if not np.isfinite(full).all():
        raise ValueError("nonfinite AC native primal")
    rebuilt, _ = canonical_objective(data, inverse, full)
    reconstruction = abs(rebuilt-native["obj_val"])
    limit = 1e-7+1e-12*abs(native["obj_val"])
    if not np.isfinite(reconstruction) or reconstruction > limit:
        raise ValueError("AC canonical objective reconstruction failed")
    for variable in solver_build.prob.variables():
        offset = inverse[-1].var_offsets[variable.id]
        variable.save_value(full[offset:offset+variable.size].reshape(variable.shape, order="F"))
    for binding in bindings:
        binding.term.variable.save_value(binding.leaf.value/binding.scale)
    build.prob._status, build.prob._value = cp.OPTIMAL, float(build.prob.objective.value)
    slack = []
    for binding, auxiliary in reduction.cycling_auxiliaries:
        offset = inverse[-1].var_offsets[auxiliary.id]
        values = full[offset:offset+auxiliary.size].reshape(auxiliary.shape, order="F")
        slack.extend((values-abs(binding.absolute.args[0].value)).ravel())
    return serializable(extract_results(build)), np.asarray(slack), reconstruction


def economics(native_cost, costs, slack=None, *, formulation):
    physical = float(sum(costs.values()))
    excess = 0. if slack is None else float(np.sum(slack))
    l1 = 0. if slack is None else float(np.sum(abs(slack)))
    cycling_limit = 1e-4+1e-6*abs(costs["storage_cost"])
    relative = 1e-10 if formulation in {"lossy_dc", "singlenode_dc"} else 1e-6
    difference, limit = native_cost-physical-excess, 1e-4+relative*abs(physical)
    return dict(physical_objective=physical, canonical_objective=native_cost,
        cycling_epigraph_excess=excess, cycling_slack_l1=l1, cycling_limit=cycling_limit,
        cycling_warning=l1 > cycling_limit, unexplained_difference=difference, unexplained_limit=limit,
        passed=bool(np.isfinite([native_cost, physical, excess, l1, difference]).all() and abs(difference) <= limit))


def assess(arm, role, record, kwargs, start=None, captured=None):
    """Fresh inverse-chain replay; native rejection remains a numerical outcome."""
    native, evidence = record["native"], record["preparation_evidence"]
    if native != evidence["native"] or record["preparation_policy"] != asdict(kwargs["options"].numerical_preparation):
        raise ValueError("native/preparation/policy disagreement")
    wanted = 0 if arm["formulation"] == "ac" else "Solved"
    if native.get("status") != wanted:
        return dict(passed=False, reason="native solver did not fully converge", native_status=native.get("status"))
    build = build_opf_multistep(**kwargs)
    try:
        if record["mathematical_input_sha256"] != physical_input_digest(kwargs):
            raise ValueError("retained mathematical input digest differs")
        if arm["formulation"] == "ac":
            restored, slack, reconstruction = restore_ac(build, evidence, start, captured)
            native_cost = native["obj_val"]
        else:
            restored, offset = restored_result(build, native, evidence)
            native_cost = native["obj_val"]+offset+native["cvxpy_objective_offset"]
            slack, reconstruction = None, evidence["checks"]["objective_reconstruction"]
        publication = publication_check(restored, record["result"])
        named = {k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")}
        if named != record["named_costs"]:
            raise ValueError("retained named costs differ from restored expressions")
        common, _ = audit(build, restored, kwargs, named)
        require_matching_audit(common, record["audit"])
        economic = economics(native_cost, common["costs"], slack, formulation=arm["formulation"])
        return dict(passed=common["passed"] and economic["passed"] and record.get("exception") is None,
                    publication=publication, economics=economic, canonical_reconstruction=reconstruction)
    except (AssertionError, ValueError, TypeError, KeyError, AttributeError, StopIteration) as exc:
        return dict(passed=False, reason=f"independent native replay failed: {exc}")


def independent_auditor(root, study):
    verify_context(read(root / "binding.json")["context"])
    if study != specification():
        raise ValueError("prepared study differs from frozen selection and policies")
    prepared = verified_inputs()

    def check(arm, role, record):
        request = record["request"]
        kwargs = kwargs_for_arm(prepared, arm, study["storage_device_ids"], role,
                               assembly=request["objective_assembly"])
        # The replay caller has already verified safe artifact hashes.
        directory = (root / record["attempt"]).resolve()
        if root.resolve() not in directory.parents:
            raise ValueError("attempt identity escapes run")
        checked = assess(arm, role, record, kwargs,
            read(directory / "start.json") if arm["formulation"] == "ac" else None,
            read(directory / "x0.json.gz") if arm["formulation"] == "ac" else None)
        if checked != record["independent_audit"]:
            raise ValueError("worker and independent acceptance replay disagree")
        if checked["passed"] and arm["formulation"] == "ac":
            verify_logical(record["logical_solution"], record["result"], kwargs["case"]["baseMVA"], kwargs["T"])
        return checked["passed"]

    return check


def worker(directory, root):
    import sys
    return e3.worker(directory, root, cohort=sys.modules[__name__])
