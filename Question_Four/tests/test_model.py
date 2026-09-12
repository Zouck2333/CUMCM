from __future__ import annotations

from datetime import date, timedelta
import unittest

import numpy as np

from Question_Four.code.model import (
    DayData,
    HistoryModel,
    ModelConfig,
    run_day,
)
from Question_Four.code.price_stress import executed_price_band, stage_price_band, stress_test
from Question_Four.code.stage_solver import solve_stage


def make_day(
    day: date,
    *,
    load: float = 100.0,
    pv: float = 0.0,
    price: float = 1.0,
    hourly_pv_kw: float = 0.0,
) -> DayData:
    return DayData(
        day,
        np.full(144, load),
        np.full(144, pv),
        np.full(144, price),
        np.full((4, 24), hourly_pv_kw),
    )


class ForecastTests(unittest.TestCase):
    def test_hourly_pv_mapping_and_first_day_price(self) -> None:
        history = HistoryModel()
        release = np.arange(1.0, 25.0) * 6.0
        forecast = history.stage_forecast(
            date(2025, 1, 1), 2,
            np.zeros(72), np.zeros(72), np.ones(73) * 0.8, release,
        )
        np.testing.assert_allclose(forecast.pv_hat[:6], 1.0)
        np.testing.assert_allclose(forecast.pv_hat[6:12], 2.0)
        np.testing.assert_allclose(forecast.price, 0.8)
        self.assertEqual(forecast.probabilities.tolist(), [1.0])

    def test_three_group_prior_and_positive_posterior(self) -> None:
        history = HistoryModel()
        for h in range(6):
            history.append(make_day(
                date(2025, 1, 1) + timedelta(days=h),
                pv=float(h), price=1.0 + h / 10.0,
            ))
        target = date(2025, 1, 7)
        initial = history.stage_forecast(
            target, 0, np.zeros(0), np.zeros(0), np.ones(1), np.zeros(24),
        )
        np.testing.assert_allclose(initial.probabilities, np.full(3, 1.0 / 3.0))
        forecast = history.stage_forecast(
            target, 1, np.full(36, 100.0), np.zeros(36),
            np.ones(37), np.zeros(24),
        )
        self.assertEqual(forecast.scenario_load.shape, (3, 108))
        self.assertEqual(len(forecast.representative_days), 3)
        self.assertTrue(np.all(forecast.probabilities > 0))
        self.assertAlmostEqual(float(np.sum(forecast.probabilities)), 1.0)

    def test_stage_zero_does_not_see_future_actuals(self) -> None:
        previous = make_day(date(2025, 1, 1), price=1.2)
        base = make_day(date(2025, 1, 2), price=1.0)
        changed_load = base.load_kwh.copy()
        changed_pv = base.pv_kwh.copy()
        changed_price = base.price.copy()
        changed_load[1:] += 300.0
        changed_pv[1:] += 40.0
        changed_price[1:] = 4.0
        changed_releases = base.pv_forecast_kw.copy()
        changed_releases[1:] = 300.0
        changed = DayData(base.day, changed_load, changed_pv, changed_price, changed_releases)
        outputs = []
        for target in (base, changed):
            history = HistoryModel()
            history.append(previous)
            outputs.append(run_day(
                history, target, 6000.0, "4-2", ModelConfig(objective_mode="primary")
            ))
        np.testing.assert_allclose(
            outputs[0].stages[0].forecast.price,
            outputs[1].stages[0].forecast.price,
        )
        np.testing.assert_allclose(outputs[0].initial_grid, outputs[1].initial_grid)
        with self.assertRaises(ValueError):
            HistoryModel().stage_forecast(
                base.day, 0, np.zeros(0), np.zeros(0),
                base.price, base.pv_forecast_kw[0],
            )

    def test_terminal_reserve_and_last_day_value(self) -> None:
        history = HistoryModel()
        history.append(make_day(date(2025, 1, 1), load=100.0, pv=0.0, price=2.0))
        self.assertAlmostEqual(history.terminal_reserve(date(2025, 1, 2), 0.90), 4000.0)
        self.assertAlmostEqual(history.terminal_price(date(2025, 1, 2), 0.0), 2.0)
        self.assertEqual(HistoryModel().terminal_price(date(2025, 12, 31), 0.0), 0.0)


class SolverTests(unittest.TestCase):
    def test_terminal_credit_changes_battery_decision(self) -> None:
        args = (
            np.array([0.1]), np.zeros(1), np.zeros(1),
            np.zeros((1, 1)), np.zeros((1, 1)), np.array([1.0]),
            1200.0, 1200.0,
        )
        base = solve_stage(*args, objective_mode="primary")
        valued = solve_stage(*args, terminal_credit_per_soc=1.0, objective_mode="primary")
        self.assertAlmostEqual(float(base.charge[0]), 0.0)
        self.assertGreater(float(valued.charge[0]), 800.0)
        self.assertGreater(float(valued.soc[-1]), float(base.soc[-1]))
        scenario_fee = 0.1 * float(valued.grid[0] + 5.0 * valued.scenario_emergency[0, 0])
        self.assertAlmostEqual(
            valued.primary_value,
            1.1 * scenario_fee - (float(valued.soc[-1]) - 1200.0),
            places=5,
        )

    def test_rolling_settlement_and_price_stress(self) -> None:
        previous = make_day(date(2025, 1, 1), price=2.0)
        target = make_day(date(2025, 1, 2), load=150.0, price=1.0)
        history = HistoryModel()
        history.append(previous)
        result = run_day(history, target, 6000.0, "4-3")
        result.verify(target)
        self.assertEqual(len(result.stages), 4)
        self.assertAlmostEqual(result.total_cost, (
            result.plan_cost + result.adjustment_cost + result.emergency_cost
        ))
        self.assertGreater(float(np.sum(result.up)), 0.0)
        self.assertGreater(result.adjustment_cost, 0.0)
        once_history = HistoryModel()
        once_history.append(previous)
        once = run_day(once_history, target, 6000.0, "4-2")
        np.testing.assert_allclose(result.initial_grid, once.initial_grid, atol=1e-5)
        band = executed_price_band(result, [previous])
        self.assertEqual(np.flatnonzero(band.known).tolist(), [0, 36, 72, 108])
        stage_band = stage_price_band(result.stages[2].forecast, [previous])
        self.assertEqual(len(stage_band.center), 72)
        self.assertEqual(np.flatnonzero(stage_band.known).tolist(), [0])
        self.assertGreater(float(np.min(stage_band.lower)), 0.0)
        zero = stress_test(result, [previous], 0.0, actual_price=target.price)
        higher = stress_test(result, [previous], 6.0, actual_price=target.price)
        self.assertAlmostEqual(zero.center_cost, zero.worst_cost)
        self.assertGreaterEqual(higher.worst_cost, zero.worst_cost)
        np.testing.assert_allclose(higher.worst_price[band.known], target.price[band.known])
        self.assertTrue(np.all(higher.worst_price > 0))


if __name__ == "__main__":
    unittest.main()
