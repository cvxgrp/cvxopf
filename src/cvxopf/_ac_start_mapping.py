"""Lossless AC start conversions shared by the controller and replay worker.

Recovery operates on the historical per-step coordinates. Conversion adds the
fixed initial SoC boundary, but never perturbs or shifts that new coordinate.
This module constructs no optimization graph and owns no recovery policy.
"""

from __future__ import annotations

from typing import Mapping

import cvxpy as cp
import numpy as np

from cvxopf.problem import OPFBuild


def variables_by_name(build: OPFBuild) -> dict[str, cp.Variable]:
    variables = build.prob.variables()
    names = [variable.name() for variable in variables]
    if len(names) != len(set(names)):
        raise ValueError("hierarchical initialization requires unique variable names")
    return dict(zip(names, variables, strict=True))


def stepwise_template(build: OPFBuild) -> dict[str, np.ndarray]:
    """Describe historical coordinate shapes without constructing another graph."""
    variables = variables_by_name(build)
    if build.temporal_assembly == "stepwise":
        return {name: np.zeros(variable.shape) for name, variable in variables.items()}
    horizon = int(build.data["T"])
    result = {}
    for name, variable in variables.items():
        if len(variable.shape) != 2 or variable.shape[-1] != horizon + (name == "soc"):
            raise ValueError(f"unsupported time-last AC variable shape for {name}")
        native: tuple[int, ...] = (variable.shape[0],)
        if name in {"theta", "v"}:
            native = (variable.shape[0], 1)
        elif name in {"P", "Q"}:
            native = (int(build.data["nb"]), int(build.data["nb"]))
        for step in range(horizon):
            result[f"{name}_{step}"] = np.zeros(native)
    return result


def pack_start(
    step_values: Mapping[str, np.ndarray], build: OPFBuild, initial: object,
) -> dict[str, np.ndarray]:
    """Map time-suffixed physical coordinates into the destination graph.

    Flatten each step in C order, matching the replay and AC dense P/Q layout.
    The native IPOPT vector is independently verified in its own F ordering.
    """
    variables = variables_by_name(build)
    if build.temporal_assembly == "stepwise":
        if set(step_values) != set(variables):
            raise ValueError("starting-value namespace does not match destination")
        result = {name: np.asarray(value, dtype=float).copy() for name, value in step_values.items()}
    else:
        result = {}
        used = set()
        horizon = int(build.data["T"])
        for name in variables:
            names = [f"{name}_{t}" for t in range(horizon)]
            columns = [np.asarray(step_values[n], dtype=float).reshape(-1) for n in names]
            used.update(names)
            if name == "soc":
                columns.insert(0, np.asarray(initial, dtype=float))
            result[name] = np.column_stack(columns)
        if used != set(step_values):
            raise ValueError("Unmapped historical variables")
    for name, variable in variables.items():
        if result[name].shape != variable.shape:
            raise ValueError(f"starting-value shape mismatch for {name}")
    return result


def unpack_values(
    values: Mapping[str, np.ndarray], template: Mapping[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Restore historical names/shapes, excluding the initial SoC boundary."""
    result = {}
    for name, original in template.items():
        base, index = name.rsplit("_", 1)
        column = int(index) + (base == "soc")
        result[name] = np.asarray(values[base], dtype=float)[:, column].reshape(
            np.asarray(original).shape
        ).copy()
    return result


def stepwise_values(build: OPFBuild, values: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    if set(values) != set(variables_by_name(build)):
        raise ValueError("starting-value namespace does not match source")
    if build.temporal_assembly == "stepwise":
        return {name: np.asarray(value, dtype=float).copy() for name, value in values.items()}
    return unpack_values(values, stepwise_template(build))


def project_stepwise_values(build: OPFBuild, values: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    packed = pack_start(values, build, build.data.get("storage_initial_soc", []))
    projected = {
        name: np.asarray(variable.project(packed[name]), dtype=float)
        for name, variable in variables_by_name(build).items()
    }
    return stepwise_values(build, projected)
