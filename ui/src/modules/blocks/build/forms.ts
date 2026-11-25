/**
 * The form of the window «Прототип модели» and the pieces the forms of the building share (ТЗ 8.7.2, 8.7.3; ADR 0023 §6,
 * decision 8, № 66): the form of the window → the flat arguments of `blocks.prototype` (the parameters of a command are
 * flat: «10; 10; 5», «3, 7») → the body of `POST /blocks/prototype[/preview]`. Every wrong thing is told here in Russian
 * with a hint and the place in the form — nothing wrong reaches the core. A refusal of the core is put in its place by
 * `issueOfPrototype`. No React.
 */
import { looksGrouped, numberText, parseDecimal } from "../report/state";
import { MAX_LEVEL, type PrototypeBody, type Refusal, type Triple } from "./api";

export type Args = Record<string, unknown>;

/** The first wrong thing: the place in the form (`index` — the row of a list), the words, the hint (as `{code, message, hint}` of the core). */
export interface FormIssue {
  field: string;
  message: string;
  hint: string;
  index?: number;
  /** a 409: the window offers «Перечитать» */
  reread?: boolean;
}

export const issue = (field: string, message: string, hint: string, index?: number): FormIssue => ({ field, message, hint, ...(index === undefined ? {} : { index }) });

export const given = (a: Args, k: string): boolean => a[k] !== undefined && a[k] !== null && (typeof a[k] !== "string" || (a[k] as string).trim() !== "");

/** «10; 10; 5» / «10 10 5» / «0,5» → numbers (a decimal comma is a number); null — a part is not a number (or «1 000», which may be a list: use «;»). */
export function parseNumbers(text: string): number[] | null {
  let parts = text.split(/[;\n]+/).map((p) => p.trim()).filter(Boolean);
  if (parts.length === 1 && looksGrouped(parts[0])) return null; // «1 000»: digit groups or a list — not guessed, «;» tells
  if (parts.length === 1 && /\s/.test(parts[0])) parts = parts[0].split(/\s+/);
  const out = parts.map((p) => parseDecimal(p));
  return out.length && out.every((n): n is number => n !== null) ? out : null;
}

/** One number for all three axes, or three numbers; null — neither. */
export function parseTriple(text: string): Triple | null {
  const n = parseNumbers(text);
  if (!n) return null;
  if (n.length === 1) return [n[0], n[0], n[0]];
  return n.length === 3 ? [n[0], n[1], n[2]] : null;
}

export const tripleText = (t: readonly (number | string)[]): string => t.map((v) => (typeof v === "number" ? numberText(v) : v)).join("; ");

/** A typed number goes to a command as a number (the registry checks the types); text that is not one stays text and is told by the form. */
export const numOrText = (t: string): number | string => parseDecimal(t) ?? t;

/** A number of an argument: a number or a text «0,5». */
export const numberOf = (v: unknown): number | null => (typeof v === "number" ? (Number.isFinite(v) ? v : null) : typeof v === "string" ? parseDecimal(v) : null);

/** A text that is a name of a model or a field: trimmed, ≤ 400 signs; null — none. */
export function textOf(a: Args, k: string, field = k): { ok: true; value: string | null } | { ok: false; issue: FormIssue } {
  if (!given(a, k)) return { ok: true, value: null };
  const t = String(a[k]).trim().replace(/\s+/g, " ");
  if (t.length > 400) return { ok: false, issue: issue(field, "Не больше 400 знаков.", "Сократите текст.") };
  return { ok: true, value: t };
}

/** A list of whole numbers («3, 7»; a JSON array too); null — a part is not a whole number. */
export function parseIds(raw: unknown): number[] | null {
  const items: unknown[] = Array.isArray(raw) ? raw : typeof raw === "number" ? [raw] : String(raw).trim().startsWith("[") ? safeJson(String(raw)) : String(raw).split(/[,;\s]+/).filter(Boolean);
  const ids = items.map((x) => (typeof x === "number" ? x : typeof x === "string" && /^\d+$/.test(x.trim()) ? Number(x) : NaN));
  return ids.length && ids.every((n) => Number.isInteger(n) && n > 0) ? ids : null;
}

function safeJson(text: string): unknown[] {
  try {
    const v: unknown = JSON.parse(text);
    return Array.isArray(v) ? v : [NaN];
  } catch {
    return [NaN];
  }
}

// ---- the prototype ----

export interface PrototypeForm {
  /** the box of the grid: of the chosen objects (`objects.extent`) or typed */
  source: "objects" | "extent";
  objectIds: number[];
  /** xmin, ymin, zmin, xmax, ymax, zmax */
  extent: string[];
  size: string[];
  /** «0» / «20» / «20; 20; 10» */
  pad: string;
  rotation: string;
  levels: string[];
  /** «Все родители заняты» (№ 66) */
  full: boolean;
  name: string;
}

export const EMPTY_PROTOTYPE: PrototypeForm = {
  source: "objects",
  objectIds: [],
  extent: ["", "", "", "", "", ""],
  size: ["10", "10", "10"],
  pad: "0",
  rotation: "0",
  levels: ["0", "0", "0"],
  full: false,
  name: "Прототип",
};

/** The arguments of `blocks.prototype` of the form (flat: the lists are text). */
export function argsOfPrototype(f: PrototypeForm): Args {
  const a: Args = { size: f.size.join("; "), pad: f.pad, rotation: numOrText(f.rotation), levels: f.levels.join("; "), all: f.full };
  if (f.source === "objects") {
    if (f.objectIds.length) a.object_ids = f.objectIds.join(", ");
  } else if (f.extent.some((t) => t.trim() !== "")) a.extent = f.extent.join("; ");
  if (f.name.trim()) a.name = f.name.trim();
  return a;
}

const OBJECTS = "Выберите объекты (каркасы, линии) или задайте границы вручную.";

/** The body of the arguments (everything is checked here), or the first wrong thing. */
export function bodyOfPrototype(a: Args): { ok: true; body: PrototypeBody } | { ok: false; issue: FormIssue } {
  const fail = (field: string, message: string, hint: string): { ok: false; issue: FormIssue } => ({ ok: false, issue: issue(field, message, hint) });
  const size = given(a, "size") ? parseTriple(String(a.size)) : null;
  if (!size) return fail("size", "Размер блока — одно число или три (X; Y; Z).", "Например 10 или 10; 10; 5.");
  if (size.some((v) => !(v > 0))) return fail("size", "Размер блока должен быть больше нуля.", "Введите положительные числа, например 10; 10; 5.");
  const body: PrototypeBody = { size };
  if (given(a, "object_ids") && given(a, "extent")) return fail("objects", "Охват задан и объектами, и границами.", "Оставьте что-то одно: объекты или границы по X, Y, Z.");
  if (given(a, "object_ids")) {
    const ids = parseIds(a.object_ids);
    if (!ids) return fail("objects", "Номера объектов — целые числа.", "Перечислите номера через запятую, например 3, 7.");
    body.object_ids = ids;
  } else if (given(a, "extent")) {
    const e = parseNumbers(String(a.extent));
    if (!e || e.length !== 6) return fail("extent", "Границы — шесть чисел: мин. и макс. по X, Y, Z.", "Например 0; 0; 0; 600; 500; 400.");
    if ([0, 1, 2].some((k) => !(e[k] < e[k + 3]))) return fail("extent", "Минимум границы должен быть меньше максимума.", "Проверьте мин. и макс. по каждой оси.");
    body.extent = e;
  } else return fail("objects", "Не задан охват модели.", OBJECTS);
  if (given(a, "pad")) {
    const p = parseTriple(String(a.pad));
    if (!p || p.some((v) => v < 0)) return fail("pad", "Отступ — неотрицательное число или три (X; Y; Z).", "Например 20 или 20; 20; 10.");
    if (p.some((v) => v > 0)) body.pad = p[0] === p[1] && p[1] === p[2] ? p[0] : p;
  }
  if (given(a, "rotation")) {
    const r = numberOf(a.rotation);
    if (r === null || !(r > -180 && r <= 180)) return fail("rotation", "Поворот вокруг Z — число градусов от −180 (не включая) до 180.", "Например 30; против часовой стрелки при виде сверху.");
    if (r !== 0) body.rotation = r;
  }
  if (given(a, "levels")) {
    const l = parseTriple(String(a.levels));
    if (!l || l.some((v) => !Number.isInteger(v) || v < 0 || v > MAX_LEVEL)) return fail("levels", `Уровни субблоков — целые числа от 0 до ${MAX_LEVEL} по осям X, Y, Z.`, "Например 3; 3; 2 (0 — без дробления по оси).");
    if (l.some((v) => v > 0)) body.levels = l;
  }
  const all = a.all === true || a.all === "true" || a.all === "да";
  if (all) body.full = true;
  for (const k of ["name", "folder"] as const) {
    const t = textOf(a, k);
    if (!t.ok) return { ok: false, issue: t.issue };
    if (t.value) body[k] = t.value;
  }
  return { ok: true, body };
}

/** The preview does not need the name and the folder: they would change the key of the request, not the answer. */
export function previewBody(b: PrototypeBody): PrototypeBody {
  const { name: _name, folder: _folder, ...rest } = b;
  return rest;
}

/** The form that shows the arguments of a call (the window then shows what the command asks). */
export function prototypeFormOfArgs(a: Args, keep: PrototypeForm = EMPTY_PROTOTYPE): PrototypeForm {
  const f: PrototypeForm = { ...keep };
  const triple3 = (k: string): string[] | null => {
    const t = given(a, k) ? parseTriple(String(a[k])) : null;
    return t ? t.map(numberText) : null;
  };
  f.size = triple3("size") ?? f.size;
  f.levels = triple3("levels") ?? f.levels;
  if (given(a, "pad")) f.pad = String(a.pad);
  if (given(a, "rotation")) f.rotation = String(a.rotation);
  if (given(a, "object_ids")) {
    f.source = "objects";
    f.objectIds = parseIds(a.object_ids) ?? [];
  } else if (given(a, "extent")) {
    f.source = "extent";
    const e = parseNumbers(String(a.extent));
    if (e && e.length === 6) f.extent = e.map(numberText);
  }
  if ("all" in a) f.full = a.all === true || a.all === "true" || a.all === "да";
  if (given(a, "name")) f.name = String(a.name).trim();
  return f;
}

/** Where in the form a refusal of the core belongs (`param` of the body; the code when it names no place). */
export function issueOfPrototype(r: Refusal, form: PrototypeForm): FormIssue {
  const head = (r.param ?? "").split(".")[0];
  const place: Record<string, string> = { size: "size", object_ids: "objects", extent: "extent", pad: "pad", rotation: "rotation", levels: "levels", name: "name", folder: "name" };
  let field = place[head] ?? "general";
  if (head === "object_ids" && form.source === "extent") field = "extent";
  if (r.code === "no_extent") field = form.source === "objects" ? "objects" : "extent";
  else if (r.code === "subblock_div") field = "levels";
  else if (r.code === "block_model_too_large") field = "size";
  else if (r.code === "object_not_found") field = "objects";
  return issue(field, r.message, r.hint);
}
