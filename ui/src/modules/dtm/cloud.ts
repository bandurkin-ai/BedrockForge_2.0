/**
 * Point clouds as a source of «Построение ЦТМ» (ТЗ 7.35 Д-22; ADR 0014 §5): the classes taken (by default the
 * ground — class 2, after «Фильтр земли»), the step of the thinning `cell` (0,01–1000 м; empty — the core picks
 * max(CR/2, √(площадь / 2 млн))), the body of `POST /wireframes/from-points` when the cloud is the only source
 * (its longest edge by default — 5 steps of the cloth of the last filter) and the arguments of
 * `terrain.from_points`. Pure.
 */
import { counted } from "../../shell/treeActions";
import { parseNumber } from "../importing/objects";
import { classLabel } from "../lines/classes";
import type { LineFileInfo, LineLayerInfo } from "../scene/objects";
import type { BuildBody } from "./build";

export interface CloudPick {
  layerId: number;
  /** ASPRS codes; by default [2] — the ground */
  classes: number[];
  /** the step of the thinning, m, as typed; empty — the core picks it */
  cell: string;
}

export const GROUND = 2;
export const CELL_RANGE = { lo: 0.01, hi: 1000 } as const;

const cloudsOf = (files: readonly LineFileInfo[]): LineLayerInfo[] => files.flatMap((f) => f.layers).filter((l) => !!l.cloud);

/** The classes a cloud offers: those it has, and the ground always (it is the default, even before a filter). */
export function classOptions(l: Pick<LineLayerInfo, "cloud">): { code: number; label: string; points: number }[] {
  const have = Object.entries(l.cloud?.classes ?? {})
    .map(([k, n]) => [Number(k), n] as const)
    .filter(([k]) => Number.isInteger(k) && k >= 0 && k <= 255);
  const codes = new Map<number, number>(have);
  if (!codes.has(GROUND)) codes.set(GROUND, 0);
  return [...codes.entries()].sort((a, b) => a[0] - b[0]).map(([code, points]) => ({ code, label: classLabel(code), points }));
}

/** Points of the classes picked. */
export const pickedPoints = (l: Pick<LineLayerInfo, "cloud">, classes: readonly number[]): number => classes.reduce((n, c) => n + (l.cloud?.classes?.[String(c)] ?? 0), 0);

/** The step of the thinning: null — not set; undefined — a wrong one. */
export function parseCell(text: string): number | null | undefined {
  const v = parseNumber(text);
  if (v === null) return null;
  return v === undefined || v < CELL_RANGE.lo || v > CELL_RANGE.hi ? undefined : v;
}

/** The `clouds` of `POST /wireframes/build-dtm`, or why they cannot be used. */
export function cloudBodies(picks: readonly CloudPick[], files: readonly LineFileInfo[]): { list: { layer_id: number; classes: number[]; cell: number | null }[]; error: string | null } {
  const clouds = cloudsOf(files);
  const list: { layer_id: number; classes: number[]; cell: number | null }[] = [];
  for (const p of picks) {
    const l = clouds.find((c) => c.id === p.layerId);
    if (!l) return { list: [], error: `Облако №${p.layerId} не найдено: обновите дерево проекта.` };
    if (!p.classes.length) return { list: [], error: `Облако «${l.name}»: отметьте классы точек — по умолчанию 2 — земля.` };
    if (!pickedPoints(l, p.classes)) {
      const ground = p.classes.length === 1 && p.classes[0] === GROUND;
      return { list: [], error: ground ? `В облаке «${l.name}» нет точек земли (класс 2): запустите «Фильтр земли».` : `В облаке «${l.name}» нет точек отмеченных классов.` };
    }
    const cell = parseCell(p.cell);
    if (cell === undefined) return { list: [], error: `«Шаг прореживания» облака «${l.name}» — число от 0,01 до 1000 м, например 0,5, или пусто — по облаку.` };
    list.push({ layer_id: l.id, classes: [...p.classes].sort((a, b) => a - b), cell });
  }
  return { list, error: null };
}

/** «облако «survey» (2 627 точек классов 2)» */
export function cloudsText(picks: readonly CloudPick[], files: readonly LineFileInfo[]): string {
  const clouds = cloudsOf(files);
  const points = picks.reduce((n, p) => {
    const l = clouds.find((c) => c.id === p.layerId);
    return n + (l ? pickedPoints(l, p.classes) : 0);
  }, 0);
  return `${counted(picks.length, "облако", "облака", "облаков")} (${counted(points, "точка", "точки", "точек")} отмеченных классов)`;
}

/** The body of `POST /wireframes/from-points` (`terrain.from_points`). */
export interface FromPointsBody {
  cloud_id: number;
  classes: number[];
  l_max: number | null;
  boundary_id: number | null;
  cell: number | null;
  name?: string | null;
  to_dtm?: boolean;
}

/** Where the window sends its build: a single cloud with a plain boundary goes to `from-points` (an empty
 * longest edge is then 5 steps of the cloth of the last filter, not «no limit»), the rest to `build-dtm`. */
export function requestOf(body: BuildBody): { path: string; body: BuildBody | FromPointsBody } {
  const c = body.clouds ?? [];
  const plain = body.boundary === null || (body.boundary.line === null && !body.boundary.use_z);
  if (c.length === 1 && !body.layer_ids.length && !body.set_ids.length && plain) {
    return {
      path: "/wireframes/from-points",
      body: { cloud_id: c[0].layer_id, classes: c[0].classes, l_max: body.max_edge, boundary_id: body.boundary?.layer_id ?? null, cell: c[0].cell, name: body.name, to_dtm: body.to_dtm },
    };
  }
  return { path: "/wireframes/build-dtm", body };
}

/** «2, 9» or «2;9» → [2, 9]; null — not a list of ASPRS codes. */
export function parseClasses(text: string): number[] | null {
  const parts = text.split(/[\s,;]+/).filter(Boolean);
  if (!parts.length) return null;
  const codes = parts.map(Number);
  return codes.every((c) => Number.isInteger(c) && c >= 0 && c <= 255) ? [...new Set(codes)].sort((a, b) => a - b) : null;
}

/** The body of `POST /wireframes/from-points` for the arguments of `terrain.from_points`. */
export function fromPointsBody(cloudId: number, a: Record<string, unknown>): { body: FromPointsBody | null; error: string | null } {
  const classes = typeof a.classes === "string" ? parseClasses(a.classes) : [GROUND];
  if (!classes) return { body: null, error: "«classes» — коды классов ASPRS через запятую, например «2» или «2, 9»." };
  const n = (v: unknown): number | null => (typeof v === "number" ? v : null);
  const cell = n(a.cell);
  if (cell !== null && (cell < CELL_RANGE.lo || cell > CELL_RANGE.hi)) return { body: null, error: "«Шаг прореживания» — от 0,01 до 1000 м." };
  const lMax = n(a.l_max);
  if (lMax !== null && lMax <= 0) return { body: null, error: "«Наибольшая длина ребра» — положительное число." };
  return { body: { cloud_id: cloudId, classes, l_max: lMax, boundary_id: n(a.boundary_id), cell }, error: null };
}
