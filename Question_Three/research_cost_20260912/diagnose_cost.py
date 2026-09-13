"""Causal, isolated cost experiments; leaves the submitted outputs unchanged."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Question_Three" / "code"))
import input_data  # supplies the documented pure-Python openpyxl fallback
sys.path.insert(0, str(ROOT / "Question_Three_cheaper" / "code"))
import solve_question_three as model
sys.path.insert(0, str(ROOT / "Question_Two" / "code"))
from forecast_calendar import calendar_load_forecast


def calendar_loader(data):
    cache = {}

    def predict(day):
        if day not in cache:
            cache[day] = (data.prior_load.copy() if day == 0 else
                          calendar_load_forecast(data.actual_load, data.dates, day,
                                                 window=28, degree=2))
        return cache[day]
    return predict


def audit(data):
    saved_path = ROOT / "Question_Three_cheaper/output/output/question_three_solution.json"
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    old = model.build_baseline_loader(data)
    new = calendar_loader(data)
    old_hat = model.build_load_hat_loader(data, old)
    new_hat = model.build_load_hat_loader(data, new)
    errors = {name: [] for name in ("original", "calendar")}
    em_cost = np.zeros(4)
    lower_adjust = upper_adjust = night_em = 0.0
    identity_error = 0.0
    for day, record in enumerate(saved["days"], start=31):
        assert record["date"] == data.dates[day].isoformat()
        g0 = np.asarray(record["initial_grid"])
        g = np.asarray(record["grid"])
        c = np.asarray(record["charge"])
        d = np.asarray(record["discharge"])
        r = np.asarray(record["actual_emergency"])
        lower_adjust += float(np.sum(.5 * data.price * np.maximum(g0-g, 0)))
        upper_adjust += float(np.sum(1.5 * data.price * np.maximum(g-g0, 0)))
        for k in range(4):
            sl = slice(k*36, (k+1)*36)
            lh = old_hat(day, k)[sl]
            ph = model.pv_hat_for_day(data, day, k)[sl]
            q = g[sl] + ph + d[sl] - lh - c[sl]
            predicted_r = np.maximum(data.actual_load[day, sl]-lh
                                     -data.actual_pv[day, sl]+ph-q, 0)
            identity_error = max(identity_error, float(np.max(np.abs(predicted_r-r[sl]))))
            cost = 5 * data.price[sl] * r[sl]
            em_cost[k] += float(np.sum(cost))
            night = (ph < 1e-8) & (data.actual_pv[day, sl] < 1e-8)
            night_em += float(np.sum(cost[night]))
            errors["original"].append(data.actual_load[day, sl] - lh)
            errors["calendar"].append(data.actual_load[day, sl] - new_hat(day, k)[sl])
    result = {
        "baseline_saved_totals": saved["diagnostics"],
        "emergency_cost_by_6h_yuan": em_cost.tolist(),
        "night_emergency_cost_yuan": night_em,
        "downward_adjustment_cost_yuan": lower_adjust,
        "upward_adjustment_cost_yuan": upper_adjust,
        "emergency_identity_max_residual_kwh": identity_error,
        "load_forecast_errors": {name: {
            "mae_kw": float(np.mean(np.abs(np.concatenate(err)))) * 6,
            "rmse_kw": float(np.sqrt(np.mean(np.concatenate(err)**2))) * 6,
        } for name, err in errors.items()},
    }
    # Interface-level prefix causality test for the proposed forecast.
    from copy import deepcopy
    altered = deepcopy(data)
    altered.actual_load[180:] = altered.actual_load[180:] * 7 + 12345
    result["forecast_future_mutation_max_difference"] = float(np.max(np.abs(
        calendar_loader(data)(180) - calendar_loader(altered)(180))))
    return result


def replay(data, variant):
    if variant == "calendar":
        model.build_baseline_loader = calendar_loader
    started = time.perf_counter()
    days, daily, diagnostics = model.solve_year(
        data, eta_charge=.9, eta_discharge=.9, risk_alpha=.9,
        risk_weight=.1, max_days=None, reserve_quantile=0,
        lexicographic=False, mip_rel_gap=1e-5,
    )
    errors = {k: 0.0 for k in ("soc_recursion", "soc_bounds", "power_bounds",
                               "charge_discharge_overlap", "emergency", "cost", "cross_day")}
    for i, day in enumerate(days):
        c, d, g, g0, e, r, l, pv = [np.asarray(day[k]) for k in (
            "charge", "discharge", "grid", "initial_grid", "soc_path",
            "actual_emergency", "actual_load", "actual_pv")]
        errors["soc_recursion"] = max(errors["soc_recursion"], float(np.max(np.abs(
            np.diff(e) - .9*c + d/.9))))
        errors["soc_bounds"] = max(errors["soc_bounds"], float(np.max(np.maximum(
            1200-e, e-10800))))
        errors["power_bounds"] = max(errors["power_bounds"], float(max(c.max(),d.max())-5000/6))
        errors["charge_discharge_overlap"] = max(errors["charge_discharge_overlap"], float(np.minimum(c,d).max()))
        errors["emergency"] = max(errors["emergency"], float(np.max(np.abs(
            r-np.maximum(l+c-g-pv-d, 0)))))
        cost = np.sum(data.price*g0 + 1.5*data.price*np.maximum(g-g0,0)
                      + .5*data.price*np.maximum(g0-g,0) + 5*data.price*r)
        errors["cost"] = max(errors["cost"], abs(float(cost)-day["total_cost"]))
        if i:
            errors["cross_day"] = max(errors["cross_day"], abs(e[0]-days[i-1]["soc_end"]))
    if any(value > 1e-3 for value in errors.values()):
        raise AssertionError(errors)
    return {"variant": variant, "diagnostics": diagnostics, "daily": daily,
            "validation_tolerance": 1e-3, "validation_max_residuals": errors,
            "first_evaluation_soc": days[0]["soc_start"],
            "final_soc": days[-1]["soc_end"],
            "full_year_cost_yuan": sum(day["total_cost"] for day in daily),
            "elapsed_seconds": time.perf_counter()-started}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("variant", choices=["audit", "baseline", "calendar"])
    args = p.parse_args()
    a = ROOT / "C题" / "附件"
    data = model.load_inputs(a/"附件1.xlsx", a/"附件2.xlsx", a/"附件3.xlsx")
    result = audit(data) if args.variant == "audit" else replay(data, args.variant)
    result["source_sha256"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (a/"附件1.xlsx", a/"附件2.xlsx", a/"附件3.xlsx",
                     Path(model.__file__), ROOT/"Question_Two/code/forecast_calendar.py")}
    output = Path(__file__).parent / f"{args.variant}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "daily"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
