/**
 * CSV of the charts (ADR 0019 §11) — the rules of «Данные по скважинам» (Э6): separator «;», decimal point,
 * text quoted, text starting with = + - @ gets an apostrophe (a spreadsheet would run it), empty stays empty
 * (a missing value is never 0). The BOM is added when the file is written (bridge `saveFile`). Pure.
 */
export type CsvCell = string | number | null | undefined;

/** Text a spreadsheet would run as a formula (= + - @) gets a leading apostrophe. */
const safe = (t: string) => (/^[=+\-@\t\r]/.test(t) ? `'${t}` : t);

export function csvCell(v: CsvCell): string {
  if (v === null || v === undefined || v === "") return "";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "";
  return `"${safe(String(v)).replace(/"/g, '""')}"`;
}

/** Lines joined by CRLF; the header is text. */
export function csvText(header: readonly string[], rows: readonly (readonly CsvCell[])[]): string {
  return [header.map(csvCell).join(";"), ...rows.map((r) => r.map(csvCell).join(";"))].join("\r\n");
}
