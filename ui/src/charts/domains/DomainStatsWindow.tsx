/**
 * The window «По доменам» (ТЗ 8.3; ADR 0019 §4): the table of the statistics of a field by domains — n, length,
 * weighted and declustered (field `DECL_W`; none — empty with the hint) mean, median, σ, CV, min, max, P10, P50,
 * P90 — and the total; the export is the CSV of the table. A value that cannot be computed stays «—» (empty in the
 * CSV), never 0. An edit computes again 300 ms later.
 */
import { useEffect, useMemo, useState } from "react";
import { errorText } from "../../shell/props";
import { createCalculator, defaultField, type Calculator } from "../../modules/charts/api";
import { computeDomains, type DomainRow, type DomainsResult } from "../../modules/charts/apiMore";
import type { ChartWindowProps } from "../../modules/charts/bind";
import { useSavedChart } from "../../modules/charts/saved";
import { needsWeightField, type DomainsSettings } from "../../modules/charts/settings";
import { ChartWindow } from "../ChartWindow";
import { formatNumber } from "../core/axisScale";
import { exportChart } from "../core/ChartToolbar";
import { csvText } from "../core/csv";
import { DomainSource, keepField, useStatsFields } from "../core/Pickers";
import { SampleFilterForm } from "../SampleFilterForm";
import css from "../core/Chart.module.css";
import { domainNotes, groupLabel } from "../box/model";

/** Settings of «По доменам» have no view: the body is the settings. */
export type DomainsRequest = DomainsSettings;

export function domainsRequest(s: DomainsSettings): DomainsRequest | null {
  return !s.field || needsWeightField(s) ? null : s;
}

export function domainsCalculator(apply: (r: DomainsResult) => void, fail: (e: unknown) => void, busy?: (on: boolean) => void, load: (body: DomainsRequest) => Promise<DomainsResult> = computeDomains): Calculator<DomainsRequest> {
  return createCalculator(load, apply, fail, busy);
}

export const DOMAINS_HEADER = ["Домен", "n", "Длина, м", "Среднее", "Декл. среднее", "Медиана", "σ", "CV", "Мин", "Макс", "P10", "P50", "P90"];

const VALUES: ((r: DomainRow) => number | null)[] = [(r) => r.n, (r) => r.length, (r) => r.mean, (r) => r.decl_mean, (r) => r.median, (r) => r.std, (r) => r.cv, (r) => r.min, (r) => r.max, (r) => r.p10, (r) => r.p50, (r) => r.p90];

/** The rows of the table: the domains, then «Итого». */
export function domainsTable(r: DomainsResult): string[][] {
  const row = (d: DomainRow, label: string) => [label, ...VALUES.map((f, i) => (i === 0 ? d.n.toLocaleString("ru-RU") : formatNumber(f(d), 5)))];
  return [...r.rows.map((d) => row(d, groupLabel(d.label, r.domain))), row(r.total, "Итого")];
}

export function domainsCsv(r: DomainsResult): string {
  const row = (d: DomainRow, label: string) => [label, ...VALUES.map((f) => f(d))];
  return csvText(DOMAINS_HEADER, [...r.rows.map((d) => row(d, groupLabel(d.label, r.domain))), row(r.total, "Итого")]);
}

/** The assistant's text: the name, the total and each domain with n and its mean. */
export function domainsSummary(name: string, r: DomainsResult): string {
  const t = r.total;
  const parts = r.rows.slice(0, 12).map((d) => `${groupLabel(d.label, r.domain)}: n ${d.n.toLocaleString("ru-RU")}, среднее ${formatNumber(d.mean, 4)}`);
  return `${name}: проб ${t.n.toLocaleString("ru-RU")}, среднее ${formatNumber(t.mean, 4)}, медиана ${formatNumber(t.median, 4)}, CV ${formatNumber(t.cv, 3)}; ${parts.join("; ")}`;
}

export function DomainStatsWindow(props: ChartWindowProps<"domains">) {
  const { setId, setName } = props;
  const [s, setS] = useState<DomainsSettings>(props.settings);
  const [result, setResult] = useState<DomainsResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const set = (patch: Partial<DomainsSettings>) => setS((prev) => ({ ...prev, ...patch }));
  const saved = useSavedChart("domains", props, s, setS);

  const f = useStatsFields(setId, s.table);
  useEffect(() => {
    const x = f.fields;
    if (x) setS((prev) => ({ ...prev, table: prev.table ?? x.table, field: keepField(x, prev.field) ?? defaultField(x), weight_field: keepField(x, prev.weight_field) }));
  }, [f.fields]);
  const calc = useMemo(() => domainsCalculator((r) => (setResult(r), setError(null)), (e) => setError(errorText(e)), setBusy), []);
  useEffect(() => () => calc.stop(), [calc]);
  const request = useMemo(() => domainsRequest(s), [s]);
  useEffect(() => {
    if (f.fields && request) calc.request(request);
  }, [calc, f.fields, request]);

  const save = () =>
    exportChart("csv", `По доменам ${setName} ${s.field ?? ""}`.trim(), null, () => (result ? domainsCsv(result) : null))
      .then((path) => setNote(path ? `Сохранено: ${path}` : null))
      .catch((e: unknown) => setNote(errorText(e)));
  const toolbar = (
    <div className={css.toolbar} role="toolbar" aria-label="Экспорт таблицы">
      <button type="button" disabled={!result} onClick={save} title="Таблица: разделитель «;», десятичная точка">
        CSV таблицы
      </button>
      {note && <span className={css.note}>{note}</span>}
    </div>
  );
  const table = result && (
    <table className={css.stats} data-testid="domains-stats">
      <thead>
        <tr>
          {DOMAINS_HEADER.map((h) => (
            <th key={h}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {domainsTable(result).map((row, i) => (
          <tr key={i}>
            {row.map((v, k) => (k ? <td key={k}>{v}</td> : <th key={k}>{v}</th>))}
          </tr>
        ))}
      </tbody>
    </table>
  );
  return (
    <ChartWindow
      settings={
        <>
          <DomainSource setId={setId} value={s} fields={f.fields} onChange={set} />
          <SampleFilterForm value={s} fields={f.fields?.numeric ?? []} onChange={set} />
        </>
      }
      toolbar={toolbar}
      busy={busy}
      error={error ?? f.error}
      empty={!f.fields ? "Расчёт…" : !f.fields.numeric.length ? "В таблице нет числовых полей." : !request ? "Выберите поле веса." : "Расчёт…"}
      note={saved.note}
      chart={table}
      footer={
        result && (
          <>
            {!result.decl_field && <p className={css.muted}>Декластерированное среднее пусто: {result.decl_hint ?? "в таблице нет поля весов DECL_W — его создаёт «Обработка проб → Декластеризация»."}</p>}
            <p className={css.muted}>{domainNotes(result.counts, s).join(" · ")}</p>
            {result.conventions && <p className={css.muted}>{result.conventions}</p>}
          </>
        )
      }
    />
  );
}
