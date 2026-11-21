/**
 * The row «Графики» of a set in the data tree (ТЗ 7.28, 7.0.13; ADR 0019 §11): after the displays and «Обработка
 * проб»; under it the saved charts of the set by name — a click opens the chart with its stored settings by the
 * current data (`chart.open`), the menu: «Открыть», «Переименовать…», «Удалить…» (the same commands).
 */
import { useMemo, useState, useSyncExternalStore, type MouseEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu } from "../../shell/ContextMenu";
import { askText } from "../../shell/dialogs";
import { toggleExpanded } from "../../shell/tree";
import { TreeRow } from "../../shell/TreeRow";
import type { ChartRow } from "./api";
import { commands } from "./commands";
import { chartsOfSet, chartsStore } from "./state";

export const GROUP_TITLE = "Графики";

const KIND_TITLES: Record<string, string> = Object.fromEntries(commands.filter((c) => c.id.startsWith("chart.")).map((c) => [c.id.slice(6), c.title]));

/** «Q-Q график», «Гистограмма»… — the kind of a saved chart in words. */
export const kindTitle = (kind: string): string => KIND_TITLES[kind] ?? "График";

const getCharts = () => chartsStore.get().charts;

/** The items of the menu of a saved chart: each runs its command (unavailable — with the reason). */
export function chartMenu(chart: ChartRow, reason: (id: string) => string | null, run: (id: string, args: Record<string, unknown>) => void) {
  return [
    { label: "Открыть", reason: reason("chart.open"), run: () => run("chart.open", { chart_id: chart.id }) },
    {
      label: "Переименовать…",
      reason: reason("chart.rename"),
      run: () =>
        void askText({ title: "Переименовать график", label: "Название", initial: chart.name, okLabel: "Переименовать" }).then((name) => {
          if (name && name.trim() && name.trim() !== chart.name) run("chart.rename", { chart_id: chart.id, name: name.trim() });
        }),
    },
    { label: "Удалить…", reason: reason("chart.delete"), run: () => run("chart.delete", { chart_id: chart.id }) },
  ];
}

export function useSetCharts(setId: number): ChartRow[] {
  const all = useSyncExternalStore(chartsStore.subscribe, getCharts, getCharts);
  return useMemo(() => chartsOfSet(all, setId), [all, setId]);
}

/** The sets that have saved charts (for the arrow of a set row). */
export function useSetChartsAll(): Set<number> {
  const all = useSyncExternalStore(chartsStore.subscribe, getCharts, getCharts);
  return useMemo(() => new Set(all.flatMap((c) => (c.set_id === null ? [] : [c.set_id]))), [all]);
}

export function ChartRows({ setId, level = 3 }: { setId: number; level?: number }) {
  const app = useApp();
  const list = useSetCharts(setId);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [picked, setPicked] = useState<number | null>(null);
  if (!list.length) return null;
  const open = !collapsed.includes("group");
  const run = (id: string, args: Record<string, unknown>) => void app.run(id, args);
  const menu = (c: ChartRow, e: MouseEvent) => {
    e.preventDefault();
    openMenu(e.clientX, e.clientY, chartMenu(c, (id) => app.registry.reason(id), run));
  };
  return (
    <div role="group" aria-label={`${GROUP_TITLE} набора`}>
      <TreeRow
        label={GROUP_TITLE}
        ariaLabel={`${GROUP_TITLE}, ${list.length}`}
        level={level}
        expanded={open}
        onToggle={() => setCollapsed((c) => toggleExpanded(c, "group"))}
        title={`Сохранённые графики набора: ${list.length}. Щелчок по графику — открыть по текущим данным`}
        testId={`tree-charts-${setId}`}
        after={<span>{list.length}</span>}
      />
      {open &&
        list.map((c) => (
          <TreeRow
            key={c.id}
            label={c.name}
            level={level + 1}
            selected={picked === c.id}
            onSelect={() => {
              setPicked(c.id);
              run("chart.open", { chart_id: c.id });
            }}
            onActivate={() => run("chart.open", { chart_id: c.id })}
            title={`${kindTitle(c.kind)} «${c.name}»: щелчок — открыть с сохранёнными настройками по текущим данным`}
            testId={`tree-chart-${c.id}`}
            onContextMenu={(e) => menu(c, e)}
          />
        ))}
    </div>
  );
}
