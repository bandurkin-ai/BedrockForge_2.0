/**
 * The subtab «Дубликаты» (ТЗ 8.4.2; ADR 0020 §4, §11): the switch of the type (field, crushed, pulp — `qaqc.open`
 * with `kind`), the summary (pairs, used, left out by reason, the shares of HARD and the criterion of the type, the
 * means, r, RMA), the scatter x1 — x2 with 1:1, ±10 %, ±20 % and RMA, the ranked HARD with the threshold and the
 * share of the criterion (thinned by the core to 2000 points), the Thompson–Howard chart with the table P(c) (or
 * why it is not drawn: «мало пар» — fewer than `th.need`, «одно среднее» — `no_fit`). Each chart exports PNG × 2,
 * SVG, CSV. A new key of the calculation or another type makes one request; an overtaken answer is dropped. A click
 * on a point of the scatter opens the card of its sample.
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode, type RefObject } from "react";
import { PairScatter } from "../../charts/qaqc/PairScatter";
import { excludedRows, excludedTotal, type ThBlock, pairCsv, rankedNote, pairFacts, pairModel, rankedCsv, rankedModel, thCsv, thModel, verdictTitle } from "../../charts/qaqc/pairModel";
import { RankedHardChart } from "../../charts/qaqc/RankedHardChart";
import { ThChart, ThTable } from "../../charts/qaqc/ThChart";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { inlineRasters } from "../../charts/core/PointsCanvas";
import { useApp } from "../../shell/AppContext";
import { errorText } from "../../shell/props";
import { calc, type CalcBody, type DuplicatesAnswer, type Thinned } from "./api";
import { DUP_KINDS, DUP_TITLES, TYPE_TITLES, type DupKind } from "./commands";
import { int, num, pct } from "./format";
import { SampleCard } from "./SampleCard";
import { calcBody, calcKey, DROPPED, latest, pickSample, qaqcStore, reportCounts } from "./state";
import css from "./Qaqc.module.css";

/** The duplicates of one type (a newer load drops this one). */
export const loadDuplicates = (body: CalcBody): Promise<DupAnswer | typeof DROPPED> => latest("duplicates", () => calc("duplicates", body) as Promise<DupAnswer>);

const safeName = (t: string) => t.replace(/[\\/:*?"<>|]/g, "_");

/** «Показано N из M пар …» when the core thinned the points, and the notes of the answer. */
export function duplicatesNotes(a: Pick<DuplicatesAnswer, "notes"> & { thinned?: Thinned }): string[] {
  const out = [...(a.notes ?? [])];
  if (a.thinned && a.thinned.shown < a.thinned.total) out.push(`Показано ${int(a.thinned.shown)} из ${int(a.thinned.total)} пар: все пары с HARD выше порога и равномерная выборка остальных; числа — по всем.`);
  return out;
}

/** The answer of the core: beyond ADR 0020 §10 — `kind`, the block `th` with `need`, `groups.n`, `precision[].q`. */
export type DupAnswer = Omit<DuplicatesAnswer, "th"> & { kind?: string; th: ThBlock; thinned?: Thinned };

/**
 * Why the Thompson–Howard chart is not drawn; null — it is. `few_pairs` — fewer pairs than `need` (max(50,
 * 2·group): at least two groups make a line); `no_fit` — every group has the same mean of its pairs.
 */
export function thStateText(a: { used: number; th: Pick<ThBlock, "state" | "need" | "group"> }): string | null {
  const th = a.th;
  if (th.state === "ok") return null;
  if (th.state === "few_pairs") {
    const need = th.need ?? Math.max(50, 2 * (th.group || 0));
    return `Мало пар для графика Томпсона–Ховарта: в расчёте ${int(a.used)}, нужно не меньше ${int(need)} (наибольшее из 50 и двух групп по ${int(th.group)} пар).`;
  }
  if (th.state === "no_fit") return "График Томпсона–Ховарта не построен: у всех групп пар одно и то же среднее — прямую через них провести нельзя.";
  return `График Томпсона–Ховарта не построен: ${th.state}.`;
}

/** The summary of the duplicates of a type: the pairs, those left out by reason, the shares and the criterion. */
export function DupSummary({ answer, kind }: { answer: Omit<DuplicatesAnswer, "th">; kind: DupKind }) {
  const a = answer;
  const left = excludedTotal(a.excluded);
  const u = a.unit ? ` ${a.unit}` : "";
  const rows: [string, ReactNode, string?][] = [
    ["Тип", `${TYPE_TITLES[kind]} (${kind})`],
    ["Пар", int(a.pairs)],
    ["В расчёте", int(a.used)],
    ["Отброшено", int(left)],
    ...excludedRows(a.excluded).map(([t, n]): [string, ReactNode, string?] => [`— ${t}`, int(n)]),
    ["HARD ≤ 10 %", pct(a.share10)],
    ["HARD ≤ 20 %", pct(a.share20)],
    ["HARD ≤ 30 %", pct(a.share30)],
    [`HARD ≤ ${num(a.hard_limit, 3)} % (порог типа)`, pct(a.share_at_limit)],
    [`Критерий: не меньше ${num(a.share_limit, 3)} % пар`, verdictTitle(a.passed), a.passed === false ? css.fail : undefined],
    ["x̄ исходных", `${num(a.stats?.mean1, 5)}${u}`],
    ["x̄ дубликатов", `${num(a.stats?.mean2, 5)}${u}`],
    ["r Пирсона", num(a.stats?.pearson, 4)],
    ["RMA: a; b", `${num(a.stats?.rma?.a, 4)}; ${num(a.stats?.rma?.b, 4)}`],
  ];
  return (
    <table className={css.grid} aria-label="Сводка дубликатов" data-testid="qaqc-dup-summary">
      <tbody>
        {rows.map(([k, v, cls]) => (
          <tr key={k}>
            <th>{k}</th>
            <td className={cls}>{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ChartBox({ svgRef, name, csv, csvLabel, disabled, busy, children }: { svgRef: RefObject<SVGSVGElement | null>; name: string; csv: () => string | null; csvLabel: string; disabled: boolean; busy: boolean; children: ReactNode }) {
  return (
    <div className={`${css.chartBox} ${busy ? css.busy : ""}`} style={{ flex: "1 1 400px", maxWidth: 520 }}>
      <ChartToolbar svgRef={svgRef} fileName={safeName(name)} csv={csv} csvLabel={csvLabel} disabled={disabled} prepare={inlineRasters} />
      {children}
    </div>
  );
}

interface Loaded {
  key: string;
  answer: DupAnswer;
}

export function DuplicatesTab() {
  const app = useApp();
  const s = useSyncExternalStore(qaqcStore.subscribe, qaqcStore.get, qaqcStore.get);
  const key = calcKey(s);
  const loadKey = key === null ? null : JSON.stringify([key, s.dupKind]);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scatterRef = useRef<SVGSVGElement | null>(null);
  const rankedRef = useRef<SVGSVGElement | null>(null);
  const thRef = useRef<SVGSVGElement | null>(null);
  useEffect(() => {
    const st = qaqcStore.get();
    const body = calcBody(st, { kind: st.dupKind });
    if (!key || !loadKey || !body) return;
    setBusy(true);
    loadDuplicates(body).then(
      (answer) => {
        if (answer === DROPPED) return;
        setBusy(false);
        setError(null);
        reportCounts(key, answer.counts);
        setLoaded({ key: loadKey, answer });
      },
      (e: unknown) => {
        setBusy(false);
        setError(errorText(e));
      },
    );
  }, [loadKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const a = loaded?.answer ?? null;
  const scatter = useMemo(() => (a ? pairModel(a.points, a.hard_limit, a.stats?.rma) : null), [a]);
  const ranked = useMemo(() => (a ? rankedModel(a.ranked, a.hard_limit, a.share_limit, a.share_at_limit) : null), [a]);
  const th = useMemo(() => (a && a.th.state === "ok" ? thModel(a.th) : null), [a]);
  const kinds = (
    <span className={css.seg} role="group" aria-label="Тип дубликатов">
      {DUP_KINDS.map((k) => (
        <button key={k} type="button" aria-pressed={s.dupKind === k} onClick={() => void app.run("qaqc.open", { section: "duplicates", kind: k })}>
          {DUP_TITLES[k]}
        </button>
      ))}
    </span>
  );
  if (error && !a)
    return (
      <div className={css.empty}>
        {kinds}
        <p className={css.error}>{error}</p>
      </div>
    );
  if (!a || !scatter || !ranked) return <p className={css.note}>Расчёт…</p>;
  const stale = loaded?.key !== loadKey;
  const kind = s.dupKind;
  const unit = a.unit;
  const u = unit ? `, ${unit}` : "";
  const title = `${DUP_TITLES[kind]} дубликаты · ${a.element}`;
  const sample = s.sample?.section === "duplicates" ? s.sample : null;
  const picked = sample ? a.points.row.indexOf(sample.row) : -1;
  const thText = thStateText(a);
  return (
    <div className={css.split} data-testid="qaqc-duplicates">
      <div className={css.tableBox} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div className={css.row}>
          {kinds}
          {(busy || stale) && <span className={css.note}>Расчёт…</span>}
        </div>
        {error && <p className={css.error}>{error}</p>}
        <DupSummary answer={a} kind={kind} />
        {th && <ThTable precision={a.th.precision} unit={unit} />}
        {thText && <p className={css.note}>{thText}</p>}
        {duplicatesNotes(a).map((n) => (
          <p key={n} className={css.note}>
            {n}
          </p>
        ))}
        {sample && <SampleCard sample={sample} />}
      </div>
      {a.used === 0 ? (
        <p className={css.note}>Пар {DUP_TITLES[kind]} дубликатов в расчёте нет: проверьте фильтр, тип и отброшенные пары.</p>
      ) : (
        <>
          <ChartBox svgRef={scatterRef} name={`Рассеяние ${title}`} csv={() => pairCsv(a.points)} csvLabel="CSV пар" disabled={!scatter.n} busy={busy}>
            <PairScatter
              ref={scatterRef}
              model={scatter}
              points={a.points}
              label={`Рассеяние пар: ${title}`}
              xTitle={`Исходная проба${u}`}
              yTitle={`Дубликат${u}`}
              limit={a.hard_limit}
              pearson={a.stats?.pearson ?? null}
              rma={a.stats?.rma ?? null}
              picked={picked >= 0 ? picked : null}
              onPick={(i) => pickSample(i === null ? null : { row: a.points.row[i], section: "duplicates", lab: null, batch: null, facts: pairFacts(a.points, i, unit, ["Исходная проба", "Дубликат"]) })}
            />
          </ChartBox>
          <ChartBox svgRef={rankedRef} name={`Ранжированный HARD ${title}`} csv={() => rankedCsv(a.ranked)} csvLabel="CSV" disabled={!ranked.n} busy={busy}>
            <RankedHardChart ref={rankedRef} model={ranked} label={`Ранжированный HARD: ${title}`} note={rankedNote(a.used, ranked.n)} />
          </ChartBox>
          {th && (
            <ChartBox svgRef={thRef} name={`Томпсон–Ховарт ${title}`} csv={() => thCsv(a.th)} csvLabel="CSV групп" disabled={!th.n} busy={busy}>
              <ThChart ref={thRef} model={th} th={a.th} label={`Томпсон–Ховарт: ${title}`} unit={unit} />
            </ChartBox>
          )}
        </>
      )}
    </div>
  );
}
