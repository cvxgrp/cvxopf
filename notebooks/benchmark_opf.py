import marimo

__generated_with = "0.23.13"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import time

    import numpy as np
    import pandas as pd
    import matplotlib.pyplot as plt

    from cvxopf.problem import OPFOptions, build_opf
    from cvxopf.testcases import case9, case14, case30, case39, case57, case118

    return (
        OPFOptions,
        build_opf,
        case118,
        case14,
        case30,
        case39,
        case57,
        case9,
        mo,
        np,
        pd,
        plt,
        time,
    )


@app.cell
def _(mo):
    intro = mo.md(
        """
    # OPF runtime benchmark: AC sparse vs AC dense vs lossy DC

    Click **Run benchmark** to solve all three formulation variants on all
    included test cases and plot runtime (seconds) vs bus size.

    - Select number of repetitions and choose to discard the first run per case, below
    - AC sparse: DNLP + IPOPT, P/Q as flat (nnz,) variables (`sparse_pq=True`, default)
    - AC dense:  DNLP + IPOPT, P/Q as dense (nb,nb) variables (`sparse_pq=False`)
    - DC: lossy DC QP + CLARABEL (convex)

    **Note:** AC sparse construction is currently slower than dense due to a scalar
    constraint loop workaround for
    [cvxpy#3442](https://github.com/cvxpy/cvxpy/issues/3442). IPOPT internal solve
    time already favours sparse. The vectorised path will be enabled once #3442 is
    patched.
    """
    )
    intro
    return


@app.cell
def _(mo):
    repeats_slider = mo.ui.slider(
        start=1, stop=10, step=1, value=6, label="Repeats per case"
    )
    discard_warmup_checkbox = mo.ui.checkbox(
        value=True, label="Discard first timed run (per case)"
    )
    mo.hstack([repeats_slider, discard_warmup_checkbox])
    return discard_warmup_checkbox, repeats_slider


@app.cell
def _(mo):
    run_button = mo.ui.run_button(label="Run benchmark")
    run_button
    return (run_button,)


@app.cell
def _(OPFOptions):
    options_ac_sparse = OPFOptions(
        enforce_vset=False,
        sparsity_tol=0.0,
        init_flat=True,
        enforce_branch_limits=False,
        sparse_pq=True,
    )
    options_ac_dense = OPFOptions(
        enforce_vset=False,
        sparsity_tol=0.0,
        init_flat=True,
        enforce_branch_limits=False,
        sparse_pq=False,
    )
    options_dc = OPFOptions(
        loss_weight=1.0,
    )
    return options_ac_dense, options_ac_sparse, options_dc


@app.cell
def _(case118, case14, case30, case39, case57, case9):
    cases = [
        ("case9",   case9()),
        ("case14",  case14()),
        ("case30",  case30()),
        ("case39",  case39()),
        ("case57",  case57()),
        ("case118", case118()),
    ]
    return (cases,)


@app.cell
def _(
    build_opf,
    cases,
    discard_warmup_checkbox,
    mo,
    options_ac_dense,
    options_ac_sparse,
    options_dc,
    pd,
    repeats_slider,
    run_button,
    time,
):
    is_script_mode = mo.app_meta().mode == "script"
    should_run = is_script_mode or run_button.value

    _repeats = int(repeats_slider.value)
    _discard_first = bool(discard_warmup_checkbox.value)

    def _solve_once(_formulation: str, _opts, _case_dict: dict) -> str:
        _build = build_opf(_case_dict, formulation=_formulation, options=_opts)
        _build.solve()
        return str(_build.prob.status)

    def _time_once(_formulation: str, _opts, _case_dict: dict) -> tuple[str, float]:
        _build = build_opf(_case_dict, formulation=_formulation, options=_opts)
        _t0 = time.perf_counter()
        _build.solve()
        _dt = time.perf_counter() - _t0
        return str(_build.prob.status), float(_dt)

    rows = []
    if should_run:
        _n_cases = len(cases)
        _n_warmup_solves = 3
        _n_timed_solves  = 3 * _repeats * _n_cases
        _total_solves    = _n_warmup_solves + _n_timed_solves

        with mo.status.progress_bar(total=_total_solves) as _bar:
            # Global warm-up: one solve of each variant on the smallest case.
            _warmup_case = cases[0][1]
            _ = _solve_once("ac",       options_ac_sparse, _warmup_case)
            _bar.update(1)
            _ = _solve_once("ac",       options_ac_dense,  _warmup_case)
            _bar.update(1)
            _ = _solve_once("lossy_dc", options_dc,        _warmup_case)
            _bar.update(1)

            for _case_name, _case_dict in cases:
                _nb = int(_case_dict["bus"].shape[0])

                _ac_sparse_times = []
                _ac_dense_times  = []
                _dc_times        = []
                _ac_sparse_status = None
                _ac_dense_status  = None
                _dc_status        = None

                for _rep in range(_repeats):
                    _ac_sparse_status, _ac_sparse_dt = _time_once(
                        "ac", options_ac_sparse, _case_dict)
                    _bar.update(1)
                    _ac_dense_status, _ac_dense_dt = _time_once(
                        "ac", options_ac_dense, _case_dict)
                    _bar.update(1)
                    _dc_status, _dc_dt = _time_once(
                        "lossy_dc", options_dc, _case_dict)
                    _bar.update(1)

                    _ac_sparse_times.append(_ac_sparse_dt)
                    _ac_dense_times.append(_ac_dense_dt)
                    _dc_times.append(_dc_dt)

                if _discard_first and _repeats >= 2:
                    _ac_sparse_times = _ac_sparse_times[1:]
                    _ac_dense_times  = _ac_dense_times[1:]
                    _dc_times        = _dc_times[1:]

                _ac_sparse_arr = pd.Series(_ac_sparse_times, dtype=float)
                _ac_dense_arr  = pd.Series(_ac_dense_times,  dtype=float)
                _dc_arr        = pd.Series(_dc_times,        dtype=float)

                rows.append(
                    dict(
                        case=_case_name,
                        nb=_nb,
                        repeats=len(_ac_sparse_times),
                        ac_sparse_status=_ac_sparse_status,
                        ac_sparse_time_mean_s=float(_ac_sparse_arr.mean()),
                        ac_sparse_time_std_s=float(_ac_sparse_arr.std(ddof=1))
                            if len(_ac_sparse_times) >= 2 else 0.0,
                        ac_dense_status=_ac_dense_status,
                        ac_dense_time_mean_s=float(_ac_dense_arr.mean()),
                        ac_dense_time_std_s=float(_ac_dense_arr.std(ddof=1))
                            if len(_ac_dense_times) >= 2 else 0.0,
                        dc_status=_dc_status,
                        dc_time_mean_s=float(_dc_arr.mean()),
                        dc_time_std_s=float(_dc_arr.std(ddof=1))
                            if len(_dc_times) >= 2 else 0.0,
                    )
                )

    results_df = (
        pd.DataFrame(rows).sort_values("nb").reset_index(drop=True)
        if rows
        else pd.DataFrame(
            columns=[
                "case", "nb", "repeats",
                "ac_sparse_status", "ac_sparse_time_mean_s", "ac_sparse_time_std_s",
                "ac_dense_status",  "ac_dense_time_mean_s",  "ac_dense_time_std_s",
                "dc_status",        "dc_time_mean_s",        "dc_time_std_s",
            ]
        )
    )
    return results_df, should_run


@app.cell
def _(mo, results_df, should_run):
    status_md = (
        mo.md("Press **Run benchmark** to compute results.")
        if not should_run
        else mo.md(f"Computed results for **{len(results_df)}** testcases.")
    )
    status_md
    return


@app.cell
def _(mo, results_df):
    results_table = mo.ui.table(results_df)
    results_table
    return


@app.cell
def _(np, plt, results_df, should_run):
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=120)

    if should_run and len(results_df) > 0:
        _nb = results_df["nb"].to_numpy(dtype=float)

        _ac_sparse_mean = results_df["ac_sparse_time_mean_s"].to_numpy(dtype=float)
        _ac_sparse_std  = results_df["ac_sparse_time_std_s"].to_numpy(dtype=float)
        _ac_dense_mean  = results_df["ac_dense_time_mean_s"].to_numpy(dtype=float)
        _ac_dense_std   = results_df["ac_dense_time_std_s"].to_numpy(dtype=float)
        _dc_mean        = results_df["dc_time_mean_s"].to_numpy(dtype=float)
        _dc_std         = results_df["dc_time_std_s"].to_numpy(dtype=float)

        _m_sparse = np.isfinite(_ac_sparse_mean)
        _m_dense  = np.isfinite(_ac_dense_mean)
        _m_dc     = np.isfinite(_dc_mean)

        ax.errorbar(
            _nb[_m_sparse], _ac_sparse_mean[_m_sparse],
            yerr=_ac_sparse_std[_m_sparse],
            marker="o", linewidth=2, capsize=3,
            label="AC-OPF sparse P/Q (IPOPT)",
        )
        ax.errorbar(
            _nb[_m_dense], _ac_dense_mean[_m_dense],
            yerr=_ac_dense_std[_m_dense],
            marker="s", linewidth=2, capsize=3, linestyle="--",
            label="AC-OPF dense P/Q (IPOPT)",
        )
        ax.errorbar(
            _nb[_m_dc], _dc_mean[_m_dc],
            yerr=_dc_std[_m_dc],
            marker="o", linewidth=2, capsize=3,
            label="Lossy DC OPF (CLARABEL QP)",
        )

    ax.set_xlabel("Number of buses (nb)")
    ax.set_ylabel("Runtime (seconds)")
    ax.set_title("Runtime vs bus size (mean ± std)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    plt.yscale("log")
    fig
    return


if __name__ == "__main__":
    app.run()