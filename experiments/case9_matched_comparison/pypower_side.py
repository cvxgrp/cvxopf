"""Isolated PYPOWER build and callback probe. The PIPS optimizer is never called."""

import importlib.metadata
import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
from pypower import pipsopf_solver
from pypower.add_userfcn import add_userfcn
from pypower.ext2int import ext2int
from pypower.makeYbus import makeYbus
from pypower.opf_setup import opf_setup
from pypower.ppoption import ppoption
from scipy.sparse import eye, hstack, vstack

from scripts.generate_pypower_fixtures import _dcline_to_gens, _make_coupling_userfcn

from .contract import (
    ANGLES, PYPINS, box_excess, capability, close, coordinates, cost_close, decode_case, generator_cost,
    native_bounds, native_from_net, require, selected_links, validate_cell, worst,
)


class ProbeComplete(Exception):
    """Exit the upstream driver at the optimizer boundary."""


def versions():
    actual = {name: importlib.metadata.version(name) for name in PYPINS}
    require(actual == PYPINS, f"wrong PYPOWER environment: {actual}")
    return actual


def verify_callbacks(f, x, A, lower, upper, xmin, xmax, gh, hess, options, nb, nl):
    """Use the pinned PIPS initialization's exact linear-inequality convention."""
    nx = len(x)
    AA = vstack([eye(nx, format="csr"), A], format="csr")
    lo, hi = np.r_[xmin, lower], np.r_[xmax, upper]
    # PIPS uses absolute endpoints < 1e10 and machine epsilon for equalities.
    with np.errstate(invalid="ignore"):
        width = abs(hi - lo)
    eq = width <= np.finfo(float).eps
    gt = (hi >= 1e10) & (lo > -1e10)
    lt = (lo <= -1e10) & (hi < 1e10)
    box = (width > np.finfo(float).eps) & (hi < 1e10) & (lo > -1e10)
    Ai = vstack([AA[lt], -AA[gt], AA[box], -AA[box]], format="csr")
    bi = np.r_[hi[lt], -lo[gt], hi[box], -lo[box]]
    Ae, be = AA[eq], hi[eq]
    h, g, dh, dg = gh(x)
    require(h.shape == (2 * nl,) and g.shape == (2 * nb,),
            f"PIPS callback residual dimensions: h={h.shape}, g={g.shape}")
    require(dh.shape == (nx, 2 * nl) and dg.shape == (nx, 2 * nb),
            "PIPS callback Jacobian dimensions")
    joint_h = np.r_[h, Ai @ x - bi]
    joint_g = np.r_[g, Ae @ x - be]
    joint_dh, joint_dg = hstack([dh, Ai.T]), hstack([dg, Ae.T])
    require(joint_dh.shape == (nx, len(joint_h)) and joint_dg.shape == (nx, len(joint_g)),
            "PIPS startup concatenation/stacking dimensions")
    objective, gradient = f(x)
    require(all(np.isfinite(v).all() for v in
                [joint_h, joint_g, joint_dh.data, joint_dg.data, objective, gradient]),
            "nonfinite PIPS startup objective or constraints")
    lmbda = {"eqnonlin": np.linspace(.5, 1., len(g)),
             "ineqnonlin": np.linspace(.25, .75, len(h))}
    H = hess(x, lmbda, options["cost_mult"])
    require(H.shape == (nx, nx) and np.isfinite(H.data).all(), "PIPS Hessian is not finite/square")
    close(H.toarray(), H.T.toarray(), "PIPS Hessian symmetry", atol=1e-8)
    return {"x0": x.tolist(), "options": options, "nonlinear_h": h.shape,
            "nonlinear_g": g.shape, "joined_dh": joint_dh.shape,
            "joined_dg": joint_dg.shape, "hessian": H.shape}


class PypModel:
    def __init__(self, case, ratings, *, historical=False):
        self.case = case
        self.links = selected_links(case)
        self.ng = len(case["gen"])
        nd = len(self.links)
        ppc = _dcline_to_gens(case) if nd else dict(case)
        ppc["bus"] = case["bus"].copy()  # Preserve all original bus types, including bus 30.
        if historical:
            terminals = np.asarray([dc[:2] for dc in self.links.values()]).ravel()
            ppc["bus"][np.isin(ppc["bus"][:, 0], terminals) & (ppc["bus"][:, 1] != 3), 1] = 1
        ppc["branch"] = np.pad(case["branch"], ((0, 0), (0, 4)))
        ppc["branch"][:, 5] = ratings
        if nd:
            ppc["gen"][self.ng:, [2, 3, 4]] = 0  # No converter Q or voltage control.
            add_userfcn(ppc, "formulation", _make_coupling_userfcn(case))
        options = ppoption(VERBOSE=0, OUT_ALL=0, OPF_ALG=560, OPF_FLOW_LIM=0)
        om = opf_setup(ext2int(ppc), options)
        om.build_cost_params()
        self.ppc = internal = om.get_ppc()
        self.vv, self.ll, nn, _ = om.get_idx()
        self.var_order = om.var["order"]
        self.lin_order = om.lin["order"]
        order = internal["order"]
        self.nb = len(case["bus"])
        self.ids = np.asarray(order["bus"]["i2e"], dtype=int)
        self.gen_cols = np.argsort(order["gen"]["e2i"])
        close(self.gen_cols, order["gen"]["i2e"], "independent generator inversion")
        self.bus_cols = np.array([np.flatnonzero(self.ids == row[0])[0] for row in case["bus"]])
        self.branch_rows = order["branch"]["status"]["on"]
        close(self.branch_rows, np.arange(len(ratings)), "branch source order")
        close(internal["bus"][self.bus_cols, 1:13], ppc["bus"][:, 1:13],
              "assembled PYP bus attributes")
        close(internal["branch"][:, 2:13], ppc["branch"][:, 2:13], "assembled PYP branch attributes")
        close(self.ids[internal["branch"][:, :2].astype(int)], case["branch"][:, :2],
              "assembled PYP branch bus IDs")
        close(internal["baseMVA"], case["baseMVA"], "assembled PYP base MVA")
        self.base = case["baseMVA"]
        self.ybus, self.yf, self.yt = makeYbus(self.base, internal["bus"], internal["branch"])
        close(self.ids[internal["gen"][self.gen_cols, 0].astype(int)],
              np.r_[case["gen"][:, 0], [dc[0] for dc in self.links.values()],
                    [dc[1] for dc in self.links.values()]], "assembled generator bus identities")
        close(internal["gen"][self.gen_cols[:self.ng], 1:], case["gen"][:, 1:],
              "assembled real-generator attributes")
        close(internal["gencost"][self.gen_cols[:self.ng]], case["gencost"], "real gencost rows")
        if nd:
            dummies = internal["gencost"][self.gen_cols[self.ng:]]
            close(dummies[:, 0], np.full(2 * nd, 2), "dummy cost models")
            close(dummies[:, 4:], np.zeros_like(dummies[:, 4:]), "zero dummy costs")
        self.A, self.lower, self.upper = om.linear_constraints()
        _, self.xmin, self.xmax = om.getv()
        self.check_rows()
        require(om.getN("lin", "vl") == 0, "unexpected constant-power-factor row")
        require(set(nn["N"]) == {"Pmis", "Qmis", "Sf", "St"}, "unexpected nonlinear rows")
        require({key for key in self.lin_order if om.getN("lin", key)} ==
                ({"PQh", "PQl", "ang"} | ({"ycon"} if "y" in self.var_order else set())
                 | ({"dcline"} if nd else set())), "unexpected active PYP linear row family")

        def capture(f, x, A, lower, upper, xmin, xmax, gh, hess, opt):
            self.f, self.gh, self.x0 = f, gh, x.copy()
            self.callbacks = verify_callbacks(f, x, A, lower, upper, xmin, xmax, gh, hess, opt,
                                              self.nb, len(ratings))
            raise ProbeComplete

        with patch.object(pipsopf_solver, "pips", capture):
            try:
                pipsopf_solver.pipsopf_solver(om, options)
            except ProbeComplete:
                pass
            else:
                raise RuntimeError("did not reach the intercepted PIPS boundary")

    def indices(self, group, block):
        return np.arange(group["i1"][block], group["iN"][block])

    def cols(self, block):
        return self.indices(self.vv, block)

    def check_rows(self):
        case, A, lo, hi, base = self.case, self.A, self.lower, self.upper, self.base
        va, vm, pg, qg = (self.cols(name) for name in ("Va", "Vm", "Pg", "Qg"))
        close(self.xmin[vm[self.bus_cols]], case["bus"][:, 12], "PYP Vmin")
        close(self.xmax[vm[self.bus_cols]], case["bus"][:, 11], "PYP Vmax")
        ref = va[np.flatnonzero(self.ids == 1)[0]]
        others = va[va != ref]
        close([self.xmin[ref], self.xmax[ref]], [0, 0], "PYP reference angle")
        require(np.isneginf(self.xmin[others]).all() and np.isposinf(self.xmax[others]).all(),
                "unexpected absolute angle limits")
        for cols, mincol, maxcol, name in ((pg, 9, 8, "P"), (qg, 4, 3, "Q")):
            real = cols[self.gen_cols[:self.ng]]
            close(base * self.xmin[real], case["gen"][:, mincol], f"PYP real {name} lower")
            close(base * self.xmax[real], case["gen"][:, maxcol], f"PYP real {name} upper")

        angle_rows = self.indices(self.ll, "ang")
        close(len(angle_rows), len(ANGLES), "active angle row count")
        for i, (_, f, t, lower, upper) in zip(angle_rows, ANGLES, strict=True):
            expected = np.zeros(A.shape[1])
            expected[va[self.ids == f]] = 1
            expected[va[self.ids == t]] = -1
            close(A[i].toarray().ravel(), expected, f"PYP angle coefficients {f}->{t}")
            close(np.rad2deg([lo[i], hi[i]]), [lower, upper], f"PYP angle bounds {f}->{t}")

        for family in ("PQh", "PQl"):
            faces = [face for face in capability(case) if face[0] == family]
            for r, (_, idx, normal, rhs, _) in zip(self.indices(self.ll, family), faces, strict=True):
                col = self.gen_cols[idx]
                expected = np.zeros(A.shape[1])
                expected[[pg[col], qg[col]]] = normal
                close(A[r].toarray().ravel(), expected, f"PYP {family} coefficients row {idx}")
                close(base * hi[r], rhs, f"PYP {family} bound row {idx}")
                require(np.isneginf(lo[r]), f"unexpected PYP {family} lower bound")

        nd = len(self.links)
        if nd:
            for k, (r, (src, dc)) in enumerate(zip(self.indices(self.ll, "dcline"),
                                                   self.links.items(), strict=True)):
                f, t = self.gen_cols[[self.ng + k, self.ng + nd + k]]
                expected = np.zeros(A.shape[1])
                expected[[pg[f], pg[t]]] = [1 - dc[16], 1]
                close(A[r].toarray().ravel(), expected, f"PYP link {src} coupling")
                close(base * np.array([lo[r], hi[r]]), [-dc[15]] * 2, f"PYP link {src} RHS")
                close(base * np.array([self.xmin[pg[f]], self.xmax[pg[f]]]),
                      native_bounds(src, dc), f"PYP link {src} effective from box")
                require(np.isneginf(self.xmin[pg[t]]) and np.isposinf(self.xmax[pg[t]]),
                        f"PYP link {src} unexpected to-terminal box")
                close(np.r_[self.xmin[qg[[f, t]]], self.xmax[qg[[f, t]]]], np.zeros(4),
                      f"PYP link {src} unity power factor")

    def dispatch(self, theta, vm, pg, qg, terminals):
        x = self.x0.copy()
        links = np.asarray(terminals).reshape(-1, 2).T
        full_pg = np.r_[pg, *links]
        full_qg = np.r_[qg, np.zeros(2 * len(self.links))]
        for name, indices, vals in (
            ("Va", self.bus_cols, theta), ("Vm", self.bus_cols, vm),
            ("Pg", self.gen_cols, full_pg / self.base), ("Qg", self.gen_cols, full_qg / self.base),
        ):
            x[self.cols(name)[indices]] = vals
        if "y" in self.var_order:
            costs = self.ppc["gencost"]
            x[self.cols("y")] = [generator_cost(cost, p) for cost, p in zip(
                costs, full_pg[self.ppc["order"]["gen"]["e2i"]], strict=True) if cost[0] == 1]
        return x

    def evaluate(self, point):
        theta, vm, pg, qg = coordinates(self.case, point)
        x = self.dispatch(theta, vm, pg, qg,
                          [(link["net_from_mw"], link["net_to_mw"]) for link in point["links"]])
        h, g, _, _ = self.gh(x)
        p, q = (self.base * chunk[self.bus_cols] for chunk in np.split(g, 2))
        ax = self.A @ x
        angle_rows = self.indices(self.ll, "ang")
        angles = np.array([self.lower[angle_rows] - ax[angle_rows],
                           ax[angle_rows] - self.upper[angle_rows]]).T.ravel()
        faces = self.base * np.concatenate([
            (ax - self.upper)[self.indices(self.ll, key)] for key in ("PQh", "PQl")
        ])
        floors = self.check_pwl_floors(x) if "y" in self.var_order else []
        if self.links:
            rows = self.indices(self.ll, "dcline")
            close(self.base * (ax - self.lower)[rows], np.zeros(len(self.links)),
                  "PYP converter rows on mapped point")
        require(np.max(h) < 0 or np.any(vm > self.case["bus"][:, 11]),
                "PYP supposedly redundant MVA row restricts an in-voltage-box point")
        va_cols, vm_cols = (self.cols(key)[self.bus_cols] for key in ("Va", "Vm"))
        pg_cols, qg_cols = (self.cols(key)[self.gen_cols] for key in ("Pg", "Qg"))
        gen_buses = self.case["gen"][:, 0].astype(int).tolist()
        report = {
            "p_mw": p.tolist(), "q_mvar": q.tolist(), "angle_rad": angles.tolist(),
            "capability": faces.tolist(), "objective": float(self.f(x)[0]),
            "pwl_floors": floors,
            "worst": {
                "real_balance": worst(p, point["bus_ids"], "MW"),
                "reactive_balance": worst(q, point["bus_ids"], "MVAr"),
                "voltage_box": worst(box_excess(self.xmin[vm_cols], self.xmax[vm_cols], x[vm_cols]),
                                     point["bus_ids"], "p.u."),
                "angle_box": worst(np.maximum(0, np.rad2deg(angles)),
                                   [f"{row}:{side}" for row, *_ in ANGLES
                                    for side in ("lower", "upper")], "degrees"),
                "reference_angle": worst(np.rad2deg(
                    box_excess(self.xmin[va_cols], self.xmax[va_cols], x[va_cols])
                ), point["bus_ids"], "degrees"),
                "capability": worst(np.maximum(0, faces), [
                    f"{family}:bus{gen_buses[i]}" for family, i, *_ in capability(self.case)
                ], "MW/MVAr projection"),
            },
        }
        for key, units, cols in (("generator_p_box", "MW", pg_cols), ("generator_q_box", "MVAr", qg_cols)):
            real = cols[:self.ng]
            report["worst"][key] = worst(
                self.base * box_excess(self.xmin[real], self.xmax[real], x[real]), gen_buses, units,
            )
        if self.links:
            nd = len(self.links)
            from_cols = pg_cols[self.ng:self.ng + nd]
            to_cols = pg_cols[self.ng + nd:]
            report["native_link_roundtrip_mw"] = [
                native_from_net(float(self.base * x[f]), float(self.base * x[t]), float(dc[15]))
                for f, t, dc in zip(from_cols, to_cols, self.links.values(), strict=True)
            ]
            report["worst"]["link_from_box"] = worst(
                self.base * box_excess(self.xmin[from_cols], self.xmax[from_cols], x[from_cols]),
                list(self.links), "MW",
            )
            report["worst"]["link_coupling"] = worst(
                self.base * (ax - self.lower)[rows], list(self.links), "MW",
            )
        return report

    def check_pwl_floors(self, x):
        """Recover the minimum feasible y for each generator from actual rows."""
        y = self.cols("y")
        rows = self.indices(self.ll, "ycon")
        coeff = self.A[rows, :][:, y].toarray()
        require(np.all(np.count_nonzero(coeff, axis=1) == 1) and np.all(coeff <= 0),
                "unexpected PYP ycon structure")
        require(np.isneginf(self.lower[rows]).all() and np.isfinite(self.upper[rows]).all(),
                "unexpected PYP ycon bounds")
        remaining = np.setdiff1d(np.arange(self.A.shape[0]), rows)
        require(not self.A[remaining, :][:, y].count_nonzero()
                and np.isneginf(self.xmin[y]).all() and np.isposinf(self.xmax[y]).all(),
                "unexpected additional PYP epigraph limits")
        # With y removed, each a*y + rest <= upper row gives a lower bound.
        rest = self.A[rows, :] @ x - coeff @ x[y]
        internal_gens = np.flatnonzero(self.ppc["gencost"][:, 0] == 1)
        result = []
        for k, (column, internal_gen) in enumerate(zip(y, internal_gens, strict=True)):
            participating = coeff[:, k] < 0
            require(participating.any(), "missing PYP ycon rows for a PWL generator")
            floor = float(max((self.upper[rows][participating] - rest[participating])
                              / coeff[participating, k]))
            external_row = int(self.ppc["order"]["gen"]["e2i"][internal_gen])
            cost_close(floor, x[column], f"PYP minimum PWL cost for generator row {external_row}")
            result.append({"gen_row": external_row, "minimum_cost": floor})
        return result

    def network(self):
        sel = np.ix_(self.bus_cols, self.bus_cols)
        matrices = {"ybus": self.ybus.toarray()[sel], "yf": self.yf.toarray()[:, self.bus_cols],
                    "yt": self.yt.toarray()[:, self.bus_cols]}
        return {k: {"real": v.real.tolist(), "imag": v.imag.tolist()} for k, v in matrices.items()}


def run(request):
    actual = versions()
    case = decode_case(request["case"])
    validate_cell(request["cell"], case)
    model = PypModel(case, [r["rating_mva"] for r in request["ratings"]])
    response = {
        "versions": actual, "python": sys.version, "network": model.network(),
        "callbacks": model.callbacks,
        "points": [model.evaluate(point) for point in request["points"]],
        "linear_rows": {key: model.indices(model.ll, key).tolist() for key in model.lin_order},
    }
    if "historical" in request:
        response["historical"] = replay_history(case, request["historical"])
    return response


def replay_history(case, history):
    # Historical neutralized target: uniform 1e5 MVA rows, terminal buses set
    # to PQ, zero converter Q, signed dummy coupling with fixed loss.
    model = PypModel(case, [1e5] * len(case["branch"]), historical=True)
    pstar, cstar = (history[f"{name}_full.json"] for name in ("pstar", "cstar"))
    ids = list(case["bus"][:, 0])
    close(pstar["bus_ids"], ids, "P* archive bus order")
    close(pstar["gen_bus"], np.r_[
        case["gen"][:, 0], [dc[0] for dc in model.links.values()],
        [dc[1] for dc in model.links.values()],
    ], "P* archive generator order")
    close(pstar["dummy_Qg"], np.zeros(6), "P* archived dummy Q")
    x = model.dispatch(
        np.deg2rad(pstar["Va_deg"]), pstar["Vm"], pstar["real_Pg"], pstar["real_Qg"],
        list(zip(pstar["from_dummy_Pg"], pstar["to_dummy_Pg"], strict=True)),
    )
    h, g, _, _ = model.gh(x)
    ax = model.A @ x
    def maximum(lo, hi, value):
        return float(np.max(box_excess(lo, hi, value)))
    mw = model.base * max(
        np.max(abs(g)),
        max(maximum(model.xmin[i], model.xmax[i], x[i])
            for i in (model.cols("Pg"), model.cols("Qg"))),
        max(maximum(model.lower[i], model.upper[i], ax[i])
            for i in (model.indices(model.ll, name) for name in ("PQh", "PQl", "dcline"))),
    )
    angle = model.indices(model.ll, "ang")
    va = model.cols("Va")
    vm = model.cols("Vm")
    ycon = model.indices(model.ll, "ycon")
    # Keep the original historical forward-flow reading; do not repair or
    # silently re-optimize C*. Only its bus-9 and angle rejections are tested.
    order = [cstar["ext_to_int"][str(int(bus))] for bus in ids]
    xc = model.dispatch(
        np.deg2rad(np.asarray(cstar["Va_deg"])[order]), np.asarray(cstar["Vm"])[order],
        cstar["Pg"], cstar["Qg"],
        list(zip(-np.asarray(cstar["p_hvdc_in"]), -np.asarray(cstar["p_hvdc_out"]), strict=True)),
    )
    _, gc, _, _ = model.gh(xc)
    c_angle = (model.A @ xc)[angle[1]]
    return {
        "pstar": {
            "objective_delta": float(model.f(x)[0] - pstar["obj"]),
            "largest_mw_or_mvar_violation": float(mw),
            "largest_voltage_pu_violation": maximum(model.xmin[vm], model.xmax[vm], x[vm]),
            "largest_angle_deg_violation": float(np.rad2deg(
                maximum(model.lower[angle], model.upper[angle], ax[angle])
            )),
            "reference_angle_deg_violation": float(np.rad2deg(
                maximum(model.xmin[va], model.xmax[va], x[va])
            )),
            "largest_mva_violation": max(0., float(np.sqrt(max(h) * model.base**2 + 1e10) - 1e5)),
            "largest_epigraph_violation": maximum(model.lower[ycon], model.upper[ycon], ax[ycon]),
        },
        "cstar": {
            "bus9_mw": float(model.base * gc[model.bus_cols[ids.index(9)]]),
            "angle_floor_violation_deg": float(np.rad2deg(model.lower[angle[1]] - c_angle)),
        },
    }


if __name__ == "__main__":
    with Path(sys.argv[1]).open() as stream:
        request = json.load(stream)
    print(json.dumps(run(request), allow_nan=False))
