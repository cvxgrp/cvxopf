"""Initialization-only recovery of the three unresolved Tracy transfers."""

from pathlib import Path

import numpy as np

from cvxopf._ac_start_mapping import variables_by_name
from cvxopf._hierarchical_solver import _assign_start, _perturbed_start
from cvxopf.hierarchical import ShiftedRecoveryConfig
from experiments.numerical_preparation import run_qualification as q
from experiments.numerical_preparation.audit import serializable
from experiments.tracy_battery_transfer import model as transfer, run as original

HERE = Path(__file__).resolve().parent
ORIGIN = transfer.HERE / "results/transfer_001"
ORIGIN_COMMIT = "4fbd85f3bd80a573edac4da284e325804f1d77a3"
CASE_IDS = (13, 14, 15)
RECOVERY = ShiftedRecoveryConfig()
LIMITS = dict(
    max_launches=21,
    wall_seconds=3600.0,
    rss_mib=16384.0,
    total_worker_seconds=75600.0,
    poll_seconds=1.0,
)


def registry(data):
    cases = [job for job in transfer.jobs(data) if job["id"] in CASE_IDS]
    if len(cases) != 3 or [job["donor"] for job in cases] != list(transfer.DONORS):
        raise ValueError("unexpected recovery cases")
    trials = []
    for job in cases:
        arm = data["cases"][job["scenario"]]["ac"]["arm"]
        if (arm["start"], arm["stop"], arm["leg"]) != (2439, 2463, "energy_neutral"):
            raise ValueError("recovery is restricted to the retained neutral window")
        slots = [("flat", None, None)]
        slots += [
            (center, index, scale)
            for center in ("matched_ac", "flat")
            for index, scale in enumerate(RECOVERY.perturbation_scales, 1)
        ]
        for slot, (center, index, scale) in enumerate(slots):
            seed = (
                None
                if index is None
                else RECOVERY.seed_base
                + 100 * job["id"]
                + 10 * (1 if center == "matched_ac" else 2)
                + index
            )
            trials.append(
                dict(
                    id=len(trials) + 1,
                    job=job,
                    slot=slot,
                    center=center,
                    scale=scale,
                    seed=seed,
                )
            )
    return trials


def origin_hashes(data):
    """Validate historical supervision/manifests without requiring today's HEAD."""
    binding = q.read(ORIGIN / "binding.json")
    if (
        binding["context"]["commit"] != ORIGIN_COMMIT
        or binding["context"]["clean"] is not True
        or binding["limits"] != transfer.LIMITS
        or [row["job"] for row in binding["rows"]] != transfer.jobs(data)
        or binding["sources"] != original.retained_hashes(data)
        or q.read(ORIGIN / "invocation-finish.json")["outcome"] != "matrix_complete"
    ):
        raise ValueError("historical transfer origin is not the completed bound matrix")
    if sorted(p.name for p in ORIGIN.glob("call-*")) != [
        f"call-{n:03d}" for n in range(1, 19)
    ]:
        raise ValueError("historical attempt inventory changed")
    paths = [
        ORIGIN / name
        for name in (
            "binding.json",
            "protocol.json",
            "invocation-start.json",
            "invocation-finish.json",
            "report.json",
        )
    ]
    for number in range(1, 19):
        directory = ORIGIN / f"call-{number:03d}"
        sup = q.read(directory / "supervision.json")
        if not q.resource_evidence(directory, sup):
            raise ValueError("historical attempt lacks verified resource evidence")
        if (directory / "completion.json").exists():
            completion = q.read(directory / "completion.json")
            hashes = {
                name: q.digest(directory / name)
                for name in original.ARTIFACTS
                if (directory / name).exists()
            }
            if completion["artifacts"] != hashes or "result.json.gz" not in hashes:
                raise ValueError("historical completion manifest changed")
            record = q.read(directory / "result.json.gz")
            if (
                record["classification"] != original.classify(record)
                or completion["classification"] != record["classification"]
                or record["binding_sha256"] != q.digest(ORIGIN / "binding.json")
                or record["request"] != q.read(directory / "request.json")
            ):
                raise ValueError("historical record identity/classification changed")
            if number in CASE_IDS and (
                record["classification"] != "native_rejection"
                or sup["classification"] != "exited"
                or sup["returncode"] != 0
            ):
                raise ValueError("expected unresolved native rejection")
        elif number != 13 or sup["classification"] != "wall_limit":
            raise ValueError("unexpected historical missing completion")
        paths.extend(path for path in directory.iterdir() if path.is_file())
    return {
        str(path.relative_to(transfer.ROOT)): q.digest(path) for path in sorted(paths)
    }


def alternative_start(build, kwargs, matched, trial):
    """Reuse production logical perturbation order; restore every fixed entry."""
    fixed = {name: matched[name].copy() for name in ("b", "soc")}
    if trial["center"] == "flat":
        for variable in variables_by_name(build).values():
            variable.value = None
        center = q.physical_start(build, kwargs)
    else:
        center = {name: value.copy() for name, value in matched.items()}
    center.update(fixed)
    if trial["scale"] is None:
        raw = {name: value.copy() for name, value in center.items()}
        assigned = {name: value.copy() for name, value in center.items()}
    else:
        raw, assigned = _perturbed_start(
            center, build, scale=trial["scale"], seed=trial["seed"]
        )
    # The helper is shared with the hierarchy, which ordinarily perturbs storage.
    # This experiment never does: restore pins/dynamics in both evidence arrays.
    masks = q.structural_inputs(kwargs)
    for values in (raw, assigned):
        values.update({name: value.copy() for name, value in fixed.items()})
        pg_fixed = np.asarray(masks["pg_fixed"])
        lower = (
            np.asarray([g.p_min_mw for g in kwargs["generators"]])
            / kwargs["case"]["baseMVA"]
        )
        values["Pg"][pg_fixed, :] = lower[pg_fixed, None]
        values["p_nd"][np.asarray(masks["nd_fixed"]).T] = 0.0
        values["theta"][build.data["ref"], :] = 0.0
    _assign_start(build, assigned)
    return raw, assigned


def construct(trial, data, prepared):
    if trial not in registry(data):
        raise ValueError("undeclared alternative-start trial")
    build, kwargs, schedule, matched, base = transfer.construct(
        trial["job"], data, prepared
    )
    historical = dict(q.read(ORIGIN / "binding.json")["rows"][trial["job"]["id"] - 1])
    historical.pop("canonical_start_sha256")
    if base != historical:
        raise ValueError("alternative start would change the historical problem/source")
    raw, assigned = alternative_start(build, kwargs, matched, trial)
    ratings = np.asarray(build.data["storage_apparent_power_rating"])[:, None]
    row = dict(
        base,
        trial=trial,
        start_sha256=transfer.object_hash(assigned),
        raw_start_sha256=transfer.object_hash(raw),
        matched_start_sha256=transfer.object_hash(matched),
        start_diagnostics=dict(
            matched_battery_circle_excess_mva2=float(
                max(0.0, np.max(matched["b"] ** 2 + matched["b_q"] ** 2 - ratings**2))
            ),
            battery_circle_excess_mva2=float(
                max(0.0, np.max(assigned["b"] ** 2 + assigned["b_q"] ** 2 - ratings**2))
            ),
            interpretation="Initialization diagnostic, not a physical acceptance audit",
        ),
    )
    return build, kwargs, schedule, raw, assigned, serializable(row)
