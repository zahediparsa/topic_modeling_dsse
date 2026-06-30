import fs from 'node:fs/promises';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const input = await FileBlob.load('../../Issues.xlsx');
const workbook = await SpreadsheetFile.importXlsx(input);
const summary = await workbook.inspect({
  kind: 'workbook,sheet,table,region',
  maxChars: 20000,
  tableMaxRows: 12,
  tableMaxCols: 20,
  tableMaxCellChars: 160,
});
console.log(summary.ndjson);
const sheetInfo = await workbook.inspect({ kind: 'sheet', include: 'id,name', maxChars: 4000 });
console.log(sheetInfo.ndjson);
for (const sheet of workbook.worksheets.items) {
  const used = sheet.getUsedRange();
  if (!used) continue;
  const preview = await workbook.render({ sheetName: sheet.name, autoCrop: 'all', scale: 1, format: 'png' });
  await fs.writeFile(`sheet-${sheet.name.replace(/[^A-Za-z0-9_-]/g, '_')}.png`, new Uint8Array(await preview.arrayBuffer()));
}
