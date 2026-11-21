/**
 * The window «Лог-вероятностный» (ТЗ 8.3; ADR 0019 §4, §11): the grade (log10) against the probability, the
 * domains — a text field of the same or another table — as series; a click at the bend of a curve opens «Урезка»
 * with that threshold (`composite.topcut {set_id, value}` — the window only: the threshold is applied there by
 * «Применить», never by itself — ADR 0018, decision 8). The table of the domains, the CSV of the points.
 */
import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { AppContext } from "../../shell/AppContext";
import type { App } from "../../shell/app";
import { errorText } from "../../shell/props";
import { defaultField } from "../../modules/charts/api";
import type { ProbPlotResult } from "../../modules/charts/apiMore";
import type { ChartWindowProps } from "../../modules/charts/bind";
import { useSavedChart } from "../../modules/charts/saved";
import { DEFAULT_PROBPLOT_VIEW, type ProbPlotSettings } from "../../modules/charts/settings";
import { ChartWindow } from "../ChartWindow";
import { formatNumber } from "../core/axisScale";
import { ChartToolbar } from "../core/ChartToolbar";
import { DomainSource, fieldLabel, keepField, useStatsFields } from "../core/Pickers";
import { SampleFilterForm } from "../SampleFilterForm";
import css from "../core/Chart.module.css";
import { domainNotes } from "../box/model";
import { ProbPlotChart } from "./ProbPlotChart";
import { probCalculator, probCsv, PROB_HEADER, probRequest, probTable, topcutArgs } from "./model";

/** A threshold picked on the chart → the window «Урезка» with it (through the registry: a command as any other). */
export async function openTopcut(app: Pick<App, "registry">, s: Pick<ProbPlotSettings, "set_id">, value: number): Promise<string | null> {
  const r = await app.registry.execute("composite.topcut", topcutArgs(s, value));
  return r.ok ? null : (r.result || "Окно «Урезка» не открылось.");
}

export function ProbPlotWindow(props: ChartWindowProps<"probplot">) {
  const { setId, setName } = props;
  const app = useContext(AppContext);
  const [s, setS] = useState<ProbPlotSettings>(props.settings);
  const [result, setResult] = useState<ProbPlotResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [threshold, setThreshold] = useState<number | null>(null);
  const [pickNote, setPickNote] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const set = (patch: Partial<ProbPlotSettings>) => setS((prev) => ({ ...prev, ...patch }));
  const saved = useSavedChart("probplot", props, s, setS);

  const f = useStatsFields(setId, s.table);
  useEffect(() => {
    const x = f.fields;
    if (x) setS((prev) => ({ ...prev, table: prev.table ?? x.table, field: keepField(x, prev.field) ?? defaultField(x), weight_field: keepField(x, prev.weight_field) }));
  }, [f.fields]);
  const calc = useMemo(() => probCalculator((r) => (setResult(r), setError(null)), (e) => setError(errorText(e)), setBusy), []);
  useEffect(() => () => calc.stop(), [calc]);
  const request = useMemo(() => probRequest(s), [s]);
  useEffect(() => {
    if (f.fields && request) calc.request(request);
  }, [calc, f.fields, request]);

  const pick = (value: number) => {
    setThreshold(value);
    if (!app) return;
    void openTopcut(app, s, value).then((e) => setPickNote(e ?? `Порог ${formatNumber(value, 4)} передан в окно «Урезка» — применяется кнопкой «Применить».`));
  };
  const info = f.fields?.numeric.find((v) => v.name === s.field);
  const xLabel = info ? fieldLabel(info) : (s.field ?? "");
  const settingsForm = (
    <>
      <DomainSource setId={setId} value={s} fields={f.fields} onChange={set} domainEmpty="нет — одна линия" />
      <SampleFilterForm value={s} fields={f.fields?.numeric ?? []} onChange={set} />
      <fieldset>
        <legend>Вид</legend>
        <label className={css.check}>
          <input type="checkbox" checked={s.view.lines} onChange={(e) => setS((p) => ({ ...p, view: { ...p.view, lines: e.target.checked } }))} /> Линии вместо точек
        </label>
      </fieldset>
    </>
  );
  const fileName = `Лог-вероятностный ${setName} ${s.field ?? ""}`.trim();
  const toolbar = <ChartToolbar svgRef={svgRef} fileName={fileName} disabled={!result?.series.length} csvLabel="CSV точек" csv={() => (result ? probCsv(result, xLabel) : null)} onReset={() => setS((p) => ({ ...p, view: { ...DEFAULT_PROBPLOT_VIEW } }))} />;
  const empty = !f.fields ? "Расчёт…" : !f.fields.numeric.length ? "В таблице нет числовых полей." : !request ? "Выберите поле веса." : result && !result.series.some((x) => x.points.length) ? "Нет проб > 0 для графика." : "Расчёт…";
  return (
    <ChartWindow
      settings={settingsForm}
      toolbar={toolbar}
      busy={busy}
      error={error ?? f.error}
      empty={empty}
      note={saved.note}
      chart={result && result.series.some((x) => x.points.length) && <ProbPlotChart ref={svgRef} result={result} lines={s.view.lines} xLabel={xLabel} threshold={threshold} onPick={pick} />}
      footer={
        result && (
          <>
            <p className={css.muted} data-testid="probplot-pick">
              {pickNote ?? "Щелчок по перегибу кривой открывает «Урезку» с этим порогом."}
            </p>
            <table className={css.stats} data-testid="probplot-stats">
              <thead>
                <tr>
                  {PROB_HEADER.map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {probTable(result).map((row, i) => (
                  <tr key={i}>
                    {row.map((v, k) => (k ? <td key={k}>{v}</td> : <th key={k}>{v}</th>))}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className={css.muted}>{domainNotes(result.counts, s).join(" · ")}</p>
            {result.conventions && <p className={css.muted}>{result.conventions}</p>}
          </>
        )
      }
    />
  );
}
