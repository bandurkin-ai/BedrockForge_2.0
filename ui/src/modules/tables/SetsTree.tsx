import { useApp } from "../../shell/AppContext";
import { api, CoreError } from "../../shell/api";
import { openMenu } from "../../shell/ContextMenu";
import { askConfirm, askText, showMessage } from "../../shell/dialogs";
import { useState } from "react";
import { toggleExpanded } from "../../shell/tree";
import { TreeRow } from "../../shell/TreeRow";
import { useStore } from "../../shell/useStore";
import { ResultRows, useResultsBySet } from "../composites/ResultsTree";
import { DisplayRows } from "../downhole/DisplaysTree";
import { libraryStore } from "../downhole/library";
import { ChartRows, useSetChartsAll } from "../charts/ChartsTree";
import { componentsReason, readComponents } from "../charts/state";
import { loadSets, selectSet, tablesStore, type SetInfo } from "./state";
import css from "./Tables.module.css";

/** The selection of a click in flight: the double click that follows waits for its tables. */
let selecting: Promise<void> = Promise.resolve();

const errText = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

/** Sets under «Скважины» in the data tree (ТЗ 7.1, 7.10, 7.35 Д-03): a click — the active set, a double click — its
 * table, the arrow — its displays, «Обработка проб» (results, ADR 0018 §2) and «Графики» (saved charts, ТЗ 7.28);
 * menu (ТЗ 7.0.13) — the charts (unavailable with the reason), tables, displays, rename, delete. */
export function SetsTree() {
  const app = useApp();
  const s = useStore(tablesStore, (x) => x);
  const charts = useStore(libraryStore, (x) => x.charts);
  const hidden = useStore(app.store, (x) => x.view.hidden);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const results = useResultsBySet(s.sets.map((x) => x.id));
  const setCharts = useSetChartsAll();
  if (!s.sets.length) return <div className={css.treeNone}>Нет объектов</div>;
  const open = async (set: SetInfo, kind?: string) => {
    await selecting;
    if (set.id !== tablesStore.get().setId) await selectSet(set.id);
    await app.run("table.open", kind ? { kind } : { kind: tablesStore.get().table ?? "collar" });
  };
  const rename = async (set: SetInfo) => {
    const name = await askText({ title: "Переименовать набор", label: "Название", initial: set.name, okLabel: "Переименовать" });
    if (!name || name === set.name) return;
    try {
      await api.patch(`/drillholes/sets/${set.id}`, { name });
      await loadSets();
    } catch (e) {
      await showMessage({ title: "Набор не переименован", message: errText(e) });
    }
  };
  const remove = async (set: SetInfo) => {
    const ok = await askConfirm({ title: "Удалить набор", message: `Набор «${set.name}» удаляется со скважинами, таблицами и историей импорта. Отменить удаление нельзя.`, okLabel: "Удалить", danger: true });
    if (!ok) return;
    try {
      await api.del(`/drillholes/sets/${set.id}`);
      await loadSets();
    } catch (e) {
      await showMessage({ title: "Набор не удалён", message: errText(e) });
    }
  };
  const select = (set: SetInfo) => {
    if (set.id !== tablesStore.get().setId) selecting = selectSet(set.id).catch(() => undefined);
  };
  return (
    <div role="group" aria-label="Наборы скважин">
      {s.sets.map((set) => {
        const hasDisplays = charts.some((c) => c.set_id === set.id && c.kind === "striplog");
        const setResults = results[set.id] ?? [];
        const hasChildren = hasDisplays || setResults.length > 0 || setCharts.has(set.id);
        const open_ = !collapsed.includes(String(set.id));
        return (
          <div key={set.id}>
            <TreeRow
              label={set.name}
              ariaLabel={`${set.name}, ${set.holes.toLocaleString("ru-RU")} скв.`}
              level={2}
              expanded={hasChildren ? open_ : undefined}
              onToggle={() => setCollapsed((c) => toggleExpanded(c, String(set.id)))}
              check={{
                checked: !hidden.includes(`set:${set.id}`),
                label: `Показать «${set.name}» в 3D`,
                onChange: (v) => void app.run("layer.show", { layer: "set", id: set.id, visible: v }),
              }}
              selected={set.id === s.setId}
              onSelect={() => select(set)}
              onActivate={() => void open(set)}
              title={`${set.name}: ${set.holes.toLocaleString("ru-RU")} скв. Щелчок — активный набор, двойной щелчок — таблица`}
              testId={`tree-set-${set.id}`}
              after={<span className={css.count}>{set.holes.toLocaleString("ru-RU")}</span>}
              onContextMenu={(e) => {
                e.preventDefault();
                void readComponents(set.id); // «Дендрограмма» of a set not read yet: known next time
                const chart = (command: string) => ({ reason: app.registry.reason(command) ?? (command === "chart.dendrogram" ? componentsReason(set.id) : null), run: () => void app.run(command, { set_id: set.id }) });
                openMenu(e.clientX, e.clientY, [
                  { label: "Гистограмма", ...chart("chart.histogram") },
                  { label: "Q-Q график", ...chart("chart.qq") },
                  { label: "Дендрограмма", ...chart("chart.dendrogram") },
                  { separator: true, label: "" },
                  { label: "Устья", run: () => void open(set, "collar") },
                  { label: "Инклинометрия", run: () => void open(set, "survey") },
                  { label: "Пробы", run: () => void open(set, "assay") },
                  { separator: true, label: "" },
                  { label: "Содержания…", run: () => void app.run("downhole.open", { form: "grade", set_id: set.id }) },
                  { label: "Литология…", run: () => void app.run("downhole.open", { form: "lith", set_id: set.id }) },
                  { label: "События…", run: () => void app.run("downhole.open", { form: "event", set_id: set.id }) },
                  { separator: true, label: "" },
                  { label: "Переименовать…", run: () => void rename(set) },
                  { label: "Удалить…", run: () => void remove(set) },
                ]);
              }}
            />
            {hasDisplays && open_ && <DisplayRows setId={set.id} />}
            {open_ && <ResultRows setId={set.id} results={setResults} />}
            {open_ && <ChartRows setId={set.id} />}
          </div>
        );
      })}
    </div>
  );
}
