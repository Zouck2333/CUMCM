from __future__ import annotations

from pathlib import Path

import solve_question_one as q1


def run_case(method: str, objective_mode: str) -> None:
    frame = q1.load_day_data(q1.DEFAULT_INPUT)
    result, info = q1.solve_dispatch(
        frame,
        method=method,
        objective_mode=objective_mode,
        eta=q1.DEFAULT_ETA,
        soc_min=q1.DEFAULT_SOC_MIN,
        soc_max=q1.DEFAULT_SOC_MAX,
        soc_initial=q1.DEFAULT_SOC_INITIAL,
        soc_terminal=q1.DEFAULT_SOC_INITIAL,
        power_limit_kw=q1.DEFAULT_POWER_LIMIT_KW,
        mip_gap=1.0e-8,
        time_limit=None,
        solver_python=None,
    )
    checks = q1.validate_solution(
        result,
        energy_limit=q1.DEFAULT_POWER_LIMIT_KW * q1.DELTA_H,
        eta=q1.DEFAULT_ETA,
        soc_min=q1.DEFAULT_SOC_MIN,
        soc_max=q1.DEFAULT_SOC_MAX,
        soc_terminal=q1.DEFAULT_SOC_INITIAL,
    )
    print(f"{method.upper()} {objective_mode}")
    print(f"  objective_value: {info['objective_value']:.9f}")
    print(f"  total_cost: {checks['total_cost']:.9f}")
    print(f"  throughput: {info['throughput']:.9f}")
    print(f"  peak_kw: {info['peak_kw']:.9f}")
    print(f"  balance_error: {checks['max_balance_error']:.12e}")
    print(f"  soc_error: {checks['max_soc_error']:.12e}")
    print(
        "  simultaneous_periods: "
        f"{checks['simultaneous_charge_discharge_periods']}"
    )
    print(f"  power_violations: {checks['power_violations']}")


def main() -> None:
    print(f"input: {q1.DEFAULT_INPUT}")
    print(f"input_exists: {Path(q1.DEFAULT_INPUT).exists()}")
    run_case("lp", "cost")
    run_case("milp", "cost")
    run_case("milp", "lexicographic")


if __name__ == "__main__":
    main()
