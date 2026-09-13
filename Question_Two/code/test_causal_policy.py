"""Boundary and risk-selection behavior for the historical tuning protocol."""
import unittest
from datetime import date, timedelta
import numpy as np
from causal_policy import margin_candidates,select_risk_candidate,tune_policy


class CausalPolicyTests(unittest.TestCase):
    def test_quantiles_match_independent_periodwise_samples(self):
        rng=np.random.default_rng(23)
        errors=rng.normal(size=(50,144))
        for target in (1,14,31,49):
            for radius in (0,3,6):
                qs=[.75,.8,.85,.99]
                actual=margin_candidates(errors,target,28,radius,qs)
                for t in (0,1,70,142,143):
                    sample=errors[max(1,target-28):target,max(0,t-radius):min(144,t+radius+1)].ravel()
                    expected=np.maximum(0,np.quantile(sample,qs,method='inverted_cdf')) if sample.size else np.zeros(4)
                    np.testing.assert_array_equal(actual[:,t],expected)

    def test_risk_budget_can_change_selected_quantile(self):
        rows=[dict(purchase_quantile=.8,risk_window_days=28,risk_radius_periods=3,
                   emergency_bound_yuan_per_day=3500.,surrogate_cost_yuan_per_day=5000.),
              dict(purchase_quantile=.9,risk_window_days=28,risk_radius_periods=3,
                   emergency_bound_yuan_per_day=1500.,surrogate_cost_yuan_per_day=5200.)]
        self.assertEqual(select_risk_candidate(rows,4000.)['purchase_quantile'],.8)
        self.assertEqual(select_risk_candidate(rows,2000.)['purchase_quantile'],.9)
        self.assertEqual(select_risk_candidate(rows,1000.)['purchase_quantile'],.9)

    def test_tuner_rejects_a_future_actual_tail(self):
        days=[date(2025,1,1)+timedelta(days=i) for i in range(31)]
        with self.assertRaises(ValueError):
            tune_policy(as_of=date(2025,2,1),dates_history=days,
                actual_load_history=np.ones((365,144)),actual_pv_history=np.ones((365,144)),
                prior_load=np.ones(144),prior_pv=np.ones(144),price=np.ones(144),
                eta_charge=.9,eta_discharge=.9,delta_h=1/6,reserve_mode='positive_steps',quantile_method='linear',
                mip_gap=1e-6,time_limit=30,objective_mode='lexicographic',emergency_spent=0,
                emergency_budget=1e6,formal_days_remaining=334,solve_day=None,compute_reserve=None)


if __name__=='__main__': unittest.main()
