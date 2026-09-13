"""Behavioral checks for chronological forecasting and learned calendar effects."""
import unittest
from datetime import date, timedelta

import numpy as np

from forecast_calendar import calendar_load_forecast, recent_pv_forecast


class CalendarForecastTests(unittest.TestCase):
    def setUp(self):
        self.dates = [date(2025,1,1) + timedelta(days=k) for k in range(365)]
        # A plant closes Friday/Saturday, with a smooth changing seasonal level.
        k = np.arange(365)
        level = np.exp(.001*k + .000001*k*k)
        factors = np.asarray([.64 if d.weekday() in (4,5) else 1. for d in self.dates])
        self.load = (1000 * level * factors)[:,None] * np.ones((1,144))
        self.pv = np.maximum(0,np.sin(np.arange(144)*np.pi/72))[None,:] * (200+k[:,None])

    def test_nonstandard_weekly_pattern_is_learned(self):
        for target in range(100,107):
            prediction = calendar_load_forecast(self.load,self.dates,target)
            self.assertLess(float(np.max(np.abs(prediction/self.load[target]-1))), .005)

    def test_all_current_and_future_actuals_are_irrelevant(self):
        for target in (31,150,300):
            altered_load, altered_pv = self.load.copy(), self.pv.copy()
            altered_load[target:] *= 100
            altered_pv[target:] += 100000
            np.testing.assert_array_equal(calendar_load_forecast(self.load,self.dates,target),
                                          calendar_load_forecast(altered_load,self.dates,target))
            np.testing.assert_array_equal(recent_pv_forecast(self.pv,target),
                                          recent_pv_forecast(altered_pv,target))

    def test_zero_pv_remains_zero(self):
        np.testing.assert_array_equal(recent_pv_forecast(np.zeros_like(self.pv),31),np.zeros(144))


if __name__ == "__main__":
    unittest.main()
