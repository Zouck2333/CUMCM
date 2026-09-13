"""Chronological forecast and purchase calibration screening, fixed battery ablation."""
import json
import argparse
from datetime import date
from pathlib import Path

import numpy as np


def calendar_local_forecast(values, dates, target, window=42, degree=2, log_scale=True):
    """Local polynomial trend plus learned weekday effects; only rows < target."""
    if target < 1:
        raise ValueError("Cold start must use the attachment 1 prior")
    if target < 14:
        return values[max(0, target - 7)].copy()
    start = max(0, target-window)
    indices = np.arange(start, target)
    time = (indices - target) / 28.
    weekday = np.array([d.weekday() for d in dates])
    x = np.column_stack([np.ones(len(indices))] + [time ** j for j in range(1, degree+1)]
                        + [(weekday[indices] == j).astype(float) for j in range(6)])
    query = np.array([1.] + [0.] * degree + [float(weekday[target] == j) for j in range(6)])
    penalty = np.eye(x.shape[1]) * .01
    penalty[0, 0] = 1e-8
    observed = np.log(np.maximum(1., values[indices])) if log_scale else values[indices]
    coef = np.linalg.solve(x.T @ x + penalty, x.T @ observed)
    pred = query @ coef
    return np.exp(pred) if log_scale else np.maximum(0., pred)


def pv_recent_forecast(values, target, window=7, trend=True):
    if target < 1:
        raise ValueError("Cold start must use the attachment 1 prior")
    if target < 3:
        return values[max(0, target-1)].copy()
    indices = np.arange(max(0, target-window), target)
    if not trend:
        return values[indices].mean(axis=0)
    # Linear fit with a modest ridge shrinkage of the short-window slope.
    time = (indices - target) / max(1., window)
    x = np.column_stack((np.ones(len(indices)), time))
    coef = np.linalg.solve(x.T @ x + np.diag([1e-8, .3]), x.T @ values[indices])
    return np.maximum(0., coef[0])


def calibrated_margins(errors, dates, quantile=.85, window=28, radius=3, grouping="all"):
    margins = []
    for target in range(31, len(dates)):
        history = list(range(max(1, target-window), target))
        if grouping == "legacy":
            same = [i for i in history if (dates[i].weekday() < 5) == (dates[target].weekday() < 5)]
            if len(same) >= 5:
                history = same
        sample = errors[history]
        if radius:
            padded = np.pad(sample, ((0,0),(radius,radius)), constant_values=np.nan)
            sample = np.lib.stride_tricks.sliding_window_view(padded, 2*radius+1, axis=1)
            margin = np.nanquantile(sample, quantile, axis=(0,2), method="inverted_cdf")
        else:
            margin = np.quantile(sample, quantile, axis=0, method="inverted_cdf")
        margins.append(np.maximum(0., margin))
    return np.array(margins)


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=root/"output/input_data.json")
    parser.add_argument("--baseline", type=Path, default=root/"archive/before_target_1500_100_20260912_182709/output/question_two_solution.json")
    parser.add_argument("--output-dir", type=Path, default=root/"archive/target_runs_20260912/target_research")
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(exist_ok=True, parents=True)
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    dates = [date.fromisoformat(d) for d in payload["dates"]]
    load, pv, price = [np.array(payload[k]) for k in ("actual_load", "actual_pv", "price")]
    loads = {"legacy": np.array([d["predicted_load"] for d in baseline["forecast_history"]])}
    pvs = {"legacy": np.array([d["predicted_pv"] for d in baseline["forecast_history"]])}
    for window in (28,42,56,84):
        for degree in (1,2):
            name = f"calendar_w{window}_d{degree}"
            loads[name] = np.array([payload["prior_load"] if d == 0 else calendar_local_forecast(load, dates, d, window, degree) for d in range(365)])
    loads["lag7_trend"] = np.array([
        payload["prior_load"] if d == 0 else load[max(0,d-7)] * (np.mean(load[max(7,d-3):d].sum(1)/load[max(0,d-10):max(0,d-7)].sum(1)) if d>=10 else 1.)
        for d in range(365)])
    for window in (3,7,14,21):
        for trend in (False,True):
            name = f"recent_w{window}_t{int(trend)}"
            pvs[name] = np.array([payload["prior_pv"] if d == 0 else pv_recent_forecast(pv,d,window,trend) for d in range(365)])
    load_scores = sorted([(float(np.abs(v[31:]-load[31:]).mean()),k) for k,v in loads.items()])
    pv_scores = sorted([(float(np.abs(v[31:]-pv[31:]).mean()),k) for k,v in pvs.items()])
    print("Load MAE",load_scores,flush=True)
    print("PV MAE",pv_scores,flush=True)
    charge, discharge = [np.array([d[k] for d in baseline["days"]]) for k in ("charge","discharge")]
    cases = []
    # Screening uses full-year scores to nominate candidates, not to fit any day's prediction.
    for _,lname in load_scores[:3]:
        for _,vname in pv_scores[:3]:
            net_pred = loads[lname] - pvs[vname]
            error = load - pv - net_pred
            for q in (.8,.85,.9):
                margin = calibrated_margins(error, dates, q)
                grid = np.maximum(0.,net_pred[31:] + margin + charge - discharge)
                emergency = np.maximum(0.,load[31:]-pv[31:] - grid + charge - discharge)
                pc = float((grid*price).sum()); ec = float((emergency*price*5).sum())
                item = {"load":lname,"pv":vname,"q":q,"normal_cost":pc,"emergency_cost":ec,"total_cost":pc+ec,
                        "total_target_met":pc+ec<15e6,"emergency_target_met":ec<1e6}
                cases.append(item)
                print(item,flush=True)
    np.savez_compressed(out/"candidate_forecasts.npz", **{f"load_{k}":v for k,v in loads.items()}, **{f"pv_{k}":v for k,v in pvs.items()})
    (out/"screening.json").write_text(json.dumps({"scope":"fixed battery screening, candidate selection is retrospective", "load_mae":load_scores,"pv_mae":pv_scores,"cases":cases},indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()
