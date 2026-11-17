/**
 * CSV of the window «Данные по скважинам» (ТЗ 7.17 «CSV содержит значения всех колонок по глубинам»):
 * per hole the boundaries of every column are merged, a row per piece between them; an event gives a row at
 * its depth. Separator «;», decimal point, empty stays empty (never 0). The BOM is added when the file is
 * written (bridge saveFile).
 */
import type { DownholeData, DownholeInterval } from "../../viz/downhole/types";

export interface CsvColumn {
  table: string;
  field: string;
  title: string;
  event: boolean;
}

const cell = (v: string | number | null | undefined) =>
  v === null || v === undefined || v === "" ? "" : typeof v === "number" ? String(v) : `"${String(v).replace(/"/g, '""')}"`;

/** The value of an interval column over [a, b]: the interval that holds it (the first one when they overlap). */
function covering(rows: DownholeInterval[], field: string, a: number, b: number): number | string | null {
  for (const r of rows) {
    if (r.from === null || r.to === null) continue;
    if (r.from <= a + 1e-9 && r.to >= b - 1e-9 && r.to > r.from) return r.values[field] ?? null;
  }
  return null;
}

export function logCsv(data: DownholeData, columns: CsvColumn[]): string {
  const lines = [["Скважина", "От", "До", ...columns.map((c) => c.title)].map(cell).join(";")];
  for (const h of data.holes) {
    const marks = new Set<number>();
    const events: number[] = [];
    for (const c of columns) {
      for (const r of h.tables[c.table] ?? []) {
        if (r.from === null) continue;
        if (c.event) events.push(r.from);
        else if (r.to !== null) {
          marks.add(r.from);
          marks.add(r.to);
        }
      }
    }
    const depths = [...marks].sort((x, y) => x - y);
    const rows: { a: number; b: number; cells: (number | string | null)[] }[] = [];
    for (let i = 0; i + 1 < depths.length; i++) {
      const a = depths[i];
      const b = depths[i + 1];
      const cells = columns.map((c) => (c.event ? null : covering(h.tables[c.table] ?? [], c.field, a, b)));
      if (cells.some((v) => v !== null)) rows.push({ a, b, cells });
    }
    for (const d of [...new Set(events)].sort((x, y) => x - y)) {
      const cells = columns.map((c) => {
        if (!c.event) return covering(h.tables[c.table] ?? [], c.field, d, d);
        const ev = (h.tables[c.table] ?? []).find((r) => r.from !== null && Math.abs(r.from - d) < 1e-9);
        return ev ? (ev.values[c.field] ?? null) : null;
      });
      rows.push({ a: d, b: d, cells });
    }
    rows.sort((x, y) => x.a - y.a || x.b - y.b);
    for (const r of rows) lines.push([h.hole_id, r.a, r.b, ...r.cells].map(cell).join(";"));
  }
  return lines.join("\r\n");
}
