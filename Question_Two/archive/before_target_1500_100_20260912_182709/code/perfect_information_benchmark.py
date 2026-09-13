from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, linprog, milp
from scipy.sparse import coo_matrix


N_TIME = 144
SOC_MIN = 1200.0
SOC_MAX = 10800.0
POWER_LIMIT_KW = 5000.0
ETA_C = 0.9
ETA_D = 0.9
DEFAULT_FORMAL_START = date(2025, 2, 1)
TOL = 1e-6


@dataclass
class DispatchSolution:
    name: str
    grid: np.ndarray
    charge: np.ndarray
    discharge: np.ndarray
    soc: np.ndarray
    objective: float
    status: int
    message: str
    solve_seconds: float
    mip_gap: float | None
    node_count: int | None
    iterations: int | None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_daily_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    required = {
        "date",
        "warmup",
        "reserve_kwh",
        "soc_start_kwh",
        "actual_total_cost_yuan",
    }
    missing = required.difference(rows[0] if rows else {})
    if missing:
        raise ValueError(f"每日结果CSV缺少字段: {sorted(missing)}")
    return rows


def select_horizon(
    payload: dict[str, Any],
    daily_rows: list[dict[str, str]],
    formal_start: date,
    end_date: date | None,
    max_days: int | None,
) -> tuple[list[date], np.ndarray, np.ndarray, list[dict[str, str]]]:
    all_dates = [date.fromisoformat(value) for value in payload["dates"]]
    daily_by_date = {date.fromisoformat(row["date"]): row for row in daily_rows}

    selected_dates = [
        value
        for value in all_dates
        if value >= formal_start and (end_date is None or value <= end_date)
    ]
    if max_days is not None:
        if max_days <= 0:
            raise ValueError("--max-days 必须为正整数")
        selected_dates = selected_dates[:max_days]
    if not selected_dates:
        raise ValueError("所选正式评价区间为空")

    missing = [value.isoformat() for value in selected_dates if value not in daily_by_date]
    if missing:
        raise ValueError(f"每日结果CSV缺少评价日期，例如: {missing[:3]}")

    date_to_index = {value: index for index, value in enumerate(all_dates)}
    indices = [date_to_index[value] for value in selected_dates]
    actual_load = np.asarray(payload["actual_load"], dtype=float)[indices]
    actual_pv = np.asarray(payload["actual_pv"], dtype=float)[indices]
    selected_rows = [daily_by_date[value] for value in selected_dates]
    return selected_dates, actual_load, actual_pv, selected_rows


def sparse_matrix(
    row_blocks: Iterable[np.ndarray],
    col_blocks: Iterable[np.ndarray],
    data_blocks: Iterable[np.ndarray],
    shape: tuple[int, int],
):
    rows = np.concatenate(list(row_blocks)).astype(np.int64, copy=False)
    cols = np.concatenate(list(col_blocks)).astype(np.int64, copy=False)
    data = np.concatenate(list(data_blocks)).astype(float, copy=False)
    return coo_matrix((data, (rows, cols)), shape=shape).tocsr()


def solve_matched_milp(
    price: np.ndarray,
    actual_load: np.ndarray,
    actual_pv: np.ndarray,
    reserves: np.ndarray,
    soc_initial: float,
    delta_h: float,
    eta_charge: float,
    eta_discharge: float,
    mip_gap: float,
    time_limit: float | None,
) -> DispatchSolution:
    """保留互斥和每日滚动安全储备的全评价期完美信息MILP。"""
    n_days, periods_per_day = actual_load.shape
    if periods_per_day != N_TIME:
        raise ValueError(f"每天应有{N_TIME}个时段")
    n = n_days * N_TIME
    load = actual_load.reshape(-1)
    pv = actual_pv.reshape(-1)
    tiled_price = np.tile(price, n_days)
    energy_limit = POWER_LIMIT_KW * delta_h

    grid = np.arange(0, n)
    charge = np.arange(n, 2 * n)
    discharge = np.arange(2 * n, 3 * n)
    soc = np.arange(3 * n, 4 * n)
    mode = np.arange(4 * n, 5 * n)
    n_variables = 5 * n

    objective = np.zeros(n_variables)
    objective[grid] = tiled_price
    lower = np.zeros(n_variables)
    upper = np.full(n_variables, np.inf)
    upper[charge] = energy_limit
    upper[discharge] = energy_limit
    lower[soc] = SOC_MIN
    upper[soc] = SOC_MAX
    upper[mode] = 1.0

    balance_rows = np.arange(0, n)
    soc_rows = np.arange(n, 2 * n)
    charge_rows = np.arange(2 * n, 3 * n)
    discharge_rows = np.arange(3 * n, 4 * n)
    reserve_rows = np.arange(4 * n, 4 * n + n_days)
    day_ends = np.arange(N_TIME - 1, n, N_TIME)

    matrix = sparse_matrix(
        [
            balance_rows,
            balance_rows,
            balance_rows,
            soc_rows,
            soc_rows,
            soc_rows,
            soc_rows[1:],
            charge_rows,
            charge_rows,
            discharge_rows,
            discharge_rows,
            reserve_rows,
        ],
        [
            grid,
            charge,
            discharge,
            soc,
            charge,
            discharge,
            soc[:-1],
            charge,
            mode,
            discharge,
            mode,
            soc[day_ends],
        ],
        [
            np.ones(n),
            -np.ones(n),
            np.ones(n),
            np.ones(n),
            -eta_charge * np.ones(n),
            (1.0 / eta_discharge) * np.ones(n),
            -np.ones(n - 1),
            np.ones(n),
            -energy_limit * np.ones(n),
            np.ones(n),
            energy_limit * np.ones(n),
            np.ones(n_days),
        ],
        (4 * n + n_days, n_variables),
    )

    row_lower = np.full(4 * n + n_days, -np.inf)
    row_upper = np.full(4 * n + n_days, np.inf)
    row_lower[balance_rows] = load - pv
    row_upper[balance_rows] = load
    row_lower[soc_rows] = 0.0
    row_upper[soc_rows] = 0.0
    row_lower[soc_rows[0]] = soc_initial
    row_upper[soc_rows[0]] = soc_initial
    row_upper[charge_rows] = 0.0
    row_upper[discharge_rows] = energy_limit
    row_lower[reserve_rows] = SOC_MIN + reserves

    integrality = np.zeros(n_variables, dtype=np.uint8)
    integrality[mode] = 1
    options: dict[str, float | bool] = {"presolve": True, "mip_rel_gap": mip_gap}
    if time_limit is not None:
        options["time_limit"] = time_limit

    started = time.perf_counter()
    result = milp(
        c=objective,
        integrality=integrality,
        bounds=Bounds(lower, upper),
        constraints=LinearConstraint(matrix, row_lower, row_upper),
        options=options,
    )
    elapsed = time.perf_counter() - started
    if result.x is None:
        raise RuntimeError(f"匹配MILP没有可行解: status={result.status}, {result.message}")
    if result.status != 0:
        raise RuntimeError(
            "匹配MILP未证明最优。可增大--time-limit或放宽--mip-gap: "
            f"status={result.status}, {result.message}"
        )

    x = np.asarray(result.x, dtype=float)
    x[np.abs(x) < 1e-9] = 0.0
    gap_value = getattr(result, "mip_gap", None)
    node_value = getattr(result, "mip_node_count", None)
    return DispatchSolution(
        name="matched_milp",
        grid=np.maximum(0.0, x[grid]).reshape(n_days, N_TIME),
        charge=np.maximum(0.0, x[charge]).reshape(n_days, N_TIME),
        discharge=np.maximum(0.0, x[discharge]).reshape(n_days, N_TIME),
        soc=x[soc].reshape(n_days, N_TIME),
        objective=float(result.fun),
        status=int(result.status),
        message=str(result.message),
        solve_seconds=elapsed,
        mip_gap=None if gap_value is None else float(gap_value),
        node_count=None if node_value is None else int(node_value),
        iterations=None,
    )


def solve_lp_lower_bound(
    price: np.ndarray,
    actual_load: np.ndarray,
    actual_pv: np.ndarray,
    soc_initial: float,
    delta_h: float,
    eta_charge: float,
    eta_discharge: float,
    time_limit: float | None,
) -> DispatchSolution:
    """取消每日储备并允许同时充放电的全评价期完美信息LP下界。"""
    n_days, periods_per_day = actual_load.shape
    if periods_per_day != N_TIME:
        raise ValueError(f"每天应有{N_TIME}个时段")
    n = n_days * N_TIME
    load = actual_load.reshape(-1)
    pv = actual_pv.reshape(-1)
    tiled_price = np.tile(price, n_days)
    energy_limit = POWER_LIMIT_KW * delta_h

    grid = np.arange(0, n)
    charge = np.arange(n, 2 * n)
    discharge = np.arange(2 * n, 3 * n)
    soc = np.arange(3 * n, 4 * n)
    n_variables = 4 * n

    objective = np.zeros(n_variables)
    objective[grid] = tiled_price
    period = np.arange(n)
    a_ub = sparse_matrix(
        [period, period, period, n + period, n + period, n + period],
        [grid, charge, discharge, grid, charge, discharge],
        [
            -np.ones(n),
            np.ones(n),
            -np.ones(n),
            np.ones(n),
            -np.ones(n),
            np.ones(n),
        ],
        (2 * n, n_variables),
    )
    b_ub = np.concatenate((-(load - pv), load))

    equality_rows = np.arange(n)
    a_eq = sparse_matrix(
        [equality_rows, equality_rows, equality_rows, equality_rows[1:]],
        [soc, charge, discharge, soc[:-1]],
        [
            np.ones(n),
            -eta_charge * np.ones(n),
            (1.0 / eta_discharge) * np.ones(n),
            -np.ones(n - 1),
        ],
        (n, n_variables),
    )
    b_eq = np.zeros(n)
    b_eq[0] = soc_initial
    bounds = (
        [(0.0, None)] * n
        + [(0.0, energy_limit)] * n
        + [(0.0, energy_limit)] * n
        + [(SOC_MIN, SOC_MAX)] * n
    )
    options: dict[str, float | bool] = {"presolve": True}
    if time_limit is not None:
        options["time_limit"] = time_limit

    started = time.perf_counter()
    result = linprog(
        c=objective,
        A_ub=a_ub,
        b_ub=b_ub,
        A_eq=a_eq,
        b_eq=b_eq,
        bounds=bounds,
        method="highs",
        options=options,
    )
    elapsed = time.perf_counter() - started
    if result.x is None or not result.success:
        raise RuntimeError(f"LP下界求解失败: status={result.status}, {result.message}")

    x = np.asarray(result.x, dtype=float)
    x[np.abs(x) < 1e-9] = 0.0
    return DispatchSolution(
        name="lp_lower",
        grid=np.maximum(0.0, x[grid]).reshape(n_days, N_TIME),
        charge=np.maximum(0.0, x[charge]).reshape(n_days, N_TIME),
        discharge=np.maximum(0.0, x[discharge]).reshape(n_days, N_TIME),
        soc=x[soc].reshape(n_days, N_TIME),
        objective=float(result.fun),
        status=int(result.status),
        message=str(result.message),
        solve_seconds=elapsed,
        mip_gap=None,
        node_count=None,
        iterations=int(result.nit),
    )


def validate_solution(
    solution: DispatchSolution,
    actual_load: np.ndarray,
    actual_pv: np.ndarray,
    price: np.ndarray,
    soc_initial: float,
    delta_h: float,
    eta_charge: float,
    eta_discharge: float,
    reserves: np.ndarray | None,
    require_mutual_exclusion: bool,
) -> dict[str, float | int | bool]:
    grid = solution.grid
    charge = solution.charge
    discharge = solution.discharge
    soc = solution.soc
    n_days = grid.shape[0]
    energy_limit = POWER_LIMIT_KW * delta_h

    balance_value = grid - charge + discharge
    balance_lower = actual_load - actual_pv
    balance_upper = actual_load
    balance_violation = np.maximum(
        np.maximum(balance_lower - balance_value, balance_value - balance_upper), 0.0
    )

    flat_soc = soc.reshape(-1)
    soc_before = np.empty_like(flat_soc)
    soc_before[0] = soc_initial
    soc_before[1:] = flat_soc[:-1]
    recursion = (
        soc_before.reshape(soc.shape)
        + eta_charge * charge
        - discharge / eta_discharge
    )
    objective_check = float(np.sum(grid * np.tile(price, (n_days, 1))))
    simultaneous = int(np.sum((charge > TOL) & (discharge > TOL)))

    reserve_violation = 0.0
    if reserves is not None:
        reserve_violation = float(
            np.max(np.maximum(SOC_MIN + reserves - soc[:, -1], 0.0), initial=0.0)
        )

    checks: dict[str, float | int | bool] = {
        "objective_recalculation_error_yuan": abs(objective_check - solution.objective),
        "max_balance_inequality_violation_kwh": float(np.max(balance_violation)),
        "max_soc_recursion_error_kwh": float(np.max(np.abs(soc - recursion))),
        "max_soc_bound_violation_kwh": float(
            max(
                np.max(np.maximum(SOC_MIN - soc, 0.0)),
                np.max(np.maximum(soc - SOC_MAX, 0.0)),
            )
        ),
        "max_power_bound_violation_kwh": float(
            max(
                np.max(np.maximum(charge - energy_limit, 0.0)),
                np.max(np.maximum(discharge - energy_limit, 0.0)),
            )
        ),
        "max_daily_reserve_violation_kwh": reserve_violation,
        "simultaneous_charge_discharge_periods": simultaneous,
        "mutual_exclusion_required": require_mutual_exclusion,
    }
    critical = [
        checks["objective_recalculation_error_yuan"],
        checks["max_balance_inequality_violation_kwh"],
        checks["max_soc_recursion_error_kwh"],
        checks["max_soc_bound_violation_kwh"],
        checks["max_power_bound_violation_kwh"],
        checks["max_daily_reserve_violation_kwh"],
    ]
    checks["passed"] = bool(max(float(value) for value in critical) <= 1e-4)
    if require_mutual_exclusion:
        checks["passed"] = bool(checks["passed"] and simultaneous == 0)
    return checks


def solution_summary(
    solution: DispatchSolution,
    price: np.ndarray,
    checks: dict[str, float | int | bool],
) -> dict[str, Any]:
    daily_price = np.tile(price, (solution.grid.shape[0], 1))
    return {
        "objective_cost_yuan": solution.objective,
        "recalculated_cost_yuan": float(np.sum(solution.grid * daily_price)),
        "total_grid_kwh": float(np.sum(solution.grid)),
        "total_charge_kwh": float(np.sum(solution.charge)),
        "total_discharge_kwh": float(np.sum(solution.discharge)),
        "ending_soc_kwh": float(solution.soc[-1, -1]),
        "peak_grid_kwh_per_10min": float(np.max(solution.grid)),
        "status": solution.status,
        "message": solution.message,
        "solve_seconds": solution.solve_seconds,
        "mip_gap": solution.mip_gap,
        "node_count": solution.node_count,
        "iterations": solution.iterations,
        "checks": checks,
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_csv_rows(
    solutions: list[DispatchSolution],
    dates: list[date],
    time_labels: list[str],
    price: np.ndarray,
    actual_load: np.ndarray,
    actual_pv: np.ndarray,
    reserves: np.ndarray,
    soc_initial: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    daily_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    for solution in solutions:
        for day_index, current_date in enumerate(dates):
            start_soc = (
                soc_initial
                if day_index == 0
                else float(solution.soc[day_index - 1, -1])
            )
            daily_rows.append(
                {
                    "model": solution.name,
                    "date": current_date.isoformat(),
                    "reserve_constraint_kwh": (
                        float(reserves[day_index])
                        if solution.name == "matched_milp"
                        else 0.0
                    ),
                    "soc_start_kwh": start_soc,
                    "soc_end_kwh": float(solution.soc[day_index, -1]),
                    "grid_kwh": float(np.sum(solution.grid[day_index])),
                    "charge_kwh": float(np.sum(solution.charge[day_index])),
                    "discharge_kwh": float(np.sum(solution.discharge[day_index])),
                    "cost_yuan": float(np.sum(solution.grid[day_index] * price)),
                    "peak_grid_kwh_per_10min": float(np.max(solution.grid[day_index])),
                }
            )
            soc_before = np.concatenate(
                (np.asarray([start_soc]), solution.soc[day_index, :-1])
            )
            curtailment = np.maximum(
                0.0,
                solution.grid[day_index]
                + actual_pv[day_index]
                + solution.discharge[day_index]
                - actual_load[day_index]
                - solution.charge[day_index],
            )
            for period in range(N_TIME):
                detail_rows.append(
                    {
                        "model": solution.name,
                        "date": current_date.isoformat(),
                        "period": period + 1,
                        "time": time_labels[period],
                        "price_yuan_per_kwh": float(price[period]),
                        "actual_load_kwh": float(actual_load[day_index, period]),
                        "actual_pv_kwh": float(actual_pv[day_index, period]),
                        "grid_kwh": float(solution.grid[day_index, period]),
                        "charge_kwh": float(solution.charge[day_index, period]),
                        "discharge_kwh": float(solution.discharge[day_index, period]),
                        "curtailment_kwh": float(curtailment[period]),
                        "soc_before_kwh": float(soc_before[period]),
                        "soc_after_kwh": float(solution.soc[day_index, period]),
                        "cost_yuan": float(solution.grid[day_index, period] * price[period]),
                    }
                )
    return daily_rows, detail_rows


def format_money(value: float) -> str:
    return f"{value:,.2f}"


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    rolling = report["rolling"]
    matched = report["matched_milp"]
    lower = report["lp_lower"]
    gap = report["gaps"]
    lines = [
        "# 第二问完美信息基准结果",
        "",
        "## 计算口径",
        "",
        f"评价区间为 {report['horizon']['start_date']} 至 {report['horizon']['end_date']}，共 {report['horizon']['days']} 天。",
        "",
        "- 滚动方案费用来自主程序每日结果，包含正常购电费与 5 倍电价的实际紧急购电费。",
        "- 匹配完美信息 MILP 使用评价期实际负载和实际光伏，保留相同首日 SOC、充放电效率、容量、功率、充放电互斥及逐日安全储备。",
        "- 完美信息 LP 下界使用相同首日 SOC，取消逐日安全储备并放松充放电互斥，全部日期在一个连续时域内联合求解。",
        "- 两个离线基准均不回填到滚动预测或计划中。",
        "",
        "## 成本与差距",
        "",
        "| 方案 | 正式期成本/元 | 相对指标 |",
        "|---|---:|---:|",
        f"| 滚动方案 | {format_money(rolling['total_cost_yuan'])} | — |",
        f"| 匹配完美信息 MILP | {format_money(matched['objective_cost_yuan'])} | Gap_info = {gap['gap_info_percent']:.4f}% |",
        f"| 完美信息 LP 下界 | {format_money(lower['objective_cost_yuan'])} | Gap_LP = {gap['gap_lp_percent']:.4f}% |",
        "",
        "其中：",
        "",
        "$$Gap_{info}=\\frac{F_{rolling}-F_{perfect}^{MILP,matched}}{F_{perfect}^{MILP,matched}}\\times100\\%,$$",
        "",
        "$$Gap_{LP}=\\frac{F_{rolling}-F_{perfect}^{LP,lower}}{F_{perfect}^{LP,lower}}\\times100\\%.$$",
        "",
        "## 求解与核验",
        "",
        f"- 匹配 MILP：status={matched['status']}，MIP gap={matched['mip_gap']}，耗时 {matched['solve_seconds']:.3f} 秒。",
        f"- LP 下界：status={lower['status']}，迭代次数={lower['iterations']}，耗时 {lower['solve_seconds']:.3f} 秒。",
        f"- 匹配 MILP 物理核验：{'通过' if matched['checks']['passed'] else '未通过'}。",
        f"- LP 下界物理核验：{'通过' if lower['checks']['passed'] else '未通过'}。",
        f"- 成本序关系 LP下界 ≤ 匹配MILP ≤ 滚动费用：{'通过' if report['ordering_check']['passed'] else '未通过'}。",
        "",
        "## 结果文件",
        "",
        "- perfect_information_benchmark.json：汇总、差距、求解状态和核验指标；",
        "- perfect_information_daily.csv：两个基准的逐日调度汇总；",
        "- perfect_information_detail.csv：两个基准的逐10分钟调度结果。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    question_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="第二问完美信息匹配MILP与LP下界")
    parser.add_argument(
        "--input",
        type=Path,
        default=question_dir / "output" / "input_data.json",
    )
    parser.add_argument(
        "--daily-csv",
        type=Path,
        default=question_dir / "output" / "question_two_daily.csv",
    )
    parser.add_argument(
        "--main-json",
        type=Path,
        default=question_dir / "output" / "question_two_solution.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=question_dir / "output" / "benchmarks",
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=None)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None)
    parser.add_argument("--max-days", type=int, default=None)
    parser.add_argument("--mip-gap", type=float, default=1e-6)
    parser.add_argument("--time-limit", type=float, default=300.0)
    parser.add_argument(
        "--skip-detail",
        action="store_true",
        help="不写逐10分钟CSV（调试时可减少磁盘写入）",
    )
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    main_payload = json.loads(args.main_json.read_text(encoding="utf-8"))
    diagnostics = main_payload.get("diagnostics", main_payload)
    daily_source = read_daily_csv(args.daily_csv)
    expected_diagnostics = {
        "soc_min_kwh": SOC_MIN,
        "soc_max_kwh": SOC_MAX,
    }
    for key, expected in expected_diagnostics.items():
        if key in diagnostics and not np.isclose(
            float(diagnostics[key]), expected, rtol=0.0, atol=1e-9
        ):
            raise ValueError(
                f"主方案diagnostics中的{key}={diagnostics[key]}，"
                f"与基准脚本口径{expected}不一致"
            )
    eta_charge = float(diagnostics.get("eta_charge", ETA_C))
    eta_discharge = float(diagnostics.get("eta_discharge", ETA_D))
    if not 0.0 < eta_charge <= 1.0 or not 0.0 < eta_discharge <= 1.0:
        raise ValueError("主方案充、放电效率必须位于(0, 1]区间")
    formal_start = args.start_date or date.fromisoformat(
        diagnostics.get("formal_start", DEFAULT_FORMAL_START.isoformat())
    )
    dates, actual_load, actual_pv, daily_rows = select_horizon(
        payload,
        daily_source,
        formal_start,
        args.end_date,
        args.max_days,
    )

    price = np.asarray(payload["price"], dtype=float)
    time_labels = [str(value) for value in payload["time_labels"]]
    delta_h = float(payload["delta_h"])
    if price.shape != (N_TIME,):
        raise ValueError(f"电价应为{N_TIME}个时段，实际为{price.shape}")
    if actual_load.shape != actual_pv.shape or actual_load.shape[1] != N_TIME:
        raise ValueError("实际负载和光伏数据维度不一致")

    reserves = np.asarray([float(row["reserve_kwh"]) for row in daily_rows])
    soc_initial = float(daily_rows[0]["soc_start_kwh"])
    rolling_cost = float(sum(float(row["actual_total_cost_yuan"]) for row in daily_rows))
    rolling_plan_cost = float(
        sum(float(row.get("planned_cost_yuan", 0.0)) for row in daily_rows)
    )
    rolling_emergency_cost = float(
        sum(float(row.get("emergency_cost_yuan", 0.0)) for row in daily_rows)
    )

    print(
        f"评价期 {dates[0].isoformat()} 至 {dates[-1].isoformat()}，{len(dates)}天；"
        f"共同初始SOC={soc_initial:.4f} kWh",
        flush=True,
    )
    print("求解匹配完美信息MILP...", flush=True)
    matched = solve_matched_milp(
        price,
        actual_load,
        actual_pv,
        reserves,
        soc_initial,
        delta_h,
        eta_charge,
        eta_discharge,
        args.mip_gap,
        args.time_limit,
    )
    print(
        f"匹配MILP完成：{matched.objective:,.2f}元，耗时{matched.solve_seconds:.3f}s",
        flush=True,
    )
    print("求解无储备、松弛互斥的完美信息LP下界...", flush=True)
    lower = solve_lp_lower_bound(
        price,
        actual_load,
        actual_pv,
        soc_initial,
        delta_h,
        eta_charge,
        eta_discharge,
        args.time_limit,
    )
    print(
        f"LP下界完成：{lower.objective:,.2f}元，耗时{lower.solve_seconds:.3f}s",
        flush=True,
    )

    matched_checks = validate_solution(
        matched,
        actual_load,
        actual_pv,
        price,
        soc_initial,
        delta_h,
        eta_charge,
        eta_discharge,
        reserves,
        True,
    )
    lower_checks = validate_solution(
        lower,
        actual_load,
        actual_pv,
        price,
        soc_initial,
        delta_h,
        eta_charge,
        eta_discharge,
        None,
        False,
    )
    if not matched_checks["passed"] or not lower_checks["passed"]:
        raise RuntimeError("基准调度没有通过物理约束核验")

    cost_tolerance = max(0.01, abs(rolling_cost) * 1e-8)
    ordering_passed = bool(
        lower.objective <= matched.objective + cost_tolerance
        and matched.objective <= rolling_cost + cost_tolerance
    )
    if not ordering_passed:
        raise RuntimeError(
            "成本序关系不成立：应满足LP下界 <= 匹配MILP <= 滚动方案费用；"
            f"实际为 {lower.objective}, {matched.objective}, {rolling_cost}"
        )

    gap_info = (rolling_cost - matched.objective) / matched.objective * 100.0
    gap_lp = (rolling_cost - lower.objective) / lower.objective * 100.0
    formal_days = int(diagnostics.get("formal_days", len(dates)))
    diagnostic_start = date.fromisoformat(
        diagnostics.get("formal_start", dates[0].isoformat())
    )
    full_default_horizon = (
        args.max_days is None
        and args.end_date is None
        and dates[0] == diagnostic_start
        and len(dates) == formal_days
    )
    diagnostic_cost_error = None
    if full_default_horizon and "formal_total_cost_yuan" in diagnostics:
        diagnostic_cost_error = abs(
            rolling_cost - float(diagnostics["formal_total_cost_yuan"])
        )
        if diagnostic_cost_error > 0.01:
            raise ValueError(
                "每日CSV汇总费用与主方案diagnostics不一致："
                f"差额={diagnostic_cost_error:.6f}元"
            )

    report: dict[str, Any] = {
        "benchmark": "perfect-information matched MILP and relaxed LP lower bound",
        "horizon": {
            "start_date": dates[0].isoformat(),
            "end_date": dates[-1].isoformat(),
            "days": len(dates),
            "periods": len(dates) * N_TIME,
            "formal_only": True,
            "common_initial_soc_kwh": soc_initial,
        },
        "parameters": {
            "eta_charge": eta_charge,
            "eta_discharge": eta_discharge,
            "soc_min_kwh": SOC_MIN,
            "soc_max_kwh": SOC_MAX,
            "power_limit_kw": POWER_LIMIT_KW,
            "delta_h": delta_h,
            "energy_limit_kwh_per_period": POWER_LIMIT_KW * delta_h,
            "matched_daily_reserve_source": str(args.daily_csv.resolve()),
            "matched_reserve_min_kwh": float(np.min(reserves)),
            "matched_reserve_max_kwh": float(np.max(reserves)),
        },
        "rolling": {
            "total_cost_yuan": rolling_cost,
            "planned_cost_yuan": rolling_plan_cost,
            "emergency_cost_yuan": rolling_emergency_cost,
            "diagnostics_total_cost_error_yuan": diagnostic_cost_error,
        },
        "matched_milp": solution_summary(matched, price, matched_checks),
        "lp_lower": solution_summary(lower, price, lower_checks),
        "gaps": {
            "gap_info_percent": gap_info,
            "gap_lp_percent": gap_lp,
        },
        "ordering_check": {
            "expected": "lp_lower <= matched_milp <= rolling",
            "tolerance_yuan": cost_tolerance,
            "passed": ordering_passed,
        },
        "interpretation": {
            "matched_milp": "仅移除预测误差，保留主方案物理约束和逐日安全储备。",
            "lp_lower": "进一步移除逐日安全储备并放松充放电互斥，构成更宽松的离线理论下界。",
            "information_boundary": "基准在滚动方案完成后离线求解，不向日前预测或计划回填任何结果。",
        },
        "provenance": {
            "input_json": str(args.input.resolve()),
            "daily_csv": str(args.daily_csv.resolve()),
            "main_json": str(args.main_json.resolve()),
            "input_sha256": sha256(args.input),
            "daily_csv_sha256": sha256(args.daily_csv),
            "main_json_sha256": sha256(args.main_json),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
        },
    }

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "perfect_information_benchmark.json"
    daily_path = output_dir / "perfect_information_daily.csv"
    detail_path = output_dir / "perfect_information_detail.csv"
    markdown_path = output_dir / "perfect_information_report.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    output_daily, output_detail = build_csv_rows(
        [matched, lower],
        dates,
        time_labels,
        price,
        actual_load,
        actual_pv,
        reserves,
        soc_initial,
    )
    daily_fields = [
        "model",
        "date",
        "reserve_constraint_kwh",
        "soc_start_kwh",
        "soc_end_kwh",
        "grid_kwh",
        "charge_kwh",
        "discharge_kwh",
        "cost_yuan",
        "peak_grid_kwh_per_10min",
    ]
    detail_fields = [
        "model",
        "date",
        "period",
        "time",
        "price_yuan_per_kwh",
        "actual_load_kwh",
        "actual_pv_kwh",
        "grid_kwh",
        "charge_kwh",
        "discharge_kwh",
        "curtailment_kwh",
        "soc_before_kwh",
        "soc_after_kwh",
        "cost_yuan",
    ]
    write_csv(daily_path, output_daily, daily_fields)
    if not args.skip_detail:
        write_csv(detail_path, output_detail, detail_fields)
    write_markdown(markdown_path, report)

    print(f"Gap_info={gap_info:.4f}%，Gap_LP={gap_lp:.4f}%", flush=True)
    print(f"基准报告已保存: {json_path}", flush=True)


if __name__ == "__main__":
    main()
