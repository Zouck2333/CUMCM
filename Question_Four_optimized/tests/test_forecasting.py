from __future__ import annotations

from datetime import date, timedelta
import unittest
import json
from pathlib import Path
import tempfile
import hashlib

import numpy as np

from Question_Four_optimized.code.forecasting import (
    calendar_baseline, choose_price_candidate, price_candidates,
)
from Question_Four_optimized.code.model import DayData, HistoryModel, ModelConfig, run_day
from Question_Four_optimized.code.stage_solver import solve_stage
from Question_Four_optimized.code.run_question_four import _load_checkpoint
from Question_Four_optimized.tests.fixtures.stage_solver_baseline import solve_stage as reference_solve


class ForecastingTests(unittest.TestCase):
    def test_compatible_checkpoint_cannot_ignore_data_or_configuration_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);path=folder/'checkpoint_4-2.jsonl'
            content=json.dumps({'run_fingerprint':'old'})+'\n'
            path.write_text(content)
            manifest={'4-2':{'current_run_fingerprint':'current','original_run_fingerprint':'old',
                            'checkpoint_sha256':hashlib.sha256(content.encode()).hexdigest()}}
            (folder/'checkpoint_compatibility.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'another model'):
                _load_checkpoint(path,None,'4-2','changed_configuration')
            path.write_text(content+'\n')
            with self.assertRaisesRegex(ValueError,'content hash mismatch'):
                _load_checkpoint(path,None,'4-2','current')

    def test_weekly_trend_prediction_and_no_future_training(self):
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(42)]
        values = np.array([[100 * np.exp(.008 * i + .08 * d.weekday())]
                           for i, d in enumerate(dates)])
        pred = calendar_baseline(values[:-1], dates[:-1], dates[-1])
        self.assertLess(abs(pred[0] / values[-1, 0] - 1), .01)
        with self.assertRaises(ValueError):
            calendar_baseline(values, dates, dates[-1])

    def test_price_selector_uses_past_score_and_decay(self):
        errors = [np.array([.2, .1, .05])] * 7
        self.assertEqual(choose_price_candidate(errors), 2)
        self.assertEqual(choose_price_candidate(errors[:6]), 0)
        prices, _ = price_candidates(np.ones(144), np.ones(144), np.array([2.0]), 0)
        self.assertAlmostEqual(prices[2, 36], 1.5)
        np.testing.assert_equal(prices[:, 0], 2)
        with self.assertRaises(ValueError):
            price_candidates(np.ones(144), np.ones(144), np.ones(144), 0)

    def test_historical_residuals_match_original_time_forecasts(self):
        history = HistoryModel()
        for i in range(35):
            d = date(2025, 1, 1) + timedelta(days=i)
            day = DayData(d, np.full(144, 100 + i + 5*d.weekday()),
                          np.zeros(144), np.full(144, .5 + i*.005), np.zeros((4,24)))
            forecasts = [history.stage_forecast(d,k,day.load_kwh[:36*k],day.pv_kwh[:36*k],
                          day.price[:36*k+1],day.pv_forecast_kw[k]) for k in range(4)]
            history.append(day)
            for k, f in enumerate(forecasts):
                np.testing.assert_allclose(history.load_residuals[k][-1], day.load_kwh[36*k:]-f.load_hat)

    def test_future_actuals_and_later_releases_cannot_change_earlier_plans(self):
        history_days = []
        for i in range(32):
            d = date(2025, 1, 1) + timedelta(days=i)
            history_days.append(DayData(d, np.full(144, 120+3*d.weekday()), np.zeros(144),
                                       np.full(144,.5+.002*i), np.zeros((4,24))))
        base = DayData(date(2025,2,2),np.full(144,150.0),np.zeros(144),np.full(144,.6),np.zeros((4,24)))
        for stage in (0,1,2,3):
            tau=stage*36
            load=base.load_kwh.copy(); load[tau:]+=90
            pv=base.pv_kwh.copy(); pv[tau:]+=20
            price=base.price.copy(); price[tau+1:]*=3
            releases=base.pv_forecast_kw.copy(); releases[stage+1:]+=600
            changed=DayData(base.day,load,pv,price,releases)
            outputs=[]
            for current in (base,changed):
                history=HistoryModel()
                for day in history_days: history.append(day)
                outputs.append(run_day(history,current,6000,'4-3',ModelConfig(objective_mode='primary')))
            np.testing.assert_allclose(outputs[0].initial_grid,outputs[1].initial_grid,atol=1e-6)
            for k in range(stage+1):
                left,right=outputs[0].stages[k],outputs[1].stages[k]
                np.testing.assert_allclose(left.solution.grid,right.solution.grid,atol=1e-6)
                self.assertEqual(left.forecast.price_model,right.forecast.price_model)


class SolverEquivalenceTests(unittest.TestCase):
    def test_real_july_stage_negative_flow_is_repaired_without_clipping_soc(self):
        payload=json.loads((Path(__file__).parent/'fixtures/negative_bound_stage.json').read_text())
        args=[np.asarray(x) if isinstance(x,list) else x for x in payload['args']]
        kwargs=payload['kwargs']
        if kwargs.get('initial_grid') is not None: kwargs['initial_grid']=np.asarray(kwargs['initial_grid'])
        result=solve_stage(*args,**kwargs)
        self.assertGreaterEqual(float(np.min(result.discharge)),0)
        self.assertGreaterEqual(float(np.min(result.soc)),1200-1e-6)
        self.assertLess(float(np.max(np.abs(np.diff(result.soc)-.9*result.charge+result.discharge/.9))),1e-6)
        self.assertIn('high_precision_polish',result.stage_status)

    def test_continuous_adjustments_match_binary_reference(self):
        rng=np.random.default_rng(941)
        for n in (6,18):
            load=rng.uniform(50,300,n); pv=rng.uniform(0,250,n)
            sl=np.maximum(0,load+rng.normal(0,20,(3,n)))
            sp=np.maximum(0,pv+rng.normal(0,20,(3,n)))
            args=(rng.uniform(.2,1.5,n),load,pv,sl,sp,np.array([.2,.5,.3]),6000,1200,
                  rng.uniform(50,200,n))
            for objective in ('primary','lexicographic'):
                a=reference_solve(*args,objective_mode=objective)
                b=solve_stage(*args,objective_mode=objective)
                self.assertLess(abs(a.primary_value-b.primary_value),.02)
                if objective=='lexicographic':
                    self.assertLess(abs(a.throughput-b.throughput),.05)
                    self.assertLess(abs(a.peak_kw-b.peak_kw),.1)
                self.assertFalse(np.any((b.charge>1e-9)&(b.discharge>1e-9)))

    def test_risk_weight_cannot_fix_night_nominal_balance(self):
        for risk in (0,.1,1):
            r=solve_stage(np.ones(1),np.array([100.]),np.zeros(1),
                          np.array([[100.],[150.],[200.]]),np.zeros((3,1)),
                          np.full(3,1/3),1200,1200,risk_weight=risk,objective_mode='primary')
            np.testing.assert_allclose(r.scenario_emergency[:,0],[0,50,100])


if __name__ == '__main__':
    unittest.main()
