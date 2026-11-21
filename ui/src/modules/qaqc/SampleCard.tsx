/**
 * The card of a control sample (ТЗ 8.4.2 «щелчок — сведения о пробе и подсветка партии»; ADR 0020 §10, §11): what
 * the chart knows of the point and every field of its record (`POST /qaqc/records {data_id, rows: [row]}`); its
 * batch stays shown on the charts and in «Партии» until another click or «Снять подсветку». Only own windows.
 */
import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { errorText } from "../../shell/props";
import { loadRecords, type QaqcRecord } from "./api";
import { dateText, num } from "./format";
import { activeData, DROPPED, latest, pickSample, setHighlight, type SamplePick } from "./state";
import css from "./Qaqc.module.css";

/** A value of a field of the record for people: a number with a decimal comma, a missing one «—». */
export function fieldText(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") return num(v, 8);
  if (typeof v === "boolean") return v ? "да" : "нет";
  return typeof v === "string" ? v : JSON.stringify(v);
}

const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

/** A value of an element of the record (`values[E]`: v, q — 1 «<DL» (v = DL/2), 2 «>x»). */
function elementText(e: Record<string, unknown>): string {
  const v = e.v;
  if (e.q === 1) return `<DL (${fieldText(v)})`;
  if (e.q === 2) return `>${fieldText(v)}`;
  return fieldText(v);
}

/**
 * The fields of the record in its order (`POST /qaqc/records`): the service ones (row numbers of the file) left out,
 * the date (ms, UTC) as a date, the elements of `values` with «<DL» and «>x» and the values of the parent sample
 * and of the previous sample of the batch, the extra fields of `extra` by their names.
 */
export function recordFacts(rec: QaqcRecord): [string, string][] {
  const out: [string, string][] = [];
  for (const [k, v] of Object.entries(rec)) {
    if (k === "parent_row" || k === "prev_row") continue;
    if (k === "values" && isObject(v)) {
      for (const [el, e] of Object.entries(v)) {
        if (!isObject(e)) continue;
        out.push([el, elementText(e)]);
        if (e.p !== null && e.p !== undefined) out.push([`${el}: исходная проба`, fieldText(e.p)]);
        if (e.b !== null && e.b !== undefined) out.push([`${el}: предыдущая проба партии`, fieldText(e.b)]);
        if (e.dl !== null && e.dl !== undefined) out.push([`${el}: DL`, fieldText(e.dl)]);
      }
      continue;
    }
    if (k === "extra" && isObject(v)) {
      for (const [name, x] of Object.entries(v)) out.push([name, fieldText(x)]);
      continue;
    }
    out.push([k.replace(/^x:/, ""), k === "date" && typeof v === "number" ? dateText(v) : fieldText(v)]);
  }
  return out;
}

export function SampleCard({ sample }: { sample: SamplePick }) {
  const app = useApp();
  const [rec, setRec] = useState<{ row: number; facts: [string, string][] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dataId = activeData()?.id ?? null;
  useEffect(() => {
    if (dataId === null) return;
    setError(null);
    latest("card", () => loadRecords({ data_id: dataId, rows: [sample.row] })).then(
      (r) => r !== DROPPED && setRec({ row: sample.row, facts: r[0] ? recordFacts(r[0]) : [] }),
      (e: unknown) => setError(errorText(e)),
    );
  }, [dataId, sample.row]);
  const fields = rec?.row === sample.row ? rec.facts : null;
  const id = fields?.find(([k]) => k === "sample_id")?.[1];
  return (
    <section className={css.card} aria-label="Карточка пробы" data-testid="qaqc-card">
      <h4>
        <span>Проба {id ?? `(запись ${sample.row})`}</span>
        <button type="button" className={css.x} aria-label="Закрыть карточку" onClick={() => pickSample(null)}>
          ×
        </button>
      </h4>
      <table className={css.facts}>
        <tbody>
          {sample.facts.map(([k, v]) => (
            <tr key={k}>
              <th>{k}</th>
              <td>{v}</td>
            </tr>
          ))}
          {fields?.map(([k, v]) => (
            <tr key={`f:${k}`}>
              <th>{k}</th>
              <td>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!fields && !error && <p className={css.note}>Чтение записи…</p>}
      {error && <p className={css.error}>{error}</p>}
      {sample.batch && (
        <div className={css.row}>
          <button type="button" className={css.btn} onClick={() => void app.run("qaqc.open", { section: "batches" })}>
            Партия в «Партиях»
          </button>
          <button type="button" className={css.btn} onClick={() => setHighlight(null)}>
            Снять подсветку
          </button>
        </div>
      )}
    </section>
  );
}
