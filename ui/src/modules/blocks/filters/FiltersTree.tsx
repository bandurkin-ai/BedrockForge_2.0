/**
 * The section «Фильтры» of the data tree (ТЗ 7.35 Д-02; ADR 0023 §13): the filters of the library by name, a click
 * chooses one, a double click or Enter puts it on edit in the window «Фильтры» (`filters.manage`); the menu: «Изменить»,
 * «Где используется», «Удалить…» — the commands of the registry. «+» of the section — `filter.save` without arguments
 * (the window with a new filter; by the orchestrator: shell/treeSections.ts).
 */
import { useMemo, useSyncExternalStore, type MouseEvent } from "react";
import { useApp } from "../../../shell/AppContext";
import { openMenu, type MenuItem } from "../../../shell/ContextMenu";
import type { IconName } from "../../../shell/Icon";
import { Glyph } from "../../../shell/ObjectRows";
import { TreeRow } from "../../../shell/TreeRow";
import tree from "../../../shell/DataTree.module.css";
import type { FilterRow } from "./api";
import { filtersByName, filtersStore, selectFilter } from "./state";

/** The items of the menu of a filter: each runs its command (unavailable — with the reason). */
export function filterMenu(f: Pick<FilterRow, "id">, reason: (id: string) => string | null, run: (id: string, args: Record<string, unknown>) => void): MenuItem[] {
  return [
    { label: "Изменить", reason: reason("filters.manage"), run: () => run("filters.manage", { filter_id: f.id }) },
    { label: "Где используется", reason: reason("filter.usage"), run: () => run("filter.usage", { filter_id: f.id }) },
    { label: "Удалить…", reason: reason("filter.delete"), run: () => run("filter.delete", { filter_id: f.id }) },
  ];
}

/** The tooltip of a row: the filter in words, the note, the way to edit it. */
export const filterTitleText = (f: FilterRow): string => `Фильтр «${f.name}»: ${f.title}${f.note ? `\n${f.note}` : ""}\nДвойной щелчок — изменить в окне «Фильтры»`;

export function FiltersTree() {
  const app = useApp();
  const getState = () => filtersStore.get();
  const { rows, loaded, selected } = useSyncExternalStore(filtersStore.subscribe, getState, getState);
  const list = useMemo(() => filtersByName(rows), [rows]);
  const run = (id: string, args: Record<string, unknown>) => void app.run(id, args);
  if (!list.length) return <div className={tree.none}>{loaded ? "Нет фильтров — «+» создаёт новый" : "Нет объектов"}</div>;
  const menu = (f: FilterRow, e: MouseEvent) => {
    e.preventDefault();
    openMenu(e.clientX, e.clientY, filterMenu(f, (id) => app.registry.reason(id), run));
  };
  return (
    <div role="group" aria-label="Фильтры">
      {list.map((f) => (
        <TreeRow
          key={f.id}
          label={f.name}
          ariaLabel={`${f.name}: ${f.title}`}
          level={2}
          selected={selected === f.id}
          onSelect={() => selectFilter(f.id)}
          onActivate={() => run("filters.manage", { filter_id: f.id })}
          title={filterTitleText(f)}
          testId={`tree-filter-${f.id}`}
          glyph={<Glyph icon={"filter" as IconName} />}
          onContextMenu={(e) => menu(f, e)}
        />
      ))}
    </div>
  );
}
