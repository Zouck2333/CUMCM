"""Causal purchase-buffer experiments with the existing battery schedule frozen.

This diagnostic also relaxes the legacy upper bound on forecast PV curtailment.
It is not a constraint-preserving ablation of the old MILP or an official workbook.
Run from the repository root with Python. Inputs and all model settings are hashed
or recorded in the output; no candidate uses its evaluation day's actual demand.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
import solve_question_two as model


def margin_from_history(errors, dates, target, window, radius, quantile):
    candidates = list(range(max(1, target - window), target))
    same = [i for i in candidates if model.is_workday(dates[i]) == model.is_workday(dates[target])]
    history = same if len(same) >= 5 else candidates
    assert history and max(history) < target
    sample = errors[history]
    margin = np.array([
        np.quantile(sample[:, max(0, t - radius):min(144, t + radius + 1)],
                    quantile, method="inverted_cdf")
        for t in range(144)
    ])
    return np.maximum(0, margin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=Path("Question_Two/output/optimized_2025/question_two_solution.json"))
    parser.add_argument("--input", type=Path, default=Path("Question_Two/output/input_data.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("Question_Two/output/risk_calibration"))
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    diag = baseline["diagnostics"]
    for name, key in [("FORECAST_BIAS_DAYS", "forecast_bias_days"),
                      ("FORECAST_BIAS_DECAY", "forecast_bias_decay"),
                      ("FORECAST_BIAS_SHRINK_DAYS", "forecast_bias_shrink_days"),
                      ("RISK_CORRECTION_WEIGHT", "risk_correction_weight")]:
        if key in diag:
            setattr(model, name, diag[key])
    dates = [date.fromisoformat(v) for v in payload["dates"]]
    load, pv, price = (np.array(payload[key]) for key in ("actual_load", "actual_pv", "price"))
    parameters = {p["month"]: model.Theta(p["k_load"], p["k_pv"], p["rho_load"], p["rho_pv"])
                  for p in diag["monthly_parameters"]}
    prediction = np.empty_like(load)
    prediction[0] = np.array(payload["prior_load"]) - np.array(payload["prior_pv"])
    for i in range(1, len(dates)):
        theta = parameters[dates[i].strftime("%Y-%m")]
        prediction[i] = (
            model.forecast_component(i, load, theta.k_load, theta.rho_load, dates, load, pv, "load")
            - model.forecast_component(i, pv, theta.k_pv, theta.rho_pv, dates, load, pv, "pv")
        )
    errors = load - pv - prediction
    days = baseline["days"]
    indices = np.array([dates.index(date.fromisoformat(day["date"])) for day in days])
    assert len(days) == 334 and indices.tolist() == list(range(31, 365))
    grid, charge, discharge = (np.array([day[key] for day in days]) for key in ("grid", "charge", "discharge"))
    net = load[indices] - pv[indices]
    configs = [(28, 0, .80), (28, 3, .80), (56, 3, .80), (28, 3, .85)]
    grids = {"baseline": grid}
    for window, radius, quantile in configs:
        name = f"w{window}_r{radius}_q{quantile:.2f}"
        margins = np.array([margin_from_history(errors, dates, i, window, radius, quantile) for i in indices])
        grids[name] = np.maximum(0, prediction[indices] + margins + charge - discharge)

    def costs(g):
        shortage = np.maximum(0, net - g + charge - discharge)
        return (g * price).sum(axis=1), (shortage * 5 * price).sum(axis=1), shortage

    costs_by_case = {name: costs(g) for name, g in grids.items()}
    # Select on the previous 28 observed formal days, update once at month start.
    # Candidates must not increase historical emergency cost relative to baseline.
    selected = "baseline"
    adaptive_grid, selections = [], []
    names = list(grids)
    for j, day in enumerate(days):
        if date.fromisoformat(day["date"]).day == 1 and j >= 14:
            start = max(0, j - 28)
            baseline_emergency = costs_by_case["baseline"][1][start:j].sum()
            eligible = [name for name in names if costs_by_case[name][1][start:j].sum() <= baseline_emergency + 1e-7]
            selected = min(eligible, key=lambda name: (float((costs_by_case[name][0][start:j] + costs_by_case[name][1][start:j]).sum()), names.index(name)))
            selections.append({"month": day["date"][:7], "case": selected,
                               "validation_start": days[start]["date"], "validation_end": days[j-1]["date"]})
        adaptive_grid.append(grids[selected][j])
    grids["monthly_past_cost_selection"] = np.array(adaptive_grid)

    checks = {}
    for name, g in grids.items():
        assert np.isfinite(g).all() and np.min(g) >= -1e-7
        assert np.min(g + discharge - charge - prediction[indices]) >= -1e-5
    for j, day in enumerate(days):
        soc = day["soc_start"] + np.cumsum(diag["eta_charge"] * charge[j] - discharge[j] / diag["eta_discharge"])
        assert soc.min() >= model.SOC_MIN - 1e-5 and soc.max() <= model.SOC_MAX + 1e-5
        assert abs(soc[-1] - day["soc_end"]) < 1e-5
        if j:
            assert abs(day["soc_start"] - days[j-1]["soc_end"]) < 1e-5
    assert np.max(np.minimum(charge, discharge)) < 1e-6
    assert max(charge.max(), discharge.max()) <= model.POWER_LIMIT_KW * payload["delta_h"] + 1e-5
    checks["battery_soc_power_exclusivity_and_continuity"] = "PASS"
    checks["nonnegative_grid_and_point_forecast_balance"] = "PASS"
    # The calibration layer must be invariant to all current/future residuals.
    boundary_tests = 0
    for target in (31, 59, 172, 300):
        changed = errors.copy()
        changed[target:] += 123456
        for window, radius, quantile in configs:
            np.testing.assert_array_equal(
                margin_from_history(errors, dates, target, window, radius, quantile),
                margin_from_history(changed, dates, target, window, radius, quantile))
            boundary_tests += 1
    checks["calibration_future_perturbation_tests"] = boundary_tests
    forecast_tests = 0
    for target in (31, 59, 172, 300):
        changed_load, changed_pv = load.copy(), pv.copy()
        changed_load[target:] += 2000
        changed_pv[target:] *= .1
        theta = parameters[dates[target].strftime("%Y-%m")]
        changed_prediction = (
            model.forecast_component(target, changed_load, theta.k_load, theta.rho_load, dates, changed_load, changed_pv, "load")
            - model.forecast_component(target, changed_pv, theta.k_pv, theta.rho_pv, dates, changed_load, changed_pv, "pv"))
        np.testing.assert_array_equal(prediction[target], changed_prediction)
        forecast_tests += 1
    checks["forecast_future_perturbation_tests_with_recorded_monthly_parameters"] = forecast_tests
    for param in diag["monthly_parameters"]:
        if param.get("validation_end"):
            assert param["validation_end"] < param["month"] + "-01"
    checks["recorded_monthly_validation_date_boundary"] = "PASS"
    results = []
    monthly = []
    for name, g in grids.items():
        planned, emergency, shortage = costs(g)
        surplus = np.maximum(0, g + discharge - charge - net)
        np.testing.assert_allclose(g + discharge + shortage - charge - surplus, net, atol=1e-6)
        result = {"case": name, "planned_cost_yuan": float(planned.sum()),
                  "emergency_cost_yuan": float(emergency.sum()), "total_cost_yuan": float((planned + emergency).sum()),
                  "emergency_kwh": float(shortage.sum()), "surplus_kwh": float(surplus.sum()),
                  "emergency_periods": int((shortage > 1e-7).sum())}
        results.append(result)
        for month in sorted({day["date"][:7] for day in days}):
            mask = np.array([day["date"].startswith(month) for day in days])
            monthly.append({"case": name, "month": month, "total_cost_yuan": float((planned[mask] + emergency[mask]).sum()), "emergency_cost_yuan": float(emergency[mask].sum())})
    np.testing.assert_allclose(results[0]["total_cost_yuan"], diag["formal_total_cost_yuan"], rtol=0, atol=1e-4)
    np.testing.assert_allclose(results[0]["emergency_cost_yuan"], diag["formal_emergency_cost_yuan"], rtol=0, atol=1e-4)
    checks["baseline_cost_reproduction_and_actual_balance"] = "PASS"
    for row in results:
        row["total_cost_change_yuan"] = row["total_cost_yuan"] - results[0]["total_cost_yuan"]
        row["emergency_cost_change_yuan"] = row["emergency_cost_yuan"] - results[0]["emergency_cost_yuan"]
    diagnostics = []
    base_emergency = costs(grid)[2]
    for begin in range(0, 144, 24):
        sl = slice(begin, begin + 24)
        diagnostics.append({"hours": f"{begin//6:02d}:00-{(begin+24)//6:02d}:00",
                            "emergency_cost_yuan": float((base_emergency[:, sl] * price[sl] * 5).sum()),
                            "emergency_kwh": float(base_emergency[:, sl].sum())})
    output = {"scope": "Fixed battery schedule; causal purchase buffer with the legacy PV-only surplus cap relaxed; not a reoptimized MILP or official result workbook",
              "inputs_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.baseline, args.input, Path(__file__), Path(model.__file__))},
              "configurations": [{"window_days": w, "radius_periods": r, "quantile": q} for w,r,q in configs],
              "quantile_method": "inverted_cdf", "checks": checks, "results": results,
              "monthly_results": monthly, "monthly_selections": selections, "baseline_emergency_by_4h": diagnostics}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "risk_calibration_results.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    by_name = {row["case"]: row for row in results}
    base, q80, q85, adaptive = [by_name[name] for name in ("baseline", "w28_r3_q0.80", "w28_r3_q0.85", "monthly_past_cost_selection")]
    evening_cost = sum(item["emergency_cost_yuan"] for item in diagnostics[-2:])
    lines = [
        "# 第二问：进一步降低总费用和紧急费用的诊断实验", "",
        "更正：本实验还隐含放宽了原程序的预测弃光上界，仅核验供需下限，不能视为保留原MILP全部约束的实验。当前正式联合优化模型与结果另见 model_Two_calibrated.md 和正式 result2.xlsx。", "",
        f"评价期为2025-02-01至2025-12-31，共334天。本实验以 `{args.baseline.as_posix()}` 为基线（默认是修改预测损失后的版本），保持其充放电计划、SOC轨迹、效率、日末储备和2月1日初始状态不变，只在每日0:00重新计算正常购电量。", "",
        "这是可执行购电策略的隔离回测，尚未把新风险估计嵌入MILP重新联合优化储能，也没有替换正式结果表。", "",
        "## 1. 本次实测结果", "",
        "| 方案 | 正常购电费用/元 | 紧急费用/元 | 总费用/元 | 相对基线总费用变化/元 | 紧急时段数 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    labels = {"baseline": "输入基线", "w28_r0_q0.80": "28天，单时段，80%",
              "w28_r3_q0.80": "28天，前后30分钟，80%", "w56_r3_q0.80": "56天，前后30分钟，80%",
              "w28_r3_q0.85": "28天，前后30分钟，85%", "monthly_past_cost_selection": "月初按过去28天费用选择"}
    for row in results:
        lines.append(f"| {labels[row['case']]} | {row['planned_cost_yuan']:,.2f} | {row['emergency_cost_yuan']:,.2f} | {row['total_cost_yuan']:,.2f} | {row['total_cost_change_yuan']:+,.2f} | {row['emergency_periods']:,} |")
    lines += ["", f"28天、前后30分钟、80%候选相对基线正常购电费变化{q80['planned_cost_yuan']-base['planned_cost_yuan']:+,.2f}元，紧急购电费变化{q80['emergency_cost_change_yuan']:+,.2f}元，总费用变化{q80['total_cost_change_yuan']:+,.2f}元（{100*q80['total_cost_change_yuan']/base['total_cost_yuan']:+.2f}%）；紧急费用变化{100*q80['emergency_cost_change_yuan']/base['emergency_cost_yuan']:+.2f}%。85%候选进一步降低紧急费用，但总费用高于80%候选。", "",
              f"固定候选间的优劣来自本年度事后比较，不等于未知年份的最优参数。为降低全年挑参造成的乐观评价，另给出月初滚动选择：2月沿用基线；3月起只用月初之前28个已完成正式日比较候选，仅接受历史紧急费用不高于基线者，再选历史总费用最低者，当月固定。该策略总费用变化{adaptive['total_cost_change_yuan']:+,.2f}元（{100*adaptive['total_cost_change_yuan']/base['total_cost_yuan']:+.2f}%），紧急费用变化{adaptive['emergency_cost_change_yuan']:+,.2f}元（{100*adaptive['emergency_cost_change_yuan']/base['emergency_cost_yuan']:+.2f}%）。候选集合的设计仍应在后续独立数据上检验。", "",
              "## 2. 为什么优先校准购电余量", "",
              r"设净负荷 $N=L-PV$，固定充放电后需要外购的净需求为 $X=N+C-D$，每时段期望费用为", "",
              r"$$J(G)=pG+5p\,\mathbb E[(X-G)_+].$$", "",
              r"在连续分布、内点且购电无上限时，$J'(G)=p-5p\Pr(X>G)$，故最优购电满足 $F_X(G)=0.8$。离散经验分布对应80%分位数的次梯度条件；程序使用 `inverted_cdf`。再施加正常购电非负和点预测平衡下限。", "",
              "原情景MILP本来已经隐含这一边际权衡，不能把增加一个80%参数本身解释为新的降费来源。本次改变的是净负荷尾部分布估计：从近期同类日联合净误差直接估计分位数，并对临近时段合并样本；同时避免负荷、光伏分别非负截断后对净误差分布的改变。当前对照没有逐项拆分这些影响。", "",
              r"每日净误差为 $e_{k,t}=(L_{k,t}-PV_{k,t})-\widehat N_{k,t}$。选取 $k<d$ 的近28天同类日，同类日不足5天时使用该窗口全部历史日；对每个时段汇集 $t-3$ 至 $t+3$ 的残差（边界截断，不环绕），令 $b_{d,t}=\max(0,Q_{0.8}(e))$，日前购电为", "",
              r"$$G'_{d,t}=\max(0,\widehat N_{d,t}+b_{d,t}+C_{d,t}-D_{d,t}).$$", "",
              "实际负荷、光伏只用于计划制定后的结算和以后日期的残差库更新。邻近时段样本可能相关，合并样本不代表独立样本数成倍增长，也不构成严格概率覆盖保证。", "",
              f"基线紧急购电发生在{100*base['emergency_periods']/grid.size:.2f}%的时段；28天、前后30分钟、80%候选为{100*q80['emergency_periods']/grid.size:.2f}%。两者是全样本实际发生率，受非负购电、点预测下限及储能计划影响，不能直接视为条件分位数的校准检验。基线16:00—24:00紧急费用合计约{evening_cost/10000:.2f}万元，占全年紧急费用约{100*evening_cost/base['emergency_cost_yuan']:.2f}%，后续应优先检查这段时间的负荷低估及尾部预测。", "",
              "## 3. 后续模型优化顺序", "",
              "1. **先接入净负荷尾部分布校准。** 在MILP中使用经滚动验证的联合净负荷情景及其概率；或者由训练分位数构造可行供能目标后联合优化购电与储能。这里的80%是供需分位数，与日末SOC储备的75%或90%是不同参数。保留原方案为候选，用过去日期的结算费用调参。", "",
              r"2. **改用费用导向的联合预测验证。** 评价负荷减光伏后的净误差，并按电价加权。固定充放电且供能目标q可换算为非负购电量时，可使用 $p[(q-N)_++4(N-q)_+]$，它与实际采购加紧急费用仅差一个与q无关的 $p(N+C-D)$，对应80%分位数；比独立的负荷、光伏MAE更贴近经济目标。最终参数仍以嵌套滚动调度结算费用为准。", "",
              "3. **再联合比较储备0.75与新风险模型。** 原正式版本的现有对照中，储备0.90改为0.75节省36,471.88元，紧急购电量也略降；但该数字来自旧预测基线，不能与本次节省直接相加。本模型日内充放电固定，日末储备没有当天应急释放的控制变量，因此大幅加储备不能直接应对当天随机缺口。", "",
              f"4. **按偏好给出费用与风险折中。** 先最小化实际总费用；若另外要求紧急费用上限，再在滚动训练中筛选满足上限的策略并比较样本外表现。本次85%候选是降低紧急费用的可选点，但比80%候选多花{q85['total_cost_yuan']-q80['total_cost_yuan']:,.2f}元总费用，不应把紧急费用最小等同于总费用最小。", "",
              "不能通过提高充放电效率、更改真实紧急电价、使用当天未来实测数据或离线完美信息结果来声称算法改进。", "",
              "## 4. 核验与复现", "",
              "已核验基线费用复现、非负购电、点预测供需约束、实际供需平衡、储能SOC边界与递推、功率、充放电互斥及跨日连续性；16组当前/未来残差扰动、4组固定已记录月参数的当前/未来实测扰动均不影响当天新计划的对应计算。原MILP和原月度调参过程未在本隔离实验中重新求解，沿用已存储的历史可用参数与计划。", "",
              "```powershell", "python Question_Two/code/analyze_risk_calibration.py", "```", "",
              "`risk_calibration_results.json` 保存费用、逐月结果、逐月选择、检查结果及输入和脚本SHA256。正式模型、正式JSON、CSV和result2.xlsx未被本脚本修改。"]
    (args.output_dir / "risk_calibration_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"results": results, "checks": checks, "by_4h": diagnostics}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
