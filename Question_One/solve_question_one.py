from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
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


def solve_with_milp(
    df: pd.DataFrame,
    mip_gap: float = 1.0e-8,
    time_limit: float | None = None,
    solver_python: Path | None = None,
) -> tuple[pd.DataFrame, dict[str, float | int | str]]:
    """Solve the day-ahead dispatch as a mixed-integer linear program.

    Variables in every period are grid purchase, battery charge, battery
    discharge, curtailed PV, ending SOC and a binary charge-mode indicator.
    """
    n_time = len(df)
    price = df["电价"].to_numpy(dtype=float)
    load = df["负载电量"].to_numpy(dtype=float)
    pv = df["光伏电量"].to_numpy(dtype=float)

    worker = Path(__file__).resolve().parent / "milp_solver_worker.py"
    solver_executable = find_solver_python(solver_python)
    payload = {
        "price": price.tolist(),
        "load": load.tolist(),
        "pv": pv.tolist(),
        "eta": ETA,
        "soc_min": SOC_MIN,
        "soc_max": SOC_MAX,
        "soc_initial": SOC_INITIAL,
        "energy_limit": ENERGY_LIMIT,
        "mip_gap": mip_gap,
        "time_limit": time_limit,
    }
    with tempfile.TemporaryDirectory(prefix="cumcm_milp_") as temp_dir:
        input_json = Path(temp_dir) / "input.json"
        output_json = Path(temp_dir) / "output.json"
        input_json.write_text(json.dumps(payload), encoding="utf-8")
        completed = subprocess.run(
            [str(solver_executable), str(worker), str(input_json), str(output_json)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"MILP求解进程失败: {message}")
        solved = json.loads(output_json.read_text(encoding="utf-8"))

    grid_values = np.maximum(0.0, np.asarray(solved["grid"], dtype=float))
    charge_values = np.maximum(0.0, np.asarray(solved["charge"], dtype=float))
    discharge_values = np.maximum(0.0, np.asarray(solved["discharge"], dtype=float))
    curtail_values = np.maximum(0.0, np.asarray(solved["curtailment"], dtype=float))
    soc_values = np.asarray(solved["soc"], dtype=float)

    records = []
    for t in range(n_time):
        e_before = SOC_INITIAL if t == 0 else soc_values[t - 1]
        records.append(
            {
                "时段": t + 1,
                "时间": df.iloc[t]["时间"],
                "电价": price[t],
                "负载电量_kWh": load[t],
                "光伏电量_kWh": pv[t],
                "计划购电量_kWh": grid_values[t],
                "充电量_kWh": charge_values[t],
                "放电量_kWh": discharge_values[t],
                "弃光或富余电量_kWh": curtail_values[t],
                "时段初储电量_kWh": e_before,
                "时段末储电量_kWh": soc_values[t],
                "购电费用": price[t] * grid_values[t],
            }
        )
    solver_info: dict[str, float | int | str] = {
        "solver": "SciPy milp / HiGHS",
        "solver_python": str(solver_executable),
        "solver_status": int(solved["status"]),
        "solver_message": str(solved["message"]),
        "mip_gap": float(solved["mip_gap"]),
        "mip_node_count": int(solved["mip_node_count"]),
    }
    return pd.DataFrame(records), solver_info


def find_solver_python(requested: Path | None) -> Path:
    candidates: list[Path] = []
    if requested is not None:
        candidates.append(requested)
    candidates.append(Path(sys.executable))
    path_python = shutil.which("python")
    if path_python:
        candidates.append(Path(path_python))

    checked: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve())
        if key in checked or not candidate.exists():
            continue
        checked.add(key)
        probe = subprocess.run(
            [str(candidate), "-c", "from scipy.optimize import milp"],
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return candidate.resolve()
    raise RuntimeError(
        "未找到支持 scipy.optimize.milp 的 Python。请通过 --solver-python 指定求解器环境。"
    )


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
    power_violations = int(
        (
            (result["充电量_kWh"] > ENERGY_LIMIT + 1e-7)
            | (result["放电量_kWh"] > ENERGY_LIMIT + 1e-7)
        ).sum()
    )
    curtailment_violations = int(
        (
            (result["弃光或富余电量_kWh"] < -1e-7)
            | (result["弃光或富余电量_kWh"] > result["光伏电量_kWh"] + 1e-7)
        ).sum()
    )
    return {
        "total_cost": float(result["购电费用"].sum()),
        "total_grid_kwh": float(result["计划购电量_kWh"].sum()),
        "total_charge_kwh": float(result["充电量_kWh"].sum()),
        "total_discharge_kwh": float(result["放电量_kWh"].sum()),
        "max_charge_kwh": float(result["充电量_kWh"].max()),
        "max_discharge_kwh": float(result["放电量_kWh"].max()),
        "max_balance_error": float(balance),
        "max_soc_error": float(soc_next),
        "initial_soc": float(result.iloc[0]["时段初储电量_kWh"]),
        "terminal_soc": float(result.iloc[-1]["时段末储电量_kWh"]),
        "min_soc": float(result["时段末储电量_kWh"].min()),
        "max_soc": float(result["时段末储电量_kWh"].max()),
        "power_violations": power_violations,
        "curtailment_violations": curtailment_violations,
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
    parser = argparse.ArgumentParser(description="C题第一问MILP储能调度求解程序")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    parser.add_argument("--mip-gap", type=float, default=1.0e-8, help="MILP相对最优间隙")
    parser.add_argument("--time-limit", type=float, default=None, help="求解时间上限，单位秒")
    parser.add_argument("--solver-python", type=Path, default=None, help="安装有SciPy的Python路径")
    args = parser.parse_args()

    df = load_day_data(args.input)
    result, solver_info = solve_with_milp(
        df, args.mip_gap, args.time_limit, args.solver_python
    )
    checks = validate_solution(result)

    args.detail.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.detail, index=False, encoding="utf-8-sig")
    fill_template(args.template, args.output, result)

    print("C题第一问MILP求解完成")
    print(f"输出模板: {args.output}")
    print(f"详细结果: {args.detail}")
    for key, value in solver_info.items():
        print(f"{key}: {value}")
    for key, value in checks.items():
        if isinstance(value, float):
            print(f"{key}: {value:.6f}")
        else:
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()
