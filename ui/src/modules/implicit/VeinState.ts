/**
 * The group «Жила» (ТЗ 8.9.2; ADR 0026 решение 17; docs/progress/e19-api.md §4): the codes of the vein in the table of the model's data; the
 * hanging and the foot wall are the top and the bottom of the intervals of these codes; the plane of the contacts is found by the core
 * (the principal components), the vein pinches out where the thickness is not above t_min; the boundary is the convex hull of the contacts
 * with an offset or a polygon in (u, v); the body is built on the grid (u, v) of the step h of the model. The data of the contract carry no
 * plane (the axes u, v and the contacts in them are the core's), so a polygon cannot be drawn here: the form keeps a stored polygon as it is
 * and offers the convex hull with an offset; a polygon is set by the core's body (`params.polygon`). No React.
 */
import { numberText } from "../composites/params";
import { bad, isIssue, numberIn, type Issue } from "../estimation/formBase";
import type { GroupMeta } from "./api";
import { boundsOf, type KindSpec, type Made } from "./groupBase";

export type VeinBound = "hull" | "polygon";

export interface VeinForm {
  name: string;
  codes: string[];
  bound: VeinBound;
  /** [[u, v], …] of a stored polygon: shown and kept, not drawn */
  polygon: number[][] | null;
  offset: string;
  tMin: string;
}

export const EMPTY_VEIN: VeinForm = { name: "", codes: [], bound: "hull", polygon: null, offset: "", tMin: "" };

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The codes of a command: «Q1; Q2» → ["Q1", "Q2"] (repeats go). */
export function codesOf(textCodes: unknown): string[] {
  const out: string[] = [];
  for (const c of String(textCodes ?? "").split(/[;\n]+/).map((x) => x.trim()).filter(Boolean)) if (!out.includes(c)) out.push(c);
  return out;
}

const polygonOf = (v: unknown): number[][] | null => {
  if (!Array.isArray(v)) return null;
  const pts = v.filter((p): p is number[] => Array.isArray(p) && p.length === 2 && p.every(num));
  return pts.length >= 3 && pts.length === v.length ? pts : null;
};

export function veinFromGroup(g: GroupMeta): VeinForm {
  const p = g.params;
  const polygon = polygonOf(p.polygon);
  return {
    name: g.name,
    codes: g.codes,
    bound: p.bound === "polygon" && polygon ? "polygon" : "hull",
    polygon,
    offset: num(p.offset) && p.offset !== 0 ? numberText(p.offset) : "",
    tMin: num(p.t_min) && p.t_min !== 0 ? numberText(p.t_min) : "",
  };
}

/** The arguments of `implicit.vein` over a base form. */
export function veinFromArgs(a: Record<string, unknown>, base: VeinForm): VeinForm | Issue {
  const f: VeinForm = { ...base };
  if (typeof a.name === "string") f.name = a.name;
  if (typeof a.codes === "string") f.codes = codesOf(a.codes);
  if (a.bound !== undefined) {
    if (a.bound === "polygon" && !f.polygon) return bad("params.polygon", "Полигон границы не задан.", "Полигон в плоскости жилы (u, v) хранится в модели; здесь задайте выпуклую оболочку (bound = hull) и отступ.");
    if (a.bound !== "hull" && a.bound !== "polygon") return bad("params.bound", `Граница: «${String(a.bound)}» не подходит.`, "Допустимо: hull (выпуклая оболочка) или polygon (стоящий в модели полигон).");
    f.bound = a.bound;
  }
  if (a.offset !== undefined) f.offset = typeof a.offset === "number" ? numberText(a.offset) : String(a.offset);
  if (a.t_min !== undefined) f.tMin = typeof a.t_min === "number" ? numberText(a.t_min) : String(a.t_min);
  return f;
}

export function veinMake(f: VeinForm, ctx: Parameters<KindSpec<VeinForm>["make"]>[1]): Made {
  const fail = (issue: Issue): Made => ({ ok: false, issue });
  const name = f.name.trim();
  if (!name) return fail(bad("name", "У жилы нет названия.", "Назовите группу, например «Жила Q1»."));
  if (ctx.model.setId === null) return fail(bad("codes", "В модели не задана таблица кодов.", "Выберите набор, таблицу и поле кода на шаге «Домены литологии» мастера модели."));
  if (!f.codes.length) return fail(bad("codes", "Не выбраны коды жилы.", "Отметьте коды интервалов жилы: по ним берутся кровля и подошва."));
  for (let j = 0; j < f.codes.length; j++) if (ctx.taken.has(f.codes[j])) return fail(bad(`codes.${j}`, `Код «${f.codes[j]}» уже есть в другой группе модели.`, "Код входит в одну группу модели: уберите его оттуда или из жилы."));
  const params: Record<string, unknown> = { bound: f.bound === "polygon" && f.polygon ? "polygon" : "hull" };
  if (params.bound === "polygon") params.polygon = f.polygon;
  const offset = f.offset.trim() === "" ? 0 : numberIn(f.offset, boundsOf(ctx.ranges, "offset"), "Отступ границы", "params.offset");
  if (isIssue(offset)) return fail(offset);
  params.offset = offset;
  const tMin = f.tMin.trim() === "" ? 0 : numberIn(f.tMin, boundsOf(ctx.ranges, "t_min"), "Мощность выклинивания t_min", "params.t_min");
  if (isIssue(tMin)) return fail(tMin);
  params.t_min = tMin;
  return { ok: true, group: { name, type: "vein", codes: f.codes, params } };
}

export function veinArgs(group: Record<string, unknown>): Record<string, unknown> {
  const p = (group.params ?? {}) as Record<string, unknown>;
  return { name: group.name, codes: (group.codes as string[]).join("; "), bound: p.bound, offset: p.offset, t_min: p.t_min };
}

export const veinSpec: KindSpec<VeinForm> = {
  kind: "vein",
  window: "implicit-vein",
  command: "implicit.vein",
  title: "Жила",
  type: "vein",
  nameBase: "Жила",
  empty: () => EMPTY_VEIN,
  fromGroup: (g) => veinFromGroup(g),
  fromArgs: (a, base) => veinFromArgs(a, base),
  make: veinMake,
  complete: (a) => typeof a.codes === "string" && a.codes.trim() !== "",
  argsOf: veinArgs,
  nameOf: (f) => f.name,
  withName: (f, name) => ({ ...f, name }),
};
