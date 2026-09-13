"""Replay historical forecasts without dispatch or evaluation-driven fitting."""
from __future__ import annotations
import csv
import json
from pathlib import Path
from collections import Counter
import numpy as np
from .input_data import load_inputs
from .model import HistoryModel, ModelConfig, FORMAL_START
from .forecasting import PRICE_CANDIDATES, choose_price_candidate


def main():
    root = Path(__file__).resolve().parents[2]
    output = root / 'Question_Four_optimized' / 'experiments'
    output.mkdir(parents=True, exist_ok=True)
    inputs = load_inputs(root)
    legacy=HistoryModel(ModelConfig(load_mode='legacy',price_mode='legacy'))
    improved=HistoryModel()
    rows=[]
    for day in inputs.days:
        for k in range(4):
            tau=k*36
            args=(day.day,k,day.load_kwh[:tau],day.pv_kwh[:tau],day.price[:tau+1],day.pv_forecast_kw[k])
            old=legacy.stage_forecast(*args)
            new=improved.stage_forecast(*args)
            _,_,base=improved._baseline(day.day)
            candidates,_=improved._price_candidates(day.day,day.price[:tau+1],k,base)
            selected=choose_price_candidate(improved.price_errors[k])
            if day.day>=FORMAL_START:
                row={'date':day.day.isoformat(),'stage':k,'training_last_date':legacy.days[-1].day.isoformat(),
                     'selected':PRICE_CANDIDATES[selected],
                     'load_old_mae_kw':float(6*np.mean(np.abs(old.load_hat[:36]-day.load_kwh[tau:tau+36]))),
                     'load_calendar_mae_kw':float(6*np.mean(np.abs(new.load_hat[:36]-day.load_kwh[tau:tau+36])))}
                for name,p in zip(PRICE_CANDIDATES,candidates):
                    row[name+'_remaining_mae']=float(np.mean(np.abs(p[1:]-day.price[tau+1:])))
                    row[name+'_execution_mae']=float(np.mean(np.abs(p[1:36]-day.price[tau+1:tau+36])))
                row['adaptive_remaining_mae']=row[row['selected']+'_remaining_mae']
                row['adaptive_execution_mae']=row[row['selected']+'_execution_mae']
                rows.append(row)
        legacy.append(day)
        improved.append(day)
    with (output/'forecast_replay.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    summary={'evaluation_days':334,'price_candidates':PRICE_CANDIDATES,'selection_window_complete_days':28,
             'minimum_scored_days':7,'selection_metric':'MAE over remaining unknown slots',
             'choice_counts':dict(Counter(r['selected'] for r in rows)),
             'load_old_execution_mae_kw':float(np.mean([r['load_old_mae_kw'] for r in rows])),
             'load_calendar_execution_mae_kw':float(np.mean([r['load_calendar_mae_kw'] for r in rows])),
             'price_execution_mae':{name:float(np.mean([r[name+'_execution_mae'] for r in rows]))
                                    for name in (*PRICE_CANDIDATES,'adaptive')},
             'price_zero_stage_remaining_mae':{name:float(np.mean([r[name+'_remaining_mae'] for r in rows if r['stage']==0]))
                                               for name in (*PRICE_CANDIDATES,'adaptive')},
             'price_decisions_with_future_training':sum(r['training_last_date']>=r['date'] for r in rows)}
    (output/'forecast_audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
