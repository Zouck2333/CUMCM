from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path


FORMAL_START = "2025-02-01"
DAILY_METRICS = (
    ("planned_grid_kwh", "计划购电量 (kWh)"),
    ("actual_emergency_kwh", "紧急购电量 (kWh)"),
    ("planned_cost_yuan", "计划购电费用 (元)"),
    ("emergency_cost_yuan", "紧急购电费用 (元)"),
    ("actual_total_cost_yuan", "实际总费用 (元)"),
)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def as_number(row: dict[str, str], field: str) -> float:
    value = float(row[field])
    if not math.isfinite(value):
        raise ValueError(f"{field} contains a non-finite value")
    return value


def compare_inputs(old: dict[str, str], new: dict[str, str]) -> None:
    if (old["date"], old["period"]) != (new["date"], new["period"]):
        raise ValueError("Detail dates or periods differ between runs")
    for field in ("actual_load_kwh", "actual_pv_kwh", "price_yuan_per_kwh"):
        if not math.isclose(as_number(old, field), as_number(new, field), abs_tol=1e-8):
            raise ValueError(f"Input mismatch at {old['date']} period {old['period']}: {field}")


def forecast_stats(rows: list[dict[str, str]]) -> dict[str, float]:
    stats: dict[str, float] = defaultdict(float)
    for row in rows:
        load_error = as_number(row, "actual_load_kwh") - as_number(row, "predicted_load_kwh")
        pv_error = as_number(row, "actual_pv_kwh") - as_number(row, "predicted_pv_kwh")
        net_error = load_error - pv_error
        for name, error in (("load", load_error), ("pv", pv_error), ("net", net_error)):
            stats[f"{name}_abs"] += abs(error)
            stats[f"{name}_sq"] += error * error
        stats["net_under"] += max(0.0, net_error)
        stats["emergency_periods"] += as_number(row, "actual_emergency_kwh") > 1e-7
        stats["detail_plan_cost"] += as_number(row, "planned_cost_yuan")
        stats["detail_emergency_cost"] += as_number(row, "emergency_cost_yuan")
    stats["count"] = len(rows)
    return stats


def daily_totals(rows: list[dict[str, str]]) -> dict[str, float]:
    return {field: sum(as_number(row, field) for row in rows) for field, _ in DAILY_METRICS}


def row(values: tuple[str, float, float], *, percent: bool = True) -> str:
    label, old, new = values
    change = new - old
    rate = f"{change / old:+.2%}" if percent and abs(old) > 1e-12 else "—"
    return f"| {label} | {old:,.2f} | {new:,.2f} | {change:+,.2f} | {rate} |"


def build_report(baseline: Path, optimized: Path) -> str:
    old_diagnostics = json.loads((baseline / "question_two_solution.json").read_text(encoding="utf-8"))["diagnostics"]
    new_diagnostics = json.loads((optimized / "question_two_solution.json").read_text(encoding="utf-8"))["diagnostics"]
    for field in (
        "days_solved",
        "formal_days",
        "eta_charge",
        "eta_discharge",
        "reserve_quantile",
        "reserve_mode",
        "reserve_quantile_method",
        "objective_mode",
    ):
        if old_diagnostics[field] != new_diagnostics[field]:
            raise ValueError(f"Run settings differ: {field}")
    if old_diagnostics["days_solved"] != 365 or old_diagnostics["formal_days"] != 334:
        raise ValueError("Expected a complete 365-day run")

    old_daily = read_rows(baseline / "question_two_daily.csv")
    new_daily = read_rows(optimized / "question_two_daily.csv")
    old_detail = read_rows(baseline / "question_two_detail.csv")
    new_detail = read_rows(optimized / "question_two_detail.csv")
    if len(old_daily) != len(new_daily) or len(old_detail) != len(new_detail):
        raise ValueError("Run lengths differ")
    if len({item["date"] for item in old_daily}) != len(old_daily):
        raise ValueError("Baseline daily dates are not unique")
    if len({item["date"] for item in new_daily}) != len(new_daily):
        raise ValueError("Optimized daily dates are not unique")

    for old, new in zip(old_daily, new_daily, strict=True):
        if (old["date"], old["warmup"]) != (new["date"], new["warmup"]):
            raise ValueError("Daily dates or warmup flags differ")
        if old["solver_status"] != "0" or new["solver_status"] != "0":
            raise ValueError(f"Solver did not succeed on {old['date']}")
        if old["warmup"] == "True":
            for field in ("actual_total_cost_yuan", "soc_end_kwh"):
                if not math.isclose(as_number(old, field), as_number(new, field), abs_tol=1e-6):
                    raise ValueError(f"Warmup differs on {old['date']}: {field}")
    for old, new in zip(old_detail, new_detail, strict=True):
        compare_inputs(old, new)

    old_formal = [item for item in old_daily if item["date"] >= FORMAL_START and item["warmup"] == "False"]
    new_formal = [item for item in new_daily if item["date"] >= FORMAL_START and item["warmup"] == "False"]
    if len(old_formal) != 334 or len(new_formal) != 334 or len(old_detail) != 334 * 144:
        raise ValueError("Expected 334 formal days and 144 intervals per day")

    old_totals = daily_totals(old_formal)
    new_totals = daily_totals(new_formal)
    old_forecast = forecast_stats(old_detail)
    new_forecast = forecast_stats(new_detail)
    for totals, forecast in ((old_totals, old_forecast), (new_totals, new_forecast)):
        for field, detail_field in (
            ("planned_cost_yuan", "detail_plan_cost"),
            ("emergency_cost_yuan", "detail_emergency_cost"),
        ):
            if not math.isclose(totals[field], forecast[detail_field], abs_tol=1e-4):
                raise ValueError(f"Daily and detail totals disagree: {field}")
        if not math.isclose(
            totals["actual_total_cost_yuan"],
            totals["planned_cost_yuan"] + totals["emergency_cost_yuan"],
            abs_tol=1e-4,
        ):
            raise ValueError("Total cost does not reconcile")

    lines = [
        "# 第二问预测模块前后对比（2025年）",
        "",
        "同一份 `input_data.json`，同一套求解参数；原模型使用已有正式输出，修改后模型重新运行365天。",
        "评价区间为2025-02-01至2025-12-31，共334天、48,096个10分钟时段。1月仅用于冷启动和滚动状态。",
        "两版逐时实际负荷、实际光伏和电价一致，逐时费用与逐日费用已经核对。",
        "1月预热期的每日费用与日终SOC也完全一致；两版365天求解状态均为成功。",
        "",
        "## 参数变化",
        "",
        "| 月份 | 原模型负荷 K/ρ | 修改后负荷 K/ρ | 原模型光伏 K/ρ | 修改后光伏 K/ρ |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    old_parameters = old_diagnostics["monthly_parameters"]
    new_parameters = new_diagnostics["monthly_parameters"]
    if [item["month"] for item in old_parameters] != [item["month"] for item in new_parameters]:
        raise ValueError("Monthly parameter coverage differs")
    changed_parameters = []
    for old, new in zip(old_parameters, new_parameters, strict=True):
        fields = ("k_load", "rho_load", "k_pv", "rho_pv")
        if any(old[field] != new[field] for field in fields):
            changed_parameters.append((old, new))
    for old, new in changed_parameters:
        lines.append(
            f"| {old['month']} | {old['k_load']}/{old['rho_load']:.2f} | "
            f"{new['k_load']}/{new['rho_load']:.2f} | {old['k_pv']}/{old['rho_pv']:.2f} | "
            f"{new['k_pv']}/{new['rho_pv']:.2f} |"
        )
    if not changed_parameters:
        lines.append("| 无 | — | — | — | — |")
    lines.extend([
        "",
        "其余月份的参数选择相同；前期状态和情景变化仍可影响后续调度。",
        "",
        "## 预测误差",
        "",
        "| 指标 | 原模型 | 修改后 | 变化 | 变化率 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    count = old_forecast["count"]
    for name, label in (("load", "负荷MAE (kWh/时段)"), ("pv", "光伏MAE (kWh/时段)"), ("net", "净负荷MAE (kWh/时段)")):
        lines.append(row((label, old_forecast[f"{name}_abs"] / count, new_forecast[f"{name}_abs"] / count)))
    for name, label in (("load", "负荷RMSE (kWh/时段)"), ("pv", "光伏RMSE (kWh/时段)"), ("net", "净负荷RMSE (kWh/时段)")):
        lines.append(row((label, math.sqrt(old_forecast[f"{name}_sq"] / count), math.sqrt(new_forecast[f"{name}_sq"] / count))))
    lines.append(row(("净负荷低估量均值 (kWh/时段)", old_forecast["net_under"] / count, new_forecast["net_under"] / count)))
    lines.extend([
        "",
        "净负荷 = 负荷 - 光伏；净负荷低估量按 `max(实际净负荷 - 预测净负荷, 0)` 计算。",
        "",
        "## 购电与费用",
        "",
        "| 指标 | 原模型 | 修改后 | 变化 | 变化率 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for field, label in DAILY_METRICS:
        lines.append(row((label, old_totals[field], new_totals[field])))
    old_periods = int(old_forecast["emergency_periods"])
    new_periods = int(new_forecast["emergency_periods"])
    lines.append(
        f"| 紧急购电时段数 | {old_periods:,} | {new_periods:,} | "
        f"{new_periods - old_periods:+,} | {(new_periods - old_periods) / old_periods:+.2%} |"
    )

    lines.extend([
        "",
        "## 逐月变化",
        "",
        "| 月份 | 紧急购电量变化 (kWh) | 紧急购电费变化 (元) | 总费用变化 (元) |",
        "| --- | ---: | ---: | ---: |",
    ])
    for month in sorted({item["date"][:7] for item in old_formal}):
        old_month = daily_totals([item for item in old_formal if item["date"].startswith(month)])
        new_month = daily_totals([item for item in new_formal if item["date"].startswith(month)])
        lines.append(
            f"| {month} | {new_month['actual_emergency_kwh'] - old_month['actual_emergency_kwh']:+,.2f} | "
            f"{new_month['emergency_cost_yuan'] - old_month['emergency_cost_yuan']:+,.2f} | "
            f"{new_month['actual_total_cost_yuan'] - old_month['actual_total_cost_yuan']:+,.2f} |"
        )

    daily_changes = [
        (item["date"], as_number(new, "actual_total_cost_yuan") - as_number(item, "actual_total_cost_yuan"))
        for item, new in zip(old_formal, new_formal, strict=True)
    ]
    improved = sum(change < -1e-6 for _, change in daily_changes)
    worsened = sum(change > 1e-6 for _, change in daily_changes)
    lines.extend([
        "",
        f"逐日总费用降低 {improved} 天、上升 {worsened} 天、基本持平 {334 - improved - worsened} 天。",
        "",
        "| 费用下降最多的日期 | 变化 (元) | 费用上升最多的日期 | 变化 (元) |",
        "| --- | ---: | --- | ---: |",
    ])
    for lower, higher in zip(sorted(daily_changes, key=lambda item: item[1])[:5], sorted(daily_changes, key=lambda item: item[1], reverse=True)[:5], strict=True):
        lines.append(f"| {lower[0]} | {lower[1]:+,.2f} | {higher[0]} | {higher[1]:+,.2f} |")

    emergency_delta = new_totals["emergency_cost_yuan"] - old_totals["emergency_cost_yuan"]
    total_delta = new_totals["actual_total_cost_yuan"] - old_totals["actual_total_cost_yuan"]
    lines.extend([
        "",
        "## 解释",
        "",
        f"紧急购电费用{'下降' if emergency_delta < 0 else '上升'} {abs(emergency_delta):,.2f} 元；"
        f"实际总费用{'下降' if total_delta < 0 else '上升'} {abs(total_delta):,.2f} 元。",
        "此次修改仅改变负荷低估和光伏高估的月度参数验证损失。历史偏差修正目前关闭，"
        "因此结果不能归因于偏差校正；优化目标与实际总费用之间仍有权衡。",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare the original and revised Question Two forecasts")
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--optimized-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.baseline_dir, args.optimized_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
