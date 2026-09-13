"""Reconcile the delivered result with the archived formal workbook's model."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=root / "output")
    parser.add_argument("--baseline-dir", type=Path, default=root / "archive/before_target_1500_100_20260912_182709/output")
    args = parser.parse_args()
    out = args.output_dir
    current_path = out / "question_two_solution.json"
    baseline_path = args.baseline_dir / "question_two_solution.json"
    old, new = read(baseline_path), read(current_path)
    old_diag, new_diag = old["diagnostics"], new["diagnostics"]
    inputs = read(out / "input_data.json")
    old_inputs = read(args.baseline_dir / "input_data.json")
    assert inputs == old_inputs, "前后版本输入数据不一致"
    assert [d["date"] for d in old["days"]] == [d["date"] for d in new["days"]]
    assert abs(old["days"][0]["soc_start"] - new["days"][0]["soc_start"]) < 1e-6
    for key in ("eta_charge", "eta_discharge", "soc_min_kwh", "soc_max_kwh"):
        assert old_diag[key] == new_diag[key]
    verification = read(out / "verification_report.json")
    boundary = read(out / "information_boundary_test.json")
    assert verification["status"] == "PASS" and boundary["passed"]
    for key in ("formal_plan_cost_yuan", "formal_emergency_cost_yuan", "formal_total_cost_yuan"):
        assert abs(new_diag[key] - verification[key + "_recomputed"]) < 1e-5, "核验报告并非当前费用结果"
    for key in ("forecast_method", "load_window_days", "load_trend_degree", "pv_window_days",
                "risk_grouping", "purchase_strategy", "purchase_quantile", "reserve_quantile"):
        assert boundary["model_parameters"].get(key) == new_diag.get(key), "信息边界测试参数过期"
    labels = {
        "formal_plan_cost_yuan": "正常购电费用/元",
        "formal_emergency_cost_yuan": "紧急购电费用/元",
        "formal_total_cost_yuan": "实际总费用/元",
        "formal_total_emergency_kwh": "紧急购电量/kWh",
    }
    changes = {key: {"before": old_diag[key], "after": new_diag[key],
                     "saved": old_diag[key] - new_diag[key],
                     "reduction_percent": 100 * (old_diag[key] - new_diag[key]) / old_diag[key]}
               for key in labels}
    forecast_changes = {}
    for name, directory in (("before", args.baseline_dir), ("after", out)):
        with (directory/"question_two_daily.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = [r for r in csv.DictReader(stream) if r["warmup"].lower() == "false"]
        forecast_changes[name] = {key: sum(float(r[key]) for r in rows)/len(rows)
                                  for key in ("load_mae_kwh", "pv_mae_kwh")}
    targets = {
        "total_cost": {"limit_yuan": 15000000.0, "actual_yuan": new_diag["formal_total_cost_yuan"]},
        "emergency_cost": {"limit_yuan": 1000000.0, "actual_yuan": new_diag["formal_emergency_cost_yuan"]},
    }
    for target in targets.values():
        target["remaining_yuan"] = target["limit_yuan"] - target["actual_yuan"]
        target["passed"] = target["actual_yuan"] < target["limit_yuan"]
    assert all(target["passed"] for target in targets.values()), "正式费用未同时达到两个严格上限"
    source_paths = [root.parent / "C题/附件/附件1.xlsx", root.parent / "C题/附件/附件2.xlsx",
                    root.parent / "C题/附件/附件5/result2.xlsx", out / "input_data.json",
                    baseline_path, current_path, out / "question_two_daily.csv",
                    out / "question_two_detail.csv", out / "result2.xlsx",
                    root / "code/forecast_calendar.py", root / "code/solve_question_two.py",
                    root / "code/verify_question_two.py"]
    record = {"baseline_directory": str(args.baseline_dir.resolve()),
              "same_input_values": True, "same_physical_parameters": True,
              "same_formal_initial_soc": True, "formal_initial_soc_kwh": new["days"][0]["soc_start"],
              "formal_days": len(new["days"]), "periods": verification["detail_rows"],
              "input_shape": [365, 144], "kw_to_kwh_factor": inputs["delta_h"],
              "input_validation": "named sheets, continuous dates, 144 right endpoints, finite nonnegative values",
              "actual_data_transformation": "unit conversion only; no imputation or smoothing",
              "verification_status": verification["status"], "information_boundary_passed": boundary["passed"],
              "changes": changes, "targets": targets, "forecast_mae": forecast_changes,
              "parameter_selection_scope": new_diag.get("parameter_selection_scope"),
              "sha256": {str(p.resolve()): digest(p) for p in source_paths}}
    (out / "processing_manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 第二问正式结果更新对照", "",
             "评价期2025-02-01至2025-12-31，共334天。基线为本次替换前正式result2.xlsx对应的结构化结果，新结果为重新联合求解后的正式工作簿。", "",
             "| 指标 | 更新前 | 更新后 | 减少量 | 降幅 |", "|---|---:|---:|---:|---:|"]
    for key, label in labels.items():
        r = changes[key]
        lines.append(f"| {label} | {r['before']:,.2f} | {r['after']:,.2f} | {r['saved']:,.2f} | {r['reduction_percent']:.2f}% |")
    lines += ["", "## 两项费用目标", "",
              f"实际总费用严格低于1500万元，余量{targets['total_cost']['remaining_yuan']:,.2f}元；紧急费用严格低于100万元，余量{targets['emergency_cost']['remaining_yuan']:,.2f}元。", "",
              f"负荷逐时段MAE从{forecast_changes['before']['load_mae_kwh']:.4f}降至{forecast_changes['after']['load_mae_kwh']:.4f}kWh；光伏MAE从{forecast_changes['before']['pv_mae_kwh']:.4f}降至{forecast_changes['after']['pv_mae_kwh']:.4f}kWh。", "",
              "## 模型变更", "",
              "1. 正式期负荷改为28天对数负荷回归，学习六个星期效应和近期二次趋势；不预设周末是低负荷日。",
              "2. 光伏改为14天逐时段线性趋势，并对斜率进行岭惩罚。每天只用此前的实测数据重新拟合。",
              f"3. 合并最近{new_diag['risk_window_days']}天全部日期及前后{10*new_diag['risk_radius_periods']}分钟的净负荷残差，购电分位数由80%提高至{new_diag['purchase_quantile']:.0%}，减少紧急补购。",
              "4. 保留75%日末储备、总供能富余口径、正常购电全额付费与富余无收益规则，以及购电和储能三级词典序联合优化。单程效率均为0.9，紧急电价为5倍，1月预热过程不变。", "",
              "## 其他版本", "", "| 版本 | 实际总费用/元 | 紧急费用/元 |", "|---|---:|---:|"]
    alternatives = [("此前仅修改预测损失", out / "optimized_2025/question_two_solution.json")]
    alternatives += [("当前预测，81%购电分位数（完整联合求解）",
                      root / "archive/target_runs_20260912/target_candidate_q081/question_two_solution.json")]
    for label, path in alternatives:
        if path.exists():
            d = read(path)["diagnostics"]
            lines.append(f"| {label} | {d['formal_total_cost_yuan']:,.2f} | {d['formal_emergency_cost_yuan']:,.2f} |")
    comparison_file = out / "comparisons/comparison_results.json"
    if comparison_file.exists():
        for item in read(comparison_file):
            if item["case"] in ("q090_eta_090", "q075_eta_090"):
                lines.append(f"| 当前校准模型，{item['reserve_quantile']:.0%}储备 | {item['formal_total_cost_yuan']:,.2f} | {item['formal_emergency_cost_yuan']:,.2f} |")
    lines += ["", "预测结构、窗口和购电分位数在已有2025年样本上筛选，属于开发样本回测，不代表未知年份独立验证。固定电池诊断仅用于候选筛选；本表正式费用来自完整365天重新联合求解，1月费用不计入正式评价。", "",
              "## 数据与核验", "", "前后版本使用同一输入数据、效率、容量、功率及2月1日10800kWh初始SOC。原始数据仅作功率到电量换算和校验，没有填补或平滑。", "",
              f"工作簿、预测和风险余量独立重算、供需平衡及SOC核验通过，共{len(verification['checks'])}项。信息边界测试扰动{boundary['target_date']}及以后全部实测，当天计划保持不变。三个工作表已渲染并人工检查。", "",
              f"旧结果位于 `{args.baseline_dir.as_posix()}`。输入、输出SHA256及费用变化见 `processing_manifest.json`。"]
    (out / "optimization_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(changes, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
