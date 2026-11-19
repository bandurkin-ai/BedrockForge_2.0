/**
 * The window «Экспорт OMF» as data (ТЗ 7.24; ADR 0017 §4, decision 9; BF `OmfExportDialog`): three lists —
 * wireframes and DTMs, files of lines, block models — each object that cannot be written shown with its reason;
 * what is checked when the window opens; the name offered in the save window; why the export cannot start; the
 * body of `POST /omf/export`; the ids of the catalog (`dataset_ids` of `omf.export`) sorted into the three lists;
 * the file of an export started by the assistant — `Документы\BedrockForge\Экспорт\‹filename›.omf`. Pure.
 */
import { count, counted } from "../../shell/treeActions";
import type { ExportableLists, OmfExportBody, OmfExportResult } from "./api";

export type ListKey = keyof ExportableLists;
export const LIST_KEYS: readonly ListKey[] = ["wireframes", "line_files", "block_models"];

/** The ids checked in each list. */
export type Chosen = Record<ListKey, number[]>;

export const NONE: Chosen = { wireframes: [], line_files: [], block_models: [] };

export const LIST_TITLES: Record<ListKey, string> = { wireframes: "Каркасы и ЦТМ", line_files: "Файлы линий", block_models: "Блочные модели" };

const BODY_KEYS = { wireframes: "wireframe_ids", line_files: "line_file_ids", block_models: "block_model_ids" } as const satisfies Record<ListKey, keyof OmfExportBody>;

/** «7, 8,7» → [7, 8]; a word that is not a whole number — null. */
export function parseIds(text: string): number[] | null {
  const parts = text.split(/[\s,;]+/).filter(Boolean);
  if (!parts.length) return null;
  const out: number[] = [];
  for (const p of parts) {
    if (!/^\d+$/.test(p)) return null;
    const id = Number(p);
    if (!out.includes(id)) out.push(id);
  }
  return out;
}

export interface Sorted {
  chosen: Chosen;
  /** ids that are in none of the lists */
  unknown: number[];
  /** objects that cannot be written: «‹name›: ‹message› ‹hint›» */
  unavailable: string[];
}

/** Sorts ids of the catalog into the lists of the window (the ids of the objects of the project). */
export function sortIds(lists: ExportableLists, ids: readonly number[]): Sorted {
  const out: Sorted = { chosen: { wireframes: [], line_files: [], block_models: [] }, unknown: [], unavailable: [] };
  for (const id of ids) {
    const key = LIST_KEYS.find((k) => lists[k].some((o) => o.id === id));
    if (!key) {
      out.unknown.push(id);
      continue;
    }
    const o = lists[key].find((x) => x.id === id)!;
    if (o.available) out.chosen[key].push(id);
    else out.unavailable.push(`«${o.name}»: ${o.reason ? `${o.reason.message} ${o.reason.hint}`.trim() : "не записывается в OMF"}`);
  }
  return out;
}

/** Checked when the window opens: the named objects that can be written; nothing named — every one that can. */
export function initialChosen(lists: ExportableLists, ids: readonly number[] | null): Chosen {
  if (ids) return sortIds(lists, ids).chosen;
  const out: Chosen = { wireframes: [], line_files: [], block_models: [] };
  for (const k of LIST_KEYS) out[k] = lists[k].filter((o) => o.available).map((o) => o.id);
  return out;
}

/** Only what is still in the lists and can be written (a list read again after a change). */
export function keepChosen(lists: ExportableLists, chosen: Chosen): Chosen {
  const out: Chosen = { wireframes: [], line_files: [], block_models: [] };
  for (const k of LIST_KEYS) out[k] = chosen[k].filter((id) => lists[k].some((o) => o.id === id && o.available));
  return out;
}

export const chosenCount = (c: Chosen): number => LIST_KEYS.reduce((n, k) => n + c[k].length, 0);

/** «2 каркаса, 1 файл линий, 1 блочная модель»; nothing — «Ничего не отмечено». */
export function chosenText(c: Chosen): string {
  const parts: string[] = [];
  if (c.wireframes.length) parts.push(counted(c.wireframes.length, "каркас", "каркаса", "каркасов"));
  if (c.line_files.length) parts.push(counted(c.line_files.length, "файл линий", "файла линий", "файлов линий"));
  if (c.block_models.length) parts.push(counted(c.block_models.length, "блочная модель", "блочные модели", "блочных моделей"));
  return parts.length ? parts.join(", ") : "Ничего не отмечено";
}

/** Why the export cannot start; null — it can. */
export function exportReason(lists: ExportableLists, chosen: Chosen, path: string | null): string | null {
  if (!LIST_KEYS.some((k) => lists[k].length)) return "В проекте нет каркасов, линий и блочных моделей: экспортировать нечего.";
  if (!chosenCount(chosen)) return "Отметьте каркасы, файлы линий или блочные модели.";
  return path ? null : "Выберите файл: «Сохранить как…».";
}

const safe = (name: string): string => name.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/\s+/g, " ").trim();

/** The name offered in the save window: the only object, else the project, else «Экспорт». */
export function exportName(lists: ExportableLists, chosen: Chosen, project: string | null): string {
  const taken = LIST_KEYS.flatMap((k) => lists[k].filter((o) => chosen[k].includes(o.id)));
  const base = taken.length === 1 ? taken[0].name.replace(/\.[^.\\/]+$/, "") : (project ?? "");
  return `${safe(base) || "Экспорт"}.omf`;
}

export function exportBody(chosen: Chosen, path: string): OmfExportBody {
  const body: OmfExportBody = { wireframe_ids: [], line_file_ids: [], block_model_ids: [], path };
  for (const k of LIST_KEYS) body[BODY_KEYS[k]] = [...chosen[k]];
  return body;
}

/** The name the assistant gives (`filename`): no folders, no forbidden characters, `.omf` at the end. */
export function cleanFileName(raw: unknown, fallback: string): string {
  const text = typeof raw === "string" ? raw.split(/[\\/]/).pop()!.trim() : "";
  const base = safe(text.replace(/\.omf$/i, "")) || safe(fallback.replace(/\.omf$/i, "")) || "Экспорт";
  return `${base.slice(0, 120)}.omf`;
}

/** `Документы\BedrockForge\Экспорт` beside the folder of projects `…\BedrockForge\Проекты` (decision 9); a folder of
 * projects chosen elsewhere — its own «Экспорт» inside. The separator of the given path is kept. */
export function exportFolder(projectsRoot: string): string {
  const sep = projectsRoot.includes("\\") ? "\\" : "/";
  const root = projectsRoot.replace(/[\\/]+$/, "");
  const parts = root.split(/[\\/]/);
  if (parts[parts.length - 1] === "Проекты") parts[parts.length - 1] = "Экспорт";
  else parts.push("Экспорт");
  return parts.join(sep);
}

export const joinPath = (folder: string, name: string): string => `${folder}${folder.includes("\\") ? "\\" : "/"}${name}`;

/** «Записан model.omf: каркасов 2, слоёв линий 1, блочных моделей 1 (3 000 ячеек)». */
export function resultText(r: Partial<OmfExportResult>): string {
  const file = (r.path ?? "").split(/[\\/]/).pop() || "файл OMF";
  const parts = [`каркасов ${count(r.wireframes ?? 0)}`, `слоёв линий ${count(r.line_layers ?? 0)}`, `блочных моделей ${count(r.block_models ?? 0)}`];
  const cells = r.cells ? ` (${counted(r.cells, "ячейка", "ячейки", "ячеек")})` : "";
  return `Записан ${file}: ${parts.join(", ")}${cells}`;
}

/** A row of a list: «196 треугольников · ЦТМ», «3 слоя, 1 200 точек», «3 000 ячеек». */
export function rowNote(key: ListKey, o: { triangles?: number; dtm?: unknown; layers?: number; points?: number; cells?: number }): string {
  if (key === "wireframes") return `${counted(o.triangles ?? 0, "треугольник", "треугольника", "треугольников")}${o.dtm ? " · ЦТМ" : ""}`;
  if (key === "line_files") return `${counted(o.layers ?? 0, "слой", "слоя", "слоёв")}, ${counted(o.points ?? 0, "точка", "точки", "точек")}`;
  return counted(o.cells ?? 0, "ячейка", "ячейки", "ячеек");
}
