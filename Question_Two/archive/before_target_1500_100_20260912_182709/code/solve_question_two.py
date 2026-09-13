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
from scipy.sparse import csr_matrix, lil_matrix, vstack


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
SIMILAR_POOL_MIN = max(max(K_LOAD_CANDIDATES), max(K_PV_CANDIDATES))
EPS = 1e-8
EMERGENCY_EPS = 1e-7
FORECAST_BIAS_DAYS = 0
FORECAST_BIAS_DECAY = 0.85
FORECAST_BIAS_SHRINK_DAYS = 5.0
LOAD_RISK_QUANTILE = 0.75
PV_RISK_QUANTILE = 0.25
RISK_CORRECTION_WEIGHT = 0.0
VALIDATION_RISK_WEIGHT = 0.35
PURCHASE_STRATEGY = "calibrated_quantile"
RISK_WINDOW_DAYS = 28
RISK_RADIUS_PERIODS = 3
PURCHASE_QUANTILE = 0.80


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
    throughput_kwh: float
    peak_grid_kwh: float
    primary_optimum: float
    objective_mode: str
    cost_lock_tolerance: float
    throughput_lock_tolerance: float
    tolerance_relaxed: bool


def physical_interval_labels() -> list[str]:
    labels: list[str] = []
    for period in range(N_TIME):
        start_minutes = period * 10
        end_minutes = (period + 1) * 10
        start = f"{start_minutes // 60}:{start_minutes % 60:02d}"
        end = (
            "0:00+1"
            if end_minutes == 24 * 60
            else f"{end_minutes // 60}:{end_minutes % 60:02d}"
        )
        labels.append(f"{start}-{end}")
    return labels


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
    # 负载和光伏必须共享同一候选池、标准差与距离排序。候选池是否扩展
    # 与当前分量的K无关，而以全部候选参数中的最大K作为统一阈值。
    pool = same_type if len(same_type) >= SIMILAR_POOL_MIN else all_candidates
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
    component: str,
    *,
    apply_bias_correction: bool = True,
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
    if apply_bias_correction:
        prediction = prediction + historical_forecast_correction(
            target,
            values,
            count,
            rho,
            dates,
            actual_load,
            actual_pv,
            component,
        )
    return np.maximum(0.0, prediction)


def historical_forecast_correction(
    target: int,
    values: np.ndarray,
    count: int,
    rho: float,
    dates: list[date],
    actual_load: np.ndarray,
    actual_pv: np.ndarray,
    component: str,
) -> np.ndarray:
    start = max(1, target - FORECAST_BIAS_DAYS)
    candidates = list(range(start, target))
    if not candidates:
        return np.zeros(N_TIME, dtype=float)

    same_type = [
        index
        for index in candidates
        if is_workday(dates[index]) == is_workday(dates[target])
    ]
    history = same_type if len(same_type) >= 3 else candidates
    residuals = []
    weights = []
    for index in history:
        base_prediction = forecast_component(
            index,
            values,
            count,
            rho,
            dates,
            actual_load,
            actual_pv,
            component,
            apply_bias_correction=False,
        )
        residuals.append(values[index] - base_prediction)
        weights.append(FORECAST_BIAS_DECAY ** (target - index))

    residual_matrix = np.vstack(residuals)
    weight_array = np.asarray(weights, dtype=float)
    weight_array = weight_array / np.sum(weight_array)
    bias = np.sum(residual_matrix * weight_array[:, None], axis=0)
    shrink = len(history) / (len(history) + FORECAST_BIAS_SHRINK_DAYS)

    if component == "load":
        risk_tail = np.quantile(
            np.maximum(0.0, residual_matrix),
            LOAD_RISK_QUANTILE,
            axis=0,
            method="linear",
        )
        correction = bias + RISK_CORRECTION_WEIGHT * risk_tail
    elif component == "pv":
        over_prediction_tail = np.quantile(
            np.minimum(0.0, residual_matrix),
            PV_RISK_QUANTILE,
            axis=0,
            method="linear",
        )
        correction = bias + RISK_CORRECTION_WEIGHT * over_prediction_tail
    else:
        raise ValueError(f"未知预测分量: {component}")
    return shrink * correction


def normalized_validation_loss(
    actual: np.ndarray,
    predicted: np.ndarray,
    component: str,
) -> float:
    errors = actual - predicted
    scale = max(EPS, float(np.mean(np.abs(actual))))
    nmae = float(np.mean(np.abs(errors))) / scale
    nrmse = float(np.sqrt(np.mean(errors * errors))) / scale
    if component == "load":
        risk_error = np.maximum(0.0, errors)
    elif component == "pv":
        risk_error = np.maximum(0.0, -errors)
    else:
        raise ValueError(f"未知预测分量: {component}")
    risk_penalty = float(np.mean(risk_error)) / scale
    return nmae + 0.2 * nrmse + VALIDATION_RISK_WEIGHT * risk_penalty


def tune_component(
    month_start: int,
    values: np.ndarray,
    k_candidates: Iterable[int],
    dates: list[date],
    actual_load: np.ndarray,
    actual_pv: np.ndarray,
    component: str,
) -> tuple[int, float, float] | None:
    validation_start = max(1, month_start - VALIDATION_DAYS)
    validation = list(range(validation_start, month_start))
    if len(validation) < 2:
        return None

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
                        component,
                    )
                    for target in validation
                ]
            )
            actual = values[validation]
            loss = normalized_validation_loss(actual, predictions, component)
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
    previous_theta: Theta,
) -> tuple[Theta, float | None, float | None]:
    load_result = tune_component(
        month_start,
        actual_load,
        K_LOAD_CANDIDATES,
        dates,
        actual_load,
        actual_pv,
        "load",
    )
    pv_result = tune_component(
        month_start,
        actual_pv,
        K_PV_CANDIDATES,
        dates,
        actual_load,
        actual_pv,
        "pv",
    )
    if load_result is None:
        k_load = previous_theta.k_load
        rho_load = previous_theta.rho_load
        load_loss = None
    else:
        k_load, rho_load, load_loss = load_result
    if pv_result is None:
        k_pv = previous_theta.k_pv
        rho_pv = previous_theta.rho_pv
        pv_loss = None
    else:
        k_pv, rho_pv, pv_loss = pv_result
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
    *,
    eta_discharge: float,
    quantile: float,
    mode: str,
    quantile_method: str,
) -> float:
    if quantile <= 0.0 or len(residual_load) < 5:
        return 0.0
    requirements = []
    for load_error, pv_error in zip(residual_load, residual_pv, strict=True):
        net_error = load_error - pv_error
        if mode == "positive_steps":
            trajectory = np.cumsum(np.maximum(0.0, net_error))
        elif mode == "cumulative_net":
            trajectory = np.cumsum(net_error)
        else:
            raise ValueError(f"未知安全储备模式: {mode}")
        requirements.append(max(0.0, float(np.max(trajectory))))
    reserve = (
        float(
            np.quantile(
                np.asarray(requirements),
                quantile,
                method=quantile_method,
            )
        )
        / eta_discharge
    )
    return min(SOC_MAX - SOC_MIN, max(0.0, reserve))


def calibrate_purchase_margin(
    target: int,
    dates: list[date],
    residual_load: list[np.ndarray],
    residual_pv: list[np.ndarray],
    *,
    window_days: int = RISK_WINDOW_DAYS,
    radius_periods: int = RISK_RADIUS_PERIODS,
    quantile: float = PURCHASE_QUANTILE,
) -> tuple[np.ndarray, list[int]]:
    """Recent, same-type joint net residuals; no current-day observations."""
    if len(residual_load) != target or len(residual_pv) != target:
        raise ValueError("风险校准残差库必须恰好包含目标日之前的数据")
    candidates = list(range(max(1, target - window_days), target))
    same_type = [k for k in candidates if is_workday(dates[k]) == is_workday(dates[target])]
    selected = same_type if len(same_type) >= 5 else candidates
    if not selected:
        return np.zeros(N_TIME), []
    errors = np.vstack([residual_load[k] - residual_pv[k] for k in selected])
    margin = np.asarray([
        np.quantile(
            errors[:, max(0, t - radius_periods):min(N_TIME, t + radius_periods + 1)],
            quantile, method="inverted_cdf",
        )
        for t in range(N_TIME)
    ])
    return np.maximum(0.0, margin), selected


def solve_day_milp(
    price: np.ndarray,
    predicted_load: np.ndarray,
    predicted_pv: np.ndarray,
    scenario_load: np.ndarray,
    scenario_pv: np.ndarray,
    soc_initial: float,
    reserve: float,
    delta_h: float,
    eta_charge: float,
    eta_discharge: float,
    mip_gap: float,
    time_limit: float | None,
    objective_mode: str,
    *,
    purchase_margin: np.ndarray | None = None,
    allow_grid_surplus: bool = False,
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
    peak_idx = emergency_start + n_scenarios * N_TIME
    n_variables = peak_idx + 1

    primary_objective = np.zeros(n_variables)
    primary_objective[grid_idx] = price
    for scenario in range(n_scenarios):
        primary_objective[emergency_idx[scenario]] = 5.0 * price / n_scenarios

    lower = np.zeros(n_variables)
    upper = np.full(n_variables, np.inf)
    upper[charge_idx] = energy_limit
    upper[discharge_idx] = energy_limit
    lower[soc_idx] = SOC_MIN
    upper[soc_idx] = SOC_MAX
    upper[mode_idx] = 1.0

    n_rows = (
        N_TIME
        + N_TIME
        + 2 * N_TIME
        + 1
        + n_scenarios * N_TIME
        + N_TIME
    )
    matrix = lil_matrix((n_rows, n_variables), dtype=float)
    row_lower = np.full(n_rows, -np.inf)
    row_upper = np.full(n_rows, np.inf)
    row = 0

    for period in range(N_TIME):
        matrix[row, grid_idx[period]] = 1.0
        matrix[row, charge_idx[period]] = -1.0
        matrix[row, discharge_idx[period]] = 1.0
        row_lower[row] = predicted_load[period] - predicted_pv[period]
        if purchase_margin is not None:
            row_lower[row] += purchase_margin[period]
        # Surplus in the calibrated model includes unused prepaid grid energy.
        # The legacy PV-only cap otherwise prevents any overnight load hedging.
        if not allow_grid_surplus:
            row_upper[row] = predicted_load[period]
        row += 1

    for period in range(N_TIME):
        matrix[row, soc_idx[period]] = 1.0
        matrix[row, charge_idx[period]] = -eta_charge
        matrix[row, discharge_idx[period]] = 1.0 / eta_discharge
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

    for period in range(N_TIME):
        matrix[row, grid_idx[period]] = 1.0
        matrix[row, peak_idx] = -1.0
        row_upper[row] = 0.0
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

    base_matrix = matrix.tocsr()
    bounds = Bounds(lower, upper)

    def run_stage(
        stage_name: str,
        stage_objective: np.ndarray,
        constraint_matrix: csr_matrix,
        constraint_lower: np.ndarray,
        constraint_upper: np.ndarray,
    ):
        stage_result = milp(
            c=stage_objective,
            integrality=integrality,
            bounds=bounds,
            constraints=LinearConstraint(
                constraint_matrix,
                constraint_lower,
                constraint_upper,
            ),
            options=options,
        )
        if stage_result.x is None or stage_result.status != 0:
            raise RuntimeError(
                f"MILP{stage_name}求解失败: "
                f"status={stage_result.status}, {stage_result.message}"
            )
        return stage_result

    started = time.perf_counter()
    primary_result = run_stage(
        "第一阶段（费用最小）",
        primary_objective,
        base_matrix,
        row_lower,
        row_upper,
    )
    primary_optimum = float(primary_result.fun)
    result = primary_result

    if objective_mode == "lexicographic":
        cost_tolerance = max(1e-6, abs(primary_optimum) * 1e-9)
        tolerance_relaxed = False
        stage_two_matrix = vstack(
            [base_matrix, csr_matrix(primary_objective.reshape(1, -1))],
            format="csr",
        )
        stage_two_lower = np.append(row_lower, -np.inf)
        stage_two_upper = np.append(row_upper, primary_optimum + cost_tolerance)
        throughput_objective = np.zeros(n_variables)
        throughput_objective[charge_idx] = 1.0
        throughput_objective[discharge_idx] = 1.0
        try:
            throughput_result = run_stage(
                "第二阶段（充放电吞吐量最小）",
                throughput_objective,
                stage_two_matrix,
                stage_two_lower,
                stage_two_upper,
            )
        except RuntimeError as exc:
            if "status=2" not in str(exc):
                raise
            tolerance_relaxed = True
            cost_tolerance = max(1e-4, abs(primary_optimum) * 1e-7)
            stage_two_upper[-1] = primary_optimum + cost_tolerance
            throughput_result = run_stage(
                "第二阶段（数值容差重试）",
                throughput_objective,
                stage_two_matrix,
                stage_two_lower,
                stage_two_upper,
            )
        throughput_optimum = float(throughput_result.fun)
        throughput_tolerance = max(1e-6, abs(throughput_optimum) * 1e-9)

        stage_three_matrix = vstack(
            [
                stage_two_matrix,
                csr_matrix(throughput_objective.reshape(1, -1)),
            ],
            format="csr",
        )
        stage_three_lower = np.append(stage_two_lower, -np.inf)
        stage_three_upper = np.append(
            stage_two_upper,
            throughput_optimum + throughput_tolerance,
        )
        peak_objective = np.zeros(n_variables)
        peak_objective[peak_idx] = 1.0
        try:
            result = run_stage(
                "第三阶段（最大购电量最小）",
                peak_objective,
                stage_three_matrix,
                stage_three_lower,
                stage_three_upper,
            )
        except RuntimeError as exc:
            if "status=2" not in str(exc):
                raise
            tolerance_relaxed = True
            cost_tolerance = max(1e-4, abs(primary_optimum) * 1e-7)
            throughput_tolerance = max(
                1e-4,
                abs(throughput_optimum) * 1e-7,
            )
            stage_three_upper[-2] = primary_optimum + cost_tolerance
            stage_three_upper[-1] = throughput_optimum + throughput_tolerance
            result = run_stage(
                "第三阶段（数值容差重试）",
                peak_objective,
                stage_three_matrix,
                stage_three_lower,
                stage_three_upper,
            )
    elif objective_mode != "cost":
        raise ValueError(f"未知目标模式: {objective_mode}")
    else:
        cost_tolerance = 0.0
        throughput_tolerance = 0.0
        tolerance_relaxed = False

    elapsed = time.perf_counter() - started
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

    gap_value = getattr(primary_result, "mip_gap", 0.0)
    if gap_value is None or not np.isfinite(gap_value):
        gap_value = 0.0
    node_value = getattr(primary_result, "mip_node_count", 0)
    if node_value is None:
        node_value = 0
    return DaySolution(
        grid=grid,
        charge=charge,
        discharge=discharge,
        soc=soc,
        curtailment=curtailment,
        scenario_emergency=scenario_emergency,
        objective=float(primary_objective @ solution),
        status=int(result.status),
        message=str(result.message),
        mip_gap=float(gap_value),
        node_count=int(node_value),
        solve_seconds=elapsed,
        throughput_kwh=float(np.sum(charge) + np.sum(discharge)),
        peak_grid_kwh=float(np.max(grid)),
        primary_optimum=primary_optimum,
        objective_mode=objective_mode,
        cost_lock_tolerance=cost_tolerance,
        throughput_lock_tolerance=throughput_tolerance,
        tolerance_relaxed=tolerance_relaxed,
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
    eta_charge: float = ETA_C,
    eta_discharge: float = ETA_D,
    reserve_quantile: float = 0.75,
    reserve_mode: str = "positive_steps",
    quantile_method: str = "linear",
    objective_mode: str = "lexicographic",
    purchase_strategy: str = PURCHASE_STRATEGY,
    risk_window_days: int = RISK_WINDOW_DAYS,
    risk_radius_periods: int = RISK_RADIUS_PERIODS,
    purchase_quantile: float = PURCHASE_QUANTILE,
    collect_detail: bool = True,
    collect_formal_days: bool = True,
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
    if prior_load.shape != (N_TIME,) or prior_pv.shape != (N_TIME,):
        raise ValueError("前一日负荷和光伏数据均应包含144个时段")
    if actual_load.shape != (365, N_TIME) or actual_pv.shape != (365, N_TIME):
        raise ValueError("附件2应为365天×144时段")
    if len(dates) != 365 or dates[0] != date(2025, 1, 1) or dates[-1] != date(2025, 12, 31):
        raise ValueError("日期必须完整覆盖2025-01-01至2025-12-31")
    if any((dates[index + 1] - dates[index]).days != 1 for index in range(364)):
        raise ValueError("日期序列必须严格按天连续递增")
    if time_labels != physical_interval_labels():
        raise ValueError(
            "时间标签必须采用右端点口径并依次覆盖0:00-0:10至23:50-0:00+1"
        )
    for name, values in (
        ("电价", price),
        ("前一日负荷", prior_load),
        ("前一日光伏", prior_pv),
        ("实际负荷", actual_load),
        ("实际光伏", actual_pv),
    ):
        if not np.all(np.isfinite(values)):
            raise ValueError(f"{name}包含非有限数值")
        if np.any(values < -EPS):
            raise ValueError(f"{name}包含负值")
    if not math.isclose(delta_h, 1.0 / 6.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(f"时间步长应为1/6小时，实际为{delta_h}")
    if not 0.0 < eta_charge <= 1.0 or not 0.0 < eta_discharge <= 1.0:
        raise ValueError("充、放电效率必须位于(0, 1]区间")
    if not 0.0 <= reserve_quantile <= 1.0:
        raise ValueError("安全储备分位数必须位于[0, 1]区间")
    if reserve_mode not in {"positive_steps", "cumulative_net"}:
        raise ValueError(f"未知安全储备模式: {reserve_mode}")
    if quantile_method not in {"linear", "higher", "lower", "nearest", "midpoint"}:
        raise ValueError(f"不支持的分位数算法: {quantile_method}")
    if objective_mode not in {"cost", "lexicographic"}:
        raise ValueError(f"未知目标模式: {objective_mode}")
    if purchase_strategy not in {"legacy_scenarios", "calibrated_quantile"}:
        raise ValueError(f"未知购电策略: {purchase_strategy}")
    if risk_window_days < 1 or not 0 <= risk_radius_periods < N_TIME:
        raise ValueError("风险窗口必须为正，时段半径必须位于[0,143]")
    if not 0.0 < purchase_quantile < 1.0:
        raise ValueError("购电分位数必须位于(0,1)")

    limit = len(dates) if max_days is None else min(max_days, len(dates))
    residual_load: list[np.ndarray] = []
    residual_pv: list[np.ndarray] = []
    current_theta = Theta(*THETA_JANUARY)
    current_month = 1
    soc_initial = SOC_INITIAL
    formal_days: list[dict[str, object]] = []
    detail_rows: list[dict[str, object]] = []
    daily_rows: list[dict[str, object]] = []
    max_balance_residual = 0.0
    monthly_parameters: list[dict[str, object]] = [
        {
            "month": "2025-01",
            "k_load": current_theta.k_load,
            "rho_load": current_theta.rho_load,
            "k_pv": current_theta.k_pv,
            "rho_pv": current_theta.rho_pv,
            "load_validation_loss": None,
            "pv_validation_loss": None,
            "validation_start": None,
            "validation_end": None,
            "warmup": True,
        }
    ]
    forecast_history: list[dict[str, object]] = []

    for day_index in range(limit):
        current_date = dates[day_index]
        if current_date.month != current_month:
            current_month = current_date.month
            current_theta, load_loss, pv_loss = tune_month(
                day_index, dates, actual_load, actual_pv, current_theta
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
                    "validation_start": dates[max(1, day_index - VALIDATION_DAYS)].isoformat(),
                    "validation_end": dates[day_index - 1].isoformat(),
                    "warmup": False,
                }
            )

        if day_index == 0:
            predicted_load = prior_load.copy()
            predicted_pv = prior_pv.copy()
            similar_load_days: list[int] = []
            similar_pv_days: list[int] = []
        else:
            similar_load_days = select_similar_days(
                day_index,
                current_theta.k_load,
                dates,
                actual_load,
                actual_pv,
            )
            similar_pv_days = select_similar_days(
                day_index,
                current_theta.k_pv,
                dates,
                actual_load,
                actual_pv,
            )
            predicted_load = forecast_component(
                day_index,
                actual_load,
                current_theta.k_load,
                current_theta.rho_load,
                dates,
                actual_load,
                actual_pv,
                "load",
            )
            predicted_pv = forecast_component(
                day_index,
                actual_pv,
                current_theta.k_pv,
                current_theta.rho_pv,
                dates,
                actual_load,
                actual_pv,
                "pv",
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

        calibrated = purchase_strategy == "calibrated_quantile" and current_date >= FORMAL_START
        purchase_margin = np.zeros(N_TIME)
        risk_source_days: list[int] = []
        if calibrated:
            purchase_margin, risk_source_days = calibrate_purchase_margin(
                day_index, dates, residual_load, residual_pv,
                window_days=risk_window_days, radius_periods=risk_radius_periods,
                quantile=purchase_quantile,
            )
            # Quantile-constrained joint procurement/storage MILP. No scenario
            # recourse objective is added: actual emergency is settled at 5p.
            scenario_load = np.empty((0, N_TIME))
            scenario_pv = np.empty((0, N_TIME))
            scenario_days = []

        reserve = compute_reserve(
            residual_load,
            residual_pv,
            eta_discharge=eta_discharge,
            quantile=reserve_quantile,
            mode=reserve_mode,
            quantile_method=quantile_method,
        )
        solved = solve_day_milp(
            price,
            predicted_load,
            predicted_pv,
            scenario_load,
            scenario_pv,
            soc_initial,
            reserve,
            delta_h,
            eta_charge,
            eta_discharge,
            mip_gap,
            time_limit,
            objective_mode,
            purchase_margin=purchase_margin if calibrated else None,
            allow_grid_surplus=calibrated,
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
        balance_residual = (
            solved.grid
            + actual_pv[day_index]
            + solved.discharge
            + actual_emergency
            - actual_load[day_index]
            - solved.charge
            - actual_surplus
        )
        max_balance_residual = max(
            max_balance_residual,
            float(np.max(np.abs(balance_residual))),
        )

        load_error = actual_load[day_index] - predicted_load
        pv_error = actual_pv[day_index] - predicted_pv
        residual_load.append(load_error)
        residual_pv.append(pv_error)
        forecast_history.append({
            "date": current_date.isoformat(),
            "predicted_load": predicted_load.tolist(),
            "predicted_pv": predicted_pv.tolist(),
        })

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
                "purchase_strategy": "calibrated_quantile" if calibrated else "legacy_scenarios",
                "risk_source_dates": ";".join(dates[k].isoformat() for k in risk_source_days),
                "risk_source_day_count": len(risk_source_days),
                "purchase_margin_kwh": float(purchase_margin.sum()),
                "similar_load_dates": ";".join(
                    dates[index].isoformat() for index in similar_load_days
                ),
                "similar_pv_dates": ";".join(
                    dates[index].isoformat() for index in similar_pv_days
                ),
                "scenario_source_dates": ";".join(
                    dates[index].isoformat() for index in scenario_days
                ),
                "reserve_latest_source_date": dates[day_index - 1].isoformat()
                if day_index > 0
                else "",
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
                "primary_optimum_yuan": solved.primary_optimum,
                "storage_throughput_kwh": solved.throughput_kwh,
                "peak_grid_kwh": solved.peak_grid_kwh,
                "objective_mode": solved.objective_mode,
                "cost_lock_tolerance": solved.cost_lock_tolerance,
                "throughput_lock_tolerance": solved.throughput_lock_tolerance,
                "lexicographic_tolerance_relaxed": solved.tolerance_relaxed,
                "solver_seconds": solved.solve_seconds,
                "mip_gap": solved.mip_gap,
                "mip_node_count": solved.node_count,
                "solver_status": solved.status,
            }
        )

        if is_formal and collect_formal_days:
            intervals = merge_emergency_intervals(actual_emergency, time_labels)
            formal_days.append(
                {
                    "date": current_date.isoformat(),
                    "grid": solved.grid.tolist(),
                    "charge": solved.charge.tolist(),
                    "discharge": solved.discharge.tolist(),
                    "purchase_margin": purchase_margin.tolist(),
                    "soc_start": soc_initial,
                    "soc_end": float(solved.soc[-1]),
                    "total_grid": float(np.sum(solved.grid)),
                    "plan_cost": float(np.sum(plan_cost)),
                    "emergency_intervals": intervals,
                }
            )
        if is_formal and collect_detail:
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
                        "purchase_margin_kwh": purchase_margin[period],
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
        "model": "rolling forecast + calibrated net-load quantile + joint procurement/storage MILP"
        if purchase_strategy == "calibrated_quantile" else "rolling forecast + residual scenarios + daily MILP",
        "purchase_strategy": purchase_strategy,
        "risk_window_days": risk_window_days,
        "risk_radius_periods": risk_radius_periods,
        "purchase_quantile": purchase_quantile,
        "purchase_quantile_method": "inverted_cdf",
        "predicted_surplus_mode": "total_supply" if purchase_strategy == "calibrated_quantile" else "pv_only",
        "warmup_strategy": "legacy_scenarios",
        "primary_cost_definition": "planned purchase cost subject to calibrated quantile floor"
        if purchase_strategy == "calibrated_quantile" else "planned cost plus scenario expected emergency cost",
        "days_solved": limit,
        "formal_days": len(formal_daily),
        "formal_start": FORMAL_START.isoformat(),
        "eta_charge": eta_charge,
        "eta_discharge": eta_discharge,
        "reserve_quantile": reserve_quantile,
        "reserve_mode": reserve_mode,
        "reserve_quantile_method": quantile_method,
        "objective_mode": objective_mode,
        "validation_risk_weight": VALIDATION_RISK_WEIGHT,
        "forecast_bias_days": FORECAST_BIAS_DAYS,
        "forecast_bias_decay": FORECAST_BIAS_DECAY,
        "forecast_bias_shrink_days": FORECAST_BIAS_SHRINK_DAYS,
        "risk_correction_weight": RISK_CORRECTION_WEIGHT,
        "similar_day_pool_minimum": SIMILAR_POOL_MIN,
        "soc_min_kwh": SOC_MIN,
        "soc_max_kwh": SOC_MAX,
        "scenario_max": SCENARIO_MAX,
        "week_type_rule": "Monday through Friday are workdays; weekends are non-workdays",
        "time_mapping": "right endpoint; period 1 is 0:00-0:10 and period 144 is 23:50-24:00",
        "monthly_parameters": monthly_parameters,
        "formal_total_grid_kwh": float(sum(row["planned_grid_kwh"] for row in formal_daily)),
        "formal_total_emergency_kwh": float(sum(row["actual_emergency_kwh"] for row in formal_daily)),
        "formal_plan_cost_yuan": float(sum(row["planned_cost_yuan"] for row in formal_daily)),
        "formal_emergency_cost_yuan": float(sum(row["emergency_cost_yuan"] for row in formal_daily)),
        "formal_total_cost_yuan": float(sum(row["actual_total_cost_yuan"] for row in formal_daily)),
        "max_balance_residual_kwh": max_balance_residual,
    }
    solution_payload = {
        "diagnostics": diagnostics,
        "time_labels": time_labels,
        "days": formal_days,
        "forecast_history": forecast_history,
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
    parser.add_argument("--purchase-strategy", choices=("legacy_scenarios", "calibrated_quantile"), default=PURCHASE_STRATEGY)
    parser.add_argument("--risk-window-days", type=int, default=RISK_WINDOW_DAYS)
    parser.add_argument("--risk-radius-periods", type=int, default=RISK_RADIUS_PERIODS)
    parser.add_argument("--purchase-quantile", type=float, default=PURCHASE_QUANTILE)
    parser.add_argument("--eta-charge", type=float, default=ETA_C)
    parser.add_argument("--eta-discharge", type=float, default=ETA_D)
    parser.add_argument("--reserve-quantile", type=float, default=0.75)
    parser.add_argument(
        "--reserve-mode",
        choices=("positive_steps", "cumulative_net"),
        default="positive_steps",
    )
    parser.add_argument(
        "--quantile-method",
        choices=("linear", "higher", "lower", "nearest", "midpoint"),
        default="linear",
    )
    parser.add_argument(
        "--objective-mode",
        choices=("cost", "lexicographic"),
        default="lexicographic",
    )
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    solution, daily_rows, detail_rows = run_model(
        payload,
        mip_gap=args.mip_gap,
        time_limit=args.time_limit,
        max_days=args.max_days,
        eta_charge=args.eta_charge,
        eta_discharge=args.eta_discharge,
        reserve_quantile=args.reserve_quantile,
        reserve_mode=args.reserve_mode,
        quantile_method=args.quantile_method,
        objective_mode=args.objective_mode,
        purchase_strategy=args.purchase_strategy,
        risk_window_days=args.risk_window_days,
        risk_radius_periods=args.risk_radius_periods,
        purchase_quantile=args.purchase_quantile,
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
        "purchase_margin_kwh",
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
