/**
 * The stack of relations as data (Э19б; ТЗ 7.35 Д-24, Д-31; docs/progress/e19-api.md §4, §10): the domains of a model in the order of their age (the
 * first is the youngest), the relation each of them has to the older ones below it, the faults that cut it; the draft the window edits and the
 * body of `PUT /relations`. The core wants `order` to hold EVERY group of the stack (all but the faults: shells, veins and expansions stay where
 * they are among the domains), so the window shows and moves the domains and the rest keep their places. Pure.
 */
import { obj } from "../estimation/api";
import { readGroup, type GroupMeta } from "./api";
import { RELATION_TITLES, relationOf, type Relation } from "./stackApi";

/** The groups of the model as the core keeps them, in the stored order (`meta.groups`). */
export const groupsOfMeta = (meta: Record<string, unknown>): GroupMeta[] => (Array.isArray(meta.groups) ? meta.groups.map(readGroup) : []);

const withId = (g: GroupMeta): g is GroupMeta & { id: number } => g.id !== null;

/** The domains in the order of their age, the youngest first. */
export const domainsOfMeta = (meta: Record<string, unknown>): (GroupMeta & { id: number })[] => groupsOfMeta(meta).filter((g) => g.type === "domain").filter(withId);

/** The faults of the model (a group of type `fault`). */
export const faultsOfMeta = (meta: Record<string, unknown>): (GroupMeta & { id: number })[] => groupsOfMeta(meta).filter((g) => g.type === "fault").filter(withId);

/** The radius R_F of a fault, m; null — an infinite fault. */
export const radiusOf = (g: Pick<GroupMeta, "params">): number | null => (typeof g.params.radius === "number" && Number.isFinite(g.params.radius) ? g.params.radius : null);

export const radiusText = (r: number | null): string => (r === null ? "бесконечный" : `R_F = ${r.toLocaleString("ru-RU", { maximumFractionDigits: 3 })} м`);

/** The draft of the stack: the order of the domains, the relation of each, the faults that cut each. */
export interface StackDraft {
  order: number[];
  relations: Record<number, Relation>;
  faults: Record<number, number[]>;
}

export function draftOf(meta: Record<string, unknown>): StackDraft {
  const d = domainsOfMeta(meta);
  return {
    order: d.map((g) => g.id),
    relations: Object.fromEntries(d.map((g) => [g.id, relationOf(g.relation)])),
    faults: Object.fromEntries(d.map((g) => [g.id, [...g.faults].sort((a, b) => a - b)])),
  };
}

/** `id` moved by `delta` places (−1 — younger, up the list); a move out of the list changes nothing. */
export function moved(order: readonly number[], id: number, delta: number): number[] {
  const at = order.indexOf(id);
  const to = at + delta;
  if (at < 0 || to < 0 || to >= order.length) return [...order];
  const out = [...order];
  out.splice(at, 1);
  out.splice(to, 0, id);
  return out;
}

/** `id` dropped before `before` (null — to the end, the oldest). */
export function dropped(order: readonly number[], id: number, before: number | null): number[] {
  if (id === before) return [...order];
  const out = order.filter((x) => x !== id);
  const at = before === null ? out.length : out.indexOf(before);
  out.splice(at < 0 ? out.length : at, 0, id);
  return out;
}

/** The order for the core: every group but the faults in the stored order, the domain places taken by the domains of the draft in their order. */
export function fullOrder(meta: Record<string, unknown>, domains: readonly number[]): number[] {
  const queue = [...domains];
  return groupsOfMeta(meta)
    .filter((g) => g.type !== "fault")
    .flatMap((g) => (g.type === "domain" ? (queue.length ? [queue.shift() as number] : []) : g.id !== null ? [g.id] : []));
}

/** The body of `PUT /relations` for a draft. */
export function relationsBody(meta: Record<string, unknown>, d: StackDraft): { order: number[]; relations: Record<string, Relation> } {
  const relations: Record<string, Relation> = {};
  for (const g of groupsOfMeta(meta)) if (g.type !== "fault" && g.id !== null) relations[String(g.id)] = g.type === "domain" ? (d.relations[g.id] ?? relationOf(g.relation)) : relationOf(g.relation);
  return { order: fullOrder(meta, d.order), relations };
}

/** Did the order or a relation change against the model (what `PUT /relations` would write)? */
export function relationsChanged(meta: Record<string, unknown>, d: StackDraft): boolean {
  const base = draftOf(meta);
  return base.order.join(",") !== d.order.join(",") || base.order.some((id) => base.relations[id] !== d.relations[id]);
}

/** The domains whose faults changed against the model: `PUT /groups/{gid}/faults` for each. */
export function faultsChanged(meta: Record<string, unknown>, d: StackDraft): number[] {
  const base = draftOf(meta);
  return base.order.filter((id) => (base.faults[id] ?? []).join(",") !== [...(d.faults[id] ?? [])].sort((a, b) => a - b).join(","));
}

/** One row of the window and of the tree in words: «Эрозия: срезает более старые». */
export function relationLine(relation: Relation, last: boolean): string {
  return last ? "самая старая: отношение не используется" : RELATION_TITLES[relation];
}

/** The words of the model in the stack for the tooltips: «1. INT — Интрузия, рассечена: Разлом». */
export function stackLines(meta: Record<string, unknown>): string[] {
  const d = domainsOfMeta(meta);
  const faults = new Map(faultsOfMeta(meta).map((f) => [f.id, f.name]));
  return d.map((g, i) => {
    const cut = g.faults.map((f) => faults.get(f)).filter((x): x is string => !!x);
    return `${i + 1}. ${g.name} — ${relationLine(relationOf(g.relation), i === d.length - 1)}${cut.length ? `; рассечена: ${cut.join(", ")}` : ""}`;
  });
}

/** The warnings of a draft that are not refusals: a relation that cannot do anything, a stack of one group. */
export function draftNotes(meta: Record<string, unknown>, d: StackDraft): string[] {
  const names = new Map(domainsOfMeta(meta).map((g) => [g.id, g.name]));
  const notes: string[] = [];
  if (d.order.length < 2) notes.push("В стеке меньше двух доменов: отношениям не с кем работать. Добавьте домены литологии.");
  const oldest = d.order[d.order.length - 1];
  if (d.order.length > 1 && oldest !== undefined && d.relations[oldest] !== "conformable") notes.push(`Группа «${names.get(oldest)}» самая старая: её отношение «${RELATION_TITLES[d.relations[oldest]]}» ничего не меняет, ниже неё нет групп.`);
  return notes;
}

/** A group named in a command by its number or its name (any case): its id, else null. */
export function groupRef(groups: readonly GroupMeta[], token: string): number | null {
  const t = token.trim();
  if (!t) return null;
  if (/^\d+$/.test(t)) return groups.some((g) => g.id === Number(t)) ? Number(t) : null;
  const low = t.toLowerCase();
  return groups.find((g) => g.id !== null && g.name.toLowerCase() === low)?.id ?? null;
}

/** «a; b; c» of a text parameter. */
export const listOf = (v: unknown): string[] =>
  String(v ?? "")
    .split(/[;\n]+/)
    .map((x) => x.trim())
    .filter(Boolean);

/** The name of a group of the meta by its id. */
export const groupName = (meta: Record<string, unknown>, id: number): string => groupsOfMeta(meta).find((g) => g.id === id)?.name ?? `№${id}`;

/** The meta of a model as the answer of the core gives it (a guard for a missing key). */
export const metaOf = (info: { meta: unknown } | undefined): Record<string, unknown> => obj(info?.meta);
