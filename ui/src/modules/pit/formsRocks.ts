/**
 * The form of the window «Породы карьера» (ТЗ 8.11.2, 8.11.5; ADR 0028 решения 4, 6, 29; docs/progress/e21-api.md §2, §3, §16) and its conversions: the form → `params.rocks`
 * (`by` and `types`), the `params` of a scenario → the form, the flat arguments of `pit.rocks` ↔ the form. The type of a block is a category field of the model (every label and
 * «(пусто)» is a type) or an ordered list of rules (the first rule with a share above 0 gives the type, else «Прочие»); each type has c_p, c_mр, the recovery of every element
 * (a share 0…1), the marks «только пустая» (R = 0) and «воздух» (outside the graph, not waste). Everything wrong is told here in Russian with a hint and the place in the form —
 * nothing wrong reaches the core. A gap is never 0: c_p and rec that are not set stay `null` / absent («не готов»). No React.
 */
import { checkDraft, EMPTY_DRAFT, ruleToDraft, type Draft, type Rule } from "../blocks/filters/constructor";
import { numberText, parseDecimal } from "../blocks/report/state";
import { DEFAULT_LIMITS, list, obj, type Limits, type Missing, type PitParams, type RockTypeParam } from "./answers";
import { has, issue, MAX_MONEY, type Args, type FormIssue, type Got } from "./forms";

export const ALL_LABEL = "Все породы";
export const EMPTY_LABEL = "(пусто)";
export const OTHER_LABEL = "Прочие";

export type RockBy = "all" | "field" | "rules";
export type RockMode = "rock" | "waste" | "air";

export const MODE_TITLES: Record<RockMode, string> = { rock: "Обычная", waste: "Только пустая", air: "Воздух" };
export const MODE_HINTS: Record<RockMode, string> = {
  rock: "Руда или пустая порода по экономике блока",
  waste: "R = 0: блок никогда не руда, его можно вынуть только как вскрышу",
  air: "Вне графа и не пустая порода: блок не входит в карьер и не считается",
};

export interface RockRow {
  name: string;
  /** c_p, $/т; "" — not set */
  cP: string;
  /** c_mр, $/т; "" — 0 */
  cMr: string;
  /** rec_e by the field of the element; "" or no key — not set */
  rec: Record<string, string>;
  mode: RockMode;
}

export interface RockRule {
  name: string;
  draft: Draft;
}

export interface RocksForm {
  by: RockBy;
  field: string;
  rules: RockRule[];
  /** the rows the person has touched (and those the scenario has); the types met in the data are listed by `encounteredTypes` */
  types: RockRow[];
}

export const EMPTY_ROCKS: RocksForm = { by: "all", field: "", rules: [], types: [] };

export const newRock = (name: string): RockRow => ({ name, cP: "", cMr: "", rec: {}, mode: "rock" });
export const newRule = (): RockRule => ({ name: "", draft: EMPTY_DRAFT });

const same = (a: string, b: string): boolean => a.trim().toLowerCase() === b.trim().toLowerCase();
const modeOf = (t: { waste_only: boolean; air: boolean }): RockMode => (t.air ? "air" : t.waste_only ? "waste" : "rock");

const rowOfType = (t: RockTypeParam): RockRow => ({
  name: t.name,
  cP: t.c_p === null ? "" : numberText(t.c_p),
  cMr: numberText(t.c_mr),
  rec: Object.fromEntries(Object.entries(t.rec).map(([k, v]) => [k, numberText(v)])),
  mode: modeOf(t),
});

/** The form that shows the rocks of `p` (a body, the defaults or the params of a scenario). */
export function rocksFormOfParams(p: PitParams): RocksForm {
  const by = p.rocks.by;
  const kind = by && typeof by.kind === "string" ? by.kind : null;
  return {
    by: kind === "field" ? "field" : kind === "rules" ? "rules" : "all",
    field: kind === "field" && typeof by?.field === "string" ? by.field : "",
    rules: kind === "rules" ? list(by?.rules).map((x) => ({ name: typeof obj(x).name === "string" ? (obj(x).name as string) : "", draft: ruleToDraft((obj(x).rules ?? { op: "and", items: [] }) as Rule) })) : [],
    types: p.rocks.types.map(rowOfType),
  };
}

// ---- the types met in the data ----

/** `rocks.types.<name>[.c_p|.rec.<element>]` of `validate.missing` → the name of the type the core says is met (the path is by the name, not by the index). */
export function typeNamesOfMissing(missing: readonly Missing[]): string[] {
  const out: string[] = [];
  for (const m of missing) {
    const r = /^rocks\.types\.(.+?)(?:\.c_p|\.rec\.[^.]+)?$/.exec(m.param);
    if (r && !out.some((n) => same(n, r[1]))) out.push(r[1]);
  }
  return out;
}

/** What identifies the way of typing the blocks (to tell whether the answer of `validate` is about the same way as the form). */
export function byKey(f: RocksForm): string {
  return f.by === "all" ? "" : f.by === "field" ? `f:${f.field.trim().toLowerCase()}` : `r:${f.rules.map((r) => r.name.trim().toLowerCase()).join("|")}`;
}

export function byKeyOfParams(p: PitParams): string {
  return byKey(rocksFormOfParams(p));
}

/**
 * The types the data has, in the order of the window: «Все породы»; every label of the category field and «(пусто)»; the names of the rules and «Прочие»; then
 * those `validate` calls (`extra`, the same way of typing only). The labels are the model's (`defaults.model.categories`).
 */
export function encounteredTypes(f: RocksForm, categories: readonly { field: string; labels: readonly string[] }[], extra: readonly string[] = []): string[] {
  const out: string[] = [];
  const add = (n: string) => {
    if (n.trim() && !out.some((o) => same(o, n))) out.push(n);
  };
  if (f.by === "all") add(ALL_LABEL);
  else if (f.by === "field") {
    for (const l of categories.find((c) => same(c.field, f.field))?.labels ?? []) add(l);
    if (f.field.trim()) add(EMPTY_LABEL);
  } else {
    for (const r of f.rules) add(r.name);
    if (f.rules.length) add(OTHER_LABEL);
  }
  for (const n of extra) add(n);
  return out;
}

/** The rows the window shows: the types met (the row of the form, else an empty one), then the rows of the form that are not met (the person may take them away). */
export function rowsOf(f: RocksForm, met: readonly string[]): { row: RockRow; met: boolean; touched: boolean }[] {
  const rows = met.map((name) => {
    const t = f.types.find((x) => same(x.name, name));
    return { row: t ?? newRock(name), met: true, touched: !!t };
  });
  for (const t of f.types) if (!met.some((n) => same(n, t.name))) rows.push({ row: t, met: false, touched: true });
  return rows;
}

/** A type of the list is changed (the row is added when the person touches it). */
export function withRow(f: RocksForm, name: string, patch: Partial<RockRow>): RocksForm {
  const i = f.types.findIndex((t) => same(t.name, name));
  if (i < 0) return { ...f, types: [...f.types, { ...newRock(name), ...patch }] };
  return { ...f, types: f.types.map((t, k) => (k === i ? { ...t, ...patch } : t)) };
}

// ---- the form → the part of the params ----

export type RocksPatch = Pick<PitParams, "rocks">;

const part = (text: string, field: FormIssue["field"], what: string, param: string): { ok: true; value: number | null } | { ok: false; issue: FormIssue } => {
  if (!text.trim()) return { ok: true, value: null };
  const n = parseDecimal(text);
  if (n === null) return { ok: false, issue: issue(field, `${what} — не число.`, "Введите число, например 12,5; пусто — не задано.", param) };
  return { ok: true, value: n };
};

/** The form «Породы» → `params.rocks`; `elements` are the fields of the elements of the scenario (the keys of the recovery). */
export function rocksPatch(f: RocksForm, elements: readonly string[], limits: Limits = DEFAULT_LIMITS): Got<RocksPatch> {
  let by: PitParams["rocks"]["by"] = null;
  if (f.by === "field") {
    if (!f.field.trim()) return { ok: false, issue: issue("rock_by", "Не выбрано поле пород.", "Выберите категорийное поле модели или тип блока по правилам.", "rocks.by.field") };
    by = { kind: "field", field: f.field.trim() };
  } else if (f.by === "rules") {
    if (!f.rules.length) return { ok: false, issue: issue("rock_by", "Нет правил пород.", "Добавьте правило: имя типа и условие; блок получает тип первого подходящего правила.", "rocks.by.rules") };
    if (f.rules.length > limits.rock_rules) return { ok: false, issue: issue("rock_by", `Правил больше ${limits.rock_rules}.`, "Объедините правила.", "rocks.by.rules") };
    const names: string[] = [];
    const rules: { name: string; rules: Rule }[] = [];
    for (const [i, r] of f.rules.entries()) {
      const at = `rocks.by.rules.${i}`;
      const name = r.name.trim();
      if (!name) return { ok: false, issue: issue("rock_by", `У правила ${i + 1} нет имени.`, "Имя правила — имя типа породы.", `${at}.name`) };
      if (same(name, OTHER_LABEL)) return { ok: false, issue: issue("rock_by", `Имя «${OTHER_LABEL}» правилу дать нельзя.`, "Так называется тип блоков, которые не подошли ни одному правилу.", `${at}.name`) };
      if (names.some((n) => same(n, name))) return { ok: false, issue: issue("rock_by", `Имя «${name}» повторяется.`, "Имена правил различаются (без учёта регистра).", `${at}.name`) };
      names.push(name);
      const c = checkDraft(r.draft);
      if (!c.ok) return { ok: false, issue: issue("rock_by", `Правило «${name}»: ${c.issue.message}`, c.issue.hint, `${at}.rules`) };
      rules.push({ name, rules: c.rule });
    }
    by = { kind: "rules", rules };
  }

  if (f.types.length > limits.rock_types) return { ok: false, issue: issue("rock_types", `Типов пород больше ${limits.rock_types}.`, "Уберите лишние типы.", "rocks.types") };
  const seen = new Set<string>();
  const types: RockTypeParam[] = [];
  for (const [i, t] of f.types.entries()) {
    const at = `rocks.types.${i}`;
    const name = t.name.trim();
    if (!name) return { ok: false, issue: issue("rock_types", `У типа ${i + 1} нет имени.`, "Имя типа — подпись поля пород или имя правила.", `${at}.name`) };
    if (seen.has(name.toLowerCase())) return { ok: false, issue: issue("rock_types", `Тип «${name}» повторяется.`, "Типы различаются по имени без учёта регистра.", `${at}.name`) };
    seen.add(name.toLowerCase());
    const cP = part(t.cP, "rock_types", `Затраты на переработку c_p типа «${name}»`, `${at}.c_p`);
    if (!cP.ok) return cP;
    if (cP.value !== null && (cP.value < 0 || cP.value > MAX_MONEY)) return { ok: false, issue: issue("rock_types", `Затраты c_p типа «${name}» вне диапазона 0…10¹².`, "Затраты на переработку — неотрицательное число, $/т.", `${at}.c_p`) };
    const cMr = part(t.cMr, "rock_types", `Доплата c_mр типа «${name}»`, `${at}.c_mr`);
    if (!cMr.ok) return cMr;
    if (cMr.value !== null && (cMr.value < 0 || cMr.value > MAX_MONEY)) return { ok: false, issue: issue("rock_types", `Доплата c_mр типа «${name}» вне диапазона 0…10¹².`, "Доплата за добычу руды — неотрицательное число, $/т; пусто — 0.", `${at}.c_mr`) };
    const rec: Record<string, number> = {};
    for (const [k, v] of Object.entries(t.rec)) {
      if (!v.trim()) continue;
      const el = elements.find((e) => same(e, k));
      if (!el) return { ok: false, issue: issue("rock_types", `Извлечение для «${k}»: такого элемента нет в сценарии.`, "Элементы задаются в команде «Экономика».", `${at}.rec.${k}`) };
      const n = parseDecimal(v);
      if (n === null) return { ok: false, issue: issue("rock_types", `Извлечение ${el} типа «${name}» — не число.`, "Доля от 0 до 1, например 0,85.", `${at}.rec.${el}`) };
      if (n < 0 || n > 1) return { ok: false, issue: issue("rock_types", `Извлечение ${el} типа «${name}» вне диапазона 0…1.`, "Доля от 0 до 1: 0,85 — это 85 %.", `${at}.rec.${el}`) };
      rec[el] = n;
    }
    types.push({ name, c_p: cP.value, c_mr: cMr.value ?? 0, rec, waste_only: t.mode === "waste", air: t.mode === "air" });
  }
  return { ok: true, patch: { rocks: { by, types } } };
}

// ---- the flat arguments of `pit.rocks` ↔ the form ----

export const ROCKS_KEYS = ["rock_field", "rock_rules", "rock_types"] as const;
export const hasRocks = (a: Args): boolean => ROCKS_KEYS.some((k) => has(a, k));

/** The arguments of `pit.rocks` from the form: `rock_field` (text) or `rock_rules` (JSON: [{name, rules}]) — one of them, none — one type «Все породы»; `rock_types` — JSON, as the API has it. */
export function rocksArgs(f: RocksForm, scenarioId: number | null, elements: readonly string[], limits: Limits = DEFAULT_LIMITS): { ok: true; args: Args } | { ok: false; issue: FormIssue } {
  const got = rocksPatch(f, elements, limits);
  if (!got.ok) return got;
  const { by, types } = got.patch.rocks;
  const a: Args = {};
  if (scenarioId !== null) a.scenario_id = scenarioId;
  a.rock_field = by?.kind === "field" ? (by.field as string) : null;
  a.rock_rules = by?.kind === "rules" ? JSON.stringify(by.rules) : null;
  a.rock_types = JSON.stringify(types);
  return { ok: true, args: a };
}

const bad = (message: string, hint: string, param: string): { ok: false; issue: FormIssue } => ({ ok: false, issue: issue("rock_types", message, hint, param) });

const asText = (v: unknown): string => (typeof v === "number" ? numberText(v) : typeof v === "string" ? v.trim() : "");

function rowOfLoose(x: unknown, i: number): { ok: true; row: RockRow } | { ok: false; issue: FormIssue } {
  const t = obj(x);
  const at = `rocks.types.${i}`;
  if (typeof t.name !== "string" || !t.name.trim()) return bad(`У типа ${i + 1} нет имени.`, 'Тип — объект {"name": "Окисленная", "c_p": 12, "c_mr": 0, "rec": {"AU": 0.7}}.', `${at}.name`);
  const waste = t.waste_only === true;
  const air = t.air === true;
  if (waste && air) return bad(`Тип «${t.name}» отмечен и «только пустая», и «воздух».`, "Выберите одно: «воздух» — вне графа, «только пустая» — только вскрыша.", `${at}.air`);
  const rec: Record<string, string> = {};
  for (const [k, v] of Object.entries(obj(t.rec))) rec[k] = asText(v);
  return { ok: true, row: { name: t.name.trim(), cP: asText(t.c_p), cMr: asText(t.c_mr), rec, mode: modeOf({ waste_only: waste, air }) } };
}

/** The form that shows the arguments of a call (a key that is not in it stays as `base` has it); a wrong argument is told. */
export function rocksFormOfArgs(a: Args, base: PitParams): { ok: true; form: RocksForm } | { ok: false; issue: FormIssue } {
  const f = rocksFormOfParams(base);
  if (has(a, "rock_field") || has(a, "rock_rules")) {
    const field = typeof a.rock_field === "string" ? a.rock_field.trim() : "";
    const rawRules = a.rock_rules;
    const rulesGiven = rawRules !== null && rawRules !== undefined && !(typeof rawRules === "string" && !rawRules.trim());
    if (field && rulesGiven) return { ok: false, issue: issue("rock_by", "Заданы и поле пород, и правила.", "Тип блока — либо поле (rock_field), либо правила (rock_rules); второе уберите или передайте null.", "rocks.by") };
    if (rulesGiven) {
      try {
        const v: unknown = typeof rawRules === "string" ? JSON.parse(rawRules) : rawRules;
        if (!Array.isArray(v)) throw new Error("not a list");
        const rules = v.map((x) => {
          const r = obj(x);
          if (typeof r.name !== "string" || !r.rules || typeof r.rules !== "object" || Array.isArray(r.rules)) throw new Error("not a rule");
          return { name: r.name, draft: ruleToDraft(r.rules as Rule) };
        });
        Object.assign(f, { by: "rules", field: "", rules });
      } catch {
        return { ok: false, issue: issue("rock_by", "Правила пород — не список правил в JSON.", 'Пример: [{"name": "Богатая", "rules": {"expr": "AU > 2"}}]; блок получает тип первого подходящего правила.', "rocks.by.rules") };
      }
    } else if (field) Object.assign(f, { by: "field", field, rules: [] });
    else Object.assign(f, { by: "all", field: "", rules: [] });
  }
  if (has(a, "rock_types")) {
    const raw = a.rock_types;
    if (raw === null || (typeof raw === "string" && !raw.trim())) f.types = [];
    else {
      let v: unknown;
      try {
        v = typeof raw === "string" ? JSON.parse(raw) : raw;
      } catch {
        v = null;
      }
      if (!Array.isArray(v)) return bad("Типы пород — не список в JSON.", 'Пример: [{"name": "Окисленная", "c_p": 12, "c_mr": 0, "rec": {"AU": 0.7}, "waste_only": false, "air": false}].', "rocks.types");
      const rows: RockRow[] = [];
      for (const [i, x] of v.entries()) {
        const r = rowOfLoose(x, i);
        if (!r.ok) return r;
        rows.push(r.row);
      }
      f.types = rows;
    }
  }
  return { ok: true, form: f };
}
