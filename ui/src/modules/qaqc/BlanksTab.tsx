/**
 * The subtab «Бланки» (ТЗ 8.4.2; ADR 0020 §4, §11): the table of the laboratories of the active element (n, DL,
 * k_б·DL, норма / отказ / не оценён), the blanks of the laboratory chosen by date or by order with the line
 * k_б·DL and the carry-over scatter «предыдущая проба партии — бланк» (r and pairs), each with the export PNG × 2,
 * SVG, CSV. A new key of the calculation makes one request of the laboratories and one of the series (the
 * laboratory chosen is asked with them when it is known). A click on a point opens its card and shows its batch.
 * «Не оценён» (no DL) is never counted as the norm. Overtaken answers are dropped.
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { BlankChart, CarryChart } from "../../charts/qaqc/BlankChart";
import { blankCsv, blankFacts, blankLines, blankModel, carryCsv, carryModel, carryTitle } from "../../charts/qaqc/blankModel";
import type { ControlAxis } from "../../charts/qaqc/controlModel";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { inlineRasters } from "../../charts/core/PointsCanvas";
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { calc, type BlankGroup, type BlanksAnswer, type CalcBody } from "./api";
import { int, labTitle, num } from "./format";
import { SampleCard } from "./SampleCard";
import { calcBody, calcKey, DROPPED, latest, pickSample, qaqcStore, reportCounts } from "./state";
import css from "./Qaqc.module.css";

export interface LabRef {
  lab: string | null;
}

/** What the subtab keeps for the session: the laboratory chosen and the axis of the chart. */
export const blanksView = new Store<{ group: LabRef | null; axis: ControlAxis }>({ group: null, axis: "date" });

const sameLab = (a: LabRef | null, g: Pick<BlankGroup, "lab">) => !!a && (a.lab ?? null) === (g.lab ?? null);

/** The laboratory to show: the one wanted when it is there, else the first with an assessed blank, else the first. */
export function chooseLab(groups: readonly BlankGroup[], want: LabRef | null): LabRef | null {
  const g = groups.find((x) => sameLab(want, x)) ?? groups.find((x) => x.norm + x.fail > 0) ?? groups[0];
  return g ? { lab: g.lab ?? null } : null;
}

export interface BlanksLoad {
  groups: BlanksAnswer;
  group: LabRef | null;
  series: BlanksAnswer | null;
}

export const loadBlankSeries = (body: CalcBody, group: LabRef): Promise<BlanksAnswer> => calc("blanks", { ...body, group: { lab: group.lab } });

/** The laboratories and the series of the one shown: with one wanted both go at once; else the series follows. */
export function loadBlanks(body: CalcBody, want: LabRef | null): Promise<BlanksLoad | typeof DROPPED> {
  return latest("blanks", async () => {
    const early = want ? loadBlankSeries(body, want).catch(() => null) : null;
    const groups = await calc("blanks", body);
    const group = chooseLab(groups.groups, want);
    if (!group) return { groups, group, series: null };
    const series = early && want && group.lab === want.lab ? await early : null;
    return { groups, group, series: series ?? (await loadBlankSeries(body, group)) };
  });
}

/** Why a laboratory has no line k_б·DL, or null (the core gives `mixed` when its blanks have different DL). */
export function limitNote(g: BlankGroup & { mixed?: boolean }): string | null {
  if (typeof g.limit === "number" && Number.isFinite(g.limit)) return null;
  if (g.mixed) return "У бланков лаборатории разные DL: линия порога не показана, статус — по DL каждой пробы.";
  if (g.not_assessed > 0) return `DL не задан ни в настройке, ни в записях: не оценено бланков ${int(g.not_assessed)}. Задайте DL в «Настройка QAQC».`;
  return "У бланков нет общего DL: линия порога не показана.";
}

function LabsTable({ groups, chosen, onChoose }: { groups: readonly BlankGroup[]; chosen: LabRef | null; onChoose: (g: LabRef) => void }) {
  return (
    <table className={css.grid} aria-label="Бланки по лабораториям">
      <thead>
        <tr>
          {["Лаборатория", "n", "DL", "k_б·DL", "Норма", "Отказ", "Не оценён"].map((h) => (
            <th key={h}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {groups.map((g) => (
          <tr key={g.lab ?? ""} className={sameLab(chosen, g) ? css.chosen : undefined} onClick={() => onChoose({ lab: g.lab ?? null })}>
            <th>{labTitle(g.lab)}</th>
            <td>{int(g.n)}</td>
            <td>{num(g.dl)}</td>
            <td>{num(g.limit)}</td>
            <td>{int(g.norm)}</td>
            <td className={g.fail ? css.fail : undefined}>{int(g.fail)}</td>
            <td className={g.not_assessed ? css.na : undefined}>{int(g.not_assessed)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface Loaded {
  key: string;
  groups: BlanksAnswer;
  seriesFor: string;
  series: BlanksAnswer | null;
}

const fileName = (t: string) => t.replace(/[\\/:*?"<>|]/g, "_");

export function BlanksTab() {
  const s = useSyncExternalStore(qaqcStore.subscribe, qaqcStore.get, qaqcStore.get);
  const view = useSyncExternalStore(blanksView.subscribe, blanksView.get, blanksView.get);
  const key = calcKey(s);
  const groupKey = JSON.stringify(view.group);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const carryRef = useRef<SVGSVGElement | null>(null);
  useEffect(() => {
    const body = calcBody();
    if (!key || !body) return;
    setBusy(true);
    loadBlanks(body, blanksView.get().group).then(
      (r) => {
        if (r === DROPPED) return;
        setBusy(false);
        setError(null);
        reportCounts(key, r.groups.counts);
        const cur = blanksView.get().group;
        if (!r.groups.groups.some((g) => sameLab(cur, g))) blanksView.set({ group: r.group });
        setLoaded({ key, groups: r.groups, seriesFor: JSON.stringify(r.group), series: r.series });
      },
      (e: unknown) => {
        setBusy(false);
        setError(errorText(e));
      },
    );
  }, [key]);
  useEffect(() => {
    const body = calcBody();
    const group = blanksView.get().group;
    if (!loaded || loaded.key !== key || loaded.seriesFor === groupKey || !group || !body) return;
    setBusy(true);
    latest("blanks-series", () => loadBlankSeries(body, group)).then(
      (series) => {
        if (series === DROPPED) return;
        setBusy(false);
        setError(null);
        setLoaded((l) => (l && l.key === key ? { ...l, seriesFor: groupKey, series } : l));
      },
      (e: unknown) => {
        setBusy(false);
        setError(errorText(e));
      },
    );
  }, [groupKey, loaded?.key]); // eslint-disable-line react-hooks/exhaustive-deps
  const groups = loaded?.groups ?? null;
  const group = groups?.groups.find((g) => sameLab(view.group, g)) ?? null;
  const answer = loaded && loaded.seriesFor === groupKey ? loaded.series : null;
  const series = answer?.series ?? null;
  const kBlank = (answer ?? groups)?.limits?.k_blank;
  const model = useMemo(() => (series && group ? blankModel(series, group, kBlank, view.axis) : null), [series, group, kBlank, view.axis]);
  const carry = useMemo(() => (series ? carryModel(series) : null), [series]);
  if (error && !groups) return <p className={css.error}>{error}</p>;
  if (!groups) return <p className={css.note}>Расчёт…</p>;
  if (!groups.groups.length) return <p className={css.note}>Бланков по элементу {groups.element} с этим фильтром нет.</p>;
  const unit = groups.unit;
  const yTitle = `${groups.element}${unit ? `, ${unit}` : ""}`;
  const sample = s.sample?.section === "blanks" ? s.sample : null;
  const batch = s.highlight?.lab === (group?.lab ?? null) ? (s.highlight?.batch ?? null) : null;
  const picked = sample && series ? series.row.indexOf(sample.row) : -1;
  const title = group ? `${labTitle(group.lab)} · ${groups.element}` : "";
  const notes = [...(answer?.notes ?? groups.notes)];
  const why = group ? limitNote(group) : null;
  if (why) notes.push(why);
  if (answer?.thinned && answer.thinned.shown < answer.thinned.total) notes.push(`Показано ${int(answer.thinned.shown)} из ${int(answer.thinned.total)} бланков: все отказы и равномерная выборка остальных; числа — по всем.`);
  if (model?.noDate) notes.push(`Без даты: ${int(model.noDate)} — они видны на оси «по порядку».`);
  const onPick = (i: number | null) => {
    if (i === null || !series) return pickSample(null);
    pickSample({ row: series.row[i], section: "blanks", lab: group?.lab ?? null, batch: series.batch[i] ?? null, facts: blankFacts(series, i, unit, group) });
  };
  const lines = group ? blankLines(group, kBlank) : [];
  return (
    <div className={css.split}>
      <div className={css.tableBox}>
        <LabsTable groups={groups.groups} chosen={view.group} onChoose={(g) => blanksView.set({ group: g })} />
      </div>
      <div className={`${css.chartBox} ${busy ? css.busy : ""}`} data-testid="qaqc-blanks">
        <ChartToolbar svgRef={svgRef} fileName={fileName(`Бланки ${title}`)} csv={() => (series ? blankCsv(series) : null)} csvLabel="CSV ряда" disabled={!model} prepare={inlineRasters}>
          <span className={css.seg} role="group" aria-label="Ось X">
            {(
              [
                ["date", "По дате"],
                ["order", "По порядку"],
              ] as const
            ).map(([axis, label]) => (
              <button key={axis} type="button" aria-pressed={view.axis === axis} onClick={() => blanksView.set({ axis })}>
                {label}
              </button>
            ))}
          </span>
          {busy && <span className={css.note}>Расчёт…</span>}
        </ChartToolbar>
        {error && <p className={css.error}>{error}</p>}
        {model && series ? (
          <BlankChart ref={svgRef} model={model} series={series} label={`Бланки: ${title}`} yTitle={yTitle} batch={batch} picked={picked >= 0 ? picked : null} onPick={onPick} />
        ) : (
          !busy && <p className={css.note}>Выберите лабораторию в таблице.</p>
        )}
        {notes.map((n) => (
          <p key={n} className={css.note}>
            {n}
          </p>
        ))}
        {sample && <SampleCard sample={sample} />}
        {carry && series && (
          <section className={css.carry} aria-label="Перенос загрязнения">
            <h4 className={css.subhead}>Перенос загрязнения: {carryTitle(answer?.carryover, carry.n)}</h4>
            <ChartToolbar svgRef={carryRef} fileName={fileName(`Перенос загрязнения ${title}`)} csv={() => carryCsv(series, carry)} csvLabel="CSV пар" disabled={!carry.n} prepare={inlineRasters} />
            {carry.n ? (
              <CarryChart ref={carryRef} model={carry} series={series} lines={lines} label={`Перенос загрязнения: ${title}`} xTitle={`Предыдущая проба партии, ${yTitle}`} yTitle={`Бланк, ${yTitle}`} batch={batch} picked={picked >= 0 ? picked : null} onPick={onPick} />
            ) : (
              <p className={css.note}>Нет пар «предыдущая проба партии — бланк» со значениями обеих проб.</p>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
