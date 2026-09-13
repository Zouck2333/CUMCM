from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np

from solve_question_two import SOC_MAX, SOC_MIN, run_model


CONFIGURATIONS = (
    {
        "case": "no_reserve_eta_090",
        "description": "不设日末安全储备，充放电效率均为0.90",
        "reserve_quantile": 0.0,
        "reserve_mode": "positive_steps",
        "eta_charge": 0.90,
        "eta_discharge": 0.90,
    },
    {
        "case": "q075_eta_090",
        "description": "安全储备取75%分位数，充放电效率均为0.90",
        "reserve_quantile": 0.75,
        "reserve_mode": "positive_steps",
        "eta_charge": 0.90,
        "eta_discharge": 0.90,
    },
    {
        "case": "q090_eta_090",
        "description": "参照方案：安全储备取90%分位数，充放电效率均为0.90",
        "reserve_quantile": 0.90,
        "reserve_mode": "positive_steps",
        "eta_charge": 0.90,
        "eta_discharge": 0.90,
    },
    {
        "case": "q090_eta_sqrt090",
        "description": "安全储备取90%分位数，往返效率为0.90",
        "reserve_quantile": 0.90,
        "reserve_mode": "positive_steps",
        "eta_charge": math.sqrt(0.90),
        "eta_discharge": math.sqrt(0.90),
    },
    {
        "case": "q090_eta090_cumulative_net",
        "description": "安全储备按累计净误差计算，90%分位数，效率均为0.90",
        "reserve_quantile": 0.90,
        "reserve_mode": "cumulative_net",
        "eta_charge": 0.90,
        "eta_discharge": 0.90,
    },
)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(
    path: Path,
    rows: list[dict[str, object]],
    objective_mode: str,
) -> None:
    lines = [
        "# 第二问安全储备与储能效率对照结果",
        "",
        "所有方案使用相同的滚动预测、购电风险策略、实际结算规则和目标层级。费用差额以本表90%储备方案为参照。",
        f"本次批处理目标模式为 `{objective_mode}`；正式对照默认采用与主模型相同的三级词典序求解。",
        "",
        "| 方案 | 储备分位数 | 储备算法 | 充电效率 | 放电效率 | 正式期总费用/元 | 相对90%储备费用/元 | 紧急购电量/kWh | 平均储备/kWh | 储备封顶天数 |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {case} | {reserve_quantile:.2f} | {reserve_mode} | "
            "{eta_charge:.6f} | {eta_discharge:.6f} | "
            "{formal_total_cost_yuan:.2f} | {cost_difference_vs_main_yuan:.2f} | "
            "{formal_total_emergency_kwh:.2f} | "
            "{formal_average_reserve_kwh:.2f} | {formal_reserve_cap_days} |".format(**row)
        )
    if any(bool(row.get("tolerance_relaxed_occurred", False)) for row in rows):
        lines.extend(
            [
                "",
                "数值说明：至少一个方案触发了预设的词典序数值容差重试，具体次数已记录在JSON中。",
            ]
        )
    lines.extend(["", "## 方案说明", ""])
    descriptions = {str(config["case"]): str(config["description"]) for config in CONFIGURATIONS}
    for row in rows:
        lines.append(f"- `{row['case']}`：{descriptions[str(row['case'])]}。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="第二问批量安全储备与效率对照")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mip-gap", type=float, default=1e-6)
    parser.add_argument("--time-limit", type=float, default=30.0)
    parser.add_argument("--max-days", type=int, default=None)
    parser.add_argument("--purchase-strategy", choices=("legacy_scenarios", "calibrated_quantile"), default="calibrated_quantile")
    parser.add_argument("--risk-window-days", type=int, default=28)
    parser.add_argument("--risk-radius-periods", type=int, default=3)
    parser.add_argument("--purchase-quantile", type=float, default=.85)
    parser.add_argument("--forecast-method", choices=("similar_day", "calendar_trend"), default="calendar_trend")
    parser.add_argument("--load-window-days", type=int, default=28)
    parser.add_argument("--load-trend-degree", type=int, choices=(1,2), default=2)
    parser.add_argument("--pv-window-days", type=int, default=14)
    parser.add_argument("--risk-grouping", choices=("all", "legacy"), default="all")
    parser.add_argument(
        "--objective-mode",
        choices=("cost", "lexicographic"),
        default="lexicographic",
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=tuple(str(item["case"]) for item in CONFIGURATIONS),
        default=None,
    )
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    configurations = [
        config
        for config in CONFIGURATIONS
        if args.cases is None or config["case"] in args.cases
    ]
    for index, config in enumerate(configurations, start=1):
        print(
            f"[{index}/{len(configurations)}] 对照方案 {config['case']}",
            flush=True,
        )
        solution, daily_rows, _ = run_model(
            payload,
            mip_gap=args.mip_gap,
            time_limit=args.time_limit,
            max_days=args.max_days,
            eta_charge=float(config["eta_charge"]),
            eta_discharge=float(config["eta_discharge"]),
            reserve_quantile=float(config["reserve_quantile"]),
            reserve_mode=str(config["reserve_mode"]),
            quantile_method="linear",
            objective_mode=args.objective_mode,
            purchase_strategy=args.purchase_strategy,
            risk_window_days=args.risk_window_days,
            risk_radius_periods=args.risk_radius_periods,
            purchase_quantile=args.purchase_quantile,
            forecast_method=args.forecast_method,
            load_window_days=args.load_window_days,
            load_trend_degree=args.load_trend_degree,
            pv_window_days=args.pv_window_days,
            risk_grouping=args.risk_grouping,
            parameter_mode="fixed",
            collect_detail=False,
            collect_formal_days=False,
        )
        diagnostics = solution["diagnostics"]
        formal_rows = [row for row in daily_rows if not bool(row["warmup"])]
        reserves = np.asarray([float(row["reserve_kwh"]) for row in formal_rows])
        rows.append(
            {
                "case": config["case"],
                "description": config["description"],
                "objective_mode": args.objective_mode,
                "purchase_strategy": args.purchase_strategy,
                "risk_window_days": args.risk_window_days,
                "risk_radius_periods": args.risk_radius_periods,
                "purchase_quantile": args.purchase_quantile,
                "forecast_method": args.forecast_method,
                "load_window_days": args.load_window_days,
                "load_trend_degree": args.load_trend_degree,
                "pv_window_days": args.pv_window_days,
                "risk_grouping": args.risk_grouping,
                "reserve_quantile": config["reserve_quantile"],
                "reserve_mode": config["reserve_mode"],
                "eta_charge": config["eta_charge"],
                "eta_discharge": config["eta_discharge"],
                "round_trip_efficiency": float(config["eta_charge"])
                * float(config["eta_discharge"]),
                "formal_days": diagnostics["formal_days"],
                "formal_total_grid_kwh": diagnostics["formal_total_grid_kwh"],
                "formal_plan_cost_yuan": diagnostics["formal_plan_cost_yuan"],
                "formal_emergency_cost_yuan": diagnostics["formal_emergency_cost_yuan"],
                "formal_total_cost_yuan": diagnostics["formal_total_cost_yuan"],
                "formal_total_emergency_kwh": diagnostics[
                    "formal_total_emergency_kwh"
                ],
                "formal_average_reserve_kwh": float(np.mean(reserves))
                if reserves.size
                else 0.0,
                "formal_max_reserve_kwh": float(np.max(reserves))
                if reserves.size
                else 0.0,
                "formal_reserve_cap_days": int(
                    np.sum(reserves >= (SOC_MAX - SOC_MIN) - 1e-6)
                ),
                "total_solver_seconds": float(
                    sum(float(row["solver_seconds"]) for row in daily_rows)
                ),
                "lexicographic_tolerance_relaxed_days": int(
                    sum(
                        str(row.get("lexicographic_tolerance_relaxed", "False")).lower()
                        == "true"
                        for row in daily_rows
                    )
                ),
                "tolerance_relaxed_occurred": any(
                    str(row.get("lexicographic_tolerance_relaxed", "False")).lower()
                    == "true"
                    for row in daily_rows
                ),
            }
        )
        main_candidates = [row for row in rows if row["case"] == "q090_eta_090"]
        if main_candidates:
            main_cost = float(main_candidates[0]["formal_total_cost_yuan"])
            main_emergency = float(
                main_candidates[0]["formal_total_emergency_kwh"]
            )
            for row in rows:
                row["cost_difference_vs_main_yuan"] = (
                    float(row["formal_total_cost_yuan"]) - main_cost
                )
                row["emergency_difference_vs_main_kwh"] = (
                    float(row["formal_total_emergency_kwh"]) - main_emergency
                )
        else:
            for row in rows:
                row["cost_difference_vs_main_yuan"] = 0.0
                row["emergency_difference_vs_main_kwh"] = 0.0
        write_csv(
            args.output_dir / f"{config['case']}_daily.csv",
            daily_rows,
        )
        # 每组完成后立即落盘，长时间批处理被中断时保留已经完成的结果。
        write_csv(args.output_dir / "comparison_results.csv", rows)
        (args.output_dir / "comparison_results.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        if main_candidates:
            write_markdown(
                args.output_dir / "comparison_results.md",
                rows,
                args.objective_mode,
            )
    print(f"对照结果已保存到: {args.output_dir}")


if __name__ == "__main__":
    main()
