/**
 * The group «Система жил» (Э19б; ТЗ 7.35 Д-39; ADR 0026 решения 15, 22; docs/progress/e19-api.md §4, §10): 2–20 veins of one model, each with its own code in the
 * table of intervals, its own boundary and its t_min. The ORDER of the list is the age: the first vein is the youngest, and the younger vein cuts the
 * older one (the body of the older minus the body of the younger). The result is the body of each vein, the common body (the union), the lines of
 * intersection (layers of strings) and the table of the pairs (`stats.vein_pairs`: the length of the line, the volume of the common zone before the
 * cut). A new system is `POST /models/{id}/vein_system`, an edit — `PUT …/groups/{gid}`. The command is `implicit.vein_system`. No React.
 */
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { refusal } from "../../shell/objectRun";
import { numberText } from "../composites/params";
import { bad, isIssue, numberIn, type Issue } from "../estimation/formBase";
import { refusalOf, replaceGroup, type GroupMeta } from "./api";
import { boundsOf, createGroupKind, detailsOf, freeGroupName, groupIssue, needModel, type GroupCtx, type KindSpec, type Made } from "./groupBase";
import { formOfMeta, type ModelForm } from "./form";
import { listOf } from "./stackModel";
import { postVeinSystem } from "./stackApi";
import { implicitStore, loadDetails, loadRows } from "./state";
import type { VeinBound } from "./VeinState";
import { ensureDefaults } from "./wizardState";

/** The limit of the veins of a system (`limits.veins` of the core). */
export const VEINS_MAX = 20;
export const VEINS_MIN = 2;

export interface VeinRow {
  code: string;
  /** the name of the vein's body; empty — «Жила <код>» */
  name: string;
  bound: VeinBound;
  /** [[u, v], …] of a stored polygon: shown and kept, not drawn */
  polygon: number[][] | null;
  offset: string;
  tMin: string;
}

export interface VeinSystemForm {
  name: string;
  /** the first is the youngest: the younger vein cuts the older one */
  veins: VeinRow[];
}

export const EMPTY_SYSTEM: VeinSystemForm = { name: "", veins: [] };

export const newVein = (code: string): VeinRow => ({ code, name: "", bound: "hull", polygon: null, offset: "", tMin: "" });
export const veinName = (v: Pick<VeinRow, "code" | "name">): string => v.name.trim() || `Жила ${v.code}`;

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

const polygonOf = (v: unknown): number[][] | null => {
  if (!Array.isArray(v)) return null;
  const pts = v.filter((p): p is number[] => Array.isArray(p) && p.length === 2 && p.every(num));
  return pts.length >= 3 && pts.length === v.length ? pts : null;
};

/** The veins of a stored system (`params.veins`). */
export function veinRowsOf(g: Pick<GroupMeta, "params">): VeinRow[] {
  const given = Array.isArray(g.params.veins) ? g.params.veins : [];
  return given.map((raw) => {
    const v = (raw ?? {}) as Record<string, unknown>;
    const polygon = polygonOf(v.polygon);
    const code = String(v.code ?? "");
    return {
      code,
      name: typeof v.name === "string" && v.name !== code && v.name !== `Жила ${code}` ? v.name : "",
      bound: v.bound === "polygon" && polygon ? "polygon" : "hull",
      polygon,
      offset: num(v.offset) && v.offset !== 0 ? numberText(v.offset) : "",
      tMin: num(v.t_min) && v.t_min !== 0 ? numberText(v.t_min) : "",
    };
  });
}

export const systemFromGroup = (g: GroupMeta): VeinSystemForm => ({ name: g.name, veins: veinRowsOf(g) });

/** «VN; VN2» (the order of the list is the age) or «VN:2; VN2:1» (the number is the age: 1 — the youngest). */
export function veinsOfText(text: unknown, base: readonly VeinRow[]): VeinRow[] | Issue {
  const items = listOf(text).map((t, i) => {
    const [code, n] = t.split(":").map((x) => x.trim());
    return { code, age: n === undefined ? i + 1 : Number(n), i, bad: !code || (n !== undefined && !/^\d+$/.test(n)) };
  });
  const wrong = items.find((x) => x.bad);
  if (wrong) return bad("params.veins", `Жила «${listOf(text)[wrong.i]}»: не разобрано.`, "Запишите коды жил по возрасту через «;» (первая — самая молодая), например «VN; VN2», или «VN:1; VN2:2».");
  const sorted = [...items].sort((a, b) => a.age - b.age || a.i - b.i);
  const seen = new Set<string>();
  for (const x of sorted) {
    if (seen.has(x.code)) return bad("params.veins", `Код «${x.code}» повторяется.`, "У каждой жилы свой код.");
    seen.add(x.code);
  }
  return sorted.map((x) => base.find((b) => b.code === x.code) ?? newVein(x.code));
}

export function systemFromArgs(a: Record<string, unknown>, base: VeinSystemForm): VeinSystemForm | Issue {
  const f: VeinSystemForm = { ...base, veins: base.veins.map((v) => ({ ...v })) };
  if (typeof a.name === "string") f.name = a.name;
  if (typeof a.veins === "string") {
    const v = veinsOfText(a.veins, base.veins);
    if (isIssue(v)) return v;
    f.veins = v;
  }
  if (a.bound !== undefined && a.bound !== "hull") return bad("params.bound", `Граница «${String(a.bound)}» не подходит.`, "Командой задаётся выпуклая оболочка (hull); полигон хранится в модели и сохраняется при правке.");
  if (a.offset !== undefined) for (const v of f.veins) v.offset = typeof a.offset === "number" ? numberText(a.offset) : String(a.offset);
  if (a.t_min !== undefined) for (const v of f.veins) v.tMin = typeof a.t_min === "number" ? numberText(a.t_min) : String(a.t_min);
  return f;
}

export function systemMake(f: VeinSystemForm, ctx: GroupCtx): Made {
  const fail = (issue: Issue): Made => ({ ok: false, issue });
  const name = f.name.trim();
  if (!name) return fail(bad("name", "У системы жил нет названия.", "Назовите группу, например «Жилы Q»."));
  if (ctx.model.setId === null) return fail(bad("params.veins", "В модели не задана таблица кодов.", "Выберите набор, таблицу и поле кода на шаге «Домены литологии» мастера модели."));
  if (f.veins.length < VEINS_MIN) return fail(bad("params.veins", "В системе жил нужно не меньше двух жил.", "Добавьте жилы: у каждой свой код в таблице интервалов."));
  if (f.veins.length > VEINS_MAX) return fail(bad("params.veins", `В системе жил больше ${VEINS_MAX} жил.`, "Уберите лишние жилы или разбейте систему на две."));
  const names = new Set<string>();
  const veins: Record<string, unknown>[] = [];
  for (let i = 0; i < f.veins.length; i++) {
    const v = f.veins[i];
    const at = `params.veins.${i}`;
    if (!v.code.trim()) return fail(bad(`${at}.code`, `Жила ${i + 1}: не выбран код.`, "Выберите код жилы из таблицы интервалов."));
    if (ctx.taken.has(v.code)) return fail(bad(`${at}.code`, `Код «${v.code}» уже есть в другой группе модели.`, "Код входит в одну группу модели: уберите его оттуда или из системы."));
    if (veins.some((x) => x.code === v.code)) return fail(bad(`${at}.code`, `Код «${v.code}» повторяется.`, "У каждой жилы свой код."));
    const vn = veinName(v);
    if (names.has(vn)) return fail(bad(`${at}.name`, `Название «${vn}» повторяется.`, "У тела каждой жилы своё название."));
    names.add(vn);
    const offset = v.offset.trim() === "" ? 0 : numberIn(v.offset, boundsOf(ctx.ranges, "offset"), `Жила «${v.code}»: отступ границы`, `${at}.offset`);
    if (isIssue(offset)) return fail(offset);
    const tMin = v.tMin.trim() === "" ? 0 : numberIn(v.tMin, boundsOf(ctx.ranges, "t_min"), `Жила «${v.code}»: мощность выклинивания t_min`, `${at}.t_min`);
    if (isIssue(tMin)) return fail(tMin);
    const polygon = v.bound === "polygon" && v.polygon;
    veins.push({ code: v.code, name: vn, bound: polygon ? "polygon" : "hull", ...(polygon ? { polygon: v.polygon } : {}), offset, t_min: tMin });
  }
  return { ok: true, group: { name, type: "vein_system", params: { veins } } };
}

export const systemSpec: KindSpec<VeinSystemForm> = {
  kind: "vein_system",
  window: "implicit-vein-system",
  command: "implicit.vein_system",
  title: "Система жил",
  type: "vein_system",
  nameBase: "Жилы",
  empty: () => EMPTY_SYSTEM,
  fromGroup: (g: GroupMeta, _m: ModelForm) => systemFromGroup(g),
  fromArgs: (a, base) => systemFromArgs(a, base),
  make: systemMake,
  complete: (a) => typeof a.veins === "string" && a.veins.trim() !== "",
  argsOf: (g) => {
    const veins = (((g.params ?? {}) as { veins?: { code: string }[] }).veins ?? []).map((v) => v.code);
    return { name: g.name, veins: veins.join("; ") };
  },
  nameOf: (f) => f.name,
  withName: (f, name) => ({ ...f, name }),
};

const base = createGroupKind(systemSpec);

/** `implicit.vein_system` with values: a new system or the edit of one (`group_id`). */
async function save(id: number, a: Record<string, unknown>): Promise<string> {
  const info = await detailsOf(id);
  const defaults = await ensureDefaults();
  const model = formOfMeta(info.name, info.meta);
  const gid = typeof a.group_id === "number" ? a.group_id : null;
  const existing = gid === null ? null : (base.groupsOf(info).find((g) => g.id === gid) ?? null);
  const fail = (issue: Issue): never => {
    if (base.windowId() && base.store.get().modelId === id) base.store.set({ issue });
    throw refusal(issue.code, issue.message, issue.hint);
  };
  if (gid !== null && !existing) throw refusal("group_not_found", `В модели «${info.name}» нет такой системы жил (№${gid}).`, "Номера групп — в окне «Система жил».");
  const s = base.store.get();
  const own = base.windowId() !== null && s.modelId === id && s.groupId === gid;
  const start = own ? s.form : existing ? systemFromGroup(existing) : EMPTY_SYSTEM;
  const typed = systemFromArgs(a, start);
  if (isIssue(typed)) return fail(typed);
  const form = typed.name.trim() ? typed : { ...typed, name: freeGroupName(model.groups.map((g) => g.name), "Жилы") };
  const made = systemMake(form, base.ctxOf(model, gid, defaults));
  if (!made.ok) return fail(made.issue);
  if (own) base.store.set({ saving: true });
  try {
    const r = existing ? await replaceGroup(id, existing.id as number, info.version, { id: existing.id, relation: existing.relation, faults: existing.faults, ...made.group }) : await postVeinSystem(id, info.version, made.group);
    const text = `Система жил «${r.group.name}» (жил: ${form.veins.length}; первая — самая молодая) ${existing ? "изменена в модели" : "добавлена в модель"} «${info.name}» (версия ${r.version}). Чтобы построить тела, общее тело и линии пересечения, выполните «Обновить».`;
    if (base.windowId() && base.store.get().modelId === id && (own || !existing)) base.store.set({ groupId: r.group.id, form, dirty: false, issue: null, saved: text });
    await loadRows();
    void loadDetails(id).catch(() => undefined);
    return text;
  } catch (e) {
    return fail(groupIssue(refusalOf(e)));
  } finally {
    base.store.set({ saving: false });
  }
}

async function run(app: App, a: Record<string, unknown>, _source: CommandSource): Promise<string> {
  const id = needModel(app, a.model_id);
  return systemSpec.complete(a) ? save(id, a) : base.open(id, a);
}

/** The buttons of the window: the form is checked, the command writes it (and «Обновить» builds). */
async function submit(app: App, andUpdate: boolean): Promise<void> {
  const s = base.store.get();
  if (s.modelId === null) return;
  const info = implicitStore.get().details[s.modelId];
  if (!info) return;
  const model = formOfMeta(info.name, info.meta);
  const d = (await ensureDefaults()) ?? null;
  const form = s.form.name.trim() ? s.form : { ...s.form, name: freeGroupName(model.groups.map((g) => g.name), "Жилы") };
  const made = systemMake(form, base.ctxOf(model, s.groupId, d));
  if (!made.ok) {
    base.store.set({ issue: made.issue });
    return;
  }
  // the parameters of each vein are those of the window: the command takes the list, the rest stays in the form of the open window
  const r = await app.registry.execute(systemSpec.command, { model_id: s.modelId, ...(s.groupId !== null ? { group_id: s.groupId } : {}), name: form.name.trim(), veins: form.veins.map((v) => v.code).join("; ") }, "user");
  if (!r.ok) {
    app.store.set({ statusNote: r.result });
    return;
  }
  if (andUpdate) await app.registry.execute("implicit.update", { model_id: s.modelId }, "user");
}

/** The runtime of the window «Система жил». */
export const veinSystems = { ...base, run, save, submit };
