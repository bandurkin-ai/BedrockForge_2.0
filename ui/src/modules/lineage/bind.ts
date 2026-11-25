/**
 * The handlers of `lineage.status`, `lineage.impact`, `lineage.rebuild`, `lineage.pin` and the window «Влияние изменения»
 * (Д-35; ADR 0025 §7–10): the object is named by `kind` and `id` (`sub`), else it is the one chosen in the tree. «Перестроить с
 * зависимыми» shows the plan (a dry run) in its own question, starts the job `lineage.rebuild` and follows it to the end (the
 * stages are in «Задания»); a rebuild that loses results or columns asks again. The states of the tree follow the events of the
 * core (`state.ts`). One app at a time (the tests create several).
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { askConfirm } from "../../shell/dialogs";
import { needProject, needWritable, noted, refusal, showJobs } from "../../shell/objectRun";
import type { FloatWin } from "../../shell/windows";
import { getImpact, kindTitle, lostOf, planRebuild, postPin, postStatus, startRebuild, STATE_TITLES, type LNode, type LStatus } from "./api";
import { ImpactWindow } from "./ImpactWindow";
import { IMPACT_WINDOW, openImpact } from "./impactAsk";
import { asRefusal, followRebuild, headLost, planLines, skippedLines } from "./rebuild";
import { followEvents, nodeOfSelected, put, scheduleRefresh, summary } from "./state";

/** The node a command is about: `kind` + `id` (+ `sub`), else the object chosen in the tree. */
export function resolveNode(app: App, a: Record<string, unknown>): LNode | null {
  if (typeof a.kind === "string" && a.kind) {
    if (typeof a.id !== "number" || !Number.isInteger(a.id) || a.id < 0) throw refusal("bad_param", "Не задан номер объекта.", "Передайте id — целое число рядом с kind.");
    return { kind: a.kind, id: a.id, sub: typeof a.sub === "string" ? a.sub : "" };
  }
  if (typeof a.id === "number") throw refusal("bad_param", "Не задан вид объекта.", "Передайте kind: например wireframe, dh_table, object:variogram_model.");
  return nodeOfSelected(app.store.get().selected);
}

const NO_OBJECT = () => refusal("lineage_no_object", "Объект не задан.", "Выберите каркас или блочную модель в дереве либо передайте kind и id.");

function need(app: App, a: Record<string, unknown>): LNode {
  const n = resolveNode(app, a);
  if (!n) throw NO_OBJECT();
  return n;
}

const label = (s: Pick<LStatus, "kind" | "name" | "id">): string => `${kindTitle(s.kind)} «${s.name || `№${s.id}`}»`;

async function statusOf(node: LNode): Promise<LStatus> {
  const [s] = await postStatus([node]).catch((e: unknown) => {
    throw asRefusal(e);
  });
  if (!s || s.state === "deleted") throw refusal("lineage_node_not_found", "Объект не найден.", "Обновите дерево проекта.");
  put(s);
  return s;
}

/** The state in a line: «таблица «Пробы»: устарел. Причина: …». */
export function statusText(s: LStatus): string {
  const word = s.state === "pinned" && s.changed ? "закреплён (входы изменились)" : STATE_TITLES[s.state];
  const why = s.reason ? ` Причина: ${s.reason}.` : "";
  return `${label(s)}: ${word}.${why}`;
}

// ---- lineage.status ----

export async function runStatus(app: App, a: Record<string, unknown>): Promise<string> {
  const node = resolveNode(app, a);
  if (!node) {
    const s = summary();
    if (!s.total) return "В дереве нет расчётных объектов, состояние которых известно: раскройте разделы с результатами или выберите объект.";
    const names = s.stale_names.slice(0, 10).map((n) => `«${n}»`).join(", ");
    const parts = [`Объектов в дереве: ${s.total}, устарели: ${s.stale}${names ? ` (${names}${s.stale > 10 ? " …" : ""})` : ""}`];
    if (s.lost) parts.push(`источник удалён: ${s.lost}`);
    if (s.pinned) parts.push(`закреплены: ${s.pinned}`);
    return `${parts.join("; ")}.`;
  }
  return statusText(await statusOf(node));
}

// ---- lineage.impact ----

export async function runImpact(app: App, a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const node = need(app, a);
  const impact = await getImpact(node).catch((e: unknown) => {
    throw asRefusal(e);
  });
  const s = (await postStatus([node]).catch(() => []))[0];
  const who = s ? label(s) : `${kindTitle(node.kind)} №${node.id}`;
  if (impact.total === 0) return `Из объекта ${who} ничего не построено: изменение никого не затронет.`;
  openImpact(node);
  const kinds = Object.entries(impact.byKind).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${kindTitle(k)}: ${n}`).join("; ");
  const list = source === "claude" ? ` Зависимые: ${impact.nodes.slice(0, 20).map((n) => `${kindTitle(n.kind)} «${n.name}»`).join("; ")}${impact.total > 20 ? " …" : ""}.` : "";
  return `Из объекта ${who} построено объектов: ${impact.total} (${kinds}). Открыто окно «Влияние изменения».${list}`;
}

// ---- lineage.pin ----

export async function runPin(app: App, a: Record<string, unknown>): Promise<string> {
  const node = need(app, a);
  const pinned = typeof a.pinned === "boolean" ? a.pinned : (await statusOf(node)).state !== "pinned";
  const s = await postPin(node, pinned).catch((e: unknown) => {
    throw asRefusal(e);
  });
  put(s);
  if (pinned) return `Объект ${label(s)} закреплён${s.changed ? ": его входы уже изменились, но он не помечен «устарел»" : ""}.`;
  return `Закрепление снято с объекта ${label(s)}: ${STATE_TITLES[s.state]}.${s.reason ? ` Причина: ${s.reason}.` : ""}`;
}

// ---- lineage.rebuild ----

export async function runRebuild(app: App, a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const node = need(app, a);
  const opts = { withDependents: a.with_dependents !== false, dropLost: a.drop_lost === true };
  const plan = await planRebuild(node, opts).catch((e: unknown) => {
    throw asRefusal(e);
  });
  if (headLost(plan)) throw refusal("source_lost", `Источник объекта «${plan.plan[0].name}» удалён.`, "Создайте объект заново из существующих данных.");
  if (source === "user") {
    const left = skippedLines(plan);
    const message = `${planLines(plan).join("\n")}${left.length ? `\n\nНе будут перестроены:\n${left.join("\n")}` : ""}\n\nХод — во вкладке «Задания»; отмена остановит оставшиеся объекты.`;
    if (!(await askConfirm({ title: opts.withDependents ? "Перестроить с зависимыми" : "Перестроить объект", message, okLabel: "Перестроить" }))) return "Отменено";
  }
  let started;
  try {
    started = await startRebuild(node, opts);
  } catch (e) {
    const lost = lostOf(e);
    if (!lost || opts.dropLost) throw asRefusal(e);
    const what = lost.map((l) => `${l.title} «${l.name}»`).join("\n");
    if (source !== "user") throw refusal("dependents_lost", `При перестроении пропадут: ${what.replace(/\n/g, "; ")}.`, "Повторите с drop_lost=true, если согласны на потерю.");
    const ok = await askConfirm({ title: "При перестроении пропадут", message: `${what}\n\nСохраните их заранее или согласитесь на потерю.`, okLabel: "Перестроить и потерять", danger: true });
    if (!ok) return "Отменено";
    started = await startRebuild(node, { ...opts, dropLost: true }).catch((e2: unknown) => {
      throw asRefusal(e2);
    });
  }
  showJobs(app);
  const end = await followRebuild(started.job).catch((e: unknown) => {
    throw asRefusal(e);
  });
  scheduleRefresh();
  if (end.status === "failed") throw refusal("lineage_rebuild_failed", end.text, "Исправьте причину и повторите: готовые объекты не пересчитаются.");
  return end.text;
}

// ---- the registry ----

let unbind: (() => void)[] = [];

export function bindLineage(app: App): void {
  unbind.forEach((fn) => fn());
  unbind = [];
  app.registry.bind("lineage.status", noted(app, (a) => runStatus(app, a)), needProject);
  app.registry.bind("lineage.impact", noted(app, (a, s) => runImpact(app, a, s)), needProject);
  app.registry.bind("lineage.rebuild", noted(app, (a, s) => runRebuild(app, a, s)), needWritable(app));
  app.registry.bind("lineage.pin", noted(app, (a) => runPin(app, a)), needWritable(app));
  unbind.push(followEvents());
}

/** The windows for the shell's window layer (`shell/Shell.tsx`); null — not a window of the graph. */
export function renderLineageWindow(w: FloatWin): ReactNode | null {
  return w.kind === IMPACT_WINDOW ? createElement(ImpactWindow, { winId: w.id }) : null;
}
