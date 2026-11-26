/**
 * The parts of the panel «Pit-by-pit» (ТЗ 8.11.4–8.11.5; ADR 0028 решения 18, 19, 29): the calendar of the view (Q_р, Q_гм, r — a view: nothing is written until «Записать в сценарий»),
 * the table (the text of the core; a gap is «—»), the legend of the chart. Drawing from what is known; the panel connects it to the stores and to the registry.
 */
import { useState } from "react";
import { numberText, parseNumber } from "../../charts/core/axisScale";
import { PIT_COLORS, PIT_SERIES } from "./PitChart";
import css from "./PitPanel.module.css";
import { calendarText, columnTitle, type Calendar, type ViewTable } from "./reportModel";

// ---- the calendar of the view ----

interface CalendarText {
  qOre: string;
  qMass: string;
  rate: string;
}

const textOf = (c: Calendar): CalendarText => ({ qOre: numberText(c.q_ore), qMass: numberText(c.q_mass), rate: numberText(c.rate === null ? null : Math.round(c.rate * 10000) / 100) });

/** The calendar of the fields, or why it is wrong (nothing wrong goes to the core): Q_р and Q_гм — empty or more than 0, r — 0…50 % a year. */
export function calendarOfText(t: CalendarText): { ok: true; calendar: Calendar } | { ok: false; message: string } {
  const one = (s: string, what: string): { ok: true; v: number | null } | { ok: false; message: string } => {
    if (!s.trim()) return { ok: true, v: null };
    const v = parseNumber(s);
    if (v === null || !(v > 0)) return { ok: false, message: `${what}: введите число больше нуля или оставьте поле пустым.` };
    return { ok: true, v };
  };
  const qOre = one(t.qOre, "Q_р");
  if (!qOre.ok) return qOre;
  const qMass = one(t.qMass, "Q_гм");
  if (!qMass.ok) return qMass;
  const pct = t.rate.trim() ? parseNumber(t.rate) : 8;
  if (pct === null || pct < 0 || pct > 50) return { ok: false, message: "Ставка r: число от 0 до 50 процентов в год, например 8." };
  return { ok: true, calendar: { q_ore: qOre.v, q_mass: qMass.v, rate: Math.round(pct * 100) / 10000 } };
}

export interface CalendarBarProps {
  calendar: Calendar;
  /** the calendar of the view is not the one of the scenario */
  changed: boolean;
  busy: boolean;
  onApply: (c: Calendar) => void;
  onReset: () => void;
  onSave: (c: Calendar) => void;
  /** why «Записать в сценарий» is unavailable (the project is read-only, the command is not bound): the button is off and says it */
  saveReason?: string | null;
}

/** «Календарь для NPV»: the fields start from the calendar the report was made with; the key makes them start again when it changes. */
export function CalendarBar(p: CalendarBarProps) {
  const [text, setText] = useState<CalendarText>(() => textOf(p.calendar));
  const [issue, setIssue] = useState<string | null>(null);
  const set = (patch: Partial<CalendarText>) => {
    setText((t) => ({ ...t, ...patch }));
    setIssue(null);
  };
  const run = (go: (c: Calendar) => void) => {
    const got = calendarOfText(text);
    if (!got.ok) setIssue(got.message);
    else go(got.calendar);
  };
  const bad = (needle: string) => (issue?.startsWith(needle) ? true : undefined);
  return (
    <div className={css.bar} data-testid="pit-calendar" title="Календарь только для показа: NPV пересчитывается из записанного результата, оболочки не пересчитываются">
      <span className={css.facts}>Календарь:</span>
      <label>
        Q_р, т/год
        <input className={css.input} value={text.qOre} inputMode="decimal" aria-label="Производительность по руде Q_р, т/год" aria-invalid={bad("Q_р")} data-testid="pit-q-ore" onChange={(e) => set({ qOre: e.target.value })} />
      </label>
      <label>
        Q_гм, т/год
        <input className={css.input} value={text.qMass} inputMode="decimal" placeholder="без ограничения" aria-label="Производительность по горной массе Q_гм, т/год" aria-invalid={bad("Q_гм")} data-testid="pit-q-mass" onChange={(e) => set({ qMass: e.target.value })} />
      </label>
      <label>
        r, % в год
        <input className={`${css.input} ${css.short}`} value={text.rate} inputMode="decimal" aria-label="Ставка дисконтирования r, процентов в год" aria-invalid={bad("Ставка")} data-testid="pit-rate" onChange={(e) => set({ rate: e.target.value })} />
      </label>
      <button type="button" className={css.btn} disabled={p.busy} data-testid="pit-calendar-apply" title="Пересчитать NPV по этому календарю (сценарий не меняется)" onClick={() => run(p.onApply)}>
        Пересчитать NPV
      </button>
      <button type="button" className={css.btn} disabled={p.busy || !p.changed} data-testid="pit-calendar-reset" title="Вернуть календарь сценария" onClick={p.onReset}>
        Как в сценарии
      </button>
      <button type="button" className={css.btn} disabled={p.busy || !!p.saveReason} data-testid="pit-calendar-save" title={p.saveReason ?? "Записать этот календарь в сценарий (команда «Календарь»): результат не устаревает"} onClick={() => run(p.onSave)}>
        Записать в сценарий
      </button>
      {issue && (
        <span className={css.error} role="alert" data-testid="pit-calendar-issue">
          {issue}
        </span>
      )}
      {p.changed && !issue && <span className={css.note}>Показан календарь панели: {calendarText(p.calendar)}.</span>}
    </div>
  );
}

// ---- the table ----

/** The table of a mode: the head with the units, the rows; the text of a cell is shown as it is, a gap is drawn apart. `onShow` — «3D» of a shell. */
export function ReportTable({ table, onShow }: { table: ViewTable; onShow?: (k: number) => void }) {
  return (
    <div className={css.tableWrap}>
      <table className={css.table} data-testid="pit-table" data-rows={table.rows.filter((r) => !r.total).length}>
        <thead>
          <tr>
            {table.columns.map((c, i) => (
              <th key={c.key} className={i === 0 ? css.left : undefined} data-col={c.key} title={columnTitle(c)}>
                {c.title}
                {c.unit && <small>{c.unit}</small>}
              </th>
            ))}
            {onShow && <th aria-label="Показать в 3D" title="Показать блоки до оболочки в 3D" />}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((r) => (
            <tr key={r.key} className={r.total ? css.total : undefined} data-row={r.k ?? "total"}>
              {table.columns.map((c, i) => {
                const v = r.cells[c.key];
                return (
                  <td key={c.key} className={`${i === 0 ? css.left : ""} ${v?.missing ? css.gap : ""}`} data-col={c.key}>
                    {v?.text ?? "—"}
                  </td>
                );
              })}
              {onShow && (
                <td>
                  {r.k !== null && (
                    <button type="button" className={css.mini} data-testid={`pit-show-${r.k}`} title={`Показать в 3D блоки модели с PIT ≤ ${r.k}`} onClick={() => onShow(r.k as number)}>
                      3D
                    </button>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- the legend of the chart ----

export function Legend() {
  return (
    <div className={css.legend} data-testid="pit-legend" role="list">
      {PIT_SERIES.map((s) => (
        <span key={s.key} className={css.legendItem} role="listitem" title={s.hint} data-series={s.key}>
          <span className={`${css.swatch} ${s.key === "worst" ? css.dash : s.key === "best" || s.key === "cf" ? css.line : ""}`} style={s.key === "worst" || s.key === "best" || s.key === "cf" ? { borderTopColor: PIT_COLORS[s.key] } : { background: PIT_COLORS[s.key] }} aria-hidden />
          {s.label}
        </span>
      ))}
    </div>
  );
}
