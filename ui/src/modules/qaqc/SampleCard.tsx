/**
 * The card of a control sample (ТЗ 8.4.2 «щелчок — сведения о пробе и подсветка партии»; ADR 0020 §10, §11): what
 * the chart knows of the point and every field of its record (`POST /qaqc/records {data_id, rows: [row]}`); its
 * batch stays shown on the charts and in «Партии» until another click or «Снять подсветку». Only own windows.
 */
import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { errorText } from "../../shell/props";
import { loadRecords, type QaqcRecord } from "./api";
import { num } from "./format";
import { activeData, DROPPED, latest, pickSample, setHighlight, type SamplePick } from "./state";
import css from "./Qaqc.module.css";

/** A value of a field of the record for people: a number with a decimal comma, a missing one «—». */
export function fieldText(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") return num(v, 8);
  if (typeof v === "boolean") return v ? "да" : "нет";
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** The fields of the record in its order; the service ones (row numbers of the file) are left out. */
export function recordFacts(rec: QaqcRecord): [string, string][] {
  return Object.entries(rec)
    .filter(([k]) => k !== "parent_row" && k !== "prev_row")
    .map(([k, v]) => [k.replace(/^x:/, ""), fieldText(v)]);
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
