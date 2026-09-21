"""Evaluate immutable build-time baseMVA as a DPP-preserving alternative."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import time

import cvxpy as cp
import numpy as np

from cvxopf import Load, build_opf
from cvxopf import _component_adapters as adapters
from cvxopf._component_adapter import InjectionContribution
from cvxopf.load import _PreparedLoadParameters
from cvxopf.testcases import case9


OUTPUT = Path(__file__).with_name("dpp_results.json")


def _immutable_base_injections(units, prepared, variables, context):
    channels = adapters._load_step_channels(prepared, variables, context)
    incidence = np.asarray(prepared["Cload"])
    p_pu = -(incidence @ channels["p_load_served"]) / context.base_mva
    q_pu = (
        -(incidence @ channels["q_load_served"]) / context.base_mva
        if context.formulation == "ac"
        else None
    )
    return InjectionContribution(p_pu, q_pu)


@contextmanager
def _scaling_representation(immutable_base: bool):
    original = adapters.LOAD_ADAPTER
    if immutable_base:
        formulations = {
            formulation: replace(
                binding, injections=_immutable_base_injections
            )
            for formulation, binding in original.formulations.items()
        }
        adapters.LOAD_ADAPTER = replace(
            original, formulations=formulations
        )
    try:
        yield
    finally:
        adapters.LOAD_ADAPTER = original


def _controller(problem: cp.Problem) -> _PreparedLoadParameters:
    by_name = {parameter.name(): parameter for parameter in problem.parameters()}
    return _PreparedLoadParameters(
        by_name["load_p_mw"],
        by_name["load_p_eligible_mw"],
        by_name["load_eligibility_mask"],
        by_name.get("load_q_mvar", cp.Parameter((1, 1), value=[[0.0]])),
    )


def _measure(immutable_base: bool) -> dict[str, object]:
    with _scaling_representation(immutable_base):
        build = build_opf(
            case9(),
            formulation="lossy_dc",
            loads=[
                Load(
                    5,
                    900.0,
                    "interruptible",
                    shedding_cost_per_mwh=5000.0,
                )
            ],
        )
        identities = (
            id(build.prob),
            tuple(map(id, build.prob.variables())),
            tuple(map(id, build.prob.constraints)),
            tuple(map(id, build.prob.parameters())),
        )
        started = time.perf_counter()
        build.solve()
        first_seconds = time.perf_counter() - started
        _controller(build.prob).update_active(np.array([[850.0]]))
        started = time.perf_counter()
        build.solve()
        updated_seconds = time.perf_counter() - started
        updated_identities = (
            id(build.prob),
            tuple(map(id, build.prob.variables())),
            tuple(map(id, build.prob.constraints)),
            tuple(map(id, build.prob.parameters())),
        )
    return {
        "scaling": (
            "immutable_base_scalar"
            if immutable_base else "inv_base_mva_parameter"
        ),
        "is_dcp": build.prob.is_dcp(),
        "is_dpp": build.prob.is_dpp(),
        "first_end_to_end_seconds": first_seconds,
        "updated_end_to_end_seconds": updated_seconds,
        "status": build.prob.status,
        "objective": float(build.prob.value),
        "identities_unchanged": identities == updated_identities,
    }


def main() -> None:
    results = [_measure(False), _measure(True)]
    OUTPUT.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
