/**
 * «Треугольник по клику» of «Свойства» of a wireframe as data (ТЗ 7.21; ADR 0013 §6): the point picked in the
 * 3D window and how the triangle under it lies. The click is answered at once by the level of detail in view
 * (a place on the simplified copy) and then by the core — the exact triangle of the float64 geometry: its
 * point to the millimetre, the dip and the dip direction, its area and its number. Pure.
 */
import type { Fact } from "../../shell/props";
import { coordText, degreesText, type ExactPick } from "../../viz/mesh/exact";
import type { PickedObject } from "../scene/objects";

/** The pick of this wireframe made in the 3D window; null — none. */
export function pickedTriangle(picked: PickedObject | null, wireframeId: number): { xyz: [number, number, number]; exact?: ExactPick | null } | null {
  return picked && picked.kind === "wireframe" && picked.id === wireframeId ? { xyz: picked.xyz, exact: picked.exact } : null;
}

const area = (v: number): string => `${v.toLocaleString("ru-RU", { maximumFractionDigits: v < 10 ? 4 : 2 })} м²`;

/** The point and, once the core has answered, the attitude, the area and the number of the triangle. */
export function pickedFacts(p: { xyz: readonly number[]; exact?: ExactPick | null }): Fact[] {
  const [x, y, z] = p.exact ? p.exact.point : p.xyz;
  const facts: Fact[] = [
    { label: "X", value: coordText(x) },
    { label: "Y", value: coordText(y) },
    { label: "Z", value: coordText(z) },
  ];
  const e = p.exact;
  if (!e) return facts;
  facts.push({ label: "Угол падения", value: e.dip === null ? "—" : degreesText(e.dip) });
  facts.push({ label: "Азимут падения", value: e.dipDirection === null ? (e.dip === null ? "—" : "нет: треугольник горизонтален") : degreesText(e.dipDirection) });
  facts.push({ label: "Площадь треугольника", value: area(e.area) });
  facts.push({ label: "Треугольник №", value: (e.triangle + 1).toLocaleString("ru-RU") });
  return facts;
}

/** What is to be said under the facts; null — nothing (the exact triangle is shown). */
export function pickedNote(p: { exact?: ExactPick | null }): string | null {
  if (p.exact === undefined) return "Точка — по копии для показа; точный треугольник уточняется…";
  if (p.exact === null) return "Точного треугольника под щелчком нет: точка показана по копии для показа.";
  return null;
}
