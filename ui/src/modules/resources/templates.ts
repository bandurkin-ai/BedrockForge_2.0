/**
 * The templates of the resource report as the window «Шаблоны форм отчёта» has them (ТЗ 8.10 «Формы»; ADR 0027 решения 16–19, № 105, 106;
 * docs/progress/e20-api.md): the body of the core (`GET /resources/templates/{id}`), the draft the editor changes, the conversions both ways
 * and the checks (the same as the core's `templates.check_body` — a wrong template is told here in Russian with a hint and the place in
 * the editor, so nothing wrong reaches the core; the core checks again). A template is the SHOW of a report: nothing is recalculated. No React.
 */
import { num, obj, str } from "./answers";

export type Metric = "tonnes" | "volume" | "grade" | "metal";
export const METRICS: readonly Metric[] = ["tonnes", "volume", "grade", "metal"];
export const METRIC_TITLES: Record<Metric, string> = { tonnes: "Тоннаж (руда)", volume: "Объём", grade: "Содержание", metal: "Металл" };

/** The units of the core (`templates.py`): code → [the label for the user, the factor from the base unit]. */
export const TONNES_UNITS: Record<string, [string, number]> = { t: ["т", 1], kt: ["тыс. т", 1e-3], mt: ["млн т", 1e-6] };
export const VOLUME_UNITS: Record<string, [string, number]> = { m3: ["м³", 1], km3: ["тыс. м³", 1e-3] };
/** The troy ounce, g (31.25 kg = 1.004711 koz). */
export const TROY_OUNCE_G = 31.1034768;
export const METAL_UNITS: Record<string, [string, number]> = { g: ["г", 1], kg: ["кг", 1e-3], t: ["т", 1e-6], koz: ["тыс. унц", 1e-3 / TROY_OUNCE_G] };
export type MetalBase = "g" | "t";
export const METAL_BASES: readonly MetalBase[] = ["g", "t"];
/** The base unit of the metal by the unit of the grade (the core: `forms.grade_unit`). */
export const BASE_OF: Record<string, MetalBase> = { "г/т": "g", "g/t": "g", ppm: "g", ppb: "g", "%": "t" };

/** The roles of the keys of a report (a field of the model has one; the bench is «bench»). */
export const ROLES: [string, string][] = [
  ["class", "категория (класс)"],
  ["domain", "домен (тело)"],
  ["bench", "уступ"],
];
export const roleTitle = (role: string): string => ROLES.find(([r]) => r === role)?.[1] ?? role;

export const OTHER_LABEL = "Прочие";
export const MAX_LABELS = 64;
export const MAX_SUBTOTALS = 16;
export const MAX_NAME = 100;
export const MAX_LABEL = 100;
export const DEFAULT_TOTAL = "Итого";
export const ID_PATTERN = /^[A-Za-z0-9_-]{1,40}$/;

export interface Subtotal {
  label: string;
  members: string[];
}

export interface TemplateBody {
  id: string;
  name: string;
  version: number;
  builtin: boolean;
  labels: string[];
  subtotals: Subtotal[];
  total: string | null;
  key: string;
  section: string | null;
  columns: Metric[];
  balance: boolean;
  units: { tonnes: string; volume: string; metal: Record<MetalBase, string> };
  decimals: Record<Metric, number>;
}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

/** `GET /resources/templates/{id}` → the template; null — not a template. The numbers that are not numbers fall to the defaults of the core. */
export function readTemplate(raw: unknown): TemplateBody | null {
  const t = obj(obj(raw).template);
  if (typeof t.id !== "string") return null;
  const u = obj(t.units);
  const m = u.metal;
  const metal = typeof m === "string" ? { g: m, t: m } : { g: str(obj(m).g) ?? "koz", t: str(obj(m).t) ?? "t" };
  const d = obj(t.decimals);
  const dec = (k: Metric, def: number) => num(d[k]) ?? def;
  const columns = strings(t.columns).filter((c): c is Metric => (METRICS as readonly string[]).includes(c));
  return {
    id: t.id,
    name: str(t.name) ?? t.id,
    version: num(t.version) ?? 1,
    builtin: t.builtin === true,
    labels: strings(t.labels),
    subtotals: (Array.isArray(t.subtotals) ? t.subtotals : []).map((s) => ({ label: str(obj(s).label) ?? "", members: strings(obj(s).members) })),
    total: typeof t.total === "string" ? t.total : null,
    key: str(t.key) ?? "class",
    section: str(t.section),
    columns: columns.length ? columns : ["tonnes", "grade", "metal"],
    balance: t.balance === true,
    units: { tonnes: str(u.tonnes) ?? "kt", volume: str(u.volume) ?? "km3", metal },
    decimals: { tonnes: dec("tonnes", 0), volume: dec("volume", 0), grade: dec("grade", 2), metal: dec("metal", 1) },
  };
}

// ---- the draft ----

/** What the editor changes: all four metrics in order with their switches, the total with its switch, the decimals as numbers. */
export interface Draft {
  /** null — a new template (the code is made on the save) */
  id: string | null;
  version: number | null;
  builtin: boolean;
  name: string;
  labels: string[];
  subtotals: Subtotal[];
  hasTotal: boolean;
  total: string;
  key: string;
  /** "" — no sections */
  section: string;
  order: Metric[];
  on: Record<Metric, boolean>;
  balance: boolean;
  units: TemplateBody["units"];
  decimals: Record<Metric, number>;
}

export const NEW_DRAFT: Draft = {
  id: null,
  version: null,
  builtin: false,
  name: "",
  labels: [""],
  subtotals: [],
  hasTotal: true,
  total: DEFAULT_TOTAL,
  key: "class",
  section: "",
  order: [...METRICS],
  on: { tonnes: true, volume: false, grade: true, metal: true },
  balance: false,
  units: { tonnes: "kt", volume: "km3", metal: { g: "koz", t: "t" } },
  decimals: { tonnes: 0, volume: 0, grade: 2, metal: 1 },
};

const copyUnits = (u: TemplateBody["units"]): TemplateBody["units"] => ({ ...u, metal: { ...u.metal } });

export function draftOf(t: TemplateBody): Draft {
  return {
    id: t.id,
    version: t.version,
    builtin: t.builtin,
    name: t.name,
    labels: [...t.labels],
    subtotals: t.subtotals.map((s) => ({ label: s.label, members: [...s.members] })),
    hasTotal: t.total !== null,
    total: t.total ?? DEFAULT_TOTAL,
    key: t.key,
    section: t.section ?? "",
    order: [...t.columns, ...METRICS.filter((m) => !t.columns.includes(m))],
    on: { tonnes: t.columns.includes("tonnes"), volume: t.columns.includes("volume"), grade: t.columns.includes("grade"), metal: t.columns.includes("metal") },
    balance: t.balance,
    units: copyUnits(t.units),
    decimals: { ...t.decimals },
  };
}

/** «Создать копию»: the same rows, units and decimals as a new template of the user (the name is «<имя> (копия)», free among `taken`). */
export function copyDraft(t: TemplateBody, taken: readonly string[] = []): Draft {
  const d = draftOf(t);
  let name = `${t.name} (копия)`;
  for (let n = 2; taken.some((x) => x.toLocaleLowerCase() === name.toLocaleLowerCase()); n++) name = `${t.name} (копия ${n})`;
  return { ...d, id: null, version: null, builtin: false, name };
}

/** The labels of the rows as the draft has them without the empty ones (a half-typed new row is not a label yet). */
const typed = (labels: readonly string[]): string[] => labels.map((l) => l.trim()).filter(Boolean);

/** A label renamed: the subtotals that name it follow it. */
export function renameLabel(d: Draft, i: number, text: string): Draft {
  const old = d.labels[i];
  return { ...d, labels: d.labels.map((l, j) => (j === i ? text : l)), subtotals: d.subtotals.map((s) => ({ ...s, members: s.members.map((m) => (m === old ? text : m)) })) };
}

/** A label taken out: it leaves the subtotals too (a subtotal left with one member is told by the check). */
export function removeLabel(d: Draft, i: number): Draft {
  const old = d.labels[i];
  return { ...d, labels: d.labels.filter((_, j) => j !== i), subtotals: d.subtotals.map((s) => ({ ...s, members: s.members.filter((m) => m !== old) })) };
}

/** An item of a list moved to a place (the buttons ↑ ↓ and the dragging); out of the list — the list as it is. */
export function moveItem<T>(list: readonly T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return [...list];
  const out = [...list];
  const [x] = out.splice(from, 1);
  out.splice(to, 0, x);
  return out;
}

/** A subtotal's member switched on / off. */
export function toggleMember(d: Draft, si: number, label: string, on: boolean): Draft {
  return { ...d, subtotals: d.subtotals.map((s, i) => (i === si ? { ...s, members: on ? (s.members.includes(label) ? s.members : [...s.members, label]) : s.members.filter((m) => m !== label) } : s)) };
}

// ---- the check ----

export type Place = "name" | "labels" | "subtotals" | "total" | "key" | "section" | "columns" | "units" | "decimals" | "general";

/** The first wrong thing: the place in the editor, the words, the hint (as `{code, message, hint}` of the core). */
export interface Issue {
  place: Place;
  message: string;
  hint: string;
  /** the index of the row or the subtotal when the core names one (`labels.2`) */
  index: number | null;
}

const issue = (place: Place, message: string, hint: string, index: number | null = null): Issue => ({ place, message, hint, index });

/** Where a `param` of a refusal of the core points in the editor: `labels.2` → the rows, the row 2. */
export function placeOfParam(param: string | null): { place: Place; index: number | null } {
  const head = (param ?? "").split(".");
  const at = ["name", "labels", "subtotals", "total", "key", "section", "columns", "units", "decimals"].find((p) => p === head[0]);
  const n = Number(head[1]);
  return { place: (at as Place | undefined) ?? "general", index: Number.isInteger(n) ? n : null };
}

const fold = (s: string): string => s.trim().replace(/\s+/g, " ").toLocaleLowerCase("ru-RU");

export type Checked = { ok: true; body: Record<string, unknown> } | { ok: false; issue: Issue };

/** The draft → the body of `PUT` (without `id` and `version` — they are the path and `expected_version`); the first wrong thing otherwise. */
export function bodyOfDraft(d: Draft): Checked {
  const fail = (i: Issue): Checked => ({ ok: false, issue: i });
  const name = d.name.trim().replace(/\s+/g, " ");
  if (!name || name.length > MAX_NAME) return fail(issue("name", `Название шаблона — от 1 до ${MAX_NAME} знаков.`, "Введите название, например «ГКЗ, участок 2»."));
  const labels = typed(d.labels).map((l) => l.replace(/\s+/g, " "));
  if (!labels.length) return fail(issue("labels", "В шаблоне нет строк.", "Добавьте хотя бы одну подпись группы."));
  if (labels.length > MAX_LABELS) return fail(issue("labels", `В шаблоне не больше ${MAX_LABELS} строк.`, "Уберите лишние строки."));
  for (let i = 0; i < labels.length; i++) {
    if (labels[i].length > MAX_LABEL) return fail(issue("labels", `Подпись строки — от 1 до ${MAX_LABEL} знаков.`, "Сократите подпись.", i));
    if (fold(labels[i]) === fold(OTHER_LABEL)) return fail(issue("labels", `Подпись «${OTHER_LABEL}» занята строкой групп вне шаблона.`, "Назовите строку иначе.", i));
    if (labels.findIndex((x) => fold(x) === fold(labels[i])) !== i) return fail(issue("labels", `Подпись «${labels[i]}» повторяется.`, "Подписи строк не повторяются.", i));
  }
  if (d.subtotals.length > MAX_SUBTOTALS) return fail(issue("subtotals", `В шаблоне не больше ${MAX_SUBTOTALS} подытогов.`, "Уберите лишние подытоги."));
  const taken = new Set([...labels.map(fold), fold(OTHER_LABEL)]);
  const subtotals: Subtotal[] = [];
  for (let i = 0; i < d.subtotals.length; i++) {
    const s = d.subtotals[i];
    const label = s.label.trim().replace(/\s+/g, " ");
    if (!label || label.length > MAX_LABEL) return fail(issue("subtotals", `Подпись подытога — от 1 до ${MAX_LABEL} знаков.`, "Введите подпись, например «A + B + C1».", i));
    if (taken.has(fold(label))) return fail(issue("subtotals", `Подпись «${label}» повторяется.`, "Подписи строк и подытогов не повторяются.", i));
    taken.add(fold(label));
    // the members in the order of the rows
    const members = labels.filter((l) => s.members.some((m) => fold(m) === fold(l)));
    if (members.length < 2) return fail(issue("subtotals", `Подытог «${label}» объединяет меньше двух строк.`, "Отметьте в подытоге не меньше двух строк шаблона.", i));
    subtotals.push({ label, members });
  }
  let total: string | null = null;
  if (d.hasTotal) {
    total = d.total.trim().replace(/\s+/g, " ");
    if (!total || total.length > MAX_LABEL) return fail(issue("total", `Подпись итога — от 1 до ${MAX_LABEL} знаков.`, "Введите подпись или отключите итоговую строку."));
    if (taken.has(fold(total))) return fail(issue("total", `Подпись «${total}» повторяется.`, "Подписи строк, подытогов и итога не повторяются."));
  }
  if (d.section && d.section === d.key) return fail(issue("section", "Разделы и строки не могут идти по одному ключу.", "Выберите другую роль для разделов."));
  const columns = d.order.filter((m) => d.on[m]);
  if (!columns.length) return fail(issue("columns", "Не выбран ни один столбец.", "Отметьте хотя бы один: тоннаж, объём, содержание или металл."));
  for (const m of METRICS) {
    const v = d.decimals[m];
    if (!Number.isInteger(v) || v < 0 || v > 6) return fail(issue("decimals", "Знаков после запятой — целое от 0 до 6.", "Выберите число из списка."));
  }
  return {
    ok: true,
    body: {
      name,
      labels,
      subtotals,
      total,
      key: d.key,
      section: d.section || null,
      columns,
      balance: d.balance,
      units: { tonnes: d.units.tonnes, volume: d.units.volume, metal: { ...d.units.metal } },
      decimals: { ...d.decimals },
    },
  };
}

/** Does the draft differ from the template it was made of (the editor asks before it drops the changes). */
export function isChanged(d: Draft, base: TemplateBody | null): boolean {
  if (!base) return d.id === null;
  const a = bodyOfDraft(d);
  const b = bodyOfDraft(draftOf(base));
  return JSON.stringify(a.ok ? a.body : d) !== JSON.stringify(b.ok ? b.body : base);
}

// ---- the code of a new template ----

const TRANSLIT: Record<string, string> = { а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "kh", ц: "ts", ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya" };

/** The code of a new template from its name (latin letters, digits, «-», ≤ 40), free among `taken` (a code of the box is never made). */
export function newTemplateId(name: string, taken: readonly string[]): string {
  const slug =
    [...name.toLocaleLowerCase("ru-RU")]
      .map((c) => TRANSLIT[c] ?? c)
      .join("")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 34) || "template";
  let id = slug;
  for (let n = 2; taken.includes(id) || taken.some((t) => t.toLowerCase() === id); n++) id = `${slug.slice(0, 34)}-${n}`;
  return id;
}

/** Text of the units of a template for the list and the assistant: «тыс. т, тыс. м³, металл: г/т → тыс. унц». */
export function unitsText(t: Pick<TemplateBody, "units">): string {
  return `${TONNES_UNITS[t.units.tonnes]?.[0] ?? t.units.tonnes}, ${VOLUME_UNITS[t.units.volume]?.[0] ?? t.units.volume}; металл: г/т → ${METAL_UNITS[t.units.metal.g]?.[0] ?? t.units.metal.g}, % → ${METAL_UNITS[t.units.metal.t]?.[0] ?? t.units.metal.t}`;
}
