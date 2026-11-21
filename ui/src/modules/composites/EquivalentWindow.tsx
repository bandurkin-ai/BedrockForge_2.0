/**
 * The window «Эквивалент металла» (ТЗ 8.2.2–8.2.3; ADR 0018 §7, §10–11): the source table; the metals as rows —
 * the base and the others, each with its field, price, recovery (0–1; 0,9), the unit of the grade (by the unit of
 * the field) and of the price (г/т — $/унц, % — $/т) and its k; under them «1 % CU = … г/т AU-экв» for every other
 * metal; «Пустое содержание считать нулём» (off: a missing grade gives a missing Eq); the name of the field
 * (‹основной›EQ). The field is written into the source table: «Применить» → the job (the assistant is asked first —
 * `confirm`); the preview on one hole.
 */
import { useMemo, useState } from "react";
import css from "../../shell/PropsParts.module.css";
import { tablesStore } from "../tables/state";
import own from "./Composites.module.css";
import type { FieldRow } from "./api";
import { PreviewBox } from "./MergeWindow";
import { numberText, parseNumber, type ToolSpec } from "./params";
import { SourcePicker } from "./SourcePicker";
import { JobView, ParamForm, ToolFooter, useTool } from "./ToolWindow";
import { defaultPriceUnit, equivalent, factorText, GRADE_UNITS, gradeUnit, kFactor, othersText, parseOthers, PRICE_UNITS, withUnits, type GradeUnit, type Metal, type PriceUnit } from "./tools/equivalent";

/** A metal row as typed: numbers stay text; a unit null — by the field (grade) or by the grade (price). */
export interface MetalRow {
  field: string;
  price: string;
  recovery: string;
  unit: GradeUnit | null;
  priceUnit: PriceUnit | null;
}

const EXTRA_PARAMS = equivalent.params.filter((p) => p.key === "nan_zero" || p.key === "name");

/** The rows of the other metals from the text of `others` («CU 9000 0,9; …»). */
export function rowsFromOthers(text: unknown): MetalRow[] {
  const parsed = typeof text === "string" && text.trim() ? parseOthers(text) : [];
  const list = Array.isArray(parsed) ? parsed : [];
  return list.length ? list.map((o) => ({ field: o.field, price: numberText(o.price), recovery: numberText(o.recovery), unit: null, priceUnit: null })) : [{ field: "", price: "", recovery: "0,9", unit: null, priceUnit: null }];
}

/** The units in force: given, else by the unit of the field, else г/т; the price — given, else by the grade. */
export function unitsOf(row: Pick<MetalRow, "field" | "unit" | "priceUnit">, fields: readonly FieldRow[]): { unit: GradeUnit; priceUnit: PriceUnit } {
  const unit = row.unit ?? gradeUnit(fields.find((f) => f.name === row.field)?.unit) ?? "г/т";
  return { unit, priceUnit: row.priceUnit ?? defaultPriceUnit(unit) };
}

/** A metal with numbers, or null while its price or recovery is not a number. */
export function metalOf(row: MetalRow, fields: readonly FieldRow[]): Metal | null {
  const price = parseNumber(row.price);
  const recovery = parseNumber(row.recovery);
  if (!row.field || price === null || recovery === null || price <= 0 || recovery <= 0) return null;
  return { field: row.field, price, recovery, ...unitsOf(row, fields) };
}

function MetalLine({ label, row, fields, onChange, onRemove }: { label: string; row: MetalRow; fields: readonly FieldRow[]; onChange: (r: MetalRow) => void; onRemove?: () => void }) {
  const u = unitsOf(row, fields);
  const numeric = fields.filter((f) => f.kind === "number");
  return (
    <tr data-testid={`composites-metal-${label}`}>
      <th scope="row">{label}</th>
      <td>
        <select className={css.input} aria-label={`${label}: поле`} value={row.field} onChange={(e) => onChange({ ...row, field: e.target.value })}>
          <option value="">выберите поле</option>
          {numeric.map((f) => (
            <option key={f.key} value={f.name}>
              {f.name}
              {f.unit ? `, ${f.unit}` : ""}
            </option>
          ))}
        </select>
      </td>
      <td>
        <input className={css.input} aria-label={`${label}: цена`} inputMode="decimal" value={row.price} placeholder="> 0" onChange={(e) => onChange({ ...row, price: e.target.value })} />
      </td>
      <td>
        <select className={css.input} aria-label={`${label}: единица цены`} value={u.priceUnit} onChange={(e) => onChange({ ...row, priceUnit: e.target.value as PriceUnit })}>
          {PRICE_UNITS.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </td>
      <td>
        <input className={css.input} aria-label={`${label}: извлечение`} inputMode="decimal" value={row.recovery} placeholder="0–1" onChange={(e) => onChange({ ...row, recovery: e.target.value })} />
      </td>
      <td>
        <select className={css.input} aria-label={`${label}: единица содержания`} value={u.unit} onChange={(e) => onChange({ ...row, unit: e.target.value as GradeUnit })}>
          {GRADE_UNITS.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
      </td>
      <td className={own.num} title="k — перевод единицы содержания в единицу цены">
        {numberText(kFactor(u.unit, u.priceUnit))}
      </td>
      <td>
        {onRemove && (
          <button type="button" className={own.iconBtn} aria-label={`Убрать ${row.field || "металл"}`} onClick={onRemove}>
            ✕
          </button>
        )}
      </td>
    </tr>
  );
}

export function EquivalentWindow({ winId, args = {} }: { winId: string; args?: Record<string, unknown> }) {
  const [rows, setRows] = useState<MetalRow[]>(() => rowsFromOthers(args.others));
  const [baseUnits, setBaseUnits] = useState<{ unit: GradeUnit | null; priceUnit: PriceUnit | null }>({ unit: null, priceUnit: null });
  const [fields, setFields] = useState<FieldRow[]>([]);
  // the units of the window go into the body of the job (the core takes them over the units of the fields)
  const tool: ToolSpec = useMemo(
    () => ({
      ...equivalent,
      finish: (body) => {
        const done = equivalent.finish!(body);
        if (typeof done === "string") return done;
        const base = unitsOf({ field: String(body.base ?? ""), ...baseUnits }, fields);
        return withUnits(done, base, rows.map((r) => ({ field: r.field, ...unitsOf(r, fields) })));
      },
    }),
    [rows, baseUnits, fields],
  );
  const t = useTool(tool, args);
  const [activeSet] = useState(() => tablesStore.get().setId);
  const text = (k: string) => (typeof t.values[k] === "string" ? (t.values[k] as string) : "");
  const baseRow: MetalRow = { field: text("base"), price: text("base_price"), recovery: text("base_recovery"), ...baseUnits };
  const setBase = (r: MetalRow) => {
    setBaseUnits({ unit: r.unit, priceUnit: r.priceUnit });
    t.setValues({ ...t.values, base: r.field, base_price: r.price, base_recovery: r.recovery });
  };
  const setOthers = (next: MetalRow[]) => {
    setRows(next);
    t.setValues({ ...t.values, others: othersText(next) });
  };
  const base = metalOf(baseRow, t.fields);
  const factors = base ? rows.map((r) => metalOf(r, t.fields)).filter((m): m is Metal => m !== null) : [];
  const fieldName = text("name").trim() || `${baseRow.field || "‹основной›"}EQ`;
  const errors = Object.values(t.checked.errors).filter((_, i) => i < 3);

  if (t.job) return <JobView title={equivalent.title} job={t.job} winId={winId} onBack={() => t.setJob(null)} />;
  return (
    <div className={css.win} data-testid="composites-equivalent">
      <div className={css.winBody}>
        <p className={css.note}>Eq = g₁ + Σ g_e·(P_e·R_e·k_e)/(P₁·R₁·k₁): P — цена, R — извлечение, k — перевод единицы содержания в единицу цены.</p>
        <SourcePicker needs={equivalent.source} value={t.src} onChange={t.setSrc}
          onFields={(f) => {
            t.setFields(f);
            setFields(f);
          }}
          activeSet={activeSet} />
        <fieldset className={css.group}>
          <legend>Металлы</legend>
          <div style={{ overflow: "auto" }}>
            <table className={own.rows} data-testid="composites-metals">
              <thead>
                <tr>
                  <th />
                  <th>Поле</th>
                  <th>Цена</th>
                  <th>за</th>
                  <th>Извлечение</th>
                  <th>Содержание в</th>
                  <th>k</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                <MetalLine label="Основной" row={baseRow} fields={t.fields} onChange={setBase} />
                {rows.map((r, i) => (
                  <MetalLine key={i} label={`Прочий ${i + 1}`} row={r} fields={t.fields} onChange={(x) => setOthers(rows.map((y, j) => (j === i ? x : y)))} onRemove={rows.length > 1 ? () => setOthers(rows.filter((_, j) => j !== i)) : undefined} />
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <button type="button" className={css.btn} onClick={() => setOthers([...rows, { field: "", price: "", recovery: "0,9", unit: null, priceUnit: null }])}>
              Добавить металл
            </button>
          </div>
          {base && factors.length > 0 && (
            <ul className={css.note} data-testid="composites-factors" style={{ margin: 0, paddingLeft: 16 }}>
              {factors.map((m) => (
                <li key={m.field}>{factorText(base, m)}</li>
              ))}
            </ul>
          )}
          {errors.map((e) => (
            <p key={e} className={css.warn}>
              {e}
            </p>
          ))}
        </fieldset>
        <ParamForm params={EXTRA_PARAMS} values={t.values} errors={t.checked.errors} fields={t.fields} onChange={t.setValues} placeholders={{ name: `${baseRow.field || "‹основной›"}EQ` }} />
        <PreviewBox t={t} first={[fieldName, baseRow.field, ...rows.map((r) => r.field)].filter(Boolean)} />
        {t.error && (
          <p className={css.error} role="alert">
            {t.error}
          </p>
        )}
      </div>
      <ToolFooter winId={winId} reason={t.checked.reason} busy={t.busy} onApply={() => void t.apply()} extra={`Поле «${fieldName}» пишется в таблицу источника; «Отменить» в журнале правок скрывает его.`} />
    </div>
  );
}
