import type { Command } from "../../shell/commands";

const DIALOGS: [string, string][] = [
  ["model.stitch", "Сшить контуры"], ["model.assemble", "Собрать из контуров"], ["model.extrude", "Выдавить"],
  ["model.repair", "Исправить"], ["model.simplify", "Упростить"], ["model.smooth", "Сгладить"],
  ["model.merge", "В один объект"], ["model.split", "На части"], ["model.boolean", "Булева"],
  ["model.clip", "Обрезать"], ["model.intersection", "Линии пересечения"], ["model.dtm", "Построить ЦТМ"],
  ["model.contours", "Изолинии"], ["model.volume", "Объём"], ["model.triangles", "Треугольники"],
];

/** Wireframes and modelling operations (Э8, Э9): 16 dialogs model.* (incl. model.rebuild). */
export const commands: Command[] = [
  { id: "wireframe.export", title: "Экспорт каркаса", description: "Каркас в DXF или OBJ.", params: { format: { type: "string", enum: ["dxf", "obj"] }, wireframe_id: { type: "integer" } }, required: ["format"], stage: "Э8" },
  { id: "wireframe.dtm", title: "В ЦТМ", description: "Перенести каркас поверхности в раздел «ЦТМ» (on: true или без on) или вернуть в «Каркасы» (on: false — настройки рельефа сбрасываются, растр снимается). Без wireframe_id — выбранный каркас.", params: { on: { type: "boolean" }, wireframe_id: { type: "integer" } }, confirm: true, stage: "Э7" },
  { id: "model.operation", title: "Операция с каркасами", description: "Выполнить операцию моделирования op с параметрами params (JSON) и именем результата.", params: { op: { type: "string" }, params: { type: "string" }, name: { type: "string" } }, required: ["op"], confirm: true, stage: "Э9" },
  { id: "model.rebuild", title: "Перестроить", description: "Повторить построение каркаса на изменённых исходных объектах.", params: { wireframe_id: { type: "integer" } }, confirm: true, stage: "Э9" },
  { id: "model.self_intersections", title: "Самопересечения", description: "Показать пересекающиеся треугольники каркаса.", params: { wireframe_id: { type: "integer" } }, stage: "Э8" },
  ...DIALOGS.map(([id, title]): Command => ({ id, title, description: `Диалог «${title}».`, uiOnly: true, stage: "Э9" })),
];
