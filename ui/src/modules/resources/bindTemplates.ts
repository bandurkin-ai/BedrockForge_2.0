/**
 * The command `resources.templates` and the window «Шаблоны форм отчёта» of the shell (ТЗ 8.10 «Формы»; ADR 0027 решения 16–19, 29): the command opens
 * the window (the ribbon, Ctrl+K, the hotkeys and the assistant read the declaration in `commands.ts`; `uiOnly` — the editing itself is the user's).
 * Until `bindTemplates` is called the command is unavailable. The window for the window layer of the shell — `renderTemplatesWindow`. No React in the binding.
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import { needProject, noted, viewport } from "../../shell/objectRun";
import { focusWindow, openWindow, windowStore, type FloatWin } from "../../shell/windows";
import { loadTemplates } from "./state";
import { TemplatesWindow } from "./TemplatesWindow";
import { TEMPLATES_TITLE, TEMPLATES_WINDOW } from "./templatesState";

/** The window «Шаблоны форм отчёта», if open. */
export const windowOfTemplates = (): string | null => windowStore.get().windows.find((w) => w.kind === TEMPLATES_WINDOW)?.id ?? null;

export function openTemplatesWindow(): string {
  void loadTemplates();
  const open = windowOfTemplates();
  if (open) {
    focusWindow(open);
    return open;
  }
  return openWindow(TEMPLATES_WINDOW, TEMPLATES_TITLE, "normal", {}, ...viewport());
}

export function bindTemplates(app: App): void {
  app.registry.bind(
    "resources.templates",
    noted(app, async () => {
      openTemplatesWindow();
      return `Открыто окно «${TEMPLATES_TITLE}»`;
    }),
    needProject,
  );
}

/** The window for the shell's window layer (`shell/Shell.tsx`); null — not this window. */
export function renderTemplatesWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== TEMPLATES_WINDOW) return null;
  return createElement(TemplatesWindow, { winId: w.id });
}
