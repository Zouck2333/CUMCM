// Excel writer for output_data.py. Runs in a temporary directory containing
// a junction to the bundled @oai/artifact-tool runtime.
import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [sourcePath, templatePath, outputPath] = process.argv.slice(2);
if (!sourcePath || !templatePath || !outputPath) {
  throw new Error("Usage: output_xlsx.mjs days.json template.xlsx result3.xlsx");
}
const days = JSON.parse(await fs.readFile(sourcePath, "utf8"));
if (!Array.isArray(days) || days.length < 1 || days.length > 334) {
  throw new Error("Expected 1 to 334 evaluation-day records");
}

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(templatePath));
const planSheet = workbook.worksheets.getItemAt(0);
const adjustedSheet = workbook.worksheets.getItemAt(1);
const storageSheet = workbook.worksheets.getItemAt(2);
const emergencySheet = workbook.worksheets.getItemAt(3);

function clock(boundary, last = "24:00") {
  if (boundary === 144) return last;
  const minute = boundary * 10;
  return `${Math.floor(minute / 60)}:${String(minute % 60).padStart(2, "0")}`;
}
function excelDate(day) {
  return new Date(`${day.date}T00:00:00.000Z`);
}
function periodsInDay(values, threshold = 1e-8) {
  const intervals = [];
  let begin = null;
  let amount = 0;
  for (let t = 0; t <= 144; t++) {
    const active = t < 144 && values[t] > threshold;
    if (active) {
      if (begin === null) begin = t;
      amount += values[t];
    } else if (begin !== null) {
      intervals.push([begin, t, amount]);
      begin = null;
      amount = 0;
    }
  }
  return intervals;
}

const headers = Array.from({ length: 144 }, (_, t) =>
  `${clock(t)}-${clock(t + 1, "0:00+1")}`
);
for (const sheet of [planSheet, adjustedSheet]) {
  sheet.getRange("B1:EO1").values = [headers];
  sheet.getRange("A2:EQ335").clear({ applyTo: "contents" });
}

const planRows = days.map(day => [
  excelDate(day), ...day.initial_purchase,
  day.initial_purchase.reduce((a, b) => a + b, 0), day.plan_cost,
]);
const adjustedRows = days.map(day => [
  excelDate(day), ...day.final_purchase,
  day.final_purchase.reduce((a, b) => a + b, 0), day.total_cost,
]);
planSheet.getRangeByIndexes(1, 0, days.length, 147).values = planRows;
adjustedSheet.getRangeByIndexes(1, 0, days.length, 147).values = adjustedRows;
for (const sheet of [planSheet, adjustedSheet]) {
  sheet.getRangeByIndexes(1, 0, days.length, 1).setNumberFormat("yyyy-mm-dd");
  sheet.getRangeByIndexes(1, 1, days.length, 146).setNumberFormat("0.0000");
}

// Imported-template copyFrom does not reliably export styles beyond its
// original used range. Give every six-row day explicit visible boundaries.
storageSheet.getRange("A1").format.columnWidth = 17;
storageSheet.getRange("B1").format.columnWidth = 18;
const storageBody = storageSheet.getRangeByIndexes(1, 0, days.length * 6, 6);
storageBody.format.borders = { preset: "all", style: "thin", color: "#A9ADB3" };
storageBody.format.rowHeight = 14;
storageSheet.getRangeByIndexes(1, 0, days.length * 6, 2)
  .format.horizontalAlignment = "center";
storageSheet.getRangeByIndexes(1, 4, days.length * 6, 1)
  .format.horizontalAlignment = "center";
for (let i = 0; i < days.length; i++) {
  storageSheet.getRangeByIndexes(1 + 6 * i + 5, 0, 1, 6).format.borders = {
    bottom: { style: "medium", color: "#50545A" },
  };
}
storageSheet.getRangeByIndexes(1, 0, Math.max(25, days.length * 6), 6)
  .clear({ applyTo: "contents" });
const storageRows = [];
for (const day of days) {
  for (let block = 0; block < 6; block++) {
    const start = block * 24;
    const stop = start + 24;
    storageRows.push([
      block === 0 ? excelDate(day) : null,
      `${clock(start)}-${clock(stop)}`,
      day.charge.slice(start, stop).reduce((a, b) => a + b, 0),
      day.discharge.slice(start, stop).reduce((a, b) => a + b, 0),
      block === 0 ? "0:00" : block === 1 ? "24:00" : null,
      block === 0 ? day.soc_start : block === 1 ? day.soc_end : null,
    ]);
  }
}
storageSheet.getRangeByIndexes(1, 0, storageRows.length, 6).values = storageRows;
storageSheet.getRangeByIndexes(1, 0, storageRows.length, 1).setNumberFormat("yyyy-mm-dd");
storageSheet.getRangeByIndexes(1, 2, storageRows.length, 2).setNumberFormat("0.0000");
storageSheet.getRangeByIndexes(1, 5, storageRows.length, 1).setNumberFormat("0.0000");

// Emergency periods are run-length encoded; zero-emergency days keep one
// blank period row so every evaluation date remains visible in the template.
const emergencyRows = [];
const emergencyDayEndRows = [];
for (const day of days) {
  const periods = periodsInDay(day.emergency);
  if (periods.length === 0) {
    emergencyRows.push([excelDate(day), null, null]);
  } else {
    for (let i = 0; i < periods.length; i++) {
      const [start, stop, quantity] = periods[i];
      emergencyRows.push([
        i === 0 ? excelDate(day) : null,
        `${clock(start)}-${clock(stop)}`,
        quantity,
      ]);
    }
  }
  emergencyDayEndRows.push(emergencyRows.length + 1);
}
emergencySheet.getRangeByIndexes(1, 0, Math.max(10, emergencyRows.length), 3)
  .clear({ applyTo: "contents" });
emergencySheet.getRange("A1").format.columnWidth = 17;
emergencySheet.getRange("B1").format.columnWidth = 20;
emergencySheet.getRange("C1").format.columnWidth = 16;
const emergencyBody = emergencySheet.getRangeByIndexes(1, 0, emergencyRows.length, 3);
emergencyBody.format.borders = { preset: "all", style: "thin", color: "#A9ADB3" };
emergencyBody.format.rowHeight = 14;
emergencySheet.getRangeByIndexes(1, 0, emergencyRows.length, 2)
  .format.horizontalAlignment = "center";
for (const excelRow of emergencyDayEndRows) {
  emergencySheet.getRangeByIndexes(excelRow - 1, 0, 1, 3).format.borders = {
    bottom: { style: "medium", color: "#50545A" },
  };
}
emergencySheet.getRangeByIndexes(1, 0, emergencyRows.length, 3).values = emergencyRows;
emergencySheet.getRangeByIndexes(1, 0, emergencyRows.length, 1).setNumberFormat("yyyy-mm-dd");
emergencySheet.getRangeByIndexes(1, 2, emergencyRows.length, 1).setNumberFormat("0.0000");

workbook.recalculate();
const result = await SpreadsheetFile.exportXlsx(workbook);
await result.save(outputPath);
console.log(JSON.stringify({ days: days.length, emergencyRows: emergencyRows.length }));
