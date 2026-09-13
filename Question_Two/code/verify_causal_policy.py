"""Independent audit of forecasts, tuning scores, past-only sources and witnesses.

This module does not import the forecasting implementation, tuner or MILP solver.
"""
from __future__ import annotations
import hashlib
import json
import math
from datetime import date

import numpy as np


class HistoricalPredictions:
    def __init__(self, inputs):
        self.inputs = inputs
        self.dates = [date.fromisoformat(d) for d in inputs["dates"]]
        self.load = np.asarray(inputs["actual_load"],dtype=float)
        self.pv = np.asarray(inputs["actual_pv"],dtype=float)
        self.load_cache, self.pv_cache = {}, {}

    def load_hat(self, target, window, degree):
        key=(target,window,degree)
        if key not in self.load_cache:
            if target == 0:
                value=np.asarray(self.inputs["prior_load"])
            elif target < 14:
                value=self.load[max(0,target-7)].copy()
            else:
                indices=list(range(max(0,target-window),target))
                tau=np.asarray([(k-target)/28. for k in indices])
                x=np.column_stack([np.ones(len(indices))]+[tau**j for j in range(1,degree+1)]
                    +[[float(self.dates[k].weekday()==j) for k in indices] for j in range(6)])
                ridge=np.eye(x.shape[1])*.1
                ridge[0,0]=.0001
                beta=np.linalg.lstsq(np.vstack((x,ridge)),
                    np.vstack((np.log(np.maximum(1,self.load[indices])),np.zeros((x.shape[1],144)))),rcond=None)[0]
                query=np.asarray([1.]+[0.]*degree+[float(self.dates[target].weekday()==j) for j in range(6)])
                value=np.exp(query@beta)
            self.load_cache[key]=value
        return self.load_cache[key]

    def pv_hat(self,target,window):
        key=(target,window)
        if key not in self.pv_cache:
            if target == 0:
                value=np.asarray(self.inputs["prior_pv"])
            elif target < 3:
                value=self.pv[target-1].copy()
            else:
                indices=list(range(max(0,target-window),target))
                z=np.column_stack((np.ones(len(indices)),(np.asarray(indices)-target)/window))
                beta=np.linalg.lstsq(np.vstack((z,np.diag([.0001,math.sqrt(.3)]))),
                    np.vstack((self.pv[indices],np.zeros((2,144)))),rcond=None)[0]
                value=np.maximum(0,beta[0])
            self.pv_cache[key]=value
        return self.pv_cache[key]

    def pair(self,target,p):
        return self.load_hat(target,p["load_window_days"],p["load_trend_degree"]),self.pv_hat(target,p["pv_window_days"])

    def errors(self,end,p):
        return np.asarray([self.load[k]-self.pv[k]-self.pair(k,p)[0]+self.pair(k,p)[1] for k in range(end)])


def independent_margins(errors,target,window,radius,quantiles):
    result=np.zeros((len(quantiles),144))
    for t in range(144):
        sample=errors[max(1,target-window):target,max(0,t-radius):min(144,t+radius+1)].ravel()
        if sample.size:
            ranks=[math.ceil(q*sample.size)-1 for q in quantiles]
            result[:,t]=np.maximum(0,np.partition(sample,ranks)[ranks])
    return result


def independent_reserve(errors,q,diag):
    if q <= 0 or len(errors) < 5:
        return 0.
    increments=np.maximum(0,errors) if diag["reserve_mode"]=="positive_steps" else errors
    needs=np.maximum(0,np.cumsum(increments,axis=1).max(axis=1))
    return min(9600.,float(np.quantile(needs,q,method=diag["reserve_quantile_method"]))/diag["eta_discharge"])


def verify_causal_solution(audit,solution,inputs,daily_by_date,detail_by_date,abs_tol):
    diag=solution["diagnostics"]
    protocol=solution["tuning_protocol"]
    encoded=json.dumps(protocol,sort_keys=True,separators=(",",":")).encode()
    digest=hashlib.sha256(encoded).hexdigest()
    updates=solution["policy_updates"]
    dates=inputs["dates"]
    predictions=HistoricalPredictions(inputs)
    price=np.asarray(inputs["price"],dtype=float)
    stats={}
    def error(name,value):
        stats[name]=max(stats.get(name,0.),float(np.max(np.abs(value))))
    def condition(name,ok):
        error(name,0. if ok else 1.)
    condition("causal_protocol_hash",digest==diag["tuning_protocol_sha256"])
    expected_months=sorted({d["date"][:7]+"-01" for d in solution["days"]})
    condition("monthly_tuning_schedule",[u["as_of_date"] for u in updates]==expected_months)
    policy_map={u["as_of_date"]:u for u in updates}
    for u in updates:
        cutoff=dates.index(u["as_of_date"])
        p=u["selected_parameters"]
        prefix_digest=hashlib.sha256(predictions.load[:cutoff].astype("<f8").tobytes()
            +predictions.pv[:cutoff].astype("<f8").tobytes()).hexdigest()
        validation=list(range(max(protocol["minimum_forecast_history_days"],cutoff-protocol["validation_days"]),cutoff))
        condition("tuning_history_cutoffs",u["history_days"]==cutoff and u["history_start"]==dates[0]
            and u["history_end"]==dates[cutoff-1] and u["validation_dates"]==[dates[k] for k in validation]
            and u["history_actuals_sha256"]==prefix_digest and u["protocol_sha256"]==digest)
        load_keys={(w,d) for w in protocol["load_window_candidates"] for d in protocol["load_degree_candidates"]}
        condition("tuning_candidate_completeness",len(u["load_candidates"])==len(load_keys)
            and {(r["load_window_days"],r["load_trend_degree"]) for r in u["load_candidates"]}==load_keys
            and sorted(r["pv_window_days"] for r in u["pv_candidates"])==sorted(protocol["pv_window_candidates"]))
        for r in u["load_candidates"]:
            score=float(np.mean([np.abs(predictions.load_hat(k,r["load_window_days"],r["load_trend_degree"])-predictions.load[k])*price for k in validation]))
            error("load_validation_scores",score-r["score"])
        for r in u["pv_candidates"]:
            score=float(np.mean([np.abs(predictions.pv_hat(k,r["pv_window_days"])-predictions.pv[k])*price for k in validation]))
            error("pv_validation_scores",score-r["score"])
        best_load=min(u["load_candidates"],key=lambda r:(round(r["score"],8),r["load_trend_degree"],r["load_window_days"]))
        best_pv=min(u["pv_candidates"],key=lambda r:(round(r["score"],8),r["pv_window_days"]))
        condition("forecast_parameters_selected_by_past_scores",all(p[k]==best_load[k] for k in ("load_window_days","load_trend_degree"))
            and p["pv_window_days"]==best_pv["pv_window_days"])
        spent=sum(float(r["emergency_cost_yuan"]) for dt,r in daily_by_date.items() if dates[31]<=dt<u["as_of_date"])
        budget=max(0.,diag["emergency_budget_yuan"]-spent)/(len(dates)-cutoff)
        error("historical_budget_recomputed",u["emergency_spent_yuan"]-spent)
        error("historical_budget_recomputed",u["daily_emergency_budget_yuan"]-budget)
        condition("historical_budget_calendar",u["formal_days_remaining"]==len(dates)-cutoff
            and u["emergency_budget_yuan"]==diag["emergency_budget_yuan"])
        errors=predictions.errors(cutoff,p)
        risk_map={(r["risk_window_days"],r["risk_radius_periods"],r["purchase_quantile"]):r for r in u["risk_candidates"]}
        risk_keys={(w,r,q) for w in protocol["risk_window_candidates"] for r in protocol["risk_radius_candidates"] for q in protocol["purchase_quantile_candidates"]}
        condition("tuning_candidate_completeness",set(risk_map)==risk_keys and len(risk_map)==len(u["risk_candidates"]))
        for window in protocol["risk_window_candidates"]:
            for radius in protocol["risk_radius_candidates"]:
                qs=protocol["purchase_quantile_candidates"]
                margins=np.stack([independent_margins(errors,k,window,radius,qs) for k in validation])
                ec=(5*price*np.maximum(0,errors[validation,None,:]-margins)).sum(axis=2).mean(axis=0)
                mc=(price*margins).sum(axis=2).mean(axis=0)
                for j,q in enumerate(qs):
                    r=risk_map[window,radius,q]
                    error("risk_validation_scores",ec[j]-r["emergency_bound_yuan_per_day"])
                    error("risk_validation_scores",mc[j]-r["margin_cost_yuan_per_day"])
                    error("risk_validation_scores",ec[j]+mc[j]-r["surrogate_cost_yuan_per_day"])
                    condition("risk_budget_flags",r["historical_budget_feasible"]==bool(r["emergency_bound_yuan_per_day"]<=budget+1e-8))
        feasible=[r for r in u["risk_candidates"] if r["historical_budget_feasible"]]
        tie=lambda r:(r["purchase_quantile"],r["risk_window_days"],r["risk_radius_periods"])
        best_risk=(min(feasible,key=lambda r:(round(r["surrogate_cost_yuan_per_day"],8),*tie(r))) if feasible else
            min(u["risk_candidates"],key=lambda r:(round(r["emergency_bound_yuan_per_day"],8),round(r["surrogate_cost_yuan_per_day"],8),*tie(r))))
        condition("risk_parameters_selected_by_past_scores",all(p[k]==best_risk[k] for k in ("risk_window_days","risk_radius_periods","purchase_quantile")))
        condition("tuning_candidate_completeness",sorted(r["reserve_quantile"] for r in u["reserve_candidates"])==sorted(protocol["reserve_quantile_candidates"]))
        for candidate in u["reserve_candidates"]:
            traces=candidate["validation_days"]
            vdays=validation[-protocol["reserve_validation_days"]:]
            condition("reserve_validation_source_dates",[r["date"] for r in traces]==[dates[k] for k in vdays])
            soc_start=protocol["reserve_validation_initial_soc_kwh"]
            pc,ec=0.,0.
            for j,row in enumerate(traces):
                k=dates.index(row["date"])
                g,c,d,s=[np.asarray(row[key]) for key in ("grid","charge","discharge","soc")]
                condition("reserve_validation_physics",all(x.shape==(144,) and np.isfinite(x).all() for x in (g,c,d,s)))
                before=np.r_[soc_start,s[:-1]]
                error("reserve_validation_physics",s-before-diag["eta_charge"]*c+d/diag["eta_discharge"])
                error("reserve_validation_physics",np.maximum(0,-g))
                error("reserve_validation_physics",np.maximum(0,-c))
                error("reserve_validation_physics",np.maximum(0,-d))
                error("reserve_validation_physics",np.maximum(0,c-5000/6))
                error("reserve_validation_physics",np.maximum(0,d-5000/6))
                error("reserve_validation_physics",np.maximum(0,1200-s))
                error("reserve_validation_physics",np.maximum(0,s-10800))
                condition("reserve_validation_physics",not np.any((c>abs_tol)&(d>abs_tol)))
                error("reserve_validation_physics",row["soc_start"]-soc_start)
                error("reserve_validation_physics",row["soc_end"]-s[-1])
                expected_reserve=independent_reserve(errors[:k],candidate["reserve_quantile"],diag)
                effective=9600. if j==len(traces)-1 else expected_reserve
                error("reserve_validation_physics",row["reserve_kwh"]-expected_reserve)
                error("reserve_validation_physics",row["effective_reserve_kwh"]-effective)
                error("reserve_validation_physics",max(0,1200+effective-s[-1]))
                pl,pp=predictions.pair(k,p)
                m=independent_margins(errors,k,p["risk_window_days"],p["risk_radius_periods"],[p["purchase_quantile"]])[0]
                error("reserve_validation_physics",np.maximum(0,pl-pp+m-g-d+c))
                condition("reserve_validation_optimal_status",row["solver_status"]==0)
                ep=float(5*price@np.maximum(0,predictions.load[k]-predictions.pv[k]+c-d-g))
                gp=float(price@g)
                error("reserve_validation_costs",gp-row["planned_cost_yuan"])
                error("reserve_validation_costs",ep-row["emergency_cost_yuan"])
                error("reserve_validation_costs",gp+ep-row["total_cost_yuan"])
                pc+=gp;ec+=ep;soc_start=float(s[-1])
            error("reserve_validation_costs",pc+ec-candidate["total_cost_yuan"])
            error("reserve_validation_costs",ec-candidate["emergency_cost_yuan"])
        best_reserve=min(u["reserve_candidates"],key=lambda r:(round(r["total_cost_yuan"],8),r["reserve_quantile"]))
        condition("reserve_selected_by_past_simulation",p["reserve_quantile"]==best_reserve["reserve_quantile"])
    # Reconstruct the actual issued predictions, current-policy residual pool,
    # daily margins and reserve, independently of the stored candidate errors.
    error_cache={}
    for day in solution["days"]:
        target=dates.index(day["date"])
        policy_date=day["date"][:7]+"-01"
        p=policy_map[policy_date]["selected_parameters"]
        daily=daily_by_date[day["date"]]
        condition("daily_selected_parameter_mapping",day["policy_as_of_date"]==daily["policy_as_of_date"]==policy_date
            and daily["parameter_mode"]=="causal_monthly" and daily["risk_grouping"]==p["risk_grouping"])
        for key,value in p.items():
            if key!="risk_grouping": error("daily_selected_parameter_mapping",float(daily[key])-value)
        pl,pp=predictions.pair(target,p)
        if policy_date not in error_cache:
            end=max(dates.index(d["date"])+1 for d in solution["days"] if d["date"].startswith(policy_date[:7]))
            error_cache[policy_date]=predictions.errors(end,p)
        errors=error_cache[policy_date]
        sources=list(range(max(1,target-p["risk_window_days"]),target))
        condition("purchase_risk_sources",daily["risk_source_dates"].split(";")==[dates[k] for k in sources]
            and int(daily["risk_source_day_count"])==len(sources) and int(daily["scenario_count"])==0)
        condition("forecast_training_sources",daily["similar_load_dates"].split(";")==dates[max(0,target-p["load_window_days"]):target]
            and daily["similar_pv_dates"].split(";")==dates[max(0,target-p["pv_window_days"]):target])
        m=independent_margins(errors,target,p["risk_window_days"],p["risk_radius_periods"],[p["purchase_quantile"]])[0]
        rows=detail_by_date[day["date"]]
        error("calendar_load_forecast_recomputed",pl-np.asarray([float(r["predicted_load_kwh"]) for r in rows]))
        error("recent_pv_forecast_recomputed",pp-np.asarray([float(r["predicted_pv_kwh"]) for r in rows]))
        error("forecast_history_mapping",pl-np.asarray(solution["forecast_history"][target]["predicted_load"]))
        error("forecast_history_mapping",pp-np.asarray(solution["forecast_history"][target]["predicted_pv"]))
        error("purchase_margin_recomputed",m-np.asarray(day["purchase_margin"]))
        error("purchase_margin_recomputed",m-np.asarray([float(r["purchase_margin_kwh"]) for r in rows]))
        error("purchase_margin_recomputed",m.sum()-float(daily["purchase_margin_kwh"]))
        error("calibrated_supply_floor",np.maximum(0,m-np.asarray([float(r["predicted_curtailment_kwh"]) for r in rows])))
        error("daily_reserve_recomputed",independent_reserve(errors[:target],p["reserve_quantile"],diag)-float(daily["reserve_kwh"]))
        error("calibrated_primary_objective",float(daily["solver_objective_yuan"])-float(daily["planned_cost_yuan"]))
    for name,value in stats.items():
        audit.check(name,value<=abs_tol,f"独立核验最大误差或违反={value:.12g}")
