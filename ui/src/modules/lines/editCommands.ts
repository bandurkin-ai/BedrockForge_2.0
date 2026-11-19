/**
 * Handlers of the commands of the string editor (ADR 0015 §5): `lines.new_layer`, `lines.edit`, `lines.tool`,
 * `lines.add_line` (without a session — a refusal with a hint), `lines.undo`, `lines.redo`, `lines.save`,
 * `lines.measure`, `lines.finish`, `lines.versions`, `lines.move_selected`, `lines.modify`, `lines.snap`. The
 * ribbon, the panel of the editor, Ctrl+K and Claude run them alike. No React.
 */
import { SNAP_KINDS, type SnapKind } from "../../edit/snap";
import type { EditSession } from "../../edit/session";
import * as cmd from "../../edit/tools";
import { TOOL_TITLES, type Tool } from "../../edit/tools";
import type { Vec3 } from "../../edit/model";
import type { App } from "../../shell/app";
import type { CommandContext } from "../../shell/commands";
import { askText } from "../../shell/dialogs";
import { needWritable, noted, refusal, viewport } from "../../shell/objectRun";
import { openWindow } from "../../shell/windows";
import { lineLayers, loadSceneObjects } from "../scene/objects";
import { newLayer } from "./editApi";
import { currentSession, finishEditor, openEditor, requireSession, saveEditor } from "./editor";
import { layerText, metres, selectionText } from "./measures";
import { applyModify, modifyBlocked, modifyInfo, type FieldKey, type ModifyAction } from "./modify";

const NOT_EDITING = "Откройте слой на правку: меню слоя «Редактировать» или «Моделирование › Новый слой»";

/** The reason of a command of the open session: none open, or the session says why (`edit/tools.blocked`). */
export const sessionReason =
  (id: string | null) =>
  (ctx: CommandContext): string | null => {
    if (!ctx.editing) return NOT_EDITING;
    const s = currentSession();
    if (!s) return NOT_EDITING;
    return id ? cmd.blocked(s, id) : null;
  };

/** `points` of `lines.add_line`: [[x, y, z], …]; a point [x, y] goes to the Z of the panel. */
export function parsePoints(text: string, zLevel: number): Vec3[] {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw refusal("bad_points", "Точки линии не разобраны: это не JSON.", "Передайте массив точек, например [[500100, 7000200, 300], [500150, 7000200, 300]].");
  }
  if (!Array.isArray(data) || !data.length) throw refusal("bad_points", "Нужен непустой массив точек.", "Например [[0, 0, 0], [100, 0, 0]].");
  return data.map((p, i) => {
    if (!Array.isArray(p) || (p.length !== 2 && p.length !== 3) || !p.every((v) => typeof v === "number" && Number.isFinite(v))) {
      throw refusal("bad_points", `Точка ${i + 1}: нужны два или три числа.`, "Точка — [x, y, z] или [x, y] (тогда на Z панели).");
    }
    return [p[0], p[1], p.length === 3 ? p[2] : zLevel] as Vec3;
  });
}

/** The snap settings from the arguments of `lines.snap`. */
export function snapPatch(s: EditSession, a: Record<string, unknown>): string {
  const next = { ...s.snap };
  if (typeof a.enabled === "boolean") next.enabled = a.enabled;
  if (typeof a.kinds === "string") {
    const kinds = a.kinds.split(/[\s,;]+/).filter(Boolean);
    const bad = kinds.find((k) => !SNAP_KINDS.includes(k as SnapKind));
    if (bad) throw refusal("bad_kind", `Нет такой цели привязки: «${bad}».`, `Цели: ${SNAP_KINDS.join(", ")}.`);
    next.kinds = kinds as SnapKind[];
  }
  if (typeof a.radius === "number") next.radius = Math.min(60, Math.max(2, Math.round(a.radius)));
  if (typeof a.project === "boolean") next.project = a.project;
  if (typeof a.grid === "number") {
    if (!(a.grid > 0)) throw refusal("bad_grid", "Шаг сетки должен быть больше нуля.", "Например 10.");
    next.grid = a.grid;
  }
  s.snap = next;
  s.viewChanged();
  s.touch();
  return `Привязка ${next.enabled ? "включена" : "выключена"}: ${next.kinds.length} целей, радиус ${next.radius} px, шаг сетки ${metres(next.grid)}${next.project ? ", с проекцией в плоскость разреза" : ""}`;
}

const NUMBER_KEYS: FieldKey[] = ["dx", "dy", "dz", "distance", "tolerance", "step", "iterations"];

export function bindEditor(app: App): void {
  const writable = needWritable(app);
  const editing = (id: string | null) => (ctx: CommandContext) => writable(ctx) ?? sessionReason(id)(ctx);
  const bind = (id: string, run: (a: Record<string, unknown>) => Promise<string | void> | string | void, reason: (ctx: CommandContext) => string | null) => app.registry.bind(id, noted(app, async (a) => run(a)), reason);

  bind(
    "lines.new_layer",
    async (a) => {
      let name = typeof a.name === "string" ? a.name.trim() : "";
      if (!name) {
        const asked = await askText({ title: "Новый слой линий", label: "Название (станет кодом стринга в STR)", initial: "Новый слой", okLabel: "Создать" });
        if (asked === null) return "Новый слой не создан";
        name = asked;
      }
      const made = await newLayer(name);
      await loadSceneObjects();
      await openEditor(app, made.id);
      return `Создан слой «${made.name}» и открыт на правку: инструмент «Линия» или «Полигон», затем Ctrl+S`;
    },
    writable,
  );

  bind(
    "lines.edit",
    async (a) => {
      const selected = app.store.get().selected;
      const id = typeof a.layer_id === "number" ? a.layer_id : selected?.kind === "lines" ? selected.id : null;
      if (id === null) throw refusal("no_layer", "Не выбран слой линий.", "Выберите слой в разделе «Линии» дерева или укажите layer_id.");
      return openEditor(app, id);
    },
    writable,
  );

  bind(
    "lines.tool",
    (a) => {
      const s = requireSession();
      s.setTool(a.tool as Tool);
      return `Инструмент: ${TOOL_TITLES[a.tool as Tool]}`;
    },
    editing(null),
  );

  // without a session the refusal comes with its hint (Claude reads it): the reason is only about the project
  bind(
    "lines.add_line",
    (a) => {
      const s = requireSession();
      const points = parsePoints(String(a.points), s.zLevel);
      const id = cmd.addLine(s, points, a.closed === true);
      if (id < 0) throw refusal("bad_points", "Линия не добавлена.", "Передайте хотя бы одну точку.");
      return `Добавлена ${a.closed === true && points.length >= 3 ? "замкнутая линия" : "линия"}: ${selectionText(s) ?? `вершин ${points.length}`}. Сохранить — lines.save`;
    },
    writable,
  );

  bind("lines.undo", () => (requireSession().undo(), requireSession().message ?? "Отменено"), editing("lines.undo"));
  bind("lines.redo", () => (requireSession().redo(), requireSession().message ?? "Возвращено"), editing("lines.redo"));
  bind("lines.save", (a) => saveEditor(app, typeof a.note === "string" ? a.note : ""), editing("lines.save"));

  bind(
    "lines.measure",
    () => {
      const s = requireSession();
      return [selectionText(s), layerText(s)].filter(Boolean).join(". ");
    },
    editing(null),
  );

  bind("lines.finish", async () => ((await finishEditor(app)) ? "Правка завершена" : "Правка продолжается"), sessionReason(null));

  bind(
    "lines.versions",
    async (a) => {
      const selected = app.store.get().selected;
      const id = typeof a.layer_id === "number" ? a.layer_id : currentSession()?.layerId ?? (selected?.kind === "lines" ? selected.id : null);
      if (id === null) throw refusal("no_layer", "Не выбран слой линий.", "Выберите слой в разделе «Линии» дерева или откройте его на правку.");
      if (!lineLayers().length) await loadSceneObjects();
      const name = lineLayers().find((l) => l.id === id)?.name ?? `№${id}`;
      openWindow("lines-versions", `Версии: ${name}`, "form", { layerId: id }, ...viewport());
      return `Открыт журнал версий слоя «${name}»`;
    },
    (ctx) => (ctx.hasProject ? null : "Откройте или создайте проект"),
  );

  bind(
    "lines.move_selected",
    () => {
      const s = requireSession();
      openWindow("lines-move", "Перенести в другой слой", "medium", { layerId: s.layerId }, ...viewport());
      return "Открыто окно «Перенести в другой слой»: выберите слой";
    },
    editing("lines.move_selected"),
  );

  bind(
    "lines.modify",
    (a) => {
      const s = requireSession();
      const action = a.action as ModifyAction;
      const info = modifyInfo(action);
      if (!info) throw refusal("bad_action", `Нет такого действия: «${String(a.action)}».`, "Действия перечислены в описании команды.");
      const values: Partial<Record<FieldKey, number>> = {};
      for (const k of NUMBER_KEYS) if (typeof a[k] === "number") values[k] = a[k] as number;
      // an action with numbers and none given: the window «Правка выбранного» asks them
      if (info.fields && !info.fields.some((f) => values[f.key] !== undefined)) {
        const why = modifyBlocked(s, action);
        if (why) throw refusal("not_available", why, "Выберите линии инструментом «Выбор» и повторите.");
        openWindow("lines-modify", info.title.replace("…", ""), "compact", { action }, ...viewport());
        return `Открыто окно «${info.title.replace("…", "")}»`;
      }
      if (info.fields) for (const f of info.fields) if (values[f.key] === undefined && (f.key === "dx" || f.key === "dy" || f.key === "dz")) values[f.key] = 0;
      return applyModify(s, action, values);
    },
    editing(null),
  );

  bind("lines.snap", (a) => snapPatch(requireSession(), a), editing(null));
}
