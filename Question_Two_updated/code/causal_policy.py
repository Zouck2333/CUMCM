"""Monthly policy selection from an explicitly truncated historical prefix.

The candidate set and scoring rules are fixed before the formal run. No annual
outcome, saved winning parameter schedule or perfect-information result is read.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date

import numpy as np

from forecast_calendar import calendar_load_forecast, recent_pv_forecast


PROTOCOL = {
    "version": "causal-monthly-v1",
    "update_frequency": "first day of each formal month",
    "validation_days": 28,
    "minimum_forecast_history_days": 14,
    "load_window_candidates": [14, 21, 28, 42, 56],
    "load_degree_candidates": [1, 2],
    "pv_window_candidates": [3, 7, 14, 21],
    "risk_window_candidates": [14, 28, 56],
    "risk_radius_candidates": [0, 3, 6],
    "purchase_quantile_candidates": [.75, .8, .85, .9, .95, .975, .99],
    "risk_grouping": "all",
    "reserve_quantile_candidates": [0., .5, .75, .9],
    "reserve_validation_days": 14,
    "reserve_validation_initial_soc_kwh": 10800.,
    "reserve_validation_terminal_soc_min_kwh": 10800.,
    "warmup_reserve_quantile": .9,
    "forecast_score": "mean price-weighted absolute error per period",
    "risk_score": "mean daily sum p*(margin+5*positive(net_error-margin))",
    "risk_budget_rule": "remaining emergency budget / remaining formal calendar days",
    "risk_selection": "minimum surrogate cost among historically budget-feasible candidates; otherwise minimum emergency bound",
    "reserve_score": "actual validation total cost with continuous SOC and identical full-SOC endpoints",
    "tie_rule": "scores rounded to 8 decimals, then increasing degree/window/quantile/radius",
}


def protocol_hash() -> str:
    return hashlib.sha256(json.dumps(PROTOCOL, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def replay_forecasts(load, pv, dates, prior_load, prior_pv, parameters):
    """Historical candidate predictions; row k is fitted from rows strictly < k."""
    n = len(dates)
    pl, pp = np.empty_like(load), np.empty_like(pv)
    pl[0], pp[0] = prior_load, prior_pv
    for k in range(1, n):
        pl[k] = calendar_load_forecast(load[:k], dates, k,
            window=parameters["load_window_days"], degree=parameters["load_trend_degree"])
        pp[k] = recent_pv_forecast(pv[:k], k, window=parameters["pv_window_days"])
    return pl, pp


def margin_candidates(errors, target, window, radius, quantiles):
    """Vectorized nearest-rank quantiles; exclude the cold-start day and target."""
    sample = errors[max(1, target-window):target]
    if not len(sample):
        return np.zeros((len(quantiles), 144))
    padded = np.pad(sample, ((0,0),(radius,radius)), constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2*radius+1, axis=1)
    pooled = windows.transpose(0,2,1).reshape(-1,144)
    ordered = np.sort(pooled, axis=0)
    counts = np.sum(np.isfinite(ordered), axis=0)
    ranks = np.ceil(np.asarray(quantiles)[:,None]*counts).astype(int)-1
    return np.maximum(0., ordered[ranks, np.arange(144)[None,:]])


def select_risk_candidate(rows, daily_budget):
    feasible = [r for r in rows if r["emergency_bound_yuan_per_day"] <= daily_budget + 1e-8]
    def tie(r):
        return (r["purchase_quantile"], r["risk_window_days"], r["risk_radius_periods"])
    if feasible:
        return min(feasible, key=lambda r: (round(r["surrogate_cost_yuan_per_day"],8), *tie(r)))
    return min(rows, key=lambda r: (round(r["emergency_bound_yuan_per_day"],8),
                                   round(r["surrogate_cost_yuan_per_day"],8), *tie(r)))


def tune_policy(*, as_of: date, dates_history, actual_load_history, actual_pv_history,
                prior_load, prior_pv, price, eta_charge, eta_discharge, delta_h,
                reserve_mode, quantile_method, mip_gap, time_limit, objective_mode,
                emergency_spent, emergency_budget, formal_days_remaining,
                solve_day, compute_reserve):
    """All actual-data arguments end before as_of; callers cannot supply a future tail."""
    n = len(dates_history)
    load, pv = np.asarray(actual_load_history), np.asarray(actual_pv_history)
    if (n < 31 or load.shape != (n,144) or pv.shape != (n,144)
            or dates_history[-1] >= as_of or (as_of-dates_history[-1]).days != 1):
        raise ValueError("调参输入必须是截至目标日前一天的完整历史，首次至少包含1月31天")
    if any(d >= as_of for d in dates_history) or formal_days_remaining < 1:
        raise ValueError("调参输入含未来日期或剩余天数非法")
    validation = list(range(max(PROTOCOL["minimum_forecast_history_days"],
                                n-PROTOCOL["validation_days"]), n))
    load_scores, pv_scores = [], []
    for window in PROTOCOL["load_window_candidates"]:
        for degree in PROTOCOL["load_degree_candidates"]:
            errors = [calendar_load_forecast(load[:k], dates_history, k, window=window, degree=degree)-load[k]
                      for k in validation]
            load_scores.append({"load_window_days":window,"load_trend_degree":degree,
                "score":float(np.mean(np.abs(errors)*price))})
    best_load = min(load_scores, key=lambda r:(round(r["score"],8),r["load_trend_degree"],r["load_window_days"]))
    for window in PROTOCOL["pv_window_candidates"]:
        errors = [recent_pv_forecast(pv[:k],k,window=window)-pv[k] for k in validation]
        pv_scores.append({"pv_window_days":window,"score":float(np.mean(np.abs(errors)*price))})
    best_pv = min(pv_scores, key=lambda r:(round(r["score"],8),r["pv_window_days"]))
    selected = {k:v for r in (best_load,best_pv) for k,v in r.items() if k != "score"}
    predicted_load, predicted_pv = replay_forecasts(load,pv,dates_history,prior_load,prior_pv,selected)
    residual_load, residual_pv = load-predicted_load, pv-predicted_pv
    errors = residual_load-residual_pv
    risk_scores = []
    quantiles = PROTOCOL["purchase_quantile_candidates"]
    daily_budget = max(0.,emergency_budget-emergency_spent)/formal_days_remaining
    for window in PROTOCOL["risk_window_candidates"]:
        for radius in PROTOCOL["risk_radius_candidates"]:
            margins = np.stack([margin_candidates(errors,k,window,radius,quantiles) for k in validation])
            upper = np.maximum(0.,errors[validation,None,:]-margins)
            ec = np.mean(np.sum(5.*price*upper,axis=2),axis=0)
            extra = np.mean(np.sum(price*margins,axis=2),axis=0)
            for j,q in enumerate(quantiles):
                risk_scores.append({"risk_window_days":window,"risk_radius_periods":radius,"purchase_quantile":q,
                    "emergency_bound_yuan_per_day":float(ec[j]),"margin_cost_yuan_per_day":float(extra[j]),
                    "surrogate_cost_yuan_per_day":float(extra[j]+ec[j]),
                    "historical_budget_feasible":bool(ec[j]<=daily_budget+1e-8)})
    best_risk = select_risk_candidate(risk_scores,daily_budget)
    selected.update({k:best_risk[k] for k in ("risk_window_days","risk_radius_periods","purchase_quantile")})
    selected["risk_grouping"] = "all"
    reserve_validation = validation[-PROTOCOL["reserve_validation_days"]:]
    reserve_scores = []
    for q in PROTOCOL["reserve_quantile_candidates"]:
        soc = PROTOCOL["reserve_validation_initial_soc_kwh"]
        traces = []
        for k in reserve_validation:
            margin = margin_candidates(errors,k,selected["risk_window_days"],selected["risk_radius_periods"],
                                       [selected["purchase_quantile"]])[0]
            reserve = compute_reserve(residual_load[:k],residual_pv[:k],eta_discharge=eta_discharge,
                quantile=q,mode=reserve_mode,quantile_method=quantile_method)
            effective_reserve = 9600. if k == reserve_validation[-1] else reserve
            solved = solve_day(price,predicted_load[k],predicted_pv[k],np.empty((0,144)),np.empty((0,144)),
                soc,effective_reserve,delta_h,eta_charge,eta_discharge,mip_gap,time_limit,objective_mode,
                purchase_margin=margin,allow_grid_surplus=True)
            emergency = np.maximum(0.,load[k]-pv[k]-solved.grid+solved.charge-solved.discharge)
            plan_cost = float(price@solved.grid)
            emergency_cost = float(5.*price@emergency)
            traces.append({"date":dates_history[k].isoformat(),"soc_start":soc,"soc_end":float(solved.soc[-1]),
                "reserve_kwh":reserve,"effective_reserve_kwh":effective_reserve,
                "planned_cost_yuan":plan_cost,"emergency_cost_yuan":emergency_cost,
                "total_cost_yuan":plan_cost+emergency_cost,"solver_status":solved.status,"mip_gap":solved.mip_gap,
                "grid":solved.grid.tolist(),"charge":solved.charge.tolist(),"discharge":solved.discharge.tolist(),
                "soc":solved.soc.tolist()})
            soc = float(solved.soc[-1])
        reserve_scores.append({"reserve_quantile":q,"total_cost_yuan":sum(r["total_cost_yuan"] for r in traces),
            "emergency_cost_yuan":sum(r["emergency_cost_yuan"] for r in traces),"validation_days":traces})
    best_reserve = min(reserve_scores,key=lambda r:(round(r["total_cost_yuan"],8),r["reserve_quantile"]))
    selected["reserve_quantile"] = best_reserve["reserve_quantile"]
    history_digest = hashlib.sha256(load.astype("<f8").tobytes()+pv.astype("<f8").tobytes()).hexdigest()
    record = {"as_of_date":as_of.isoformat(),"history_start":dates_history[0].isoformat(),
        "history_end":dates_history[-1].isoformat(),"history_days":n,"history_actuals_sha256":history_digest,
        "validation_dates":[dates_history[k].isoformat() for k in validation],"protocol_sha256":protocol_hash(),
        "emergency_spent_yuan":emergency_spent,"emergency_budget_yuan":emergency_budget,
        "formal_days_remaining":formal_days_remaining,"daily_emergency_budget_yuan":daily_budget,
        "selected_parameters":selected,"load_candidates":load_scores,"pv_candidates":pv_scores,
        "risk_candidates":risk_scores,"reserve_candidates":reserve_scores}
    return selected,record,list(residual_load),list(residual_pv)
