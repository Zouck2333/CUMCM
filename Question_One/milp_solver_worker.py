from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, milp
from scipy.sparse import lil_matrix


def solve(payload: dict) -> dict:
    method = str(payload["method"]).lower()
    if method not in {"lp", "milp"}:
        raise ValueError(f"不支持的求解方法: {method}")
    price = np.asarray(payload["price"], dtype=float)
    load = np.asarray(payload["load"], dtype=float)
    pv = np.asarray(payload["pv"], dtype=float)
    eta = float(payload["eta"])
    soc_min = float(payload["soc_min"])
    soc_max = float(payload["soc_max"])
    soc_initial = float(payload["soc_initial"])
    energy_limit = float(payload["energy_limit"])
    n_time = len(price)

    grid_idx = np.arange(0, n_time)
    charge_idx = np.arange(n_time, 2 * n_time)
    discharge_idx = np.arange(2 * n_time, 3 * n_time)
    curtail_idx = np.arange(3 * n_time, 4 * n_time)
    soc_idx = np.arange(4 * n_time, 5 * n_time)
    mode_idx = np.arange(5 * n_time, 6 * n_time) if method == "milp" else None
    n_var = 6 * n_time if method == "milp" else 5 * n_time

    objective = np.zeros(n_var)
    objective[grid_idx] = price
    lower = np.zeros(n_var)
    upper = np.full(n_var, np.inf)
    upper[charge_idx] = energy_limit
    upper[discharge_idx] = energy_limit
    upper[curtail_idx] = pv
    lower[soc_idx] = soc_min
    upper[soc_idx] = soc_max
    if mode_idx is not None:
        upper[mode_idx] = 1.0

    equality = lil_matrix((2 * n_time + 1, n_var), dtype=float)
    equality_rhs = np.zeros(2 * n_time + 1)
    for t in range(n_time):
        equality[t, grid_idx[t]] = 1.0
        equality[t, charge_idx[t]] = -1.0
        equality[t, discharge_idx[t]] = 1.0
        equality[t, curtail_idx[t]] = -1.0
        equality_rhs[t] = load[t] - pv[t]

        row = n_time + t
        equality[row, soc_idx[t]] = 1.0
        equality[row, charge_idx[t]] = -eta
        equality[row, discharge_idx[t]] = 1.0 / eta
        if t == 0:
            equality_rhs[row] = soc_initial
        else:
            equality[row, soc_idx[t - 1]] = -1.0
    equality[2 * n_time, soc_idx[-1]] = 1.0
    equality_rhs[2 * n_time] = soc_initial

    if method == "lp":
        options = {"presolve": True}
        if payload.get("time_limit") is not None:
            options["time_limit"] = float(payload["time_limit"])
        result = linprog(
            c=objective,
            A_eq=equality.tocsr(),
            b_eq=equality_rhs,
            bounds=list(zip(lower, upper)),
            method="highs",
            options=options,
        )
    else:
        integrality = np.zeros(n_var, dtype=np.uint8)
        integrality[mode_idx] = 1
        mutex = lil_matrix((2 * n_time, n_var), dtype=float)
        mutex_upper = np.zeros(2 * n_time)
        for t in range(n_time):
            mutex[2 * t, charge_idx[t]] = 1.0
            mutex[2 * t, mode_idx[t]] = -energy_limit
            mutex[2 * t + 1, discharge_idx[t]] = 1.0
            mutex[2 * t + 1, mode_idx[t]] = energy_limit
            mutex_upper[2 * t + 1] = energy_limit

        options = {"mip_rel_gap": float(payload["mip_gap"]), "presolve": True}
        if payload.get("time_limit") is not None:
            options["time_limit"] = float(payload["time_limit"])
        result = milp(
            c=objective,
            integrality=integrality,
            bounds=Bounds(lower, upper),
            constraints=[
                LinearConstraint(equality.tocsr(), equality_rhs, equality_rhs),
                LinearConstraint(mutex.tocsr(), -np.inf, mutex_upper),
            ],
            options=options,
        )
    if result.x is None or result.status != 0:
        raise RuntimeError(f"status={result.status}, message={result.message}")

    x = result.x
    output = {
        "grid": x[grid_idx].tolist(),
        "charge": x[charge_idx].tolist(),
        "discharge": x[discharge_idx].tolist(),
        "curtailment": x[curtail_idx].tolist(),
        "soc": x[soc_idx].tolist(),
        "solver": "SciPy linprog / HiGHS" if method == "lp" else "SciPy milp / HiGHS",
        "status": int(result.status),
        "message": str(result.message),
        "objective_value": float(result.fun),
    }
    if method == "milp":
        output["mip_gap"] = float(getattr(result, "mip_gap", np.nan))
        output["mip_node_count"] = int(getattr(result, "mip_node_count", 0))
    else:
        output["iterations"] = int(getattr(result, "nit", 0))
    return output


def main() -> None:
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    output_path.write_text(json.dumps(solve(payload)), encoding="utf-8")


if __name__ == "__main__":
    main()
