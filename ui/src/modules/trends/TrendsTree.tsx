/**
 * The section «Тренды» of the data tree (ТЗ 7.35 Д-15; ADR 0022 §15): the trends by name, each with the check box
 * «в 3D» (`trend.show`, the view key `ellipsoids`); a click chooses it, a double click or Enter opens its window
 * (`trend.open`); the menu: «Изменить», «Показать / Скрыть», «Где используется», «Удалить…» — the commands of the
 * registry. «+» of the section — `trend.open` (by the orchestrator: shell/treeSections.ts).
 */
import { useMemo, useSyncExternalStore, type MouseEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu, type MenuItem } from "../../shell/ContextMenu";
import type { IconName } from "../../shell/Icon";
import { Glyph } from "../../shell/ObjectRows";
import { TreeRow } from "../../shell/TreeRow";
import tree from "../../shell/DataTree.module.css";
import { viewStore } from "../scene/bind";
import type { Trend } from "./api";
import { structuralSummary } from "./structural";
import { ELLIPSOIDS_KEY, selectTrend, shownObjects, trendsByName, trendsStore } from "./state";

/** The items of the menu of a trend: each runs its command (unavailable — with the reason). */
export function trendMenu(t: Trend, shown: boolean, reason: (id: string) => string | null, run: (id: string, args: Record<string, unknown>) => void): MenuItem[] {
  return [
    { label: "Изменить", reason: reason("trend.open"), run: () => run("trend.open", { trend_id: t.id }) },
    { label: t.kind === "structural" ? (shown ? "Скрыть диски в 3D" : "Показать диски в 3D") : shown ? "Скрыть в 3D" : "Показать в 3D", reason: reason("trend.show"), run: () => run("trend.show", { trend_id: t.id, visible: !shown }) },
    { label: "Где используется", reason: reason("trend.usage"), run: () => run("trend.usage", { trend_id: t.id }) },
    { label: "Удалить…", reason: reason("trend.delete"), run: () => run("trend.delete", { trend_id: t.id }) },
  ];
}

const n = (v: number) => v.toLocaleString("ru-RU", { maximumSignificantDigits: 4 });

/** The tooltip of a row: the angles and the ratios. */
export const trendTitleText = (t: Trend): string =>
  t.kind === "structural"
    ? `${structuralSummary(t)}${t.sourceLost ? "; источник удалён" : ""}\nДвойной щелчок — изменить; галочка — диски в 3D`
    : `Тренд «${t.name}»: ang1 ${n(t.ang1)}°, ang2 ${n(t.ang2)}°, ang3 ${n(t.ang3)}°; 1 : ${n(t.ratios[1])} : ${n(t.ratios[2])}${t.used ? "; используется" : ""}\nДвойной щелчок — изменить; галочка — эллипсоид в 3D`;

export function TrendsTree() {
  const app = useApp();
  const getState = () => trendsStore.get();
  const { trends, loaded, selected } = useSyncExternalStore(trendsStore.subscribe, getState, getState);
  const getKey = () => viewStore.get()[ELLIPSOIDS_KEY];
  const key = useSyncExternalStore(viewStore.subscribe, getKey, getKey);
  const shown = useMemo(() => new Set(shownObjects(key)), [key]);
  const list = useMemo(() => trendsByName(trends), [trends]);
  const run = (id: string, args: Record<string, unknown>) => void app.run(id, args);
  if (!list.length) return <div className={tree.none}>{loaded ? "Нет трендов — «+» создаёт новый" : "Нет объектов"}</div>;
  const menu = (t: Trend, e: MouseEvent) => {
    e.preventDefault();
    openMenu(e.clientX, e.clientY, trendMenu(t, shown.has(t.id), (id) => app.registry.reason(id), run));
  };
  return (
    <div role="group" aria-label="Тренды">
      {list.map((t) => (
        <TreeRow
          key={t.id}
          label={t.name}
          level={2}
          selected={selected === t.id}
          onSelect={() => selectTrend(t.id)}
          onActivate={() => run("trend.open", { trend_id: t.id })}
          check={{ checked: shown.has(t.id), label: `${t.name}: ${t.kind === "structural" ? "диски в 3D" : "в 3D"}`, onChange: (on) => run("trend.show", { trend_id: t.id, visible: on }) }}
          title={trendTitleText(t)}
          testId={`tree-trend-${t.id}`}
          glyph={<Glyph icon={"trend" as IconName} />}
          onContextMenu={(e) => menu(t, e)}
        />
      ))}
    </div>
  );
}
