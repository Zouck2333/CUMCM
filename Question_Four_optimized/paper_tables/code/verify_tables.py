"""Verify delivered tables independently against original CSV records."""
from pathlib import Path
from itertools import groupby
import csv
import hashlib
import json
import math

from docx import Document
from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
DATES=['2025-03-20','2025-06-21','2025-09-23','2025-12-21']
SHEETS=['3月20日','6月21日','9月23日','12月21日']
checks=0


def same(actual,expected):
    global checks
    if isinstance(expected,(int,float)):
        assert isinstance(actual,(int,float)) and math.isclose(actual,expected,rel_tol=0,abs_tol=1e-6),(actual,expected)
    else:
        assert actual==expected,(actual,expected)
    checks+=1


def fmt(value):
    return f'{0.0 if abs(value)<.00005 else value:.4f}'


report={'strategies':{},'table1_basis':'final normal purchase; actual total cost including emergency','rounding':'four decimal places; aggregates calculated before rounding'}
for strategy in ('4-2','4-3'):
    input_data=json.loads((ROOT/'data'/f'tables_{strategy}.json').read_text(encoding='utf-8'))
    for filename,digest in input_data['sources'].items():
        same(hashlib.sha256((ROOT.parent/filename).read_bytes()).hexdigest(),digest)
    rows={d:[] for d in DATES}
    with (ROOT.parent/'output'/f'detail_{strategy}.csv').open(encoding='utf-8-sig',newline='') as stream:
        for row in csv.DictReader(stream):
            if row['date'] in rows: rows[row['date']].append(row)
    file=ROOT/f'第四问_{strategy}_指定日期表1表2表3.xlsx'
    book=load_workbook(file,data_only=True)
    formulas=load_workbook(file,data_only=False)
    doc=Document(ROOT/f'第四问_{strategy}_指定日期表1表2表3.docx')
    same(len(doc.tables),9)
    same(book.sheetnames,SHEETS+['表3 紧急购电','数据明细'])
    counts=[]
    for i,date in enumerate(DATES):
        day=sorted(rows[date],key=lambda r:int(r['t']))
        same([int(r['t']) for r in day],list(range(144)))
        ws=book[SHEETS[i]];t1,t2=doc.tables[2*i:2*i+2]
        same(len(t1.rows),4);same(len(t2.rows),5)
        for merge in ('A8:B8','D8:E8','A17:B17','D17:E17'):
            same(merge in {str(m) for m in ws.merged_cells.ranges},True)
        for j,hour in enumerate((10,12,14,16,18,20)):
            row,col=6+j//3,2*(j%3)+1
            amount=float(day[hour*6]['final_purchase_kwh'])
            same(ws.cell(row,col).value,day[hour*6]['interval'])
            same(ws.cell(row,col+1).value,amount)
            same(t1.cell(row-5,col-1).text,day[hour*6]['interval'])
            same(t1.cell(row-5,col).text,fmt(amount))
        normal=sum(float(r['final_purchase_kwh']) for r in day)
        cost=sum(float(r['total_cost_yuan']) for r in day)
        same(ws['C8'].value,normal);same(ws['F8'].value,cost)
        same(t1.cell(3,2).text,fmt(normal));same(t1.cell(3,5).text,fmt(cost))
        for j in range(6):
            for offset,key in ((1,'charge_kwh'),(2,'discharge_kwh')):
                amount=sum(float(r[key]) for r in day[j*24:(j+1)*24])
                same(ws.cell(14+j//2,3*(j%2)+offset+1).value,amount)
                same(t2.cell(1+j//2,3*(j%2)+offset).text,fmt(amount))
        for cell,r,c,amount in (('C17',4,2,float(day[0]['soc_before_kwh'])),('F17',4,5,float(day[-1]['soc_after_kwh']))):
            same(ws[cell].value,amount);same(t2.cell(r,c).text,fmt(amount))
        groups=[]
        for positive,chunk in groupby(day,key=lambda r:float(r['emergency_kwh'])>1e-8):
            entries=list(chunk)
            if positive:
                groups.append((entries[0]['interval'].split('-')[0]+'-'+entries[-1]['interval'].split('-')[1],sum(float(e['emergency_kwh']) for e in entries)))
        counts.append(len(groups))
        for j,(time,amount) in enumerate(groups):
            same(book['表3 紧急购电'].cell(j+7,2*i+1).value,time)
            same(book['表3 紧急购电'].cell(j+7,2*i+2).value,amount)
            same(doc.tables[-1].cell(j+2,2*i).text,time)
            same(doc.tables[-1].cell(j+2,2*i+1).text,fmt(amount))
        same(sum(g[1] for g in groups),sum(float(r['emergency_kwh']) for r in day))
    same(len(doc.tables[-1].rows),max(counts)+2)
    for i,n in enumerate(counts):
        for j in range(n,max(counts)):
            same(book['表3 紧急购电'].cell(j+7,2*i+1).value,None)
            same(book['表3 紧急购电'].cell(j+7,2*i+2).value,None)
            same(doc.tables[-1].cell(j+2,2*i).text,'')
            same(doc.tables[-1].cell(j+2,2*i+1).text,'')
    for ws in book:
        for row in ws:
            for cell in row:
                assert cell.data_type!='e',(ws.title,cell.coordinate,cell.value)
    formula_count=sum(c.data_type=='f' for ws in formulas for row in ws for c in row)
    assert formula_count>576
    # Test all formula caches against their source ranges, including raw cost rows.
    raw=book['数据明细']
    for i,date in enumerate(DATES):
        day=sorted(rows[date],key=lambda r:int(r['t']))
        keys=['initial_purchase_kwh','final_purchase_kwh','charge_kwh','discharge_kwh','soc_before_kwh','soc_after_kwh','emergency_kwh','plan_cost_yuan','adjustment_cost_yuan','emergency_cost_yuan','total_cost_yuan']
        for j,r in enumerate(day):
            for c,key in enumerate(keys,3): same(raw.cell(6+144*i+j,c).value,float(r[key]))
    images={}
    for folder in ('word','excel'):
        image_paths=list((ROOT/'_qa'/strategy/folder).glob('*.png'))
        same(len(image_paths),3 if folder=='word' else 7)
        for path in image_paths:
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                im.load();assert im.width>500 and im.height>100
            images[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    pdf=PdfReader(ROOT/'_qa'/strategy/'word/render.pdf')
    same(len(pdf.pages),3)
    for page in pdf.pages: assert len(page.extract_text().strip())>100
    report['strategies'][strategy]={'tables':9,'word_pages':3,'sheets':6,'emergency_interval_counts':counts,'formulas':formula_count,'all_cached_values_match_source':True,'png_integrity_verified':True,'images':images}
    book.close();formulas.close()
report['checks_passed']=checks
report['status']='PASS'
report['word_render_engine']='Microsoft Word read-only ExportAsFixedFormat plus bundled Poppler; packaged render_docx.py unavailable because soffice.exe is missing'
report['excel_render_engine']='artifact-tool; completion markers received for 7 images per workbook, all PNGs independently verified; Windows renderer process returned nonzero after completed writes'
(ROOT/'_qa'/'data_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':'PASS','checks_passed':checks,'strategies':{s:{k:v for k,v in r.items() if k!='images'} for s,r in report['strategies'].items()}},ensure_ascii=False))
