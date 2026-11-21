/**
 * The subtab «Внешний контроль» (ТЗ 8.4.2; ADR 0020 §4, §11): the table of the groups element × (the main lab, the
 * check lab) — pairs, used and left out, the means, the bias, the paired t-test (t, p, degrees of freedom; empty with
 * a note when every difference is equal), r, the RMA slope and intercept with their bootstrap intervals and the
 * number of realizations, the shares of HARD and the criterion `hard_ump` — with its CSV as answered; the scatter
 * (main — check) and the ranked HARD of the group chosen. A new key of the calculation makes one request of the
 * groups and one of the points (the chosen group is asked with the groups when it is known, else the first group
 * with pairs after them). Overtaken answers are dropped. A click on a point opens the card of its sample.
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { PairScatter } from "../../charts/qaqc/PairScatter";
import { excludedTotal, pairCsv, pairFacts, pairModel, rankedCsv, rankedModel, rankedNote, verdictTitle } from "../../charts/qaqc/pairModel";
import { RankedHardChart } from "../../charts/qaqc/RankedHardChart";
import { ChartToolbar, exportChart } from "../../charts/core/ChartToolbar";
import { csvText, type CsvCell } from "../../charts/core/csv";
import { inlineRasters } from "../../charts/core/PointsCanvas";
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { calc, type CalcBody, type ExternalAnswer, type ExternalGroup, type Thinned } from "./api";
import { int, labTitle, num, pct } from "./format";
import { SampleCard } from "./SampleCard";
import { calcBody, calcKey, DROPPED, latest, pickSample, qaqcStore, reportCounts } from "./state";
import css from "./Qaqc.module.css";

export interface ExternalRef {
  lab_main: string | null;
  lab_check: string | null;
}

/** What the subtab keeps for the session: the group chosen. */
export const externalView = new Store<{ group: ExternalRef | null }>({ group: null });

/** A group as the core answers it: beyond ADR 0020 §10 — `share30`. */
export type ExtGroup = ExternalGroup & { share30?: number | null };

/**
 * The answer of the core: beyond ADR 0020 §10 — `hard_limit` (`hard_ump`) and `share_limit` at the top; with `group`
 * — `points` and `ranked`, null when the group is gone under the filter; `thinned` past `max_points`.
 */
export type ExtAnswer = Omit<ExternalAnswer, "groups" | "points" | "ranked"> & {
  hard_limit?: number;
  share_limit?: number;
  groups: ExtGroup[];
  points?: ExternalAnswer["points"] | null;
  ranked?: ExternalAnswer["ranked"] | null;
  thinned?: Thinned;
};
type Detail = ExtAnswer;

/** The HARD threshold and the share of the criterion of an answer: from its top, else from its limits. */
export function externalLimits(a: Pick<ExtAnswer, "hard_limit" | "share_limit" | "limits"> | null | undefined): { limit: number | null; share: number } {
  return { limit: a?.hard_limit ?? a?.limits?.hard_ump ?? null, share: a?.share_limit ?? a?.limits?.hard_share ?? 90 };
}

const sameGroup = (a: ExternalRef | null, g: ExternalRef) => !!a && (a.lab_main ?? null) === (g.lab_main ?? null) && (a.lab_check ?? null) === (g.lab_check ?? null);
const refOf = (g: ExternalRef): ExternalRef => ({ lab_main: g.lab_main ?? null, lab_check: g.lab_check ?? null });
export const groupTitle = (g: ExternalRef): string => `${labTitle(g.lab_main)} → ${labTitle(g.lab_check)}`;

/** The group to show: the one wanted when it is there, else the first with pairs used, else the first. */
export function chooseExternal(groups: readonly ExternalGroup[], want: ExternalRef | null): ExternalRef | null {
  const g = groups.find((x) => sameGroup(want, x)) ?? groups.find((x) => x.used > 0) ?? groups[0];
  return g ? refOf(g) : null;
}

/** The points and the ranked HARD of one group. */
export const loadExternalGroup = (body: CalcBody, group: ExternalRef): Promise<Detail> => calc("external", { ...body, group: { lab_main: group.lab_main, lab_check: group.lab_check } }) as Promise<Detail>;

export interface ExternalLoad {
  groups: ExtAnswer;
  group: ExternalRef | null;
  detail: Detail | null;
}

/** The groups and the points of the group shown (as «Стандарты»: with a group wanted both go at once). */
export function loadExternal(body: CalcBody, want: ExternalRef | null): Promise<ExternalLoad | typeof DROPPED> {
  return latest("external", async () => {
    const early = want ? loadExternalGroup(body, want).catch(() => null) : null;
    const groups = (await calc("external", body)) as ExtAnswer;
    const group = chooseExternal(groups.groups, want);
    if (!group) return { groups, group, detail: null };
    const detail = early && sameGroup(want, group) ? await early : null;
    return { groups, group, detail: detail ?? (await loadExternalGroup(body, group)) };
  });
}

/** «1,002 [0,981; 1,024]»: a value with its interval; missing — «—». */
export function withInterval(v: number | null | undefined, ci: readonly [number, number] | null | undefined): string {
  const iv = ci && Number.isFinite(ci[0]) && Number.isFinite(ci[1]) ? ` [${num(ci[0], 4)}; ${num(ci[1], 4)}]` : "";
  return `${num(v, 4)}${iv}`;
}

export const EXTERNAL_CSV_HEADER = [
  "lab_main", "lab_check", "pairs", "used", "no_parent", "missing", "negative", "zero", "below", "mean_main", "mean_check", "bias", "t", "p", "df", "pearson",
  "rma_a", "rma_b", "a_ci_lo", "a_ci_hi", "b_ci_lo", "b_ci_hi", "n_boot", "seed", "share10", "share20", "share30", "share_at_limit", "passed", "notes",
] as const;

/** The CSV of the table: every group, every number as the core answered it (empty — empty, never 0). */
export function externalCsv(groups: readonly ExtGroup[]): string {
  const rows: CsvCell[][] = groups.map((g) => [
    g.lab_main, g.lab_check, g.pairs, g.used, g.excluded?.no_parent, g.excluded?.missing, g.excluded?.negative, g.excluded?.zero, g.excluded?.below,
    g.mean_main, g.mean_check, g.bias, g.t, g.p, g.df, g.pearson,
    g.rma?.a, g.rma?.b, g.rma?.a_ci?.[0], g.rma?.a_ci?.[1], g.rma?.b_ci?.[0], g.rma?.b_ci?.[1], g.rma?.n_boot, g.rma?.seed,
    g.share10, g.share20, g.share30, g.share_at_limit, g.passed === null || g.passed === undefined ? null : g.passed ? 1 : 0, (g.notes ?? []).join(" | ") || null,
  ]);
  return csvText(EXTERNAL_CSV_HEADER, rows);
}

const HEAD = ["Основная", "Контроль", "Пар", "В расчёте", "Отброшено", "x̄ осн.", "x̄ контр.", "Смещение", "t", "p", "Ст. св.", "r", "Наклон b [95 %]", "Свободный член a [95 %]", "Реализаций", "≤ 10 %", "≤ 20 %", "≤ 30 %", "≤ порога", "Критерий"];

/** The table of the groups; a group whose t and p are empty shows them «—» with its notes (a mark and a line below). */
export function ExternalTable({ groups, chosen, limit, onChoose }: { groups: readonly ExtGroup[]; chosen: ExternalRef | null; limit: number | null; onChoose?: (g: ExternalRef) => void }) {
  const head = HEAD.map((h) => (h === "≤ порога" && limit !== null ? `≤ ${num(limit, 3)} %` : h));
  const noted = groups.filter((g) => g.notes?.length);
  return (
    <>
      <table className={css.grid} aria-label="Группы внешнего контроля">
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => {
            const note = g.notes?.length ? g.notes.join(" ") : undefined;
            const mark = note ? `${noted.indexOf(g) + 1}` : null;
            return (
              <tr key={`${g.lab_main ?? ""}|${g.lab_check ?? ""}`} className={sameGroup(chosen, g) ? css.chosen : undefined} onClick={() => onChoose?.(refOf(g))}>
                <th>{labTitle(g.lab_main)}</th>
                <td style={{ textAlign: "left" }}>{labTitle(g.lab_check)}</td>
                <td>{int(g.pairs)}</td>
                <td>{int(g.used)}</td>
                <td>{int(excludedTotal(g.excluded))}</td>
                <td>{num(g.mean_main)}</td>
                <td>{num(g.mean_check)}</td>
                <td>{pct(g.bias)}</td>
                <td title={g.t === null ? note : undefined} data-cell="t">
                  {num(g.t, 4)}
                  {g.t === null && mark && <sup>{mark}</sup>}
                </td>
                <td title={g.p === null ? note : undefined} data-cell="p">
                  {num(g.p, 4)}
                  {g.p === null && mark && <sup>{mark}</sup>}
                </td>
                <td>{num(g.df, 6)}</td>
                <td>{num(g.pearson, 4)}</td>
                <td data-cell="b">{withInterval(g.rma?.b, g.rma?.b_ci)}</td>
                <td data-cell="a">{withInterval(g.rma?.a, g.rma?.a_ci)}</td>
                <td>{int(g.rma?.n_boot)}</td>
                <td>{pct(g.share10)}</td>
                <td>{pct(g.share20)}</td>
                <td>{pct(g.share30)}</td>
                <td>{pct(g.share_at_limit)}</td>
                <td className={g.passed === false ? css.fail : undefined}>{verdictTitle(g.passed)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {noted.map((g, k) => (
        <p key={`n${k}`} className={css.note} data-note={k + 1}>
          {`${k + 1}. ${groupTitle(g)}: ${g.notes.join(" ")}`}
        </p>
      ))}
    </>
  );
}

interface Loaded {
  key: string;
  groups: ExtAnswer;
  /** JSON of the group whose points are shown */
  detailFor: string;
  detail: Detail | null;
}

export function ExternalTab() {
  const s = useSyncExternalStore(qaqcStore.subscribe, qaqcStore.get, qaqcStore.get);
  const view = useSyncExternalStore(externalView.subscribe, externalView.get, externalView.get);
  const key = calcKey(s);
  const groupKey = JSON.stringify(view.group);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const scatterRef = useRef<SVGSVGElement | null>(null);
  const rankedRef = useRef<SVGSVGElement | null>(null);
  // a new key: the groups and the points (one request each)
  useEffect(() => {
    const body = calcBody();
    if (!key || !body) return;
    setBusy(true);
    loadExternal(body, externalView.get().group).then(
      (r) => {
        if (r === DROPPED) return;
        setBusy(false);
        setError(null);
        reportCounts(key, r.groups.counts);
        const cur = externalView.get().group;
        if (!r.groups.groups.some((g) => sameGroup(cur, g))) externalView.set({ group: r.group });
        setLoaded({ key, groups: r.groups, detailFor: JSON.stringify(r.group), detail: r.detail });
      },
      (e: unknown) => {
        setBusy(false);
        setError(errorText(e));
      },
    );
  }, [key]);
  // another group of the same groups: its points only
  useEffect(() => {
    const body = calcBody();
    const group = externalView.get().group;
    if (!loaded || loaded.key !== key || loaded.detailFor === groupKey || !group || !body) return;
    setBusy(true);
    latest("external-group", () => loadExternalGroup(body, group)).then(
      (detail) => {
        if (detail === DROPPED) return;
        setBusy(false);
        setError(null);
        setLoaded((l) => (l && l.key === key ? { ...l, detailFor: groupKey, detail } : l));
      },
      (e: unknown) => {
        setBusy(false);
        setError(errorText(e));
      },
    );
  }, [groupKey, loaded?.key]); // eslint-disable-line react-hooks/exhaustive-deps
  const saveCsv = (name: string, text: string) =>
    void exportChart("csv", name.replace(/[\\/:*?"<>|]/g, "_"), null, () => text).then(
      (path) => setSaved(path ? `Сохранено: ${path}` : null),
      (e: unknown) => setSaved(errorText(e)),
    );
  const groups = loaded?.groups ?? null;
  const group = groups?.groups.find((g) => sameGroup(view.group, g)) ?? null;
  const detail = loaded && loaded.detailFor === groupKey ? loaded.detail : null;
  const { limit, share } = externalLimits(detail ?? groups);
  const scatter = useMemo(() => (detail?.points && group && limit !== null ? pairModel(detail.points, limit, group.rma) : null), [detail, group, limit]);
  const ranked = useMemo(() => (detail?.ranked && group && limit !== null ? rankedModel(detail.ranked, limit, share, group.share_at_limit) : null), [detail, group, limit, share]);
  if (error && !groups) return <p className={css.error}>{error}</p>;
  if (!groups) return <p className={css.note}>Расчёт…</p>;
  if (!groups.groups.length) return <p className={css.note}>Проб внешнего контроля (UMP) по элементу {groups.element} с этим фильтром нет.</p>;
  const unit = groups.unit;
  const u = unit ? `, ${unit}` : "";
  const title = group ? `${groupTitle(group)} · ${groups.element}` : "";
  const sample = s.sample?.section === "external" ? s.sample : null;
  const picked = sample && detail?.points ? detail.points.row.indexOf(sample.row) : -1;
  const notes = [...(detail?.notes ?? groups.notes)];
  if (detail?.thinned && detail.thinned.shown < detail.thinned.total) notes.push(`Показано ${int(detail.thinned.shown)} из ${int(detail.thinned.total)} пар: все пары с HARD выше порога и равномерная выборка остальных; числа — по всем.`);
  return (
    <div className={css.split} data-testid="qaqc-external">
      <div className={css.tableBox} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <div className={css.row}>
          <button type="button" className={css.btn} title="Таблица групп: разделитель «;», десятичная точка" onClick={() => saveCsv(`Внешний контроль ${groups.element}`, externalCsv(groups.groups))}>
            CSV таблицы
          </button>
          {busy && <span className={css.note}>Расчёт…</span>}
          {saved && <span className={css.note}>{saved}</span>}
        </div>
        <ExternalTable groups={groups.groups} chosen={view.group} limit={limit} onChoose={(g) => externalView.set({ group: g })} />
        {notes.map((n) => (
          <p key={n} className={css.note}>
            {n}
          </p>
        ))}
        {error && <p className={css.error}>{error}</p>}
        {sample && <SampleCard sample={sample} />}
      </div>
      {!group || group.used === 0 ? (
        <p className={css.note}>{group ? `В группе ${groupTitle(group)} пар в расчёте нет.` : "Выберите группу в таблице."}</p>
      ) : (
        <>
          <div className={`${css.chartBox} ${busy ? css.busy : ""}`} style={{ flex: "1 1 400px", maxWidth: 520 }}>
            <ChartToolbar svgRef={scatterRef} fileName={`Рассеяние ${title}`.replace(/[\\/:*?"<>|]/g, "_")} csv={() => (detail?.points ? pairCsv(detail.points) : null)} csvLabel="CSV пар" disabled={!scatter?.n} prepare={inlineRasters} />
            {scatter && detail?.points ? (
              <PairScatter
                ref={scatterRef}
                model={scatter}
                points={detail.points}
                label={`Рассеяние пар: ${title}`}
                xTitle={`Основная лаборатория${u}`}
                yTitle={`Контрольная лаборатория${u}`}
                limit={limit ?? 0}
                pearson={group.pearson}
                rma={group.rma}
                picked={picked >= 0 ? picked : null}
                onPick={(i) => pickSample(i === null ? null : { row: detail.points!.row[i], section: "external", lab: group.lab_check, batch: null, facts: pairFacts(detail.points!, i, unit, ["Основная", "Контроль"]) })}
              />
            ) : (
              !busy && <p className={css.note}>{detail && detail.points === null ? "Группы с этим фильтром больше нет: выберите другую в таблице." : "Точки группы не получены."}</p>
            )}
          </div>
          {ranked && detail?.ranked && (
            <div className={`${css.chartBox} ${busy ? css.busy : ""}`} style={{ flex: "1 1 400px", maxWidth: 520 }}>
              <ChartToolbar svgRef={rankedRef} fileName={`Ранжированный HARD ${title}`.replace(/[\\/:*?"<>|]/g, "_")} csv={() => rankedCsv(detail.ranked!)} csvLabel="CSV" disabled={!ranked.n} />
              <RankedHardChart ref={rankedRef} model={ranked} label={`Ранжированный HARD: ${title}`} note={rankedNote(group.used, ranked.n)} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
