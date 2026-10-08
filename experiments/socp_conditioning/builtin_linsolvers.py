"""Stock CLARABEL QDLDL/Faer comparison; four serial bounded workers."""

import argparse
from contextlib import contextmanager
import gzip
import json
import os
from pathlib import Path
import signal
from unittest.mock import patch

import clarabel
import clarabel.clarabel as extension
import numpy as np
from scipy import sparse
from cvxpy.reductions.solvers.conic_solvers.clarabel_conif import CLARABEL

from experiments.socp_conditioning import practical_lu as p

d, t = p.d, p.t
STOCK_INSTRUMENTATION = t.instrumentation
METHODS = ("qdldl", "faer")
PRIOR = d.HERE / "results/practical_lu_001"
PRIOR_SHA = "f77f329ac502daa9c70a68a5cb218132f4448d3950cab738ab2364ff6c52a1ce"


def options(method):
    if method not in METHODS:
        raise ValueError("unknown treatment")
    return p.options("CLARABEL") | dict(direct_solve_method=method)


def availability():
    records = []
    for method in (*METHODS, "panua", "mkl"):
        settings = clarabel.DefaultSettings()
        settings.verbose, settings.max_threads = False, 1
        settings.direct_solve_method = method
        try:
            solver = clarabel.DefaultSolver(
                sparse.csc_matrix([[1.0]]),
                np.array([0.0]),
                sparse.csc_matrix([[-1.0]]),
                np.array([1.0]),
                [clarabel.NonnegativeConeT(1)],
                settings,
            )
            records.append(
                dict(
                    method=method,
                    available=True,
                    actual=str(solver.get_info().linsolver),
                )
            )
        except Exception as exc:
            records.append(
                dict(
                    method=method,
                    available=False,
                    exception=f"{type(exc).__name__}: {exc}",
                )
            )
    return dict(optimizer_calls=0, constructor_checks=records)


def context():
    if d.sha(PRIOR / "summary.json") != PRIOR_SHA:
        raise ValueError("comparison changed")
    base = p.context()
    base.pop("native_engine_sha256")
    return base | dict(
        experiment="stock_clarabel_builtin_linear_backends",
        stock_extension=dict(
            path=extension.__file__,
            sha256=d.sha(extension.__file__),
            version=clarabel.__version__,
        ),
        prior_summary_sha256=PRIOR_SHA,
        options={m: options(m) for m in METHODS},
        additional_sources=base["additional_sources"]
        | {
            str(Path(v).relative_to(d.ROOT)): d.sha(v)
            for v in (__file__, d.HERE / "BUILTIN_LINSOLVER_PROTOCOL.md")
        },
        policy=dict(
            methods=list(METHODS),
            case_order=list(p.CASES),
            max_optimizer_calls=4,
            custom_native_engine_used=False,
            native_success_required=True,
        ),
    )


@contextmanager
def clean_environment():
    env = {k: v for k, v in os.environ.items() if not k.startswith("CVXOPF_")}
    with patch.dict(os.environ, env, clear=True):
        yield


def verify_payload(payload, previous, method):
    expected = previous | dict(
        settings=previous["settings"] | dict(direct_solve_method=method)
    )
    if payload != expected:
        raise ValueError("more than backend changed")


@contextmanager
def instrumentation(solver, folder, record, *, mode):
    original = CLARABEL.solve_via_data

    def interface(self, data, warm_start, verbose, solver_opts, solver_cache=None):
        payload = p.n.native_input(folder, data, verbose, solver_opts)
        case, method = folder.name.split("-", 1)
        if case not in p.CASES or method not in METHODS:
            raise ValueError("unexpected arm identity")
        verify_payload(
            payload,
            json.loads((PRIOR / case / "native_input.json").read_text()),
            method,
        )
        record["native_input_sha256"] = d.sha(folder / "native_input.json")
        record["requested_backend"] = payload["settings"]["direct_solve_method"]
        return original(self, data, warm_start, verbose, solver_opts, solver_cache)

    with (
        patch.object(CLARABEL, "solve_via_data", interface),
        STOCK_INSTRUMENTATION(solver, folder, record),
    ):
        yield


def analyze_arm(root, case, method, binding):
    folder = root / f"{case}-{method}"
    sup = json.loads((folder / "supervision.json").read_text())
    if sup["classification"] != "completed" or sup["returncode"] != 0:
        raise ValueError("process/resource failure")
    for name, digest in sup["artifacts"].items():
        if d.sha(folder / name) != digest:
            raise ValueError("artifact changed")
    record = json.load(gzip.open(folder / "arm.json.gz", "rt"))
    if (
        record["exception"] is not None
        or record["context"] != binding
        or record["context_after"] != binding
        or record["optimizer_calls"] != 1
    ):
        raise ValueError(f"worker evidence failure: {record['exception']}")
    if record["solver_options"] != options(method):
        raise ValueError("settings changed")
    payload = json.loads((folder / "native_input.json").read_text())
    verify_payload(
        payload, json.loads((PRIOR / case / "native_input.json").read_text()), method
    )
    if f'name: "{method}"' not in record["native"]["linsolver"]:
        raise ValueError("actual backend mismatch")
    result = {
        k: record.get(k)
        for k in (
            "native",
            "accepted",
            "solver_exception",
            "common_kkt",
            "named_costs",
            "objective_reconstruction_error",
            "worker_seconds",
            "solve_interface_seconds",
        )
    }
    return result | dict(
        case=case,
        method=method,
        checks=p.lu.a.numerical_checks(record["audit"]) if record["audit"] else None,
        physical_objective=(record.get("result") or {}).get("objective"),
        physical_residuals=(record["audit"] or {}).get("residuals"),
        peak_rss_mib=sup["peak_rss_mib"],
        supervisor_seconds=sup["wall_seconds"],
        arm_sha256=d.sha(folder / "arm.json.gz"),
        supervision_sha256=d.sha(folder / "supervision.json"),
        native_input_sha256=d.sha(folder / "native_input.json"),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--case", choices=p.CASES)
    parser.add_argument("--method", choices=METHODS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()

    def interrupt(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    root, reference = args.output.resolve(), args.reference.resolve()
    with patch.object(t, "context", context), patch.object(t, "SOLVERS", ("CLARABEL",)):
        if args.worker:
            with (
                patch.object(t, "options", lambda solver: options(args.method)),
                patch.object(p.n, "instrumentation", instrumentation),
                patch.object(p, "native_environment", clean_environment),
            ):
                p.worker(root, reference, args.case)
            return
        binding = context()
        available = availability()
        if any(
            not r["available"]
            for r in available["constructor_checks"]
            if r["method"] in METHODS
        ):
            raise ValueError("declared backend unavailable")
        root.mkdir(parents=True, exist_ok=False)
        d.publish(root / "binding.json", binding)
        d.publish(root / "availability.json", available)
        arms = []
        for case in p.CASES:
            for method in METHODS:
                p.lu.supervision.supervise(
                    root / f"{case}-{method}",
                    reference,
                    worker_module=__spec__.name,
                    context_factory=context,
                    worker_arguments=("--case", case, "--method", method),
                )
                arm = analyze_arm(root, case, method, binding)
                arms.append(arm)
                d.publish(root / f"{case}-{method}.json", arm)
                print(
                    json.dumps(
                        {
                            k: arm[k]
                            for k in ("case", "method", "native", "accepted", "checks")
                        }
                    ),
                    flush=True,
                )
        if context() != binding:
            raise ValueError("context changed")
        d.publish(
            root / "summary.json",
            dict(
                context=binding,
                arms=arms,
                promotional=False,
                optimizer_calls=4,
                availability_sha256=d.sha(root / "availability.json"),
            ),
        )


if __name__ == "__main__":
    main()
