/**
 * Attributes of the user of a wireframe or a DTM as data (ТЗ 7.20 «Атрибуты»: add, change, rename, delete;
 * ADR 0011 §3 `PATCH /wireframes/{id}/attrs`). A value is a text, a number, yes/no or empty; an empty value
 * stays empty — never 0. Names are compared without case, as the core does. Pure.
 */
import { extentFacts, type Fact } from "../../shell/props";
import { count } from "../../shell/treeActions";
import { parseNumber } from "../importing/objects";
import type { AttrsPatch, WireframeAttr, WireframeInfo } from "./state";

export type AttrValue = WireframeAttr["value"];

/** The value as the text of its field: empty — "", «да»/«нет», a number with a decimal comma. */
export function attrValueText(v: AttrValue | undefined): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? "да" : "нет";
  if (typeof v === "number") return Number.isFinite(v) ? v.toLocaleString("ru-RU", { maximumFractionDigits: 10, useGrouping: false }).replace("-", "−") : "";
  return v;
}

/** The text of a field as a value: empty — null, «да»/«нет» — yes/no, «12,5» — a number, the rest — a text. */
export function parseAttrValue(text: string): AttrValue {
  const t = text.trim();
  if (!t) return null;
  const low = t.toLowerCase();
  if (low === "да") return true;
  if (low === "нет") return false;
  const n = parseNumber(t);
  return typeof n === "number" ? n : t;
}

const key = (name: string): string => name.trim().replace(/\s+/g, " ").toLowerCase();

export const findAttr = (attrs: readonly WireframeAttr[], name: string): WireframeAttr | undefined => attrs.find((a) => key(a.name) === key(name));

/** Why a name cannot be given to an attribute; null — it can. `except` — the attribute being renamed. */
export function attrNameError(name: string, attrs: readonly WireframeAttr[], except?: string): string | null {
  if (!key(name)) return "У атрибута должно быть название: введите его.";
  const taken = findAttr(attrs, name);
  if (taken && (except === undefined || key(taken.name) !== key(except))) return `Атрибут «${taken.name}» уже есть: введите другое название.`;
  return null;
}

/** One change of `wireframe.attr` as the body of the request. */
export function attrPatch(a: { name: string; value?: AttrValue; rename?: string; remove?: boolean }): AttrsPatch {
  if (a.remove) return { remove: [a.name] };
  const patch: AttrsPatch = {};
  if (a.rename !== undefined) patch.rename = { [a.name]: a.rename };
  if (a.value !== undefined) patch.set = { [a.rename ?? a.name]: a.value };
  return patch;
}

/** «Атрибут «Блок» = 12,5» · «… переименован в «Участок»» · «… удалён» */
export function attrResult(a: { name: string; value?: AttrValue; rename?: string; remove?: boolean }): string {
  if (a.remove) return `Атрибут «${a.name}» удалён`;
  const name = a.rename ?? a.name;
  const renamed = a.rename !== undefined ? `Атрибут «${a.name}» переименован в «${a.rename}»` : null;
  if (a.value === undefined) return renamed ?? `Атрибут «${name}» не изменён`;
  const value = a.value === null ? "пусто" : attrValueText(a.value);
  return renamed ? `${renamed}, значение — ${value}` : `Атрибут «${name}» = ${value}`;
}

/** What «Свойства» tell about a wireframe or a DTM. */
export function wireframeFacts(w: Pick<WireframeInfo, "triangle_count" | "point_count" | "extent">): Fact[] {
  return [{ label: "Треугольников", value: count(w.triangle_count) }, { label: "Вершин", value: count(w.point_count) }, ...extentFacts(w.extent)];
}
