/**
 * The swath plots in the section «Аудит и статистика» of the data tree (ТЗ 7.30 «объект раздела «Аудит»»,
 * 7.0.13; ADR 0019 §11): by name, each with the check box «в 3D» (`stats.swath_3d`); a click chooses it (its
 * handles in 3D — Д-01), a double click or Enter opens its window (`stats.swath {swath_id}`); the menu: «Открыть»,
 * «Переименовать…», «Удалить…» — the same commands as the charts of a set (`chart.rename`, `chart.delete`).
 */
import { useMemo, useSyncExternalStore, type MouseEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu } from "../../shell/ContextMenu";
import { askText } from "../../shell/dialogs";
import { Glyph } from "../../shell/ObjectRows";
import { TreeRow } from "../../shell/TreeRow";
import tree from "../../shell/DataTree.module.css";
import type { ChartRow } from "../charts/api";
import { viewStore } from "../scene/bind";
import { shownSwaths, SWATHS_KEY } from "../scene/viewSync";
import { selectSwath, swathsByName, swathsStore } from "./state";

/** The items of the menu of a swath: each runs its command (unavailable — with the reason). */
export function swathMenu(row: ChartRow, reason: (id: string) => string | null, run: (id: string, args: Record<string, unknown>) => void) {
  return [
    { label: "Открыть", reason: reason("stats.swath"), run: () => run("stats.swath", { swath_id: row.id }) },
    {
      label: "Переименовать…",
      reason: reason("chart.rename"),
      run: () =>
        void askText({ title: "Переименовать swath plot", label: "Название", initial: row.name, okLabel: "Переименовать" }).then((name) => {
          if (name && name.trim() && name.trim() !== row.name) run("chart.rename", { chart_id: row.id, name: name.trim() });
        }),
    },
    { label: "Удалить…", reason: reason("chart.delete"), run: () => run("chart.delete", { chart_id: row.id }) },
  ];
}

export function SwathTree() {
  const app = useApp();
  const getState = () => swathsStore.get();
  const { swaths: rows, loaded, selected } = useSyncExternalStore(swathsStore.subscribe, getState, getState);
  const getKey = () => viewStore.get()[SWATHS_KEY];
  const key = useSyncExternalStore(viewStore.subscribe, getKey, getKey);
  const shown = useMemo(() => new Set(shownSwaths(key)), [key]);
  const list = useMemo(() => swathsByName(rows), [rows]);
  const run = (id: string, args: Record<string, unknown>) => void app.run(id, args);
  if (!list.length) return <div className={tree.none}>{loaded ? "Нет swath plot — «+» создаёт новый" : "Нет объектов"}</div>;
  const menu = (row: ChartRow, e: MouseEvent) => {
    e.preventDefault();
    openMenu(e.clientX, e.clientY, swathMenu(row, (id) => app.registry.reason(id), run));
  };
  return (
    <div role="group" aria-label="Swath plot">
      {list.map((row) => (
        <TreeRow
          key={row.id}
          label={row.name}
          level={2}
          selected={selected === row.id}
          onSelect={() => selectSwath(row.id)}
          onActivate={() => run("stats.swath", { swath_id: row.id })}
          check={{ checked: shown.has(row.id), label: `${row.name}: в 3D`, onChange: (on) => run("stats.swath_3d", { swath_id: row.id, visible: on }) }}
          title={`Swath plot «${row.name}»: двойной щелчок — открыть окно; галочка — полосы в 3D`}
          testId={`tree-swath-${row.id}`}
          glyph={<Glyph icon="swath" />}
          onContextMenu={(e) => menu(row, e)}
        />
      ))}
    </div>
  );
}
