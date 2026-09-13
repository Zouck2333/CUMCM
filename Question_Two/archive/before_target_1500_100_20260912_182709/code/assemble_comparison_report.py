from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from run_model_comparisons import CONFIGURATIONS, write_csv, write_markdown
from solve_question_two import SOC_MAX, SOC_MIN


def read_daily(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def main_row(main_json: Path, main_daily: Path) -> dict[str, object]:
    solution = json.loads(main_json.read_text(encoding="utf-8"))
    diagnostics = solution["diagnostics"]
    daily_rows = read_daily(main_daily)
    formal = [row for row in daily_rows if row["warmup"].lower() == "false"]
    reserves = np.asarray([float(row["reserve_kwh"]) for row in formal])
    config = next(item for item in CONFIGURATIONS if item["case"] == "q090_eta_090")
    return {
        "case": config["case"],
        "description": config["description"],
        "objective_mode": diagnostics.get("objective_mode", "lexicographic"),
        "reserve_quantile": diagnostics["reserve_quantile"],
        "reserve_mode": diagnostics["reserve_mode"],
        "eta_charge": diagnostics["eta_charge"],
        "eta_discharge": diagnostics["eta_discharge"],
        "round_trip_efficiency": float(diagnostics["eta_charge"])
        * float(diagnostics["eta_discharge"]),
        "formal_days": diagnostics["formal_days"],
        "formal_total_grid_kwh": diagnostics["formal_total_grid_kwh"],
        "formal_plan_cost_yuan": diagnostics["formal_plan_cost_yuan"],
        "formal_emergency_cost_yuan": diagnostics["formal_emergency_cost_yuan"],
        "formal_total_cost_yuan": diagnostics["formal_total_cost_yuan"],
        "formal_total_emergency_kwh": diagnostics["formal_total_emergency_kwh"],
        "formal_average_reserve_kwh": float(np.mean(reserves)),
        "formal_max_reserve_kwh": float(np.max(reserves)),
        "formal_reserve_cap_days": int(
            np.sum(reserves >= (SOC_MAX - SOC_MIN) - 1e-6)
        ),
        "total_solver_seconds": float(
            sum(float(row["solver_seconds"]) for row in daily_rows)
        ),
        "lexicographic_tolerance_relaxed_days": int(
            sum(
                row.get("lexicographic_tolerance_relaxed", "False").lower()
                == "true"
                for row in daily_rows
            )
        ),
        "tolerance_relaxed_occurred": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="合并并行完成的第二问灵敏度结果")
    parser.add_argument("--main-json", type=Path, required=True)
    parser.add_argument("--main-daily", type=Path, required=True)
    parser.add_argument("--case-json", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    by_case: dict[str, dict[str, object]] = {
        "q090_eta_090": main_row(args.main_json, args.main_daily)
    }
    for path in args.case_json:
        values = json.loads(path.read_text(encoding="utf-8"))
        for row in values:
            case = str(row["case"])
            if case in by_case:
                raise ValueError(f"方案重复: {case}")
            row["objective_mode"] = "lexicographic"
            if case == "q075_eta_090":
                row.setdefault("lexicographic_tolerance_relaxed_days", None)
                row["tolerance_relaxed_occurred"] = True
                row["tolerance_note"] = (
                    "严格锁定曾发生数值不可行；自适应数值容差重试成功，"
                    "分组摘要未保留逐日重试次数。"
                )
            else:
                row.setdefault("lexicographic_tolerance_relaxed_days", 0)
                row["tolerance_relaxed_occurred"] = False
            by_case[case] = row

    expected = [str(item["case"]) for item in CONFIGURATIONS]
    missing = [case for case in expected if case not in by_case]
    extra = [case for case in by_case if case not in expected]
    if missing or extra:
        raise ValueError(f"方案集合不完整: missing={missing}, extra={extra}")

    rows = [by_case[case] for case in expected]
    main_cost = float(by_case["q090_eta_090"]["formal_total_cost_yuan"])
    main_emergency = float(
        by_case["q090_eta_090"]["formal_total_emergency_kwh"]
    )
    for row in rows:
        row.setdefault("tolerance_note", "")
        row["cost_difference_vs_main_yuan"] = (
            float(row["formal_total_cost_yuan"]) - main_cost
        )
        row["emergency_difference_vs_main_kwh"] = (
            float(row["formal_total_emergency_kwh"]) - main_emergency
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "comparison_results.csv", rows)
    (args.output_dir / "comparison_results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    write_markdown(
        args.output_dir / "comparison_results.md",
        rows,
        "lexicographic",
    )
    print(f"已合并{len(rows)}组正式灵敏度结果: {args.output_dir}")


if __name__ == "__main__":
    main()
