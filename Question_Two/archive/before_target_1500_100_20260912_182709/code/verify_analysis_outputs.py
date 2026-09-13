from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


EXPECTED_CASES = (
    "no_reserve_eta_090",
    "q075_eta_090",
    "q090_eta_090",
    "q090_eta_sqrt090",
    "q090_eta090_cumulative_net",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    question_dir = Path(__file__).resolve().parent.parent
    output_dir = question_dir / "output"
    parser = argparse.ArgumentParser(description="核验第二问附加分析结果")
    parser.add_argument("--output-dir", type=Path, default=output_dir)
    parser.add_argument(
        "--report",
        type=Path,
        default=output_dir / "analysis_verification.json",
    )
    args = parser.parse_args()

    root = args.output_dir
    main_path = root / "question_two_solution.json"
    daily_path = root / "question_two_daily.csv"
    input_path = root / "input_data.json"
    main = json.loads(main_path.read_text(encoding="utf-8"))
    diagnostics = main["diagnostics"]
    verification = json.loads(
        (root / "verification_report.json").read_text(encoding="utf-8")
    )
    boundary = json.loads(
        (root / "information_boundary_test.json").read_text(encoding="utf-8")
    )
    comparisons = json.loads(
        (root / "comparisons" / "comparison_results.json").read_text(
            encoding="utf-8"
        )
    )
    benchmark = json.loads(
        (root / "benchmarks" / "perfect_information_benchmark.json").read_text(
            encoding="utf-8"
        )
    )

    checks: dict[str, bool] = {}
    checks["main_verification_passed"] = verification.get("status") == "PASS"
    checks["information_boundary_passed"] = boundary.get("passed") is True
    checks["five_expected_comparison_cases"] = tuple(
        row.get("case") for row in comparisons
    ) == EXPECTED_CASES
    checks["comparison_horizon_complete"] = all(
        int(row.get("formal_days", 0)) == 334 for row in comparisons
    )
    checks["comparison_objective_consistent"] = all(
        row.get("objective_mode") == "lexicographic" for row in comparisons
    )
    checks["comparison_values_finite"] = all(
        math.isfinite(float(row[key]))
        for row in comparisons
        for key in (
            "formal_total_cost_yuan",
            "formal_total_emergency_kwh",
            "formal_average_reserve_kwh",
        )
    )
    matching = [row for row in comparisons if all(
        row.get(key) == diagnostics.get(key) for key in
        ("reserve_quantile", "reserve_mode", "eta_charge", "eta_discharge",
         "purchase_strategy", "risk_window_days", "risk_radius_periods", "purchase_quantile"))]
    checks["comparison_main_matches_solution"] = len(matching) == 1 and math.isclose(
        float(matching[0]["formal_total_cost_yuan"]),
        float(diagnostics["formal_total_cost_yuan"]), rel_tol=0.0, abs_tol=1e-6)
    checks["comparison_risk_policy_matches"] = all(all(row.get(key) == diagnostics.get(key) for key in
        ("purchase_strategy", "risk_window_days", "risk_radius_periods", "purchase_quantile")) for row in comparisons)
    checks["benchmark_ordering_passed"] = (
        benchmark["ordering_check"].get("passed") is True
    )
    checks["benchmark_physics_passed"] = bool(
        benchmark["matched_milp"]["checks"].get("passed")
        and benchmark["lp_lower"]["checks"].get("passed")
    )
    provenance = benchmark["provenance"]
    checks["benchmark_input_hash_matches"] = (
        sha256(input_path) == provenance["input_sha256"]
    )
    checks["benchmark_daily_hash_matches"] = (
        sha256(daily_path) == provenance["daily_csv_sha256"]
    )
    checks["benchmark_main_hash_matches"] = (
        sha256(main_path) == provenance["main_json_sha256"]
    )
    checks["summary_exists"] = (root / "result_summary.md").is_file()

    failed = [name for name, passed in checks.items() if not passed]
    report = {
        "status": "PASS" if not failed else "FAIL",
        "checks": checks,
        "failures": failed,
        "comparison_cases": list(EXPECTED_CASES),
        "gap_info_percent": benchmark["gaps"]["gap_info_percent"],
        "gap_lp_percent": benchmark["gaps"]["gap_lp_percent"],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    if failed:
        raise RuntimeError(f"附加分析核验失败: {failed}")
    print(f"附加分析核验通过: {args.report}")


if __name__ == "__main__":
    main()
