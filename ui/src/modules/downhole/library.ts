/**
 * The style library and the saved displays of a project (ТЗ 7.16, ADR 0007 §3): API of the core and its
 * cache for the forms. Built-in styles (BUILTIN_STYLES) are read-only and never sent to the core.
 */
import { bridge, saveFile } from "../../bridge";
import { api } from "../../shell/api";
import { Store } from "../../shell/store";
import type { StripLogSettings } from "../../charts/striplog/model";
import { BUILTIN_STYLES, parseStyleFile, type DataKind, type StyleLevel, type StyleRec } from "../../viz/downhole/styles";

export interface StyleRow extends StyleRec {
  version: number;
  author: string | null;
  updated_at: string;
}

export interface ChartRow {
  id: number;
  set_id: number;
  name: string;
  kind: "striplog";
  settings: StripLogSettings;
  version: number;
  updated_at: string;
}

export interface StyleUsage {
  chart_id: number;
  name: string;
  set_id: number;
  linked: boolean;
  tracks: string[];
}

export const libraryStore = new Store<{
  styles: StyleRow[];
  charts: ChartRow[];
  loaded: boolean;
  /** the last deleted style: every open form keeps a copy of its settings (ТЗ 7.16) */
  removed: { style: StyleRec; seq: number } | null;
}>({ styles: [], charts: [], loaded: false, removed: null });

/** Library styles and the built-in ones (built-in last, as in BF). */
export function allStyles(): StyleRec[] {
  return [...libraryStore.get().styles, ...BUILTIN_STYLES];
}

export async function loadLibrary(): Promise<void> {
  const [s, c] = await Promise.all([api.get<{ styles: StyleRow[] }>("/styles"), api.get<{ charts: ChartRow[] }>("/charts?kind=striplog")]);
  libraryStore.set({ styles: s.styles, charts: c.charts, loaded: true });
}

export async function createStyle(name: string, level: StyleLevel, dataKind: DataKind, body: Record<string, unknown>, defaultFor: string[] = []): Promise<StyleRow> {
  const s = await api.post<StyleRow>("/styles", { name, level, data_kind: dataKind, body, default_for: defaultFor });
  libraryStore.set((x) => ({ styles: [...x.styles, s] }));
  return s;
}

export async function patchStyle(style: StyleRow, patch: Partial<Pick<StyleRow, "name" | "body" | "default_for" | "data_kind">>): Promise<StyleRow> {
  const s = await api.patch<StyleRow>(`/styles/${style.id}`, { ...patch, expected_version: style.version });
  libraryStore.set((x) => ({ styles: x.styles.map((y) => (y.id === s.id ? s : y)) }));
  return s;
}

/** Deletes a style; the displays that used it keep its settings (the core unlinks them). */
export async function deleteStyle(style: StyleRow): Promise<number[]> {
  const r = await api.del<{ charts: number[] }>(`/styles/${style.id}`);
  libraryStore.set((x) => ({ removed: { style, seq: (x.removed?.seq ?? 0) + 1 } }));
  await loadLibrary();
  return r.charts;
}

export async function styleUsage(style: StyleRow): Promise<StyleUsage[]> {
  return (await api.get<{ usage: StyleUsage[] }>(`/styles/${style.id}/usage`)).usage;
}

/** «Экспорт…»: a .bfstyle.json through the save dialog; the path or null. */
export async function exportStyles(ids: number[] | null): Promise<string | null> {
  const r = await api.post<{ name: string; file: unknown }>("/styles/export", { ids });
  return saveFile(r.name, "bfstyle", JSON.stringify(r.file, null, 1));
}

export interface StyleImportResult {
  created: number;
  replaced: number;
  skipped: number;
  renamed: { from: string; to: string; level: string }[];
}

/** «Импорт…» step 1: the file and the names already in the library (to ask about each). */
export async function readStyleFile(): Promise<{ name: string; file: unknown; conflicts: string[]; count: number } | null> {
  const b = bridge();
  if (!b) throw new Error("Открытие файлов доступно только в приложении");
  const f = await b.openText("bfstyle");
  if (!f) return null;
  if (f.text === null) throw new Error(f.error ?? "Файл не читается");
  const file = parseStyleFile(f.text);
  const r = await api.post<{ styles: number; conflicts: string[] }>("/styles/import?preview=true", { file });
  return { name: f.name, file, conflicts: r.conflicts, count: r.styles };
}

/** «Импорт…» step 2: decisions per «level:name» (replace, keep_both, skip). */
export async function importStyles(file: unknown, conflicts: Record<string, "replace" | "keep_both" | "skip">, fallback: "replace" | "keep_both" | "skip" = "skip"): Promise<StyleImportResult> {
  const r = await api.post<StyleImportResult & { styles: StyleRow[] }>("/styles/import", { file, conflicts, default: fallback });
  libraryStore.set({ styles: r.styles });
  return r;
}

// ---- saved displays (objects «chart» under the set) ----

export async function saveChart(setId: number, name: string, settings: StripLogSettings, existing?: ChartRow): Promise<ChartRow> {
  const c = existing
    ? await api.put<ChartRow>(`/charts/${existing.id}`, { name, settings, expected_version: existing.version })
    : await api.post<ChartRow>("/charts", { set_id: setId, name, settings });
  libraryStore.set((x) => ({ charts: [...x.charts.filter((y) => y.id !== c.id), c] }));
  return c;
}

export async function deleteChart(chart: ChartRow): Promise<void> {
  await api.del(`/charts/${chart.id}`);
  libraryStore.set((x) => ({ charts: x.charts.filter((y) => y.id !== chart.id) }));
}

export function chartsOfSet(setId: number): ChartRow[] {
  return libraryStore.get().charts.filter((c) => c.set_id === setId);
}
