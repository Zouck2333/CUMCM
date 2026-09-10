from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "C题" / "附件" / "附件1.xlsx"
DEFAULT_TEMPLATE = ROOT / "C题" / "附件" / "附件5" / "result1.xlsx"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "result1_question_one.xlsx"
DEFAULT_DETAIL = Path(__file__).resolve().parent / "question_one_detail.csv"

DELTA_H = 1.0 / 6.0
ETA = 0.9
SOC_MIN = 1200.0
SOC_MAX = 10800.0
SOC_INITIAL = 6000.0
POWER_LIMIT_KW = 5000.0
ENERGY_LIMIT = POWER_LIMIT_KW * DELTA_H


def load_day_data(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    required = ["时间", "电价", "小区负载", "光伏发电预测功率"]
    missing = [name for name in required if name not in df.columns]
    if missing:
        raise ValueError(f"附件1缺少列: {missing}")
    if len(df) != 144:
        raise ValueError(f"附件1应有144个时段，实际为{len(df)}个")

    out = df[required].copy()
    out["电价"] = pd.to_numeric(out["电价"], errors="raise")
    out["小区负载"] = pd.to_numeric(out["小区负载"], errors="raise")
    out["光伏发电预测功率"] = pd.to_numeric(out["光伏发电预测功率"], errors="raise")
    out["负载电量"] = out["小区负载"] * DELTA_H
    out["光伏电量"] = out["光伏发电预测功率"] * DELTA_H
    return out


def build_soc_grid(step_kwh: float) -> np.ndarray:
    if step_kwh <= 0:
        raise ValueError("SOC网格步长必须为正数")
    states = np.arange(SOC_MIN, SOC_MAX + step_kwh * 0.5, step_kwh)
    if not np.any(np.isclose(states, SOC_INITIAL)):
        states = np.sort(np.append(states, SOC_INITIAL))
    return states.astype(float)


def solve_with_dynamic_programming(df: pd.DataFrame, step_kwh: float) -> pd.DataFrame:
    states = build_soc_grid(step_kwh)
    n_state = len(states)
    n_time = len(df)
    start_idx = int(np.where(np.isclose(states, SOC_INITIAL))[0][0])

    price = df["电价"].to_numpy(dtype=float)
    load = df["负载电量"].to_numpy(dtype=float)
    pv = df["光伏电量"].to_numpy(dtype=float)

    inf = 1.0e100
    dp = np.full((n_time + 1, n_state), inf)
    parent = np.full((n_time + 1, n_state), -1, dtype=int)
    dp[0, start_idx] = 0.0

    for t in range(n_time):
        reachable = np.where(np.isfinite(dp[t]))[0]
        for i in reachable:
            current_soc = states[i]
            min_next = max(SOC_MIN, current_soc - ETA * ENERGY_LIMIT)
            max_next = min(SOC_MAX, current_soc + ETA * ENERGY_LIMIT)
            candidates = np.where((states >= min_next - 1e-9) & (states <= max_next + 1e-9))[0]
            for j in candidates:
                diff = states[j] - current_soc
                if diff >= -1e-9:
                    charge = max(0.0, diff / ETA)
                    discharge = 0.0
                else:
                    charge = 0.0
                    discharge = -diff * ETA

                if charge > ENERGY_LIMIT + 1e-9 or discharge > ENERGY_LIMIT + 1e-9:
                    continue

                grid = max(0.0, load[t] + charge - pv[t] - discharge)
                cost = price[t] * grid
                value = dp[t, i] + cost
                if value < dp[t + 1, j] - 1e-9:
                    dp[t + 1, j] = value
                    parent[t + 1, j] = i

    if not np.isfinite(dp[n_time, start_idx]):
        raise RuntimeError("未找到满足首尾SOC一致的可行调度方案")

    state_path = [start_idx]
    idx = start_idx
    for t in range(n_time, 0, -1):
        prev = parent[t, idx]
        if prev < 0:
            raise RuntimeError("动态规划回溯失败")
        state_path.append(prev)
        idx = prev
    state_path = list(reversed(state_path))

    records = []
    for t in range(n_time):
        e_before = states[state_path[t]]
        e_after = states[state_path[t + 1]]
        diff = e_after - e_before
        if diff >= -1e-9:
            charge = max(0.0, diff / ETA)
            discharge = 0.0
        else:
            charge = 0.0
            discharge = -diff * ETA
        grid = max(0.0, load[t] + charge - pv[t] - discharge)
        curtailment = grid + pv[t] + discharge - load[t] - charge
        records.append(
            {
                "时段": t + 1,
                "时间": df.iloc[t]["时间"],
                "电价": price[t],
                "负载电量_kWh": load[t],
                "光伏电量_kWh": pv[t],
                "计划购电量_kWh": grid,
                "充电量_kWh": charge,
                "放电量_kWh": discharge,
                "弃光或富余电量_kWh": max(0.0, curtailment),
                "时段初储电量_kWh": e_before,
                "时段末储电量_kWh": e_after,
                "购电费用": price[t] * grid,
            }
        )

    return pd.DataFrame(records)


def validate_solution(result: pd.DataFrame) -> dict[str, float]:
    balance = (
        result["计划购电量_kWh"]
        + result["光伏电量_kWh"]
        + result["放电量_kWh"]
        - result["负载电量_kWh"]
        - result["充电量_kWh"]
        - result["弃光或富余电量_kWh"]
    ).abs().max()
    soc_next = (
        result["时段初储电量_kWh"]
        + ETA * result["充电量_kWh"]
        - result["放电量_kWh"] / ETA
        - result["时段末储电量_kWh"]
    ).abs().max()
    simultaneous = int(((result["充电量_kWh"] > 1e-7) & (result["放电量_kWh"] > 1e-7)).sum())
    return {
        "total_cost": float(result["购电费用"].sum()),
        "total_grid_kwh": float(result["计划购电量_kWh"].sum()),
        "total_charge_kwh": float(result["充电量_kWh"].sum()),
        "total_discharge_kwh": float(result["放电量_kWh"].sum()),
        "max_balance_error": float(balance),
        "max_soc_error": float(soc_next),
        "min_soc": float(result["时段末储电量_kWh"].min()),
        "max_soc": float(result["时段末储电量_kWh"].max()),
        "simultaneous_charge_discharge_periods": simultaneous,
    }


def fill_template(template_path: Path, output_path: Path, result: pd.DataFrame) -> None:
    wb = openpyxl.load_workbook(template_path)

    ws_grid = wb["计划购电量"]
    for row_idx, value in enumerate(result["计划购电量_kWh"], start=2):
        ws_grid.cell(row=row_idx, column=2).value = round(float(value), 4)

    ws_battery = wb["充放电量"]
    for block in range(6):
        start = block * 24
        stop = start + 24
        ws_battery.cell(row=block + 2, column=2).value = round(
            float(result.iloc[start:stop]["充电量_kWh"].sum()), 4
        )
        ws_battery.cell(row=block + 2, column=3).value = round(
            float(result.iloc[start:stop]["放电量_kWh"].sum()), 4
        )

    ws_battery.cell(row=2, column=5).value = SOC_INITIAL
    ws_battery.cell(row=3, column=5).value = SOC_INITIAL

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="C题第一问储能调度求解程序")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    parser.add_argument("--soc-step", type=float, default=10.0, help="SOC动态规划网格步长，单位kWh")
    args = parser.parse_args()

    df = load_day_data(args.input)
    result = solve_with_dynamic_programming(df, args.soc_step)
    checks = validate_solution(result)

    args.detail.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.detail, index=False, encoding="utf-8-sig")
    fill_template(args.template, args.output, result)

    print("C题第一问求解完成")
    print(f"输出模板: {args.output}")
    print(f"详细结果: {args.detail}")
    for key, value in checks.items():
        if isinstance(value, float):
            print(f"{key}: {value:.6f}")
        else:
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()
