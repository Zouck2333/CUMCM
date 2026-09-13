import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.dirname(here);
const data = JSON.parse(await fs.readFile(path.join(here, 'tables_data.json'), 'utf8'));
const wb = Workbook.create();
const s1 = wb.worksheets.add('表1 购电量与费用');
const s2 = wb.worksheets.add('表2 储能充放电');
const s3 = wb.worksheets.add('表3 紧急购电');
const raw = wb.worksheets.add('所选日期明细');
const expected = [];
const value = (s, addr, v) => { s.getRange(addr).values = [[v]]; };
const merge = (s, addr, v) => { s.mergeCells(addr); value(s, addr.split(':')[0], v); };
const formula = (s, addr, f, e) => { s.getRange(addr).formulas = [[f]]; expected.push({sheet: s.name, cell: addr, value: e}); };
const direct = (s, addr, col, row, e) => formula(s, addr, `='所选日期明细'!${col}${row}`, e);
const sum = (s, addr, col, a, b, e) => formula(s, addr, `=SUM('所选日期明细'!${col}${a}:${col}${b})`, e);
const dateStyle = (d) => new Date(`${d}T00:00:00Z`);
const title1 = '表1  微网在指定时间段的购电量及全天的购电量和购电费';
const title2 = '表2  储能设备在指定时间段的充放电量及0:00和24:00的储电量';

function base(s, columns, lastRow) {
  s.showGridLines = false;
  const r = s.getRange(`A1:${columns}${lastRow}`);
  r.format.font = {name: 'SimSun', size: 12, color: '#000000'};
  r.format.fill = '#FFFFFF';
  r.format.verticalAlignment = 'center';
  r.format.horizontalAlignment = 'center';
  r.format.rowHeight = 27;
  r.format.columnWidth = 23;
  r.setNumberFormat('0.0000');
}
function caption(s, addr, text) {
  merge(s, addr, text);
  s.getRange(addr).format.font = {name: 'SimSun', bold: true, size: 16, color: '#000000'};
  s.getRange(addr).format.rowHeight = 34;
}
function grid(s, addr) { s.getRange(addr).format.borders = {preset: 'all', color: '#000000', style: 'thin'}; }
function note(s, addr, text) {
  merge(s, addr, text);
  s.getRange(addr).format.font = {name: 'SimSun', size: 10, color: '#333333'};
  s.getRange(addr).format.horizontalAlignment = 'left';
  s.getRange(addr).format.rowHeight = 23;
}
base(s1, 'F', 46);
base(s2, 'F', 45);
base(s3, 'H', 26);
base(raw, 'R', 580);
raw.getRange('A1:R580').format.font = {name: 'SimSun', size: 10, color: '#000000'};
raw.getRange('A1:R580').format.columnWidth = 19;
raw.getRange('A1:R580').format.rowHeight = 23;
raw.getRange('C1:C580').format.columnWidth = 21;
caption(raw, 'A1:R1', '所选日期10分钟原始执行明细');
note(raw, 'A2:R2', '来源：优化结果 question_three_detail.csv；已与 result3.xlsx 和滚动策略断点逐项核对。');
note(raw, 'A3:R3', '电量单位：kWh；费用单位：元；单价单位：元/kWh；时间段为实际物理区间。');
const keys = Object.keys(data.records[0]);
raw.getRange('A4:R4').values = [['日期','时段序号','时间段','初始计划购电','最终正常购电','充电量','放电量','紧急购电量','实际负载','实际光伏','时段初储电量','时段末储电量','剩余电量','电价','计划购电费','调整费','紧急购电费','总费用']];
raw.getRange('A4:R4').format.font = {name: 'SimSun', bold: true, size: 10};
grid(raw, 'A4:R580');
raw.getRange('A5:R580').values = data.records.map(r => keys.map(k => k === 'date' ? dateStyle(r[k]) : r[k]));
raw.getRange('A5:A580').setNumberFormat('yyyy-mm-dd');
raw.getRange('B5:B580').setNumberFormat('0');
raw.getRange('D5:R580').format.horizontalAlignment = 'right';
raw.freezePanes.freezeRows(4);
raw.freezePanes.freezeColumns(3);

data.days.forEach((d, i) => {
  const r = 2 + i * 11;
  caption(s1, `A${r}:F${r}`, title1);
  merge(s1, `A${r+1}:F${r+1}`, dateStyle(d.date));
  s1.getRange(`A${r+1}:F${r+1}`).setNumberFormat('yyyy"."m"."d');
  s1.getRange(`A${r+2}:F${r+2}`).values = [['时间段','购电量','时间段','购电量','时间段','购电量']];
  for (let j=0;j<6;j++) {
    const row = r+3+Math.floor(j/3), c = (j%3)*2;
    value(s1, `${String.fromCharCode(65+c)}${row}`, d.selected[j].period);
    direct(s1, `${String.fromCharCode(66+c)}${row}`, 'E', d.selected[j].detail_row, d.selected[j].energy);
  }
  merge(s1, `A${r+5}:B${r+5}`, '全天购电量');
  sum(s1, `C${r+5}`, 'E', d.first_row, d.last_row, d.totals.final_purchase_kwh);
  merge(s1, `D${r+5}:E${r+5}`, '全天购电费');
  sum(s1, `F${r+5}`, 'R', d.first_row, d.last_row, d.totals.total_cost_yuan);
  s1.getRange(`F${r+5}`).setNumberFormat('0.00');
  grid(s1, `A${r+2}:F${r+5}`);
  note(s1, `A${r+6}:F${r+6}`, '单位：购电量为kWh，购电费为元。购电量为最终正常购电量，紧急购电量见表3。');
  note(s1, `A${r+7}:F${r+7}`, '全天购电费＝计划购电费＋调整费＋紧急购电费。');

  const q = 2 + i * 11;
  caption(s2, `A${q}:F${q}`, title2);
  merge(s2, `A${q+1}:F${q+1}`, dateStyle(d.date));
  s2.getRange(`A${q+1}:F${q+1}`).setNumberFormat('yyyy"."m"."d');
  s2.getRange(`A${q+2}:F${q+2}`).values = [['时间段','充电量','放电量','时间段','充电量','放电量']];
  for(let j=0;j<6;j++) {
    const row=q+3+Math.floor(j/2), c=(j%2)*3, b=d.blocks[j];
    value(s2, `${String.fromCharCode(65+c)}${row}`, b.period);
    sum(s2, `${String.fromCharCode(66+c)}${row}`, 'F', b.first_row, b.last_row, b.charge);
    sum(s2, `${String.fromCharCode(67+c)}${row}`, 'G', b.first_row, b.last_row, b.discharge);
  }
  merge(s2, `A${q+6}:B${q+6}`, '0:00 储电量');
  direct(s2, `C${q+6}`, 'K', d.first_row, d.soc_start);
  merge(s2, `D${q+6}:E${q+6}`, '24:00 储电量');
  direct(s2, `F${q+6}`, 'L', d.last_row, d.soc_end);
  grid(s2, `A${q+2}:F${q+6}`);
  note(s2, `A${q+7}:F${q+7}`, '单位：kWh。充放电量为各4小时时段内实际执行电量之和。');
  note(s2, `A${q+8}:F${q+8}`, '充电量、放电量按微网侧计量；储电量按电池内部SOC计量。');
});

s3.getRange('A1:H26').format.columnWidth = 20;
caption(s3, 'A2:H2', '表3  微网在指定日期的紧急购电量');
note(s3, 'A3:H3', '单位：kWh');
for(let i=0;i<4;i++) {
  const c=String.fromCharCode(65+i*2), e=String.fromCharCode(66+i*2), d=data.days[i];
  merge(s3, `${c}5:${e}5`, dateStyle(d.date));
  s3.getRange(`${c}5:${e}5`).setNumberFormat('yyyy"."m"."d');
  value(s3, `${c}6`, '时间段'); value(s3, `${e}6`, '购电量');
  for(let j=0;j<d.emergency.length;j++) {
    const g=d.emergency[j], row=7+j;
    value(s3, `${c}${row}`, g.period);
    sum(s3, `${e}${row}`, 'H', g.first_row, g.last_row, g.energy);
  }
}
grid(s3, 'A5:H25');
note(s3, 'A26:H26', '相邻且均发生紧急购电的10分钟时段合并列示；空白表示该日期已无更多紧急购电时段。');

wb.recalculate();
for(const e of expected) {
  const actual=wb.worksheets.getItem(e.sheet).getRange(e.cell).values[0][0];
  if(typeof actual !== 'number' || Math.abs(actual-e.value)>1e-6) throw new Error(`Mismatch ${e.sheet}!${e.cell}: ${actual} vs ${e.value}`);
}
// Demonstrate that a source edit updates both an interval result and its daily sum.
const sample=data.days[0].selected[1], sourceCell=`E${sample.detail_row}`;
const original=raw.getRange(sourceCell).values[0][0];
value(raw, sourceCell, original+1);
wb.recalculate();
if(Math.abs(s1.getRange('D5').values[0][0]-sample.energy-1)>1e-6) throw new Error('Interval recalculation failed');
if(Math.abs(s1.getRange('C7').values[0][0]-data.days[0].totals.final_purchase_kwh-1)>1e-6) throw new Error('Daily recalculation failed');
value(raw, sourceCell, original);
wb.recalculate();
const errors=await wb.inspect({kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!', options: {useRegex:true,maxResults:30}, maxChars:3000});
await fs.writeFile(path.join(here,'formula_scan.ndjson'), errors.ndjson);
await fs.writeFile(path.join(here,'expected_cells.json'), JSON.stringify(expected,null,2));
const previews=[['table1_first',s1,'A1:F21'],['table1_last',s1,'A23:F43'],['table2_first',s2,'A1:F22'],['table2_last',s2,'A23:F44'],['table3',s3,'A1:H26'],['source',raw,'A1:R11']];
for(const [name,s,range] of previews) {
  const blob=await wb.render({sheetName:s.name,range,scale:1.5,format:'png'});
  await fs.writeFile(path.join(here,`${name}.png`),new Uint8Array(await blob.arrayBuffer()));
}
const file=await SpreadsheetFile.exportXlsx(wb);
await file.save(path.join(out,'第三问优化结果_表1表2表3.xlsx'));
console.log(JSON.stringify({status:'passed',formula_checks:expected.length,recalculation:'passed',sheets:4}));
