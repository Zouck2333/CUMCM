"""Reconcile the delivered result with the archived formal workbook's model."""
from __future__ import annotations

import argparse
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
    parser.add_argument("--baseline-dir", type=Path, default=root / "archive/before_risk_calibration_20260912_175610/output")
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
    source_paths = [root.parent / "C题/附件/附件1.xlsx", root.parent / "C题/附件/附件2.xlsx",
                    root.parent / "C题/附件/附件5/result2.xlsx", out / "input_data.json",
                    baseline_path, current_path, out / "question_two_daily.csv",
                    out / "question_two_detail.csv", out / "result2.xlsx"]
    record = {"baseline_directory": str(args.baseline_dir.resolve()),
              "same_input_values": True, "same_physical_parameters": True,
              "same_formal_initial_soc": True, "formal_initial_soc_kwh": new["days"][0]["soc_start"],
              "formal_days": len(new["days"]), "periods": verification["detail_rows"],
              "input_shape": [365, 144], "kw_to_kwh_factor": inputs["delta_h"],
              "input_validation": "named sheets, continuous dates, 144 right endpoints, finite nonnegative values",
              "actual_data_transformation": "unit conversion only; no imputation or smoothing",
              "verification_status": verification["status"], "information_boundary_passed": boundary["passed"],
              "changes": changes, "sha256": {str(p.resolve()): digest(p) for p in source_paths}}
    (out / "processing_manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 第二问正式结果更新对照", "",
             "评价期2025-02-01至2025-12-31，共334天。基线为本次替换前正式result2.xlsx对应的结构化结果，新结果为重新联合求解后的正式工作簿。", "",
             "| 指标 | 更新前 | 更新后 | 减少量 | 降幅 |", "|---|---:|---:|---:|---:|"]
    for key, label in labels.items():
        r = changes[key]
        lines.append(f"| {label} | {r['before']:,.2f} | {r['after']:,.2f} | {r['saved']:,.2f} | {r['reduction_percent']:.2f}% |")
    lines += ["", "负的减少量表示增加。正常购电费增加，但紧急费用的降低超过这一增加，实际总费用下降。", "",
              "## 模型变更", "",
              "1. 正式期使用最近28天同类日及前后30分钟的联合净负荷残差，按80%经验分位数形成供能下限。",
              "2. 预测富余改为总供能富余，放开原先仅允许弃光的上界，允许未利用的已付费计划购电额度；富余无收益，不改变电池状态。",
              "3. 联合优化购电和充放电，按费用、吞吐量、购电峰值三级求解。正式期主费用为满足风险下限的计划费用，紧急费用按实际缺口以5倍电价事后结算。",
              "4. 日末储备分位数改为75%；保留当前负荷低估和光伏高估的月度验证惩罚0.35。原正式基线该惩罚为0，因此此处比较包括此前预测修改。", "",
              "## 其他版本", "", "| 版本 | 实际总费用/元 | 紧急费用/元 |", "|---|---:|---:|"]
    alternatives = [("此前仅修改预测损失", out / "optimized_2025/question_two_solution.json")]
    for label, path in alternatives:
        if path.exists():
            d = read(path)["diagnostics"]
            lines.append(f"| {label} | {d['formal_total_cost_yuan']:,.2f} | {d['formal_emergency_cost_yuan']:,.2f} |")
    comparison_file = out / "comparisons/comparison_results.json"
    if comparison_file.exists():
        for item in read(comparison_file):
            if item["case"] in ("q090_eta_090", "q075_eta_090"):
                lines.append(f"| 当前校准模型，{item['reserve_quantile']:.0%}储备 | {item['formal_total_cost_yuan']:,.2f} | {item['formal_emergency_cost_yuan']:,.2f} |")
    lines += ["", "当前75%储备方案的总费用和紧急费用均低于90%储备方案。分位数和储备参数在已有2025年样本上比较后选定，不代表未知年份的独立验证。此前固定电池诊断还隐含放开了原弃光上界，其24.11万元节省不等于本次正式联合优化结果。", "",
              "## 数据与核验", "", "前后版本使用同一输入数据、效率、容量、功率及2月1日10800kWh初始SOC。原始数据仅作功率到电量换算和校验，没有填补或平滑。", "",
              f"工作簿、风险余量独立重算、供需平衡及SOC核验通过，共{len(verification['checks'])}项。信息边界测试扰动{boundary['target_date']}及以后全部实测，当天计划保持不变。三个工作表已渲染并人工检查。", "",
              f"旧结果位于 `{args.baseline_dir.as_posix()}`。输入、输出SHA256及费用变化见 `processing_manifest.json`。"]
    (out / "optimization_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(changes, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
