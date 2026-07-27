"""Shared lossy-DC setup for battery-terminal experiment studies."""

from __future__ import annotations

from dataclasses import dataclass
import warnings

import pandas as pd

from cvxopf.problem import OPFBuild, build_opf_multistep
from cvxopf.testcases import case9

from experiments.battery_terminal.devices import (
    make_dispatchable_generators,
    make_nondispatchable_units,
)
from experiments.battery_terminal.scenario import (
    REPRESENTATIVE_WINDOWS,
    ScenarioConfig,
    ScenarioData,
    generate_scenario,
    select_representative_window,
)


OPTIMAL_STATUSES = {"optimal", "optimal_inaccurate"}


@dataclass(frozen=True)
class PreparedExperiment:
    """Fixed scenarios and devices shared by comparable controller runs."""

    scenarios: dict[str, ScenarioData]
    nondispatchable: list
    generators: list


def prepare_experiment(
    source: pd.DataFrame,
    scenario_config: ScenarioConfig,
) -> PreparedExperiment:
    """Construct every representative scenario and size fixed devices jointly."""
    scenarios = {
        name: generate_scenario(
            select_representative_window(source, name),
            scenario_config,
        )
        for name in REPRESENTATIVE_WINDOWS
    }
    return PreparedExperiment(
        scenarios=scenarios,
        nondispatchable=make_nondispatchable_units(
            [scenario.df_nd for scenario in scenarios.values()]
        ),
        generators=make_dispatchable_generators(),
    )


def build_lossy_dc(
    prepared: PreparedExperiment,
    scenario: ScenarioData,
    storage,
) -> OPFBuild:
    """Build one lossy-DC study case using the fixed experiment devices."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return build_opf_multistep(
            case9(),
            scenario.df_P,
            None,
            T=len(scenario.df_P),
            formulation="lossy_dc",
            storage=[storage],
            nondispatchable=prepared.nondispatchable,
            df_nd=scenario.df_nd,
            generators=prepared.generators,
            delta=1.0,
        )


def build_singlenode_dc(
    prepared: PreparedExperiment,
    scenario: ScenarioData,
    storage,
) -> OPFBuild:
    """Build the matching copper-plate diagnostic case."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return build_opf_multistep(
            case9(),
            scenario.df_P,
            None,
            T=len(scenario.df_P),
            formulation="singlenode_dc",
            storage=[storage],
            nondispatchable=prepared.nondispatchable,
            df_nd=scenario.df_nd,
            generators=prepared.generators,
            delta=1.0,
        )


def build_ac(
    prepared: PreparedExperiment,
    scenario: ScenarioData,
    storage,
) -> OPFBuild:
    """Build one AC study case using the fixed experiment devices."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return build_opf_multistep(
            case9(),
            scenario.df_P,
            scenario.df_Q,
            T=len(scenario.df_P),
            formulation="ac",
            storage=[storage],
            nondispatchable=prepared.nondispatchable,
            df_nd=scenario.df_nd,
            generators=prepared.generators,
            delta=1.0,
        )
