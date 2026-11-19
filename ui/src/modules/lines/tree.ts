/**
 * The «Линии» section of the data tree as data (ТЗ 7.18, ADR 0011 §5): files → layers, the check boxes of
 * visibility (`layer.show {layer: "lines", id}`, key `ln:<layer>`), what a row tells and offers. Pure.
 */
import { count, counted, SEPARATOR, type TreeAction } from "../../shell/treeActions";
import type { LineFileInfo, LineLayerInfo } from "../scene/objects";

/** A file with more layers has no check box of its own: its layers are switched one by one (a DXF may hold
 * thousands of layers, and the list of hidden keys of a project keeps the last 2000). */
export const FILE_TOGGLE_LIMIT = 200;
/** A file with more layers starts collapsed. */
export const AUTO_OPEN_LAYERS = 30;
/** Layer rows drawn under one file; the rest is told in one line. */
export const MAX_LAYER_ROWS = 500;

export const layerKey = (id: number): string => `ln:${id}`;

/** «12 линий, 340 вершин» · «25 точек» */
export function layerCounts(l: Pick<LineLayerInfo, "points_only" | "line_count" | "point_count">): string {
  if (l.points_only) return counted(l.point_count, "точка", "точки", "точек");
  return `${counted(l.line_count, "линия", "линии", "линий")}, ${counted(l.point_count, "вершина", "вершины", "вершин")}`;
}

const LENGTH = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
const metres = (v: number): string => `${LENGTH.format(v)} м`;

/** The tooltip of a layer row. */
export function layerTitle(l: LineLayerInfo): string {
  const parts = [`${l.name}: ${layerCounts(l)}`];
  if (!l.points_only && Number.isFinite(l.length) && l.length > 0) parts.push(`длина ${metres(l.length)}`);
  if (l.closed === "all") parts.push("замкнутые");
  else if (l.closed === "some") parts.push(`замкнутых ${count(l.closed_count)}`);
  if (l.flat) parts.push("без высот");
  return `${parts.join(", ")}. Щелчок — выбор, галочка — показать в 3D`;
}

/** «3 слоя» */
export const fileCounts = (f: Pick<LineFileInfo, "layers">): string => counted(f.layers.length, "слой", "слоя", "слоёв");

export function fileTitle(f: LineFileInfo): string {
  const parts = [`${f.name}: ${fileCounts(f)}`];
  if (f.label) parts.push(f.label);
  if (f.crs_name) parts.push(`СК ${f.crs_name}`);
  if (f.warnings.length) parts.push(`замечаний при импорте: ${f.warnings.length}`);
  return parts.join(", ");
}

/** Is the file row expanded: small files are open until their arrow is used, big ones closed. */
export function fileOpen(f: Pick<LineFileInfo, "id" | "layers">, toggled: readonly string[]): boolean {
  const byDefault = f.layers.length <= AUTO_OPEN_LAYERS;
  return toggled.includes(String(f.id)) ? !byDefault : byDefault;
}

/** The check box of a file: checked while every layer is shown; null — the file has none (no layers, or too many). */
export function fileCheck(f: Pick<LineFileInfo, "layers">, hidden: readonly string[]): { checked: boolean } | null {
  if (!f.layers.length || f.layers.length > FILE_TOGGLE_LIMIT) return null;
  const off = new Set(hidden);
  return { checked: f.layers.every((l) => !off.has(layerKey(l.id))) };
}

/** Layers whose visibility has to change for the whole file to be shown (or hidden). */
export function fileToggle(f: Pick<LineFileInfo, "layers">, hidden: readonly string[], visible: boolean): number[] {
  const off = new Set(hidden);
  return f.layers.filter((l) => off.has(layerKey(l.id)) === visible).map((l) => l.id);
}

/** The layers drawn under a file and how many are left out. */
export function shownLayers(f: Pick<LineFileInfo, "layers">): { layers: LineLayerInfo[]; rest: number } {
  return { layers: f.layers.slice(0, MAX_LAYER_ROWS), rest: Math.max(0, f.layers.length - MAX_LAYER_ROWS) };
}

/**
 * A layer; a point cloud adds «Фильтр земли…», «Построить ЦТМ из облака…» and «Карта разностей…» (ADR 0014 §5,
 * §6), an ordinary layer — «Редактировать» (the string editor; «Завершить правку» for the layer under edit) and
 * «Версии…» (ADR 0015 §5).
 */
export function layerMenu(l: Pick<LineLayerInfo, "id"> & Partial<Pick<LineLayerInfo, "cloud">>, visible: boolean, edited = false): TreeAction[] {
  const cloud: TreeAction[] = l.cloud
    ? [
        { label: "Фильтр земли…", command: "ground.classify", args: { cloud_id: l.id } },
        { label: "Построить ЦТМ из облака…", command: "terrain.from_points", args: { cloud_id: l.id } },
        { label: "Карта разностей…", command: "cloud.dz", args: { cloud_id: l.id } },
      ]
    : [];
  const editor: TreeAction[] = l.cloud
    ? []
    : [
        edited ? { label: "Завершить правку", command: "lines.finish" } : { label: "Редактировать", command: "lines.edit", args: { layer_id: l.id } },
        { label: "Версии…", command: "lines.versions", args: { layer_id: l.id } },
      ];
  return [
    { label: "Свойства", local: "properties" },
    { label: visible ? "Скрыть в 3D" : "Показать в 3D", command: "layer.show", args: { layer: "lines", id: l.id, visible: !visible } },
    ...cloud,
    { label: "Экспорт…", command: "lines.export", args: { layer_id: l.id } },
    ...editor,
    SEPARATOR,
    { label: "Переименовать…", command: "object.rename", args: { kind: "lines", id: l.id } },
  ];
}

/** A file: the export of all its layers and the delete (`object.delete` asks in an own window). */
export function fileMenu(f: Pick<LineFileInfo, "id">): TreeAction[] {
  return [
    { label: "Экспорт…", command: "lines.export", args: { file_id: f.id } },
    SEPARATOR,
    { label: "Удалить…", command: "object.delete", args: { kind: "line_file", id: f.id } },
  ];
}
