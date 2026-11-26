/**
 * The form of the window «Углы бортов карьера» (ТЗ 8.11.2, 8.11.5; ADR 0028 решения 4, 9–12, 29; docs/progress/e21-api.md §2, §16) and its conversions: the form → `params.slopes`
 * and `params.k`, the `params` of a scenario → the form, the flat arguments of `pit.slopes` ↔ the form. The slope angle is a table of sectors {azimuth, angle} (up to 16;
 * the azimuth is from the north clockwise, [0; 360); the angle 10…80°); the domains (a category field; a label has its own table, up to 16 of them) let the angle differ by the
 * lower block of an arc; K is the number of levels of the template (auto: the least from 8 to 20, or 1…20); the tolerance of the angle error is 0,5…10°. Everything wrong is told
 * here in Russian with a hint and the place in the form — nothing wrong reaches the core. No React.
 */
import { numberText, parseDecimal } from "../blocks/report/state";
import { DEFAULT_LIMITS, list, obj, type DomainParam, type Limits, type PitParams, type SectorParam } from "./answers";
import { has, issue, txt, type Args, type FormIssue, type Got } from "./forms";

export const MIN_ANGLE = 10;
export const MAX_ANGLE = 80;
export const MIN_TOLERANCE = 0.5;
export const MAX_TOLERANCE = 10;
export const MAX_K = 20;

export interface SectorRow {
  az: string;
  angle: string;
}

export interface DomainRow {
  label: string;
  table: SectorRow[];
}

export interface SlopesForm {
  table: SectorRow[];
  domainOn: boolean;
  domainField: string;
  /** the domains that have their own table (a label without a row uses the common table) */
  domains: DomainRow[];
  kAuto: boolean;
  k: string;
  tolerance: string;
}

export const newSector = (): SectorRow => ({ az: "", angle: "" });
export const EMPTY_SLOPES: SlopesForm = { table: [{ az: "0", angle: "45" }], domainOn: false, domainField: "", domains: [], kAuto: true, k: "", tolerance: "2,5" };

const rowsOf = (t: readonly SectorParam[]): SectorRow[] => t.map((s) => ({ az: numberText(s.az), angle: numberText(s.angle) }));

/** The form that shows the slopes of `p` (a body, the defaults or the params of a scenario). */
export function slopesFormOfParams(p: PitParams): SlopesForm {
  return {
    table: rowsOf(p.slopes.table),
    domainOn: p.slopes.domain_field !== null,
    domainField: p.slopes.domain_field ?? "",
    domains: p.slopes.domains.map((d: DomainParam) => ({ label: d.label, table: rowsOf(d.table) })),
    kAuto: p.k === null,
    k: p.k === null ? "" : String(p.k),
    tolerance: numberText(p.slopes.tolerance),
  };
}

// ---- the form → the part of the params ----

export type SlopesPatch = Pick<PitParams, "k"> & { slopes: PitParams["slopes"] };

/** The table of sectors: 1…16 rows, the azimuth [0; 360) (a single row may be without it: a constant angle), the angle 10…80°, no azimuth twice. */
export function sectorsPatch(rows: readonly SectorRow[], field: FormIssue["field"], at: string, who: string, limits: Limits = DEFAULT_LIMITS): { ok: true; value: SectorParam[] } | { ok: false; issue: FormIssue } {
  if (!rows.length) return { ok: false, issue: issue(field, `${who}: нет ни одного сектора.`, "Добавьте сектор: азимут от севера по часовой стрелке и угол борта.", at) };
  if (rows.length > limits.sectors) return { ok: false, issue: issue(field, `${who}: секторов больше ${limits.sectors}.`, "Объедините соседние секторы: их не больше 16.", at) };
  const out: SectorParam[] = [];
  for (const [i, r] of rows.entries()) {
    const angle = parseDecimal(r.angle);
    if (angle === null) return { ok: false, issue: issue(field, `${who}: угол сектора ${i + 1} — не число.`, "Угол борта — число от 10 до 80°, например 45.", `${at}.${i}.angle`) };
    if (angle < MIN_ANGLE || angle > MAX_ANGLE) return { ok: false, issue: issue(field, `${who}: угол сектора ${i + 1} (${numberText(angle)}°) вне диапазона ${MIN_ANGLE}…${MAX_ANGLE}°.`, "Углы бортов карьера — от 10 до 80°.", `${at}.${i}.angle`) };
    let az = 0;
    if (r.az.trim() || rows.length > 1) {
      const v = parseDecimal(r.az);
      if (v === null) return { ok: false, issue: issue(field, `${who}: азимут сектора ${i + 1} — не число.`, "Азимут — градусы от севера по часовой стрелке, от 0 до 360 (360 не входит).", `${at}.${i}.az`) };
      if (v < 0 || v >= 360) return { ok: false, issue: issue(field, `${who}: азимут сектора ${i + 1} (${numberText(v)}°) вне диапазона [0; 360).`, "Азимут — от 0 до 360° (360 не входит: это тот же север).", `${at}.${i}.az`) };
      az = v;
    }
    if (out.some((s) => s.az === az)) return { ok: false, issue: issue(field, `${who}: азимут ${numberText(az)}° задан дважды.`, "Каждый азимут — один сектор.", `${at}.${i}.az`) };
    out.push({ az, angle });
  }
  return { ok: true, value: out };
}

/** The form «Углы бортов» → `params.slopes` and `params.k`. */
export function slopesPatch(f: SlopesForm, limits: Limits = DEFAULT_LIMITS): Got<SlopesPatch> {
  const table = sectorsPatch(f.table, "slopes", "slopes.table", "Углы бортов", limits);
  if (!table.ok) return table;
  let domainField: string | null = null;
  const domains: DomainParam[] = [];
  if (f.domainOn) {
    if (!f.domainField.trim()) return { ok: false, issue: issue("domains", "Не выбрано поле доменов.", "Выберите категорийное поле модели или снимите отметку «Домены».", "slopes.domain_field") };
    domainField = f.domainField.trim();
    if (f.domains.length > limits.domains) return { ok: false, issue: issue("domains", `Доменов больше ${limits.domains}.`, "Оставьте собственные таблицы не больше чем у 16 подписей.", "slopes.domains") };
    const seen = new Set<string>();
    for (const [i, d] of f.domains.entries()) {
      const label = d.label.trim();
      if (!label) return { ok: false, issue: issue("domains", `У домена ${i + 1} нет подписи.`, "Подпись — значение поля доменов.", `slopes.domains.${i}.label`) };
      if (seen.has(label.toLowerCase())) return { ok: false, issue: issue("domains", `Домен «${label}» повторяется.`, "У подписи одна таблица.", `slopes.domains.${i}.label`) };
      seen.add(label.toLowerCase());
      const t = sectorsPatch(d.table, "domains", `slopes.domains.${i}.table`, `Домен «${label}»`, limits);
      if (!t.ok) return t;
      domains.push({ label, table: t.value });
    }
  }
  const tolerance = parseDecimal(f.tolerance);
  if (tolerance === null) return { ok: false, issue: issue("tolerance", "Допуск угла — не число.", "Допуск ошибки угла — от 0,5 до 10°, по умолчанию 2,5.", "slopes.tolerance") };
  if (tolerance < MIN_TOLERANCE || tolerance > MAX_TOLERANCE) return { ok: false, issue: issue("tolerance", `Допуск угла ${numberText(tolerance)}° вне диапазона ${numberText(MIN_TOLERANCE)}…${MAX_TOLERANCE}°.`, "Допуск ошибки угла — от 0,5 до 10°, по умолчанию 2,5.", "slopes.tolerance") };
  let k: number | null = null;
  if (!f.kAuto) {
    const v = parseDecimal(f.k);
    if (v === null || !Number.isInteger(v)) return { ok: false, issue: issue("k", "Число уровней K — целое число.", "K — от 1 до 20; «Авто» подбирает наименьшее от 8 до 20.", "k") };
    if (v < 1 || v > MAX_K) return { ok: false, issue: issue("k", `K = ${v} вне диапазона 1…${MAX_K}.`, "K — от 1 до 20; «Авто» подбирает наименьшее от 8 до 20.", "k") };
    k = v;
  }
  return { ok: true, patch: { slopes: { table: table.value, domain_field: domainField, domains, tolerance }, k } };
}

// ---- the flat arguments of `pit.slopes` ↔ the form ----

export const SLOPES_KEYS = ["table", "domain_field", "domains", "k", "tolerance"] as const;
export const hasSlopes = (a: Args): boolean => SLOPES_KEYS.some((k) => has(a, k));

/** «0:45; 90:50; 180:40» — the azimuth and the angle of a sector (the azimuth may be left out of a single sector: «45»). */
export const sectorsText = (t: readonly SectorParam[]): string => t.map((s) => `${numberText(s.az)}:${numberText(s.angle)}`).join("; ");

export function parseSectors(text: string): SectorRow[] {
  return text.split(/[;\n]+/).map((s) => s.trim()).filter(Boolean).map((part) => {
    const bits = part.split(":").map((x) => x.trim());
    return bits.length === 1 ? { az: "", angle: bits[0] } : { az: bits[0], angle: bits[1] };
  });
}

/** The arguments of `pit.slopes` from the form: `table` is the text above, `domains` — JSON [{label, table: [{az, angle}]}]. */
export function slopesArgs(f: SlopesForm, scenarioId: number | null, limits: Limits = DEFAULT_LIMITS): { ok: true; args: Args } | { ok: false; issue: FormIssue } {
  const got = slopesPatch(f, limits);
  if (!got.ok) return got;
  const { slopes, k } = got.patch;
  const a: Args = {};
  if (scenarioId !== null) a.scenario_id = scenarioId;
  a.table = sectorsText(slopes.table);
  a.domain_field = slopes.domain_field;
  a.domains = slopes.domain_field === null ? null : JSON.stringify(slopes.domains);
  a.k = k;
  a.tolerance = slopes.tolerance;
  return { ok: true, args: a };
}

/** The form that shows the arguments of a call (a key that is not in it stays as `base` has it); a wrong argument is told. */
export function slopesFormOfArgs(a: Args, base: PitParams): { ok: true; form: SlopesForm } | { ok: false; issue: FormIssue } {
  const f = slopesFormOfParams(base);
  if (has(a, "table")) f.table = typeof a.table === "string" ? parseSectors(a.table) : Array.isArray(a.table) ? a.table.map((x) => ({ az: txt(obj(x).az), angle: txt(obj(x).angle) })) : [];
  if (has(a, "domain_field")) {
    const v = typeof a.domain_field === "string" ? a.domain_field.trim() : "";
    f.domainOn = v !== "";
    f.domainField = v;
    if (!v) f.domains = [];
  }
  if (has(a, "domains")) {
    const raw = a.domains;
    if (raw === null || (typeof raw === "string" && !raw.trim())) f.domains = [];
    else {
      let v: unknown;
      try {
        v = typeof raw === "string" ? JSON.parse(raw) : raw;
      } catch {
        v = null;
      }
      if (!Array.isArray(v)) return { ok: false, issue: issue("domains", "Домены — не список в JSON.", 'Пример: [{"label": "A", "table": [{"az": 0, "angle": 40}, {"az": 180, "angle": 50}]}].', "slopes.domains") };
      f.domains = v.map((x) => {
        const d = obj(x);
        return { label: typeof d.label === "string" ? d.label.trim() : "", table: list(d.table).map((s) => ({ az: txt(obj(s).az), angle: txt(obj(s).angle) })) };
      });
    }
  }
  if (has(a, "k")) {
    const v = a.k;
    if (v === null || v === "" || v === "auto") Object.assign(f, { kAuto: true, k: "" });
    else Object.assign(f, { kAuto: false, k: txt(v) });
  }
  if (has(a, "tolerance")) f.tolerance = txt(a.tolerance);
  return { ok: true, form: f };
}

/** The labels of the category field of the model (`defaults.model.categories`) that have no table of their own yet. */
export function labelsWithoutTable(f: SlopesForm, labels: readonly string[]): string[] {
  return labels.filter((l) => !f.domains.some((d) => d.label.trim().toLowerCase() === l.trim().toLowerCase()));
}
