/**
 * «Модель на скважины» (ТЗ 7.35 Д-34 ч. 1; docs/progress/e19-api.md §8.4): what the dialog knows and the run of the command
 * `drillholes.from_model`. The dialog is a form of the command: its «Записать» runs the command with the typed values, so the registry, the
 * log and the assistant see the same write. The run asks the core (`POST /drillholes/from_model`: the request is checked and the name of
 * the table is tried before a job is made — 409 `table_exists` with the free name `suggested` stands under the field), follows the job
 * `drillholes.from_model` to its end and keeps its result for the dialog. No React.
 */
import { CoreError } from "../../shell/api";
import { refusal } from "../../shell/objectRun";
import { num } from "../../shell/props";
import { Store } from "../../shell/store";
import { loadJob } from "../blocks/build/api";
import { projectStore } from "../projects/state";
import { refusalOf, waitJob } from "./api";
import { readFromModelResult, startFromModel, type FromModelBody, type FromModelResult } from "./compareApi";

export const FROM_WINDOW = "implicit-from-model";
export const FROM_TITLE = "Модель на скважины";

export type FromSource = "model" | "meshes" | "field";

export interface FromWin {
  setId: number | null;
  name: string;
  /** the person typed the name: the default no longer follows the source */
  nameTouched: boolean;
  source: FromSource;
  modelId: number | null;
  /** closed wireframes, by the priority (the first is cut out first) */
  meshIds: number[];
  /** the code of a body: the id of the group (a model) or of the wireframe (meshes) → the code; empty — the name */
  codes: Record<string, string>;
  blockId: number | null;
  /** the number field of the block model */
  field: string | null;
  /** the interval table whose intervals are the places of the field */
  table: string | null;
  busy: boolean;
  error: { code: string; message: string; hint: string; param: string | null } | null;
  /** the free name the core offers when the table exists */
  suggested: string | null;
  result: FromModelResult | null;
  /** the line of the job: «Пересечения» */
  stage: string | null;
}

const fresh = (): FromWin => ({ setId: null, name: "", nameTouched: false, source: "model", modelId: null, meshIds: [], codes: {}, blockId: null, field: null, table: null, busy: false, error: null, suggested: null, result: null, stage: null });

export const fromStore = new Store<FromWin>(fresh());

export const resetFrom = (): void => fromStore.set(fresh());

export function setFrom(patch: Partial<FromWin>): void {
  fromStore.set({ ...patch, error: null, suggested: null, result: null });
}

// ---- the arguments of the command (flat text) ----

const ids = (text: unknown): number[] =>
  String(text ?? "")
    .split(/[;,\s]+/)
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isInteger(n) && n > 0);

/** «12=Дайка; 14=Руда» → {"12": "Дайка", "14": "Руда"}; a line that is not «номер=код» is a refusal. */
export function parseCodes(text: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of String(text ?? "").split(/[;\n]+/)) {
    const p = part.trim();
    if (!p) continue;
    const m = /^(\d+)\s*=\s*(.+)$/.exec(p);
    if (!m) throw refusal("bad_param", `Коды тел: «${p}» — не «номер=код».`, "Пишите «12=Дайка; 14=Руда»: номер группы модели (или каркаса) и код через «=».");
    out[m[1]] = m[2].trim();
  }
  return out;
}

export const codesText = (codes: Record<string, string>): string =>
  Object.entries(codes)
    .filter(([, v]) => v.trim())
    .map(([k, v]) => `${k}=${v.trim()}`)
    .join("; ");

/** Does the command carry enough to write (else it opens the dialog)? */
export const complete = (a: Record<string, unknown>): boolean => typeof a.set_id === "number" && typeof a.name === "string" && !!a.name.trim() && (typeof a.model_id === "number" || !!ids(a.mesh_ids).length || (typeof a.block_model_id === "number" && !!a.field && !!a.table));

/** The body of the request from the arguments of the command (the form of the dialog is turned into the same arguments). */
export function bodyOf(a: Record<string, unknown>): FromModelBody {
  const given = [typeof a.model_id === "number", ids(a.mesh_ids).length > 0, typeof a.block_model_id === "number"].filter(Boolean).length;
  if (given !== 1) throw refusal("bad_param", "Выберите одно: модель, каркасы или поле блочной модели.", "Задайте ровно одно из model_id, mesh_ids, block_model_id.");
  const set_id = a.set_id as number;
  const name = String(a.name ?? "").trim();
  const codes = parseCodes(a.codes);
  const withCodes = Object.keys(codes).length ? { codes } : {};
  if (typeof a.model_id === "number") return { set_id, name, model_id: a.model_id, ...withCodes };
  if (typeof a.block_model_id === "number") {
    if (!a.field || !a.table) throw refusal("bad_param", "Для поля блочной модели нужны field и table.", "Выберите числовое поле модели и таблицу интервалов набора.");
    return { set_id, name, block_model_id: a.block_model_id, field: String(a.field), table: String(a.table) };
  }
  return { set_id, name, mesh_ids: ids(a.mesh_ids), ...withCodes };
}

/** The arguments of the command from the form of the dialog. */
export function argsOf(f: FromWin): Record<string, unknown> {
  const base: Record<string, unknown> = { set_id: f.setId, name: f.name.trim() };
  if (f.source === "model") return { ...base, model_id: f.modelId, ...(codesText(f.codes) ? { codes: codesText(f.codes) } : {}) };
  if (f.source === "meshes") return { ...base, mesh_ids: f.meshIds.join("; "), ...(codesText(f.codes) ? { codes: codesText(f.codes) } : {}) };
  return { ...base, block_model_id: f.blockId, field: f.field, table: f.table };
}

// ---- the run ----

/** «DY — 5 интервалов, 252,7 м; SS — …» */
export function codesLine(r: FromModelResult): string {
  return r.codes.map((c) => `${c.code} — ${num(c.rows, 0)} инт.${c.length !== null ? `, ${num(c.length, 1)} м` : ""}`).join("; ");
}

export function resultLine(r: FromModelResult): string {
  const what = r.mode === "field" ? `значения поля в серединах интервалов: строк ${num(r.rows, 0)}` : `строк ${num(r.rows, 0)}, скважин ${num(r.holes, 0)}${r.length_inside !== null && r.length !== null ? `, внутри тел ${num(r.length_inside, 0)} м из ${num(r.length, 0)} м` : ""}`;
  const lost = r.no_trajectory ? `; без траектории — ${num(r.no_trajectory, 0)} скв., в таблицу не вошли` : "";
  const codes = r.codes.length ? `; ${codesLine(r)}` : "";
  return `Таблица «${r.name}» записана в набор: ${what}${codes}${lost}.`;
}

function fail(e: unknown): never {
  const r = refusalOf(e);
  const suggested = e instanceof CoreError && r.code === "table_exists" && typeof e.body?.suggested === "string" ? e.body.suggested : null;
  fromStore.set({ busy: false, stage: null, error: { code: r.code, message: r.message, hint: r.hint, param: r.param }, suggested });
  throw refusal(r.code, r.message, suggested ? `${r.hint} Свободное название: «${suggested}».` : r.hint);
}

/** `drillholes.from_model` with the arguments: the table is written; the text of the result. */
export async function runFromModel(a: Record<string, unknown>): Promise<string> {
  fromStore.set({ busy: true, error: null, suggested: null, result: null, stage: "Запрос" });
  const uid = projectStore.get().current?.uid ?? null;
  let body: FromModelBody;
  try {
    body = bodyOf(a);
  } catch (e) {
    fail(e);
  }
  let started;
  try {
    started = await startFromModel(body);
  } catch (e) {
    fail(e);
  }
  fromStore.set({ stage: "Задание запущено" });
  let end;
  try {
    end = await waitJob(started);
  } catch (e) {
    fail(e);
  }
  if ((projectStore.get().current?.uid ?? null) !== uid) return "Проект изменился: результат больше не показывается";
  if (end.status === "cancelled") {
    fromStore.set({ busy: false, stage: null });
    return "Запись модели на скважины отменена: ничего не записано";
  }
  if (end.status === "failed") fail(new CoreError(0, end.error?.code ?? "job_failed", end.error?.message ?? "Модель не записана на скважины.", end.error?.hint ?? "Повторите запись."));
  let done;
  try {
    done = await loadJob(started);
  } catch (e) {
    fail(e);
  }
  const r = readFromModelResult(done.result);
  if (!r) fail(new CoreError(0, "bad_answer", "Ядро вернуло запись без итогов.", "Повторите запись; если повторится — обновите программу."));
  fromStore.set({ busy: false, stage: null, result: r });
  return resultLine(r);
}
