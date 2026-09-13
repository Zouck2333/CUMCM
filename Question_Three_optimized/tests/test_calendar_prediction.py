from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sys
import unittest
import importlib.util

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from input_data import InputData
from load_prediction import calendar_load_baseline
from run_question_three import (historical_load_baseline, load_forecast,
                                select_scenarios, append_historical_residuals,
                                pv_forecast, decision_fingerprint)
from verify_question_three import independent_calendar_baseline, decision_fingerprint as audit_hash


def inputs():
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(70)]
    values = np.array([np.full(144, 100. + 70.*d.weekday()) for d in dates])
    return InputData(np.ones(144), values, np.zeros_like(values),
                     np.zeros((70, 4, 24)), dates, Path("."))


class CalendarTests(unittest.TestCase):
    def test_continuous_adjustment_has_same_optimum_as_binary_formulation(self):
        import milp_stage
        archive = Path(__file__).resolve().parent / "fixtures/milp_stage_reference.py"
        spec = importlib.util.spec_from_file_location("q3_archived_milp", archive)
        archived = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = archived
        spec.loader.exec_module(archived)
        load = np.array([200., 800., 500., 350., 200., 100.])
        pv = np.array([0., 0., 100., 150., 0., 0.])
        args = (np.array([.2, .5, 1., .8, .5, .2]), load, pv,
                np.array([load*.9, load, load*1.1]), np.array([pv*.9, pv, pv*1.1]),
                np.array([.3, .4, .3]), 6000., 1200.,
                np.array([500., 400., 100., 300., 50., 200.]))
        before = archived.solve_stage(*args, objective_mode="primary")
        after = milp_stage.solve_stage(*args, objective_mode="primary")
        self.assertAlmostEqual(before.primary_value, after.primary_value, places=4)
        self.assertFalse(np.any((after.charge > 1e-7) & (after.discharge > 1e-7)))

    def test_future_and_current_observations_cannot_change_midnight_forecast(self):
        data = inputs()
        expected = historical_load_baseline(data, 50)
        data.load_kwh[50:] = np.nan
        np.testing.assert_array_equal(expected, historical_load_baseline(data, 50))

    def test_learns_distinct_weekdays(self):
        data = inputs()
        calendar_error, old_error = [], []
        for day in range(35, 70):
            calendar_error.append(np.mean(abs(historical_load_baseline(data, day)-data.load_kwh[day])))
            old_error.append(np.mean(abs(historical_load_baseline(data, day, forecast_mode="four_day")
                                         -data.load_kwh[day])))
        self.assertLess(np.mean(calendar_error), .05*np.mean(old_error))

    def test_cold_start_never_reads_the_first_day_actuals(self):
        data = inputs()
        data.load_kwh[0] = 1e8
        np.testing.assert_array_equal(historical_load_baseline(data, 0), np.zeros(144))
        np.testing.assert_array_equal(historical_load_baseline(data, 1), data.load_kwh[0])
        np.testing.assert_array_equal(historical_load_baseline(data, 13), data.load_kwh[6])

    def test_independent_forecast_and_source_fingerprints_agree(self):
        data = inputs()
        for day in (0, 7, 14, 50):
            np.testing.assert_allclose(historical_load_baseline(data, day),
                                       independent_calendar_baseline(data, day), atol=1e-7)
        self.assertEqual(decision_fingerprint(data), audit_hash(data))

    def test_all_release_inputs_ignore_unpublished_data(self):
        for stage in range(4):
            with self.subTest(stage=stage):
                data = inputs()
                target, tau = 50, stage*36
                baselines, loads, pvs = [], [[], [], [], []], [[], [], [], []]
                for day in range(target):
                    base = historical_load_baseline(data, day)
                    append_historical_residuals(data, day, base, loads, pvs)
                    baselines.append(base)

                def issued_inputs():
                    base = historical_load_baseline(data, target)
                    load = load_forecast(base, data.load_kwh[target, :tau], stage)
                    pv = pv_forecast(data, target, stage)
                    scenarios = select_scenarios(data, target, stage, load, pv, loads,
                                                 pvs, baselines, base)
                    return load, pv, *scenarios[:3]
                before = issued_inputs()
                data.load_kwh[target, tau:] = 1e8
                data.pv_kwh[target, tau:] = 1e8
                data.load_kwh[target+1:] = 1e9
                data.pv_forecast_kw[target, stage+1:] = 1e9
                data.pv_forecast_kw[target+1:] = 1e9
                for a, b in zip(before, issued_inputs()):
                    np.testing.assert_array_equal(a, b)


if __name__ == "__main__":
    unittest.main()
