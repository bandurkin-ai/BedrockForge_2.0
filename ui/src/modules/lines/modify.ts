/**
 * The editing actions of `lines.modify` (ADR 0015 §5; ТЗ 7.22): close, open, break at a vertex, join two,
 * reverse; shift and copy by a vector (also by Z); a copy with an offset; thin out (Douglas–Peucker); densify;
 * smooth; delete; finish the line being drawn. The actions with numbers get them from the command or from the
 * window «Правка выбранного» (`ModifyWindow.tsx`): a comma and a dot both mark the decimals. No React.
 */
import type { EditSession } from "../../edit/session";
import * as cmd from "../../edit/tools";
import type { IconName } from "../../shell/Icon";
import { refusal } from "../../shell/objectRun";

export type ModifyAction = "close" | "open" | "break" | "join" | "reverse" | "translate" | "copy" | "offset" | "simplify" | "densify" | "smooth" | "delete" | "finish_line";
export type FieldKey = "dx" | "dy" | "dz" | "distance" | "tolerance" | "step" | "iterations";

export interface FieldSpec {
  key: FieldKey;
  label: string;
  initial: string;
  hint: string;
  /** the least value allowed (strictly greater with `above`) */
  min?: number;
  above?: boolean;
  integer?: boolean;
}

export interface ModifyInfo {
  id: ModifyAction;
  title: string;
  /** what it does (the tooltip) */
  action: string;
  keys: string;
  icon: IconName;
  fields?: FieldSpec[];
}

const VECTOR: FieldSpec[] = [
  { key: "dx", label: "ΔX, м", initial: "0", hint: "На восток — плюс" },
  { key: "dy", label: "ΔY, м", initial: "0", hint: "На север — плюс" },
  { key: "dz", label: "ΔZ, м", initial: "0", hint: "Вверх — плюс; вершины без высоты остаются без неё" },
];

/** The actions in the order of the panel. */
export const MODIFY: readonly ModifyInfo[] = [
  { id: "finish_line", title: "Закончить линию", action: "Закончить рисуемую линию; полигон замыкается", keys: "Enter, двойной щелчок, правая кнопка", icon: "edExit" },
  { id: "close", title: "Замкнуть", action: "Замкнуть выбранные линии в полигоны", keys: "", icon: "edClose" },
  { id: "open", title: "Разомкнуть", action: "Разомкнуть выбранные полигоны", keys: "", icon: "edOpen" },
  { id: "break", title: "Разорвать", action: "Разорвать линию в выбранной вершине", keys: "", icon: "edBreak" },
  { id: "join", title: "Соединить", action: "Соединить две выбранные линии ближайшими концами", keys: "", icon: "edJoin" },
  { id: "reverse", title: "Развернуть", action: "Развернуть направление выбранных линий", keys: "", icon: "edReverse" },
  { id: "translate", title: "Сдвинуть…", action: "Сдвинуть выбранные линии на вектор, в том числе по Z", keys: "", icon: "edMove", fields: VECTOR },
  { id: "copy", title: "Копия на вектор…", action: "Копия выбранных линий, сдвинутая на вектор", keys: "", icon: "copy", fields: VECTOR },
  { id: "offset", title: "Копия со смещением…", action: "Параллельная копия на расстоянии; у полигона больше нуля — наружу, меньше — внутрь", keys: "", icon: "edOffset", fields: [{ key: "distance", label: "Смещение, м", initial: "5", hint: "У полигона: больше нуля — наружу, меньше — внутрь" }] },
  { id: "simplify", title: "Проредить…", action: "Убрать вершины, отстоящие от линии меньше допуска (Дуглас–Пекер)", keys: "", icon: "edSimplify", fields: [{ key: "tolerance", label: "Допуск, м", initial: "0,5", hint: "Вершины ближе допуска к линии удаляются", min: 0, above: true }] },
  { id: "densify", title: "Сгустить…", action: "Добавить вершины: отрезки не длиннее шага", keys: "", icon: "edDensify", fields: [{ key: "step", label: "Шаг, м", initial: "5", hint: "Наибольшая длина отрезка", min: 0, above: true }] },
  { id: "smooth", title: "Сгладить…", action: "Сгладить выбранные линии срезанием углов", keys: "", icon: "edSmooth", fields: [{ key: "iterations", label: "Проходов", initial: "1", hint: "От 1 до 10", min: 1, integer: true }] },
  { id: "delete", title: "Удалить", action: "Удалить выбранные вершины или линии", keys: "Delete", icon: "edErase" },
];

export const MODIFY_IDS = MODIFY.map((m) => m.id);
export const modifyInfo = (id: string): ModifyInfo | undefined => MODIFY.find((m) => m.id === id);

/** A number typed by a person: «1,5», «-2.25», «1 000,5»; null — not a number. */
export function parseNumber(text: string): number | null {
  const t = text.trim().replace(/[\s  ]/g, "").replace(",", ".");
  if (!/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(t)) return null;
  const v = Number(t);
  return Number.isFinite(v) ? v : null;
}

/** The fields of the window checked: numbers, or the first error in Russian. */
export function readFields(fields: readonly FieldSpec[], values: Partial<Record<FieldKey, string>>): { values: Partial<Record<FieldKey, number>> } | { error: string } {
  const out: Partial<Record<FieldKey, number>> = {};
  for (const f of fields) {
    const v = parseNumber(values[f.key] ?? f.initial);
    if (v === null) return { error: `«${f.label}»: введите число (десятичный знак — запятая или точка)` };
    if (f.integer && !Number.isInteger(v)) return { error: `«${f.label}»: нужно целое число` };
    if (f.min !== undefined && (f.above ? v <= f.min : v < f.min)) return { error: `«${f.label}»: число должно быть ${f.above ? "больше" : "не меньше"} ${String(f.min).replace(".", ",")}` };
    if (f.key === "iterations" && v > 10) return { error: `«${f.label}»: не больше 10` };
    out[f.key] = v;
  }
  return { values: out };
}

/** The id of `edit/tools.blocked` for an action. */
const BLOCK_ID: Record<ModifyAction, string> = {
  finish_line: "lines.finish", close: "lines.close", open: "lines.open", break: "lines.break", join: "lines.join", reverse: "lines.reverse", translate: "lines.translate", copy: "lines.copy",
  offset: "lines.offset", simplify: "lines.simplify", densify: "lines.densify", smooth: "lines.smooth", delete: "lines.delete",
};

/** Why the action cannot run now (the panel shows it), or null. */
export function modifyBlocked(s: EditSession, action: ModifyAction): string | null {
  return cmd.blocked(s, BLOCK_ID[action]);
}

/** Runs an action with its numbers; the text of the result, or a refusal {code, message, hint}. */
export function applyModify(s: EditSession, action: ModifyAction, v: Partial<Record<FieldKey, number>> = {}): string {
  const info = modifyInfo(action) as ModifyInfo;
  const why = modifyBlocked(s, action);
  if (why) throw refusal("not_available", why, "Выберите линии инструментом «Выбор» (Shift — добавить к выбору) и повторите.");
  if (info.fields) {
    const missing = info.fields.find((f) => v[f.key] === undefined);
    if (missing) throw refusal("bad_value", `Не задано «${missing.label}».`, "Откройте окно действия кнопкой панели или передайте число.");
  }
  const before = s.layer;
  s.message = null;
  switch (action) {
    case "finish_line":
      s.finishDrawing();
      return "Линия закончена";
    case "close":
    case "open":
      cmd.setClosed(s, action === "close");
      break;
    case "break":
      cmd.breakAtVertex(s);
      break;
    case "join":
      cmd.join(s);
      break;
    case "reverse":
      cmd.reverse(s);
      break;
    case "translate":
    case "copy":
      cmd.translate(s, [v.dx ?? 0, v.dy ?? 0, v.dz ?? 0], action === "copy");
      break;
    case "offset":
      cmd.offset(s, v.distance as number);
      break;
    case "simplify":
      cmd.simplify(s, v.tolerance as number);
      break;
    case "densify":
      cmd.densify(s, v.step as number);
      break;
    case "smooth":
      cmd.smooth(s, v.iterations ?? 1);
      break;
    case "delete":
      cmd.deleteSelection(s);
      break;
  }
  if (s.layer === before) {
    const said = s.message as string | null;
    if (said) throw refusal("nothing_changed", said, "Проверьте выбор и значения и повторите.");
    return `${info.title.replace("…", "")}: изменений нет`;
  }
  return `${info.title.replace("…", "")}: ${s.history.undoLabel ?? "выполнено"} · линий выбрано ${s.selection.lines.length}`;
}
