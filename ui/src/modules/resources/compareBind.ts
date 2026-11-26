/**
 * The window «Сравнить отчёты» of the shell (ТЗ 7.35 Д-37; ADR 0027 решение 29): `resources.compare` without parameters opens it; with both reports
 * from the interface it opens the window and shows the comparison there; from the assistant it answers with the text (and, when the window is
 * open, fills it too). `resources.compare` itself is bound with the other commands (`otherCommands.ts`). The window for the window layer of the shell —
 * `renderCompareWindow`. No React in the binding.
 */
import { createElement, type ReactNode } from "react";
import { viewport } from "../../shell/objectRun";
import { focusWindow, openWindow, windowStore, type FloatWin } from "../../shell/windows";
import { CompareWindow } from "./CompareWindow";
import { COMPARE_TITLE, COMPARE_WINDOW, syncCompare } from "./compareState";
import { loadReports } from "./state";

/** The window «Сравнить отчёты», if open. */
export const windowOfCompare = (): string | null => windowStore.get().windows.find((w) => w.kind === COMPARE_WINDOW)?.id ?? null;

export function openCompareWindow(): string {
  syncCompare();
  void loadReports();
  const open = windowOfCompare();
  if (open) {
    focusWindow(open);
    return open;
  }
  return openWindow(COMPARE_WINDOW, COMPARE_TITLE, "normal", {}, ...viewport());
}

/** The window for the shell's window layer (`shell/Shell.tsx`); null — not this window. */
export function renderCompareWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== COMPARE_WINDOW) return null;
  return createElement(CompareWindow, { winId: w.id });
}
