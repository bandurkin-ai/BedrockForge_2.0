/**
 * The window «Ящики с усами» (ТЗ 8.3; ADR 0019 §4, §11): a box per domain — a text field of the same or of another
 * interval table (without it — one box of all samples); quartiles by `weighted_percentiles`, whiskers 1,5·IQR, the
 * outliers (≤ 5000 drawn, how many more — in the notes), the mean, n; the log scale is by the logarithms. The table
 * of the boxes under the chart, the CSV of it. An edit computes again 300 ms later.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { errorText } from "../../shell/props";
import { defaultField } from "../../modules/charts/api";
import type { BoxResult } from "../../modules/charts/apiMore";
import type { ChartWindowProps } from "../../modules/charts/bind";
import { useSavedChart } from "../../modules/charts/saved";
import { DEFAULT_BOX_VIEW, type BoxSettings, type BoxView } from "../../modules/charts/settings";
import { ChartWindow } from "../ChartWindow";
import { ChartToolbar } from "../core/ChartToolbar";
import { DomainSource, fieldLabel, keepField, useStatsFields } from "../core/Pickers";
import { SampleFilterForm } from "../SampleFilterForm";
import { ColorField } from "../../shell/PropsParts";
import css from "../core/Chart.module.css";
import { BoxChart } from "./BoxChart";
import { boxCalculator, boxCsv, boxHeader, boxNotes, boxRequest, boxTable } from "./model";

export function BoxWindow(props: ChartWindowProps<"box">) {
  const { setId, setName } = props;
  const [s, setS] = useState<BoxSettings>(props.settings);
  const [result, setResult] = useState<BoxResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const set = (patch: Partial<BoxSettings>) => setS((prev) => ({ ...prev, ...patch }));
  const setView = (patch: Partial<BoxView>) => setS((prev) => ({ ...prev, view: { ...prev.view, ...patch } }));
  const saved = useSavedChart("box", props, s, setS);

  const f = useStatsFields(setId, s.table);
  useEffect(() => {
    const x = f.fields;
    if (x) setS((prev) => ({ ...prev, table: prev.table ?? x.table, field: keepField(x, prev.field) ?? defaultField(x), weight_field: keepField(x, prev.weight_field) }));
  }, [f.fields]);

  const calc = useMemo(() => boxCalculator((r) => (setResult(r), setError(null)), (e) => setError(errorText(e)), setBusy), []);
  useEffect(() => () => calc.stop(), [calc]);
  const request = useMemo(() => boxRequest(s), [s]);
  useEffect(() => {
    if (f.fields && request) calc.request(request);
  }, [calc, f.fields, request]);

  const info = f.fields?.numeric.find((v) => v.name === s.field);
  const yLabel = info ? fieldLabel(info) : (s.field ?? "");
  const settingsForm = (
    <>
      <DomainSource setId={setId} value={s} fields={f.fields} onChange={set} />
      <SampleFilterForm value={s} fields={f.fields?.numeric ?? []} onChange={set} />
      <fieldset>
        <legend>Вид</legend>
        <label className={css.check}>
          <input type="checkbox" checked={s.log} onChange={(e) => set({ log: e.target.checked })} /> Логарифмическая шкала (по логарифмам)
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.view.mean} onChange={(e) => setView({ mean: e.target.checked })} /> Среднее
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={s.view.outliers} onChange={(e) => setView({ outliers: e.target.checked })} /> Выбросы
        </label>
        <label className={css.check}>
          <ColorField value={s.view.color} label="Цвет ящика" onCommit={(color) => setView({ color })} /> Цвет (без доменов)
        </label>
      </fieldset>
    </>
  );
  const fileName = `Ящики ${setName} ${s.field ?? ""} ${s.domain ?? ""}`.trim();
  const toolbar = <ChartToolbar svgRef={svgRef} fileName={fileName} disabled={!result?.groups.length} csvLabel="CSV таблицы" csv={() => (result ? boxCsv(result) : null)} onReset={() => setView({ ...DEFAULT_BOX_VIEW })} />;
  const empty = !f.fields ? "Расчёт…" : !f.fields.numeric.length ? "В таблице нет числовых полей." : !request ? "Выберите поле веса." : result && !result.groups.length ? "Нет проб для ящиков." : "Расчёт…";
  return (
    <ChartWindow
      settings={settingsForm}
      toolbar={toolbar}
      busy={busy}
      error={error ?? f.error}
      empty={empty}
      note={saved.note}
      chart={result && result.groups.length > 0 && <BoxChart ref={svgRef} result={result} mean={s.view.mean} outliers={s.view.outliers} color={result.groups.length === 1 ? s.view.color : null} yLabel={yLabel} />}
      footer={
        result && (
          <>
            <table className={css.stats} data-testid="box-stats">
              <thead>
                <tr>
                  {boxHeader(result).map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {boxTable(result).map((row, i) => (
                  <tr key={i}>
                    {row.map((v, k) => (k ? <td key={k}>{v}</td> : <th key={k}>{v}</th>))}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className={css.muted} data-testid="box-notes">
              {boxNotes(result, s).join(" · ")}
            </p>
            {result.conventions && <p className={css.muted}>{result.conventions}</p>}
          </>
        )
      }
    />
  );
}
