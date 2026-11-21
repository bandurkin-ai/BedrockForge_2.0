/**
 * The tools of «Анализ → Пересечения» by the operation of a result (ADR 0021 §8, §11): `tt` — «Истинная мощность»,
 * `intercepts` — «Рудные пересечения» (their windows, not the windows of Э12), and the link with Д-26 (decision 16:
 * Д-26 takes TT as a numeric field of the thickness): the field TT is offered first in «Мощность пробы», with the
 * mark «истинная мощность» and «устарел» of its result. Pure: no React.
 */
import type { FieldRow, ResultRow } from "../composites/api";
import type { ToolSpec } from "../composites/params";
import { staleText } from "../composites/results";
import { RUN_TOOL, TT_TOOL } from "./params";

/** dh_results.op → the tool of its window. */
export const OP_TOOLS: Readonly<Record<string, ToolSpec>> = { tt: TT_TOOL, intercepts: RUN_TOOL };

export const isInterceptOp = (op: string | null | undefined): boolean => typeof op === "string" && Object.prototype.hasOwnProperty.call(OP_TOOLS, op);

export const interceptToolOfOp = (op: string | null | undefined): ToolSpec | null => (isInterceptOp(op) ? OP_TOOLS[op as string] : null);

// ---- Д-26: TT as the thickness of a sample ----

/** The field «Истинная мощность» writes (ADR 0021 §8). */
export const TT_FIELD = "TT";

export const isTtField = (f: Pick<FieldRow, "name" | "kind">): boolean => f.kind === "number" && f.name.trim().toUpperCase() === TT_FIELD;

/** The fields with TT first (the others keep their order); no TT — the same list. */
export function ttFirst<T extends Pick<FieldRow, "name" | "kind">>(fields: readonly T[]): T[] {
  const i = fields.findIndex(isTtField);
  return i <= 0 ? [...fields] : [fields[i], ...fields.slice(0, i), ...fields.slice(i + 1)];
}

/** The newest result «Истинная мощность» of the source table (`table` — its key, the id as text). */
export function ttResultOf(results: readonly ResultRow[], table: string | null): ResultRow | null {
  if (!table) return null;
  return results.find((r) => r.op === "tt" && r.table_id !== null && String(r.table_id) === table) ?? null;
}

export interface TtHint {
  /** the name of the field TT as the table stores it */
  field: string;
  text: string;
  /** «Взять TT» is offered: TT is there and not chosen */
  pick: boolean;
  /** the result that wrote TT is out of date */
  stale: boolean;
}

/**
 * The line under «Мощность пробы» of Д-26: the table has TT — offered first; chosen — said so; its result out of
 * date — said why. No TT — nothing (the length along the hole or another field, as before).
 */
export function ttThicknessHint(fields: readonly Pick<FieldRow, "name" | "kind">[], chosen: unknown, tt: ResultRow | null): TtHint | null {
  const f = fields.find(isTtField);
  if (!f) return null;
  const picked = typeof chosen === "string" && chosen.trim() === f.name;
  const stale = tt ? staleText(tt) : null;
  const tail = stale ? ` Результат «${tt?.name || "Истинная мощность"}» ${stale} — перестройте его в дереве («Обработка проб»).` : "";
  const text = picked ? `Мощность пробы — поле ${f.name}, истинная мощность (ТЗ 8.5).${tail}` : `В таблице есть поле ${f.name} — истинная мощность (ТЗ 8.5); можно взять его мощностью пробы.${tail}`;
  return { field: f.name, text, pick: !picked, stale: stale !== null };
}
