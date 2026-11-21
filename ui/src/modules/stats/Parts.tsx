/**
 * Shared parts of the panels of the tab «Статистика» (перенос `StatsPage.tsx` BF: Field, NumberInput, RunButton,
 * DeclusFields, Summary): a labelled field, the parameters of declustering (cells or polygons in plan), the facts
 * of an answer, the refusal with its hint — more than 20 000 intervals offers «Композиты» and «Декластеризация»
 * of «Обработка проб» (вопрос № 30).
 */
import { useSyncExternalStore, type ReactNode } from "react";
import { useApp } from "../../shell/AppContext";
import type { Store } from "../../shell/store";
import type { DeclusForm, Fact, Problem } from "./forms";
import css from "./Stats.module.css";

/** The whole state of a store (with a server snapshot: the panels render in tests without a DOM). */
export function useWhole<T>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get, store.get);
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className={css.field} title={hint}>
      <span>{label}</span>
      {children}
    </label>
  );
}

export function NumberInput({ label, value, onChange, placeholder, width = 72 }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string; width?: number }) {
  return <input type="text" inputMode="decimal" aria-label={label} value={value} placeholder={placeholder} style={{ width }} onChange={(e) => onChange(e.target.value)} />;
}

export function RunButton({ busy, reason, onClick, children = "Рассчитать" }: { busy: boolean; reason?: string | null; onClick: () => void; children?: ReactNode }) {
  return (
    <button type="button" className={css.primary} onClick={onClick} disabled={busy || !!reason} title={reason ?? undefined}>
      {busy ? "Расчёт…" : children}
    </button>
  );
}

/** The parameters of declustering (shared by declustering, normal scores and the bootstrap). */
export function DeclusFields({ form, onChange }: { form: DeclusForm; onChange: (patch: Partial<DeclusForm>) => void }) {
  const bound = (i: number) => (v: string) => onChange({ bounds: form.bounds.map((b, j) => (j === i ? v : b)) as DeclusForm["bounds"] });
  return (
    <>
      <Field label="Метод">
        <select aria-label="Метод декластеризации" value={form.method} onChange={(e) => onChange({ method: e.target.value as DeclusForm["method"] })}>
          <option value="cell">Ячейки в плане</option>
          <option value="polygon">Полигоны в плане</option>
        </select>
      </Field>
      {form.method === "cell" ? (
        <>
          <Field label="Ячейка от, м" hint="Наименьший размер ячейки; пусто — 2 % габарита плана">
            <NumberInput label="Ячейка от" value={form.cmin} onChange={(cmin) => onChange({ cmin })} placeholder="авто" />
          </Field>
          <Field label="до, м" hint="Наибольший размер ячейки; пусто — 50 % габарита плана">
            <NumberInput label="Ячейка до" value={form.cmax} onChange={(cmax) => onChange({ cmax })} placeholder="авто" />
          </Field>
          <Field label="Размеров" hint="Число размеров ячейки между «от» и «до» (1–500)">
            <NumberInput label="Размеров" value={form.ncell} onChange={(ncell) => onChange({ ncell })} width={52} />
          </Field>
          <Field label="Сдвигов" hint="Число сдвигов начала сетки (1–64)">
            <NumberInput label="Сдвигов" value={form.noff} onChange={(noff) => onChange({ noff })} width={52} />
          </Field>
          <Field label="Критерий" hint="Минимум — для данных, пробуренных гуще в богатых зонах">
            <select aria-label="Критерий" value={form.minmax} onChange={(e) => onChange({ minmax: e.target.value as DeclusForm["minmax"] })}>
              <option value="min">минимум среднего</option>
              <option value="max">максимум среднего</option>
            </select>
          </Field>
        </>
      ) : (
        <>
          <Field label="Сетка X × Y" hint="Узлов сетки для площади полигонов; пусто — по числу скважин">
            <span className={css.pair}>
              <NumberInput label="Сетка по X" value={form.nx} onChange={(nx) => onChange({ nx })} placeholder="авто" width={56} />
              <NumberInput label="Сетка по Y" value={form.ny} onChange={(ny) => onChange({ ny })} placeholder="авто" width={56} />
            </span>
          </Field>
          <Field label="Границы X от … до, Y от … до" hint="Область, которой обрезаются полигоны; пусто — габарит проб">
            <span className={css.pair}>
              {["X от", "X до", "Y от", "Y до"].map((l, i) => (
                <NumberInput key={l} label={l} value={form.bounds[i]} onChange={bound(i)} placeholder={l} width={68} />
              ))}
            </span>
          </Field>
        </>
      )}
    </>
  );
}

export function Facts({ rows, label }: { rows: readonly Fact[]; label: string }) {
  return (
    <table className={css.facts} aria-label={label}>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <th scope="row">{k}</th>
            <td>{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** A refusal of the core or of the form: the message and its hint; > 20 000 intervals — the ways out (Т7.29-2). */
export function ProblemBox({ problem }: { problem: Problem }) {
  const app = useApp();
  return (
    <div className={css.problem} role="alert" data-code={problem.code}>
      <p className={css.problemText}>{problem.message}</p>
      {problem.hint && <p className={css.hint}>{problem.hint}</p>}
      {problem.composite && (
        <div className={css.row}>
          <button type="button" className={css.btn} onClick={() => void app.run("composite.run")}>
            Композиты…
          </button>
          <button type="button" className={css.btn} onClick={() => void app.run("composite.declus")}>
            Декластеризация всей таблицы…
          </button>
        </div>
      )}
    </div>
  );
}
