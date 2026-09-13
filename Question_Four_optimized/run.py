"""Reproduce both official optimized strategies from configuration.json."""
from pathlib import Path
import argparse
import json
import sys

PACKAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE.parent))

from Question_Four_optimized.code.input_data import load_inputs
from Question_Four_optimized.code.model import ModelConfig
from Question_Four_optimized.code.run_question_four import _run_one
from Question_Four_optimized.code.verify_optimized import verify_directory
from Question_Four_optimized.code.verify_outputs import verify


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=PACKAGE/'output')
    parser.add_argument('--no-workbook',action='store_true')
    parser.add_argument('--max-days',type=int,default=365)
    args=parser.parse_args()
    if not 1<=args.max_days<=365: parser.error('max-days must lie in [1,365]')
    settings=json.loads((PACKAGE/'configuration.json').read_text(encoding='utf-8'))
    inputs=load_inputs(PACKAGE.parent)
    output=args.output_dir.resolve();output.mkdir(parents=True,exist_ok=True)
    for strategy in ('4-2','4-3'):
        config=ModelConfig(**settings['strategies'][strategy]['model_config'])
        summary=_run_one(inputs,strategy,output,args.max_days,config,export_workbook=not args.no_workbook)
        print(json.dumps({'strategy':strategy,'total':summary.get('totals')},ensure_ascii=False))
    if args.max_days==365:
        report={'trajectories':verify_directory(output,inputs)}
        if not args.no_workbook:report['workbooks']=verify(output)
        (output/'verification_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
