/**
 * Results of sample processing under a set of the data tree (ТЗ 8.0.2, 8.2.3; ADR 0018 §2): the row «Обработка
 * проб» and a row per result — its name, «устарел» with the reason; a double click — the table of the result (a
 * result without one — «Свойства»); the menu: «Открыть таблицу», «Параметры…» (the tool window with the values the
 * core ran with), «Перестроить» (always our own question, decision 11; losses — a second one, bind.ts), «Свойства»,
 * «Удалить…» (our own question, `danger` — bind.ts). Rows are `TreeRow`s:
 * the WAI-ARIA keyboard of the tree (shell/tree.ts) works on them as on every row. `TabMark` — the mark of a result
 * table on its tab of the panel «Таблица».
 */
import { useEffect, useState, type MouseEvent } from "react";
import type { App } from "../../shell/app";
import { useApp } from "../../shell/AppContext";
import { openMenu } from "../../shell/ContextMenu";
import { askConfirm, showMessage } from "../../shell/dialogs";
import { viewport } from "../../shell/objectRun";
import { errorText } from "../../shell/props";
import { toggleExpanded } from "../../shell/tree";
import { TreeRow } from "../../shell/TreeRow";
import { useStore } from "../../shell/useStore";
import { openWindow } from "../../shell/windows";
import type { ResultRow, TableRow } from "./api";
import { openTool, WINDOW_PREFIX } from "./bind";
import own from "./Composites.module.css";
import { loadSetResults, paramsArgs, rebuildQuestion, resultLabel, resultsStore, staleText, tabMark, toolOfOp, watchResults, type ResultAction } from "./results";
import { toolById } from "./tools";
import { openIntercept } from "../intercepts/bind";
import { interceptArgs, interceptMenu } from "../intercepts/results";
import { interceptToolOfOp } from "../intercepts/tools";

export const RESULT_WINDOW = `${WINDOW_PREFIX}result`;
export const GROUP_TITLE = "Обработка проб";

/** «Свойства» of a result — its own window (one per result). */
export const openResultProps = (r: Pick<ResultRow, "id" | "name" | "set_id">): string => openWindow(RESULT_WINDOW, `Свойства: ${r.name || `результат №${r.id}`}`, "form", { resultId: r.id, setId: r.set_id }, ...viewport());

/** Does what an item of the menu of a result says; a failure — our own message. */
export async function runResultAction(app: App, r: ResultRow, action: ResultAction): Promise<void> {
  try {
    if (action === "open") {
      if (r.table_id === null) return void openResultProps(r);
      await app.run("table.open", { set_id: r.set_id, kind: String(r.table_id) });
    } else if (action === "params") {
      const icpt = interceptToolOfOp(r.op); // Э15: `tt`, `intercepts` — the windows of «Пересечения»
      const tool = icpt ? null : toolById(toolOfOp(r.op) ?? "");
      if (icpt) openIntercept(icpt, interceptArgs(r));
      else if (tool) openTool(tool, paramsArgs(r));
    } else if (action === "rebuild") {
      if (!(await askConfirm({ title: "Перестроить результат", message: rebuildQuestion(r), okLabel: "Перестроить", danger: true }))) return;
      await app.run("composite.rebuild", { result_id: r.id });
    } else if (action === "props") openResultProps(r);
    else if (action === "delete") {
      await app.run("composite.delete", { result_id: r.id });
      await loadSetResults(r.set_id);
    }
  } catch (e) {
    await showMessage({ title: "Обработка проб", message: errorText(e) });
  }
}

export function openResultMenu(app: App, r: ResultRow, e: MouseEvent): void {
  e.preventDefault();
  e.stopPropagation();
  const ro = app.store.get().projectReadOnly === true;
  const items = interceptMenu(r, ro);
  openMenu(
    e.clientX,
    e.clientY,
    items.flatMap((it) => [
      ...(it.action === "delete" ? [{ separator: true, label: "" }] : []),
      { label: it.label, reason: it.reason, run: () => void runResultAction(app, r, it.action) },
    ]),
  );
}

/** The results of these sets as they are read; a set not read yet is read (and kept current). */
export function useResultsBySet(setIds: readonly number[]): Record<number, ResultRow[]> {
  const bySet = useStore(resultsStore, (s) => s.bySet);
  // The sets not read yet: after `resetResults` (another project) they change and are read again.
  const key = setIds.filter((id) => bySet[id] === undefined).join(",");
  useEffect(() => {
    watchResults();
    for (const id of setIds) if (resultsStore.get().bySet[id] === undefined) void loadSetResults(id);
  }, [key]);
  return bySet;
}

/** The text after the name of a result: «устарел» (warn colour) or nothing. */
function StaleMark({ r }: { r: ResultRow }) {
  return r.stale ? (
    <span className={own.staleMark} data-testid={`result-stale-${r.id}`}>
      устарел
    </span>
  ) : null;
}

/** The title of a result row: what it is, why it is out of date, what a double click does. */
export function resultTitle(r: ResultRow): string {
  const what = r.makes_table ? (r.table ? `таблица «${r.table}»` : "таблица") : r.table ? `поля в таблице «${r.table}»` : "поля в таблице источника";
  const stale = staleText(r);
  return `${resultLabel(r)} — ${r.op_title}, ${what}${stale ? `; ${stale}` : ""}. Двойной щелчок — ${r.table_id === null ? "свойства" : "таблица"}, меню — действия`;
}

/** «Обработка проб» under a set with its results; nothing when the set has none. */
export function ResultRows({ setId, results, level = 3 }: { setId: number; results: readonly ResultRow[]; level?: number }) {
  const app = useApp();
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [picked, setPicked] = useState<number | null>(null);
  if (!results.length) return null;
  const open = !collapsed.includes("group");
  const stale = results.filter((r) => r.stale).length;
  return (
    <div role="group" aria-label={`${GROUP_TITLE}: результаты`}>
      <TreeRow
        label={GROUP_TITLE}
        ariaLabel={`${GROUP_TITLE}, результатов ${results.length}${stale ? `, устарели ${stale}` : ""}`}
        level={level}
        expanded={open}
        onToggle={() => setCollapsed((c) => toggleExpanded(c, "group"))}
        title={`Результаты обработки проб набора: ${results.length}${stale ? `, устарели ${stale}` : ""}`}
        testId={`tree-results-${setId}`}
        after={<span className={own.treeCount}>{results.length}</span>}
      />
      {open &&
        results.map((r) => (
          <TreeRow
            key={r.id}
            label={resultLabel(r)}
            ariaLabel={`${resultLabel(r)}${r.stale ? `, ${staleText(r)}` : ""}`}
            level={level + 1}
            selected={picked === r.id}
            onSelect={() => setPicked(r.id)}
            onActivate={() => void runResultAction(app, r, "open")}
            title={resultTitle(r)}
            testId={`tree-result-${r.id}`}
            className={r.stale ? own.staleRow : undefined}
            after={<StaleMark r={r} />}
            onContextMenu={(e) => openResultMenu(app, r, e)}
          />
        ))}
    </div>
  );
}

/** The mark of a result table on its tab (panel «Таблица»): «устарел» with the reason in the tooltip. */
export function TabMark({ origin }: { origin: TableRow["origin"] | undefined }) {
  const m = tabMark(origin);
  if (!m) return null;
  return (
    <span className={m.stale ? own.staleMark : own.resultMark} title={m.title} data-testid="table-tab-mark">
      {m.stale ? m.text : "◆"}
    </span>
  );
}
