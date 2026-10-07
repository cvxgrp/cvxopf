"""Non-solving canonical/public checks and device-aligned Tracy comparisons."""

from pathlib import Path

import numpy as np
import cvxpy as cp

from . import fixture as f
from .audit import canonical_ac_accounting, physical_audit, serializable
from .run_qualification import read, digest
from tests.socp_matched import digest as input_digest

CALLS = tuple(f.Call(i, form, treatment, "tracy", 3, 1165, 1168)
              for i, (form, treatment) in enumerate((
                  ("socp", "prepared_socp"), ("lossy_dc", "prepared_dc"),
                  ("singlenode_dc", "prepared_dc")), 1))
HISTORICAL_BINDING_SHA = "6b7e8fe8126f9ced99efd0ed6c4c94d322dab15e85ad02192acacf6adf0c587c"
HISTORICAL_RESULTS = {
    24: "a567d6497572e78bdf4df4581beb7f461581436dc9a309bcae763be984f6190c",
    25: "fb7ea17d25ee1dff5a48187f185a37d57cfb1109a248dda2f84337c2befef7fd",
}
DIRECT = ("Pg", "Qg", "b", "b_q", "p_nd", "q_nd", "load_shed_fraction",
          "soc", "p_flows", "w", "W_re", "W_im")
COMMON = ("Pg", "Qg", "b", "b_q", "soc", "p_nd", "q_nd", "curtailment",
          "load_shed_fraction", "p_load_served", "q_load_served", "p_load_shed",
          "q_load_shed")


def common_inputs(value):
    """Strip only formulation from already preparation-disabled inputs."""
    return {k: v for k, v in value.items() if k != "formulation"}


def expand_primal(native, evidence):
    x = np.asarray(native["x"], float)
    if evidence is None:
        if x.ndim != 1 or not np.isfinite(x).all():
            raise ValueError("invalid original canonical primal")
        return x
    mapping = evidence["coordinates"]
    fixed = np.asarray(mapping["fixed"], int)
    free = np.setdiff1d(np.arange(mapping["full_size"]), fixed)
    scale = np.asarray(evidence["variable_scale"], float)
    if (x.shape != free.shape or scale.shape != free.shape or
        not np.isfinite(x).all() or not np.isfinite(scale).all() or
        np.any(scale <= 0) or len(np.unique(fixed)) != len(fixed) or
        np.any(fixed < 0) or np.any(fixed >= mapping["full_size"])):
        raise ValueError("invalid reduced canonical primal/map")
    full = np.empty(mapping["full_size"])
    full[free] = x * scale
    full[fixed] = mapping["values"]
    if not np.isfinite(full).all():
        raise ValueError("nonfinite restored canonical primal")
    return full


def canonical_layout(build):
    """Fresh canonicalization only; never solve or alter a solver registry."""
    chain = build.prob._construct_chain(solver=cp.CLARABEL,
        canon_backend=build.canonicalization_backend, ignore_dpp=True,
        solver_opts=f.solver_options(build.formulation))
    data, inverse = chain.apply(build.prob)
    offsets = inverse[-2].var_offsets
    layout = []
    for key, variable in build.variables.items():
        if not isinstance(variable, cp.Variable):
            raise ValueError(f"expected vectorized original leaf: {key}")
        variable_id = variable.id
        for item in inverse[:-1]:
            if isinstance(item, tuple) and len(item) == 3 and variable_id in item[0]:
                variable_id = item[0][variable_id].id
        if inverse[-2].var_shapes.get(variable_id) != variable.shape:
            raise ValueError(f"canonical original-leaf shape changed: {key}")
        offset = offsets[variable_id]
        layout.append(dict(name=key, shape=variable.shape, start=offset,
                           stop=offset + variable.size, is_original_variable=True))
    return data, serializable(layout)


def capture_coordinates(build, record):
    data, layout = canonical_layout(build)
    full = expand_primal(record["native"], record["preparation_evidence"])
    if full.shape != data["c"].shape:
        raise ValueError("fresh canonical layout dimension differs")
    raw = {name: variable.value for name, variable in build.variables.items()}
    return serializable(dict(full_x=full, layout=layout, raw=raw))


def discrepancy(actual, expected):
    a, b = np.asarray(actual, float), np.asarray(expected, float)
    if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
        return dict(passed=False, reason="shape/nonfinite mismatch",
                    actual_shape=a.shape, expected_shape=b.shape)
    error = float(np.max(abs(a - b), initial=0))
    limit = 1e-10 + 1e-12 * float(np.max(abs(b), initial=0))
    return dict(passed=error <= limit, maximum=error, tolerance=limit)


def check_coordinates(coordinates, result, kwargs, *, require_raw=True):
    """Independent time-last→time-first/unit/boundary projection arithmetic."""
    full = np.asarray(coordinates["full_x"], float)
    if full.ndim != 1 or not np.isfinite(full).all():
        raise ValueError("nonfinite/malformed full canonical primal")
    T, base = kwargs["T"], kwargs["case"]["baseMVA"]
    leaves, projections, raw = {}, {}, coordinates.get("raw", {})
    values = {}
    for item in coordinates["layout"]:
        if not item["is_original_variable"]:
            continue
        name, shape = item["name"], tuple(item["shape"])
        start, stop = item["start"], item["stop"]
        if (name in values or start < 0 or stop > full.size or
            stop-start != int(np.prod(shape))):
            raise ValueError("invalid original leaf layout")
        value = full[start:stop].reshape(shape, order="F")
        values[name] = value
        if require_raw:
            leaves[name] = discrepancy(raw.get(name), value)
    for name in DIRECT + (("v", "theta") if kwargs["formulation"] == "ac" else ()):
        if name not in values:
            # Reactive channels are genuinely absent in the DC models.
            if name in DIRECT and result.get(name) is not None:
                projections[name] = dict(passed=False, reason="public field lacks original leaf")
            continue
        expected = values[name].T
        public = name
        if name in {"Pg", "Qg", "p_flows"}:
            expected = expected * base
        elif name == "soc":
            projections["initial_soc"] = discrepancy(expected[0], [s.initial_soc for s in kwargs["storage"]])
            expected = expected[1:]
        elif name == "v":
            public = "Vm"
        elif name == "theta":
            public, expected = "Va_deg", np.rad2deg(expected)
        projections[public] = discrepancy(result.get(public), expected)
    required = {"Pg", "b", "soc", "p_nd", "load_shed_fraction"}
    if kwargs["formulation"] in {"socp", "ac"}:
        required |= {"Qg", "b_q", "q_nd"}
    if kwargs["formulation"] == "socp":
        required |= {"w", "W_re", "W_im"}
    elif kwargs["formulation"] == "lossy_dc":
        required |= {"p_flows"}
    elif kwargs["formulation"] == "ac":
        required |= {"Vm", "Va_deg"}
    for missing in required - projections.keys():
        projections[missing] = dict(passed=False, reason="missing required projection")
    if kwargs["formulation"] in {"lossy_dc", "singlenode_dc"}:
        for name in ("Qg", "b_q", "q_nd", "q_net"):
            projections["absent_" + name] = dict(passed=result.get(name) is None)
    expected_net_shape = (T,) if kwargs["formulation"] == "singlenode_dc" else (T, len(kwargs["case"]["bus"]))
    projections["p_net_shape"] = dict(passed=np.shape(result.get("p_net")) == expected_net_shape)
    passed = all(c["passed"] for c in [*leaves.values(), *projections.values()])
    return serializable(dict(passed=passed, canonical_to_restored=leaves,
                             canonical_to_public=projections))


def historical_candidates():
    """Pin and verify old evidence; never invoke source-bound historical replay."""
    root = f.OUTPUT
    if digest(root / "binding.json") != HISTORICAL_BINDING_SHA:
        raise ValueError("historical qualification binding changed")
    binding = read(root / "binding.json")
    candidates, pins = {}, {"binding.json": HISTORICAL_BINDING_SHA}
    for number, expected in HISTORICAL_RESULTS.items():
        directory = root / f"call-{number:03d}"
        completion, supervision = (read(directory / n) for n in ("completion.json", "supervision.json"))
        if (completion["artifacts"]["result.json.gz"] != expected or
            supervision["classification"] != "exited" or supervision["returncode"] != 0 or
            not 0 <= supervision["wall_seconds"] <= 180 or
            supervision["peak_sampled_rss_mib"] > 16384):
            raise ValueError("historical AC lacks pinned completed supervision")
        for name, sha in completion["artifacts"].items():
            if name != Path(name).name or digest(directory / name) != sha:
                raise ValueError("historical completed manifest/archive mismatch")
        for path in [directory / n for n in completion["artifacts"]] + [directory / "completion.json", directory / "supervision.json"]:
            pins[str(path.relative_to(root))] = digest(path)
        record = read(directory / "result.json.gz")
        if (record["mathematical_input_sha256"] != binding["calls"][number-1]["mathematical_input_sha256"] or
            record["request"] != read(directory / "request.json") or
            record["classification"] != completion["classification"] or
            record["optimizer_calls"] != 1 or record["native"]["status"] != 0):
            raise ValueError("historical mathematical input binding mismatch")
        captured = read(directory / "x0.json.gz")
        coordinates = dict(full_x=expand_primal(record["native"], record["preparation_evidence"]), layout=captured["layout"])
        call = f.calls()[number-1]
        kwargs = f.kwargs_for_call(call)
        if f.mathematical_inputs(kwargs) != binding["calls"][number-1]["mathematical_inputs"]:
            raise ValueError("current AC inputs differ from historical inputs")
        common, _ = physical_audit(None, record["result"], kwargs, record["named_costs"])
        candidates["ac_" + call.treatment] = dict(result=record["result"],
            common=common, coordinate_checks=check_coordinates(coordinates, record["result"], kwargs, require_raw=False),
            canonical_accounting=canonical_ac_accounting(record["native"], common),
            historical_classification=record["classification"],
            mathematical_input_sha256=input_digest(common_inputs(f.mathematical_inputs(kwargs))))
    return serializable(candidates), pins


def compare(candidates, kwargs):
    """Keep complete aligned arrays/deltas; different optima do not fail a gate."""
    axes = dict(hours=list(range(1165, 1168)), boundary_hours=list(range(1165, 1169)),
        generators=[dict(row=i, bus=g.bus, device_id=getattr(g, "device_id", None)) for i, g in enumerate(kwargs["generators"])],
        storage=[dict(device_id=s.device_id, bus=s.bus) for s in kwargs["storage"]],
        nondispatchable=[dict(device_id=u.device_id, bus=u.bus) for u in kwargs["nondispatchable"]],
        loads=[dict(device_id=u.device_id, bus=u.bus) for u in kwargs["loads"]])
    aligned, deltas = {}, {}
    for label, candidate in candidates.items():
        aligned[label] = {key: candidate["result"].get(key) for key in COMMON}
    labels = list(candidates)
    for i, left in enumerate(labels):
        for right in labels[i+1:]:
            pair = {}
            for key in COMMON:
                a, b = aligned[left][key], aligned[right][key]
                if a is None or b is None:
                    pair[key] = dict(available=False, reason="not modeled/unavailable in one or both candidates")
                    continue
                x, y = np.asarray(a, float), np.asarray(b, float)
                if x.shape != y.shape or not np.isfinite(x).all() or not np.isfinite(y).all():
                    pair[key] = dict(available=False, reason="shape/nonfinite mismatch")
                    continue
                pair[key] = dict(available=True, left_minus_right=x-y,
                                 maximum_absolute_delta=float(np.max(abs(x-y), initial=0)))
            deltas[left + "__" + right] = pair
    expected_axes = {"Pg": len(axes["generators"]), "Qg": len(axes["generators"]),
        **{k: len(axes["storage"]) for k in ("b", "b_q", "soc")},
        **{k: len(axes["nondispatchable"]) for k in ("p_nd", "q_nd", "curtailment")},
        **{k: len(axes["loads"]) for k in ("load_shed_fraction", "p_load_served", "q_load_served", "p_load_shed", "q_load_shed")}}
    axis_checks = {label: {key: dict(passed=np.shape(value) == (kwargs["T"], expected_axes[key]))
                          for key, value in arrays.items() if value is not None}
                   for label, arrays in aligned.items()}
    return serializable(dict(axes=axes, axis_checks=axis_checks, aligned=aligned, pairwise=deltas,
        note="Deltas are descriptive, not equality gates. DC reactive fields are absent; network models differ."))
