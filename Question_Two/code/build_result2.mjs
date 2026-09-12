import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";


const [, , templatePath, solutionPath, outputPath, previewDir] = process.argv;
if (!templatePath || !solutionPath || !outputPath || !previewDir) {
  throw new Error(
    "用法: node build_result2.mjs <result2模板> <solution.json> <输出xlsx> <预览目录>",
  );
}

const BLOCK_LABELS = [
  "0:00-4:00",
  "4:00-8:00",
  "8:00-12:00",
  "12:00-16:00",
  "16:00-20:00",
  "20:00-24:00",
];

function dateValue(isoDate) {
  return new Date(`${isoDate}T00:00:00`);
}

function blockSum(values, blockIndex) {
  const start = blockIndex * 24;
  return values.slice(start, start + 24).reduce((sum, value) => sum + value, 0);
}

function applyBodyStyle(range) {
  range.format.font = { name: "等线", size: 10, color: "#1F2937" };
  range.format.verticalAlignment = "center";
  range.format.borders = { preset: "all", style: "thin", color: "#D9E1F2" };
}

async function savePreview(workbook, sheetName, outputName, range) {
  const preview = await workbook.render({
    sheetName,
    range,
    scale: 1,
    format: "png",
  });
  const bytes = new Uint8Array(await preview.arrayBuffer());
  await fs.writeFile(path.join(previewDir, outputName), bytes);
}

const solution = JSON.parse(await fs.readFile(solutionPath, "utf8"));
const days = solution.days;
const timeLabels = solution.time_labels;
if (!Array.isArray(days) || days.length !== 334) {
  throw new Error(`正式结果应包含334天，实际为${Array.isArray(days) ? days.length : "非数组"}`);
}
if (!Array.isArray(timeLabels) || timeLabels.length !== 144) {
  throw new Error("求解结果必须包含144个物理时间区间标签");
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const input = await FileBlob.load(templatePath);
const workbook = await SpreadsheetFile.importXlsx(input);
const planSheet = workbook.worksheets.getItem("计划购电量");
const batterySheet = workbook.worksheets.getItem("充放电量");
const emergencySheet = workbook.worksheets.getItem("紧急购电量");

// 计划购电量：A为日期，B:EO为144个时段，EP/EQ为全天合计。
// 原模板标题比附件功率时点的右端点口径晚10分钟；在输出文件中校正标题，
// 使第1列对应0:00-0:10，第144列对应23:50-24:00。
planSheet.getRange("B1:EO1").write([timeLabels]);
const planRows = days.map((day) => [
  dateValue(day.date),
  ...day.grid,
  day.total_grid,
  day.plan_cost,
]);
planSheet.getRange("A2:EQ335").clear({ applyTo: "contents" });
planSheet.getRange("A2").write(planRows);
const planBody = planSheet.getRange("A2:EQ335");
applyBodyStyle(planBody);
planSheet.getRange("A2:A335").setNumberFormat("yyyy-mm-dd");
planSheet.getRange("B2:EP335").setNumberFormat("0.0000");
planSheet.getRange("EQ2:EQ335").setNumberFormat("0.00");
planSheet.freezePanes.freezeRows(1);
planSheet.freezePanes.freezeColumns(1);

// 充放电量：每24个10分钟时段汇总成一个4小时区间；SOC填写日初和日终。
const batteryRows = [];
for (const day of days) {
  for (let block = 0; block < 6; block += 1) {
    batteryRows.push([
      block === 0 ? dateValue(day.date) : null,
      BLOCK_LABELS[block],
      blockSum(day.charge, block),
      blockSum(day.discharge, block),
      block === 0 ? "0:00" : block === 1 ? "24:00" : null,
      block === 0 ? day.soc_start : block === 1 ? day.soc_end : null,
    ]);
  }
}
const batteryLastRow = batteryRows.length + 1;
batterySheet.getRange("A2:F2500").clear({ applyTo: "contents" });
batterySheet.getRange("A2").write(batteryRows);
const batteryBody = batterySheet.getRange(`A2:F${batteryLastRow}`);
applyBodyStyle(batteryBody);
batterySheet.getRange(`A2:A${batteryLastRow}`).setNumberFormat("yyyy-mm-dd");
batterySheet.getRange(`C2:D${batteryLastRow}`).setNumberFormat("0.0000");
batterySheet.getRange(`F2:F${batteryLastRow}`).setNumberFormat("0.0000");
batterySheet.getRange(`A1:F${batteryLastRow}`).format.autofitColumns();
batterySheet.getRange(`B1:B${batteryLastRow}`).format.columnWidth = 16;
batterySheet.freezePanes.freezeRows(1);

// 紧急购电量：只输出实际发生的连续区间；同一天仅首行显示日期。
const emergencyRows = [];
for (const day of days) {
  for (let index = 0; index < day.emergency_intervals.length; index += 1) {
    const interval = day.emergency_intervals[index];
    emergencyRows.push([
      index === 0 ? dateValue(day.date) : null,
      interval.period,
      interval.amount,
    ]);
  }
}
if (emergencyRows.length === 0) {
  emergencyRows.push([null, "全年未发生紧急购电", 0]);
}
const emergencyLastRow = emergencyRows.length + 1;
emergencySheet.getRange("A2:C10000").clear({ applyTo: "contents" });
emergencySheet.getRange("A2").write(emergencyRows);
const emergencyBody = emergencySheet.getRange(`A2:C${emergencyLastRow}`);
applyBodyStyle(emergencyBody);
emergencySheet.getRange(`A2:A${emergencyLastRow}`).setNumberFormat("yyyy-mm-dd");
emergencySheet.getRange(`C2:C${emergencyLastRow}`).setNumberFormat("0.0000");
emergencySheet.getRange(`A1:C${emergencyLastRow}`).format.autofitColumns();
emergencySheet.getRange(`B1:B${emergencyLastRow}`).format.columnWidth = 24;
emergencySheet.freezePanes.freezeRows(1);

workbook.recalculate();

const inspections = [];
inspections.push(
  await workbook.inspect({
    kind: "region",
    sheetId: "计划购电量",
    range: "A1:H5",
    maxChars: 3500,
  }),
);
inspections.push(
  await workbook.inspect({
    kind: "region",
    sheetId: "充放电量",
    range: "A1:F14",
    maxChars: 3500,
  }),
);
inspections.push(
  await workbook.inspect({
    kind: "region",
    sheetId: "紧急购电量",
    range: `A1:C${Math.min(emergencyLastRow, 15)}`,
    maxChars: 3500,
  }),
);
const errorInspection = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  maxChars: 3500,
});

for (const inspection of inspections) {
  console.log(inspection.ndjson ?? inspection);
}
console.log(errorInspection.ndjson ?? errorInspection);

await savePreview(workbook, "计划购电量", "计划购电量_preview.png", "A1:L8");
await savePreview(workbook, "充放电量", "充放电量_preview.png", "A1:F14");
await savePreview(
  workbook,
  "紧急购电量",
  "紧急购电量_preview.png",
  `A1:C${Math.min(emergencyLastRow, 15)}`,
);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(`已生成结果工作簿: ${outputPath}`);
