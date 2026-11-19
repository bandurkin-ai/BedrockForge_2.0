/**
 * The «Серии разрезов» section of the data tree (ТЗ 8.1; ADR 0016 §4, decision 8): the series of the project and
 * under each its sections (a polyline — its segments) — at most 1 000 rows, then «Перейти…» (`Ctrl+G`). A click on
 * a section goes to it; a double click on a series makes it active; the menu of a series: make active, parameters,
 * copy, rename, delete. The keys are those of the tree (WAI-ARIA, `TreeRow`), as in «Виды».
 */
import { useEffect, useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu, type MenuItem } from "../../shell/ContextMenu";
import { askText } from "../../shell/dialogs";
import { TreeRow } from "../../shell/TreeRow";
import { useStore } from "../../shell/useStore";
import css from "../tables/Tables.module.css";
import { viewStore } from "../scene/bind";
import type { SeriesRow } from "./seriesApi";
import { ensureTable, geometryOf, seriesStore, stepsOf, type SeriesState } from "./seriesState";
import { activeSeriesOf, type ActiveSeries } from "./state";

/** Rows of sections under one series before «Перейти…» (decision 8). */
export const TREE_SECTIONS_MAX = 1000;

export type SeriesTreeRow =
  | { kind: "series"; row: SeriesRow; expanded: boolean; active: boolean }
  | { kind: "section"; seriesId: number; index: number; label: string; active: boolean }
  | { kind: "more"; seriesId: number; rest: number }
  | { kind: "loading"; seriesId: number };

/** The rows of the section: every series, the sections of the expanded ones (names from their tables). */
export function seriesTreeRows(state: Pick<SeriesState, "list" | "tables">, expanded: ReadonlySet<number>, active: ActiveSeries | null): SeriesTreeRow[] {
  const out: SeriesTreeRow[] = [];
  for (const row of state.list) {
    const open = expanded.has(row.id);
    const isActive = active?.id === row.id;
    out.push({ kind: "series", row, expanded: open, active: isActive });
    if (!open) continue;
    const geom = geometryOf(row);
    if (!geom) continue;
    const poly = geom.kind === "polyline";
    const steps = stepsOf(geom);
    const current = isActive ? (poly ? (active.seg ?? 0) : active.k) : -1;
    const table = state.tables[row.id];
    if (!poly && !(table && table.version === row.version)) {
      out.push({ kind: "loading", seriesId: row.id });
      continue;
    }
    const shown = Math.min(steps, TREE_SECTIONS_MAX);
    for (let i = 0; i < shown; i++) {
      const label = poly ? `Сегмент ${i + 1}` : `${i + 1}. ${table!.sections[i]?.name ?? ""}`;
      out.push({ kind: "section", seriesId: row.id, index: i, label, active: i === current });
    }
    if (steps > shown) out.push({ kind: "more", seriesId: row.id, rest: steps - shown });
  }
  return out;
}

export interface SeriesAction {
  label: string;
  command: string;
  args: Record<string, unknown>;
  /** asks the new name first (`series.rename`) */
  rename?: boolean;
  separator?: boolean;
}

/** The menu of a series in the tree: every item is a command of the registry. */
export function seriesMenu(row: SeriesRow, active: boolean): SeriesAction[] {
  const id = { series_id: row.id };
  return [
    { label: active ? "Активная серия" : "Сделать активной", command: "series.activate", args: id },
    { label: "Параметры…", command: "series.params", args: id },
    { label: "Копировать", command: "series.copy", args: id },
    { label: "Переименовать…", command: "series.rename", args: id, rename: true },
    { label: "Удалить", command: "series.delete", args: id, separator: true },
  ];
}

export function SeriesTree() {
  const app = useApp();
  const state = useStore(seriesStore, (s) => s);
  const active = useStore(viewStore, activeSeriesOf);
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set(active ? [active.id] : []));
  useEffect(() => {
    if (active) setExpanded((e) => (e.has(active.id) ? e : new Set([...e, active.id])));
  }, [active?.id]);
  useEffect(() => {
    for (const id of expanded) if (state.list.some((r) => r.id === id)) void ensureTable(id).catch(() => undefined);
  }, [expanded, state.list]);
  const rows = useMemo(() => seriesTreeRows(state, expanded, active), [state, expanded, active]);
  if (!state.list.length) return <div className={css.treeNone}>{state.loaded ? "Нет серий: «+» — новая серия" : "Серии читаются…"}</div>;

  const run = (command: string, args: Record<string, unknown> = {}) => void app.run(command, args);
  const toggle = (id: number) =>
    setExpanded((e) => {
      const n = new Set(e);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const menu = (row: SeriesRow, isActive: boolean) => (e: React.MouseEvent) => {
    e.preventDefault();
    const items: MenuItem[] = seriesMenu(row, isActive).flatMap((a) => [
      ...(a.separator ? [{ separator: true, label: "" }] : []),
      {
        label: a.label,
        reason: a.command === "series.activate" && isActive ? "Серия уже активна" : app.registry.reason(a.command),
        run: a.rename
          ? () => void askText({ title: "Переименовать серию", label: "Название", initial: row.name, okLabel: "Переименовать" }).then((name) => name && name !== row.name && run(a.command, { ...a.args, name }))
          : () => run(a.command, a.args),
      },
    ]);
    openMenu(e.clientX, e.clientY, items);
  };

  return (
    <div role="group" aria-label="Серии разрезов">
      {rows.map((r) => {
        if (r.kind === "series")
          return (
            <TreeRow
              key={`s${r.row.id}`}
              label={`${r.row.number}. ${r.row.name}`}
              level={2}
              expanded={r.expanded}
              onToggle={() => toggle(r.row.id)}
              selected={r.active}
              testId={`series-tree-${r.row.id}`}
              title={`${r.row.name}: ${r.row.count} разр. — двойной щелчок делает серию активной`}
              onSelect={() => !r.expanded && toggle(r.row.id)}
              onActivate={() => run("series.activate", { series_id: r.row.id })}
              onContextMenu={menu(r.row, r.active)}
            />
          );
        if (r.kind === "section")
          return <TreeRow key={`s${r.seriesId}:${r.index}`} label={r.label} level={3} selected={r.active} testId={`series-tree-${r.seriesId}-${r.index}`} title="Щелчок — перейти к разрезу" onSelect={() => run("series.activate", { series_id: r.seriesId, k: r.index + 1 })} />;
        if (r.kind === "more")
          return (
            <TreeRow
              key={`m${r.seriesId}`}
              label={`Перейти… (ещё ${r.rest})`}
              level={3}
              testId={`series-tree-${r.seriesId}-more`}
              title="Разрез по имени или номеру (Ctrl+G)"
              onSelect={() => {
                if (active?.id !== r.seriesId) void app.run("series.activate", { series_id: r.seriesId }).then(() => run("series.goto"));
                else run("series.goto");
              }}
            />
          );
        return <div key={`l${r.seriesId}`} className={css.treeNone}>Имена разрезов читаются…</div>;
      })}
    </div>
  );
}
