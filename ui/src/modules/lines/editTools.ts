/**
 * The buttons of the editor panel (ADR 0015 §5; ТЗ 7.22 «иконки в духе AutoCAD: название, действие, клавиши,
 * причина недоступности»): one per command of the registry — the tools (`lines.tool`), the actions on lines
 * (`lines.modify`), undo and redo, measure, move to another layer, versions, save, finish. A button runs its
 * command; it is unavailable with the reason of the registry or of the session. Pure.
 */
import type { EditSession } from "../../edit/session";
import * as cmd from "../../edit/tools";
import { TOOLS, type Tool } from "../../edit/tools";
import type { IconName } from "../../shell/Icon";
import { MODIFY, modifyBlocked, type ModifyAction } from "./modify";

export type PanelGroup = "tools" | "modify" | "history" | "layer";

export interface PanelButton {
  /** the test id is `edit-<id>` */
  id: string;
  command: string;
  args?: Record<string, unknown>;
  icon: IconName;
  title: string;
  /** what it does */
  action: string;
  /** keys, "" — none */
  keys: string;
  group: PanelGroup;
}

const TOOL_ICONS: Record<Tool, IconName> = { select: "edSelect", line: "edPline", polygon: "edPolygon", point: "edPoint", tie: "edTie", insert: "edInsert" };

/** The panel in its order. */
export const PANEL: readonly PanelButton[] = [
  ...TOOLS.map((t): PanelButton => ({ id: `tool-${t.id}`, command: "lines.tool", args: { tool: t.id }, icon: TOOL_ICONS[t.id], title: t.title, action: t.action, keys: t.keys, group: "tools" })),
  ...MODIFY.map((m): PanelButton => ({ id: `modify-${m.id}`, command: "lines.modify", args: { action: m.id }, icon: m.icon, title: m.title, action: m.action, keys: m.keys, group: "modify" })),
  { id: "undo", command: "lines.undo", icon: "edUndo", title: "Отменить", action: "Отменить последнюю правку", keys: "Ctrl+Z", group: "history" },
  { id: "redo", command: "lines.redo", icon: "edRedo", title: "Повторить", action: "Повторить отменённую правку", keys: "Ctrl+Y, Ctrl+Shift+Z", group: "history" },
  { id: "measure", command: "lines.measure", icon: "info", title: "Измерить", action: "Вершины, длина 3D и в плане, периметр и площадь выбранного и всего слоя — в журнал команд", keys: "", group: "history" },
  { id: "move", command: "lines.move_selected", icon: "export", title: "Перенести в другой слой…", action: "Перенести выбранные линии в другой слой: оба слоя получат новую версию", keys: "", group: "layer" },
  { id: "versions", command: "lines.versions", icon: "list", title: "Версии…", action: "Журнал сохранений слоя и возврат к версии", keys: "", group: "layer" },
  { id: "save", command: "lines.save", icon: "edSave", title: "Сохранить", action: "Сохранить слой: новая версия в журнале", keys: "Ctrl+S", group: "layer" },
  { id: "finish", command: "lines.finish", icon: "edExit", title: "Завершить правку", action: "Выйти из режима правки; несохранённые изменения будет предложено сохранить", keys: "", group: "layer" },
];

/** The commands of the editor that the panel must offer (the field of coordinates and «Привязка» aside). */
export const PANEL_COMMANDS = ["lines.tool", "lines.modify", "lines.undo", "lines.redo", "lines.measure", "lines.move_selected", "lines.versions", "lines.save", "lines.finish"];

export interface ButtonState {
  /** why the button does not work now, or null */
  reason: string | null;
  /** the tool in use */
  active: boolean;
}

/** The state of a button: the reason of the registry first, then of the session. */
export function buttonState(b: PanelButton, s: EditSession, registryReason: (command: string) => string | null): ButtonState {
  const active = b.command === "lines.tool" && s.tool === b.args?.tool;
  const reg = registryReason(b.command);
  if (reg) return { reason: reg, active };
  if (b.command === "lines.modify") return { reason: modifyBlocked(s, b.args?.action as ModifyAction), active };
  if (b.command === "lines.tool" || b.command === "lines.finish" || b.command === "lines.versions" || b.command === "lines.measure") return { reason: null, active };
  return { reason: cmd.blocked(s, b.command), active };
}

/** The tooltip: «Линия — Щелчок — точка… (L)», and why it is unavailable. */
export function tooltip(b: PanelButton, st: ButtonState): string {
  const head = `${b.title.replace("…", "")} — ${b.action}${b.keys ? ` (${b.keys})` : ""}`;
  return st.reason ? `${head}. Недоступно: ${st.reason}` : head;
}

/** What the tool does with the mouse (the line of hints of the panel). */
export const TOOL_HINTS: Record<Tool, string> = {
  select: "Щелчок — выбрать линию или вершину, Shift — добавить; тянуть вершину или выбранную линию — переместить; Ctrl + тянуть — рамка; двойной щелчок по отрезку — вставить вершину",
  line: "Щелчки ставят вершины; правая кнопка, Enter или двойной щелчок — закончить; щелчок по концу готовой линии — продолжить её; Backspace — убрать последнюю точку",
  polygon: "Щелчки ставят вершины; правая кнопка, Enter, двойной щелчок или щелчок по первой вершине — замкнуть",
  point: "Каждый щелчок — отдельная точка",
  tie: "Тай-линия для «Собрать из контуров»: два щелчка по соответствующим вершинам контуров соседних разрезов",
  insert: "Щелчок по отрезку вставляет в него вершину",
};
