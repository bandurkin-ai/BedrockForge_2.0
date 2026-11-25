/**
 * The group «Расширение каркаса» (ТЗ 8.9.2; ADR 0026 решение 18; docs/progress/e19-api.md §4): the surface at the distance Δ from a closed
 * wireframe — the field d − Δ, d the signed distance to the wireframe (negative inside); Δ > 0 grows the body, Δ < 0 shrinks it. Δ is not 0,
 * −1000…1000 m. The closed wireframes are those with `stats.closed` (an open one has no inside). No React.
 */
import { numberText } from "../composites/params";
import { bad, isIssue, numberIn, type Issue } from "../estimation/formBase";
import { sceneObjects } from "../scene/objects";
import type { GroupMeta } from "./api";
import { boundsOf, type KindSpec, type Made } from "./groupBase";

export interface ExpandForm {
  name: string;
  wireframeId: number | null;
  delta: string;
}

export const EMPTY_EXPAND: ExpandForm = { name: "", wireframeId: null, delta: "" };

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The wireframes that are known to be open (the core refuses them too; here the words are under the field before the request). */
export type Closed = (id: number) => boolean | null;

export function expandFromGroup(g: GroupMeta): ExpandForm {
  const p = g.params;
  return { name: g.name, wireframeId: num(p.wireframe_id) ? p.wireframe_id : null, delta: num(p.delta) ? numberText(p.delta) : "" };
}

export function expandFromArgs(a: Record<string, unknown>, base: ExpandForm): ExpandForm | Issue {
  const f: ExpandForm = { ...base };
  if (typeof a.name === "string") f.name = a.name;
  if (a.wireframe_id !== undefined) f.wireframeId = num(a.wireframe_id) ? a.wireframe_id : null;
  if (a.delta !== undefined) f.delta = typeof a.delta === "number" ? numberText(a.delta) : String(a.delta);
  return f;
}

/** The words under the field for Δ: «Расширение на 5 м» / «Сжатие на 2,5 м»; null — not a number. */
export function deltaWords(delta: string): string | null {
  const n = Number(delta.trim().replace(/\s+/g, "").replace(",", "."));
  if (delta.trim() === "" || !Number.isFinite(n) || n === 0) return null;
  return `${n > 0 ? "Расширение" : "Сжатие"} на ${numberText(Math.abs(n))} м`;
}

export function expandMake(f: ExpandForm, ctx: Parameters<KindSpec<ExpandForm>["make"]>[1], closed: Closed = () => null): Made {
  const fail = (issue: Issue): Made => ({ ok: false, issue });
  const name = f.name.trim();
  if (!name) return fail(bad("name", "У расширения нет названия.", "Назовите группу, например «Рудное тело +5 м»."));
  if (f.wireframeId === null) return fail(bad("params.wireframe_id", "Не выбран каркас.", "Выберите замкнутый каркас, который нужно расширить или сжать."));
  if (closed(f.wireframeId) === false) return fail(bad("params.wireframe_id", "Каркас не замкнут: внутренность не определена.", "Запустите «Исправить каркас» и повторите.", "wireframe_open"));
  const delta = numberIn(f.delta, boundsOf(ctx.ranges, "expand"), "Смещение Δ", "params.delta");
  if (isIssue(delta)) return fail(delta);
  if (delta === 0) return fail(bad("params.delta", "Смещение Δ равно нулю: поверхность совпала бы с каркасом.", "Задайте Δ не равное нулю: положительное расширяет, отрицательное сжимает."));
  return { ok: true, group: { name, type: "expand", codes: [], params: { wireframe_id: f.wireframeId, delta } } };
}

export function expandArgs(group: Record<string, unknown>): Record<string, unknown> {
  const p = (group.params ?? {}) as Record<string, unknown>;
  return { name: group.name, wireframe_id: p.wireframe_id, delta: p.delta };
}

export const expandSpec: KindSpec<ExpandForm> = {
  kind: "expand",
  window: "implicit-expand",
  command: "implicit.expand",
  title: "Расширение каркаса",
  type: "expand",
  nameBase: "Расширение",
  empty: () => EMPTY_EXPAND,
  fromGroup: (g) => expandFromGroup(g),
  fromArgs: (a, base) => expandFromArgs(a, base),
  make: (f, ctx) => expandMake(f, ctx, wireframeClosed),
  complete: (a) => typeof a.wireframe_id === "number" && a.delta !== undefined && a.delta !== null,
  argsOf: expandArgs,
  nameOf: (f) => f.name,
  withName: (f, name) => ({ ...f, name }),
};

/** Is the wireframe closed by what the tree knows (`stats.closed`); null — not known (the core decides). */
export function wireframeClosed(id: number): boolean | null {
  const w = sceneObjects.get().wireframes.find((x) => x.id === id);
  return w?.stats?.closed === undefined ? null : w.stats.closed;
}
