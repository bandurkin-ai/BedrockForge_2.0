/**
 * `implicit.structural {model_id?, set_id?, table?, …}` (ТЗ 7.35 Д-15, 8.9.2; ADR 0026 «Команды реестра», решение 27): the measurements of the
 * dip of a table of holes (or a reference wireframe) → a structural trend → the trend of the model. No React.
 * - the source is complete (`set_id`, `table`, `alpha`, `dipdir`, or `wireframe_id`): `trend.save {kind: "structural", …}` through the registry,
 *   then — with a model — `PUT /models/{id} {trend_id}` (the form of the stored model with the trend chosen from the list);
 * - otherwise the window «Тренд» (`trend.open`: a new structural trend) opens with the set and the table put in; with a model, the window is
 *   watched: the trend saved in it becomes the trend of the model.
 */
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { refusal } from "../../shell/objectRun";
import { errorText } from "../../shell/props";
import { lastSaved, openTrendWindow } from "../trends/bind";
import { editOf, EMPTY_STRUCTURAL, setEdit, trendsStore, type SourceType } from "../trends/state";
import { refusalOf, replaceModel } from "./api";
import { bodyOf, formOfMeta } from "./form";
import { implicitStore, loadDetails, loadRows, modelById, pickModel } from "./state";
import { ensureDefaults } from "./wizardState";

const has = (a: Record<string, unknown>, k: string): boolean => a[k] !== undefined && a[k] !== null && (typeof a[k] !== "string" || (a[k] as string).trim() !== "");

/** The trend of the model: the stored form with the trend chosen from the list, written as a new version (`expected_version` is the stored one). */
export async function setModelTrend(modelId: number, trendId: number): Promise<string> {
  const info = await loadDetails(modelId).catch((e: unknown) => {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  });
  if (!info) throw refusal("project_changed", "Проект изменился.", "Повторите команду.");
  const built = bodyOf({ ...formOfMeta(info.name, info.meta), trend: "list", trendId }, await ensureDefaults());
  if (!built.ok) throw refusal(built.issue.code, built.issue.message, built.issue.hint);
  try {
    const saved = await replaceModel(modelId, info.version, built.body);
    await loadRows();
    void loadDetails(modelId).catch(() => undefined);
    return `тренд №${trendId} задан модели «${saved.name}» (версия ${saved.version}); чтобы он вошёл в поверхность, выполните «Обновить»`;
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
}

// ---- the window of a new trend watched for the model ----

let pending: { win: string; modelId: number; off: () => void } | null = null;

function unwatch(): void {
  pending?.off();
  pending = null;
}

/** The trend saved in the window becomes the trend of the model; a window closed unsaved ends the watch. */
function watch(app: App, win: string, modelId: number): void {
  unwatch();
  const off = trendsStore.subscribe(() => {
    if (!pending || pending.win !== win) return;
    const e = editOf(win);
    if (!e) return unwatch();
    if (e.trendId === null) return;
    const id = e.trendId;
    unwatch();
    setModelTrend(modelId, id).then(
      (text) => app.store.set({ statusNote: `Модель №${modelId}: ${text}` }),
      (err: unknown) => app.store.set({ statusNote: errorText(err) }),
    );
  });
  pending = { win, modelId, off };
}

export function forgetStructural(): void {
  unwatch();
}

/** The window «Тренд» of a new structural trend with what is known of the source. */
function openWindow(app: App, a: Record<string, unknown>, modelId: number | null, setId: number | null): string {
  const win = openTrendWindow(null, has(a, "name") ? { name: String(a.name).trim() } : {}, "structural");
  const mesh = has(a, "wireframe_id") || a.source === "mesh";
  const type: SourceType = mesh ? "mesh" : "measurements";
  setEdit(win, {
    structural: {
      ...EMPTY_STRUCTURAL,
      type,
      setId: has(a, "set_id") ? Number(a.set_id) : setId,
      table: has(a, "table") ? String(a.table).trim() : "",
      alpha: has(a, "alpha") ? String(a.alpha).trim() : "",
      dipdir: has(a, "dipdir") ? String(a.dipdir).trim() : "",
      depth: has(a, "depth") ? String(a.depth).trim() : "",
      wireframeId: has(a, "wireframe_id") ? Number(a.wireframe_id) : null,
    },
  });
  if (modelId !== null) watch(app, win, modelId);
  return win;
}

export async function runStructural(app: App, a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const id = pickModel(a.model_id, app.store.get().selected);
  if (id !== null && !modelById(id) && implicitStore.get().loaded) throw refusal("model_not_found", `Неявная модель №${id} не найдена.`, "Номера моделей — в разделе «Неявные модели» дерева.");
  const complete = has(a, "wireframe_id") || (has(a, "set_id") && has(a, "table") && has(a, "alpha") && has(a, "dipdir"));
  if (!complete) {
    // the set of the data of the model is the default of the set; the table of the dip is the person's choice
    const info = id !== null ? await loadDetails(id).catch(() => null) : null;
    const setId = info ? formOfMeta(info.name, info.meta).setId : null;
    openWindow(app, a, id, setId);
    const tail = id !== null ? ` Сохранённый тренд станет трендом модели №${id}.` : "";
    return `Открыто окно «Новый тренд» (структурный): выберите набор, таблицу замеров и поля α и Aп.${tail}`;
  }
  const args: Record<string, unknown> = { kind: "structural" };
  for (const k of ["name", "source", "set_id", "table", "alpha", "dipdir", "depth", "wireframe_id", "r_n", "radius"]) if (has(a, k)) args[k] = a[k];
  const before = lastSaved();
  const r = await app.registry.execute("trend.save", args, source);
  if (!r.ok) throw refusal("trend_not_saved", r.result, "Исправьте параметры источника и повторите команду.");
  const saved = lastSaved();
  if (!saved || saved === before) return r.result;
  if (id === null) return `${r.result}. Модель не выбрана: выберите тренд в мастере модели (шаг «Интерполяция») или повторите команду с model_id.`;
  return `${r.result}; ${await setModelTrend(id, saved.id)}`;
}
