/**
 * «Менеджер серий» (ТЗ 8.1; ADR 0016 §4): the list of the series of the project (create, copy, delete — after an
 * own question, make active), the parameters of the chosen one (its window «Параметры…»), the lines of its
 * sections on a plan and the table of its sections with their own windows (`SeriesTable.tsx`). Every action is a
 * command of the registry (`series.copy`, `series.delete`, `series.activate`): logged, with its reason when off.
 */
import { useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { useStore } from "../../shell/useStore";
import css from "../../shell/PropsParts.module.css";
import { emptyOverview, overviewBounds, planFrame, seriesTraces } from "../../viz/section/miniPlan";
import { viewStore } from "../scene/bind";
import { currentScene } from "../scene/sceneHost";
import type { SeriesRow } from "./seriesApi";
import { openNewSeries, openSeriesParams } from "./seriesActions";
import { fmt, newForm, SHADOW_TITLES, TYPE_TITLES } from "./seriesForm";
import { boxCorners, geometryOf, seriesStore, visibleBoxes } from "./seriesState";
import { SeriesLines } from "./SectionMap";
import { SeriesTable } from "./SeriesTable";
import { freeLine, activeSeriesOf } from "./state";

const PW = 300;
const PH = 200;

/** One line of the parameters: «Вертикальная · A = 90° · s 0…100 м, шаг 25 м · окно 12,5 / 12,5 м · 5 разр.» */
export function seriesSummary(row: SeriesRow): string {
  const p = row.params;
  const parts = [TYPE_TITLES[p.type]];
  if (p.type === "polyline") parts.push(`${(p.polyline ?? []).length} вершин, коридор ${fmt(p.corridor ?? 25)} м`);
  else {
    parts.push(p.p2 ? `P1 → P2 (${fmt(p.p2[0])}; ${fmt(p.p2[1])})` : `A = ${fmt(p.az ?? 90)}°`);
    if (p.type === "inclined") parts.push(`φ = ${fmt(p.phi ?? 90)}°`);
    parts.push(`s ${fmt(p.s0 ?? 0)}…${fmt(p.s1 ?? 0)} м, шаг ${fmt(p.step ?? 50)} м`, `окно ${fmt(p.towards ?? 25)} / ${fmt(p.away ?? 25)} м`);
  }
  parts.push(`${row.count} разр.`, `тень: ${SHADOW_TITLES[p.shadow ?? "prev"].toLowerCase()}`);
  return parts.join(" · ");
}

function SeriesPlan({ row, current }: { row: SeriesRow; current: number }) {
  const overview = useMemo(() => currentScene()?.planOverview() ?? emptyOverview(), []);
  const geom = geometryOf(row);
  if (!geom) return null;
  const traces = seriesTraces(geom, current, overviewBounds(overview, []));
  const f = planFrame(overview, null, PW, PH, traces.extent);
  return (
    <svg viewBox={`0 0 ${PW} ${PH}`} width={PW} height={PH} role="img" aria-label="Линии серии на плане" data-testid="series-plan" style={{ border: "1px solid var(--border)", borderRadius: "var(--radius)", background: "var(--bg-input)" }}>
      {overview.collars.map(([x, y], i) => (
        <circle key={i} cx={f.sx(x)} cy={f.sy(y)} r={1.5} style={{ fill: "var(--text-faint)" }} />
      ))}
      <SeriesLines f={f} traces={traces} />
      {geom.kind === "plan" && (
        <text x={PW / 2} y={PH - 8} textAnchor="middle" style={{ fill: "var(--text-dim)", fontSize: 11 }}>
          Планы — горизонтальные сечения
        </text>
      )}
    </svg>
  );
}

export function SeriesManager({ selected: initial }: { selected?: number }) {
  const app = useApp();
  const state = useStore(seriesStore, (s) => s);
  const active = useStore(viewStore, activeSeriesOf);
  const [picked, setPicked] = useState<number | null>(initial ?? active?.id ?? null);
  const [error, setError] = useState<string | null>(null);
  const row = state.list.find((r) => r.id === picked) ?? state.list[0] ?? null;
  /** A command of the registry: its result in the status line, its refusal under the table. */
  const act = async (command: "series.copy" | "series.delete" | "series.activate", seriesId: number) => {
    setError(null);
    const r = await app.registry.execute(command, { series_id: seriesId });
    if (r.ok) app.store.set({ statusNote: r.result });
    else setError(r.result);
  };
  const off = (command: string) => app.registry.reason(command) ?? undefined;
  const create = () => openNewSeries(newForm(freeLine(viewStore.get()), boxCorners(visibleBoxes(app.store.get().view.hidden))));
  return (
    <div className={css.win} data-testid="series-manager">
      <div className={css.winBody} style={{ flexDirection: "row", alignItems: "stretch" }}>
        <div className={css.group} style={{ width: 230, flex: "none" }}>
          <div className={css.layers} role="listbox" aria-label="Серии разрезов" style={{ maxHeight: "none", flex: 1 }}>
            {state.list.length === 0 && <span className={css.note}>{state.loaded ? "В проекте нет серий разрезов" : "Список серий читается…"}</span>}
            {state.list.map((r) => (
              <div key={r.id} role="option" aria-selected={r.id === row?.id} tabIndex={0} data-testid={`series-row-${r.id}`} className={css.check} style={{ cursor: "pointer", fontWeight: r.id === row?.id ? 600 : undefined }} onClick={() => setPicked(r.id)} onDoubleClick={() => void act("series.activate", r.id)} onKeyDown={(e) => e.key === "Enter" && setPicked(r.id)}>
                <span>{r.number}.</span> <span>{r.name}</span>
                <span className={css.count} style={{ marginLeft: "auto", color: "var(--text-faint)" }}>
                  {active?.id === r.id ? "● " : ""}
                  {r.count}
                </span>
              </div>
            ))}
          </div>
          <div className={css.inline} style={{ flexWrap: "wrap" }}>
            <button type="button" className={css.btn} data-testid="series-new" onClick={create}>
              Создать…
            </button>
            <button type="button" className={css.btn} data-testid="series-copy" disabled={!row || !!off("series.copy")} title={off("series.copy")} onClick={() => row && void act("series.copy", row.id)}>
              Копировать
            </button>
            <button type="button" className={css.btn} data-testid="series-delete" disabled={!row || !!off("series.delete")} title={off("series.delete")} onClick={() => row && void act("series.delete", row.id)}>
              Удалить
            </button>
          </div>
        </div>
        <div className={css.group} style={{ flex: 1, minWidth: 0 }}>
          {row ? (
            <>
              <div className={css.inline}>
                <b>{row.name}</b>
                <span className={css.grow} style={{ flex: 1 }} />
                <button type="button" className={css.btn} data-testid="series-edit" onClick={() => openSeriesParams(row)}>
                  Параметры…
                </button>
                <button type="button" className={css.primary} data-testid="series-activate" disabled={active?.id === row.id} onClick={() => void act("series.activate", row.id)}>
                  Сделать активной
                </button>
              </div>
              <p className={css.note} data-testid="series-summary">
                {seriesSummary(row)}
              </p>
              <SeriesPlan row={row} current={active?.id === row.id ? (active.seg ?? active.k) : 0} />
              <SeriesTable row={row} />
            </>
          ) : (
            <p className={css.note}>Создайте серию: «Создать…» — параметры по линии разреза и видимым данным.</p>
          )}
          {(error || state.error) && (
            <p className={css.error} role="alert" data-testid="series-manager-error">
              {error ?? `${state.error?.message} ${state.error?.hint}`}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
