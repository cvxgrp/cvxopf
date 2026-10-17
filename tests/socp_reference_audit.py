"""Independent SOCP reference physics from original tables, not cvxopf maps.

This intentionally reconstructs complex branch coefficients and nodal sums
locally as an independent test oracle. It builds no optimization expressions.
"""

import numpy as np


def edge_violations(w_from, w_to, re, im):
    """Maximum norm-cone violation (pu^2) and determinant violation (pu^4)."""
    norm = np.sqrt((2*re)**2 + (2*im)**2 + (w_from-w_to)**2)
    soc = np.maximum(0., norm-(w_from+w_to))
    determinant = np.maximum(0., np.abs(re+1j*im)**2-w_from*w_to)
    return float(soc.max(initial=0.)), float(determinant.max(initial=0.))


def audit(case, primal, *, enforce_vset=False):
    bus = np.asarray(case["bus"], dtype=float)
    gen = np.asarray(case["gen"], dtype=float)
    branch = np.asarray(case["branch"], dtype=float)
    costs = np.asarray(case["gencost"], dtype=float)
    base = case["baseMVA"]
    ids = bus[:, 0].astype(int).tolist()
    assert primal["bus_ids"] == ids
    pos = {external: i for i, external in enumerate(ids)}
    pairs = [tuple(pair) for pair in primal["voltage_product_pairs"]]
    expected = sorted({tuple(sorted((int(row[0]), int(row[1]))))
                       for row in branch if row[10] and row[0] != row[1]})
    assert pairs == expected
    w = np.asarray(primal["w"], dtype=float)
    re = np.asarray(primal["W_re"], dtype=float)
    im = np.asarray(primal["W_im"], dtype=float)
    pg = np.asarray(primal["Pg"], dtype=float)
    qg = np.asarray(primal["Qg"], dtype=float)
    assert w.shape == (len(bus),) and re.shape == im.shape == (len(pairs),)
    assert pg.shape == qg.shape == (len(gen),)
    assert all(np.all(np.isfinite(a)) for a in (w, re, im, pg, qg))
    products = dict(zip(pairs, re + 1j*im))
    nodal = (bus[:, 4] - 1j*bus[:, 5])*w  # MW/MVAr shunt withdrawal
    sf, st = np.zeros(len(branch), complex), np.zeros(len(branch), complex)
    for k, row in enumerate(branch):
        if not row[10]:
            continue
        f, t = int(row[0]), int(row[1])
        i, j = pos[f], pos[t]
        y = 1/complex(row[2], row[3])
        tap = (row[8] or 1)*np.exp(1j*np.deg2rad(row[9]))
        yff = (y + .5j*row[4])/abs(tap)**2
        yft, ytf = -y/tap.conjugate(), -y/tap
        ytt = y + .5j*row[4]
        z = w[i] if f == t else products[tuple(sorted((f, t)))]
        if f > t:
            z = np.conj(z)
        sf[k] = base*(np.conj(yff)*w[i] + np.conj(yft)*z)
        st[k] = base*(np.conj(ytt)*w[j] + np.conj(ytf)*np.conj(z))
        nodal[i] += sf[k]
        nodal[j] += st[k]
    injections = -(bus[:, 2] + 1j*bus[:, 3])
    for k, row in enumerate(gen):
        if row[7]:
            injections[pos[int(row[0])]] += pg[k] + 1j*qg[k]
        else:
            assert abs(pg[k]) <= 1e-4 and abs(qg[k]) <= 1e-4
    power_error = max(np.max(abs((nodal-injections).real)), np.max(abs((nodal-injections).imag)))
    assert power_error <= 1e-4, ("balance", power_error)
    voltage_violation = max(0., np.max(bus[:, 12]**2-w), np.max(w-bus[:, 11]**2))
    assert voltage_violation <= 1e-6
    if enforce_vset:
        selected = set()
        for row in gen:
            i = pos[int(row[0])]
            if row[7] and bus[i, 1] in (2, 3) and i not in selected:
                assert abs(w[i]-row[5]**2) <= 1e-6
                selected.add(i)
    on = gen[:, 7] != 0
    assert np.all(pg[on] >= gen[on, 9]-1e-4) and np.all(pg[on] <= gen[on, 8]+1e-4)
    assert np.all(qg[on] >= gen[on, 4]-1e-4) and np.all(qg[on] <= gen[on, 3]+1e-4)
    w_from = np.array([w[pos[i]] for i, _ in pairs])
    w_to = np.array([w[pos[j]] for _, j in pairs])
    cone_violation, determinant_violation = edge_violations(w_from, w_to, re, im)
    assert cone_violation <= 1e-6
    rated = (branch[:, 10] != 0) & (branch[:, 5] > 0) & np.isfinite(branch[:, 5])
    assert np.all(abs(sf[rated]) <= branch[rated, 5]+1e-4)
    assert np.all(abs(st[rated]) <= branch[rated, 5]+1e-4)
    for key, actual in (("branch_p_from", sf.real), ("branch_q_from", sf.imag),
                        ("branch_p_to", st.real), ("branch_q_to", st.imag)):
        np.testing.assert_allclose(primal[key], actual, rtol=0, atol=1e-4)
    assert np.all(costs[:, 0] == 2) and np.all(costs[:, 3] == 3)
    objective = float(np.sum(costs[on, 4]*pg[on]**2 + costs[on, 5]*pg[on] + costs[on, 6]))
    return dict(objective=objective, max_balance_mva=float(power_error),
                max_cone_violation=float(cone_violation),
                max_determinant_violation=float(determinant_violation),
                branch_loss_mw=float(np.sum((sf+st).real)),
                shunt_consumption_mw=float(np.sum(bus[:, 4]*w)))
