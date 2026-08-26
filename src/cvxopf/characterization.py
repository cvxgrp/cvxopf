"""Representation-aware structural characterization for OPF builds.

These records describe the CVXPY source and canonical graphs without treating
representation-specific counts as scientific formulation invariants.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Literal

import cvxpy as cp

from cvxopf.problem import OPFBuild, TemporalAssembly


CanonicalBackend = Literal["CPP", "SCIPY"]


@dataclass(frozen=True)
class NamedShape:
    """One stable public mapping key and its represented CVXPY shapes."""

    name: str
    shapes: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class SourceGraphStructure:
    """Immutable characterization of a build's CVXPY source graph."""

    formulation: str
    temporal_assembly: TemporalAssembly
    horizon: int | None
    variable_schema: tuple[NamedShape, ...]
    expression_schema: tuple[NamedShape, ...]
    data_keys: tuple[str, ...]
    parameter_shapes: tuple[tuple[int, ...], ...]
    variable_object_count: int
    parameter_object_count: int
    constraint_object_count: int
    equality_object_count: int
    inequality_object_count: int
    other_constraint_object_count: int
    scalar_variables: int
    scalar_equalities: int
    scalar_inequalities: int
    scalar_data: int


@dataclass(frozen=True)
class CanonicalGraphStructure:
    """Immutable convex canonicalization dimensions for one backend."""

    formulation: str
    temporal_assembly: TemporalAssembly
    backend: CanonicalBackend
    solver: str
    canonical_variable_count: int
    equality_rows: int
    nonnegative_rows: int
    exponential_cones: int
    second_order_cones: tuple[int, ...]
    positive_semidefinite_cones: tuple[int, ...]
    power_cones_3d: tuple[float, ...]
    coefficient_rows: int
    coefficient_columns: int
    coefficient_nonzeros: int
    quadratic_nonzeros: int
    reduction_chain: tuple[str, ...]


def _shape(value: object) -> tuple[int, ...]:
    raw = getattr(value, "shape", ())
    return tuple(int(item) for item in raw)


def _mapping_schema(mapping: Mapping[str, object]) -> tuple[NamedShape, ...]:
    records: list[NamedShape] = []
    for name in sorted(mapping):
        value = mapping[name]
        values = value if isinstance(value, list) else [value]
        records.append(NamedShape(str(name), tuple(_shape(item) for item in values)))
    return tuple(records)


def characterize_source_graph(build: OPFBuild) -> SourceGraphStructure:
    """Capture stable schemas and representation-specific source counts."""
    constraints = build.prob.constraints
    equality_count = sum(
        isinstance(constraint, cp.constraints.Equality) for constraint in constraints
    )
    inequality_count = sum(
        isinstance(constraint, cp.constraints.Inequality) for constraint in constraints
    )
    metrics = build.prob.size_metrics
    horizon_value = build.data.get("T")
    horizon = None if horizon_value is None else int(horizon_value)
    return SourceGraphStructure(
        formulation=build.formulation,
        temporal_assembly=build.temporal_assembly,
        horizon=horizon,
        variable_schema=_mapping_schema(build.variables),
        expression_schema=_mapping_schema(build.expressions),
        data_keys=tuple(sorted(str(key) for key in build.data)),
        parameter_shapes=tuple(
            sorted(_shape(parameter) for parameter in build.prob.parameters())
        ),
        variable_object_count=len(build.prob.variables()),
        parameter_object_count=len(build.prob.parameters()),
        constraint_object_count=len(constraints),
        equality_object_count=equality_count,
        inequality_object_count=inequality_count,
        other_constraint_object_count=(
            len(constraints) - equality_count - inequality_count
        ),
        scalar_variables=int(metrics.num_scalar_variables),
        scalar_equalities=int(metrics.num_scalar_eq_constr),
        scalar_inequalities=int(metrics.num_scalar_leq_constr),
        scalar_data=int(metrics.num_scalar_data),
    )


def characterize_convex_canonicalization(
    build: OPFBuild,
    *,
    backend: CanonicalBackend = "CPP",
) -> CanonicalGraphStructure:
    """Canonicalize for CLARABEL and retain sparse cone-program dimensions."""
    if not build.is_convex:
        raise ValueError(
            "convex canonical characterization does not support AC/DNLP builds"
        )
    if backend not in {"CPP", "SCIPY"}:
        raise ValueError("backend must be 'CPP' or 'SCIPY'")
    canon_backend = cp.CPP_CANON_BACKEND if backend == "CPP" else cp.SCIPY_CANON_BACKEND
    data, chain, _inverse = build.prob.get_problem_data(
        cp.CLARABEL, canon_backend=canon_backend
    )
    dims = data["dims"]
    coefficient = data["A"]
    quadratic = data.get("P")
    objective = data["c"]
    return CanonicalGraphStructure(
        formulation=build.formulation,
        temporal_assembly=build.temporal_assembly,
        backend=backend,
        solver=str(cp.CLARABEL),
        canonical_variable_count=int(objective.shape[0]),
        equality_rows=int(dims.zero),
        nonnegative_rows=int(dims.nonneg),
        exponential_cones=int(dims.exp),
        second_order_cones=tuple(int(item) for item in dims.soc),
        positive_semidefinite_cones=tuple(int(item) for item in dims.psd),
        power_cones_3d=tuple(float(item) for item in dims.p3d),
        coefficient_rows=int(coefficient.shape[0]),
        coefficient_columns=int(coefficient.shape[1]),
        coefficient_nonzeros=int(coefficient.nnz),
        quadratic_nonzeros=(0 if quadratic is None else int(quadratic.nnz)),
        reduction_chain=tuple(
            type(reduction).__name__ for reduction in chain.reductions
        ),
    )
