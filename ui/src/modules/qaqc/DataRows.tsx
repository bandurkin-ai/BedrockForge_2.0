/**
 * The data of QAQC as rows of the section «Аудит и статистика» of the data tree (ADR 0020 §11): by name, «устарели»
 * marked; a double click or Enter opens the tab «QAQC» on them (`qaqc.open {section: standards, data_id}`); the
 * menu: «Открыть», «Настройка QAQC…», «Обновить», «Переименовать…», «Удалить…» — the same commands as the window.
 * `AuditTree` — the section with these rows above the swath plots of Э13.
 */
import { useSyncExternalStore, type MouseEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { openMenu } from "../../shell/ContextMenu";
import { Glyph } from "../../shell/ObjectRows";
import { TreeRow } from "../../shell/TreeRow";
import { SwathTree } from "../swath/SwathTree";
import type { QaqcData } from "./setupApi";
import { dataByName, dataStore } from "./setup/dataStore";
import { dataActions } from "./setup/DataTab";

/** The menu of a data row: the actions of the window, «Заменить» aside (it needs the form), plus «Настройка QAQC…». */
export function dataMenu(d: QaqcData, reason: (id: string) => string | null, run: (id: string, args: Record<string, unknown>) => void) {
  const items = dataActions(d, reason, run).filter((a) => !a.label.startsWith("Заменить"));
  return [items[0], { label: "Настройка QAQC…", reason: reason("qaqc.setup"), run: () => run("qaqc.setup", { tab: "data" }) }, ...items.slice(1)];
}

export function DataRows() {
  const app = useApp();
  const getState = () => dataStore.get();
  const { list } = useSyncExternalStore(dataStore.subscribe, getState, getState);
  const run = (id: string, args: Record<string, unknown>) => void app.run(id, args);
  if (!list.length) return null;
  const menu = (d: QaqcData, e: MouseEvent) => {
    e.preventDefault();
    openMenu(e.clientX, e.clientY, dataMenu(d, (id) => app.registry.reason(id), run));
  };
  return (
    <div role="group" aria-label="Данные QAQC">
      {dataByName(list).map((d) => (
        <TreeRow
          key={d.id}
          label={d.stale ? `${d.name} · устарели` : d.name}
          ariaLabel={d.name}
          level={2}
          onActivate={() => run("qaqc.open", { section: "standards", data_id: d.id })}
          title={`Данные QAQC «${d.name}»: ${d.rows.toLocaleString("ru-RU")} записей; двойной щелчок — вкладка «QAQC»${d.stale ? "; таблица набора изменилась — «Обновить» в меню" : ""}`}
          testId={`tree-qaqc-data-${d.id}`}
          glyph={<Glyph icon="audit" />}
          onContextMenu={(e) => menu(d, e)}
        />
      ))}
    </div>
  );
}

/** The section «Аудит и статистика»: the data of QAQC, then the swath plots. */
export function AuditTree() {
  return (
    <>
      <DataRows />
      <SwathTree />
    </>
  );
}
