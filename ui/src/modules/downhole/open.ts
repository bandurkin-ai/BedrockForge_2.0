/** Opening the display forms and the style import (ТЗ 7.16): floating windows of the application. */
import { FORM_TITLES, withDefaults, type DisplayForm } from "../../charts/striplog/model";
import { focusWindow, openWindow, windowStore } from "../../shell/windows";
import { tablesStore } from "../tables/state";
import { formOf } from "./formModel";
import type { StyleFileToImport } from "./ImportDialog";
import { libraryStore } from "./library";

const viewport = (): [number, number] => (typeof window === "undefined" ? [1280, 800] : [window.innerWidth, window.innerHeight]);
const setName = (setId: number) => tablesStore.get().sets.find((s) => s.id === setId)?.name ?? `набор ${setId}`;

/** The form of a new display of a set; with `field` (and `table`) it starts with that field. */
export function openForm(setId: number, form: DisplayForm, hint: { table?: string | null; field?: string | null } = {}): string {
  const props = { setId, form, chartId: null, table: hint.table ?? null, field: hint.field ?? null };
  return openWindow("downhole", `${FORM_TITLES[form]} (новое) · ${setName(setId)}`, "form", props, ...viewport());
}

/** The form of a saved display (`focus` — a layer to select, e.g. clicked in the legend); an open one comes to the front. */
export function openDisplay(chartId: number, focus?: string): string {
  const open = windowStore.get().windows.find((w) => w.kind === "downhole" && w.props?.chartId === chartId);
  if (open) {
    focusWindow(open.id);
    return open.id;
  }
  const c = libraryStore.get().charts.find((x) => x.id === chartId);
  if (!c) throw new Error("Отображение не найдено: обновите дерево проекта");
  const form = formOf(withDefaults(c.settings));
  return openWindow("downhole", `${c.name} · ${setName(c.set_id)}`, "form", { setId: c.set_id, form, chartId, table: null, field: null, ...(focus ? { focus } : {}) }, ...viewport());
}

/** Files read for import, by the token in the window props (the file itself does not go into the props). */
const pending = new Map<string, StyleFileToImport>();
let token = 0;

export function openImport(source: StyleFileToImport): string {
  const key = `i${++token}`;
  pending.set(key, source);
  return openWindow("styles-import", "Импорт стилей", "medium", { token: key }, ...viewport());
}

export const importSource = (key: string): StyleFileToImport | undefined => pending.get(key);
export const forgetImport = (key: string): void => void pending.delete(key);
