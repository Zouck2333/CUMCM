from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
from scipy.optimize import OptimizeResult


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

import milp_stage
from input_data import InputData
from output_data import _normalize_day, write_outputs
from run_question_three import select_scenarios


def synthetic_inputs(days: int) -> InputData:
    return InputData(
        prices=np.ones(144),
        load_kwh=np.zeros((days, 144)),
        pv_kwh=np.zeros((days, 144)),
        pv_forecast_kw=np.zeros((days, 4, 24)),
        dates=[date(2025, 1, 1) + timedelta(days=i) for i in range(days)],
        template_path=Path("."),
    )


def residuals(days: int) -> list[list[np.ndarray]]:
    return [[np.zeros(144 - 36 * k) for _ in range(days)] for k in range(4)]


class ScenarioTests(unittest.TestCase):
    def test_nominal_and_single_history(self) -> None:
        data = synthetic_inputs(2)
        errors = residuals(1)
        future = np.zeros(108)
        nominal = select_scenarios(
            data, 0, 1, future, future, errors, errors, [], np.zeros(144)
        )
        single = select_scenarios(
            data, 1, 1, future, future, errors, errors,
            [np.zeros(144)], np.zeros(144),
        )
        self.assertEqual(nominal[2].tolist(), [1.0])
        self.assertEqual(single[2].tolist(), [1.0])

    def test_zero_variance_and_extreme_prefix_distance(self) -> None:
        data = synthetic_inputs(3)
        data.load_kwh[2, :36] = 1e6
        errors = residuals(2)
        future = np.zeros(108)
        _, _, probabilities, representatives = select_scenarios(
            data, 2, 1, future, future, errors, errors,
            [np.zeros(144), np.zeros(144)], np.zeros(144),
        )
        self.assertEqual(len(representatives), 2)
        self.assertTrue(np.all(np.isfinite(probabilities)))
        self.assertTrue(np.all(probabilities > 0))
        self.assertAlmostEqual(float(np.sum(probabilities)), 1.0, places=12)

    def test_five_scenario_grouping(self) -> None:
        data = synthetic_inputs(7)
        errors = residuals(6)
        future = np.zeros(144)
        _, _, probabilities, representatives = select_scenarios(
            data, 6, 0, future, future, errors, errors,
            [np.zeros(144) for _ in range(6)], np.zeros(144),
            scenario_count=5,
        )
        self.assertEqual(len(representatives), 5)
        self.assertEqual(len(set(representatives)), 5)
        self.assertTrue(np.all(probabilities > 0))
        self.assertAlmostEqual(float(np.sum(probabilities)), 1.0, places=12)

    def test_current_future_actuals_do_not_enter_scenarios(self) -> None:
        data = synthetic_inputs(4)
        errors = residuals(3)
        future = np.zeros(108)
        args = (
            data, 3, 1, future, future, errors, errors,
            [np.zeros(144) for _ in range(3)], np.zeros(144),
        )
        before = select_scenarios(*args)
        data.load_kwh[3, 36:] = 1e6
        data.pv_kwh[3, 36:] = 1e6
        after = select_scenarios(*args)
        for index in range(3):
            np.testing.assert_array_equal(before[index], after[index])
        self.assertEqual(before[3], after[3])


class StageTests(unittest.TestCase):
    def test_terminal_soc_is_not_fixed_to_initial_soc(self) -> None:
        demand = np.full(6, 100.0)
        result = milp_stage.solve_stage(
            np.ones(6), demand, np.zeros(6),
            demand[None, :], np.zeros((1, 6)), np.array([1.0]),
            6000.0, 1200.0,
        )
        self.assertEqual(result.soc[0], 6000.0)
        self.assertLess(result.soc[-1], 6000.0)
        self.assertGreaterEqual(result.soc[-1], 1200.0)
        self.assertEqual(set(result.stage_gaps) & {"cost_risk", "throughput", "peak"},
                         {"cost_risk", "throughput", "peak"})

    def test_time_limited_incumbent_is_not_accepted_as_optimal(self) -> None:
        fake = OptimizeResult(
            status=1, message="time limit", x=np.zeros(1), fun=1.0,
            mip_dual_bound=0.0, mip_gap=1.0,
        )
        with patch.object(milp_stage, "milp", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "did not prove optimality"):
                milp_stage.solve_stage(
                    np.ones(1), np.ones(1), np.zeros(1),
                    np.ones((1, 1)), np.zeros((1, 1)), np.array([1.0]),
                    6000.0, 1200.0,
                )

    def test_zero_risk_and_reserve_floor(self) -> None:
        demand = np.full(6, 100.0)
        common = (
            np.ones(6), demand, np.zeros(6),
            demand[None, :], np.zeros((1, 6)), np.array([1.0]),
        )
        zero_risk = milp_stage.solve_stage(
            *common, 6000.0, 1200.0, risk_weight=0.0, cvar_alpha=0.80
        )
        reserved = milp_stage.solve_stage(
            *common, 6000.0, 5900.0
        )
        self.assertLess(zero_risk.soc[-1], 6000.0)
        self.assertGreaterEqual(reserved.soc[-1], 5900.0 - 1e-6)


class OutputTests(unittest.TestCase):
    def test_official_workbook_allows_variable_daily_soc(self) -> None:
        record = {
            "date": "2025-02-01",
            **{field: [0.0] * 144 for field in (
                "initial_purchase", "final_purchase", "charge",
                "discharge", "emergency",
            )},
            **{field: 0.0 for field in (
                "plan_cost", "adjustment_cost", "emergency_cost", "total_cost",
            )},
            "soc_start": 6000.0,
            "soc_end": 6000.0,
        }
        self.assertEqual(_normalize_day(record)["soc_end"], 6000.0)
        record["soc_end"] = 5999.0
        self.assertEqual(_normalize_day(record)["soc_end"], 5999.0)
        next_day = dict(record, date="2025-02-02", soc_start=6000.0)
        with self.assertRaisesRegex(ValueError, "previous closing SOC"):
            write_outputs([record, next_day], Path("."), Path("."))
        record["soc_end"] = 1199.0
        with self.assertRaisesRegex(ValueError, "SOC must remain"):
            _normalize_day(record)


if __name__ == "__main__":
    unittest.main()
