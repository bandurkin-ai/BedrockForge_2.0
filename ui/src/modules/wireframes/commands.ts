import type { Command } from "../../shell/commands";

const DIALOGS: [string, string, string?][] = [
  ["model.stitch", "Сшить контуры"], ["model.assemble", "Собрать из контуров"], ["model.extrude", "Выдавить"],
  ["model.repair", "Исправить"], ["model.simplify", "Упростить"], ["model.smooth", "Сгладить"],
  ["model.merge", "В один объект"], ["model.split", "На части"], ["model.boolean", "Булева"],
  ["model.clip", "Обрезать"], ["model.intersection", "Линии пересечения"],
  ["model.dtm", "Построить ЦТМ", "Окно «Построение ЦТМ»: поверхность из слоёв точек и линий и устьев скважин (триангуляция Делоне с ограничениями), граница — замкнутая линия, наибольшая длина ребра; результат — сразу в раздел «ЦТМ»."],
  ["model.contours", "Изолинии"], ["model.volume", "Объём"], ["model.triangles", "Треугольники"],
];

const OBJECT = "Без kind и id — объект, выбранный в дереве или в 3D-окне.";

/** Wireframes and modelling operations (Э8, Э9): 16 dialogs model.* (incl. model.rebuild); DTMs and the objects
 * of the data tree — rename, colour, delete (Э7; handlers — `bind.ts`, `objectCommands.ts`); the checks of a
 * wireframe (Э8; `checkCommands.ts`). */
export const commands: Command[] = [
  {
    id: "wireframe.check",
    title: "Проверить каркас",
    description:
      "Запустить задание «Проверка и копии для показа» для каркаса или ЦТМ: согласованность нормалей, самопересечения (пересекающиеся треугольники — красным в 3D-окне), упрощённые копии для показа. Итог — в «Свойствах» каркаса: замкнутость, открытые и неманифолдные рёбра, вырожденные треугольники, нормали, самопересечения, площадь и объём. Данные каркаса не меняются. Без wireframe_id — выбранный каркас.",
    params: { wireframe_id: { type: "integer" } },
    stage: "Э8",
  },
  { id: "wireframe.export", title: "Экспорт каркаса", description: "Каркас в DXF или OBJ.", params: { format: { type: "string", enum: ["dxf", "obj"] }, wireframe_id: { type: "integer" } }, required: ["format"], stage: "Э8" },
  {
    id: "wireframe.dtm",
    title: "В ЦТМ",
    description:
      "Перенести каркас поверхности в раздел «ЦТМ» (on: true или без on) или вернуть в «Каркасы» (on: false — настройки рельефа сбрасываются, растр снимается). Настройки ЦТМ: raster_id — растр в плане на рельефе (0 — снять растр; вертикальный растр не кладётся), clip — «Скрыть данные за ЦТМ», tint — цвет-множитель растра #rrggbb (белый — без изменений), opacity — непрозрачность растра 0…1, shade — тени рельефа. Настройки без on переносят каркас в ЦТМ. Без wireframe_id — выбранный каркас.",
    params: {
      on: { type: "boolean" },
      wireframe_id: { type: "integer" },
      raster_id: { type: "integer", description: "Номер растра в плане; 0 — снять растр" },
      clip: { type: "boolean" },
      tint: { type: "string", description: "#rrggbb" },
      opacity: { type: "number" },
      shade: { type: "boolean" },
    },
    confirm: true,
    stage: "Э7",
  },
  {
    id: "wireframe.attr",
    title: "Атрибут каркаса",
    description:
      "Атрибуты пользователя у каркаса или ЦТМ: value — добавить атрибут name или изменить его значение (текст, число, «да»/«нет»; null или пусто — пустое значение), rename — новое название, remove: true — удалить. Без wireframe_id — выбранный каркас. Без value, rename и remove — открыть «Свойства» с атрибутами.",
    params: { wireframe_id: { type: "integer" }, name: { type: "string" }, value: { type: "string", nullable: true }, rename: { type: "string" }, remove: { type: "boolean" } },
    confirm: true,
    stage: "Э7",
  },
  {
    id: "object.rename",
    title: "Переименовать объект",
    description: `Новое название слоя линий (lines), растра (raster), каркаса или ЦТМ (wireframe). ${OBJECT} Без name название спрашивается в окне.`,
    params: { kind: { type: "string", enum: ["wireframe", "lines", "raster"] }, id: { type: "integer" }, name: { type: "string" } },
    confirm: true,
    stage: "Э7",
  },
  {
    id: "object.color",
    title: "Цвет объекта",
    description: `Цвет слоя линий (lines), каркаса или ЦТМ (wireframe): color — #rrggbb, null — без собственного цвета. ${OBJECT} Без color — открыть «Свойства», где цвет выбирается.`,
    params: { kind: { type: "string", enum: ["wireframe", "lines"] }, id: { type: "integer" }, color: { type: "string", nullable: true } },
    confirm: true,
    stage: "Э7",
  },
  {
    id: "object.delete",
    title: "Удалить объект",
    description: `Удалить из проекта каркас или ЦТМ (wireframe), растр (raster; растр, наложенный на ЦТМ, не удаляется) или файл линий со всеми его слоями (line_file, id — номер файла). Отдельный слой линий не удаляется. ${OBJECT} Отменить удаление нельзя.`,
    params: { kind: { type: "string", enum: ["wireframe", "raster", "line_file"] }, id: { type: "integer" } },
    confirm: true,
    stage: "Э7",
  },
  { id: "model.operation", title: "Операция с каркасами", description: "Выполнить операцию моделирования op с параметрами params (JSON) и именем результата.", params: { op: { type: "string" }, params: { type: "string" }, name: { type: "string" } }, required: ["op"], confirm: true, stage: "Э9" },
  { id: "model.rebuild", title: "Перестроить", description: "Повторить построение каркаса на изменённых исходных объектах.", params: { wireframe_id: { type: "integer" } }, confirm: true, stage: "Э9" },
  { id: "model.self_intersections", title: "Самопересечения", description: "Показать пересекающиеся треугольники каркаса: красным в 3D-окне, числа — в «Свойствах». Каркас, который ещё не проверялся, сначала проверяется (задание «Проверка и копии для показа»). Без wireframe_id — выбранный каркас.", params: { wireframe_id: { type: "integer" } }, stage: "Э8" },
  ...DIALOGS.map(([id, title, description]): Command => ({ id, title, description: description ?? `Диалог «${title}».`, uiOnly: true, stage: "Э9" })),
];
