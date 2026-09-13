import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Workbook, SpreadsheetFile, FileBlob} from '@oai/artifact-tool';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const names=['3月20日','6月21日','9月23日','12月21日'];
const previewMode=process.argv[2]==='--preview';
const strategies=previewMode?[process.argv[3]]:['4-2','4-3'];
const filename=s=>path.join(root,`第四问_${s}_指定日期表1表2表3.xlsx`);
const val=(ws,cell,value)=>{ws.getRange(cell).values=[[value]];};
const formula=(ws,cell,value)=>{ws.getRange(cell).formulas=[[value]];};
const col=n=>String.fromCharCode(65+n);
function merged(ws,range,text){ws.mergeCells(range);val(ws,range.split(':')[0],text);}
function base(ws,range,width=20,size=13){
  ws.showGridLines=false;
  const f=ws.getRange(range).format;
  f.font={name:'SimSun',size,color:'#000000'};
  f.fill='#FFFFFF';f.columnWidth=width;f.rowHeight=30;
  f.horizontalAlignment='center';f.verticalAlignment='center';f.wrapText=false;
}
function caption(ws,range,text){
  merged(ws,range,text);
  ws.getRange(range).format.font={name:'SimHei',size:16,bold:true,color:'#000000'};
  ws.getRange(range).format.rowHeight=40;
}
function grid(ws,range){ws.getRange(range).format.borders={preset:'all',style:'thin',color:'#000000'};}
function number(ws,cell,f){formula(ws,cell,f);ws.getRange(cell).setNumberFormat('0.0000');ws.getRange(cell).format.font={name:'Times New Roman',size:13,color:'#000000'};}
for(const strategy of strategies){
  const data=JSON.parse(await fs.readFile(path.join(root,'data',`tables_${strategy}.json`),'utf8'));
  const nr=Math.max(...data.days.map(d=>d.emergency.length));
  if(previewMode){
    const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(filename(strategy)));
    const dir=path.join(root,'_qa',strategy,'excel');await fs.mkdir(dir,{recursive:true});
    const ranges=[...names.map(name=>[name,'A1:F21',name]),['表3 紧急购电',`A1:H${nr+8}`,'表3'],['数据明细','A1:I13','数据明细电量'],['数据明细','J5:M13','数据明细费用']];
    for(const [sheetName,range,label] of ranges){
      const blob=await wb.render({sheetName,range,scale:1.5,format:'png'});
      await fs.writeFile(path.join(dir,label+'.png'),new Uint8Array(await blob.arrayBuffer()));
    }
    console.log(`Q4_TABLE_PREVIEWS_WRITTEN:${ranges.length}`);
    continue;
  }
  const wb=Workbook.create();
  const sheets=names.map(n=>wb.worksheets.add(n));
  const emergencySheet=wb.worksheets.add('表3 紧急购电');
  const raw=wb.worksheets.add('数据明细');
  base(raw,'A1:M581',18,10);
  raw.getRange('A1:M581').format.rowHeight=21;
  raw.getRange('A1:M4').format.horizontalAlignment='left';
  raw.getRange('A:A').format.columnWidth=13;
  raw.getRange('B:B').format.columnWidth=15;
  val(raw,'A1',`第四问策略${strategy} 四个指定日期的模型结果明细`);
  raw.getRange('A1').format.font={name:'SimHei',size:14,bold:true,color:'#000000'};
  val(raw,'A2',`来源：Question_Four_optimized/output/checkpoint_${strategy}.jsonl 与 detail_${strategy}.csv`);
  val(raw,'A3','每行表示一个10分钟区间。电量单位kWh，费用单位元；保留源数据计算精度。');
  val(raw,'A4','表1与表2通过公式引用本页；表3按本次结果的连续紧急购电时段汇总。');
  raw.getRange('A5:M5').values=[['日期','时间段','初始正常购电量','最终正常购电量','充电量','放电量','段初储电量','段末储电量','紧急购电量','计划购电费','调整购电费','紧急购电费','总购电费']];
  raw.getRange('A5:M5').format.font={name:'SimSun',size:10,bold:true,color:'#000000'};
  raw.getRange('A5:M5').format.fill='#F2F2F2';
  raw.getRange('A5:M5').format.rowHeight=28;
  raw.freezePanes.freezeRows(5);
  const rawValues=[];
  for(const day of data.days){
    const serial=(Date.parse(day.date+'T00:00:00Z')-Date.UTC(1899,11,30))/86400000;
    for(const r of day.detail) rawValues.push([serial,r.interval,r.initial_purchase_kwh,r.final_purchase_kwh,r.charge_kwh,r.discharge_kwh,r.soc_before_kwh,r.soc_after_kwh,r.emergency_kwh,r.plan_cost_yuan,r.adjustment_cost_yuan,r.emergency_cost_yuan]);
  }
  raw.getRange('A6:L581').values=rawValues;
  raw.getRange('A6:A581').setNumberFormat('yyyy-mm-dd');
  raw.getRange('C6:M581').setNumberFormat('0.0000');
  raw.getRange('C6:M581').format.horizontalAlignment='right';
  formula(raw,'M6','=SUM(J6:L6)');raw.getRange('M6:M581').fillDown();
  for(let i=0;i<4;i++){
    const ws=sheets[i],day=data.days[i],start=6+i*144,end=start+143;
    const ref=(c,t)=>`'数据明细'!${c}${start+t}`;
    base(ws,'A1:F21',22,13);
    caption(ws,'A1:F1',`第四问策略${strategy}  ${day.date_label}`);
    merged(ws,'A2:F2','电量单位：kWh；费用单位：元；显示四位小数，汇总按未舍入值计算。');
    ws.getRange('A2:F2').format.font={name:'SimSun',size:10,color:'#000000'};
    ws.getRange('A3:F3').format.rowHeight=12;
    caption(ws,'A4:F4',data.captions[0]);
    ws.getRange('A5:F8').values=[['时间段','购电量','时间段','购电量','时间段','购电量'],[day.selected[0].time,null,day.selected[1].time,null,day.selected[2].time,null],[day.selected[3].time,null,day.selected[4].time,null,day.selected[5].time,null],['全天购电量',null,null,'全天购电费',null,null]];
    ws.mergeCells('A8:B8');ws.mergeCells('D8:E8');grid(ws,'A5:F8');
    day.selected.forEach((v,j)=>number(ws,`${col(2*(j%3)+1)}${6+Math.floor(j/3)}`,`=${ref('D',v.t)}`));
    number(ws,'C8',`=SUM('数据明细'!D${start}:D${end})`);
    number(ws,'F8',`=SUM('数据明细'!M${start}:M${end})`);
    merged(ws,'A9:F10',data.table1_note);
    ws.getRange('A9:F10').format.font={name:'SimSun',size:10,color:'#000000'};
    ws.getRange('A9:F10').format.horizontalAlignment='left';
    ws.getRange('A9:F10').format.rowHeight=16;
    ws.getRange('A11:F11').format.rowHeight=12;
    caption(ws,'A12:F12',data.captions[1]);
    ws.getRange('A13:F17').values=[['时间段','充电量','放电量','时间段','充电量','放电量'],...Array.from({length:3},(_,j)=>[day.blocks[2*j].time,null,null,day.blocks[2*j+1].time,null,null]),['0:00 储电量',null,null,'24:00 储电量',null,null]];
    ws.mergeCells('A17:B17');ws.mergeCells('D17:E17');grid(ws,'A13:F17');
    day.blocks.forEach((b,j)=>{
      const row=14+Math.floor(j/2),c=3*(j%2),a=start+24*j,z=a+23;
      number(ws,`${col(c+1)}${row}`,`=SUM('数据明细'!E${a}:E${z})`);
      number(ws,`${col(c+2)}${row}`,`=SUM('数据明细'!F${a}:F${z})`);
    });
    number(ws,'C17',`=${ref('G',0)}`);number(ws,'F17',`=${ref('H',143)}`);
    merged(ws,'A19:F19','表2为最终执行的充放电量，按六个4小时时间段汇总。');
    ws.getRange('A19:F19').format.font={name:'SimSun',size:10,color:'#000000'};
    ws.getRange('A19:F19').format.horizontalAlignment='left';
  }
  base(emergencySheet,`A1:H${nr+8}`,17,13);
  caption(emergencySheet,'A1:H1',`第四问策略${strategy}`);
  caption(emergencySheet,'A2:H2',data.captions[2]);
  merged(emergencySheet,'A3:H3','电量单位：kWh');
  emergencySheet.getRange('A3:H3').format.font={name:'SimSun',size:10,color:'#000000'};
  emergencySheet.getRange('A4:H4').format.rowHeight=12;
  grid(emergencySheet,`A5:H${nr+6}`);
  for(let i=0;i<4;i++){
    const a=col(i*2),b=col(i*2+1),day=data.days[i];
    merged(emergencySheet,`${a}5:${b}5`,day.date_label);
    val(emergencySheet,`${a}6`,'时间段');val(emergencySheet,`${b}6`,'购电量');
    day.emergency.forEach((e,j)=>{
      val(emergencySheet,`${a}${j+7}`,e.time);
      number(emergencySheet,`${b}${j+7}`,`=SUM('数据明细'!I${6+144*i+e.start}:I${6+144*i+e.end-1})`);
    });
  }
  merged(emergencySheet,`A${nr+8}:H${nr+8}`,'注：连续发生的10分钟时段合并列示；空白表示该日期无更多紧急购电记录。');
  emergencySheet.getRange(`A${nr+8}:H${nr+8}`).format.font={name:'SimSun',size:10,color:'#000000'};
  wb.recalculate();
  const checks=[];
  for(const name of names){checks.push((await wb.inspect({kind:'table',range:`'${name}'!A5:F17`,include:'values,formulas',tableMaxRows:13,tableMaxCols:6,maxChars:12000})).ndjson);}
  checks.push((await wb.inspect({kind:'table',range:`'表3 紧急购电'!A5:H${nr+6}`,include:'values,formulas',tableMaxRows:nr+2,tableMaxCols:8,maxChars:24000})).ndjson);
  checks.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:50},summary:'final formula error scan'})).ndjson);
  await fs.mkdir(path.join(root,'_qa',strategy),{recursive:true});
  await fs.writeFile(path.join(root,'_qa',strategy,'workbook_inspect.ndjson'),checks.join('\n'));
  const out=await SpreadsheetFile.exportXlsx(wb);await out.save(filename(strategy));
  console.log(JSON.stringify({file:filename(strategy),sheets:6,intervals:data.days.map(d=>d.emergency.length)}));
}
