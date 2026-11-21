/**
 * The subtab «Стандарты» (ТЗ 8.4.2; ADR 0020 §11): the table of the groups CRM × lab of the active element (n, μ,
 * σ, x̄, s, the bias and its class, norm / warning / failure, two in a row, runs, swaps) and the control chart of
 * the group chosen, by date or by order, with the export PNG × 2, SVG, CSV. A new key of the calculation (data,
 * element, filter, an edit) makes one request of the groups and one of the series: the chosen group is asked with
 * the groups when it is known, else the first group with a reference after them. A click on a point opens its card
 * and shows its batch on the chart and in «Партии». Overtaken answers are dropped.
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ControlChart } from "../../charts/qaqc/ControlChart";
import { controlCsv, controlModel, pointFacts, type ControlAxis } from "../../charts/qaqc/controlModel";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { inlineRasters } from "../../charts/core/PointsCanvas";
import { errorText } from "../../shell/props";
import { Store } from "../../shell/store";
import { calc, type CalcBody, type StandardGroup, type StandardsAnswer } from "./api";
import { biasTitle, int, labTitle, num, pct, stateTitle } from "./format";
import { SampleCard } from "./SampleCard";
import { calcBody, calcKey, DROPPED, latest, pickSample, qaqcStore, reportCounts } from "./state";
import css from "./Qaqc.module.css";

export interface GroupRef {
  crm: string;
  lab: string | null;
}

/** What the subtab keeps for the session: the group chosen and the axis of the chart. */
export const standardsView = new Store<{ group: GroupRef | null; axis: ControlAxis }>({ group: null, axis: "date" });

const sameGroup = (a: GroupRef | null, g: Pick<StandardGroup, "crm" | "lab">) => !!a && a.crm === g.crm && (a.lab ?? null) === (g.lab ?? null);
const refOf = (g: StandardGroup): GroupRef => ({ crm: g.crm, lab: g.lab ?? null });

/** The group to show: the one wanted when it is there, else the first with a reference, else the first. */
export function chooseGroup(groups: readonly StandardGroup[], want: GroupRef | null): GroupRef | null {
  const g = groups.find((x) => sameGroup(want, x)) ?? groups.find((x) => x.state === "ok") ?? groups[0];
  return g ? refOf(g) : null;
}

export interface StandardsLoad {
  groups: StandardsAnswer;
  group: GroupRef | null;
  series: StandardsAnswer | null;
}

/** The series of one group. */
export const loadSeries = (body: CalcBody, group: GroupRef): Promise<StandardsAnswer> => calc("standards", { ...body, group: { crm: group.crm, lab: group.lab } });

/**
 * The groups and the series of the group shown: with a group wanted both go at once (the series is asked again
 * only when the group is gone); without one the series follows the groups. A newer load drops this one.
 */
export function loadStandards(body: CalcBody, want: GroupRef | null): Promise<StandardsLoad | typeof DROPPED> {
  return latest("standards", async () => {
    const early = want ? loadSeries(body, want).catch(() => null) : null;
    const groups = await calc("standards", body);
    const group = chooseGroup(groups.groups, want);
    if (!group) return { groups, group, series: null };
    const series = early && want && group.crm === want.crm && group.lab === want.lab ? await early : null;
    return { groups, group, series: series ?? (await loadSeries(body, group)) };
  });
}

interface Loaded {
  key: string;
  groups: StandardsAnswer;
  /** JSON of the group whose series is shown */
  seriesFor: string;
  series: StandardsAnswer | null;
}

function GroupsTable({ groups, chosen, onChoose }: { groups: readonly StandardGroup[]; chosen: GroupRef | null; onChoose: (g: GroupRef) => void }) {
  return (
    <table className={css.grid} aria-label="Группы стандартов">
      <thead>
        <tr>
          {["CRM", "Лаборатория", "n", "μ", "σ", "x̄", "s", "Смещение", "Класс", "Норма", "Предупр.", "Отказ", "Две подряд", "Серии", "Подмены"].map((h) => (
            <th key={h}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {groups.map((g) => {
          const why = stateTitle(g.state === "ok" ? null : g.state);
          return (
            <tr key={`${g.crm}|${g.lab ?? ""}`} className={sameGroup(chosen, g) ? css.chosen : undefined} title={why ? `Без оценки: ${why}` : undefined} onClick={() => onChoose(refOf(g))}>
              <th>{g.crm}</th>
              <td style={{ textAlign: "left" }}>{labTitle(g.lab)}</td>
              <td>{int(g.n)}</td>
              <td>{num(g.mu)}</td>
              <td>{num(g.sd)}</td>
              <td>{num(g.mean)}</td>
              <td>{num(g.std)}</td>
              <td className={g.bias_class === 2 ? css.fail : g.bias_class === 1 ? css.warn : undefined}>{pct(g.bias)}</td>
              <td>{why || biasTitle(g.bias_class)}</td>
              <td>{int(g.norm)}</td>
              <td className={g.warn ? css.warn : undefined}>{int(g.warn)}</td>
              <td className={g.fail ? css.fail : undefined}>{int(g.fail)}</td>
              <td>{int(g.pair_fails)}</td>
              <td>{int(g.run_flags)}</td>
              <td>{int(g.swaps)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function StandardsTab() {
  const s = useSyncExternalStore(qaqcStore.subscribe, qaqcStore.get, qaqcStore.get);
  const view = useSyncExternalStore(standardsView.subscribe, standardsView.get, standardsView.get);
  const key = calcKey(s);
  const groupKey = JSON.stringify(view.group);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  // a new key: the groups and the series (one request each)
  useEffect(() => {
    const body = calcBody();
    if (!key || !body) return;
    setBusy(true);
    loadStandards(body, standardsView.get().group).then(
      (r) => {
        if (r === DROPPED) return;
        setBusy(false);
        setError(null);
        reportCounts(key, r.groups.counts);
        // a group chosen meanwhile stays (its series follows); one gone from the groups is replaced
        const cur = standardsView.get().group;
        if (!r.groups.groups.some((g) => sameGroup(cur, g))) standardsView.set({ group: r.group });
        setLoaded({ key, groups: r.groups, seriesFor: JSON.stringify(r.group), series: r.series });
      },
      (e: unknown) => {
        setBusy(false);
        setError(errorText(e));
      },
    );
  }, [key]);
  // another group of the same groups: its series only
  useEffect(() => {
    const body = calcBody();
    const group = standardsView.get().group;
    if (!loaded || loaded.key !== key || loaded.seriesFor === groupKey || !group || !body) return;
    setBusy(true);
    latest("standards-series", () => loadSeries(body, group)).then(
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
  const group = groups?.groups.find((g) => sameGroup(view.group, g)) ?? null;
  const answer = loaded && loaded.seriesFor === groupKey ? loaded.series : null;
  const series = answer?.series ?? null;
  const limits = (answer ?? groups)?.limits;
  const model = useMemo(() => (series && group && limits ? controlModel(series, group, limits.sigma_warn, limits.sigma_fail, view.axis) : null), [series, group, limits, view.axis]);
  if (error && !groups) return <p className={css.error}>{error}</p>;
  if (!groups) return <p className={css.note}>Расчёт…</p>;
  if (!groups.groups.length) return <p className={css.note}>Стандартов CRM по элементу {groups.element} с этим фильтром нет.</p>;
  const unit = groups.unit;
  const sample = s.sample?.section === "standards" ? s.sample : null;
  const batch = s.highlight?.lab === (group?.lab ?? null) ? (s.highlight?.batch ?? null) : null;
  const picked = sample && series ? series.row.indexOf(sample.row) : -1;
  const title = group ? `${group.crm} · ${labTitle(group.lab)} · ${groups.element}` : "";
  const notes = [...(answer?.notes ?? groups.notes)];
  if (answer?.thinned && answer.thinned.shown < answer.thinned.total) notes.push(`Показано ${int(answer.thinned.shown)} из ${int(answer.thinned.total)} точек: все нарушения и равномерная выборка нормы; числа — по всем.`);
  if (model?.noDate) notes.push(`Без даты: ${int(model.noDate)} — они видны на оси «по порядку».`);
  return (
    <div className={css.split}>
      <div className={css.tableBox}>
        <GroupsTable groups={groups.groups} chosen={view.group} onChoose={(g) => standardsView.set({ group: g })} />
      </div>
      <div className={`${css.chartBox} ${busy ? css.busy : ""}`} data-testid="qaqc-control">
        <ChartToolbar svgRef={svgRef} fileName={`Контрольная карта ${title}`.replace(/[\\/:*?"<>|]/g, "_")} csv={() => (series ? controlCsv(series) : null)} csvLabel="CSV ряда" disabled={!model} prepare={inlineRasters}>
          <span className={css.seg} role="group" aria-label="Ось X">
            {(
              [
                ["date", "По дате"],
                ["order", "По порядку"],
              ] as const
            ).map(([axis, label]) => (
              <button key={axis} type="button" aria-pressed={view.axis === axis} onClick={() => standardsView.set({ axis })}>
                {label}
              </button>
            ))}
          </span>
          {busy && <span className={css.note}>Расчёт…</span>}
        </ChartToolbar>
        {error && <p className={css.error}>{error}</p>}
        {group && group.state !== "ok" && <p className={css.note}>Группа без оценки: {stateTitle(group.state)}. Значения показаны без линий μ и σ.</p>}
        {model && series ? (
          <ControlChart
            ref={svgRef}
            model={model}
            series={series}
            label={`Контрольная карта: ${title}`}
            yTitle={`${groups.element}${unit ? `, ${unit}` : ""}`}
            batch={batch}
            picked={picked >= 0 ? picked : null}
            onPick={(i) => pickSample(i === null ? null : { row: series.row[i], section: "standards", lab: group?.lab ?? null, batch: series.batch[i] ?? null, facts: pointFacts(series, i, unit) })}
          />
        ) : (
          !busy && <p className={css.note}>Выберите группу в таблице.</p>
        )}
        {notes.map((n) => (
          <p key={n} className={css.note}>
            {n}
          </p>
        ))}
        {sample && <SampleCard sample={sample} />}
      </div>
    </div>
  );
}
