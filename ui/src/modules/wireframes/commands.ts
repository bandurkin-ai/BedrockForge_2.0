import type { Command } from "../../shell/commands";
import { DWG_VERSIONS, WIREFRAME_FORMAT_IDS } from "./export";

/** The windows of the operations (ADR 0015 §6): [id, title, description]; `model.assemble` and `model.triangles` —
 * panels of the edit mode (9.32–9.33); `model.dtm` — the window «Построение ЦТМ» (`modules/dtm`). */
const DIALOGS: [string, string, string?][] = [
  ["model.stitch", "Сшить контуры", "Окно «Сшить контуры»: замкнутые контуры разрезов (и тай-линии с ROLE = TIE) из слоёв линий → замкнутое тело; торцы — крышка, в точку, в линию, продолжение, открыто."],
  ["model.assemble", "Собрать из контуров", "Панель «Собрать из контуров» в режиме правки слоя линий (как связывание стрингов в Micromine): замкнутые контуры щёлкаются по порядку, каркас растёт в 3D-окне (контуры могут быть не сохранены); видны число треугольников и объём; «Замкнуть торцы» — плоские крышки; тай-линии между вершинами соседних контуров направляют сшивку; «Создать каркас» сохраняет слой и ставит операцию «Сшить контуры». layer_id — слой с контурами (без него — слой на правке или выбранный в дереве)."],
  ["model.extrude", "Выдавить", "Окно «Выдавить»: замкнутые полигоны слоя → тело по нормали, вертикали или вектору, в одну или обе стороны."],
  ["model.repair", "Исправить", "Окно «Исправить каркас»: сшивка вершин с допуском, повторные и вырожденные треугольники, рёбра с тремя и более треугольниками, отверстия, нормали; отчёт «было → стало»."],
  ["model.simplify", "Упростить", "Окно «Упростить»: до числа треугольников или допуска отклонения; в отчёте — изменение объёма и наибольшее отклонение."],
  ["model.smooth", "Сгладить", "Окно «Сгладить»: сглаживание Таубина с сохранением объёма; в отчёте — изменение объёма и наибольшее отклонение."],
  ["model.merge", "В один объект", "Окно «В один объект»: несколько каркасов → один."],
  ["model.split", "На части", "Окно «На части»: связные части каркаса → отдельные каркасы."],
  ["model.boolean", "Булева", "Окно «Булева операция»: объединение, пересечение, вычитание замкнутых тел."],
  ["model.clip", "Обрезать", "Окно «Обрезать»: каркас плоскостью (уровнем), полигоном в плане или поверхностью (ЦТМ, разлом); op — какая обрезка открыта."],
  ["model.intersection", "Линии пересечения", "Окно «Линии пересечения»: линия пересечения двух каркасов → слой линий."],
  ["model.dtm", "Построить ЦТМ", "Окно «Построение ЦТМ»: поверхность из слоёв точек и линий и устьев скважин (триангуляция Делоне с ограничениями), граница — замкнутая линия, наибольшая длина ребра; результат — сразу в раздел «ЦТМ»."],
  ["model.contours", "Изолинии", "Окно «Изолинии»: горизонтали поверхности с шагом, основные — каждая N-я, атрибут ELEV → слой линий."],
  ["model.volume", "Объём", "Окно «Объём»: выемка и насыпь между поверхностями или поверхностью и уровнем, в общем контуре или полигоне; отчёт и тела выемки и насыпи."],
  ["model.triangles", "Треугольники", "Панель «Править треугольники» каркаса: щелчок по треугольнику (точный выбор) отмечает его к удалению; «Добавить» — новый треугольник по трём вершинам каркаса (щелчок берёт ближайшую вершину треугольника под курсором). «Применить» ставит операцию «Править треугольники»: результат — новый каркас, исходный не меняется. wireframe_id — каркас (без него — выбранный)."],
];

/** Panels of the edit mode (9.32–9.33): no window, no parameters of one. The windows — `modules/model/bind.ts`. */
const PANELS: Record<string, Command["params"]> = {
  "model.assemble": { layer_id: { type: "integer", description: "Слой линий с контурами" } },
  "model.triangles": { wireframe_id: { type: "integer", description: "Каркас, треугольники которого правятся" } },
};
const DIALOG_PARAMS = {
  wireframe_id: { type: "integer", description: "Каркас, с которым открывается окно" },
  layer_id: { type: "integer", description: "Слой линий, с которым открывается окно" },
} as const;
const CLIP_OPS = ["clip_plane", "clip_polygon", "clip_surface"];

/** The operations of `model.operation` (ТЗ 7.23; `core/bf_core/ops/schema.py`). */
export const OPERATION_IDS = ["stitch", "extrude", "edit_triangles", "repair", "simplify", "smooth", "merge", "split", "boolean", "clip_plane", "clip_polygon", "clip_surface", "intersection_lines", "dtm", "contours", "volume_between"];

const OPERATION =
  "Выполнить операцию моделирования (ТЗ 7.23) как задание: результат — новый объект в папке первого исходного, исходные не меняются; в «Свойствах» результата — операция, исходные, параметры, допуски и «Перестроить». Предусловия проверяются до запуска: отказ называет причину (незамкнутый каркас — число открытых рёбер; исправить — op repair с этим wireframe_id). params — JSON-объект. Каркасы — номера wireframe_id; линии — {layer_id, lines}: lines — номера линий с 0 (null — все линии слоя); line — {layer_id, line} (line null — единственная замкнутая линия слоя). Числа можно с запятой. " +
  "stitch {contours:[{layer_id, lines}], start_cap, end_cap: flat|point|line|extend|none, cap_distance}; extrude {contours, distance, direction: normal|vertical|vector, vector:[x,y,z], both_sides}; " +
  "repair {wireframe_id, weld_on, weld, drop_duplicate, drop_degenerate, fix_nonmanifold, fill_holes, orient}; simplify {wireframe_id, target_triangles или tolerance}; smooth {wireframe_id, iterations, strength 0…0,95, keep_boundary}; " +
  "merge {wireframe_ids ≥ 2}; split {wireframe_id, max_parts}; boolean {op: union|intersection|difference, a, b:[…]} — только замкнутые тела; clip_plane {wireframe_id, point, normal, keep: positive|negative|both}; " +
  "clip_polygon {wireframe_id, polygon:{layer_id, line}, keep: inside|outside|both}; clip_surface {wireframe_id, surface_id, keep: above|below|both, direction: auto|normal}; intersection_lines {a, b} → слой линий; " +
  "dtm {layers, set_ids, breaklines, boundary, max_edge}; contours {wireframe_id, interval, base, major_every} → слой линий; volume_between {top, mode: surface|level, bottom или level, polygon, report_only} — выемка и насыпь. " +
  "name — название результата (по умолчанию — из операции и исходных), folder — папка дерева. Ход — на вкладке «Задания», отчёт открывается, когда задание готово.";

const OBJECT = "Без kind и id — объект, выбранный в дереве или в 3D-окне.";

/** Wireframes and modelling operations (Э8, Э9): 16 dialogs model.* (incl. model.rebuild); DTMs and the objects
 * of the data tree — rename, colour, delete (Э7; handlers — `bind.ts`, `objectCommands.ts`); the checks of a
 * wireframe (Э8; `checkCommands.ts`); the export of wireframes (Э8; `exportCommands.ts`). */
export const commands: Command[] = [
  {
    id: "wireframe.check",
    title: "Проверить каркас",
    description:
      "Запустить задание «Проверка и копии для показа» для каркаса или ЦТМ: согласованность нормалей, самопересечения (пересекающиеся треугольники — красным в 3D-окне), упрощённые копии для показа. Итог — в «Свойствах» каркаса: замкнутость, открытые и неманифолдные рёбра, вырожденные треугольники, нормали, самопересечения, площадь и объём. Данные каркаса не меняются. Без wireframe_id — выбранный каркас.",
    params: { wireframe_id: { type: "integer" } },
    stage: "Э8",
  },
  {
    id: "wireframe.export",
    title: "Экспорт каркасов",
    description:
      "Каркасы и ЦТМ в файл: Micromine TRIDB (tridb), DXF (dxf), DWG через ODA File Converter (dwg, version — версия DWG, по умолчанию R2013), Wavefront OBJ (obj), STL (stl; binary: true — двоичный, координаты float32 с потерей точности на UTM; false — текстовый, без потерь) или Surpac DTM с файлом точек STR (dtm). wireframe_id — один каркас, folder — каркасы папки дерева (например, все каркасы одного загруженного файла), section — все каркасы раздела «Каркасы» (wireframes) или «ЦТМ» (dtm); без них — выбранный каркас. Открывает окно «Экспорт каркасов» с этим форматом и каркасами: файл выбирает пользователь, экспорт идёт как задание.",
    params: {
      format: { type: "string", enum: WIREFRAME_FORMAT_IDS },
      wireframe_id: { type: "integer" },
      folder: { type: "string", description: "Папка дерева: «Модель» или «Модель/Тела»" },
      section: { type: "string", enum: ["wireframes", "dtm"] },
      version: { type: "string", enum: [...DWG_VERSIONS], description: "Версия DWG" },
      binary: { type: "boolean", description: "STL: двоичный (true) или текстовый (false)" },
    },
    stage: "Э8",
  },
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
  {
    id: "model.operation",
    title: "Операция моделирования",
    description: OPERATION,
    params: {
      op: { type: "string", enum: OPERATION_IDS },
      params: { type: "string", description: "JSON-объект параметров операции" },
      name: { type: "string", description: "Название результата" },
      folder: { type: "string", description: "Папка дерева для результата" },
    },
    required: ["op"],
    confirm: true,
    stage: "Э9",
  },
  {
    id: "model.rebuild",
    title: "Перестроить",
    description:
      "Повторить операцию, которой построен каркас (wireframe_id) или слой линий (layer_id: изолинии, линии пересечения), на текущих исходных объектах с прежними параметрами и тай-линиями; ЦТМ — заново из её источников. Результат — новый объект, прежний не меняется. Исходный удалён или линии нет — отказ с его именем. Без параметров — выбранный каркас или слой.",
    params: { wireframe_id: { type: "integer" }, layer_id: { type: "integer" } },
    confirm: true,
    stage: "Э9",
  },
  { id: "model.self_intersections", title: "Самопересечения", description: "Показать пересекающиеся треугольники каркаса: красным в 3D-окне, числа — в «Свойствах». Каркас, который ещё не проверялся, сначала проверяется (задание «Проверка и копии для показа»). Без wireframe_id — выбранный каркас.", params: { wireframe_id: { type: "integer" } }, stage: "Э8" },
  ...DIALOGS.map(
    ([id, title, description]): Command => ({
      id,
      title,
      description: description ?? `Диалог «${title}».`,
      ...(id === "model.dtm" ? {} : { params: PANELS[id] ?? (id === "model.clip" ? { ...DIALOG_PARAMS, op: { type: "string", enum: CLIP_OPS } } : DIALOG_PARAMS) }),
      uiOnly: true,
      stage: "Э9",
    }),
  ),
];
