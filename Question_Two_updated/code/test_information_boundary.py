from __future__ import annotations

import argparse
import copy
import csv
import json
from pathlib import Path

import numpy as np

from solve_question_two import run_model
from causal_policy import protocol_hash


def formal_day(solution: dict, day_text: str) -> dict:
    for item in solution["days"]:
        if item["date"] == day_text:
            return item
    raise ValueError(f"正式结果中找不到测试日期: {day_text}")


def max_array_difference(left: dict, right: dict, field: str) -> float:
    return float(
        np.max(
            np.abs(
                np.asarray(left[field], dtype=float)
                - np.asarray(right[field], dtype=float)
            )
        )
    )


def compare_policy_records(left, right):
    """Compare all selected parameters, validation scores and simulation witnesses."""
    if isinstance(left, dict) and isinstance(right, dict):
        if left.keys() != right.keys():
            return float("inf"), False
        pairs = [compare_policy_records(left[k],right[k]) for k in left]
    elif isinstance(left,list) and isinstance(right,list):
        if len(left) != len(right):
            return float("inf"),False
        pairs = [compare_policy_records(a,b) for a,b in zip(left,right)]
    elif isinstance(left,(int,float)) and isinstance(right,(int,float)):
        return abs(float(left)-float(right)),True
    else:
        return 0.,left==right
    return max((p[0] for p in pairs),default=0.),all(p[1] for p in pairs)


def main() -> None:
    question_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="检验第二问日前决策的信息边界")
    parser.add_argument(
        "--input",
        type=Path,
        default=question_dir / "output" / "input_data.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=question_dir / "output" / "information_boundary_test.json",
    )
    parser.add_argument(
        "--target-index",
        type=int,
        default=34,
        help="从0开始的测试日索引；默认34即2025-02-04",
    )
    parser.add_argument("--abs-tol", type=float, default=1e-5)
    parser.add_argument("--mip-gap", type=float, default=1e-6)
    parser.add_argument("--time-limit", type=float, default=30.0)
    parser.add_argument("--solution", type=Path, default=None, help="从正式结果读取本次策略及参数")
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if not 31 <= args.target_index < len(payload["dates"]):
        raise ValueError("测试日必须位于正式评价期且不得超出输入日期")
    target_date = str(payload["dates"][args.target_index])

    common = {
        "mip_gap": args.mip_gap,
        "time_limit": args.time_limit,
        "max_days": args.target_index + 1,
        "eta_charge": 0.9,
        "eta_discharge": 0.9,
        "reserve_quantile": 0.9,
        "reserve_mode": "positive_steps",
        "quantile_method": "linear",
        "objective_mode": "lexicographic",
        "collect_detail": False,
        "collect_formal_days": True,
    }
    if args.solution is not None:
        baseline_solution = json.loads(args.solution.read_text(encoding="utf-8"))
        diag = baseline_solution["diagnostics"]
        for key in ("eta_charge", "eta_discharge", "reserve_quantile", "reserve_mode",
                    "objective_mode", "purchase_strategy", "risk_window_days",
                    "risk_radius_periods", "purchase_quantile", "forecast_method",
                    "load_window_days", "load_trend_degree", "pv_window_days", "risk_grouping",
                    "parameter_mode", "emergency_budget_yuan"):
            if diag.get(key) is not None:
                common[key] = diag[key]
        common["quantile_method"] = diag["reserve_quantile_method"]
        if diag.get("parameter_mode") == "causal_monthly" and diag["tuning_protocol_sha256"] != protocol_hash():
            raise ValueError("当前调参规则与正式结果不一致")
        with args.solution.with_name("question_two_daily.csv").open(encoding="utf-8-sig",newline="") as stream:
            baseline_daily = list(csv.DictReader(stream))
    else:
        baseline_solution, baseline_daily, _ = run_model(payload, **common)

    changed = copy.deepcopy(payload)
    changed_load = np.asarray(changed["actual_load"][args.target_index:], dtype=float)
    changed_pv = np.asarray(changed["actual_pv"][args.target_index:], dtype=float)
    changed["actual_load"][args.target_index:] = (1.7 * changed_load + 123.0).tolist()
    changed["actual_pv"][args.target_index:] = (0.1 * changed_pv).tolist()
    changed_solution, changed_daily, _ = run_model(changed, **common)

    baseline_day = formal_day(baseline_solution, target_date)
    changed_day = formal_day(changed_solution, target_date)
    array_differences = {
        field: max_array_difference(baseline_day, changed_day, field)
        for field in ("grid", "charge", "discharge", "purchase_margin")
    }
    scalar_fields = (
        "reserve_kwh",
        "soc_start_kwh",
        "soc_end_kwh",
        "planned_grid_kwh",
        "planned_cost_yuan",
        "solver_objective_yuan",
        "primary_optimum_yuan",
        "storage_throughput_kwh",
        "peak_grid_kwh",
        "purchase_margin_kwh",
    )
    baseline_row = baseline_daily[args.target_index]
    changed_row = changed_daily[args.target_index]
    scalar_differences = {
        field: abs(float(baseline_row[field]) - float(changed_row[field]))
        for field in scalar_fields
    }
    source_fields = (
        "similar_load_dates",
        "similar_pv_dates",
        "scenario_source_dates",
        "reserve_latest_source_date",
        "objective_mode",
        "purchase_strategy",
        "risk_source_dates",
    )
    source_fields_equal = {
        field: baseline_row[field] == changed_row[field] for field in source_fields
    }
    baseline_policies = [u for u in baseline_solution.get("policy_updates",[]) if u["as_of_date"]<=target_date]
    policy_difference,policy_metadata_equal = compare_policy_records(
        baseline_policies,changed_solution.get("policy_updates",[]))
    maximum_plan_difference = max(
        [*array_differences.values(), *scalar_differences.values()]
    )
    settlement_cost_change = abs(
        float(baseline_row["actual_total_cost_yuan"])
        - float(changed_row["actual_total_cost_yuan"])
    )
    passed = bool(
        maximum_plan_difference <= args.abs_tol
        and all(source_fields_equal.values())
        and settlement_cost_change > args.abs_tol
        and policy_difference <= args.abs_tol
        and policy_metadata_equal
    )
    report = {
        "test": "current/future actual-data perturbation must not change day-ahead plan",
        "model_parameters": common,
        "includes_parameter_selection": common.get("parameter_mode")=="causal_monthly",
        "policy_records_compared": len(baseline_policies),
        "maximum_policy_numeric_difference": policy_difference,
        "policy_metadata_equal": policy_metadata_equal,
        "target_date": target_date,
        "target_index_zero_based": args.target_index,
        "perturbation": {
            "actual_load": "1.7 * original + 123 kWh per period",
            "actual_pv": "0.1 * original",
        },
        "maximum_plan_difference": maximum_plan_difference,
        "array_differences": array_differences,
        "scalar_differences": scalar_differences,
        "source_fields_equal": source_fields_equal,
        "actual_settlement_cost_change_yuan": settlement_cost_change,
        "absolute_tolerance": args.abs_tol,
        "passed": passed,
        "interpretation": (
            "测试日实际负荷和光伏只改变事后结算，不改变该日0:00形成的预测、"
            "历史来源、安全储备或日前调度；历史滚动模式还比较全部参数选择、候选评分与验证调度。"
        ),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    if not passed:
        raise RuntimeError(f"信息边界扰动测试失败: {report}")
    print(f"信息边界扰动测试通过: {args.report}")


if __name__ == "__main__":
    main()
