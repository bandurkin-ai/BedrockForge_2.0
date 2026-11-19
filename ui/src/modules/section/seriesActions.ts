/**
 * What the windows and the commands of series do (ADR 0016 §4): open «Новая серия», the window of the parameters
 * and «Менеджер серий»; make a series active; copy; delete after an own question (never window.confirm). Each
 * returns the Russian result for the command log and Claude; errors of the core keep their text and hint.
 */
import { askConfirm } from "../../shell/dialogs";
import { openWindow } from "../../shell/windows";
import { viewStore } from "../scene/bind";
import { faultText, seriesApi, seriesFault, type SeriesRow } from "./seriesApi";
import { dropSeries, ensureTable, geometryOf, putSeries, seriesById, setActiveSeries, stepsOf } from "./seriesState";
import type { SeriesForm } from "./seriesForm";
import { sectionOf } from "./state";

export const SERIES_WINDOW = "series-edit";
export const SERIES_MANAGER = "series-manager";
export const SERIES_GOTO = "series-goto";
export const SERIES_WINDOWS = [SERIES_WINDOW, SERIES_MANAGER, SERIES_GOTO];

function viewport(): [number, number] {
  return typeof window === "undefined" ? [1280, 800] : [window.innerWidth, window.innerHeight];
}

/** «Новая серия» filled with `form` (from the line, through a hole, from one section, or the defaults). */
export function openNewSeries(form: SeriesForm, title = "Новая серия"): string {
  return openWindow(SERIES_WINDOW, title, "form", { mode: "create", form }, ...viewport());
}

/** The parameters of a stored series. */
export function openSeriesParams(row: SeriesRow): string {
  return openWindow(SERIES_WINDOW, `Серия «${row.name}»`, "form", { mode: "edit", seriesId: row.id }, ...viewport());
}

export function openSeriesManager(selected?: number): string {
  return openWindow(SERIES_MANAGER, "Менеджер серий", "normal", selected ? { selected } : undefined, ...viewport());
}

const need = (id: number): SeriesRow => {
  const row = seriesById(id);
  if (!row) throw new Error("Серия разрезов не найдена: её удалили. Обновите список серий.");
  return row;
};

export function openGoto(): string {
  return openWindow(SERIES_GOTO, "Перейти к разрезу", "compact", undefined, ...viewport());
}

/** Makes a series active at section k (from 0; a polyline — segment k), clipping on; the table is read for the names. */
export function activateSeries(id: number, k = 0): string {
  const row = need(id);
  const geom = geometryOf(row);
  if (!geom) throw new Error(`Серию «${row.name}» нельзя показать: параметры не читаются этой версией. Откройте её параметры и сохраните заново.`);
  const steps = stepsOf(geom);
  const i = Math.min(Math.max(Math.trunc(k), 0), steps - 1);
  const poly = geom.kind === "polyline";
  setActiveSeries({ id, k: poly ? 0 : i, seg: poly ? i : null }, true);
  void ensureTable(id).catch(() => undefined);
  return `Серия «${row.name}» активна: ${poly ? "сегмент" : "разрез"} ${i + 1} из ${steps}`;
}

export async function copySeries(id: number): Promise<SeriesRow> {
  need(id);
  try {
    const row = await seriesApi.copy(id);
    putSeries(row);
    return row;
  } catch (e) {
    throw new Error(faultText(seriesFault(e)));
  }
}

/** Deletes after the own question; false — the user kept it. The contours of the series stay. */
export async function deleteSeries(id: number, ask = true): Promise<boolean> {
  const row = need(id);
  if (ask) {
    const ok = await askConfirm({ title: "Удалить серию", message: `Удалить серию «${row.name}» (${row.count} разр.)? Контуры, оцифрованные на её разрезах, останутся в слое.`, okLabel: "Удалить", danger: true });
    if (!ok) return false;
  }
  try {
    await seriesApi.remove(id);
  } catch (e) {
    throw new Error(faultText(seriesFault(e)));
  }
  if (sectionOf(viewStore.get()).series?.id === id) setActiveSeries(null);
  dropSeries(id);
  return true;
}

/** A series created or changed in a window: in the list now, active at its first section when new. */
export function seriesSaved(row: SeriesRow, created: boolean): string {
  putSeries(row);
  if (created) return activateSeries(row.id, 0);
  return `Серия «${row.name}» сохранена: ${row.count} разр.`;
}
