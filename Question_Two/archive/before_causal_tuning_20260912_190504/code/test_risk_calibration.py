"""Regression checks for the economic/causal constraints added to Question Two."""
import sys
import unittest
from datetime import date, timedelta

import numpy as np

sys.dont_write_bytecode = True
from solve_question_two import calibrate_purchase_margin, solve_day_milp


class RiskCalibrationTests(unittest.TestCase):
    def test_joint_residual_and_truncated_time_window(self):
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(4)]
        load = [np.zeros(144), np.arange(144, dtype=float), np.arange(144, dtype=float) + 4]
        pv = [np.zeros(144), np.ones(144), np.ones(144)]
        margin, selected = calibrate_purchase_margin(3, dates, load, pv, radius_periods=1, quantile=.8)
        self.assertEqual(selected, [1, 2])
        # At midnight only t=0,1 are pooled; the previous night's t=143 is excluded.
        self.assertEqual(margin[0], 4.0)
        sample = sorted([9., 10., 11., 13., 14., 15.])
        self.assertEqual(margin[11], sample[4])

    def test_all_date_pool_does_not_apply_legacy_weekend_split(self):
        dates = [date(2025,1,1) + timedelta(days=i) for i in range(32)]
        load = [np.full(144,float(i)) for i in range(31)]
        pv = [np.zeros(144) for _ in range(31)]
        _, selected = calibrate_purchase_margin(31, dates, load, pv)
        self.assertEqual(selected, list(range(3,31)))
        _, legacy = calibrate_purchase_margin(31, dates, load, pv, grouping="legacy")
        self.assertLess(len(legacy),len(selected))
        self.assertTrue(all(dates[k].weekday() >= 5 for k in legacy))

    def test_reject_current_or_future_residual(self):
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(4)]
        with self.assertRaises(ValueError):
            calibrate_purchase_margin(2, dates, [np.zeros(144)] * 3, [np.zeros(144)] * 3)

    def test_no_pv_can_still_prebuy_risk_margin(self):
        load = np.full(144, 100.)
        margin = np.full(144, 20.)
        result = solve_day_milp(
            np.ones(144), load, np.zeros(144), np.empty((0, 144)), np.empty((0, 144)),
            1200., 0., 1 / 6, .9, .9, 1e-7, 30., "lexicographic",
            purchase_margin=margin, allow_grid_surplus=True,
        )
        np.testing.assert_allclose(result.grid, 120., atol=1e-5)
        np.testing.assert_allclose(result.soc, 1200., atol=1e-5)
        self.assertGreater(np.min(result.curtailment), 19.999)
        self.assertAlmostEqual(result.objective, 17280., places=3)


if __name__ == "__main__":
    unittest.main()
