/**
 * The window «Урезка» (ТЗ 8.2.2–8.2.3; ADR 0018 §7, decision 8): the source (table, field, domain); the threshold —
 * the percentile P, the value T or by domain (a row of T per domain of the analysis); the analysis of the set
 * (`POST /composites/topcut/analysis`, synchronous, the newest answer only): the curves «порог → среднее, CV,
 * потеря металла, число урезанных» for P 90…99,9 % — a click (or ←/→) on a curve changes the threshold of the
 * window only; the shares of metal by deciles and by percentiles of the top decile with the marks (40 % / 10 %, set
 * here); the simple log-probability curve (the full chart of 8.3 — Э13); the report «до/после» by domain read off
 * the curves for the threshold of the window, and the report of the last applied top cut. The threshold is never
 * applied by itself: «Применить» → the job (the assistant is asked first — `confirm`).
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { api } from "../../shell/api";
import { errorText } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { tablesStore } from "../tables/state";
import own from "./Composites.module.css";
import { createPreviewer, type Previewer, type ResultRow } from "./api";
import { CurvePlot } from "./CurvePlot";
import plot from "./CurvePlot.module.css";
import { numberText, parseNumber, type FormValues, type SourceState } from "./params";
import { loadSetResults, resultsStore, staleText, watchResults } from "./results";
import { SourcePicker } from "./SourcePicker";
import { HolePicker, JobView, ParamForm, SummaryTable, ToolFooter, useTool } from "./ToolWindow";
import {
  analysisBody,
  domainLabel,
  domainThreshold,
  forecast,
  latestResult,
  LIMITS,
  markedP,
  marks,
  parseThresholds,
  pickValues,
  PROB_TICKS,
  reportRows,
  thresholdsText,
  toAnalysis,
  topcut,
  type DomainAnalysis,
  type Limits,
  type Num,
  type Slice,
  type TopcutAnalysis,
} from "./tools/topcut";

/** The analysis of the set: the curves, the shares of metal, the quantiles; nothing is written. */
export async function fetchAnalysis(body: Record<string, unknown>): Promise<TopcutAnalysis> {
  return toAnalysis(await api.post<Record<string, unknown>>("/composites/topcut/analysis", body));
}

const n = (v: Num, digits = 3): string => (v === null ? "—" : v.toLocaleString("ru-RU", { maximumFractionDigits: digits }));
const pct = (v: Num): string => (v === null ? "—" : `${n(v, 1)} %`);

/** The five curves of a domain: x — P, 90…99,9 %; the mark — the P of the window. */
export function TopcutCurves({ d, field, marked, onPick }: { d: DomainAnalysis; field: string; marked: number | null; onPick?: (p: number) => void }) {
  const c = d.curves;
  const common = { xs: c.p, xLabel: "P, %", markX: marked, onPick };
  return (
    <div className={plot.curves} data-testid="topcut-curves">
      <CurvePlot {...common} title={`Порог T, ${field}`} ys={c.threshold} testId="topcut-curve-threshold" />
      <CurvePlot {...common} title="Среднее после урезки" ys={c.mean} refY={d.mean === null ? null : { value: d.mean, label: `до ${n(d.mean)}` }} testId="topcut-curve-mean" />
      <CurvePlot {...common} title="CV после урезки" ys={c.cv} refY={d.cv === null ? null : { value: d.cv, label: `до ${n(d.cv)}` }} testId="topcut-curve-cv" />
      <CurvePlot {...common} title="Потеря металла, %" ys={c.loss_pct} testId="topcut-curve-loss" />
      <CurvePlot {...common} title="Число урезанных проб" ys={c.n_cut} testId="topcut-curve-ncut" />
    </div>
  );
}

/** The shares of metal by slices of cumulative length; the last row is marked when `flagged`. */
export function SharesTable({ rows, caption, head, flagged, testId }: { rows: readonly Slice[]; caption: string; head: string; flagged: boolean; testId: string }) {
  const max = Math.max(1, ...rows.map((r) => r.share_pct ?? 0));
  return (
    <table className={own.rows} data-testid={testId}>
      <caption className={css.note} style={{ textAlign: "left" }}>
        {caption}
      </caption>
      <thead>
        <tr>
          <th>{head}</th>
          <th>Содержание от … до</th>
          <th>Среднее</th>
          <th>Доля металла</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const mark = flagged && i === rows.length - 1;
          return (
            <tr key={i} className={mark ? plot.flagged : undefined} data-flagged={mark || undefined}>
              <th scope="row">
                {numberText(r.from)}–{numberText(r.to)} %
              </th>
              <td className={own.num}>
                {n(r.min)} … {n(r.max)}
              </td>
              <td className={own.num}>{n(r.mean)}</td>
              <td className={own.num}>
                <span className={plot.share}>
                  <span className={plot.bar} style={{ width: `${Math.max(0, ((r.share_pct ?? 0) / max) * 60)}px` }} />
                  {pct(r.share_pct)}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** The simple log-probability curve: x — the cumulative probability (normal score), y — the grade on a log axis. */
export function LogProbPlot({ d, field, threshold }: { d: DomainAnalysis; field: string; threshold: Num }) {
  const nonPositive = d.quantiles.value.some((v) => v !== null && v <= 0);
  return (
    <div data-testid="topcut-logprob">
      <CurvePlot
        title="Лог-вероятностная кривая"
        xs={d.quantiles.z}
        ys={d.quantiles.value}
        logY
        xTicks={PROB_TICKS}
        xLabel="Накопленная вероятность (по длине), %"
        yLabel={`${field}, логарифмическая шкала`}
        refY={threshold === null ? null : { value: threshold, label: `T = ${n(threshold)}` }}
        width={420}
        height={210}
      />
      {nonPositive && <p className={css.note}>Значения ≤ 0 на логарифмической шкале не показаны.</p>}
    </div>
  );
}

/** The report «до/после» by domain for the threshold of the window, read off the curves (≈ — between two points). */
export function ForecastTable({ analysis, values }: { analysis: TopcutAnalysis; values: FormValues }) {
  return (
    <table className={own.rows} data-testid="topcut-forecast">
      <caption className={css.note} style={{ textAlign: "left" }}>
        Отчёт «до/после» по кривым набора — для порога окна; точный — после «Применить»
      </caption>
      <thead>
        <tr>
          <th>Домен</th>
          <th>T</th>
          <th>Урезано</th>
          <th>Среднее до → после</th>
          <th>CV до → после</th>
          <th>Макс. до → после</th>
          <th>Потеря металла</th>
        </tr>
      </thead>
      <tbody>
        {analysis.domains.map((d) => {
          const t = domainThreshold(values, d.domain);
          const f = forecast(d, t);
          const approx = f && !f.exact ? "≈ " : "";
          if (!f)
            return (
              <tr key={d.domain} aria-disabled>
                <th scope="row">{domainLabel(d.domain)}</th>
                <td colSpan={6}>{!t ? "не урезается" : !d.n ? "нет проб" : "порог вне кривых (P 90–99,9 %) — отчёт после «Применить»"}</td>
              </tr>
            );
          const maxAfter = d.max === null || f.threshold === null ? null : Math.min(d.max, f.threshold);
          return (
            <tr key={d.domain}>
              <th scope="row">{domainLabel(d.domain)}</th>
              <td className={own.num}>
                {approx}
                {n(f.threshold)}
                {f.p !== null && <span className={css.note}> (P {approx}{n(f.p, 2)} %)</span>}
              </td>
              <td className={own.num}>
                {approx}
                {n(f.n_cut, 0)} из {n(d.n, 0)} ({pct(f.cut_pct)})
              </td>
              <td className={own.num}>
                {n(d.mean)} → {approx}
                {n(f.mean)}
              </td>
              <td className={own.num}>
                {n(d.cv)} → {approx}
                {n(f.cv)}
              </td>
              <td className={own.num}>
                {n(d.max)} → {n(maxAfter)}
              </td>
              <td className={own.num}>
                {approx}
                {pct(f.loss_pct)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** «По доменам»: a row of T per domain of the analysis; empty — the domain is not cut. */
export function ThresholdRows({ analysis, values, onChange }: { analysis: TopcutAnalysis; values: FormValues; onChange: (v: FormValues) => void }) {
  const parsed = parseThresholds(typeof values.thresholds === "string" ? values.thresholds : "");
  const by = typeof parsed === "string" ? {} : parsed;
  const set = (domain: string, text: string) => onChange({ ...values, thresholds: thresholdsText({ ...by, [domain]: text }) });
  return (
    <table className={own.rows} data-testid="topcut-thresholds">
      <thead>
        <tr>
          <th>Домен</th>
          <th>Порог T</th>
          <th>P на кривой</th>
        </tr>
      </thead>
      <tbody>
        {analysis.domains.map((d) => {
          const text = by[d.domain] ?? "";
          const v = parseNumber(text);
          const bad = text.trim() !== "" && (v === null || v <= 0);
          const p = markedP(d, v !== null && v > 0 ? { value: v } : null);
          return (
            <tr key={d.domain}>
              <th scope="row">{domainLabel(d.domain)}</th>
              <td>
                <input className={css.input} aria-label={`Порог домена ${domainLabel(d.domain)}`} aria-invalid={bad || undefined} inputMode="decimal" placeholder="не урезать" value={text} onChange={(e) => set(d.domain, e.target.value)} />
              </td>
              <td className={own.num}>{p === null ? "—" : `${n(p, 2)} %`}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** The report of the last applied top cut of the table and field («до/после» by domain, «Всего»). */
export function AppliedReport({ result }: { result: ResultRow }) {
  const rows = reportRows(result.report);
  const stale = staleText(result);
  const when = result.created_at ? new Date(result.created_at).toLocaleString("ru-RU") : "";
  return (
    <fieldset className={css.group} data-testid="topcut-applied">
      <legend>Последняя урезка {result.params?.field ? `поля ${String(result.params.field)}` : ""}</legend>
      <p className={css.note}>
        {when && `${when}. `}Поля: {Array.isArray(result.report?.columns) ? (result.report.columns as unknown[]).join(", ") : "‹F›_CUT, ‹F›_CUT_F"}
      </p>
      {stale && <p className={css.warn}>{stale}</p>}
      {rows.length > 0 && (
        <div style={{ overflow: "auto" }}>
          <table className={own.rows}>
            <thead>
              <tr>
                <th>Домен</th>
                <th>T</th>
                <th>Урезано</th>
                <th>Среднее до → после</th>
                <th>CV до → после</th>
                <th>Макс. до → после</th>
                <th>Потеря металла</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <th scope="row">{r.domain === null ? "Всего" : domainLabel(r.domain)}</th>
                  <td className={own.num}>{r.domain === null ? "" : r.threshold === null ? "не урезался" : n(r.threshold)}</td>
                  <td className={own.num}>
                    {n(r.n_cut, 0)} из {n(r.n, 0)} ({pct(r.cut_pct)})
                  </td>
                  <td className={own.num}>
                    {n(r.mean_before)} → {n(r.mean_after)}
                  </td>
                  <td className={own.num}>
                    {n(r.cv_before)} → {n(r.cv_after)}
                  </td>
                  <td className={own.num}>
                    {n(r.max_before)} → {n(r.max_after)}
                  </td>
                  <td className={own.num}>{pct(r.loss_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </fieldset>
  );
}

/** The analysis of the source, asked again after an edit of the source (the newest answer only). */
function useAnalysis(src: SourceState): { analysis: TopcutAnalysis | null; error: string | null; reason: string | null } {
  const [analysis, setAnalysis] = useState<TopcutAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const body = useMemo(() => analysisBody(src), [src]);
  const previewer = useRef<Previewer | null>(null);
  useEffect(() => {
    const p = createPreviewer<TopcutAnalysis>(
      (a) => {
        setAnalysis(a);
        setError(null);
      },
      (e) => setError(errorText(e)),
      (_op, params) => fetchAnalysis(params),
    );
    previewer.current = p;
    return () => p.stop();
  }, []);
  useEffect(() => {
    if (typeof body === "string") {
      setAnalysis(null);
      return;
    }
    previewer.current?.request("topcut-analysis", body, 0);
  }, [body]);
  return { analysis, error, reason: typeof body === "string" ? body : null };
}

/** The results of the set of the window (read once, kept current by `composites.changed`). */
export function useSetResults(setId: number | null): ResultRow[] {
  useEffect(() => {
    watchResults();
    if (setId !== null) void loadSetResults(setId);
  }, [setId]);
  const get = () => (setId === null ? EMPTY : (resultsStore.get().bySet[setId] ?? EMPTY));
  return useSyncExternalStore(resultsStore.subscribe, get, get); // a server snapshot too: the window renders statically in tests
}
const EMPTY: ResultRow[] = [];

function LimitsRow({ text, onChange }: { text: { decile: string; percentile: string }; onChange: (t: { decile: string; percentile: string }) => void }) {
  const bad = (s: string) => {
    const v = parseNumber(s);
    return v === null || v < 0 || v > 100;
  };
  return (
    <div className={plot.limits} data-testid="topcut-limits">
      <span>Пометить, если металла больше:</span>
      <label>
        верхний дециль{" "}
        <input className={css.input} aria-label="Порог доли верхнего дециля, %" aria-invalid={bad(text.decile) || undefined} inputMode="decimal" value={text.decile} onChange={(e) => onChange({ ...text, decile: e.target.value })} /> %
      </label>
      <label>
        верхний процентиль{" "}
        <input className={css.input} aria-label="Порог доли верхнего процентиля, %" aria-invalid={bad(text.percentile) || undefined} inputMode="decimal" value={text.percentile} onChange={(e) => onChange({ ...text, percentile: e.target.value })} /> %
      </label>
    </div>
  );
}

/** The limits of the marks as typed → numbers; a wrong one — its default. */
export function limitsOf(text: { decile: string; percentile: string }): Limits {
  const v = (s: string, d: number) => {
    const x = parseNumber(s);
    return x === null || x < 0 || x > 100 ? d : x;
  };
  return { decile: v(text.decile, LIMITS.decile), percentile: v(text.percentile, LIMITS.percentile) };
}

/** The analysis of one domain: its numbers, the marks, the curves, the shares, the log-probability curve. */
export function DomainView({ d, field, values, limits, onPick }: { d: DomainAnalysis; field: string; values: FormValues; limits: Limits; onPick?: (p: number) => void }) {
  if (!d.n) return <p className={css.note}>В домене нет проб со значением и длиной.</p>;
  const t = domainThreshold(values, d.domain);
  const m = marks(d, limits);
  const f = forecast(d, t);
  return (
    <>
      <p className={css.note} data-testid="topcut-stats">
        Проб {n(d.n, 0)}, длина {n(d.length, 2)} м; среднее {n(d.mean)}, CV {n(d.cv)}, от {n(d.min)} до {n(d.max)}
      </p>
      {m.decile && (
        <p className={plot.flag} data-testid="topcut-flag-decile">
          Пометка: верхний дециль — {pct(d.top_decile_pct)} металла (больше {numberText(limits.decile)} %)
        </p>
      )}
      {m.percentile && (
        <p className={plot.flag} data-testid="topcut-flag-percentile">
          Пометка: верхний процентиль — {pct(d.top_percentile_pct)} металла (больше {numberText(limits.percentile)} %)
        </p>
      )}
      <TopcutCurves d={d} field={field} marked={markedP(d, t)} onPick={onPick} />
      <div className={plot.tables}>
        <SharesTable rows={d.deciles} caption="Доли металла по децилям (по накопленной длине)" head="Дециль" flagged={m.decile} testId="topcut-deciles" />
        <SharesTable rows={d.top} caption="Процентили верхнего дециля" head="Процентиль" flagged={m.percentile} testId="topcut-top" />
      </div>
      <LogProbPlot d={d} field={field} threshold={f?.threshold ?? (t && "value" in t ? t.value : null)} />
    </>
  );
}

export function TopcutWindow({ winId, args = {} }: { winId: string; args?: Record<string, unknown> }) {
  const t = useTool(topcut, args);
  const [activeSet] = useState(() => tablesStore.get().setId); // the set active when the window opened
  const [limitText, setLimitText] = useState({ decile: numberText(LIMITS.decile), percentile: numberText(LIMITS.percentile) });
  const [domain, setDomain] = useState<string | null>(null);
  const { analysis, error, reason } = useAnalysis(t.src);
  const results = useSetResults(t.src.setId);
  const field = t.src.fields[0] ?? null;
  const applied = latestResult(results, "topcut", t.src.table, field);
  const limits = limitsOf(limitText);
  const d = analysis?.domains.find((x) => x.domain === domain) ?? analysis?.domains[0] ?? null;
  const byDomain = t.values.by === "thresholds" && analysis !== null && analysis.domains.length > 0;
  const formParams = topcut.params.filter((p) => !(byDomain && p.key === "thresholds"));

  if (t.job) return <JobView title={topcut.title} job={t.job} winId={winId} onBack={() => t.setJob(null)} />;
  return (
    <div className={css.win} data-testid="composites-topcut">
      <div className={css.winBody}>
        <p className={css.note}>{topcut.note} Порог сам не применяется: щелчок по кривой меняет только порог окна.</p>
        <div className={own.split}>
          <div style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
            <SourcePicker needs={topcut.source} value={t.src} onChange={t.setSrc} onFields={t.setFields} activeSet={activeSet} />
            <ParamForm params={formParams} values={t.values} errors={t.checked.errors} fields={t.fields} onChange={t.setValues} />
            {byDomain && <ThresholdRows analysis={analysis!} values={t.values} onChange={t.setValues} />}
            {analysis && analysis.domains.length > 0 && (
              <div style={{ overflow: "auto" }}>
                <ForecastTable analysis={analysis} values={t.values} />
              </div>
            )}
            <fieldset className={css.group} data-testid="composites-preview">
              <legend>Предпросмотр</legend>
              <HolePicker setId={t.src.setId} value={t.hole?.id ?? null} onChange={(id, name) => t.setHole(id === null ? null : { id, name })} />
              {t.checked.reason && <p className={css.note}>Предпросмотр — когда заданы параметры: {t.checked.reason}</p>}
              {t.previewError && <p className={css.warn}>{t.previewError}</p>}
              <SummaryTable summary={t.answer?.summary ?? null} title={t.answer ? `Скважина ${t.answer.hole}` : "Скважина"} />
              {t.answer?.set_summary && <SummaryTable summary={t.answer.set_summary} title="Набор" />}
            </fieldset>
            {applied && <AppliedReport result={applied} />}
          </div>
          <fieldset className={css.group} data-testid="topcut-analysis">
            <legend>Выбор порога</legend>
            <LimitsRow text={limitText} onChange={setLimitText} />
            {reason && <p className={css.note}>Кривые — когда выбран источник: {reason}</p>}
            {error && <p className={css.warn}>{error}</p>}
            {!reason && !analysis && !error && <p className={css.note}>Расчёт кривых…</p>}
            {analysis && analysis.domains.length > 1 && (
              <label className={css.formRow}>
                <span>Домен</span>
                <select className={css.input} aria-label="Домен кривых" value={d?.domain ?? ""} onChange={(e) => setDomain(e.target.value)}>
                  {analysis.domains.map((x) => {
                    const m = marks(x, limits);
                    return (
                      <option key={x.domain} value={x.domain}>
                        {domainLabel(x.domain)}
                        {m.decile || m.percentile ? " — есть пометка" : ""}
                      </option>
                    );
                  })}
                </select>
              </label>
            )}
            {d && <DomainView d={d} field={field ?? analysis?.field ?? ""} values={t.values} limits={limits} onPick={(p) => t.setValues(pickValues(t.values, d, p))} />}
          </fieldset>
        </div>
        {t.error && (
          <p className={css.error} role="alert">
            {t.error}
          </p>
        )}
      </div>
      <ToolFooter winId={winId} reason={t.checked.reason} busy={t.busy} onApply={() => void t.apply()} extra={`Поля ${field ?? "‹F›"}_CUT и ${field ?? "‹F›"}_CUT_F пишутся в таблицу источника; «Отменить» в журнале правок скрывает их.`} />
    </div>
  );
}
