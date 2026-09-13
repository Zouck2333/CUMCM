import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [source, output] = process.argv.slice(2);
await fs.mkdir(output, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(source));
const logs = [];
for (const [index, range, label] of [
  [0, "A1:H7", "plan"], [1, "A1:H7", "adjusted"],
  [2, "A1:F13", "battery"], [3, "A1:C16", "emergency"],
]) {
  const sheet = workbook.worksheets.getItemAt(index);
  logs.push((await workbook.inspect({kind: "table", sheetId: sheet.name, range,
    include: "values,formulas", tableMaxRows: 16, tableMaxCols: 8,
    maxChars: 3500})).ndjson);
  const blob = await workbook.render({sheetName: sheet.name, range, scale: 1.5});
  await fs.writeFile(path.join(output, `${label}.png`), new Uint8Array(await blob.arrayBuffer()));
}
logs.push((await workbook.inspect({kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: {useRegex: true, maxResults: 30}, maxChars: 1500})).ndjson);
await fs.writeFile(path.join(output, "inspection.ndjson"), logs.join("\n"));
console.log(JSON.stringify({source, output, renderedSheets: 4}));
// All inspection and image writes have completed; avoid renderer shutdown work.
process.exit(0);
