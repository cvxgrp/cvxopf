"""Eight serial stock CLARABEL solves with unchanged optional shedding."""

import argparse
from copy import copy
import gzip
import json
from pathlib import Path
import signal
import time

import clarabel
import clarabel.clarabel as extension
import numpy as np

from experiments.socp_conditioning import builtin_linsolvers as b
from experiments.socp_conditioning import termination_probe as t
from experiments.socp_conditioning.practical_lu_analysis import convergence

d = t.d
METHODS = ("qdldl", "faer")
CASES = {
    "surplus": (2, "Large surplus", 3308, 3332),
    "deficit": (6, "Large deficit", 1165, 1189),
    "ramp_up": (14, "Surplus to deficit", 8580, 8604),
    "ramp_down": (18, "Deficit to surplus", 2439, 2463),
}


def options(method):
    return b.options(method) | dict(tol_gap_rel=1e-6)


def context():
    base = t.f.context()
    return base | dict(
        experiment="four_conditions_optional_shedding_relative_gap_1e-6",
        cases={k: list(v) for k, v in CASES.items()},
        options={m: options(m) for m in METHODS},
        stock_extension=dict(
            version=clarabel.__version__, sha256=d.sha(extension.__file__)
        ),
        additional_sources=base["additional_sources"]
        | {
            str(Path(p).relative_to(d.ROOT)): d.sha(p)
            for p in (
                __file__,
                t.__file__,
                b.__file__,
                b.p.__file__,
                b.p.lu.__file__,
                b.p.lu.m.__file__,
                d.HERE / "practical_lu_analysis.py",
                d.HERE / "FOUR_CONDITIONS_PROTOCOL.md",
            )
        },
        policy=dict(
            interventions=[1, 2, 4],
            optional_shedding=True,
            max_optimizer_calls=8,
            native_success_required=True,
            objective_reconstruction_tolerance=1e-4,
            wall_seconds=d.WALL_SECONDS,
            rss_mib=d.RSS_MIB,
        ),
    )


def load_case(case, reference):
    number, label, start, stop = CASES[case]
    binding = json.loads((reference / "binding.json").read_text())
    if (
        binding["context"]["commit"] != d.BASE_COMMIT
        or d.git("rev-parse", "HEAD") != d.BASE_COMMIT
    ):
        raise ValueError("unexpected E3/source base")
    for path, digest in binding["context"]["code_sha256"].items():
        if d.sha(d.ROOT / path) != digest:
            raise ValueError(f"historical source changed: {path}")
    arm = binding["study"]["arms"][number]
    if (
        arm["id"],
        arm["window"],
        arm["start"],
        arm["stop"],
        arm["leg"],
        arm["formulation"],
    ) != (number, label, start, stop, "energy_neutral", "socp"):
        raise ValueError("wrong historical window")
    directory = reference / f"arm-{number:03d}/attempt-000"
    # E3 stopped before arm 18. Its frozen input definition remains usable,
    # but absence of a previous solve must not become an invented comparator.
    historical_available = directory.exists()
    if not historical_available and case != "ramp_down":
        raise ValueError("required historical solve missing")
    archived = None
    if historical_available:
        completion = json.loads((directory / "completion.json").read_text())
        for name, digest in completion["artifacts"].items():
            if d.sha(directory / name) != digest:
                raise ValueError("historical artifact changed")
        archived = json.load(gzip.open(directory / "result.json.gz", "rt"))
    kwargs = d.e3.kwargs_for_arm(
        d.e3.verified_inputs(), arm, binding["study"]["storage_device_ids"]
    )
    neutral = dict(
        case=kwargs["case"],
        kwargs={k: v for k, v in kwargs.items() if k not in {"case", "formulation"}},
    )
    mathematical_digest = d.input_digest(neutral)
    if (
        archived is not None
        and mathematical_digest != archived["mathematical_input_sha256"]
    ):
        raise ValueError("historical mathematical inputs changed")
    if not all(u.shedding_cost_per_mwh is not None for u in kwargs["loads"]):
        raise ValueError("original optional shedding required")
    return kwargs, dict(
        arm=arm,
        binding_sha256=d.sha(reference / "binding.json"),
        historical_solve_available=historical_available,
        completion_sha256=d.sha(directory / "completion.json")
        if historical_available
        else None,
        result_sha256=d.sha(directory / "result.json.gz")
        if historical_available
        else None,
        mathematical_input_sha256=mathematical_digest,
    )


def prepare(kwargs):
    build, objective, _ = d.build_variant(kwargs, "fixed_boxes")
    if "load_shedding_cost" not in build.expressions:
        raise ValueError("shedding model missing")
    data, _, inverse = build.prob.get_problem_data("CLARABEL", canon_backend="SCIPY")
    reduction = t.f.reduce(data, inverse)
    layout = t.oa.cone_layout(str(reduction.data["dims"]), reduction.data["A"].shape[0])
    R, D = t.j.joint_scales(reduction.data, layout)
    scaled = t.c.transform(reduction.data, R, D)
    mapping = t.c.verify_mapping(reduction.data, scaled, R, D)
    return build, objective, data, inverse, reduction, layout, R, D, scaled, mapping


def restore(build, inverse, data, full_x):
    if full_x.shape != data["c"].shape or not np.isfinite(full_x).all():
        raise ValueError("invalid retained full primal")
    for v in data["param_prob"].variables:
        offset = inverse[-2].var_offsets[v.id]
        v.save_value(full_x[offset : offset + v.size].reshape(v.shape, order="F"))


def accepted(record):
    info = record.get("native") or {}
    return bool(
        record.get("exception") is None
        and record.get("solver_exception") is None
        and info.get("status") == "Solved"
        and convergence(info, record["solver_options"])
        and (record.get("audit") or {}).get("passed") is True
        and record.get("objective_reconstruction_error", np.inf) <= 1e-4
    )


def worker(folder, reference, case, method):
    began = time.monotonic()
    r = dict(
        case=case,
        method=method,
        context=context(),
        exception=None,
        solver_exception=None,
        accepted=False,
        native={},
        audit=None,
        optimizer_calls=0,
        solver_options=options(method),
    )
    try:
        kwargs, r["historical_reference"] = load_case(case, reference)
        r["input_sha256"] = d.input_digest(kwargs)
        start = time.monotonic()
        build, objective, data, inverse, reduction, layout, R, D, scaled, mapping = (
            prepare(kwargs)
        )
        r["preparation_seconds"] = time.monotonic() - start
        r["substitution"], r["mapping_check"] = reduction.evidence, mapping
        r["canonical_archive"] = d.save_canonical(
            folder / "canonical.npz", data, inverse
        )
        r["reduced_archive"] = reduction.save(folder / "reduced.npz", reduction.data)
        r["transformed_archive"] = reduction.save(folder / "transformed.npz", scaled)
        with (folder / "scales.npz").open("xb") as stream:
            np.savez_compressed(stream, R=R, D=D)
        problem, _ = t.replay_problem(scaled, layout)
        replay, _, _ = problem.get_problem_data("CLARABEL", canon_backend="SCIPY")
        t.exact_matrices(scaled, replay)
        if str(replay["dims"]) != str(scaled["dims"]):
            raise ValueError("cone order changed")
        solve_build = copy(build)
        solve_build.prob = problem
        start = time.monotonic()
        try:
            with b.clean_environment(), b.STOCK_INSTRUMENTATION("CLARABEL", folder, r):
                solve_build.solve(
                    solver="CLARABEL",
                    warm_start=False,
                    verbose=True,
                    **r["solver_options"],
                )
        except Exception as exc:
            r["solver_exception"] = f"{type(exc).__name__}: {exc}"
        r["solve_interface_seconds"] = time.monotonic() - start
        r["cvxpy_status"] = problem.status
        raw = r.get("raw_clarabel")
        if raw is not None and np.isfinite(raw["x"]).all():
            x, s, z = (np.asarray(raw[k]) for k in ("x", "s", "z"))
            full_x = np.zeros(len(data["c"]))
            full_x[reduction.fixed], full_x[reduction.free] = reduction.values, D * x
            restore(build, inverse, data, full_x)
            result = t.extract_results(build)
            result.update(
                objective=float(objective.value),
                status=problem.status or "native_rejected_iterate",
            )
            named = {
                k: float(v.value)
                for k, v in build.expressions.items()
                if k.endswith("_cost")
            }
            r["result"], r["named_costs"] = result, named
            r["audit"] = d.original_audit(case, build, result, kwargs, named)
            r["objective_reconstruction_error"] = abs(
                0.5 * x @ (scaled["P"] @ x)
                + scaled["c"] @ x
                + reduction.offset
                + float(inverse[-1]["offset"])
                - float(objective.value)
            )
            r["original_primal_cones"] = t.oa.cone_errors(
                data["b"] - data["A"] @ full_x,
                t.oa.cone_layout(str(data["dims"]), data["A"].shape[0]),
            )
            r["common_kkt"] = dict(
                primal_inf=t.oa.inf(scaled["A"] @ x + s - scaled["b"]),
                dual_inf=t.oa.inf(scaled["P"] @ x + scaled["c"] + scaled["A"].T @ z),
                complementarity=float(s @ z),
                dual_cones=t.oa.cone_errors(z, layout, dual=True),
            )
            with (folder / "vectors.npz").open("xb") as stream:
                np.savez_compressed(stream, x=x, s=s, z=z, full_x=full_x)
            r["vectors_sha256"] = d.sha(folder / "vectors.npz")
    except Exception as exc:
        r["exception"] = f"{type(exc).__name__}: {exc}"
    r["context_after"] = context()
    if r["context_after"] != r["context"]:
        r["exception"] = "execution context changed"
    r["accepted"] = accepted(r)
    r["worker_seconds"] = time.monotonic() - began
    d.publish(folder / "arm.json.gz", d.e3.encode(r))
    d.publish(
        folder / "completion.json",
        dict(accepted=r["accepted"], arm_sha256=d.sha(folder / "arm.json.gz")),
    )


def verify_arm(root, case, method, binding):
    folder = root / f"{case}-{method}"
    sup = json.loads((folder / "supervision.json").read_text())
    if sup["classification"] != "completed" or sup["returncode"] != 0:
        raise ValueError("process/resource failure; stop")
    for name, digest in sup["artifacts"].items():
        if d.sha(folder / name) != digest:
            raise ValueError("artifact changed")
    r = json.load(gzip.open(folder / "arm.json.gz", "rt"))
    if (
        r["case"],
        r["method"],
        r["context"],
        r["context_after"],
        r["exception"],
        r["optimizer_calls"],
    ) != (case, method, binding, binding, None, 1):
        raise ValueError(f"worker evidence failure: {r['exception']}")
    if (
        r["solver_options"] != options(method)
        or f'name: "{method}"' not in r["native"]["linsolver"]
    ):
        raise ValueError("wrong options/backend")
    if accepted(r) != r["accepted"]:
        raise ValueError("acceptance mismatch")
    return dict(
        case=case,
        method=method,
        native=r["native"],
        accepted=r["accepted"],
        physical_checks=b.p.lu.a.numerical_checks(r["audit"]) if r["audit"] else None,
        objective=(r.get("result") or {}).get("objective"),
        named_costs=r.get("named_costs"),
        objective_reconstruction_error=r.get("objective_reconstruction_error"),
        worker_seconds=r["worker_seconds"],
        peak_rss_mib=sup["peak_rss_mib"],
        supervisor_seconds=sup["wall_seconds"],
        arm_sha256=d.sha(folder / "arm.json.gz"),
        supervision_sha256=d.sha(folder / "supervision.json"),
    )


def analyze(root, reference):
    summary = json.loads((root / "summary.json").read_text())
    binding = json.loads((root / "binding.json").read_text())
    if summary["context"] != binding or summary["optimizer_calls"] != 8:
        raise ValueError("root mismatch")
    for name, digest in (binding["sources"] | binding["additional_sources"]).items():
        if d.sha(d.ROOT / name) != digest:
            raise ValueError(f"execution source changed: {name}")
    results = []
    for saved, (case, method) in zip(
        summary["arms"], [(c, m) for c in CASES for m in METHODS], strict=True
    ):
        if verify_arm(root, case, method, binding) != saved:
            raise ValueError("summary differs")
        folder = root / f"{case}-{method}"
        r = json.load(gzip.open(folder / "arm.json.gz", "rt"))
        kwargs, historical = load_case(case, reference)
        if (
            historical != r["historical_reference"]
            or d.input_digest(kwargs) != r["input_sha256"]
        ):
            raise ValueError("input provenance differs")
        build, objective, data, inverse, reduction, _, _, _, scaled, _ = prepare(kwargs)
        t.exact_matrices(data, t.ca.matrices(folder / "canonical.npz"))
        t.exact_matrices(scaled, t.ca.matrices(folder / "transformed.npz"))
        if method == "faer":
            t.exact_matrices(
                scaled, t.ca.matrices(root / f"{case}-qdldl/transformed.npz")
            )
        if r.get("result") is not None:
            with np.load(folder / "vectors.npz") as vectors:
                restore(build, inverse, data, vectors["full_x"])
                x = vectors["x"]
                error = abs(
                    0.5 * x @ (scaled["P"] @ x)
                    + scaled["c"] @ x
                    + reduction.offset
                    + float(inverse[-1]["offset"])
                    - float(objective.value)
                )
            result = t.extract_results(build)
            result.update(
                objective=float(objective.value), status=r["result"]["status"]
            )
            named = {
                k: float(v.value)
                for k, v in build.expressions.items()
                if k.endswith("_cost")
            }
            audit = d.original_audit(case, build, result, kwargs, named)
            if (
                d.e3.encode(result) != r["result"]
                or d.e3.encode(audit) != r["audit"]
                or named != r["named_costs"]
            ):
                raise ValueError("physical reconstruction mismatch")
            if error != r["objective_reconstruction_error"]:
                raise ValueError("objective reconstruction mismatch")
        results.append(saved)
    return dict(
        optimizer_calls=0,
        promotional=False,
        arms=results,
        summary_sha256=d.sha(root / "summary.json"),
        analysis_source_sha256=d.sha(__file__),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--method", choices=METHODS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()

    def interrupt(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    root, reference = args.output.resolve(), args.reference.resolve()
    if args.worker:
        worker(root, reference, args.case, args.method)
    elif args.analyze:
        result = analyze(root, reference)
        d.publish(root / "analysis.json", d.e3.encode(result))
        print(json.dumps(result, indent=2))
    else:
        binding = context()
        for case in CASES:
            load_case(case, reference)
        root.mkdir(parents=True, exist_ok=False)
        d.publish(root / "binding.json", binding)
        arms = []
        for case in CASES:
            for method in METHODS:
                t.supervision.supervise(
                    root / f"{case}-{method}",
                    reference,
                    worker_module=__spec__.name,
                    context_factory=context,
                    worker_arguments=("--case", case, "--method", method),
                )
                arm = verify_arm(root, case, method, binding)
                arms.append(arm)
                print(json.dumps(arm), flush=True)
        if context() != binding:
            raise ValueError("context changed")
        d.publish(
            root / "summary.json",
            dict(context=binding, arms=arms, optimizer_calls=8, promotional=False),
        )


if __name__ == "__main__":
    main()
