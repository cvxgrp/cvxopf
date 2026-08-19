"""Compare explicit and leaf-bound shedding fractions in full OPF builds."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import time

import cvxpy as cp
import numpy as np
import pandas as pd
from cvxpy.reductions.solvers.nlp_solving_chain import (
    _build_nlp_chain,
    _set_nlp_initial_point,
)

from cvxopf import Load, build_opf, build_opf_multistep
from cvxopf import _component_adapters as adapters
from cvxopf._component_adapter import StepContext, VariableSpec
from cvxopf.testcases import case9
from cvxopf.load import _PreparedLoadParameters


OUTPUT = Path(__file__).with_name("results.json")
CONVEX_OUTPUT = Path(__file__).with_name("convex_results.json")


def _leaf_variable_specs(units, prepared, context: StepContext):
    nsheddable = int(prepared["nsheddable"])
    if nsheddable == 0:
        return ()
    indices = np.asarray(prepared["sheddable_load_indices"], dtype=int)
    upper = np.asarray(prepared["load_max_shed_fraction"])[indices]
    return (
        VariableSpec(
            "load_shed_fraction",
            (nsheddable,),
            attributes={"bounds": [np.zeros(nsheddable), upper]},
        ),
    )


def _leaf_constraints(units, prepared, variables, context: StepContext):
    fraction = variables.get("load_shed_fraction")
    if fraction is None:
        return ()
    parameters = prepared["_load_parameters"]
    indices = np.asarray(prepared["sheddable_load_indices"], dtype=int)
    maximum = np.asarray(prepared["load_max_shed_fraction"])[indices]
    return (
        fraction
        <= cp.multiply(
            maximum, parameters.eligibility_mask[context.step, indices]
        ),
    )


@contextmanager
def _load_representation(name: str):
    original = adapters.LOAD_ADAPTER
    if name == "leaf_plus_eligibility":
        formulations = {
            formulation: replace(
                binding,
                variable_specs=_leaf_variable_specs,
                operating_constraints=_leaf_constraints,
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


def _count_constraints(problem: cp.Problem) -> tuple[int, int]:
    equalities = sum(
        constraint.size
        for constraint in problem.constraints
        if isinstance(constraint, cp.constraints.Equality)
    )
    inequalities = sum(
        constraint.size
        for constraint in problem.constraints
        if not isinstance(constraint, cp.constraints.Equality)
    )
    return int(equalities), int(inequalities)


def _timed_solve(problem: cp.Problem) -> dict[str, object]:
    """Time DNLP canonicalization/setup and IPOPT separately."""
    chain, solver_options = _build_nlp_chain(
        problem,
        cp.IPOPT,
        {"max_iter": 1000, "print_level": 0, "sb": "yes"},
    )
    cache = problem._solver_cache.setdefault("NLP", {})
    _set_nlp_initial_point(problem)
    started = time.perf_counter()
    canonical_problem, inverse_data = chain.apply(problem=problem)
    canonicalization_seconds = time.perf_counter() - started
    started = time.perf_counter()
    solution = chain.solver.solve_via_data(
        canonical_problem,
        False,
        False,
        solver_opts=solver_options,
        solver_cache=cache,
    )
    solver_seconds = time.perf_counter() - started
    problem.unpack_results(solution, chain, inverse_data)
    return {
        "canonicalization_and_setup_seconds": canonicalization_seconds,
        "solver_seconds": solver_seconds,
        "iterations": solution.get("num_iters"),
        "status": problem.status,
        "objective": (
            None if problem.value is None else float(problem.value)
        ),
    }


def _load_parameter_controller(problem: cp.Problem) -> _PreparedLoadParameters:
    by_name = {parameter.name(): parameter for parameter in problem.parameters()}
    return _PreparedLoadParameters(
        by_name["load_p_mw"],
        by_name["load_p_eligible_mw"],
        by_name["load_eligibility_mask"],
        by_name["load_q_mvar"],
    )


def _measure(
    representation: str, *, multistep: bool, repeat: int
) -> dict[str, object]:
    loads = [
        Load(
            5,
            700.0,
            "interruptible",
            q_load_mvar=200.0,
            shedding_cost_per_mwh=5000.0,
            max_shed_fraction=0.9,
        ),
        Load(7, 100.0, "fixed", q_load_mvar=35.0),
    ]
    with _load_representation(representation):
        started = time.perf_counter()
        if multistep:
            T = 4
            p = pd.DataFrame(
                {
                    "interruptible": [700.0, 0.0, -20.0, 650.0],
                    "fixed": [100.0, 100.0, 100.0, 100.0],
                }
            )
            q = pd.DataFrame(
                {
                    "interruptible": [200.0, 20.0, -5.0, 180.0],
                    "fixed": [35.0, 35.0, 35.0, 35.0],
                }
            )
            build = build_opf_multistep(
                case9(),
                T=T,
                formulation="ac",
                loads=loads,
                df_load_p=p,
                df_load_q=q,
            )
        else:
            build = build_opf(case9(), formulation="ac", loads=loads)
        construction_seconds = time.perf_counter() - started

        identity_before = {
            "problem": id(build.prob),
            "variables": [id(variable) for variable in build.prob.variables()],
            "constraints": [id(item) for item in build.prob.constraints],
            "parameters": [id(item) for item in build.prob.parameters()],
        }
        first_solve = _timed_solve(build.prob)
        controller = _load_parameter_controller(build.prob)
        updated = (
            np.array([[650.0, 100.0], [5.0, 100.0],
                      [-10.0, 100.0], [600.0, 100.0]])
            if multistep
            else np.array([[650.0, 100.0]])
        )
        controller.update_active(updated)
        assert np.array_equal(
            controller.p_eligible_mw.value, np.maximum(updated, 0.0)
        )
        assert np.array_equal(
            controller.eligibility_mask.value, (updated > 0.0).astype(float)
        )
        updated_solve = _timed_solve(build.prob)
        identity_after = {
            "problem": id(build.prob),
            "variables": [id(variable) for variable in build.prob.variables()],
            "constraints": [id(item) for item in build.prob.constraints],
            "parameters": [id(item) for item in build.prob.parameters()],
        }

    equalities, inequalities = _count_constraints(build.prob)
    fraction = build.variables["load_shed_fraction"]
    fraction_values = (
        [None if item.value is None else item.value.tolist() for item in fraction]
        if isinstance(fraction, list)
        else None if fraction.value is None else fraction.value.tolist()
    )
    return {
        "representation": representation,
        "multistep": multistep,
        "repeat": repeat,
        "construction_seconds": construction_seconds,
        "is_dcp": build.prob.is_dcp(),
        "is_dpp": build.prob.is_dpp(),
        "first_solve": first_solve,
        "updated_solve": updated_solve,
        "identities_unchanged": identity_before == identity_after,
        "scalar_variables": build.prob.size_metrics.num_scalar_variables,
        "scalar_equalities": equalities,
        "scalar_inequalities": inequalities,
        "updated_shed_fraction": fraction_values,
    }


def _measure_convex(representation: str, formulation: str) -> dict[str, object]:
    with _load_representation(representation):
        build = build_opf(
            case9(),
            formulation=formulation,
            loads=[
                Load(
                    5,
                    900.0,
                    "interruptible",
                    shedding_cost_per_mwh=5000.0,
                    max_shed_fraction=0.9,
                )
            ],
        )
        build.solve()
    return {
        "representation": representation,
        "formulation": formulation,
        "status": build.prob.status,
        "objective": float(build.prob.value),
        "shed_fraction": build.variables["load_shed_fraction"].value.tolist(),
        "is_dcp": build.prob.is_dcp(),
    }


def main() -> None:
    results = [
        _measure(representation, multistep=multistep, repeat=repeat)
        for multistep in (False, True)
        for representation in ("explicit", "leaf_plus_eligibility")
        for repeat in range(5)
    ]
    OUTPUT.write_text(json.dumps(results, indent=2) + "\n")
    convex_results = [
        _measure_convex(representation, formulation)
        for formulation in ("lossy_dc", "singlenode_dc")
        for representation in ("explicit", "leaf_plus_eligibility")
    ]
    CONVEX_OUTPUT.write_text(json.dumps(convex_results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    print(json.dumps(convex_results, indent=2))


if __name__ == "__main__":
    main()
