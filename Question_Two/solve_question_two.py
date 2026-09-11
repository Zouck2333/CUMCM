from __future__ import annotations

import argparse
import csv
import json
import math
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix


N_TIME = 144
SOC_MIN = 1200.0
SOC_MAX = 10800.0
SOC_INITIAL = 6000.0
POWER_LIMIT_KW = 5000.0
ETA_C = 0.9
ETA_D = 0.9
SCENARIO_MAX = 14
VALIDATION_DAYS = 14
FORMAL_START = date(2025, 2, 1)
K_LOAD_CANDIDATES = (2, 4, 6, 8)
K_PV_CANDIDATES = (3, 5, 7, 10)
RHO_CANDIDATES = (0.80, 0.90, 0.95, 1.00)
THETA_JANUARY = (4, 7, 1.0, 1.0)
EPS = 1e-8
EMERGENCY_EPS = 1e-7


@dataclass(frozen=True)
class Theta:
    k_load: int
    k_pv: int
    rho_load: float
    rho_pv: float


@dataclass
class DaySolution:
    grid: np.ndarray
    charge: np.ndarray
    discharge: np.ndarray
    soc: np.ndarray
    curtailment: np.ndarray
    scenario_emergency: np.ndarray
    objective: float
    status: int
    message: str
    mip_gap: float
    node_count: int
    solve_seconds: float


def is_workday(value: date) -> bool:
    """题目未提供节假日表，主程序按周一至周五划分工作日。"""
    return value.weekday() < 5


def feature_vector(
    target: int,
    dates: list[date],
    actual_load: np.ndarray,
    actual_pv: np.ndarray,
) -> np.ndarray:
    if target < 1:
        raise ValueError("相似日特征至少需要前一日数据")
    current = dates[target]
    weekday = current.weekday() + 1
    day_of_year = current.timetuple().tm_yday
    previous_load = actual_load[target - 1]
    previous_pv = actual_pv[target - 1]
    return np.asarray(
        [
            math.sin(2.0 * math.pi * weekday / 7.0),
            math.cos(2.0 * math.pi * weekday / 7.0),
            math.sin(2.0 * math.pi * day_of_year / 365.0),
            math.cos(2.0 * math.pi * day_of_year / 365.0),
            float(np.mean(previous_load)),
            float(np.max(previous_load)),
            float(np.sum(previous_pv)),
            float(np.max(previous_pv)),
        ],
        dtype=float,
    )


def select_similar_days(
    target: int,
    count: int,
    dates: list[date],
    actual_load: np.ndarray,
    actual_pv: np.ndarray,
) -> list[int]:
    if target <= 0:
        return []
    if target == 1:
        return [0]

    all_candidates = list(range(1, target))
    target_type = is_workday(dates[target])
    same_type = [index for index in all_candidates if is_workday(dates[index]) == target_type]
    pool = same_type if len(same_type) >= count else all_candidates
    if not pool:
        return [target - 1]

    target_feature = feature_vector(target, dates, actual_load, actual_pv)
    candidate_features = np.vstack(
        [feature_vector(index, dates, actual_load, actual_pv) for index in pool]
    )
    sigma = np.std(candidate_features, axis=0, ddof=0)
    active = sigma > EPS
    if not np.any(active):
        return sorted(pool, reverse=True)[:count]

    differences = (candidate_features[:, active] - target_feature[active]) / sigma[active]
    distances = np.mean(differences * differences, axis=1)
    ranked = sorted(
        zip(pool, distances, strict=True),
        key=lambda item: (float(item[1]), -item[0]),
    )
    return [index for index, _ in ranked[:count]]


def forecast_component(
    target: int,
    values: np.ndarray,
    count: int,
    rho: float,
    dates: list[date],
    actual_load: np.ndarray,
    actual_pv: np.ndarray,
) -> np.ndarray:
    if target == 0:
        raise ValueError("1月1日应使用附件1冷启动先验")
    selected = select_similar_days(
        target, count, dates, actual_load, actual_pv
    )
    if not selected:
        selected = [target - 1]
    raw_weights = np.asarray([rho ** (target - index) for index in selected], dtype=float)
    weights = raw_weights / np.sum(raw_weights)
    prediction = np.sum(values[selected] * weights[:, None], axis=0)
    return np.maximum(0.0, prediction)


def normalized_validation_loss(actual: np.ndarray, predicted: np.ndarray) -> float:
    errors = actual - predicted
    scale = max(EPS, float(np.mean(np.abs(actual))))
    nmae = float(np.mean(np.abs(errors))) / scale
    nrmse = float(np.sqrt(np.mean(errors * errors))) / scale
    return nmae + 0.2 * nrmse


def tune_component(
    month_start: int,
    values: np.ndarray,
    k_candidates: Iterable[int],
    dates: list[date],
    actual_load: np.ndarray,
    actual_pv: np.ndarray,
) -> tuple[int, float, float]:
    validation_start = max(1, month_start - VALIDATION_DAYS)
    validation = list(range(validation_start, month_start))
    if len(validation) < 2:
        return 1, 1.0, float("nan")

    best: tuple[float, int, float] | None = None
    for count in k_candidates:
        for rho in RHO_CANDIDATES:
            predictions = np.vstack(
                [
                    forecast_component(
                        target,
                        values,
                        count,
                        rho,
                        dates,
                        actual_load,
                        actual_pv,
                    )
                    for target in validation
                ]
            )
            actual = values[validation]
            loss = normalized_validation_loss(actual, predictions)
            candidate = (loss, count, -rho)
            if best is None or candidate < best:
                best = candidate
    assert best is not None
    return best[1], -best[2], best[0]


def tune_month(
    month_start: int,
    dates: list[date],
    actual_load: np.ndarray,
    actual_pv: np.ndarray,
) -> tuple[Theta, float, float]:
    k_load, rho_load, load_loss = tune_component(
        month_start,
        actual_load,
        K_LOAD_CANDIDATES,
        dates,
        actual_load,
        actual_pv,
    )
    k_pv, rho_pv, pv_loss = tune_component(
        month_start,
        actual_pv,
        K_PV_CANDIDATES,
        dates,
        actual_load,
        actual_pv,
    )
    return Theta(k_load, k_pv, rho_load, rho_pv), load_loss, pv_loss


def select_scenario_days(
    target: int,
    dates: list[date],
    residual_load: list[np.ndarray],
) -> list[int]:
    available = list(range(len(residual_load)))
    if not available:
        return []
    target_type = is_workday(dates[target])
    same_type = [index for index in available if is_workday(dates[index]) == target_type]
    selected = sorted(same_type, reverse=True)[:SCENARIO_MAX]
    target_count = min(5, len(available))
    if len(selected) < target_count:
        remaining = [index for index in sorted(available, reverse=True) if index not in selected]
        selected.extend(remaining[: target_count - len(selected)])
    return selected


def compute_reserve(
    residual_load: list[np.ndarray],
    residual_pv: list[np.ndarray],
) -> float:
    if len(residual_load) < 5:
        return 0.0
    requirements = []
    for load_error, pv_error in zip(residual_load, residual_pv, strict=True):
        positive_net_error = np.maximum(0.0, load_error - pv_error)
        requirements.append(float(np.max(np.cumsum(positive_net_error))))
    reserve = float(np.quantile(np.asarray(requirements), 0.9)) / ETA_D
    return min(SOC_MAX - SOC_MIN, max(0.0, reserve))


def solve_day_milp(
    price: np.ndarray,
    predicted_load: np.ndarray,
    predicted_pv: np.ndarray,
    scenario_load: np.ndarray,
    scenario_pv: np.ndarray,
    soc_initial: float,
    reserve: float,
    delta_h: float,
    mip_gap: float,
    time_limit: float | None,
) -> DaySolution:
    n_scenarios = scenario_load.shape[0]
    energy_limit = POWER_LIMIT_KW * delta_h

    grid_idx = np.arange(0, N_TIME)
    charge_idx = np.arange(N_TIME, 2 * N_TIME)
    discharge_idx = np.arange(2 * N_TIME, 3 * N_TIME)
    soc_idx = np.arange(3 * N_TIME, 4 * N_TIME)
    mode_idx = np.arange(4 * N_TIME, 5 * N_TIME)
    emergency_start = 5 * N_TIME
    emergency_idx = np.arange(
        emergency_start, emergency_start + n_scenarios * N_TIME
    ).reshape(n_scenarios, N_TIME)
    n_variables = emergency_start + n_scenarios * N_TIME

    objective = np.zeros(n_variables)
    objective[grid_idx] = price
    for scenario in range(n_scenarios):
        objective[emergency_idx[scenario]] = 5.0 * price / n_scenarios

    lower = np.zeros(n_variables)
    upper = np.full(n_variables, np.inf)
    upper[charge_idx] = energy_limit
    upper[discharge_idx] = energy_limit
    lower[soc_idx] = SOC_MIN
    upper[soc_idx] = SOC_MAX
    upper[mode_idx] = 1.0

    n_rows = N_TIME + N_TIME + 2 * N_TIME + 1 + n_scenarios * N_TIME
    matrix = lil_matrix((n_rows, n_variables), dtype=float)
    row_lower = np.full(n_rows, -np.inf)
    row_upper = np.full(n_rows, np.inf)
    row = 0

    for period in range(N_TIME):
        matrix[row, grid_idx[period]] = 1.0
        matrix[row, charge_idx[period]] = -1.0
        matrix[row, discharge_idx[period]] = 1.0
        row_lower[row] = predicted_load[period] - predicted_pv[period]
        row_upper[row] = predicted_load[period]
        row += 1

    for period in range(N_TIME):
        matrix[row, soc_idx[period]] = 1.0
        matrix[row, charge_idx[period]] = -ETA_C
        matrix[row, discharge_idx[period]] = 1.0 / ETA_D
        if period == 0:
            rhs = soc_initial
        else:
            matrix[row, soc_idx[period - 1]] = -1.0
            rhs = 0.0
        row_lower[row] = rhs
        row_upper[row] = rhs
        row += 1

    for period in range(N_TIME):
        matrix[row, charge_idx[period]] = 1.0
        matrix[row, mode_idx[period]] = -energy_limit
        row_upper[row] = 0.0
        row += 1

        matrix[row, discharge_idx[period]] = 1.0
        matrix[row, mode_idx[period]] = energy_limit
        row_upper[row] = energy_limit
        row += 1

    matrix[row, soc_idx[-1]] = 1.0
    row_lower[row] = SOC_MIN + reserve
    row += 1

    for scenario in range(n_scenarios):
        for period in range(N_TIME):
            matrix[row, grid_idx[period]] = 1.0
            matrix[row, charge_idx[period]] = -1.0
            matrix[row, discharge_idx[period]] = 1.0
            matrix[row, emergency_idx[scenario, period]] = 1.0
            row_lower[row] = scenario_load[scenario, period] - scenario_pv[scenario, period]
            row += 1
    assert row == n_rows

    integrality = np.zeros(n_variables, dtype=np.uint8)
    integrality[mode_idx] = 1
    options: dict[str, float | bool] = {
        "presolve": True,
        "mip_rel_gap": mip_gap,
    }
    if time_limit is not None:
        options["time_limit"] = time_limit

    started = time.perf_counter()
    result = milp(
        c=objective,
        integrality=integrality,
        bounds=Bounds(lower, upper),
        constraints=LinearConstraint(matrix.tocsr(), row_lower, row_upper),
        options=options,
    )
    elapsed = time.perf_counter() - started
    if result.x is None or result.status != 0:
        raise RuntimeError(f"MILP求解失败: status={result.status}, {result.message}")

    solution = np.asarray(result.x, dtype=float)
    solution[np.abs(solution) < 1e-8] = 0.0
    grid = np.maximum(0.0, solution[grid_idx])
    charge = np.maximum(0.0, solution[charge_idx])
    discharge = np.maximum(0.0, solution[discharge_idx])
    soc = solution[soc_idx]
    scenario_emergency = np.maximum(0.0, solution[emergency_idx])
    curtailment = np.maximum(
        0.0,
        grid + predicted_pv + discharge - predicted_load - charge,
    )

    gap_value = getattr(result, "mip_gap", 0.0)
    if gap_value is None or not np.isfinite(gap_value):
        gap_value = 0.0
    node_value = getattr(result, "mip_node_count", 0)
    if node_value is None:
        node_value = 0
    return DaySolution(
        grid=grid,
        charge=charge,
        discharge=discharge,
        soc=soc,
        curtailment=curtailment,
        scenario_emergency=scenario_emergency,
        objective=float(result.fun),
        status=int(result.status),
        message=str(result.message),
        mip_gap=float(gap_value),
        node_count=int(node_value),
        solve_seconds=elapsed,
    )


def merge_emergency_intervals(
    emergency: np.ndarray,
    time_labels: list[str],
) -> list[dict[str, float | str]]:
    intervals: list[dict[str, float | str]] = []
    start: int | None = None
    for index, value in enumerate(emergency):
        active = value > EMERGENCY_EPS
        if active and start is None:
            start = index
        if start is not None and (not active or index == len(emergency) - 1):
            end = index if active and index == len(emergency) - 1 else index - 1
            first_parts = time_labels[start].split("-", 1)
            last_parts = time_labels[end].split("-", 1)
            label = f"{first_parts[0]}-{last_parts[-1]}"
            intervals.append(
                {
                    "period": label,
                    "amount": float(np.sum(emergency[start : end + 1])),
                }
            )
            start = None
    return intervals


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_model(
    payload: dict,
    *,
    mip_gap: float,
    time_limit: float | None,
    max_days: int | None,
) -> tuple[dict, list[dict[str, object]], list[dict[str, object]]]:
    dates = [date.fromisoformat(value) for value in payload["dates"]]
    price = np.asarray(payload["price"], dtype=float)
    prior_load = np.asarray(payload["prior_load"], dtype=float)
    prior_pv = np.asarray(payload["prior_pv"], dtype=float)
    actual_load = np.asarray(payload["actual_load"], dtype=float)
    actual_pv = np.asarray(payload["actual_pv"], dtype=float)
    time_labels = [str(value) for value in payload["time_labels"]]
    delta_h = float(payload["delta_h"])

    if price.shape != (N_TIME,):
        raise ValueError(f"电价应为144个时段，实际为{price.shape}")
    if actual_load.shape != (365, N_TIME) or actual_pv.shape != (365, N_TIME):
        raise ValueError("附件2应为365天×144时段")

    limit = len(dates) if max_days is None else min(max_days, len(dates))
    residual_load: list[np.ndarray] = []
    residual_pv: list[np.ndarray] = []
    current_theta = Theta(*THETA_JANUARY)
    current_month = 1
    soc_initial = SOC_INITIAL
    formal_days: list[dict[str, object]] = []
    detail_rows: list[dict[str, object]] = []
    daily_rows: list[dict[str, object]] = []
    monthly_parameters: list[dict[str, object]] = [
        {
            "month": "2025-01",
            "k_load": current_theta.k_load,
            "rho_load": current_theta.rho_load,
            "k_pv": current_theta.k_pv,
            "rho_pv": current_theta.rho_pv,
            "load_validation_loss": None,
            "pv_validation_loss": None,
            "warmup": True,
        }
    ]

    for day_index in range(limit):
        current_date = dates[day_index]
        if current_date.month != current_month:
            current_month = current_date.month
            current_theta, load_loss, pv_loss = tune_month(
                day_index, dates, actual_load, actual_pv
            )
            monthly_parameters.append(
                {
                    "month": current_date.strftime("%Y-%m"),
                    "k_load": current_theta.k_load,
                    "rho_load": current_theta.rho_load,
                    "k_pv": current_theta.k_pv,
                    "rho_pv": current_theta.rho_pv,
                    "load_validation_loss": load_loss,
                    "pv_validation_loss": pv_loss,
                    "warmup": False,
                }
            )

        if day_index == 0:
            predicted_load = prior_load.copy()
            predicted_pv = prior_pv.copy()
        else:
            predicted_load = forecast_component(
                day_index,
                actual_load,
                current_theta.k_load,
                current_theta.rho_load,
                dates,
                actual_load,
                actual_pv,
            )
            predicted_pv = forecast_component(
                day_index,
                actual_pv,
                current_theta.k_pv,
                current_theta.rho_pv,
                dates,
                actual_load,
                actual_pv,
            )

        scenario_days = select_scenario_days(day_index, dates, residual_load)
        if scenario_days:
            scenario_load = np.vstack(
                [np.maximum(0.0, predicted_load + residual_load[k]) for k in scenario_days]
            )
            scenario_pv = np.vstack(
                [np.maximum(0.0, predicted_pv + residual_pv[k]) for k in scenario_days]
            )
        else:
            scenario_load = predicted_load[None, :]
            scenario_pv = predicted_pv[None, :]

        reserve = compute_reserve(residual_load, residual_pv)
        solved = solve_day_milp(
            price,
            predicted_load,
            predicted_pv,
            scenario_load,
            scenario_pv,
            soc_initial,
            reserve,
            delta_h,
            mip_gap,
            time_limit,
        )

        actual_shortfall = (
            actual_load[day_index]
            + solved.charge
            - solved.grid
            - actual_pv[day_index]
            - solved.discharge
        )
        actual_emergency = np.maximum(0.0, actual_shortfall)
        actual_surplus = np.maximum(0.0, -actual_shortfall)
        plan_cost = price * solved.grid
        emergency_cost = 5.0 * price * actual_emergency
        actual_total_cost = float(np.sum(plan_cost) + np.sum(emergency_cost))
        soc_before = np.concatenate(([soc_initial], solved.soc[:-1]))

        load_error = actual_load[day_index] - predicted_load
        pv_error = actual_pv[day_index] - predicted_pv
        residual_load.append(load_error)
        residual_pv.append(pv_error)

        is_formal = current_date >= FORMAL_START
        daily_rows.append(
            {
                "date": current_date.isoformat(),
                "warmup": not is_formal,
                "k_load": current_theta.k_load,
                "rho_load": current_theta.rho_load,
                "k_pv": current_theta.k_pv,
                "rho_pv": current_theta.rho_pv,
                "scenario_count": scenario_load.shape[0],
                "reserve_kwh": reserve,
                "soc_start_kwh": soc_initial,
                "soc_end_kwh": float(solved.soc[-1]),
                "load_mae_kwh": float(np.mean(np.abs(load_error))),
                "load_rmse_kwh": float(np.sqrt(np.mean(load_error * load_error))),
                "pv_mae_kwh": float(np.mean(np.abs(pv_error))),
                "pv_rmse_kwh": float(np.sqrt(np.mean(pv_error * pv_error))),
                "planned_grid_kwh": float(np.sum(solved.grid)),
                "actual_emergency_kwh": float(np.sum(actual_emergency)),
                "actual_surplus_kwh": float(np.sum(actual_surplus)),
                "planned_cost_yuan": float(np.sum(plan_cost)),
                "emergency_cost_yuan": float(np.sum(emergency_cost)),
                "actual_total_cost_yuan": actual_total_cost,
                "solver_objective_yuan": solved.objective,
                "solver_seconds": solved.solve_seconds,
                "mip_gap": solved.mip_gap,
                "mip_node_count": solved.node_count,
                "solver_status": solved.status,
            }
        )

        if is_formal:
            intervals = merge_emergency_intervals(actual_emergency, time_labels)
            formal_days.append(
                {
                    "date": current_date.isoformat(),
                    "grid": solved.grid.tolist(),
                    "charge": solved.charge.tolist(),
                    "discharge": solved.discharge.tolist(),
                    "soc_start": soc_initial,
                    "soc_end": float(solved.soc[-1]),
                    "total_grid": float(np.sum(solved.grid)),
                    "plan_cost": float(np.sum(plan_cost)),
                    "emergency_intervals": intervals,
                }
            )
            for period in range(N_TIME):
                detail_rows.append(
                    {
                        "date": current_date.isoformat(),
                        "period": period + 1,
                        "time": time_labels[period],
                        "price_yuan_per_kwh": price[period],
                        "predicted_load_kwh": predicted_load[period],
                        "actual_load_kwh": actual_load[day_index, period],
                        "predicted_pv_kwh": predicted_pv[period],
                        "actual_pv_kwh": actual_pv[day_index, period],
                        "planned_grid_kwh": solved.grid[period],
                        "planned_charge_kwh": solved.charge[period],
                        "planned_discharge_kwh": solved.discharge[period],
                        "predicted_curtailment_kwh": solved.curtailment[period],
                        "soc_before_kwh": soc_before[period],
                        "soc_after_kwh": solved.soc[period],
                        "actual_emergency_kwh": actual_emergency[period],
                        "actual_surplus_kwh": actual_surplus[period],
                        "planned_cost_yuan": plan_cost[period],
                        "emergency_cost_yuan": emergency_cost[period],
                    }
                )

        soc_initial = float(solved.soc[-1])
        if day_index == 0 or (day_index + 1) % 10 == 0 or day_index + 1 == limit:
            print(
                f"[{day_index + 1:3d}/{limit}] {current_date.isoformat()} "
                f"场景={scenario_load.shape[0]:2d} SOC={soc_initial:9.3f} "
                f"费用={actual_total_cost:11.2f} 求解={solved.solve_seconds:.3f}s",
                flush=True,
            )

    formal_daily = [row for row in daily_rows if not bool(row["warmup"])]
    diagnostics = {
        "model": "rolling forecast + residual scenarios + daily MILP",
        "days_solved": limit,
        "formal_days": len(formal_days),
        "formal_start": FORMAL_START.isoformat(),
        "eta_charge": ETA_C,
        "eta_discharge": ETA_D,
        "soc_min_kwh": SOC_MIN,
        "soc_max_kwh": SOC_MAX,
        "scenario_max": SCENARIO_MAX,
        "week_type_rule": "Monday through Friday are workdays; weekends are non-workdays",
        "monthly_parameters": monthly_parameters,
        "formal_total_grid_kwh": float(sum(row["planned_grid_kwh"] for row in formal_daily)),
        "formal_total_emergency_kwh": float(sum(row["actual_emergency_kwh"] for row in formal_daily)),
        "formal_plan_cost_yuan": float(sum(row["planned_cost_yuan"] for row in formal_daily)),
        "formal_emergency_cost_yuan": float(sum(row["emergency_cost_yuan"] for row in formal_daily)),
        "formal_total_cost_yuan": float(sum(row["actual_total_cost_yuan"] for row in formal_daily)),
        "max_balance_residual_kwh": float(
            max(
                (
                    abs(
                        float(row["planned_grid_kwh"])
                        + float(row["actual_pv_kwh"])
                        + float(row["planned_discharge_kwh"])
                        + float(row["actual_emergency_kwh"])
                        - float(row["actual_load_kwh"])
                        - float(row["planned_charge_kwh"])
                        - float(row["actual_surplus_kwh"])
                    )
                    for row in detail_rows
                ),
                default=0.0,
            )
        ),
    }
    solution_payload = {
        "diagnostics": diagnostics,
        "days": formal_days,
    }
    return solution_payload, daily_rows, detail_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="C题第二问滚动预测与随机情景MILP")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--daily-csv", type=Path, required=True)
    parser.add_argument("--detail-csv", type=Path, required=True)
    parser.add_argument("--mip-gap", type=float, default=1e-6)
    parser.add_argument("--time-limit", type=float, default=30.0)
    parser.add_argument("--max-days", type=int, default=None)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    solution, daily_rows, detail_rows = run_model(
        payload,
        mip_gap=args.mip_gap,
        time_limit=args.time_limit,
        max_days=args.max_days,
    )

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(solution, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    write_csv(args.daily_csv, daily_rows, list(daily_rows[0].keys()))
    detail_fields = [
        "date",
        "period",
        "time",
        "price_yuan_per_kwh",
        "predicted_load_kwh",
        "actual_load_kwh",
        "predicted_pv_kwh",
        "actual_pv_kwh",
        "planned_grid_kwh",
        "planned_charge_kwh",
        "planned_discharge_kwh",
        "predicted_curtailment_kwh",
        "soc_before_kwh",
        "soc_after_kwh",
        "actual_emergency_kwh",
        "actual_surplus_kwh",
        "planned_cost_yuan",
        "emergency_cost_yuan",
    ]
    write_csv(args.detail_csv, detail_rows, detail_fields)
    print(f"求解结果已保存: {args.output_json}")


if __name__ == "__main__":
    main()
