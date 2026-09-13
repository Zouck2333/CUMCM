from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, milp
from scipy.sparse import lil_matrix


SUPPORTED_METHODS = {"lp", "milp"}
SUPPORTED_OBJECTIVES = {"cost", "throughput", "peak"}


def solve(payload: dict) -> dict:
    method = str(payload.get("method", "milp")).lower()
    objective_kind = str(payload.get("objective", "cost")).lower()
    if method not in SUPPORTED_METHODS:
        raise ValueError(f"Unsupported method: {method}")
    if objective_kind not in SUPPORTED_OBJECTIVES:
        raise ValueError(f"Unsupported objective: {objective_kind}")
    if method == "lp" and objective_kind != "cost":
        raise ValueError("The LP entry only supports the cost objective.")

    price = np.asarray(payload["price"], dtype=float)
    load = np.asarray(payload["load"], dtype=float)
    pv = np.asarray(payload["pv"], dtype=float)
    delta_h = float(payload["delta_h"])
    eta = float(payload["eta"])
    soc_min = float(payload["soc_min"])
    soc_max = float(payload["soc_max"])
    soc_initial = float(payload["soc_initial"])
    soc_terminal = float(payload["soc_terminal"])
    power_limit_kw = float(payload["power_limit_kw"])
    energy_limit = power_limit_kw * delta_h
    n_time = len(price)

    grid_idx = np.arange(0, n_time)
    charge_idx = np.arange(n_time, 2 * n_time)
    discharge_idx = np.arange(2 * n_time, 3 * n_time)
    curtail_idx = np.arange(3 * n_time, 4 * n_time)
    soc_idx = np.arange(4 * n_time, 5 * n_time)
    mode_idx = (
        np.arange(5 * n_time, 6 * n_time)
        if method == "milp"
        else None
    )
    peak_idx = 6 * n_time if method == "milp" else None
    n_var = 6 * n_time + 1 if method == "milp" else 5 * n_time

    objective = np.zeros(n_var)
    if objective_kind == "cost":
        objective[grid_idx] = price
    elif objective_kind == "throughput":
        objective[charge_idx] = 1.0
        objective[discharge_idx] = 1.0
    else:
        objective[peak_idx] = 1.0

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
    equality_rhs[2 * n_time] = soc_terminal

    if method == "lp":
        if payload.get("cost_upper") is not None:
            raise ValueError("The LP entry does not accept cost_upper.")
        options = {"presolve": True}
        if payload.get("time_limit") is not None:
            options["time_limit"] = float(payload["time_limit"])
        solved = linprog(
            c=objective,
            A_eq=equality.tocsr(),
            b_eq=equality_rhs,
            bounds=list(zip(lower, upper)),
            method="highs",
            options=options,
        )
        if solved.x is None or solved.status != 0:
            raise RuntimeError(
                f"status={solved.status}, message={solved.message}"
            )
        x = solved.x
        return {
            "grid": x[grid_idx].tolist(),
            "charge": x[charge_idx].tolist(),
            "discharge": x[discharge_idx].tolist(),
            "curtailment": x[curtail_idx].tolist(),
            "soc": x[soc_idx].tolist(),
            "solver": "SciPy linprog / HiGHS",
            "status": int(solved.status),
            "message": str(solved.message),
            "objective_value": float(solved.fun),
            "iterations": int(getattr(solved, "nit", 0)),
        }

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

    peak_constraint = lil_matrix((n_time, n_var), dtype=float)
    peak_upper = np.zeros(n_time)
    for t in range(n_time):
        peak_constraint[t, grid_idx[t]] = 1.0 / delta_h
        peak_constraint[t, peak_idx] = -1.0

    constraints = [
        LinearConstraint(equality.tocsr(), equality_rhs, equality_rhs),
        LinearConstraint(mutex.tocsr(), -np.inf, mutex_upper),
        LinearConstraint(peak_constraint.tocsr(), -np.inf, peak_upper),
    ]

    if payload.get("cost_upper") is not None:
        cost_row = np.zeros((1, n_var))
        cost_row[0, grid_idx] = price
        constraints.append(
            LinearConstraint(
                cost_row,
                -np.inf,
                np.asarray([float(payload["cost_upper"])]),
            )
        )

    if payload.get("throughput_upper") is not None:
        throughput_row = np.zeros((1, n_var))
        throughput_row[0, charge_idx] = 1.0
        throughput_row[0, discharge_idx] = 1.0
        constraints.append(
            LinearConstraint(
                throughput_row,
                -np.inf,
                np.asarray([float(payload["throughput_upper"])]),
            )
        )

    options = {"mip_rel_gap": float(payload["mip_gap"]), "presolve": True}
    if payload.get("time_limit") is not None:
        options["time_limit"] = float(payload["time_limit"])
    solved = milp(
        c=objective,
        integrality=integrality,
        bounds=Bounds(lower, upper),
        constraints=constraints,
        options=options,
    )
    if solved.x is None or solved.status != 0:
        raise RuntimeError(f"status={solved.status}, message={solved.message}")

    x = solved.x
    return {
        "grid": x[grid_idx].tolist(),
        "charge": x[charge_idx].tolist(),
        "discharge": x[discharge_idx].tolist(),
        "curtailment": x[curtail_idx].tolist(),
        "soc": x[soc_idx].tolist(),
        "solver": "SciPy milp / HiGHS",
        "status": int(solved.status),
        "message": str(solved.message),
        "objective_value": float(solved.fun),
        "mip_gap": float(getattr(solved, "mip_gap", np.nan)),
        "mip_node_count": int(getattr(solved, "mip_node_count", 0)),
    }


def main() -> None:
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    output_path.write_text(
        json.dumps(solve(payload), ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
