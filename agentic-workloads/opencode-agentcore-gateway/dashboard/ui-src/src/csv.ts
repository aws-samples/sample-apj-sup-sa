// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// CSV export. Cells starting with = + - @ (after any whitespace), tab or CR are prefixed with ' so spreadsheets do not run them as formulas.
type Cell = string | number | null | undefined;

function cell(v: Cell): string {
  let s = v === null || v === undefined ? '' : String(v);
  // Leading whitespace too: some spreadsheets trim it before deciding a cell is a formula.
  if (/^\s*[=+\-@]|^[\t\r]/.test(s) && Number.isNaN(Number(s))) s = `'${s}`;
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function downloadCsv(filename: string, header: string[], rows: Cell[][]): void {
  const text = [header, ...rows].map((r) => r.map(cell).join(',')).join('\r\n') + '\r\n';
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
