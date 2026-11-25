/**
 * The words of the properties of the fields (ТЗ 8.7.2, 8.7.5; ADR 0023 §2, §9; № 74): the roles of a field, where it came
 * from, «Где используется», why a field cannot be deleted, why it cannot be renamed, and the line the log and the assistant
 * read of a field. Russian, short. No React.
 */
import type { FieldRow, Usage } from "./api";

export const ROLE_TITLES: Record<string, string> = { grade: "содержание", density: "плотность", frac: "доля объёма", code: "код каркаса", class: "категория" };

/** The roles a field of a kind may take (a number — grade, density, frac; a category — code, class). */
export const rolesFor = (kind: FieldRow["kind"]): string[] => (kind === "number" ? ["grade", "density", "frac"] : ["code", "class"]);

export const roleTitle = (role: string | null): string => (role ? (ROLE_TITLES[role] ?? role) : "без роли");

/** № 74: a field is renamed by a new field of the calculator and the deletion of the old one. */
export const RENAME_HINT = "Переименовать поле нельзя: его имя — ключ файлов модели, фильтров, видов и выражений. Создайте поле калькулятором под новым именем и удалите старое.";

const METHOD_TITLE: Record<string, string> = { constant: "постоянная", field: "по полю", table: "таблица по домену", formula: "формула" };

/** Where the field came from: the file of the model, or the operation that made it with its main parameter. */
export function sourceText(f: FieldRow): string {
  const s = f.source;
  if (!s) return "из файла модели";
  const p = s.params;
  const text = (v: unknown): string => (typeof v === "string" ? v : "");
  if (s.op === "calc") return `Калькулятор: ${text(p.text) || text(p.expr)}`;
  if (s.op === "density") return `Плотность (${METHOD_TITLE[text(p.method)] ?? text(p.method)})`;
  if (s.op === "classify") return text(p.scheme) ? `Категории по схеме «${text(p.scheme)}»` : "Категории по своим правилам";
  if (s.op === "fill" || s.op === "code") return "Заполнение каркасами";
  if (s.op === "regularize") return "Регуляризация";
  return s.op;
}

const USE_ROLE: Record<string, string> = { colour: "раскраска", restrictor: "быстрый ограничитель", filter: "фильтр библиотеки читает поле", source: "считается из этого поля" };

/** One consumer as a line: «поле «AU2» считается из этого поля», «Вид проекта · Модель: раскраска». */
export function usageLine(u: Usage): string {
  const what = USE_ROLE[u.role] ?? u.role;
  return u.kind === "field" ? `поле «${u.name}» ${what}` : `${u.name}: ${what}`;
}

/** «AU2, GRADE и ещё 3» — the consumers as a line of text (up to `max`). */
export function usageNames(list: readonly Usage[], max = 5): string {
  const names = [...new Set(list.map((u) => `«${u.name}»`))];
  return names.length > max ? `${names.slice(0, max).join(", ")} и ещё ${names.length - max}` : names.join(", ");
}

/** Why the field cannot be deleted now; null — it can. */
export function deleteReason(f: FieldRow): string | null {
  if (f.required) return `Поле «${f.name}» задаёт объём блоков. Снимите роль «доля объёма», затем удалите его.`;
  if (f.usage.length) return `Поле «${f.name}» используется: ${usageNames(f.usage)}. Пока ссылки есть, оно не удаляется.`;
  return null;
}

/** One field of the list for the assistant: «AU: число f8, г/т, роль: содержание, используется: 10». */
export function fieldBrief(f: FieldRow): string {
  const parts = [f.kind === "category" ? `категория, подписей ${f.labelsCount}` : `число ${f.dtype}`];
  if (f.unit) parts.push(f.unit);
  if (f.role) parts.push(`роль: ${roleTitle(f.role)}`);
  if (f.stale) parts.push("устарел");
  if (f.usage.length) parts.push(`используется: ${f.usage.length}`);
  return `${f.name}: ${parts.join(", ")}`;
}

/** The field for the log and the assistant: the kind, the unit, the role, the maker, the state, where it is used. */
export function fieldLine(f: FieldRow, model: string): string {
  const kind = f.kind === "category" ? `категория, подписей ${f.labelsCount}` : `число ${f.dtype}`;
  const parts = [kind, f.unit ? `единица ${f.unit}` : "единица не задана", `роль: ${roleTitle(f.role)}`];
  if (f.description) parts.push(`описание: ${f.description}`);
  parts.push(`источник: ${sourceText(f)}`);
  if (f.stale) parts.push(`устарел${f.staleReason ? ` (${f.staleReason})` : ""}`);
  parts.push(f.usage.length ? `используется: ${f.usage.map(usageLine).join("; ")}` : "нигде не используется");
  return `Поле «${f.name}» модели «${model}»: ${parts.join("; ")}`;
}
