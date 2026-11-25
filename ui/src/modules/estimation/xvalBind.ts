/**
 * The command `estimate.xval {estimation_id?, by?}` and the window «Перекрёстная проверка» (ТЗ 8.8.5; docs/progress/e18-api.md §12):
 * without `by` it opens the window with the row (the row asked, else the one the window has, else the only one of the project) and
 * reads the result there is; with `by` it also runs the check (`POST /estimate/xval`), follows the job to its end and answers with the
 * summary. The run writes the table of the row, so a project open for reading refuses it. The state of the window follows the project
 * (another project — a clean window) and `geostat.changed` of the row (the result may be out of date or made again).
 * `bindEstimation` (`bind.ts`) binds it with the rest.
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { needProject, noted, refusal, viewport } from "../../shell/objectRun";
import { focusWindow, openWindow, windowStore, type FloatWin } from "../../shell/windows";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { estimationStore, loadRows, rowById } from "./state";
import { XVAL_BY, type XvalBy } from "./xvalApi";
import { openXval, resetXval, runXval, syncXval, XVAL_TITLE, XVAL_WINDOW, xvalStore } from "./xvalState";
import { XvalWindow } from "./XvalWindow";

/** The window of the check, if open. */
export const xvalWindow = (): string | null => windowStore.get().windows.find((w) => w.kind === XVAL_WINDOW)?.id ?? null;

/** The row of the command: the one asked, else the window's, else the only one of the project (null — the person chooses in the window). */
function chosenRow(a: Record<string, unknown>): number | null {
  if (typeof a.estimation_id === "number") {
    if (!rowById(a.estimation_id) && estimationStore.get().loaded) throw refusal("estimation_not_found", `Строка «Параметры оценки» №${a.estimation_id} не найдена.`, "Номера строк — в разделе «Геостатистика → Параметры оценки» дерева.");
    return a.estimation_id;
  }
  const have = xvalStore.get().estimationId;
  if (have !== null && rowById(have)) return have;
  const rows = estimationStore.get().rows;
  return rows.length === 1 ? rows[0].id : null;
}

export async function runXvalCommand(app: App, a: Record<string, unknown>, _source: CommandSource): Promise<string> {
  const by = a.by === undefined ? null : (a.by as XvalBy);
  if (by !== null && !XVAL_BY.includes(by)) throw refusal("bad_param", "Параметр by: hole — скважина, sample — проба.", "Передайте by = hole или by = sample.");
  if (by !== null && app.store.get().projectReadOnly) throw refusal("project_read_only", "Проект открыт только для чтения: проверку выполнить нельзя.", "Откройте проект для записи или смотрите результат без by.");
  const id = chosenRow(a);
  if (by !== null && id === null) throw refusal("bad_param", "Не указана строка «Параметры оценки».", "Передайте estimation_id: номера строк — в разделе «Геостатистика → Параметры оценки» дерева.");
  if (by !== null && rowById(id as number)?.source_lost) throw refusal("source_lost", "Источник строки удалён: проверку выполнить нельзя.", "Восстановите набор проб или создайте строку заново.");
  const open = xvalWindow();
  const loading = openXval(id);
  if (open) focusWindow(open);
  else openWindow(XVAL_WINDOW, XVAL_TITLE, "normal", {}, ...viewport());
  if (by === null) {
    await loading;
    return `Открыто окно «${XVAL_TITLE}»`;
  }
  return runXval(by);
}

/** The binding with the registry and the events; the functions that take it off. */
export function bindXval(app: App): (() => void)[] {
  app.registry.bind("estimate.xval", noted(app, (a, s) => runXvalCommand(app, a, s)), needProject);
  let uid: string | null = projectStore.get().current?.uid ?? null;
  return [
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      resetXval();
    }),
    onCoreEvent((event, data) => {
      if (event !== "geostat.changed" || !uid) return;
      const id = xvalStore.get().estimationId;
      if (id === null) return;
      try {
        const d = JSON.parse(data) as { kind?: unknown; id?: unknown };
        if (d.kind !== undefined && d.kind !== "estimation") return;
        if (d.id !== undefined && d.id !== id) return;
      } catch {
        // an event without a body: whatever changed, look again
      }
      void loadRows().then(() => syncXval());
    }),
  ];
}

/** The window for the window layer (`renderEstimationWindow`); null — not this one. */
export function renderXvalWindow(w: FloatWin): ReactNode | null {
  return w.kind === XVAL_WINDOW ? createElement(XvalWindow, { winId: w.id }) : null;
}
