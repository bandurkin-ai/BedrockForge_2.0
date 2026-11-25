/**
 * The handlers of `blocks.field.props`, `blocks.field.rebuild`, `blocks.field.delete` (ТЗ 8.7.2, 8.7.5; ADR 0023 §2, §7, §9;
 * № 74) and the list of the properties of the fields of the window: the fields of the model chosen with their unit,
 * description, role, maker, «устарел», «Где используется» (`GET /blocks/{id}/fields`). The properties are edited by the
 * version of the model the interface read (409 — read again, told); a field is deleted only when nothing uses it and it
 * is not the field of the volume (the consumers are told in our own window; the core says the same with 422
 * `field_in_use`); «Перестроить» runs the job that made the field once more over its parameters. A field is not renamed
 * (№ 74): the window says how. A command without the model and the field opens the window on its tab. No React.
 */
import type { App } from "../../../shell/app";
import { CoreError } from "../../../shell/api";
import type { CommandSource } from "../../../shell/commands";
import { askConfirm, showMessage } from "../../../shell/dialogs";
import { refusal } from "../../../shell/objectRun";
import { errorText } from "../../../shell/props";
import { blockById, loadBlocks } from "../state";
import { deleteField, loadFields, patchField, refusalOf, startRebuild, usageOfError, type FieldPatch, type FieldRow } from "./api";
import { openFieldsWindow, windowOfFields } from "./bind";
import { given, isInt, issue, limitIssue, MAX_DESCRIPTION, MAX_UNIT, type Args } from "./common";
import { notFound, READ_ONLY, runOp } from "./run";
import { clearPropsEdit, currentModel, fieldsStore, setIssue, setProps, type FieldEdit } from "./state";
import { deleteReason, fieldBrief, fieldLine, roleTitle, usageLine, usageNames } from "./propsText";

/** The place of a refusal in the list: the row of the field. */
export const rowPlace = (name: string): string => `field:${name}`;

export const editOf = (f: FieldRow): FieldEdit => ({ unit: f.unit ?? "", description: f.description ?? "", role: f.role ?? "" });

/** The edits that still differ from the list (a field that is gone or saved has none). */
function keepEdits(edits: Record<string, FieldEdit>, rows: readonly FieldRow[]): Record<string, FieldEdit> {
  const out: Record<string, FieldEdit> = {};
  for (const f of rows) {
    const e = edits[f.name];
    const base = editOf(f);
    if (e && (e.unit !== base.unit || e.description !== base.description || e.role !== base.role)) out[f.name] = e;
  }
  return out;
}

/** The keys of the row that were changed: what `PATCH` carries. */
export function patchOfEdit(f: FieldRow, e: FieldEdit): FieldPatch {
  const base = editOf(f);
  const out: FieldPatch = {};
  if (e.unit !== base.unit) out.unit = e.unit;
  if (e.description !== base.description) out.description = e.description;
  if (e.role !== base.role) out.role = e.role === "" ? null : e.role;
  return out;
}

let readSeq = 0;

/** Reads the properties of the fields of the model (the edits not saved stay while they differ from the list). */
export async function refreshProps(modelId: number | null = currentModel()): Promise<void> {
  if (modelId === null) return setProps({ modelId: null, list: null, loading: false, error: null, edits: {}, usageOpen: null });
  const mine = ++readSeq;
  setProps({ modelId, loading: true, error: null });
  try {
    const list = await loadFields(modelId);
    if (mine !== readSeq || currentModel() !== modelId) return;
    fieldsStore.set((s) => ({ props: { ...s.props, modelId, list, loading: false, error: null, edits: keepEdits(s.props.edits, list.fields) } }));
  } catch (e) {
    if (mine === readSeq) setProps({ loading: false, error: errorText(e) });
  }
}

const fieldNotFound = (model: string, name: string) => refusal("block_field_not_found", `В модели «${model}» нет поля «${name}».`, "Имена полей — во вкладке «Свойства полей» окна «Поля блочной модели».");

/** The field of the model as the core lists it (the list of the window is brought up to date). */
async function fieldOf(modelId: number, name: string): Promise<FieldRow> {
  const model = blockById(modelId);
  if (!model) throw notFound(modelId);
  const list = await loadFields(modelId);
  setProps({ modelId, list });
  const f = list.fields.find((x) => x.name === name) ?? list.fields.find((x) => x.name.toLowerCase() === name.toLowerCase());
  if (!f) throw fieldNotFound(model.name, name);
  return f;
}

const open = (app: App, modelId: number | undefined): string => {
  openFieldsWindow(app, "props", modelId);
  void refreshProps();
  return "Открыто окно «Поля блочной модели»: свойства полей";
};

/** The refusal of a write to a field: told in its place (the row of the list), the words of the core. */
function failRow(name: string, e: unknown, extra = ""): never {
  const r = refusalOf(e);
  const hint = `${r.hint}${extra ? ` ${extra}` : ""}`.trim();
  setIssue(issue("props", rowPlace(name), r.message, hint, r.param));
  throw refusal(r.code, r.message, hint);
}

// ---- the properties ----

const PATCH_KEYS = ["unit", "description", "role"] as const;

/** `blocks.field.info`: the fields of the model (or one field) as the core lists them — for the assistant and the log; nothing is written. */
export async function runFieldInfo(app: App, a: Args, source: CommandSource): Promise<string> {
  if (!isInt(a.model_id)) throw refusal("bad_param", "Не задана блочная модель.", "Передайте model_id — номер модели из раздела «Блочные модели» дерева.");
  const modelId = a.model_id;
  const model = blockById(modelId);
  if (!model) throw notFound(modelId);
  if (source === "user" || windowOfFields()) openFieldsWindow(app, "props", modelId);
  if (given(a, "field")) return fieldLine(await fieldOf(modelId, String(a.field).trim()), model.name);
  const list = await loadFields(modelId);
  setProps({ modelId, list });
  const head = `Модель «${model.name}», версия ${list.version}${list.stale ? `, устарела (${list.staleReason ?? "изменились данные, из которых она получена"})` : ""}: полей ${list.fields.length}`;
  return [head, ...list.fields.map(fieldBrief)].join("\n");
}

export async function runFieldProps(app: App, a: Args): Promise<string> {
  const sent = PATCH_KEYS.filter((k) => a[k] !== undefined);
  if (!isInt(a.model_id) || !given(a, "field") || !sent.length) return open(app, isInt(a.model_id) ? a.model_id : undefined);
  const modelId = a.model_id;
  const name = String(a.field).trim();
  const model = blockById(modelId);
  if (!model) throw notFound(modelId);
  if (app.store.get().projectReadOnly) throw READ_ONLY;
  const patch: FieldPatch = {};
  for (const k of sent) {
    const v = a[k];
    if (v !== null && typeof v !== "string") throw refusal("bad_param", `«${k}»: нужен текст.`, k === "role" ? "Роль — grade, density, frac, code или class; пустая строка снимает роль." : "Пустая строка очищает значение.");
    if (k === "role") patch.role = v === null || v.trim() === "" ? null : v.trim();
    else patch[k] = v === null ? null : v;
  }
  const bad = limitIssue("props", rowPlace(name), String(patch.unit ?? ""), MAX_UNIT, "Единица") ?? limitIssue("props", rowPlace(name), String(patch.description ?? ""), MAX_DESCRIPTION, "Описание");
  if (bad) {
    setIssue(bad);
    throw refusal("bad_param", bad.message, bad.hint);
  }
  try {
    const out = await patchField(modelId, name, blockById(modelId)?.version ?? model.version, patch);
    clearPropsEdit(name);
    await loadBlocks().catch(() => undefined);
    await refreshProps(modelId);
    const f = out.field;
    const what = [sent.includes("unit") ? `единица «${f?.unit ?? "не задана"}»` : null, sent.includes("description") ? `описание «${f?.description ?? "нет"}»` : null, sent.includes("role") ? `роль: ${roleTitle(f?.role ?? null)}` : null];
    return `Поле «${name}» модели «${model.name}» изменено: ${what.filter(Boolean).join(", ")}; версия модели ${out.version}`;
  } catch (e) {
    if (e instanceof CoreError && e.code === "version_conflict") {
      await loadBlocks().catch(() => undefined);
      await refreshProps(modelId);
      return failRow(name, e, "Данные модели обновлены — повторите действие.");
    }
    return failRow(name, e);
  }
}

// ---- «Перестроить» ----

export const rebuildQuestion = (field: string, model: string) => ({
  title: "Перестроить поле",
  message: `Поле «${field}» модели «${model}» будет рассчитано заново по тем же параметрам и записано поверх прежнего. Продолжить?`,
  okLabel: "Перестроить",
  danger: true,
});

export async function runFieldRebuild(app: App, a: Args, source: CommandSource): Promise<string> {
  if (!isInt(a.model_id) || !given(a, "field")) return open(app, isInt(a.model_id) ? a.model_id : undefined);
  const modelId = a.model_id;
  const name = String(a.field).trim();
  const model = blockById(modelId);
  if (!model) throw notFound(modelId);
  if (app.store.get().projectReadOnly) throw READ_ONLY;
  if (source === "user" && !(await askConfirm(rebuildQuestion(name, model.name)))) return "Отменено";
  if (source === "user" || windowOfFields()) openFieldsWindow(app, "props", modelId);
  return runOp(app, { op: "rebuild", tab: "props", modelId, field: name, place: rowPlace(name), replace: false, start: (version) => startRebuild(modelId, name, version) }, source);
}

// ---- the deletion ----

export const deleteQuestion = (field: string, model: string) => ({
  title: "Удалить поле",
  message: `Удалить поле «${field}» из модели «${model}»? Его значения пропадут: вернуть поле можно только пересчётом.`,
  okLabel: "Удалить",
  danger: true,
});

/** The consumers of a field told in our own window. */
async function tellUsed(f: Pick<FieldRow, "name" | "usage">, why: string): Promise<void> {
  await showMessage({ title: "Поле не удалено", message: `${why}${f.usage.length ? `\n\n${f.usage.map(usageLine).join("\n")}\n\nУберите эти ссылки (выберите другое поле или удалите зависимые поля), затем удалите поле.` : ""}` });
}

export async function runFieldDelete(app: App, a: Args, source: CommandSource): Promise<string> {
  if (!isInt(a.model_id) || !given(a, "field")) return open(app, isInt(a.model_id) ? a.model_id : undefined);
  const modelId = a.model_id;
  const name = String(a.field).trim();
  const model = blockById(modelId);
  if (!model) throw notFound(modelId);
  if (app.store.get().projectReadOnly) throw READ_ONLY;
  const f = await fieldOf(modelId, name);
  const why = deleteReason(f);
  if (why) {
    if (source === "user") await tellUsed(f, why);
    const code = f.required ? "field_required" : "field_in_use";
    setIssue(issue("props", rowPlace(f.name), why, "", null));
    throw refusal(code, why, f.required ? "Снимите роль в свойствах поля." : "Удалите или измените зависимые поля и виды.");
  }
  if (source === "user" && !(await askConfirm(deleteQuestion(f.name, model.name)))) return "Отменено";
  try {
    const version = await deleteField(modelId, f.name, blockById(modelId)?.version ?? model.version);
    clearPropsEdit(f.name);
    await loadBlocks().catch(() => undefined);
    await refreshProps(modelId);
    return `Поле «${f.name}» удалено из модели «${model.name}»; версия модели ${version}`;
  } catch (e) {
    const usage = usageOfError(e);
    if (usage.length) {
      // the field began to be used after the list was read: the core says so
      if (source === "user") await tellUsed({ name: f.name, usage }, `Поле «${f.name}» используется: ${usageNames(usage)}.`);
      await refreshProps(modelId);
    }
    if (e instanceof CoreError && e.code === "version_conflict") {
      await loadBlocks().catch(() => undefined);
      await refreshProps(modelId);
      return failRow(f.name, e, "Данные модели обновлены — повторите действие.");
    }
    return failRow(f.name, e);
  }
}
