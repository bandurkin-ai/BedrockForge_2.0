/** «Эквивалент металла» (ТЗ 8.2.2; ADR 0018 §7): Eq = g₁ + Σ g_e·(P_e·R_e·k_e)/(P₁·R₁·k₁) — a field of the source
 * table. `others` — «ПОЛЕ цена извлечение» through «;». Stub of 12.25 (12.28). */
import { parseNumber, type ToolSpec } from "../params";

/** «CU 9000 0,9; AG 25 0,8» → [{field, price, recovery}]; a part that is not «поле цена извлечение» — a reason. */
export function parseOthers(text: string): { field: string; price: number; recovery: number }[] | string {
  const out: { field: string; price: number; recovery: number }[] = [];
  for (const part of text.split(";").map((x) => x.trim()).filter(Boolean)) {
    const [field, p, r, ...rest] = part.split(/\s+/);
    const price = parseNumber(p ?? "");
    const recovery = parseNumber(r ?? "");
    if (!field || price === null || recovery === null || rest.length || price <= 0 || recovery <= 0 || recovery > 1)
      return `«Прочие металлы»: «${part}» — нужно «ПОЛЕ цена извлечение», извлечение 0–1`;
    out.push({ field, price, recovery });
  }
  return out.length ? out : "«Прочие металлы»: укажите хотя бы один";
}

export const equivalent: ToolSpec = {
  id: "equivalent",
  command: "composite.equivalent",
  title: "Эквивалент металла",
  endpoint: "/equivalent",
  preview: () => "equivalent",
  source: { table: "one", fields: "none", domain: false },
  params: [
    { key: "base", label: "Основной металл", kind: "field", numeric: true },
    { key: "base_price", label: "Цена основного", kind: "number", min: 0 },
    { key: "base_recovery", label: "Извлечение основного", kind: "number", min: 0, max: 1, default: 0.9 },
    { key: "others", label: "Прочие металлы", kind: "text", hint: "ПОЛЕ цена извлечение; …, например «CU 9000 0,9»" },
    { key: "nan_zero", label: "Пустое содержание считать нулём", kind: "boolean", default: false },
    { key: "name", label: "Имя поля", kind: "text", optional: true, hint: "пусто — ‹основной›EQ" },
  ],
  required: ["set_id", "table", "base", "others"],
  writes: "fields",
  note: "Единицы и перевод k — по единицам полей (г/т и $/унц — 1/31,1034768; % и $/т — 0,01).",
  finish: (body) => {
    if (typeof body.others !== "string") return body;
    const others = parseOthers(body.others);
    return typeof others === "string" ? others : { ...body, others };
  },
};
