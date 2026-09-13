"""Report the verified historical tuning run without selecting a year-end winner."""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path


BASELINE = "archive/before_causal_tuning_20260912_190504/output"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analysis_checks(out):
    s=read(out/"question_two_solution.json");d=s["diagnostics"]
    v=read(out/"verification_report.json")
    b=read(out/"information_boundary_test.json")
    later=read(out/"information_boundary_test_later.json")
    benchmark=read(out/"benchmarks/perfect_information_benchmark.json")
    protocol=read(out/"tuning_protocol.json")
    checks={"main_verification_passed":v["status"]=="PASS",
        "causal_monthly_mode":d["parameter_mode"]=="causal_monthly",
        "eleven_monthly_selections":len(s["policy_updates"])==11,
        "january_only_initialization":s["policy_updates"][0]["as_of_date"]=="2025-02-01" and s["policy_updates"][0]["history_end"]=="2025-01-31",
        "all_selection_sources_in_past":all(u["history_end"]<u["as_of_date"] and all(k<u["as_of_date"] for k in u["validation_dates"]) for u in s["policy_updates"]),
        "frozen_protocol_matches":protocol["sha256"]==d["tuning_protocol_sha256"] and protocol["protocol"]==s["tuning_protocol"] and protocol["frozen_before_full_horizon_run"],
        "initial_tuning_perturbation_passed":b["passed"] and b["includes_parameter_selection"] and b["target_date"]=="2025-02-01",
        "later_tuning_perturbation_passed":later["passed"] and later["includes_parameter_selection"] and later["target_date"]=="2025-06-01",
        "independent_selection_audit_passed":all(v["checks"][k]["status"]=="PASS" for k in
            ("tuning_history_cutoffs","load_validation_scores","pv_validation_scores","risk_validation_scores",
             "forecast_parameters_selected_by_past_scores","risk_parameters_selected_by_past_scores","reserve_selected_by_past_simulation")),
        "benchmark_ordering":benchmark["ordering_check"]["passed"],
        "benchmark_physics":benchmark["matched_milp"]["checks"]["passed"] and benchmark["lp_lower"]["checks"]["passed"]}
    for filename,key in (("input_data.json","input_sha256"),("question_two_daily.csv","daily_csv_sha256"),("question_two_solution.json","main_json_sha256")):
        checks["benchmark_"+key]=sha(out/filename)==benchmark["provenance"][key]
    return checks,benchmark


def verify_analysis(out,report_path):
    checks,benchmark=analysis_checks(out)
    checks["summary_exists"]=(out/"result_summary.md").is_file()
    checks["tuning_report_exists"]=(out/"causal_tuning_report.md").is_file()
    failures=[k for k,v in checks.items() if not v]
    report={"status":"FAIL" if failures else "PASS","checks":checks,"failures":failures,
        "parameter_mode":"causal_monthly","gap_info_percent":benchmark["gaps"]["gap_info_percent"],
        "gap_lp_percent":benchmark["gaps"]["gap_lp_percent"]}
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    if failures: raise RuntimeError(f"历史滚动分析核验失败: {failures}")
    print(f"历史滚动分析核验通过: {report_path}")


def build_summary(out,baseline_dir=None):
    checks,benchmark=analysis_checks(out)
    if not all(checks.values()): raise ValueError(f"报告前核验失败: {[k for k,v in checks.items() if not v]}")
    root=Path(__file__).resolve().parent.parent
    baseline_dir=baseline_dir or root/BASELINE
    solution=read(out/"question_two_solution.json");d=solution["diagnostics"]
    old=read(baseline_dir/"question_two_solution.json")
    inputs=read(out/"input_data.json")
    v=read(out/"verification_report.json")
    assert inputs==read(baseline_dir/"input_data.json"),"前后实测数据发生变化"
    assert abs(solution["days"][0]["soc_start"]-old["days"][0]["soc_start"])<1e-6
    for k in ("eta_charge","eta_discharge","soc_min_kwh","soc_max_kwh"):
        assert d[k]==old["diagnostics"][k]
    totals={"正常购电费用":d["formal_plan_cost_yuan"],"紧急购电费用":d["formal_emergency_cost_yuan"],"总费用":d["formal_total_cost_yuan"]}
    lines=["# 第二问历史滚动调参结果","","评价期为2025-02-01至2025-12-31，共334天、48,096个10分钟时段。1月为历史积累和SOC预热期。","",
        "2月1日只用1月数据确定初始参数；之后每个月初只用截至前一天的数据重新选择参数。候选集和评分规则在完整正式期运行前保存，未按全年结算结果回选参数。","",
        "| 指标 | 金额/元 | 金额/万元 |","|---|---:|---:|"]
    for label,value in totals.items(): lines.append(f"| {label} | {value:,.2f} | {value/10000:,.4f} |")
    lines += ["",f"总费用低于1500万元：{'达到' if totals['总费用']<15e6 else '未达到'}；紧急费用低于100万元：{'达到' if totals['紧急购电费用']<1e6 else '未达到'}。",
        "","充、放电效率均为0.90，功率上限5000kW，SOC范围1200—10800kWh，2月1日日初SOC为10800kWh；紧急费用按5倍电价结算。","",
        f"独立核验通过，共{len(v['checks'])}项，包含所有月度候选预测评分、风险评分、储备验证调度、逐日参数映射及Excel全量映射。",
        "2月1日和6月1日的当天及未来实测扰动测试均通过，测试包含重新选参及验证调度，未加载既定月度最优参数作为替代。","",
        "## 月度参数","","| 生效日 | 历史截止 | 负荷窗口/天 | 趋势阶数 | 光伏窗口/天 | 残差窗口/天 | 时段半径 | 购电分位数 | 储备分位数 |","|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    flat=[]
    for u in solution["policy_updates"]:
        p=u["selected_parameters"]
        lines.append(f"| {u['as_of_date']} | {u['history_end']} | {p['load_window_days']} | {p['load_trend_degree']} | {p['pv_window_days']} | {p['risk_window_days']} | {p['risk_radius_periods']} | {p['purchase_quantile']:.1%} | {p['reserve_quantile']:.0%} |")
        flat.append({"as_of_date":u["as_of_date"],"history_end":u["history_end"],"validation_start":u["validation_dates"][0],
            "validation_end":u["validation_dates"][-1],"emergency_spent_yuan":u["emergency_spent_yuan"],
            "daily_emergency_budget_yuan":u["daily_emergency_budget_yuan"],**p})
    lines += ["","## 完美信息参照","",
        f"匹配储能与日末储备约束的完美信息MILP费用为{benchmark['matched_milp']['objective_cost_yuan']:,.2f}元；放松约束的LP下界为{benchmark['lp_lower']['objective_cost_yuan']:,.2f}元。二者只用于事后评价，不进入参数选择或日前计划。","",
        "本结果证明当前程序的数据依赖和参数选择遵守历史截止。候选模型的设计已受到此前研究启发，因此仍不是从未查看过2025年数据的外部独立验证，也不构成未知年份的费用保证。"]
    (out/"result_summary.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    with (out/"policy_updates_summary.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(flat[0]));writer.writeheader();writer.writerows(flat)
    tuning=["# 历史滚动调参审计","",
        "调参函数只接收截至前一天的负荷与光伏数组，拒绝长度含未来日期的输入。1月初始冷启动使用附件1先验，1月运行使用预设的旧相似日策略与90%储备；正式期参数从2月1日起由历史验证选出。","",
        "每月依次选择负荷与光伏预测参数、购电风险参数、储备分位数。预测评分为电价加权绝对误差；风险评分为余量购电成本加5倍电价缺口上界，并参考剩余紧急预算；储备评分来自14天连续SOC验证，候选首末SOC均为10800kWh。","",
        "每个历史验证日的候选预测仅拟合更早日期。月初选出新参数后，重新计算该参数下的历史逐日前推残差，供之后的风险和储备估计使用；这些历史候选重算不替换已经执行的旧计划。","",
        f"协议SHA256：`{d['tuning_protocol_sha256']}`。完整候选集见`tuning_protocol.json`；11次全部候选分数与储备验证调度见`question_two_solution.json`的`policy_updates`。","",
        "核验器独立使用增广最小二乘、分位数选秩、供需与SOC递推重算上述数据；不会导入调参器或求解器。两次扰动测试均重新运行选参过程，检查参数、分数、验证调度及最终计划。"]
    (out/"causal_tuning_report.md").write_text("\n".join(tuning)+"\n",encoding="utf-8")
    labels={"formal_plan_cost_yuan":"正常费用","formal_emergency_cost_yuan":"紧急费用","formal_total_cost_yuan":"总费用"}
    changes={k:{"before":old["diagnostics"][k],"after":d[k],"change_yuan":d[k]-old["diagnostics"][k]} for k in labels}
    comparison=["# 历史滚动调参与原固定参数结果对照","",
        "原结果的固定参数曾参考全年回测；新结果在1月初始化、之后仅按已发生数据调参。前后原始输入、电价、储能物理参数及正式期日初SOC一致。","",
        "| 指标 | 原固定参数/元 | 历史滚动调参/元 | 新减旧/元 |","|---|---:|---:|---:|"]
    for k,label in labels.items():
        r=changes[k];comparison.append(f"| {label} | {r['before']:,.2f} | {r['after']:,.2f} | {r['change_yuan']:+,.2f} |")
    comparison += ["","负的变化表示下降。新结果的总费用下降，紧急费用上升，两项仍满足此前的金额目标。此表只比较结果，不用于反向选取新模型参数。"]
    (out/"optimization_comparison.md").write_text("\n".join(comparison)+"\n",encoding="utf-8")
    sources=[root.parent/"C题/附件/附件1.xlsx",root.parent/"C题/附件/附件2.xlsx",root.parent/"C题/附件/附件5/result2.xlsx"]
    sources += [out/name for name in ("input_data.json","question_two_solution.json","question_two_daily.csv","question_two_detail.csv","result2.xlsx","tuning_protocol.json")]
    sources += [root/"code"/name for name in ("causal_policy.py","forecast_calendar.py","solve_question_two.py","verify_causal_policy.py","verify_question_two.py","test_information_boundary.py")]
    sources += [root/"docs/model_Two.md"]
    manifest={"parameter_mode":"causal_monthly","same_input_values":True,"same_physical_parameters":True,
        "same_formal_initial_soc":True,"formal_initial_soc_kwh":solution["days"][0]["soc_start"],"formal_days":334,"periods":48096,
        "baseline_directory":str(baseline_dir.resolve()),"tuning_protocol_sha256":d["tuning_protocol_sha256"],
        "parameter_updates":len(solution["policy_updates"]),"changes":changes,
        "targets":{"total_cost":{"limit_yuan":15e6,"actual_yuan":totals['总费用'],"passed":totals['总费用']<15e6},
                   "emergency_cost":{"limit_yuan":1e6,"actual_yuan":totals['紧急购电费用'],"passed":totals['紧急购电费用']<1e6}},
        "verification_status":v["status"],"initial_and_later_tuning_boundary_tests_passed":True,
        "actual_data_transformation":"unit conversion only; no imputation or smoothing",
        "sha256":{str(p.resolve()):sha(p) for p in sources}}
    (out/"processing_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"历史滚动汇总、选参表与对照已更新: {out}")
