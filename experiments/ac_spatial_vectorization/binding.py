"""Solver-free admission for the fixed fixture population and source pair."""

import ctypes
import importlib
import importlib.metadata
import io
import math
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

from .preflight import CASE500, CASE500_SHA256, benchmark_inputs, e3
from .run import ROOT, THREADS, atomic_immutable_json, read, sha256_path, utc


REVISIONS = dict(baseline="812aa7d62de6ea9d9e936be90ce8aac853658c3a",
                 batched="6ab39dac7e55d4e3991557ed6c5e8f7bce1264c5")
PACKAGES = ("cvxpy", "cyipopt", "sparsediffpy", "numpy", "pandas", "scipy")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def native_solver_identity():
    """Identify the loaded IPOPT library, not merely the cyipopt extension."""
    import cyipopt

    if sys.platform == "darwin":
        loader = ctypes.CDLL(None)
        loader._dyld_image_count.restype = ctypes.c_uint32
        loader._dyld_get_image_name.argtypes = (ctypes.c_uint32,)
        loader._dyld_get_image_name.restype = ctypes.c_char_p
        paths = [value.decode() for i in range(loader._dyld_image_count())
                 if (value := loader._dyld_get_image_name(i)) is not None]
    elif sys.platform.startswith("linux"):
        paths = [line.split(maxsplit=5)[-1] for line in Path("/proc/self/maps").read_text().splitlines()]
    else:
        raise ValueError("Native IPOPT library provenance is implemented for macOS and Linux")
    libraries = {Path(name).resolve() for name in paths if Path(name).name.startswith("libipopt")
                 and not Path(name).name.startswith("libipoptampl")}
    if len(libraries) != 1:
        raise ValueError(f"Could not identify a unique loaded IPOPT library: {libraries}")
    path = next(iter(libraries))
    return dict(version=list(cyipopt.IPOPT_VERSION), library=str(path), sha256=sha256_path(path))


def environment():
    native = {}
    for name in ("cyipopt", "sparsediffpy"):
        dist = importlib.metadata.distribution(name)
        for entry in dist.files or ():
            if entry.suffix in {".so", ".dylib", ".pyd", ".dll"}:
                native[f"{name}/{entry}"] = sha256_path(dist.locate_file(entry))
    return dict(python=sys.version, executable=sys.executable, platform=platform.platform(),
                packages={p: importlib.metadata.version(p) for p in PACKAGES},
                native_extensions=native, ipopt=native_solver_identity(), worker_threads=THREADS)


def harness_sources():
    """Imported execution helpers, including the delayed Tracy network audit."""
    importlib.import_module("experiments.case118_tracy_2021.stage_d_physics")
    prefix = "experiments.ac_spatial_vectorization"
    for name in ("run", "worker", "binding", "preflight"):
        importlib.import_module(f"{prefix}.{name}")
    files = set()
    for name, module in tuple(sys.modules.items()):
        if not name.startswith(("experiments.", "tests.")) or name == f"{prefix}.report":
            continue
        filename = getattr(module, "__file__", None)
        if filename is not None:
            path = Path(filename).resolve()
            if path.is_relative_to(ROOT) and path.suffix == ".py" and not path.name.startswith("test_"):
                files.add(path.relative_to(ROOT).as_posix())
    return {name: sha256_path(ROOT / name) for name in sorted(files)}


def verify_harness(expected):
    importlib.import_module("experiments.case118_tracy_2021.stage_d_physics")
    for name, digest in expected.items():
        if sha256_path(ROOT / name) != digest:
            raise ValueError(f"Relevant benchmark code changed: {name}")


def validate_settings(settings):
    def keys(obj, expected):
        if not isinstance(obj, dict) or set(obj) != set(expected.split()):
            raise ValueError(f"Expected settings fields: {expected}")

    def positive(value):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("Benchmark limits and tolerances must be finite and positive")

    keys(settings, "limits ipopt snapshot_tolerances")
    keys(settings["limits"], "wall_seconds rss_mib poll_seconds")
    keys(settings["ipopt"], "tol max_iter mu_strategy")
    keys(settings["snapshot_tolerances"], "power voltage angle cost_abs cost_rel")
    for group in (settings["limits"], settings["snapshot_tolerances"]):
        for value in group.values():
            positive(value)
    positive(settings["ipopt"]["tol"])
    positive(settings["ipopt"]["max_iter"])
    if (type(settings["ipopt"]["max_iter"]) is not int or
            settings["ipopt"]["mu_strategy"] not in {"adaptive", "monotone"}):
        raise ValueError("IPOPT requires an integer iteration limit and a known mu_strategy")
    return settings


def source_snapshot(root, name, revision):
    output = root / "sources" / name
    files = {}
    with tarfile.open(fileobj=io.BytesIO(git("archive", revision, "src/cvxopf"))) as archive:
        for entry in archive:
            if entry.isdir():
                continue
            path = Path(entry.name)
            if not entry.isfile() or path.parts[:2] != ("src", "cvxopf") or ".." in path.parts:
                raise ValueError(f"Unexpected source snapshot entry: {entry.name}")
            target = output / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(entry) as source, target.open("xb") as stream:
                shutil.copyfileobj(source, stream)
            files[path.as_posix()] = sha256_path(target)
    return files


def work_matrix(fixtures):
    work = []
    for phase, repeats in (("compile", 3), ("solve", 1)):
        for index, fixture in enumerate(fixtures):
            for repeat in range(1, repeats + 1):
                sides = tuple(REVISIONS) if (index + repeat) % 2 else tuple(reversed(REVISIONS))
                for side in sides:
                    work.append(dict(id=f"{phase}:{fixture}:{repeat}:{side}", fixture=fixture,
                                     phase=phase, repeat=repeat, revision=side))
    return work


def require_no_ambient_options():
    if (ROOT / "ipopt.opt").exists():
        raise ValueError("Ambient ipopt.opt would override bound settings")


def verify_context(root, binding):
    require_no_ambient_options()
    if (binding["schema"] != 1 or binding["revisions"] != REVISIONS or
            binding["work"] != work_matrix(binding["fixture_order"])):
        raise ValueError("Unsupported benchmark binding")
    validate_settings(binding["settings"])
    if environment() != binding["environment"]:
        raise ValueError("Relevant benchmark environment changed")
    verify_harness(binding["harness"])
    if sha256_path(root / "case500.m") != CASE500_SHA256:
        raise ValueError("Bound PGLib input changed")
    for side, expected in binding["source_hashes"].items():
        base = root / "sources" / side
        actual = {p.relative_to(base).as_posix(): sha256_path(p) for p in (base / "src/cvxopf").rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts}
        if actual != expected:
            raise ValueError(f"Bound {side} model source changed")


def bind(root, settings_path, case500_path=None):
    from .worker import build_model, named_start
    from cvxopf import ac_problem

    require_no_ambient_options()
    settings = validate_settings(read(settings_path))
    if git("show", f"{REVISIONS['batched']}:src/cvxopf/ac_problem.py") != Path(ac_problem.__file__).read_bytes():
        raise ValueError("Bind from the batched source to freeze the named starts")
    source = CASE500 if case500_path is None else case500_path
    fixtures = {}
    for fixture, metadata, kwargs in benchmark_inputs(source):
        build = build_model(kwargs)
        fixtures[fixture] = dict(**metadata, input_sha256=e3.input_digest(kwargs),
                                 named_start_sha256=e3.input_digest(named_start(build)))
        del build
    root.mkdir(parents=True, exist_ok=False)
    with source.open("rb") as data, (root / "case500.m").open("xb") as out:
        shutil.copyfileobj(data, out)
    if sha256_path(root / "case500.m") != CASE500_SHA256:
        raise ValueError("Source snapshot changed during bind")
    snapshots = {side: source_snapshot(root, side, rev) for side, rev in REVISIONS.items()}
    if ({key for key in snapshots["baseline"].keys() | snapshots["batched"].keys()
         if snapshots["baseline"].get(key) != snapshots["batched"].get(key)} != {"src/cvxopf/ac_problem.py"}):
        raise ValueError("The model revisions differ beyond the intended assembly module")
    order = list(fixtures)
    atomic_immutable_json(root / "binding.json", dict(
        schema=1, created_utc=utc(), harness_commit=git("rev-parse", "HEAD").decode().strip(),
        revisions=REVISIONS, source_hashes=snapshots, harness=harness_sources(),
        environment=environment(), settings=settings, fixtures=fixtures, fixture_order=order, work=work_matrix(order),
    ))
    return sha256_path(root / "binding.json")
