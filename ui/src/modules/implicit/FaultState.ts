/**
 * The group «Разлом» (Э19б; ТЗ 7.35 Д-24; ADR 0026 решения 19–21; docs/progress/e19-api.md §4, §10): the surface of a fault is its own RBF (the data are the
 * contacts of the fault codes of the table, or the hand layers of strings with the roles contact / inside / outside); a finite fault has the radius
 * of influence R_F, an empty radius is an infinite fault. The domains «рассечена разломом» are set in the same window (`PUT …/groups/{gid}/faults`
 * after the fault is written). The offset of the wings is the result of the build and is shown read only. The window is the frame of the groups
 * (`GroupFrame`); a new fault is `POST /models/{id}/faults` (with the hand layers), an edit is `PUT …/groups/{gid}` (and `PUT /models/{id}` for the layers).
 * The command is `implicit.fault`. No React.
 */
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { refusal } from "../../shell/objectRun";
import { numberText } from "../composites/params";
import { bad, isIssue, numberIn, type Issue } from "../estimation/formBase";
import { refusalOf, replaceGroup, replaceModel, type GroupMeta } from "./api";
import { createGroupKind, detailsOf, freeGroupName, groupIssue, boundsOf, needModel, type GroupCtx, type KindSpec, type Made } from "./groupBase";
import { formOfMeta, type ManualRole, type ModelForm } from "./form";
import { codesOf } from "./VeinState";
import { groupRef, listOf } from "./stackModel";
import { postFault, putGroupFaults, type ManualLayer } from "./stackApi";
import { implicitStore, loadDetails, loadRows } from "./state";
import { ensureDefaults } from "./wizardState";

export interface FaultLayer {
  layerId: number | null;
  role: ManualRole;
}

export interface FaultForm {
  name: string;
  codes: string[];
  /** R_F in metres as typed; empty — an infinite fault */
  radius: string;
  /** the layers of strings with the data of the fault (the manual data of the model whose group is the fault) */
  layers: FaultLayer[];
  /** the domains this fault cuts (the faults of the domain hold its number) */
  domains: number[];
}

export const EMPTY_FAULT: FaultForm = { name: "", codes: [], radius: "", layers: [], domains: [] };

export const ROLE_TITLES: Record<ManualRole, string> = { contact: "контакт", inside: "внутри", outside: "снаружи" };
const ROLE_WORDS: Record<string, ManualRole> = { contact: "contact", контакт: "contact", inside: "inside", внутри: "inside", outside: "outside", снаружи: "outside" };

/** «77:contact; 78:внутри; 79» → the layers (a layer without a role is a contact). */
export function layersOf(text: unknown): FaultLayer[] | Issue {
  const out: FaultLayer[] = [];
  for (const item of listOf(text)) {
    const [id, word] = item.split(":").map((x) => x.trim());
    if (!/^\d+$/.test(id)) return bad("layers", `Слой «${item}»: нужен номер слоя линий.`, "Запишите «номер:роль; номер:роль», роль — contact, inside или outside (по умолчанию contact).");
    const role = word ? ROLE_WORDS[word.toLowerCase()] : "contact";
    if (!role) return bad("layers", `Роль «${word}» не подходит.`, "Допустимо: contact (контакт), inside (внутри), outside (снаружи).");
    out.push({ layerId: Number(id), role });
  }
  return out;
}

export const layersText = (l: readonly FaultLayer[]): string => l.filter((x) => x.layerId !== null).map((x) => `${x.layerId}:${x.role}`).join("; ");

/** The domains of the model: id → group (a fault cuts only a domain). */
const domainsOf = (model: ModelForm): GroupMeta[] => model.groups.flatMap((g) => (g.type === "domain" && g.raw ? [g.raw] : []));

export function faultFromGroup(g: GroupMeta, model: ModelForm): FaultForm {
  const r = g.params.radius;
  return {
    name: g.name,
    codes: g.codes,
    radius: typeof r === "number" && Number.isFinite(r) ? numberText(r) : "",
    layers: model.manual.filter((m) => m.group === g.id).map((m) => ({ layerId: m.layerId, role: m.role })),
    domains: domainsOf(model).filter((d) => g.id !== null && d.faults.includes(g.id)).map((d) => d.id as number),
  };
}

export function faultFromArgs(a: Record<string, unknown>, base: FaultForm, model: ModelForm): FaultForm | Issue {
  const f: FaultForm = { ...base };
  if (typeof a.name === "string") f.name = a.name;
  if (typeof a.codes === "string") f.codes = codesOf(a.codes);
  if (a.layers !== undefined) {
    const l = layersOf(a.layers);
    if (isIssue(l)) return l;
    f.layers = l;
  }
  if (a.radius === null) f.radius = "";
  else if (a.radius !== undefined) f.radius = typeof a.radius === "number" ? numberText(a.radius) : String(a.radius);
  if (a.domains !== undefined) {
    const known = domainsOf(model);
    const ids: number[] = [];
    for (const t of listOf(a.domains)) {
      const id = groupRef(known, t);
      if (id === null) return bad("domains", `Домен «${t}» не найден в модели.`, `Домены модели: ${known.map((d) => `«${d.name}»`).join(", ") || "нет"}. Разлом рассекает только домены литологии; укажите номер или название.`);
      if (!ids.includes(id)) ids.push(id);
    }
    f.domains = ids;
  }
  return f;
}

export function faultMake(f: FaultForm, ctx: GroupCtx): Made {
  const fail = (issue: Issue): Made => ({ ok: false, issue });
  const name = f.name.trim();
  if (!name) return fail(bad("name", "У разлома нет названия.", "Назовите группу, например «Разлом F1»."));
  if (f.codes.length && ctx.model.setId === null) return fail(bad("codes", "В модели не задана таблица кодов.", "Выберите набор, таблицу и поле кода на шаге «Домены литологии» мастера модели, или задайте слои линий."));
  for (let j = 0; j < f.codes.length; j++) if (ctx.taken.has(f.codes[j])) return fail(bad(`codes.${j}`, `Код «${f.codes[j]}» уже есть в другой группе модели.`, "Код входит в одну группу модели: уберите его оттуда или из разлома."));
  for (let i = 0; i < f.layers.length; i++) if (f.layers[i].layerId === null) return fail(bad(`layers.${i}`, `Слой данных ${i + 1}: не выбран слой линий.`, "Выберите слой линий (редактор стрингов) или уберите строку."));
  const layers = f.layers.map((l) => l.layerId);
  if (new Set(layers).size !== layers.length) return fail(bad("layers", "Слой линий выбран два раза.", "Один слой — одна роль: уберите повтор."));
  if (!f.codes.length && !f.layers.length) return fail(bad("codes", "У разлома нет данных: ни кодов, ни слоёв линий.", "Отметьте коды разлома в таблице скважин или добавьте слой линий (контакт разлома — роль «контакт»)."));
  const radius = f.radius.trim() === "" ? null : numberIn(f.radius, boundsOf(ctx.ranges, "fault_radius"), "Радиус влияния R_F", "params.radius");
  if (isIssue(radius)) return fail(radius);
  return { ok: true, group: { name, type: "fault", codes: f.codes, params: { radius } } };
}

export const faultSpec: KindSpec<FaultForm> = {
  kind: "fault",
  window: "implicit-fault",
  command: "implicit.fault",
  title: "Разлом",
  type: "fault",
  nameBase: "Разлом",
  empty: () => EMPTY_FAULT,
  fromGroup: faultFromGroup,
  fromArgs: faultFromArgs,
  make: faultMake,
  complete: (a) => (typeof a.codes === "string" && a.codes.trim() !== "") || (typeof a.layers === "string" && a.layers.trim() !== "") || (typeof a.group_id === "number" && ["name", "radius", "domains"].some((k) => a[k] !== undefined)),
  argsOf: (g) => ({ name: g.name, codes: ((g.codes as string[]) ?? []).join("; "), radius: ((g.params ?? {}) as Record<string, unknown>).radius ?? null }),
  nameOf: (f) => f.name,
  withName: (f, name) => ({ ...f, name }),
};

const base = createGroupKind(faultSpec);

const manualOf = (layers: readonly FaultLayer[]): ManualLayer[] => layers.flatMap((l) => (l.layerId === null ? [] : [{ layer_id: l.layerId, role: l.role }]));

/** `implicit.fault` with values: a new fault (with its hand layers and domains) or the edit of one. */
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
  if (gid !== null && !existing) throw refusal("group_not_found", `В модели «${info.name}» нет такого разлома (№${gid}).`, "Номера групп — в окне «Разлом».");
  const s = base.store.get();
  const own = base.windowId() !== null && s.modelId === id && s.groupId === gid;
  const start = own ? s.form : existing ? faultFromGroup(existing, model) : EMPTY_FAULT;
  const typed = faultFromArgs(a, start, model);
  if (isIssue(typed)) return fail(typed);
  const form = typed.name.trim() ? typed : { ...typed, name: freeGroupName(model.groups.map((g) => g.name), "Разлом") };
  const made = faultMake(form, base.ctxOf(model, gid, defaults));
  if (!made.ok) return fail(made.issue);
  if (own) base.store.set({ saving: true });
  try {
    let version = info.version;
    let fid: number;
    let name: string;
    if (existing) {
      const was = model.manual.filter((m) => m.group === existing.id).map((m) => `${m.layerId}:${m.role}`).sort().join(",");
      const now = manualOf(form.layers).map((m) => `${m.layer_id}:${m.role}`).sort().join(",");
      if (was !== now) {
        // the hand layers belong to the model: the others stay, the layers of this fault are replaced
        const keep = model.manual.filter((m) => m.group !== existing.id && m.layerId !== null).map((m) => ({ layer_id: m.layerId, role: m.role, group: m.group }));
        version = (await replaceModel(id, version, { manual: [...keep, ...manualOf(form.layers).map((m) => ({ ...m, group: existing.id }))] })).version;
      }
      const r = await replaceGroup(id, existing.id as number, version, { id: existing.id, relation: existing.relation, faults: existing.faults, ...made.group });
      version = r.version;
      fid = existing.id as number;
      name = r.group.name;
    } else {
      const r = await postFault(id, version, made.group, manualOf(form.layers));
      version = r.version;
      fid = r.group.id as number;
      name = r.group.name;
    }
    // the domains that are cut by this fault: the faults of a domain are written whole
    let cut = 0;
    for (const d of domainsOf(model)) {
      const have = d.faults.includes(fid);
      const want = form.domains.includes(d.id as number);
      if (have === want) continue;
      const next = want ? [...d.faults, fid] : d.faults.filter((x) => x !== fid);
      version = (await putGroupFaults(id, d.id as number, version, next)).version;
      cut++;
    }
    const text = `Разлом «${name}» ${existing ? "изменён" : "добавлен"} в модели «${info.name}» (версия ${version})${form.domains.length ? `; рассекает доменов: ${form.domains.length}` : cut ? "; доменов не рассекает" : ""}. Чтобы построить крылья и смещение, выполните «Обновить».`;
    if (base.windowId() && base.store.get().modelId === id && (own || !existing)) base.store.set({ groupId: fid, form, dirty: false, issue: null, saved: text });
    await loadRows();
    void loadDetails(id).catch(() => undefined);
    return text;
  } catch (e) {
    const r = refusalOf(e);
    return fail(groupIssue(r));
  } finally {
    base.store.set({ saving: false });
  }
}

async function run(app: App, a: Record<string, unknown>, _source: CommandSource): Promise<string> {
  const id = needModel(app, a.model_id);
  return faultSpec.complete(a) ? save(id, a) : base.open(id, a);
}

/** The buttons of the window: the form is checked, the command writes it (and «Обновить» builds). */
async function submit(app: App, andUpdate: boolean): Promise<void> {
  const s = base.store.get();
  if (s.modelId === null) return;
  const info = implicitStore.get().details[s.modelId];
  if (!info) return;
  const model = formOfMeta(info.name, info.meta);
  const d = (await ensureDefaults()) ?? null;
  const form = s.form.name.trim() ? s.form : { ...s.form, name: freeGroupName(model.groups.map((g) => g.name), "Разлом") };
  const made = faultMake(form, base.ctxOf(model, s.groupId, d));
  if (!made.ok) {
    base.store.set({ issue: made.issue });
    return;
  }
  const radius = (made.group.params as { radius: number | null }).radius;
  const args = { model_id: s.modelId, ...(s.groupId !== null ? { group_id: s.groupId } : {}), name: form.name.trim(), codes: form.codes.join("; "), layers: layersText(form.layers), radius, domains: form.domains.join("; ") };
  const r = await app.registry.execute(faultSpec.command, args, "user");
  if (!r.ok) {
    app.store.set({ statusNote: r.result });
    return;
  }
  if (andUpdate) await app.registry.execute("implicit.update", { model_id: s.modelId }, "user");
}

/** The runtime of the window «Разлом»: the frame's own, but the writes go through `POST /faults` and the faults of the domains. */
export const faults = { ...base, run, save, submit };
