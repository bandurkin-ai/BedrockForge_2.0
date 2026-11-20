/** «Кодировать каркасами» (ТЗ 8.2.2; ADR 0018 §6): the samples of a table coded by closed wireframes — a new table
 * «‹Таблица› — коды». The bodies go in the order of priority (an overlap takes the first); an open body cannot be
 * chosen and says why (`stats` of `GET /wireframes`: open and non-manifold edges); with the division (8.2.2: «да»
 * by default) the samples are cut at the points of entry and exit, without it — the code by the middle and the
 * share of the length inside (INSIDE_FRAC). Pure. */
import { api } from "../../../shell/api";
import type { ToolSpec } from "../params";

export const coding: ToolSpec = {
  id: "coding",
  command: "composite.code",
  title: "Кодировать каркасами",
  endpoint: "/coding",
  preview: () => "coding",
  source: { table: "one", fields: "none", domain: false, wireframes: true },
  params: [
    { key: "split", label: "Делить пробы на границе каркаса", kind: "boolean", default: true },
    { key: "name", label: "Имя таблицы", kind: "text", optional: true, hint: "пусто — «‹Таблица› — коды»" },
  ],
  required: ["set_id", "table", "wireframe_ids"],
  writes: "table",
  note: "Только замкнутые каркасы; при перекрытии тел код берётся по порядку списка.",
};

/** A wireframe as the window lists it: is it a closed solid, and if not — why. */
export interface CodingBody {
  id: number;
  name: string;
  folder: string | null;
  /** true — closed; false — open (see `reason`); null — not checked (the core checks it at the start) */
  closed: boolean | null;
  /** «незамкнут: 12 открытых рёбер», «поверхность ЦТМ — не тело»; null — can be chosen */
  reason: string | null;
}

interface RawWireframe {
  id: number;
  name: string;
  folder?: string | null;
  dtm?: unknown;
  stats?: { closed?: unknown; open_edges?: unknown; nonmanifold_edges?: unknown } | null;
}

const plural = (n: number, one: string, few: string, many: string): string => {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
};

/** Why a wireframe cannot code samples (ADR 0018 §6: 422 `wireframe_open` otherwise); null — it can. */
export function bodyReason(w: RawWireframe): { closed: boolean | null; reason: string | null } {
  if (w.dtm !== null && w.dtm !== undefined) return { closed: false, reason: "поверхность ЦТМ — не тело" };
  const st = w.stats ?? {};
  if (st.closed === true) return { closed: true, reason: null };
  if (st.closed !== false) return { closed: null, reason: null };
  const open = typeof st.open_edges === "number" ? st.open_edges : 0;
  const nm = typeof st.nonmanifold_edges === "number" ? st.nonmanifold_edges : 0;
  const parts = [open ? `${open} ${plural(open, "открытое ребро", "открытых ребра", "открытых рёбер")}` : "", nm ? `${nm} ${plural(nm, "неманифолдное ребро", "неманифолдных ребра", "неманифолдных рёбер")}` : ""].filter(Boolean);
  return { closed: false, reason: `незамкнут${parts.length ? `: ${parts.join(", ")}` : ""} — «Проверить каркас» и исправить` };
}

export function toBodies(raw: readonly RawWireframe[]): CodingBody[] {
  return raw.map((w) => ({ id: w.id, name: w.name, folder: w.folder ?? null, ...bodyReason(w) }));
}

export async function loadBodies(): Promise<CodingBody[]> {
  return toBodies((await api.get<{ wireframes: RawWireframe[] }>("/wireframes")).wireframes ?? []);
}

/** The chosen bodies first in the order of priority, then the others in the order of the project. */
export function orderBodies(bodies: readonly CodingBody[], chosen: readonly number[]): CodingBody[] {
  const first = chosen.map((id) => bodies.find((b) => b.id === id)).filter((b): b is CodingBody => b !== undefined);
  return [...first, ...bodies.filter((b) => !chosen.includes(b.id))];
}

/** The id moved by `delta` places in the order of priority (the ends stay). */
export function moveId(ids: readonly number[], id: number, delta: -1 | 1): number[] {
  const i = ids.indexOf(id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= ids.length) return [...ids];
  const out = [...ids];
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

/** A chosen body that cannot code samples blocks «Применить»: the reason; null — all good. */
export function chosenProblem(bodies: readonly CodingBody[], chosen: readonly number[]): string | null {
  for (const id of chosen) {
    const b = bodies.find((x) => x.id === id);
    if (!b) return `Каркас №${id} не найден в проекте — снимите отметку`;
    if (b.reason) return `Каркас «${b.name}»: ${b.reason}`;
  }
  return null;
}

/** «Пробы — коды» (ADR 0018 §2). */
export const codingName = (table: string | null): string => `${table ?? "‹Таблица›"} — коды`;
