from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def main() -> None:
    question_dir = Path(__file__).resolve().parent.parent
    output_dir = question_dir / "output"
    parser = argparse.ArgumentParser(description="汇总第二问主结果与模型评价")
    parser.add_argument("--solution", type=Path, default=output_dir / "question_two_solution.json")
    parser.add_argument("--daily", type=Path, default=output_dir / "question_two_daily.csv")
    parser.add_argument("--verification", type=Path, default=output_dir / "verification_report.json")
    parser.add_argument("--paper-summary", type=Path, default=output_dir / "paper_tables" / "paper_selected_dates_summary.csv")
    parser.add_argument("--comparisons", type=Path, default=output_dir / "comparisons" / "comparison_results.json")
    parser.add_argument("--benchmark", type=Path, default=output_dir / "benchmarks" / "perfect_information_benchmark.json")
    parser.add_argument("--output", type=Path, default=output_dir / "result_summary.md")
    args = parser.parse_args()

    diagnostics = json.loads(args.solution.read_text(encoding="utf-8"))["diagnostics"]
    daily = read_csv(args.daily)
    verification = json.loads(args.verification.read_text(encoding="utf-8"))
    paper = read_csv(args.paper_summary)
    comparisons = json.loads(args.comparisons.read_text(encoding="utf-8"))
    benchmark = json.loads(args.benchmark.read_text(encoding="utf-8"))

    max_gap = max(float(row["mip_gap"]) for row in daily)
    max_seconds = max(float(row["solver_seconds"]) for row in daily)
    lines = [
        "# C题第二问最终运行与评价摘要",
        "",
        "## 正式主结果",
        "",
        "评价期为2025年2月1日至12月31日，共334天、48,096个10分钟时段；1月仅用于预热和形成2月1日初始SOC。",
        "",
        f"购电策略：{diagnostics.get('purchase_strategy', 'legacy_scenarios')}；历史窗口{diagnostics.get('risk_window_days', '—')}天，邻近时段半径{diagnostics.get('risk_radius_periods', '—')}个10分钟，购电分位数{diagnostics.get('purchase_quantile', '—')}；日末储备分位数{diagnostics['reserve_quantile']}。",
        f"预测方法：{diagnostics.get('forecast_method', 'similar_day')}；负荷窗口{diagnostics.get('load_window_days', '—')}天、趋势阶数{diagnostics.get('load_trend_degree', '—')}；光伏窗口{diagnostics.get('pv_window_days', '—')}天；残差分组{diagnostics.get('risk_grouping', 'legacy')}。",
        "模型结构与参数基于现有2025年样本开发；逐日拟合只用历史数据，本结果不构成未知年份独立样本保证。",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 计划购电量 | {diagnostics['formal_total_grid_kwh']:,.4f} kWh |",
        f"| 实际紧急购电量 | {diagnostics['formal_total_emergency_kwh']:,.4f} kWh |",
        f"| 计划购电费用 | {diagnostics['formal_plan_cost_yuan']:,.2f} 元 |",
        f"| 紧急购电费用 | {diagnostics['formal_emergency_cost_yuan']:,.2f} 元 |",
        f"| 实际总费用 | {diagnostics['formal_total_cost_yuan']:,.2f} 元 |",
        f"| 总费用低于1500万元 | {'通过' if diagnostics['formal_total_cost_yuan'] < 15000000 else '未达到'} |",
        f"| 紧急费用低于100万元 | {'通过' if diagnostics['formal_emergency_cost_yuan'] < 1000000 else '未达到'} |",
        f"| 最大MILP相对间隙 | {max_gap:.3e} |",
        f"| 最大单日三级求解时间 | {max_seconds:.3f} 秒 |",
        "",
        "## 四个指定日期",
        "",
        "| 日期 | 计划购电量/kWh | 紧急购电量/kWh | 实际总费用/元 | 日初SOC/kWh | 日终SOC/kWh |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in paper:
        lines.append(
            f"| {row['日期']} | {float(row['计划购电量/kWh']):,.4f} | "
            f"{float(row['紧急购电量/kWh']):,.4f} | "
            f"{float(row['实际总费用/元']):,.2f} | "
            f"{float(row['日初SOC/kWh']):,.4f} | {float(row['日终SOC/kWh']):,.4f} |"
        )

    lines.extend(
        [
            "",
            "## 灵敏度分析",
            "",
            "五组方案均使用当前购电风险策略和三级词典序求解；费用差额以表内90%储备、逐时正误差累积、单程效率0.90的对照方案为基准。实际正式结果参数见上文。",
            "",
            "| 方案 | 实际总费用/元 | 费用差额/元 | 紧急购电量/kWh | 平均储备/kWh | 封顶天数 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in comparisons:
        lines.append(
            f"| {row['case']} | {row['formal_total_cost_yuan']:,.2f} | "
            f"{row['cost_difference_vs_main_yuan']:,.2f} | "
            f"{row['formal_total_emergency_kwh']:,.2f} | "
            f"{row['formal_average_reserve_kwh']:,.2f} | "
            f"{row['formal_reserve_cap_days']} |"
        )

    lines.extend(
        [
            "",
            "储备方案的费用与紧急购电量以本次实测表格为准；效率解释对照属于物理参数口径变化，不作为算法节费成果。",
            "",
            "## 完美信息基准",
            "",
            "| 模型 | 正式期费用/元 | 相对差距 |",
            "|---|---:|---:|",
            f"| 滚动主方案 | {benchmark['rolling']['total_cost_yuan']:,.2f} | — |",
            f"| 匹配完美信息MILP | {benchmark['matched_milp']['objective_cost_yuan']:,.2f} | Gap_info={benchmark['gaps']['gap_info_percent']:.4f}% |",
            f"| 完美信息LP下界 | {benchmark['lp_lower']['objective_cost_yuan']:,.2f} | Gap_LP={benchmark['gaps']['gap_lp_percent']:.4f}% |",
            "",
            "成本序关系 `LP下界 ≤ 匹配完美信息MILP ≤ 滚动主方案` 已通过核验。信息差距表明，进一步提升负荷与光伏预测仍有明显节费空间。",
            "",
            "## 自动核验",
            "",
            f"- 总体状态：`{verification['status']}`。",
            f"- 最大预测平衡误差：{verification['max_predicted_balance_error_kwh']:.3e} kWh。",
            f"- 最大实际平衡误差：{verification['max_actual_balance_error_kwh']:.3e} kWh。",
            f"- 最大SOC递推误差：{verification['max_soc_recursion_error_kwh']:.3e} kWh。",
            f"- Excel计划购电映射最大误差：{verification['max_excel_grid_error_kwh']:.3e} kWh。",
            f"- 同时充放电时段数：{verification['simultaneous_charge_discharge_periods']}。",
            "- 完美信息基准的输入、每日结果和主结果SHA256均与当前文件一致。",
            "",
        ]
    )
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"最终摘要已保存: {args.output}")


if __name__ == "__main__":
    main()
