/**
 * What «Свойства» of the window «Блочная модель · …» read of `GET /blocks/{id}` of the core (meta v2, e17-api §2, §4): the
 * turn of the model (φ about +Z), the division of a parent into sub-blocks (`div`, `levels`), the rows and the parents,
 * the roles and descriptions of the fields, the findings of the checks of the import, a model that is out of date. The
 * answer is read as it comes — a v1 model has none of the new keys: nothing is made up for them (no turn is «0°» only
 * when the core said so; a missing check is «ещё не выполнялись», never «без замечаний»). Pure: no React.
 */
import { num, type Fact } from "../../../shell/props";
import { count } from "../../../shell/treeActions";
import type { BlockModelInfo } from "../api";
import { gridFacts, isFreeDiv } from "../props";

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

export interface CheckExample {
  check: string;
  row: number;
  p: number | null;
  other: number | null;
}

export interface Checks {
  at: string | null;
  duplicates: number | null;
  overlaps: { n: number; method: string } | null;
  empty: string[];
  constant: string[];
  volume: number | null;
  examples: CheckExample[];
}

export interface FieldMeta {
  name: string;
  role: string | null;
  description: string | null;
  dtype: string | null;
}

export interface Details {
  /** degrees about +Z; null — the core did not say */
  rotation: number | null;
  div: [number, number, number] | null;
  levels: [number, number, number] | null;
  rows: number | null;
  parents: number | null;
  sub: boolean;
  full: boolean;
  stale: boolean;
  staleReason: string | null;
  /** null — not checked yet */
  checks: Checks | null;
  fields: FieldMeta[];
}

const triple = (v: unknown): [number, number, number] | null => (Array.isArray(v) && v.length === 3 && v.every(isNum) ? [v[0], v[1], v[2]] : null);

function readChecks(raw: unknown): Checks | null {
  if (!isObj(raw)) return null;
  const o = isObj(raw.overlaps) && isNum(raw.overlaps.n) ? { n: raw.overlaps.n, method: str(raw.overlaps.method) ?? "" } : null;
  const examples = Array.isArray(raw.examples)
    ? raw.examples.filter(isObj).map((e) => ({ check: str(e.check) ?? "", row: isNum(e.row) ? e.row : -1, p: isNum(e.p) ? e.p : null, other: isNum(e.other_row) ? e.other_row : null }))
    : [];
  return { at: str(raw.at), duplicates: isNum(raw.duplicates) ? raw.duplicates : null, overlaps: o, empty: strings(raw.empty), constant: strings(raw.constant), volume: isNum(raw.volume) ? raw.volume : null, examples };
}

/** The details of a model as the core sent them; anything else is «not said». */
export function readDetails(raw: unknown): Details {
  const o = isObj(raw) ? raw : {};
  const g = isObj(o.grid) ? o.grid : {};
  return {
    rotation: isNum(g.rotation) ? g.rotation : null,
    div: triple(g.div),
    levels: triple(g.levels),
    rows: isNum(o.rows) ? o.rows : isNum(o.cells) ? o.cells : null,
    parents: isNum(o.parents) ? o.parents : null,
    sub: o.sub === true,
    full: o.full === true,
    stale: o.stale === true,
    staleReason: str(o.stale_reason),
    checks: readChecks(o.checks),
    fields: Array.isArray(o.fields)
      ? o.fields.filter(isObj).map((f) => ({ name: str(f.name) ?? "", role: str(f.role), description: str(f.description), dtype: str(f.dtype) }))
      : [],
  };
}

/** The roles of the fields (8.7.5; e17-api §2) as the interface says them. */
export const ROLE_TEXT: Record<string, string> = { grade: "содержание", density: "плотность", frac: "доля блока", code: "код", class: "класс" };
export const roleText = (role: string | null): string => (role ? (ROLE_TEXT[role] ?? role) : "—");

/** «Поворот 30° против часовой стрелки при виде сверху» — or «нет поворота» when the core said 0. */
export function rotationText(d: Details): string {
  if (d.rotation === null) return "не указан";
  if (d.rotation === 0) return "нет (оси модели вдоль осей проекта)";
  return `${num(d.rotation, 6)}° вокруг оси Z, против часовой стрелки при виде сверху`;
}

/** «2 × 2 × 2 подъячеек в блоке» — the parent block is split into sub-blocks (`div`), or «нет». */
export function subText(d: Details): string {
  if (!d.sub || !d.div) return d.div && d.div.some((v) => v > 1) ? `возможны, деление блока ${d.div.join(" × ")} (в модели их нет)` : "нет";
  const lv = d.levels ? `; уровней деления ${d.levels.join(" × ")}` : isFreeDiv(d.div) ? "; произвольное деление, не по уровням 2^L" : "";
  return `деление блока ${d.div.join(" × ")}${lv}`;
}

const CHECK_TEXT: Record<string, string> = { outside: "блок вне сетки", crossing: "блок пересекает границу родителя", duplicate: "повтор блока", overlap: "перекрытие блоков" };

/** The findings of the checks, one line each; none (null) — they were not run, an empty list — nothing found. */
export function checkLines(c: Checks | null): { lines: string[]; clean: boolean } | null {
  if (!c) return null;
  const lines: string[] = [];
  if (c.duplicates) lines.push(`Повторы блоков: ${count(c.duplicates)} (оставлена первая строка)`);
  if (c.overlaps && c.overlaps.n > 0) lines.push(`Перекрытия блоков: ${count(c.overlaps.n)} (${c.overlaps.method === "volume" ? "по объёму" : "точно"})`);
  if (c.empty.length) lines.push(`Поля без единого значения: ${c.empty.join(", ")}`);
  if (c.constant.length) lines.push(`Поля с одним и тем же значением: ${c.constant.join(", ")}`);
  const shown = c.examples.slice(0, 5).map((e) => `строка ${e.row + 1}: ${CHECK_TEXT[e.check] ?? e.check}`);
  if (shown.length) lines.push(`Примеры: ${shown.join("; ")}${c.examples.length > shown.length ? ` и ещё ${count(c.examples.length - shown.length)}` : ""}`);
  return { lines, clean: lines.length === 0 };
}

/** The facts of the model for the tab: the grid of «Свойства», the turn, the sub-blocks, the rows. */
export function modelFacts(model: BlockModelInfo, d: Details): Fact[] {
  const facts = gridFacts(model);
  facts.push({ label: "Поворот", value: rotationText(d) });
  facts.push({ label: "Субблоки", value: subText(d) });
  if (d.rows !== null) facts.push({ label: "Строк в файле", value: d.parents !== null && d.parents !== d.rows ? `${count(d.rows)} (родительских блоков ${count(d.parents)})` : count(d.rows) });
  if (d.full) facts.push({ label: "Родители", value: "все заняты (прототип)" });
  const volume = d.checks?.volume;
  if (volume !== null && volume !== undefined) facts.push({ label: "Объём блоков", value: `${num(volume, 1)} м³` });
  return facts;
}

