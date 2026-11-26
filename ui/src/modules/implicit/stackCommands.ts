/**
 * The handlers of the commands of Э19б (ТЗ 7.35 Д-24, Д-31, Д-39; ADR 0026 «Команды реестра»; docs/progress/e19-api.md §10, §12):
 * - `implicit.relations {model_id?, order?, relations?}` — the window «Стек отношений», or (with values) `PUT /models/{id}/relations`: `order` — the
 *   domains by age through «;» (the youngest first; a number or a name), `relations` — «группа=отношение» through «;» (`erode`, `onlap`, `intrusion`,
 *   `conformable` or the Russian words);
 * - `implicit.group_faults {model_id?, group_id, fault_ids}` — the faults that cut a domain (`PUT …/groups/{gid}/faults`; empty — none);
 * - `implicit.fault`, `implicit.vein_system` — the windows and the writes of the groups (`FaultState.ts`, `VeinSystemState.ts`).
 * A refusal of the core is the words of the core with its hint (`param` is in the text). No React.
 */
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { needWritable, noted, refusal } from "../../shell/objectRun";
import { refusalOf } from "./api";
import { faults, veinSystems } from "./groupKinds";
import { detailsOf, needModel } from "./groupBase";
import { needModelFor } from "./reasons";
import { RELATION_TITLES, parseRelation, putGroupFaults, putRelations, relationOf, RELATIONS } from "./stackApi";
import { draftOf, groupName, groupRef, groupsOfMeta, listOf, relationsBody } from "./stackModel";
import { openStack, resetStack, stackStore } from "./StackState";
import { loadDetails, loadRows } from "./state";

const fromCore = (e: unknown): never => {
  const r = refusalOf(e);
  throw refusal(r.code, r.message, r.hint);
};

const SOURCE_NOTE = "Чтобы пересчитать поверхности и тела, выполните «Обновить».";

/** `implicit.relations`. */
export async function runRelations(app: App, a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const id = needModel(app, a.model_id);
  if (a.order === undefined && a.relations === undefined) return openStack(id, source);
  const info = await detailsOf(id);
  const groups = groupsOfMeta(info.meta);
  const stack = groups.filter((g) => g.type !== "fault");
  const domains = groups.filter((g) => g.type === "domain");
  const draft = draftOf(info.meta);
  if (a.order !== undefined) {
    const order: number[] = [];
    for (const t of listOf(a.order)) {
      const gid = groupRef(groups, t);
      const g = groups.find((x) => x.id === gid);
      if (gid === null || !g) throw refusal("bad_param", `Группа «${t}» не найдена в модели «${info.name}».`, `Домены по возрасту, от самого молодого: ${domains.map((d) => `«${d.name}» (№${d.id})`).join(", ") || "нет"}. Укажите номер или название.`);
      if (g.type !== "domain") throw refusal("bad_param", `Группа «${g.name}» — не домен литологии.`, "В порядке стека только домены; оболочки, жилы и расширения остаются на своих местах, разломы в стек не входят.");
      if (order.includes(gid)) throw refusal("bad_param", `Группа «${g.name}» названа два раза.`, "Каждый домен — один раз.");
      order.push(gid);
    }
    if (order.length !== domains.length) throw refusal("bad_param", `В порядке названо доменов ${order.length} из ${domains.length}.`, `Назовите все домены по возрасту, от самого молодого: ${domains.map((d) => `«${d.name}»`).join(", ")}.`);
    draft.order = order;
  }
  if (a.relations !== undefined) {
    for (const item of listOf(a.relations)) {
      const at = item.lastIndexOf("=");
      const who = at < 0 ? "" : item.slice(0, at);
      const word = at < 0 ? "" : item.slice(at + 1);
      if (!who.trim() || !word.trim()) throw refusal("bad_param", `Отношение «${item}»: не разобрано.`, "Запишите «группа=отношение» через «;», например «INT=intrusion; SS=erode»; отношения: conformable (Согласно), erode (Эрозия), onlap (Налегание), intrusion (Интрузия).");
      const gid = groupRef(stack, who);
      if (gid === null) throw refusal("bad_param", `Группа «${who.trim()}» не найдена в стеке модели «${info.name}».`, "Укажите номер или название домена; разломы отношений не имеют.");
      const relation = parseRelation(word);
      if (!relation) throw refusal("bad_relations", `Отношение «${word.trim()}» не подходит.`, `Допустимо: ${RELATIONS.map((r) => `${r} (${RELATION_TITLES[r]})`).join(", ")}.`);
      if (stack.find((g) => g.id === gid)?.type === "domain") draft.relations[gid] = relation;
      else if (relation !== "conformable") throw refusal("bad_param", `У группы «${groupName(info.meta, gid)}» отношение «${RELATION_TITLES[relation]}» не задаётся.`, "Отношения стека — у доменов литологии.");
    }
  }
  const body = relationsBody(info.meta, draft);
  try {
    const r = await putRelations(id, info.version, body.order, body.relations);
    await loadRows();
    void loadDetails(id).catch(() => undefined);
    if (source !== "user" && stackStore.get().modelId === id && !stackStore.get().dirty) void resetStack();
    const words = draft.order.map((g, i) => `${i + 1}. ${groupName(info.meta, g)}${i < draft.order.length - 1 ? ` — ${RELATION_TITLES[relationOf(draft.relations[g])]}` : ""}`).join("; ");
    return `Стек модели «${info.name}» записан (версия ${r.version}): ${words}. ${SOURCE_NOTE}`;
  } catch (e) {
    return fromCore(e);
  }
}

/** `implicit.group_faults`. */
export async function runGroupFaults(app: App, a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const id = needModel(app, a.model_id);
  const gid = a.group_id;
  if (typeof gid !== "number") throw refusal("bad_param", "Не указана группа.", "Передайте group_id — номер домена литологии.");
  const info = await detailsOf(id);
  const groups = groupsOfMeta(info.meta);
  const g = groups.find((x) => x.id === gid);
  if (!g) throw refusal("group_not_found", `В модели «${info.name}» нет группы №${gid}.`, "Номера групп — в окне «Стек отношений».");
  if (g.type !== "domain") throw refusal("bad_param", `Группа «${g.name}» — не домен литологии: разлом рассекает только домены.`, "Выберите домен.");
  const faultsOf = groups.filter((x) => x.type === "fault");
  const ids: number[] = [];
  for (const t of listOf(a.fault_ids)) {
    const fid = groupRef(faultsOf, t);
    if (fid === null) throw refusal("bad_param", `Разлом «${t}» не найден в модели «${info.name}».`, `Разломы модели: ${faultsOf.map((f) => `«${f.name}» (№${f.id})`).join(", ") || "нет — добавьте командой «Разлом»"}.`);
    if (!ids.includes(fid)) ids.push(fid);
  }
  try {
    const r = await putGroupFaults(id, gid, info.version, ids);
    await loadRows();
    void loadDetails(id).catch(() => undefined);
    if (source !== "user" && stackStore.get().modelId === id && !stackStore.get().dirty) void resetStack();
    const names = ids.map((f) => `«${groupName(info.meta, f)}»`).join(", ");
    return `${ids.length ? `Домен «${g.name}» рассекается разломами: ${names}` : `Домен «${g.name}» больше не рассекается разломами`} (версия ${r.version}). ${SOURCE_NOTE}`;
  } catch (e) {
    return fromCore(e);
  }
}

/** The four commands of Э19б. */
export function bindStack(app: App): void {
  const reason = needModelFor(app, needWritable(app), "any");
  app.registry.bind("implicit.relations", noted(app, (a, s) => runRelations(app, a, s)), reason);
  app.registry.bind("implicit.group_faults", noted(app, (a, s) => runGroupFaults(app, a, s)), reason);
  app.registry.bind("implicit.fault", noted(app, (a, s) => faults.run(app, a, s)), reason);
  app.registry.bind("implicit.vein_system", noted(app, (a, s) => veinSystems.run(app, a, s)), reason);
}
