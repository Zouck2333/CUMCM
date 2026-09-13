"""Independent verification of physical trajectories, accounting and files."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import numpy as np

from .input_data import load_inputs
from .verify_outputs import verify as verify_workbooks


def read_records(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def verify_directory(directory: Path, inputs) -> dict:
    report={}
    for strategy in ('4-2','4-3'):
        records=read_records(directory/f'checkpoint_{strategy}.jsonl')
        assert len(records)==365, (directory,strategy,'incomplete year')
        errors={name:0.0 for name in ('soc_recurrence_kwh','soc_continuity_kwh','emergency_kwh',
                                     'settlement_yuan','nominal_balance_kwh','csv_difference')}
        previous=6000.0
        formal_costs=[]; formal_plan=0.; formal_adjustment=0.; formal_emergency=0.; emergency_kwh=0.
        sum_annual=0.; months={}
        for index,(r,actual) in enumerate(zip(records,inputs.days,strict=True)):
            assert r['date']==actual.day.isoformat() and r['strategy']==strategy
            g0,g,c,d,em,soc=(np.asarray(r[key],dtype=float) for key in (
                'initial_purchase','final_purchase','charge','discharge','emergency','soc_path'))
            assert soc.shape==(145,)
            for a in (g0,g,c,d,em):
                assert a.shape==(144,) and np.all(np.isfinite(a)) and np.min(a)>=-1e-7
            assert np.all(np.isfinite(soc))
            assert np.max(c)<=5000/6+1e-6 and np.max(d)<=5000/6+1e-6
            assert not np.any((c>1e-9)&(d>1e-9))
            assert np.min(soc)>=1200-1e-6 and np.max(soc)<=10800+1e-6
            errors['soc_recurrence_kwh']=max(errors['soc_recurrence_kwh'],float(np.max(np.abs(np.diff(soc)-.9*c+d/.9))))
            errors['soc_continuity_kwh']=max(errors['soc_continuity_kwh'],abs(soc[0]-previous),abs(r['soc_start']-soc[0]),abs(r['soc_end']-soc[-1]))
            previous=soc[-1]
            computed_em=np.maximum(0,actual.load_kwh+c-g-actual.pv_kwh-d)
            errors['emergency_kwh']=max(errors['emergency_kwh'],float(np.max(np.abs(computed_em-em))))
            unused=np.maximum(0,g+actual.pv_kwh+d-actual.load_kwh-c)
            assert np.max(np.abs(g+actual.pv_kwh+d+em-actual.load_kwh-c-unused))<2e-5
            load=np.asarray(r['load_forecast_at_execution']); pv=np.asarray(r['pv_forecast_at_execution'])
            q=g+pv+d-load-c
            errors['nominal_balance_kwh']=max(errors['nominal_balance_kwh'],float(np.max(-q)),float(np.max(q-pv)))
            if strategy=='4-2': assert np.max(np.abs(g-g0))<1e-7
            pc=float(actual.price@g0)
            ac=float(actual.price@(1.5*np.maximum(g-g0,0)+.5*np.maximum(g0-g,0)))
            ec=float(5*actual.price@em); total=pc+ac+ec
            errors['settlement_yuan']=max(errors['settlement_yuan'],abs(pc-r['plan_cost']),abs(ac-r['adjustment_cost']),abs(ec-r['emergency_cost']),abs(total-r['total_cost']))
            for stage in r['stage_gaps']:
                assert all(v is None or v<=1.0001e-6 for v in stage.values()), stage
            assert len(r['stage_gaps'])==(1 if strategy=='4-2' else 4)
            for stage in r['stage_status']:
                assert all(s.startswith('status=0;') for s in stage.values())
            sum_annual+=total
            if index>=31:
                formal_costs.append(total);formal_plan+=pc;formal_adjustment+=ac;formal_emergency+=ec;emergency_kwh+=sum(em)
                month=r['date'][:7];months[month]=months.get(month,0)+total
        assert max(errors[k] for k in errors if k!='settlement_yuan')<2e-5,errors
        assert errors['settlement_yuan']<1e-4,errors
        s=json.loads((directory/f'summary_{strategy}.json').read_text(encoding='utf-8'))
        controls={'plan_cost_yuan':formal_plan,'adjustment_cost_yuan':formal_adjustment,
                  'emergency_cost_yuan':formal_emergency,'total_cost_yuan':sum(formal_costs),'emergency_kwh':emergency_kwh}
        for key,value in controls.items(): assert abs(s['totals'][key]-value)<.001,(key,value,s['totals'][key])
        assert abs(s['annual_total_cost_yuan']-sum_annual)<.001
        assert abs(s['formal_start_soc_kwh']-records[31]['soc_start'])<1e-5
        assert abs(s['year_end_soc_kwh']-previous)<1e-5
        assert s['evaluation_days']==334 and s['evaluation_start']=='2025-02-01' and s['evaluation_end']=='2025-12-31'
        for month,cost in months.items(): assert abs(s['monthly'][month]['total_cost_yuan']-cost)<.001
        with (directory/f'detail_{strategy}.csv').open(encoding='utf-8-sig',newline='') as stream:
            rows=list(csv.DictReader(stream))
        assert len(rows)==334*144
        for index,row in enumerate(rows):
            di,t=divmod(index,144);r=records[di+31];actual=inputs.days[di+31]
            assert row['date']==r['date'] and int(row['t'])==t
            controls={
                'initial_purchase_kwh':r['initial_purchase'][t],'final_purchase_kwh':r['final_purchase'][t],
                'charge_kwh':r['charge'][t],'discharge_kwh':r['discharge'][t],'emergency_kwh':r['emergency'][t],
                'load_kwh':actual.load_kwh[t],'pv_kwh':actual.pv_kwh[t],
                'soc_before_kwh':r['soc_path'][t],'soc_after_kwh':r['soc_path'][t+1],
                'actual_price_yuan_kwh':actual.price[t],'forecast_price_yuan_kwh':r['forecast_price_at_execution'][t],
                'plan_cost_yuan':actual.price[t]*r['initial_purchase'][t],
                'adjustment_cost_yuan':actual.price[t]*(1.5*max(0,r['final_purchase'][t]-r['initial_purchase'][t])+.5*max(0,r['initial_purchase'][t]-r['final_purchase'][t])),
                'emergency_cost_yuan':5*actual.price[t]*r['emergency'][t],
            }
            controls['total_cost_yuan']=sum(controls[k] for k in ('plan_cost_yuan','adjustment_cost_yuan','emergency_cost_yuan'))
            errors['csv_difference']=max(errors['csv_difference'],*(abs(float(row[k])-v) for k,v in controls.items()))
        assert errors['csv_difference']<2e-5,errors
        report[strategy]={'days':365,'formal_days':334,'checked_csv_rows':len(rows),'maximum_errors':errors,'totals':s['totals']}
    return report


def main():
    root=Path(__file__).resolve().parents[2]
    package=root/'Question_Four_optimized'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=package/'output')
    parser.add_argument('--no-workbook',action='store_true')
    args=parser.parse_args()
    inputs=load_inputs(root)
    report={'trajectories':verify_directory(args.output_dir,inputs)}
    source=json.loads((package/'reference/source_hashes.json').read_text(encoding='utf-8-sig'))
    for row in source:
        assert hashlib.sha256(Path(row['Path']).read_bytes()).hexdigest().upper()==row['Hash'],row['Path']
    report['original_question_four_unchanged']=True
    if not args.no_workbook: report['workbooks']=verify_workbooks(args.output_dir)
    path=args.output_dir/'verification_report.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
