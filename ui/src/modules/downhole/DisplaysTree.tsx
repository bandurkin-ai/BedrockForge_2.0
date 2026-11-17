/**
 * Saved displays of a set in the data tree (ТЗ 7.16, 7.35 Д-03): a row under the set — the check box shows it in
 * the view (view key `displays`), a click selects it, a double click opens its form, the menu opens, renames
 * and deletes it.
 * A draft of a form is not here: only «Сохранить» makes a row.
 */
import { useMemo, useState } from "react";
import { TreeRow } from "../../shell/TreeRow";
import { CoreError } from "../../shell/api";
import { openMenu } from "../../shell/ContextMenu";
import { askConfirm, askText, showMessage } from "../../shell/dialogs";
import { useStore } from "../../shell/useStore";
import { updateView, viewStore } from "../scene/bind";
import { dropDisplay, isShown, showDisplay } from "./displays";
import { deleteChart, libraryStore, saveChart, type ChartRow } from "./library";
import { openDisplay } from "./open";
import "./downhole.css";

const errText = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

function show(chart: ChartRow, visible: boolean): void {
  try {
    updateView("displays", showDisplay(chart.id, visible));
  } catch (e) {
    void showMessage({ title: "Отображение", message: errText(e) });
  }
}

async function rename(chart: ChartRow): Promise<void> {
  const name = await askText({ title: "Переименовать отображение", label: "Название", initial: chart.name, okLabel: "Переименовать" });
  if (!name || name === chart.name) return;
  try {
    await saveChart(chart.set_id, name, chart.settings, chart);
  } catch (e) {
    await showMessage({ title: "Отображение не переименовано", message: errText(e) });
  }
}

async function remove(chart: ChartRow): Promise<void> {
  const ok = await askConfirm({ title: "Удалить отображение", message: `Отображение «${chart.name}» удаляется из проекта. Стили библиотеки остаются.`, okLabel: "Удалить", danger: true });
  if (!ok) return;
  try {
    await deleteChart(chart);
    try {
      updateView("displays", dropDisplay(chart.id));
    } catch {
      /* no project view */
    }
  } catch (e) {
    await showMessage({ title: "Отображение не удалено", message: errText(e) });
  }
}

export function DisplayRows({ setId }: { setId: number }) {
  const charts = useStore(libraryStore, (s) => s.charts);
  const shown = useStore(viewStore, (s) => s.displays);
  const list = useMemo(() => charts.filter((c) => c.set_id === setId && c.kind === "striplog").sort((a, b) => a.name.localeCompare(b.name, "ru")), [charts, setId]);
  const [picked, setPicked] = useState<number | null>(null);
  if (!list.length) return null;
  return (
    <div role="group" aria-label="Отображения по стволу">
      {list.map((c) => {
        const on = isShown(shown, c.id);
        const open = () => {
          try {
            openDisplay(c.id);
          } catch (e) {
            void showMessage({ title: "Отображение", message: errText(e) });
          }
        };
        return (
          <TreeRow
            key={c.id}
            label={c.name}
            level={3}
            check={{ checked: on, label: `Показать «${c.name}» на скважинах`, onChange: (v) => show(c, v) }}
            selected={picked === c.id}
            onSelect={() => setPicked(c.id)}
            onActivate={open}
            title={`${c.name}: галочка — показать или скрыть, двойной щелчок — настройки`}
            glyph={<span className="dh-tree-glyph" aria-hidden>▥ </span>}
            onContextMenu={(e) => {
              e.preventDefault();
              e.stopPropagation();
              openMenu(e.clientX, e.clientY, [
                { label: "Настройки…", run: open },
                { label: on ? "Скрыть на скважинах" : "Показать на скважинах", run: () => show(c, !on) },
                { label: "Переименовать…", run: () => void rename(c) },
                { label: "Удалить…", run: () => void remove(c) },
              ]);
            }}
          />
        );
      })}
    </div>
  );
}
