/**
 * «Версии» of a line layer (ТЗ 7.22; ADR 0015 §1–§2): the journal of saves as rows — the version, when, who, the
 * action, lines and vertices «было → стало», the note; a return to a version is a new version with the geometry
 * of the old one (nothing is erased), asked in an own dialog; with the layer under edit and changes not saved
 * the question says they go, and the session reads the layer anew. No React.
 */
import type { App } from "../../shell/app";
import { askConfirm } from "../../shell/dialogs";
import { refusal } from "../../shell/objectRun";
import { lineLayers, loadSceneObjects } from "../scene/objects";
import { getVersions, restoreVersion, type Journal, type VersionRow } from "./editApi";
import { editStore } from "./editState";
import { formatAt, reloadEditor } from "./editor";

export const ACTION_TITLES: Record<string, string> = {
  import: "Импорт",
  create: "Создание",
  save: "Сохранение",
  restore: "Возврат",
  move: "Перенос линий",
  operation: "Операция",
};

const N = new Intl.NumberFormat("ru-RU");

/** «12 → 14»; the same count — «12»; the first version (nothing before) — «12». */
export function change(prev: number, next: number, first = false): string {
  if (first || prev === next) return N.format(next);
  return `${N.format(prev)} → ${N.format(next)}`;
}

export interface VersionLine {
  version: number;
  when: string;
  who: string;
  action: string;
  lines: string;
  vertices: string;
  note: string;
  current: boolean;
}

/** The rows of the window, newest first. */
export function versionLines(j: Journal): VersionLine[] {
  const oldest = Math.min(...j.versions.map((v) => v.version));
  return j.versions.map((v: VersionRow) => {
    const first = v.version === oldest && v.prev_line_count === 0 && v.prev_point_count === 0 && v.action !== "save";
    return {
      version: v.version,
      when: formatAt(v.at),
      who: v.author ?? "—",
      action: ACTION_TITLES[v.action] ?? v.action,
      lines: change(v.prev_line_count, v.line_count, first),
      vertices: change(v.prev_point_count, v.point_count, first),
      note: v.note,
      current: v.current,
    };
  });
}

/** `lines.restore`: the layer back to version `k` as version N+1; asked in an own dialog when a person runs it. */
export async function restoreLayer(app: App, layerId: number, k: number, ask: boolean): Promise<string> {
  const j = await getVersions(layerId);
  if (!j.versions.some((v) => v.version === k)) throw refusal("version_not_found", `Версии ${k} в журнале слоя нет.`, "Откройте «Версии» и выберите версию из списка.");
  if (k === j.version) return `Версия ${k} — текущая: возвращать нечего`;
  const name = lineLayers().find((l) => l.id === layerId)?.name ?? `№${layerId}`;
  const s = editStore.get().session;
  const editing = s?.layerId === layerId;
  if (ask) {
    const lost = editing && s?.dirty ? "\n\nНесохранённые правки этого слоя будут потеряны." : "";
    const ok = await askConfirm({ title: "Возврат к версии", message: `Вернуть слой «${name}» к версии ${k}? Будет создана версия ${j.version + 1} с геометрией версии ${k}; журнал не стирается.${lost}`, okLabel: "Вернуть", danger: !!lost });
    if (!ok) return "Возврат отменён";
  }
  const r = await restoreVersion(layerId, k, j.version);
  if (editing) await reloadEditor(app);
  void loadSceneObjects();
  return `Слой «${name}» возвращён к версии ${k}: новая версия ${r.version}`;
}
