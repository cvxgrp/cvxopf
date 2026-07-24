"""
Single-node DC dispatch (copper-plate) example.

Demonstrates the "singlenode_dc" formulation across four scenarios:
    1. case9 single-step, no storage / no nondispatchable
    2. make_singlenode_case single-step with storage and nondispatchable
    3. make_singlenode_case 24-hour multistep with storage and nondispatchable
    4. case9 3-step multistep with varying load

Run with:
    uv run python examples/case9_singlenode_dc.py
"""

import numpy as np
import pandas as pd

from cvxopf.testcases import case9, make_singlenode_case
from cvxopf.problem import build_opf, build_opf_multistep, StorageUnitIdeal
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.results import extract_results
from cvxopf.generator import DispatchableGenerator


# ---------------------------------------------------------------------------
# Part 1 — case9, no storage, no nondispatchable
# ---------------------------------------------------------------------------
build = build_opf(case9(), formulation="singlenode_dc")
build.solve()
r = extract_results(build)
print(f"[case9 single-step] status={r['status']}, objective={r['objective']:.2f}, "
      f"Pg={np.round(r['Pg'], 2)} MW, p_net={r['p_net']:.4f} MW")


# ---------------------------------------------------------------------------
# Part 2 — make_singlenode_case, single-step with storage and nondispatchable
# ---------------------------------------------------------------------------
case = make_singlenode_case(
    P_load_MW=250.0,
    generators=[
        DispatchableGenerator(
            bus=1, p_max_mw=200.0, cost_coeffs=(0.0, 10.0, 0.05)
        ),
        DispatchableGenerator(
            bus=1, p_max_mw=150.0, cost_coeffs=(0.0, 15.0, 0.08)
        ),
    ],
)
storage = StorageUnitIdeal(
    bus=1, apparent_power_rating=50.0, capacity=100.0,
    initial_soc=50.0, aging_weight=1e-2,
)
nd_unit = NondispatchableUnit(bus=1, p_available=80.0, apparent_power_rating=100.0)

build = build_opf(case, formulation="singlenode_dc",
                  storage=[storage], nondispatchable=[nd_unit])
build.solve()
r = extract_results(build)
print(f"[singlenode single-step] status={r['status']}, objective={r['objective']:.2f}")
print(f"  Pg={np.round(r['Pg'], 2)} MW")
print(f"  b={np.round(r['b'], 4)} MW, soc={np.round(r['soc'], 4)} MWh")
print(f"  p_nd={np.round(r['p_nd'], 4)} MW, curtailment={np.round(r['curtailment'], 4)} MW")


# ---------------------------------------------------------------------------
# Part 3 — make_singlenode_case, multistep T=24 with storage and nondispatchable
# ---------------------------------------------------------------------------
T = 24
np.random.seed(42)
P_load = 250.0
# Sinusoidal load profile
load_profile = P_load + 30.0 * np.sin(np.linspace(0, 2 * np.pi, T))
df_P = pd.DataFrame(load_profile.reshape(T, 1))
df_Q = pd.DataFrame(np.zeros((T, 1)))

# Nondispatchable availability: daytime solar profile
solar = np.clip(80.0 * np.sin(np.linspace(0, np.pi, T)), 0, None)
df_nd = pd.DataFrame({1: solar})

build = build_opf_multistep(
    case, df_P, df_Q, T=T,
    formulation="singlenode_dc",
    storage=[storage], nondispatchable=[nd_unit],
    df_nd=df_nd, delta=1.0,
)
build.solve()
r = extract_results(build)
print(f"[singlenode 24h multistep] status={r['status']}, "
      f"objective={r['objective']:.2f}")
print(f"  Pg range: [{r['Pg'].min():.2f}, {r['Pg'].max():.2f}] MW")
print(f"  SoC range: [{r['soc'].min():.2f}, {r['soc'].max():.2f}] MWh")
print(f"  Total curtailment: {r['curtailment'].sum():.2f} MW")


# ---------------------------------------------------------------------------
# Part 4 — case9, multistep T=3 with varying load
# ---------------------------------------------------------------------------
ppc     = case9()
Pd_base = ppc["bus"][:, 2].copy()
Qd_base = ppc["bus"][:, 3].copy()
scales  = [0.8, 1.0, 1.2]
df_P    = pd.DataFrame(np.outer(scales, Pd_base))
df_Q    = pd.DataFrame(np.outer(scales, Qd_base))

build = build_opf_multistep(
    ppc, df_P, df_Q, T=3, formulation="singlenode_dc",
)
build.solve()
r = extract_results(build)
print(f"[case9 3-step varying load] status={r['status']}")
for t in range(3):
    print(f"  t={t}: Pg={np.round(r['Pg'][t], 2)} MW, p_net={r['p_net'][t]:.4f} MW")
