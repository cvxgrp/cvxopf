"""Build and evaluate the study-local CVXOPF model, without solving."""

from dataclasses import replace

import cvxpy as cp
import numpy as np

from cvxopf._ac_start_mapping import variables_by_name
from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.load import Load, loads_from_matpower
from cvxopf.network import make_branch_admittance, reindex_case_to_consecutive
from cvxopf.problem import OPFOptions, build_opf
from cvxopf.testcases.case9 import case9
from cvxopf.testcases.case9_dcline import case9_dcline

from .contract import (
    ANGLES, CELLS, box_excess, capability, close, coordinates, native_bounds, require,
    selected_links, worst,
)


def make_case(cell):
    require(cell in CELLS, f"unknown cell: {cell}")
    case = case9_dcline()
    case.pop("dclinecost")  # The matched contract is zero link tariff.
    if cell.startswith("smooth"):
        case["gencost"] = case9()["gencost"].copy()
    if cell.endswith("_no_links"):
        case.pop("dcline")
    close(case["baseMVA"], 100, "base MVA")
    close(case["bus"][:, 0], [1, 2, 30, 4, 5, 6, 7, 8, 9], "external bus IDs")
    close(case["gen"][:, 0], [1, 2, 30], "real-generator IDs")
    close(case["bus"][:, 2:4],
          [[0, 0]] * 4 + [[90, 30], [0, 0], [100, 35], [0, 0], [125, 50]],
          "ordinary demand by bus")
    return case


def branch_data(case):
    reindexed, _ = reindex_case_to_consecutive(case)
    adm = make_branch_admittance(reindexed)
    require(np.all(adm.status), "the matched specification requires nine active branches")
    vmax = case["bus"][:, 11]
    f, t = adm.from_bus, adm.to_bus
    uf, ut = vmax[f], vmax[t]
    base = case["baseMVA"]
    bound_f = base * uf * (abs(adm.yff) * uf + abs(adm.yft) * ut)
    bound_t = base * ut * (abs(adm.ytf) * uf + abs(adm.ytt) * ut)
    largest = np.maximum(bound_f, bound_t)
    rating = np.ceil(largest) + 1
    require(np.all((largest < rating) & (rating < 1e10)), "nonredundant branch rating")
    return adm, [
        dict(row=i, from_bus=int(branch[0]), to_bus=int(branch[1]),
             from_bound_mva=bound_f[i], to_bound_mva=bound_t[i], rating_mva=rating[i],
             margin_mva=rating[i] - largest[i])
        for i, branch in enumerate(case["branch"])
    ]


class CvxModel:
    def __init__(self, case):
        self.case = case
        loads = loads_from_matpower(case["bus"])
        active = selected_links(case)
        links = hvdc_from_dcline(case["dcline"]) if active else []
        if links:
            links[-1] = replace(links[-1], p_max_mw=-1e-6)
            loads.append(Load(bus=4, p_load_mw=1., q_load_mvar=0.,
                              device_id="converter_30_to_4_fixed_loss"))
        for (row, dc), link in zip(active.items(), links, strict=True):
            close([link.from_bus, link.to_bus], dc[:2], f"CVXOPF link {row} endpoints")
            close([link.p_min_mw, link.p_max_mw], native_bounds(row, dc),
                  f"CVXOPF link {row} bounds")
            close(link.loss_percent, 100 * dc[16], f"CVXOPF link {row} proportional loss")
            close(link.cost_coeffs, np.zeros(len(link.cost_coeffs)), f"CVXOPF link {row} tariff")
        build = build_opf(
            case, options=OPFOptions(enforce_branch_limits=False, enforce_vset=False),
            loads=loads, hvdc=links, delta=1.,
        )
        data = build.data
        self.ybus = data["Ybus"].toarray() if hasattr(data["Ybus"], "toarray") else data["Ybus"]
        self.adm, self.ratings = branch_data(case)
        extra = []
        theta = build.variables["theta"]
        for _, src, dst, lo, hi in ANGLES:
            f, t = (data["ext_to_int"][bus] for bus in (src, dst))
            diff = theta[f, 0] - theta[t, 0]
            extra.extend([np.deg2rad(lo) <= diff, diff <= np.deg2rad(hi)])
        for family, i, normal, rhs, endpoints in capability(case):
            close([normal @ pt for pt in endpoints], [rhs, rhs], f"{family} endpoints")
            expr = case["baseMVA"] * (
                normal[0] * build.variables["Pg"][i] + normal[1] * build.variables["Qg"][i]
            )
            extra.append(expr <= rhs)
        self.build = replace(build, prob=cp.Problem(build.prob.objective, [
            *build.prob.constraints, *extra,
        ]))
        self.variables = variables_by_name(self.build)
        close(data["baseMVA"], case["baseMVA"], "assembled base MVA")
        close(data["ref"], data["ext_to_int"][1], "assembled reference bus")
        require(not self.build.prob.is_dcp(), "expected the full-physics AC model")
        close(self.variables["v"].bounds[0].ravel(), case["bus"][:, 12], "CVXOPF Vmin")
        close(self.variables["v"].bounds[1].ravel(), case["bus"][:, 11], "CVXOPF Vmax")
        assembled = np.zeros((len(case["bus"]), 2))
        for bus, p, q in zip(
            data["load_bus_external"], build.expressions["p_load_served"].value,
            build.expressions["q_load"].value, strict=True,
        ):
            assembled[data["ext_to_int"][bus]] += [p, q]
        require(not np.any(data["load_is_sheddable"]), "unexpected load shedding")
        expected = case["bus"][:, 2:4].copy()
        expected[data["ext_to_int"][4], 0] += bool(active)
        close(assembled, expected, "CVXOPF explicit demand by bus")
        self.load_by_bus = assembled.tolist()

    def _final_rows(self):
        """Check and return only the constraints actually attached to this problem."""
        rows = self.build.prob.constraints
        active = selected_links(self.case)
        base_count = 15 + (3 if active else 0)
        angle_count = 2 * len(ANGLES)
        face_count = sum(1 for _ in capability(self.case))
        require(len(rows) == base_count + angle_count + face_count,
                "CVXOPF final problem missing or has extra constraint rows")
        equality, inequality = cp.constraints.Equality, cp.constraints.Inequality
        require(all(isinstance(row, equality) for row in rows[:11]),
                "CVXOPF final problem must retain reference, flow and balance equalities")
        box_end = 17 if active else 15
        require(all(isinstance(row, inequality) for row in rows[11:box_end]),
                "CVXOPF final problem must retain device box inequalities")
        if active:
            loss = rows[17]
            require(isinstance(loss, equality) and loss.shape == (len(active),),
                    "CVXOPF final link loss row must be a per-link equality")
        require(all(isinstance(row, inequality) and row.shape == () for row in rows[base_count:]),
                "CVXOPF final angle and generator capability rows must be scalar inequalities")
        return rows[:base_count], rows[base_count:base_count + angle_count], rows[base_count + angle_count:]

    def probe_link_losses(self, point):
        """Test both sides of the final problem's loss equality, link by link."""
        core, _, _ = self._final_rows()
        links = point["links"]
        if not links:
            return []
        loss = core[17]
        p_in, p_out = (self.variables[name] for name in ("p_hvdc_in", "p_hvdc_out"))
        previous = p_in.value, p_out.value
        sent = np.array([link["native_from_mw"] for link in links])
        received = np.array([link["native_to_mw"] for link in links])
        probes = []
        try:
            p_in.save_value(sent)
            p_out.save_value(received)
            close(loss.violation(), np.zeros(len(links)), "CVXOPF link loss baseline violation")
            for i, link in enumerate(links):
                for delta in (-1., 1.):
                    modified = received.copy()
                    modified[i] += delta
                    p_out.save_value(modified)
                    violation = loss.violation()
                    expected = np.zeros(len(links))
                    expected[i] = abs(delta)
                    close(violation, expected, f"CVXOPF link {link['row']} receiving-terminal probe")
                    probes.append({
                        "source_row": link["row"], "receiving_delta_mw": delta,
                        "violation_mw": violation.tolist(),
                    })
        finally:
            p_in.save_value(previous[0])
            p_out.save_value(previous[1])
        return probes

    def evaluate(self, point):
        core, angle_rows, face_rows = self._final_rows()
        theta, vm, pg, qg = coordinates(self.case, point)
        base = self.case["baseMVA"]
        v = vm * np.exp(1j * theta)
        pair = v[:, None] * (self.ybus * v[None, :]).conj()
        f, t = self.adm.from_bus, self.adm.to_bus
        sf = v[f] * (self.adm.yff * v[f] + self.adm.yft * v[t]).conj()
        st = v[t] * (self.adm.ytf * v[f] + self.adm.ytt * v[t]).conj()
        r, c = (self.build.data[k] for k in ("rows", "cols"))
        lifted = {
            "theta": theta[:, None], "v": vm[:, None], "Pg": pg / base, "Qg": qg / base,
            "P_vec": pair[r, c].real, "Q_vec": pair[r, c].imag,
            "p": pair.sum(axis=1).real, "q": pair.sum(axis=1).imag,
            "branch_p_from_pu": sf.real, "branch_q_from_pu": sf.imag,
            "branch_p_to_pu": st.real, "branch_q_to_pu": st.imag,
        }
        if point["links"]:
            lifted["p_hvdc_in"] = np.array([lnk["native_from_mw"] for lnk in point["links"]])
            lifted["p_hvdc_out"] = np.array([lnk["native_to_mw"] for lnk in point["links"]])
        require(lifted.keys() == self.variables.keys(),
                f"unmapped CVXOPF coordinates: {lifted.keys() ^ self.variables.keys()}")
        for name, variable in self.variables.items():
            value = lifted[name]
            close(value.shape, variable.shape, f"lift shape for {name}")
            # Negative controls must reach the actual rows, not be clipped by the setter.
            variable.save_value(value.copy())
        rows = [np.asarray(row.expr.value).ravel() for row in core]
        close(rows[0], theta[[point["bus_ids"].index(1)]], "CVXOPF reference row")
        for index in range(1, 9):
            close(rows[index], np.zeros_like(rows[index]), f"CVXOPF lifted definition {index}")
        close(base * np.vstack(rows[11:15]), np.vstack([
            self.case["gen"][:, 9] - pg, pg - self.case["gen"][:, 8],
            self.case["gen"][:, 4] - qg, qg - self.case["gen"][:, 3],
        ]), "CVXOPF generator box rows")
        if point["links"]:
            link_data = selected_links(self.case)
            bounds = np.array([native_bounds(row, dc) for row, dc in link_data.items()])
            close(np.vstack(rows[15:17]), np.vstack([
                bounds[:, 0] - lifted["p_hvdc_in"], lifted["p_hvdc_in"] - bounds[:, 1],
            ]), "CVXOPF link box rows")
            close(rows[17], np.zeros(len(link_data)), "CVXOPF link proportional rows")
        angles = np.array([row.expr.value for row in angle_rows])
        faces = [float(row.expr.value) for row in face_rows]
        vmin, vmax = self.variables["v"].bounds
        gen_buses = self.case["gen"][:, 0].astype(int).tolist()
        report = {
            "p_mw": (base * rows[9]).tolist(), "q_mvar": (base * rows[10]).tolist(),
            "angle_rad": angles.tolist(), "capability": faces,
            "objective": float(self.build.prob.objective.value),
            "constraint_inventory": [
                {"type": row.__class__.__name__, "shape": row.shape,
                 "variables": sorted(v.name() for v in row.variables())}
                for row in self.build.prob.constraints
            ],
            "worst": {
                "real_balance": worst(base * rows[9], point["bus_ids"], "MW"),
                "reactive_balance": worst(base * rows[10], point["bus_ids"], "MVAr"),
                "voltage_box": worst(box_excess(vmin.ravel(), vmax.ravel(), vm),
                                     point["bus_ids"], "p.u."),
                "angle_box": worst(np.maximum(0, np.rad2deg(angles)),
                                   [f"{row}:{side}" for row, *_ in ANGLES
                                    for side in ("lower", "upper")], "degrees"),
                "generator_p_box": worst(base * np.maximum(0, np.maximum(rows[11], rows[12])),
                                         gen_buses, "MW"),
                "generator_q_box": worst(base * np.maximum(0, np.maximum(rows[13], rows[14])),
                                         gen_buses, "MVAr"),
                "capability": worst(np.maximum(0, faces), [
                    f"{family}:bus{gen_buses[i]}" for family, i, *_ in capability(self.case)
                ], "MW/MVAr projection"),
            },
        }
        if point["links"]:
            report["worst"]["link_from_box"] = worst(
                np.maximum(0, np.maximum(rows[15], rows[16])), list(link_data), "MW",
            )
            report["worst"]["link_proportional"] = worst(rows[17], list(link_data), "MW")
        return report
