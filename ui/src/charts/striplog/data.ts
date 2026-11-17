/**
 * Data of the window «Данные по скважинам» (ТЗ 7.17, ADR 0007 §6): the answer of `POST /viz/sets/{id}/log`
 * turned into the model's DownholeData (BF shapes), and the tables of a set with their fields.
 */
import { api } from "../../shell/api";
import type { DownholeData, DownholeInterval, IntervalColumn, IntervalTableInfo } from "../../viz/downhole/types";

export const MAX_HOLES = 30;

export interface LogTable {
  from: (number | null)[];
  to: (number | null)[];
  values: Record<string, (number | string | null)[]>;
  truncated?: boolean;
}

export interface LogAnswer {
  holes: { name: string; collar_z: number | null; td: number | null; found: boolean; tables: Record<string, LogTable> }[];
  tables: { key: string; id: number | null; name: string; kind: string; columns: IntervalColumn[] }[];
}

/** Columns → rows of intervals; `max_depth` is the deepest end (or the depth of the hole). */
export function toDownhole(a: LogAnswer): DownholeData {
  return {
    tables: a.tables.map((t) => ({ key: t.key, name: t.name, columns: t.columns, kind: t.kind === "event" ? "event" : "interval" })),
    holes: a.holes.map((h) => {
      const tables: Record<string, DownholeInterval[]> = {};
      let deepest = 0;
      for (const [key, t] of Object.entries(h.tables)) {
        const rows: DownholeInterval[] = [];
        const fields = Object.keys(t.values);
        for (let i = 0; i < t.from.length; i++) {
          const from = t.from[i];
          // events have one depth: the model draws them at `from` and needs `to` as well
          const to = t.to[i] ?? from;
          const values: Record<string, number | string | null> = {};
          for (const f of fields) values[f] = t.values[f][i] ?? null;
          rows.push({ from, to, values });
          if (to !== null && to > deepest) deepest = to;
        }
        tables[key] = rows;
      }
      return { hole_id: h.name, found: h.found, td: h.td, max_depth: Math.max(deepest, h.td ?? 0), tables };
    }),
  };
}

/** Interval tables of a set (assay first) with their fields, for the columns of the window. */
export async function loadTables(setId: number): Promise<IntervalTableInfo[]> {
  const r = await api.get<{ tables: { table: string; kind: string; name: string; rows: number }[] }>(`/drillholes/sets/${setId}/tables`);
  const tabs = r.tables.filter((t) => t.kind === "assay" || t.kind === "interval" || t.kind === "event");
  return Promise.all(
    tabs.map(async (t) => {
      const page = await api.get<{ columns: { name: string; kind: string; unit?: string | null; role?: string }[] }>(`/drillholes/sets/${setId}/tables/${t.table}?limit=1`);
      const columns = page.columns
        .filter((c) => c.role !== "fixed" && c.role !== "source")
        .map((c) => ({ name: c.name, kind: c.kind === "number" ? "number" : "text", unit: c.unit ?? "" }) as IntervalColumn);
      return { key: t.table, id: Number(t.table), name: t.name, builtin: false, row_count: t.rows, columns, kind: t.kind === "event" ? "event" : "interval" } as IntervalTableInfo;
    }),
  );
}

export async function loadHoleNames(setId: number): Promise<string[]> {
  return (await api.get<{ names: string[] }>(`/viz/sets/${setId}/hole-names`)).names;
}

export async function loadLog(setId: number, holes: string[], tables: { table: string; fields: string[] }[]): Promise<DownholeData> {
  if (holes.length > MAX_HOLES) throw new Error(`Не больше ${MAX_HOLES} скважин рядом`);
  return toDownhole(await api.post<LogAnswer>(`/viz/sets/${setId}/log`, { holes, tables }));
}
