/**
 * The form of the categories of a block model (ТЗ 8.7.5; ADR 0023 §9, decision 13, № 68) and the arguments of the commands
 * `blocks.classify` and `blocks.scheme.save`: the scheme of the project (JORC, ГКЗ, the user's own) and its rules in order
 * — the first rule a block fulfils gives it the label —, the field of the estimate («блок без оценки категории не
 * получает»), the wireframes of the categories that override the rule, the majority filter 3×3×3 (0–10 passes), the
 * restrictor. The table of the rules is the draft of the window: unchanged, the run names the scheme; edited, it sends the
 * rules themselves (exactly one of them goes to the core). Every wrong thing of the arguments is told here in Russian with
 * a hint and the place in the form. No React.
 */
import type { ClassifyBody, Scheme, SchemeRule } from "./api";
import { EMPTY_RESTRICT, given, isInt, issue, jsonArg, nameIssue, restrictArgs, restrictFormOfArgs, restrictOfArgs, type Args, type Issue, type RestrictForm } from "./common";

export const MAX_RULES = 64;
export const MAX_MAJORITY = 10;
export const MAX_LABEL = 100;
export const MAX_SCHEME_NAME = 100;
export const SCHEME_ID = /^[A-Za-z0-9_-]{1,40}$/;
export const MAX_PREFIX = 40;
/** The same rule as the prefix of an estimate (core `schemes.PREFIX_RE`): letters of any alphabet, digits, «_» — «Золото» gives «ЗОЛОТО_OK_». */
export const PREFIX_RE = /^[\p{L}\p{N}_]{1,40}$/u;
/** The fields an estimate writes (`docs/progress/e18-api.md` §2.1): a scheme names them without a prefix. */
const ESTIMATE_NAME = /^(?:STATUS|EST|KV|NS|NH|NHY|AD|DMIN|PASS|SLOPE|KE|LAGR|SNW|NOCT|ETYPE|P\d{1,2})$/i;
const NAME_TOKEN = /'(?:[^']|'')*'|\[([^\]]*)\]|[\p{L}_][\p{L}\p{N}_]*/gu;

/** The text of a rule with the fields of an estimate written as `<prefix>NAME` (the same renaming the core makes; for the check of a rule). */
export function withPrefix(expr: string, prefix: string): string {
  const p = prefix.trim();
  if (!p) return expr;
  return expr.replace(NAME_TOKEN, (token: string, bracket?: string) => {
    if (token.startsWith("'")) return token;
    if (bracket !== undefined) return ESTIMATE_NAME.test(bracket) ? `[${p}${bracket.toUpperCase()}]` : token;
    return ESTIMATE_NAME.test(token) ? p + token.toUpperCase() : token;
  });
}

export interface RuleRow {
  label: string;
  /** "" — the threshold is not set (the template ГКЗ) */
  expr: string;
}

export interface OverrideRow {
  wireframeId: number | null;
  label: string;
}

export interface ClassifyForm {
  /** the scheme the rules are taken from; "" — the rules are the user's own, not saved */
  schemeId: string;
  rules: RuleRow[];
  /** the field whose empty value means «no estimate»; "" — every block has one */
  estimate: string;
  /** «Префикс полей оценки» as typed: `PASS` of the scheme reads `<prefix>PASS`; "" — no prefix */
  prefix: string;
  out: string;
  /** passes of the majority filter, as typed */
  majority: string;
  overrides: OverrideRow[];
  r: RestrictForm;
  /** the editor of the scheme: the id and the name it is saved under */
  editId: string;
  editName: string;
}

export const DEFAULT_OUT = "CLASS";

export const EMPTY_CLASSIFY: ClassifyForm = { schemeId: "jorc", rules: [], estimate: "", prefix: "", out: DEFAULT_OUT, majority: "0", overrides: [], r: EMPTY_RESTRICT, editId: "", editName: "" };

const bad = (place: string, message: string, hint: string, param: string | null = null) => ({ ok: false as const, issue: issue("classify", place, message, hint, param) });

export const rowsOfScheme = (s: Scheme): RuleRow[] => s.rules.map((r) => ({ label: r.label, expr: r.expr ?? "" }));

const norm = (s: string) => s.replace(/\s+/g, " ").trim();

/** The rules of the table are those of the scheme as it is stored (a rule not set is "" in the table). */
export function sameRules(rows: readonly RuleRow[], scheme: Scheme | undefined): boolean {
  if (!scheme || rows.length !== scheme.rules.length) return false;
  return rows.every((r, i) => norm(r.label) === norm(scheme.rules[i].label) && norm(r.expr) === norm(scheme.rules[i].expr ?? ""));
}

/** The form after the scheme is chosen: its rules are the table; the overrides name the labels of the scheme. */
export function pickScheme(f: ClassifyForm, schemes: readonly Scheme[], id: string): ClassifyForm {
  const s = schemes.find((x) => x.id === id);
  const rules = s ? rowsOfScheme(s) : f.rules;
  const labels = new Set(rules.map((r) => r.label));
  return { ...f, schemeId: id, rules, overrides: f.overrides.filter((o) => labels.has(o.label)), editId: s ? s.id : f.editId, editName: s ? s.name : f.editName };
}

// ---- the form → the arguments of the command ----

function prefixIssue(raw: string): Issue | null {
  const p = raw.trim();
  if (!p || PREFIX_RE.test(p)) return null;
  return issue("classify", "prefix", `Префикс полей оценки — до ${MAX_PREFIX} знаков: буквы, цифры и «_».`, "Например: EST_ или ЗОЛОТО_OK_. Пусто — поля без префикса.", "prefix");
}

/** The arguments of `blocks.classify` from the form: the scheme when the table is the scheme's, else the rules themselves. */
export function argsOfForm(modelId: number | null, f: ClassifyForm, schemes: readonly Scheme[]): { ok: true; args: Args } | { ok: false; issue: Issue } {
  if (modelId === null) return bad("model", "Не выбрана блочная модель.", "Выберите модель в списке.");
  const args: Args = { model_id: modelId };
  const scheme = schemes.find((s) => s.id === f.schemeId);
  if (scheme && sameRules(f.rules, scheme)) args.scheme = scheme.id;
  else {
    if (!f.rules.length) return bad("rules", "В схеме нет правил.", "Выберите схему или добавьте хотя бы одну категорию.");
    for (const [i, r] of f.rules.entries()) {
      if (!r.label.trim()) return bad("rules", `Правило ${i + 1}: не задана подпись категории.`, "Введите подпись, например «Измеренные».", `rules.${i}.label`);
      if (!r.expr.trim()) return bad("rules", `Правило «${r.label.trim()}»: не задано условие.`, "Введите условие 8.7.4, например AU >= 1.", `rules.${i}.expr`);
    }
    args.rules = JSON.stringify(f.rules.map((r) => ({ label: norm(r.label), expr: r.expr })));
  }
  if (f.estimate) args.estimate = f.estimate;
  const px = prefixIssue(f.prefix);
  if (px) return { ok: false, issue: px };
  if (f.prefix.trim()) args.prefix = f.prefix.trim();
  const n = nameIssue("classify", "out", f.out, "Имя поля категорий");
  if (n) return { ok: false, issue: n };
  args.out = f.out.trim();
  const m = f.majority.trim() === "" ? 0 : Number(f.majority.trim());
  if (!Number.isInteger(m) || m < 0 || m > MAX_MAJORITY) return bad("majority", `Проходов мажоритарного фильтра — целое число от 0 до ${MAX_MAJORITY}.`, "0 — без фильтра.");
  if (m) args.majority = m;
  if (f.overrides.length) {
    for (const [i, o] of f.overrides.entries()) {
      if (o.wireframeId === null) return bad("overrides", `Каркас категории ${i + 1}: не выбран каркас.`, "Выберите каркас или удалите строку.", `overrides.${i}.wireframe_id`);
      if (!o.label) return bad("overrides", `Каркас категории ${i + 1}: не выбрана категория.`, "Выберите подпись из категорий схемы.", `overrides.${i}.label`);
    }
    args.overrides = JSON.stringify(f.overrides.map((o) => ({ wireframe_id: o.wireframeId, label: o.label })));
  }
  const r = restrictArgs("classify", f.r);
  if (!r.ok) return r;
  return { ok: true, args: { ...args, ...r.args } };
}

export interface ClassifyRequest {
  modelId: number;
  body: Omit<ClassifyBody, "expected_version">;
}

/** The rules of a text of JSON or a list: [{label, expr}], each checked. */
function rulesOfArg(raw: unknown, param: string): { ok: true; rules: { label: string; expr: string }[] } | { ok: false; issue: Issue } {
  const j = jsonArg(raw);
  if (!j.ok || !Array.isArray(j.value)) return bad("rules", "Правила — не список в JSON.", 'Пример: [{"label": "Руда", "expr": "AU >= 1"}, {"label": "Бедная", "expr": "AU >= 0"}].', param);
  const list = j.value as unknown[];
  if (!list.length) return bad("rules", "В схеме нет правил.", "Добавьте хотя бы одну категорию.", param);
  if (list.length > MAX_RULES) return bad("rules", `В схеме не больше ${MAX_RULES} правил.`, "Сократите список.", param);
  const rules: { label: string; expr: string }[] = [];
  for (const [i, x] of list.entries()) {
    const r = x && typeof x === "object" ? (x as Record<string, unknown>) : {};
    if (typeof r.label !== "string" || !norm(r.label)) return bad("rules", `Правило ${i + 1}: не задана подпись категории.`, "У каждого правила есть подпись и условие.", `${param}.${i}.label`);
    if (norm(r.label).length > MAX_LABEL) return bad("rules", `Правило ${i + 1}: подпись длиннее ${MAX_LABEL} знаков.`, "Сократите подпись.", `${param}.${i}.label`);
    if (typeof r.expr !== "string" || !r.expr.trim()) return bad("rules", `Правило «${norm(r.label)}»: не задано условие.`, "Условие — выражение 8.7.4, например AU >= 1.", `${param}.${i}.expr`);
    rules.push({ label: norm(r.label), expr: r.expr });
  }
  const seen = new Set<string>();
  for (const [i, r] of rules.entries()) {
    const k = r.label.toLowerCase();
    if (seen.has(k)) return bad("rules", `Подпись «${r.label}» повторяется.`, "Подписи категорий различны (без учёта регистра).", `${param}.${i}.label`);
    seen.add(k);
  }
  return { ok: true, rules };
}

/** The request of the arguments (everything is checked here: the scheme or the rules, the numbers, the lists), or the first wrong thing. */
export function requestOfArgs(a: Args): { ok: true; request: ClassifyRequest } | { ok: false; issue: Issue } {
  if (!isInt(a.model_id)) return bad("model", "Не задана блочная модель.", "Передайте model_id — номер модели из раздела «Блочные модели» дерева.");
  const body: ClassifyRequest["body"] = { out: DEFAULT_OUT };
  if (given(a, "scheme") === given(a, "rules")) return bad("rules", "Нужно ровно одно: схема или правила.", "Передайте scheme (jorc, gkz или своя схема) либо rules — список правил в JSON.");
  if (given(a, "scheme")) body.scheme = String(a.scheme).trim();
  else {
    const r = rulesOfArg(a.rules, "rules");
    if (!r.ok) return r;
    body.rules = r.rules;
  }
  if (given(a, "estimate")) body.estimate = String(a.estimate).trim();
  if (given(a, "prefix")) {
    const px = prefixIssue(String(a.prefix));
    if (px) return { ok: false, issue: px };
    if (String(a.prefix).trim()) body.prefix = String(a.prefix).trim();
  }
  if (given(a, "out")) {
    const n = nameIssue("classify", "out", String(a.out), "Имя поля категорий");
    if (n) return { ok: false, issue: n };
    body.out = String(a.out).trim();
  }
  if (given(a, "majority")) {
    const m = typeof a.majority === "number" ? a.majority : Number(String(a.majority).trim());
    if (!Number.isInteger(m) || m < 0 || m > MAX_MAJORITY) return bad("majority", `Проходов мажоритарного фильтра — целое число от 0 до ${MAX_MAJORITY}.`, "0 — без фильтра.");
    body.majority = m;
  }
  if (given(a, "overrides")) {
    const j = jsonArg(a.overrides);
    if (!j.ok || !Array.isArray(j.value)) return bad("overrides", "Каркасы категорий — не список в JSON.", 'Пример: [{"wireframe_id": 7, "label": "Измеренные"}].', "overrides");
    const list: { wireframe_id: number; label: string }[] = [];
    for (const [i, x] of (j.value as unknown[]).entries()) {
      const o = x && typeof x === "object" ? (x as Record<string, unknown>) : {};
      if (!isInt(o.wireframe_id) || o.wireframe_id < 1) return bad("overrides", `Каркас категории ${i + 1}: нужен номер каркаса.`, "Номера каркасов — в разделе «Каркасы» дерева.", `overrides.${i}.wireframe_id`);
      if (typeof o.label !== "string" || !o.label.trim()) return bad("overrides", `Каркас категории ${i + 1}: не задана категория.`, "Назовите подпись из правил схемы.", `overrides.${i}.label`);
      list.push({ wireframe_id: o.wireframe_id, label: o.label.trim() });
    }
    if (list.length) body.overrides = list;
  }
  const r = restrictOfArgs("classify", a);
  if (!r.ok) return r;
  Object.assign(body, r.body);
  if (a.replace !== undefined && a.replace !== null) {
    if (typeof a.replace !== "boolean") return bad("general", "Замена — true или false.", "Передайте replace: true, чтобы заменить существующее поле.");
    body.replace = a.replace;
  }
  return { ok: true, request: { modelId: a.model_id, body } };
}

/** The form that shows the arguments of a call (the window then reads the same request the core got). */
export function formOfArgs(a: Args, schemes: readonly Scheme[], keep: ClassifyForm = EMPTY_CLASSIFY): ClassifyForm {
  let out: ClassifyForm = { ...keep, r: restrictFormOfArgs(a, keep.r) };
  if (given(a, "scheme")) out = pickScheme(out, schemes, String(a.scheme).trim());
  else if (given(a, "rules")) {
    const j = jsonArg(a.rules);
    if (j.ok && Array.isArray(j.value)) {
      const rows = (j.value as unknown[]).map((x) => {
        const r = x && typeof x === "object" ? (x as Record<string, unknown>) : {};
        return { label: String(r.label ?? ""), expr: String(r.expr ?? "") };
      });
      // the rules of a call that are exactly a scheme's show as that scheme
      const same = schemes.find((s) => sameRules(rows, s));
      out = { ...out, schemeId: same?.id ?? "", rules: rows };
    }
  }
  if (given(a, "estimate")) out.estimate = String(a.estimate).trim();
  if (given(a, "prefix")) out.prefix = String(a.prefix).trim();
  if (given(a, "out")) out.out = String(a.out).trim();
  if (given(a, "majority")) out.majority = String(a.majority).trim();
  if (given(a, "overrides")) {
    const j = jsonArg(a.overrides);
    if (j.ok && Array.isArray(j.value)) {
      out.overrides = (j.value as unknown[]).map((x) => {
        const o = x && typeof x === "object" ? (x as Record<string, unknown>) : {};
        return { wireframeId: typeof o.wireframe_id === "number" ? o.wireframe_id : null, label: String(o.label ?? "") };
      });
    }
  }
  return out;
}

// ---- the schemes of the project ----

export interface SchemeRequest {
  id: string;
  name: string;
  rules: SchemeRule[];
  /** the version the write is made over; undefined — a new scheme */
  version?: number;
}

/** The arguments of `blocks.scheme.save` from the editor of the window. */
export function schemeArgsOfForm(f: ClassifyForm, schemes: readonly Scheme[]): { ok: true; args: Args } | { ok: false; issue: Issue } {
  const id = f.editId.trim();
  if (!SCHEME_ID.test(id)) return bad("scheme_id", "Код схемы — до 40 латинских букв, цифр, «_» и «-».", "Например: jorc, my-scheme.");
  if (!f.editName.trim()) return bad("scheme_name", "У схемы нет названия.", "Введите название, например «Моя схема».");
  if (!f.rules.length) return bad("rules", "В схеме нет правил.", "Добавьте хотя бы одну категорию.");
  for (const [i, r] of f.rules.entries()) if (!r.label.trim()) return bad("rules", `Правило ${i + 1}: не задана подпись категории.`, "Введите подпись, например «Измеренные».", `rules.${i}.label`);
  const args: Args = { id, name: f.editName.trim(), rules: JSON.stringify(f.rules.map((r) => ({ label: norm(r.label), expr: r.expr.trim() ? r.expr : null }))) };
  const have = schemes.find((s) => s.id === id);
  if (have) args.expected_version = have.version;
  return { ok: true, args };
}

/** The request of the arguments of `blocks.scheme.save`; an unset rule (`expr` null or "") stays unset: the template ГКЗ is saved. */
export function schemeRequestOfArgs(a: Args, schemes: readonly Scheme[]): { ok: true; request: SchemeRequest } | { ok: false; issue: Issue } {
  if (!given(a, "id") || !SCHEME_ID.test(String(a.id).trim())) return bad("scheme_id", "Код схемы — до 40 латинских букв, цифр, «_» и «-».", "Например: jorc, my-scheme.");
  const id = String(a.id).trim();
  if (!given(a, "name") || !norm(String(a.name))) return bad("scheme_name", "У схемы нет названия.", "Передайте name, например «Моя схема».");
  if (norm(String(a.name)).length > MAX_SCHEME_NAME) return bad("scheme_name", `Название схемы длиннее ${MAX_SCHEME_NAME} знаков.`, "Сократите название.");
  const j = jsonArg(a.rules);
  if (!j.ok || !Array.isArray(j.value) || !j.value.length) return bad("rules", "В схеме нет правил.", 'Передайте rules — список в JSON: [{"label": "A", "expr": "AU >= 1"}, {"label": "B", "expr": null}].', "rules");
  if (j.value.length > MAX_RULES) return bad("rules", `В схеме не больше ${MAX_RULES} правил.`, "Сократите список.", "rules");
  const rules: SchemeRule[] = [];
  for (const [i, x] of (j.value as unknown[]).entries()) {
    const r = x && typeof x === "object" ? (x as Record<string, unknown>) : {};
    if (typeof r.label !== "string" || !norm(r.label)) return bad("rules", `Правило ${i + 1}: не задана подпись категории.`, "У каждого правила есть подпись; условие может быть пустым (порог не задан).", `rules.${i}.label`);
    const expr = typeof r.expr === "string" && r.expr.trim() ? r.expr : null;
    rules.push({ label: norm(r.label), expr });
  }
  const have = schemes.find((s) => s.id === id);
  const version = isInt(a.expected_version) ? a.expected_version : have?.version;
  return { ok: true, request: { id, name: norm(String(a.name)), rules, ...(version !== undefined ? { version } : {}) } };
}
