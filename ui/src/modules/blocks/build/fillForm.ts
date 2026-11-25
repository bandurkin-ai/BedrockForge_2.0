/**
 * The forms of «Заполнить каркасами» and «Кодировать каркасами» (ТЗ 8.7.3; ADR 0023 §6, decisions 8, 9; e17-api.md §7):
 * the form of the window → the flat arguments of the commands `blocks.fill` / `blocks.code` (the list of wireframes is
 * JSON or «5; 6») → the body of `POST /blocks/fill` / `POST /blocks/{id}/code`. Priorities: a bigger number is the main
 * one, equal ones — the one earlier in the list. A code that is not typed is the name of the wireframe (the core's
 * default). Every wrong thing is told here in Russian with a hint and the place in the form (`index` — the row of the
 * wireframe); a refusal of the core is put in its place by `issueOfFill`. No React.
 */
import { numberText } from "../report/state";
import { MAX_LEVEL, MAX_M, MODES, type CodeBody, type FillBody, type Mode, type Refusal, type WireBody } from "./api";
import { given, issue, numberOf, numOrText, parseIds, parseTriple, textOf, type Args, type FormIssue } from "./forms";

export interface WireRow {
  id: number;
  /** "" — the name of the wireframe */
  code: string;
  /** "" — none: the equal ones go by the list */
  priority: string;
}

export interface FillForm {
  prototypeId: number | null;
  wires: WireRow[];
  mode: Mode;
  /** false — the levels of the prototype */
  levelsOn: boolean;
  levels: string[];
  m: string;
  outsideCode: string;
  codeField: string;
  fracField: string;
  name: string;
}

export interface CodeForm {
  modelId: number | null;
  wires: WireRow[];
  mode: Mode;
  /** «по центру» (false) or «с дроблением» (true) */
  split: boolean;
  m: string;
  outsideCode: string;
  codeField: string;
  /** by the centre FRAC is written only when asked */
  fracOn: boolean;
  fracField: string;
  name: string;
}

export const DEFAULT_M = 4;
export const OUTSIDE = "вне";

export const EMPTY_FILL: FillForm = {
  prototypeId: null,
  wires: [],
  mode: "inside",
  levelsOn: false,
  levels: ["0", "0", "0"],
  m: String(DEFAULT_M),
  outsideCode: OUTSIDE,
  codeField: "CODE",
  fracField: "FRAC",
  name: "",
};

export const EMPTY_CODE: CodeForm = {
  modelId: null,
  wires: [],
  mode: "inside",
  split: false,
  m: String(DEFAULT_M),
  outsideCode: OUTSIDE,
  codeField: "CODE",
  fracOn: false,
  fracField: "FRAC",
  name: "",
};

/** The words of the modes (the list of the window and what a person may type into a command). */
export const MODE_TITLES: Record<Mode, string> = { inside: "Внутри каркасов", outside: "Снаружи каркасов", both: "Внутри и снаружи" };
const MODE_WORDS: Record<string, Mode> = { inside: "inside", внутри: "inside", outside: "outside", снаружи: "outside", both: "both", оба: "both", "внутри и снаружи": "both" };

export const modeOf = (v: unknown): Mode | null => (typeof v === "string" ? (MODE_WORDS[v.trim().toLowerCase()] ?? null) : null);

// ---- the list of wireframes ----

const wireText = (w: WireRow): Record<string, unknown> => ({ id: w.id, ...(w.code.trim() ? { code: w.code.trim() } : {}), ...(w.priority.trim() ? { priority: Number(w.priority.replace(",", ".")) } : {}) });

/** The `wireframes` argument of a form: JSON `[{id, code?, priority?}]`. */
export const wiresArg = (rows: readonly WireRow[]): string => JSON.stringify(rows.map(wireText));

/** The list of wireframes of an argument (JSON of objects or numbers, or «5; 6»), or the first wrong thing. */
export function wiresOfArgs(raw: unknown): { ok: true; wires: WireBody[] } | { ok: false; issue: FormIssue } {
  const bad = (message: string, hint: string, index?: number): { ok: false; issue: FormIssue } => ({ ok: false, issue: issue("wireframes", message, hint, index) });
  let items: unknown[];
  if (Array.isArray(raw)) items = raw;
  else if (typeof raw === "string" && raw.trim().startsWith("[")) {
    try {
      const v: unknown = JSON.parse(raw);
      items = Array.isArray(v) ? v : [];
    } catch {
      return bad("Список каркасов — не JSON.", 'Пример: [{"id": 5, "code": "Руда", "priority": 2}, {"id": 6}] или номера через «;».');
    }
  } else {
    const ids = typeof raw === "number" || typeof raw === "string" ? parseIds(raw) : null;
    items = ids ?? [];
  }
  if (!items.length) return bad("Не выбрано ни одного каркаса.", "Добавьте хотя бы один замкнутый каркас.");
  const wires: WireBody[] = [];
  for (const [i, x] of items.entries()) {
    const o = typeof x === "number" ? { id: x } : x && typeof x === "object" ? (x as Record<string, unknown>) : {};
    const id = numberOf(o.id);
    if (id === null || !Number.isInteger(id) || id <= 0) return bad(`Каркас №${i + 1} списка: не указан номер.`, "Номера каркасов — в разделе «Каркасы» дерева.", i);
    if (wires.some((w) => w.id === id)) return bad(`Каркас №${id} указан дважды.`, "Каждый каркас — один раз; приоритет и код задаются в строке списка.", i);
    const w: WireBody = { id };
    if (given(o, "code")) {
      const code = String(o.code).trim().replace(/\s+/g, " ");
      if (code.length > 100) return bad("Код каркаса — не больше 100 знаков.", "Сократите код.", i);
      w.code = code;
    }
    if (given(o, "priority")) {
      const p = numberOf(o.priority);
      if (p === null || !Number.isInteger(p)) return bad("Приоритет — целое число.", "Чем больше число, тем главнее каркас; пусто — по порядку списка.", i);
      w.priority = p;
    }
    wires.push(w);
  }
  return { ok: true, wires };
}

/** The rows of a form from a list of wires. */
export const rowsOfWires = (wires: readonly WireBody[]): WireRow[] => wires.map((w) => ({ id: w.id, code: w.code ?? "", priority: w.priority === undefined ? "" : String(w.priority) }));

// ---- the parts both forms have ----

interface Common {
  mode: Mode;
  m: number | null;
  outside_code: string | null;
  code_field: string | null;
  frac_field: string | null;
  name: string | null;
  levels: [number, number, number] | null;
}

type Checked<T> = { ok: true; value: T } | { ok: false; issue: FormIssue };

function commonOfArgs(a: Args): Checked<Common> {
  const bad = (field: string, message: string, hint: string): { ok: false; issue: FormIssue } => ({ ok: false, issue: issue(field, message, hint) });
  const mode = given(a, "mode") ? modeOf(a.mode) : "inside";
  if (!mode) return bad("mode", `Режим «${String(a.mode)}» неизвестен.`, `Допустимы: ${MODES.join(", ")} (внутри, снаружи, оба).`);
  let m: number | null = null;
  if (given(a, "m")) {
    const n = numberOf(a.m);
    if (n === null || !Number.isInteger(n) || n < 1 || n > MAX_M) return bad("m", `Точек подсетки m: целое число от 1 до ${MAX_M}.`, `Чем больше m, тем точнее доля объёма и дольше расчёт; обычно ${DEFAULT_M}.`);
    m = n;
  }
  let levels: [number, number, number] | null = null;
  if (given(a, "levels")) {
    const l = parseTriple(String(a.levels));
    if (!l || l.some((v) => !Number.isInteger(v) || v < 0 || v > MAX_LEVEL)) return bad("levels", `Уровни дробления L — целые числа от 0 до ${MAX_LEVEL} по осям X, Y, Z.`, "Например 3; 3; 2; пусто — уровни прототипа.");
    levels = l;
  }
  const texts: Record<string, string | null> = {};
  for (const k of ["outside_code", "code_field", "frac_field", "name"] as const) {
    const t = textOf(a, k);
    if (!t.ok) return { ok: false, issue: t.issue };
    texts[k] = t.value;
  }
  return { ok: true, value: { mode, m, levels, outside_code: texts.outside_code, code_field: texts.code_field, frac_field: texts.frac_field, name: texts.name } };
}

/** What both bodies take the same way: the code outside, the name of the field of the code (not the default «CODE»), the name of the model. */
function applyCommon(body: FillBody | CodeBody, c: Common): void {
  if (c.outside_code && c.mode !== "inside") body.outside_code = c.outside_code;
  if (c.code_field && c.code_field !== "CODE") body.code_field = c.code_field;
  if (c.name) body.name = c.name;
}

// ---- the fill ----

export function argsOfFill(f: FillForm): Args {
  const a: Args = { wireframes: wiresArg(f.wires), mode: f.mode, m: numOrText(f.m) };
  if (f.prototypeId !== null) a.prototype_id = f.prototypeId;
  if (f.levelsOn) a.levels = f.levels.join("; ");
  if (f.mode !== "inside") a.outside_code = f.outsideCode;
  if (f.codeField.trim() && f.codeField.trim() !== "CODE") a.code_field = f.codeField;
  if (f.fracField.trim() && f.fracField.trim() !== "FRAC") a.frac_field = f.fracField;
  if (f.name.trim()) a.name = f.name;
  return a;
}

export function bodyOfFill(a: Args): { ok: true; body: FillBody } | { ok: false; issue: FormIssue } {
  const id = numberOf(a.prototype_id);
  if (id === null || !Number.isInteger(id)) return { ok: false, issue: issue("prototype", "Не выбран прототип.", "Выберите прототип модели: он создаётся командой «Прототип» (номер — в дереве, «Блочные модели»).") };
  const w = wiresOfArgs(a.wireframes);
  if (!w.ok) return w;
  const c = commonOfArgs(a);
  if (!c.ok) return c;
  const body: FillBody = { prototype_id: id, wireframes: w.wires, mode: c.value.mode, m: c.value.m ?? DEFAULT_M };
  if (c.value.levels) body.levels = c.value.levels;
  applyCommon(body, c.value);
  if (c.value.frac_field && c.value.frac_field !== "FRAC") body.frac_field = c.value.frac_field;
  const f = textOf(a, "folder");
  if (!f.ok) return f;
  if (f.value) body.folder = f.value;
  return { ok: true, body };
}

export function fillFormOfArgs(a: Args, keep: FillForm = EMPTY_FILL): FillForm {
  const f: FillForm = { ...keep };
  if (given(a, "prototype_id")) f.prototypeId = numberOf(a.prototype_id);
  const w = given(a, "wireframes") ? wiresOfArgs(a.wireframes) : null;
  if (w?.ok) f.wires = rowsOfWires(w.wires);
  const mode = given(a, "mode") ? modeOf(a.mode) : null;
  if (mode) f.mode = mode;
  if (given(a, "m")) f.m = String(a.m);
  const l = given(a, "levels") ? parseTriple(String(a.levels)) : null;
  if (l) Object.assign(f, { levelsOn: true, levels: l.map(numberText) });
  for (const [k, key] of [["outside_code", "outsideCode"], ["code_field", "codeField"], ["frac_field", "fracField"], ["name", "name"]] as const) if (given(a, k)) f[key] = String(a[k]).trim();
  return f;
}

// ---- the coding ----

export function argsOfCode(f: CodeForm): Args {
  const a: Args = { wireframes: wiresArg(f.wires), mode: f.mode, split: f.split };
  if (f.modelId !== null) a.model_id = f.modelId;
  if (f.split) a.m = numOrText(f.m);
  if (f.mode !== "inside") a.outside_code = f.outsideCode;
  if (f.codeField.trim() && f.codeField.trim() !== "CODE") a.code_field = f.codeField;
  if ((f.split || f.fracOn) && f.fracField.trim() && (f.split ? f.fracField.trim() !== "FRAC" : true)) a.frac_field = f.fracField;
  if (f.split && f.name.trim()) a.name = f.name;
  return a;
}

export function bodyOfCode(a: Args, version: number): { ok: true; modelId: number; body: CodeBody } | { ok: false; issue: FormIssue } {
  const id = numberOf(a.model_id);
  if (id === null || !Number.isInteger(id)) return { ok: false, issue: issue("model", "Не выбрана блочная модель.", "Выберите модель, которую нужно закодировать.") };
  const w = wiresOfArgs(a.wireframes);
  if (!w.ok) return w;
  const c = commonOfArgs(a);
  if (!c.ok) return c;
  const split = a.split === true || a.split === "true" || a.split === "да";
  if (given(a, "split") && !split && !(a.split === false || a.split === "false" || a.split === "нет")) return { ok: false, issue: issue("split", "Способ кодирования — «по центру» или «с дроблением».", "Передайте split: true (с дроблением) или false (по центру).") };
  const expected = given(a, "expected_version") ? numberOf(a.expected_version) : version;
  const body: CodeBody = { expected_version: expected ?? version, wireframes: w.wires, mode: c.value.mode };
  if (split) {
    body.split = true;
    body.m = c.value.m ?? DEFAULT_M;
  }
  applyCommon(body, c.value);
  if (c.value.frac_field && (split ? c.value.frac_field !== "FRAC" : true)) body.frac_field = c.value.frac_field;
  return { ok: true, modelId: id, body };
}

export function codeFormOfArgs(a: Args, keep: CodeForm = EMPTY_CODE): CodeForm {
  const f: CodeForm = { ...keep };
  if (given(a, "model_id")) f.modelId = numberOf(a.model_id);
  const w = given(a, "wireframes") ? wiresOfArgs(a.wireframes) : null;
  if (w?.ok) f.wires = rowsOfWires(w.wires);
  const mode = given(a, "mode") ? modeOf(a.mode) : null;
  if (mode) f.mode = mode;
  if ("split" in a) f.split = a.split === true || a.split === "true" || a.split === "да";
  if (given(a, "m")) f.m = String(a.m);
  for (const [k, key] of [["outside_code", "outsideCode"], ["code_field", "codeField"], ["name", "name"]] as const) if (given(a, k)) f[key] = String(a[k]).trim();
  if (given(a, "frac_field")) Object.assign(f, { fracOn: true, fracField: String(a.frac_field).trim() });
  return f;
}

// ---- a refusal of the core in its place in the form ----

/** Where a refusal of the fill / the coding belongs (`param` of the body; the code when it names no place). */
export function issueOfFill(r: Refusal): FormIssue {
  const parts = (r.param ?? "").split(".");
  const head = parts[0];
  const place: Record<string, string> = { prototype_id: "prototype", wireframes: "wireframes", mode: "mode", levels: "levels", m: "m", outside_code: "outside_code", code_field: "code_field", frac_field: "frac_field", name: "name", folder: "name", split: "split", expected_version: "general" };
  let field = place[head] ?? "general";
  const index = head === "wireframes" && /^\d+$/.test(parts[1] ?? "") ? Number(parts[1]) : undefined;
  if (r.code === "block_model_not_found") field = "model";
  else if (r.code === "not_prototype") field = "prototype";
  else if (r.code === "block_model_empty") field = "model";
  else if (r.code === "wireframe_orientation" || r.code === "wireframe_open" || r.code === "wireframe_not_found") field = "wireframes";
  else if (r.code === "fill_mode") field = "mode";
  else if (r.code === "frac_exists") field = "frac_field";
  else if (r.code === "version_conflict") return { ...issue("general", r.message, r.hint), reread: true };
  return issue(field, r.message, r.hint, index);
}
