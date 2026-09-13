"""Reconcile the optimization comparisons and prepare paper-ready tables."""
from __future__ import annotations

import json
from pathlib import Path

from input_data import load_inputs
from run_question_three import decision_fingerprint, verify_day


ROOT = Path(__file__).resolve().parents[1]


def read_records(path, data):
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if len(records) != 365:
        raise ValueError(f"Incomplete run: {path}")
    fingerprint = decision_fingerprint(data)
    for i, record in enumerate(records):
        assert record["date"] == data.dates[i].isoformat()
        assert record["config"]["decision_fingerprint_sha256"] == fingerprint
        verify_day(record, data, i)
        if i:
            assert abs(record["soc_start"]-records[i-1]["soc_end"]) < 1e-6
    return records


def metrics(records):
    return {**{name: sum(r[name] for r in records)
               for name in ("plan_cost", "adjustment_cost", "emergency_cost", "total_cost")},
            "emergency_kwh": sum(sum(r["emergency"]) for r in records),
            "initial_soc": records[0]["soc_start"], "final_soc": records[-1]["soc_end"]}


def main():
    data = load_inputs(ROOT.parent)
    output = ROOT / "output_optimized"
    old = read_records(ROOT/"comparison_four_day/checkpoint_rolling.jsonl", data)
    new = read_records(output/"checkpoint_rolling.jsonl", data)
    zero = read_records(output/"checkpoint_zero_only.jsonl", data)
    before_config = {k:v for k,v in old[0]["config"].items() if k != "forecast_mode"}
    after_config = {k:v for k,v in new[0]["config"].items() if k != "forecast_mode"}
    assert before_config == after_config, "Unmatched forecast comparison configuration"
    assert old[0]["config"]["forecast_mode"] == "four_day"
    assert new[0]["config"]["forecast_mode"] == "calendar"
    cases = {"four_day": old, "calendar_rolling": new, "calendar_zero_only": zero}
    formal = {name: metrics(records[31:]) for name, records in cases.items()}
    full = {name: metrics(records) for name, records in cases.items()}
    saving = formal["four_day"]["total_cost"]-formal["calendar_rolling"]["total_cost"]
    monthly = []
    for month in range(2, 13):
        name = f"2025-{month:02d}"
        a, b = [metrics([r for r in records if r["date"].startswith(name)]) for records in (old,new)]
        monthly.append({"month": name, "before":a["total_cost"], "after":b["total_cost"],
                        "saving":a["total_cost"]-b["total_cost"]})
    results = {"period":"2025-02-01 to 2025-12-31", "formal":formal,
               "full_year":full, "monthly":monthly, "saving_yuan":saving,
               "saving_fraction":saving/formal["four_day"]["total_cost"],
               "source_fingerprint":decision_fingerprint(data),
               "matched_comparison_validation":"passed"}
    (output/"optimization_comparison.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
    lines = ["# 第三问优化结果", "",
             "评价期：2025年2月1日至12月31日，共334天。所有策略均从1月1日6000 kWh开始顺序执行365天。", "",
             "本版采用28天星期效应与近期二次趋势预测，历史情景残差同步重算。默认优化期望费用加0.1倍CVaR，置信度0.9。调整方向改用等价连续变量，电池互斥、容量、功率、效率及费用结算保持不变。", "",
             f"同配置对照节省 **{saving:,.2f}元（{saving/formal['four_day']['total_cost']:.2%}）**。", "",
             "| 指标 | 旧负载预测滚动策略 | 优化预测滚动策略 | 优化预测仅0:00策略 |",
             "|---|---:|---:|---:|"]
    for field, title in [("plan_cost","计划购电费/元"),("adjustment_cost","调整费/元"),
                         ("emergency_cost","紧急购电费/元"),("total_cost","总费用/元"),
                         ("emergency_kwh","紧急购电量/kWh"),("initial_soc","2月1日日初SOC/kWh"),
                         ("final_soc","12月31日日末SOC/kWh")]:
        lines.append(f"| {title} | " + " | ".join(f"{v[field]:,.2f}" for v in formal.values()) + " |")
    update_saving=formal["calendar_zero_only"]["total_cost"]-formal["calendar_rolling"]["total_cost"]
    lines += ["",f"新预测下，滚动更新相对仅0:00策略的评价期节省额为 **{update_saving:,.2f}元**（正值表示滚动更便宜，负值表示仅0:00更便宜）。这只是完整策略对照，不能据实际未来结果逐日选优。", "",
              "## 全年同初始状态核对", "", "| 策略 | 1—12月总费用/元 | 年末SOC/kWh |", "|---|---:|---:|"]
    for name, title in (("four_day","旧预测滚动"),("calendar_rolling","优化预测滚动"),("calendar_zero_only","优化预测仅0:00")):
        lines.append(f"| {title} | {full[name]['total_cost']:,.2f} | {full[name]['final_soc']:,.4f} |")
    lines += ["", "2月1日的SOC由各自1月策略决定，因此评价期比较包含预热期带入的状态差异。全年同初始状态成本与年末SOC同时列出，以避免把边界状态差异误当成纯预测收益。", "",
              "## 逐月对照", "", "| 月份 | 旧预测费用/元 | 优化费用/元 | 节省/元 |", "|---|---:|---:|---:|"]
    for m in monthly:
        lines.append(f"| {m['month']} | {m['before']:,.2f} | {m['after']:,.2f} | {m['saving']:,.2f} |")
    lines += ["", "## 论文指定日期", "", "| 日期 | 初始计划购电/kWh | 最终正常购电/kWh | 紧急购电/kWh | 总费用/元 |", "|---|---:|---:|---:|---:|"]
    chosen = [r for r in new if r["date"] in ("2025-03-20","2025-06-21","2025-09-23","2025-12-21")]
    for r in chosen:
        lines.append(f"| {r['date']} | {sum(r['initial_purchase']):,.4f} | {sum(r['final_purchase']):,.4f} | {sum(r['emergency']):,.4f} | {r['total_cost']:,.2f} |")
    lines += ["", "## 核验与适用范围", "",
              "已核对全年断点、逐时物理约束、费用重算与同配置对照。正式工作簿与CSV、摘要、双策略断点的独立核验见verification_report.json；14项单元测试覆盖未来信息屏蔽、历史预测重建、调整变量等价性及电池约束。", "",
              "原先1583.53万元来自Question_Three_cheaper实验，和本版的冷启动、前缀概率及数值校正存在差异；本报告以正式程序同配置重算为准。本次没有完成独立年份验证，也未声称全局最优。", "",
              "备用购电合同、区间内电池反馈、多阶段初始计划等扩展尚未加入。保持现有名义供需平衡及实际缺口结算口径，避免把物理或计费假设的变化计作算法收益。", "",
              "主结果：result3.xlsx；10分钟明细：question_three_detail.csv；数学模型：../model_Three.md。"]
    (output/"optimization_summary.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    paper=["# 第三问指定日期详细表", "", "电量单位kWh，费用单位元；时间区间均按10分钟右端点数据映射。"]
    for r in chosen:
        paper += ["",f"## {r['date']}", "", "| 时段 | 初始计划购电 | 最终正常购电 |", "|---|---:|---:|"]
        for hour in (10,12,14,16,18,20):
            t=hour*6
            paper.append(f"| {hour}:00–{hour}:10 | {r['initial_purchase'][t]:.4f} | {r['final_purchase'][t]:.4f} |")
        paper += ["", "| 储能时段 | 充电量 | 放电量 |", "|---|---:|---:|"]
        for block in range(6):
            sl=slice(block*24,(block+1)*24)
            paper.append(f"| {block*4}:00–{(block+1)*4}:00 | {sum(r['charge'][sl]):.4f} | {sum(r['discharge'][sl]):.4f} |")
        paper += ["",f"0:00 SOC：{r['soc_start']:.4f}；24:00 SOC：{r['soc_end']:.4f}。", "",
                  "| 紧急购电时段 | 购电量 |", "|---|---:|"]
        start, amount = None,0.
        for t in range(145):
            if t<144 and r['emergency'][t]>1e-8:
                if start is None: start=t
                amount+=r['emergency'][t]
            elif start is not None:
                paper.append(f"| {start//6}:{(start%6)*10:02d}–{t//6}:{(t%6)*10:02d} | {amount:.4f} |")
                start,amount=None,0.
        if not any(v>1e-8 for v in r['emergency']): paper.append("| 无 | 0.0000 |")
    (output/"paper_selected_dates.md").write_text("\n".join(paper)+"\n",encoding="utf-8")
    print(json.dumps({"saving_yuan":saving,"formal":formal},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
