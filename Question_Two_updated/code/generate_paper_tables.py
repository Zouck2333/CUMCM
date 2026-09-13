from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


DEFAULT_DATES = (
    "2025-03-20",
    "2025-06-21",
    "2025-09-23",
    "2025-12-21",
)
BLOCK_LABELS = (
    "0:00-4:00",
    "4:00-8:00",
    "8:00-12:00",
    "12:00-16:00",
    "16:00-20:00",
    "20:00-24:00",
)


def read_detail(path: Path) -> dict[str, list[dict[str, str]]]:
    rows_by_date: dict[str, list[dict[str, str]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {
            "date",
            "period",
            "time",
            "planned_grid_kwh",
            "planned_charge_kwh",
            "planned_discharge_kwh",
            "soc_before_kwh",
            "soc_after_kwh",
            "actual_emergency_kwh",
            "planned_cost_yuan",
            "emergency_cost_yuan",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"逐时CSV缺少列: {sorted(missing)}")
        for row in reader:
            rows_by_date[row["date"]].append(row)
    return rows_by_date


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def merge_emergency(
    rows: list[dict[str, str]], threshold: float
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    start: int | None = None
    for index, row in enumerate(rows):
        active = float(row["actual_emergency_kwh"]) > threshold
        if active and start is None:
            start = index
        if start is not None and (not active or index == len(rows) - 1):
            end = index if active and index == len(rows) - 1 else index - 1
            period_start = rows[start]["time"].split("-", 1)[0]
            period_end = rows[end]["time"].split("-", 1)[-1]
            result.append(
                {
                    "紧急购电时段": f"{period_start}-{period_end}",
                    "紧急购电量/kWh": sum(
                        float(item["actual_emergency_kwh"])
                        for item in rows[start : end + 1]
                    ),
                }
            )
            start = None
    return result


def fmt(value: float, digits: int = 4) -> str:
    return f"{value:.{digits}f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="生成第二问四个指定日期的论文结果表")
    parser.add_argument("--solution", type=Path, required=True)
    parser.add_argument("--detail", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dates", nargs="+", default=list(DEFAULT_DATES))
    parser.add_argument("--interval-eps", type=float, default=1e-7)
    args = parser.parse_args()

    solution = json.loads(args.solution.read_text(encoding="utf-8"))
    solution_days = {str(item["date"]): item for item in solution["days"]}
    detail_by_date = read_detail(args.detail)
    requested = [str(value) for value in args.dates]
    missing = [
        value
        for value in requested
        if value not in solution_days or value not in detail_by_date
    ]
    if missing:
        raise ValueError(f"结果中缺少指定日期: {missing}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, object]] = []
    markdown: list[str] = [
        "# 第二问指定日期结果表",
        "",
        "数据直接由 `question_two_solution.json` 和 "
        "`question_two_detail.csv` 自动提取。电量单位为 kWh，费用单位为元。",
        "",
        "## 日结果汇总",
        "",
        "| 日期 | 计划购电量 | 紧急购电量 | 计划购电费 | 紧急购电费 | "
        "实际总费用 | 日初SOC | 日终SOC |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for day_text in requested:
        day = solution_days[day_text]
        rows = detail_by_date[day_text]
        if len(rows) != 144:
            raise ValueError(f"{day_text}应有144个时段，实际为{len(rows)}")
        rows.sort(key=lambda row: int(row["period"]))
        total_grid = sum(float(row["planned_grid_kwh"]) for row in rows)
        total_emergency = sum(float(row["actual_emergency_kwh"]) for row in rows)
        plan_cost = sum(float(row["planned_cost_yuan"]) for row in rows)
        emergency_cost = sum(float(row["emergency_cost_yuan"]) for row in rows)
        summary = {
            "日期": day_text,
            "计划购电量/kWh": total_grid,
            "紧急购电量/kWh": total_emergency,
            "计划购电费/元": plan_cost,
            "紧急购电费/元": emergency_cost,
            "实际总费用/元": plan_cost + emergency_cost,
            "日初SOC/kWh": float(day["soc_start"]),
            "日终SOC/kWh": float(day["soc_end"]),
        }
        summary_rows.append(summary)
        markdown.append(
            f"| {day_text} | {fmt(total_grid)} | {fmt(total_emergency)} | "
            f"{fmt(plan_cost, 2)} | {fmt(emergency_cost, 2)} | "
            f"{fmt(plan_cost + emergency_cost, 2)} | "
            f"{fmt(float(day['soc_start']))} | {fmt(float(day['soc_end']))} |"
        )

    summary_path = args.output_dir / "paper_selected_dates_summary.csv"
    write_csv(summary_path, summary_rows, list(summary_rows[0]))

    for day_text in requested:
        rows = detail_by_date[day_text]
        detail_output: list[dict[str, object]] = []
        for row in rows:
            detail_output.append(
                {
                    "时段序号": int(row["period"]),
                    "时间段": row["time"],
                    "计划购电量/kWh": float(row["planned_grid_kwh"]),
                    "充电量/kWh": float(row["planned_charge_kwh"]),
                    "放电量/kWh": float(row["planned_discharge_kwh"]),
                    "时段初SOC/kWh": float(row["soc_before_kwh"]),
                    "时段末SOC/kWh": float(row["soc_after_kwh"]),
                    "紧急购电量/kWh": float(row["actual_emergency_kwh"]),
                }
            )
        detail_path = args.output_dir / f"paper_{day_text}_144_periods.csv"
        write_csv(detail_path, detail_output, list(detail_output[0]))

        block_rows: list[dict[str, object]] = []
        for block, label in enumerate(BLOCK_LABELS):
            selected = rows[block * 24 : (block + 1) * 24]
            block_rows.append(
                {
                    "时间段": label,
                    "充电量/kWh": sum(
                        float(row["planned_charge_kwh"]) for row in selected
                    ),
                    "放电量/kWh": sum(
                        float(row["planned_discharge_kwh"]) for row in selected
                    ),
                }
            )
        block_path = args.output_dir / f"paper_{day_text}_battery_4h.csv"
        write_csv(block_path, block_rows, list(block_rows[0]))

        emergency_rows = merge_emergency(rows, args.interval_eps)
        emergency_path = args.output_dir / f"paper_{day_text}_emergency_intervals.csv"
        if emergency_rows:
            write_csv(emergency_path, emergency_rows, list(emergency_rows[0]))
        elif emergency_path.exists():
            # A refreshed zero-emergency day must not retain a previous version's intervals.
            emergency_path.unlink()

        markdown.extend(
            [
                "",
                f"## {day_text}",
                "",
                "### 储能设备四小时汇总",
                "",
                "| 时间段 | 充电量/kWh | 放电量/kWh |",
                "|---|---:|---:|",
            ]
        )
        for row in block_rows:
            markdown.append(
                f"| {row['时间段']} | {fmt(float(row['充电量/kWh']))} | "
                f"{fmt(float(row['放电量/kWh']))} |"
            )
        markdown.extend(["", "### 紧急购电连续区间", ""])
        if emergency_rows:
            markdown.extend(
                [
                    "| 紧急购电时段 | 紧急购电量/kWh |",
                    "|---|---:|",
                ]
            )
            for row in emergency_rows:
                markdown.append(
                    f"| {row['紧急购电时段']} | "
                    f"{fmt(float(row['紧急购电量/kWh']))} |"
                )
        else:
            markdown.append("该日没有紧急购电。")
        markdown.extend(
            [
                "",
                f"该日144个时段的购电、充放电、SOC及紧急购电明细见 "
                f"`{detail_path.name}`。",
            ]
        )

    markdown_path = args.output_dir / "paper_selected_dates.md"
    markdown_path.write_text("\n".join(markdown) + "\n", encoding="utf-8")
    print(f"已生成论文指定日期结果表: {markdown_path}")


if __name__ == "__main__":
    main()
