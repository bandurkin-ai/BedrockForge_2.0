/**
 * The form of a planned hole and the words about it (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решения 28, 29; docs/progress/e19-api.md §10). The form is text (as
 * typed, a decimal comma allowed); `bodyOf` checks it against the ranges of the core (`planned.py`: azimuth [0; 360), dip [−90; 90], the length
 * (0; 10 000] m, a change −30…30 deg per 100 m, the three numbers of a point) and makes the body of `POST` / `PUT`, or the first wrong thing with the
 * path of the body (`collar`, `azimuth`, `change.dip`…) — the words stand under the field. The mode is one of three, as in the core: a straight hole,
 * a hole that turns (the change of the azimuth and of the dip per 100 m) or a hole through a target (the azimuth and the dip are solved by the core).
 * Pure: no React.
 */
import { numberText, parseNumber } from "../composites/params";
import { bad, isIssue, numberIn, type Bounds, type Issue } from "../estimation/formBase";
import { METHODS, type CompareAnswer, type CompareRow, type EvaluateAnswer, type Method, type Nearest, type PlannedBody, type PlannedRow, type Triple } from "./plannedApi";

export type Mode = "straight" | "change" | "target";
export const MODES: readonly Mode[] = ["straight", "change", "target"];
export const MODE_TITLES: Record<Mode, string> = { straight: "Прямая", change: "С изменением на 100 м", target: "Через цель" };
export const MODE_HINTS: Record<Mode, string> = {
  straight: "Азимут и угол постоянны по всей длине",
  change: "Азимут и угол меняются на заданное число градусов на каждые 100 м глубины",
  target: "Прямая от устья через целевую точку: азимут и угол считает ядро и записывает в скважину",
};

export type Xyz = [string, string, string];

export interface PlannedForm {
  name: string;
  setId: number | null;
  method: Method;
  collar: Xyz;
  /** deg from the north, clockwise: [0; 360) */
  azimuth: string;
  /** deg: down is negative (as 7.11) */
  dip: string;
  /** m; in the mode «через цель» empty means «до цели» */
  length: string;
  mode: Mode;
  changeAzimuth: string;
  changeDip: string;
  target: Xyz;
}

export const EMPTY_FORM: PlannedForm = {
  name: "",
  setId: null,
  method: "minimum_curvature",
  collar: ["", "", ""],
  azimuth: "0",
  dip: "-90",
  length: "",
  mode: "straight",
  changeAzimuth: "0",
  changeDip: "0",
  target: ["", "", ""],
};

/** The ranges of the form (they mirror `planned.py`). */
export const RANGES = {
  azimuth: { lo: 0, hi: 360, unit: "°" },
  dip: { lo: -90, hi: 90, unit: "°" },
  length: { lo: 0, hi: 10_000, open: true, unit: "м" },
  change: { lo: -30, hi: 30, unit: "° на 100 м" },
} satisfies Record<string, Bounds>;

const text = (v: number): string => numberText(v);
const xyzText = (t: Triple | null): Xyz => (t ? [text(t[0]), text(t[1]), text(t[2])] : ["", "", ""]);

/** The form of a stored hole (what the window shows when it is opened). */
export function formOfRow(row: PlannedRow): PlannedForm {
  const m = row.meta;
  const mode: Mode = m.target ? "target" : m.change ? "change" : "straight";
  // a hole that ends at its target has no length of its own: empty means «до цели»
  const toTarget = mode === "target" && row.target_depth !== null && m.length <= row.target_depth + 1e-6;
  return {
    name: row.name,
    setId: m.set_id,
    method: m.method,
    collar: xyzText(m.collar),
    azimuth: text(m.azimuth),
    dip: text(m.dip),
    length: toTarget ? "" : text(m.length),
    mode,
    changeAzimuth: m.change ? text(m.change.azimuth) : "0",
    changeDip: m.change ? text(m.change.dip) : "0",
    target: xyzText(m.target),
  };
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const numText = (v: unknown, dflt: string): string => (typeof v === "string" ? v : num(v) !== null ? text(v as number) : dflt);

/** The form taken from the arguments of a command over `base` (a missing key keeps the old value). */
export function formOfArgs(a: Record<string, unknown>, base: PlannedForm): PlannedForm | Issue {
  const f: PlannedForm = { ...base, collar: [...base.collar], target: [...base.target] };
  if (typeof a.name === "string") f.name = a.name;
  if (a.set_id !== undefined) f.setId = num(a.set_id) !== null ? (a.set_id as number) : null;
  if (a.method !== undefined) {
    if (!(METHODS as readonly unknown[]).includes(a.method)) return bad("method", "Метод расчёта траектории не поддерживается.", `Выберите один из: ${METHODS.join(", ")}.`);
    f.method = a.method as Method;
  }
  (["x", "y", "z"] as const).forEach((k, i) => {
    if (a[k] !== undefined) f.collar[i] = numText(a[k], "");
    if (a[`target_${k}`] !== undefined) f.target[i] = numText(a[`target_${k}`], "");
  });
  if (a.azimuth !== undefined) f.azimuth = numText(a.azimuth, f.azimuth);
  if (a.dip !== undefined) f.dip = numText(a.dip, f.dip);
  if (a.length !== undefined) f.length = numText(a.length, f.length);
  if (a.change_azimuth !== undefined) f.changeAzimuth = numText(a.change_azimuth, f.changeAzimuth);
  if (a.change_dip !== undefined) f.changeDip = numText(a.change_dip, f.changeDip);
  const hasTarget = a.target_x !== undefined || a.target_y !== undefined || a.target_z !== undefined;
  const hasChange = a.change_azimuth !== undefined || a.change_dip !== undefined;
  if (hasTarget && hasChange) return bad("change", "Задайте одно: изменения на 100 м или цель.", "Уберите изменения азимута и угла или координаты цели.");
  if (hasTarget) f.mode = "target";
  else if (hasChange) f.mode = "change";
  return f;
}

const AXES = ["X", "Y", "Z"] as const;

function point(t: Xyz, what: string, param: string): Triple | Issue {
  const out: number[] = [];
  for (let i = 0; i < 3; i++) {
    const v = parseNumber(t[i]);
    if (v === null) return bad(param, `${what}: координата ${AXES[i]} — не число.`, "Введите три числа X, Y, Z проекта, например 5500,25.");
    out.push(v);
  }
  return [out[0], out[1], out[2]];
}

export type Built = { ok: true; body: PlannedBody } | { ok: false; issue: Issue };

/** The body of `POST` / `PUT` for the form, or the first wrong thing. `update` — a stored hole: an empty name keeps the name. */
export function bodyOf(f: PlannedForm): Built {
  const fail = (issue: Issue): Built => ({ ok: false, issue });
  const body: PlannedBody = { method: f.method, set_id: f.setId };
  if (f.name.trim()) body.name = f.name.trim();
  const collar = point(f.collar, "Устье", "collar");
  if (isIssue(collar)) return fail(collar);
  body.collar = collar;
  if (f.mode === "target") {
    const target = point(f.target, "Цель", "target");
    if (isIssue(target)) return fail(target);
    body.target = target;
    body.change = null;
    if (f.length.trim() === "") body.length = null; // the hole ends at its target
    else {
      const length = numberIn(f.length, RANGES.length, "Длина", "length");
      if (isIssue(length)) return fail(length);
      body.length = length;
    }
    return { ok: true, body };
  }
  const azimuth = numberIn(f.azimuth, RANGES.azimuth, "Азимут", "azimuth");
  if (isIssue(azimuth)) return fail(azimuth);
  if (azimuth >= 360) return fail(bad("azimuth", "Азимут: нужно число от 0 до 360 (360 не входит).", "Задайте азимут в градусах, например 90."));
  const dip = numberIn(f.dip, RANGES.dip, "Угол", "dip");
  if (isIssue(dip)) return fail(dip);
  const length = numberIn(f.length, RANGES.length, "Длина", "length");
  if (isIssue(length)) return fail(length);
  Object.assign(body, { azimuth, dip, length, target: null });
  if (f.mode === "change") {
    const ca = numberIn(f.changeAzimuth, RANGES.change, "Изменение азимута на 100 м", "change.azimuth");
    if (isIssue(ca)) return fail(ca);
    const cd = numberIn(f.changeDip, RANGES.change, "Изменение угла на 100 м", "change.dip");
    if (isIssue(cd)) return fail(cd);
    body.change = { azimuth: ca, dip: cd };
  } else body.change = null;
  return { ok: true, body };
}

/** Does the form differ from the one of the stored hole (the same numbers, whatever the spelling)? */
export function sameForm(a: PlannedForm, b: PlannedForm): boolean {
  const n = (s: string) => parseNumber(s) ?? s.trim();
  const xs = (t: Xyz, u: Xyz) => t.every((v, i) => n(v) === n(u[i]));
  const base = a.name.trim() === b.name.trim() && a.setId === b.setId && a.method === b.method && a.mode === b.mode && xs(a.collar, b.collar) && n(a.length) === n(b.length);
  if (!base) return false;
  if (a.mode === "target") return xs(a.target, b.target);
  return n(a.azimuth) === n(b.azimuth) && n(a.dip) === n(b.dip) && (a.mode !== "change" || (n(a.changeAzimuth) === n(b.changeAzimuth) && n(a.changeDip) === n(b.changeDip)));
}

// ---- the words ----

const ru = (v: number, digits = 2): string => v.toLocaleString("ru-RU", { maximumFractionDigits: digits });

export const lengthText = (v: number | null): string => (v === null ? "—" : `${ru(v)} м`);
export const angleText = (v: number | null): string => (v === null ? "—" : `${ru(v, 1)}°`);

/** «210; 210; 330». */
export const xyzLine = (t: Triple | null): string => (t ? t.map((v) => ru(v, 2)).join("; ") : "—");

/** A short line about a stored hole: the tree's tooltip and the answer of a command. */
export function rowLine(r: PlannedRow): string {
  const m = r.meta;
  const how = m.target ? `через цель (${xyzLine(m.target)}), цель на глубине ${lengthText(r.target_depth)}` : m.change ? `изменение на 100 м: азимут ${ru(m.change.azimuth, 2)}°, угол ${ru(m.change.dip, 2)}°` : "прямая";
  return `«${r.name}» (№${r.id}, версия ${r.version}): устье ${xyzLine(m.collar)}, азимут ${angleText(m.azimuth)}, угол ${angleText(m.dip)}, длина ${lengthText(m.length)}, ${how}`;
}

/** The text of a code of an interval: a gap between the bodies has none. */
export const codeText = (c: string | null): string => c ?? "вне тел";

export function evaluateLine(a: EvaluateAnswer, name: string): string {
  const parts: string[] = [];
  if (a.intervals) {
    const real = a.intervals.filter((i) => i.code !== null);
    parts.push(`прогноз по телам: ${real.length} ${real.length === 1 ? "интервал" : "интервалов"} в телах, ${lengthText(a.inside)} из ${lengthText(a.length)}${a.codes?.length ? ` (${a.codes.filter((c) => c.length > 0).map((c) => `${c.code} — ${lengthText(c.length)}`).join("; ") || "тела не пересечены"})` : ""}`);
  }
  if (a.nearest) parts.push(`ближайшие скважины у ${a.target.depth !== null ? "точки на глубине " + lengthText(a.target.depth) : "цели"}: ${a.nearest.length ? a.nearest.map((n) => `${n.hole} — ${lengthText(n.distance)}`).join("; ") : "нет"}`);
  if (a.journal.length) parts.push(a.journal.join(" "));
  return `Проектная скважина «${name}»: ${parts.join(". ") || "нечего показать"}.`;
}

export function compareLine(a: CompareAnswer, name: string): string {
  if (a.max === null) return `План / факт «${name}» и «${a.hole}»: нечего сравнивать — траектории не пересекаются по глубине.`;
  return `План / факт «${name}» и «${a.hole}»: наибольшее отклонение ${lengthText(a.max)} на глубине ${lengthText(a.max_depth)}; сравнено ${lengthText(a.length.compared)} (план ${lengthText(a.length.planned)}, факт ${lengthText(a.length.actual)}).`;
}

/** The nearest holes as the table shows them. */
export const nearestRows = (a: EvaluateAnswer | null): Nearest[] => a?.nearest ?? [];

/** The points of the chart «отклонение по глубине»: the depth and the distance between the paths. */
export const chartPoints = (rows: readonly CompareRow[]): { x: number; y: number }[] => rows.map((r) => ({ x: r.depth, y: r.d }));

/** A file name without what Windows forbids. */
export const fileSafe = (name: string): string => name.replace(/[\\/:*?"<>|]+/g, "_").trim() || "Проектная скважина";

export const exportName = (name: string, format: "csv" | "dxf"): string => `${fileSafe(name)}.${format}`;

/** The next free name «ПС-1», «ПС-2» … among the stored holes (the core numbers the empty ones itself). */
export function freeName(taken: readonly string[], base = "ПС"): string {
  const used = new Set(taken.map((n) => n.trim().toLowerCase()));
  for (let i = 1; i < 10_000; i++) if (!used.has(`${base}-${i}`.toLowerCase())) return `${base}-${i}`;
  return base;
}
