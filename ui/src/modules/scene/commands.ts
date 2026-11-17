import type { Command } from "../../shell/commands";

const VIEW = { type: "string", enum: ["top", "front", "side", "iso", "section"] } as const;
const LAYER = { type: "string", enum: ["set", "paths", "intervals", "collars", "wireframe", "lines", "raster", "blocks"] } as const;

/** 3D scene, selection (Э6). scene.style and layer.show are stored already on Э1. */
export const commands: Command[] = [
  { id: "scene.view", title: "Вид", description: "Повернуть камеру: в план (top), сбоку Z/X (front), сбоку Z/Y (side), изометрия (iso), в плоскость разреза (section). Показывает то, что на экране, не вписывая всё.", params: { view: VIEW }, required: ["view"], stage: "Э6" },
  { id: "scene.fit", title: "Вписать", description: "Показать все видимые объекты в окне отсечения, не поворачивая вид.", stage: "Э6" },
  {
    id: "scene.style",
    title: "Оформление сцены",
    description: "Преувеличение по вертикали (1, 2, 3, 5), сетка, перспектива, непрозрачность каркасов 0,1–1.",
    params: { vexag: { type: "number", enum: [1, 2, 3, 5] }, grid: { type: "boolean" }, perspective: { type: "boolean" }, wireframe_opacity: { type: "number" } },
  },
  { id: "layer.show", title: "Видимость слоя", description: "Показать или скрыть слой в 3D.", params: { layer: LAYER, id: { type: "integer" }, visible: { type: "boolean" } }, required: ["layer", "visible"] },
  { id: "set.select", title: "Активный набор", description: "Сделать набор скважин активным.", params: { set_id: { type: "integer" } }, required: ["set_id"], stage: "Э4" },
  { id: "hole.select", title: "Выбрать скважину", description: "Выбрать скважину (null — снять выбор).", params: { hole_id: { type: "string", nullable: true }, set_id: { type: "integer" } }, required: ["hole_id"], stage: "Э6" },
  { id: "object.select", title: "Выбрать объект", description: "Выбрать объект проекта: показать его свойства.", params: { kind: { type: "string", enum: ["wireframe", "lines", "raster", "blocks"] }, id: { type: "integer" } }, required: ["kind", "id"], stage: "Э7" },
  { id: "table.open", title: "Таблица набора", description: "Открыть таблицу набора в нижней панели.", params: { kind: { type: "string" }, set_id: { type: "integer" } }, required: ["kind"], stage: "Э4" },
  { id: "stats.open", title: "Статистика", description: "Открыть анализ вкладки «Статистика».", params: { analysis: { type: "string", enum: ["declus", "nscore", "variogram", "map", "bootstrap", "local"] } }, required: ["analysis"], stage: "Э13" },
  {
    id: "holes.collar_style",
    title: "Устья: значок и подпись",
    description: "Стиль устьев набора в 3D: значок (circle, square, diamond, triangle, cross, star), цвет (#rrggbb или audit — по аудиту), размер, подпись номера. Без параметров стиля открывает форму.",
    params: { set_id: { type: "integer" }, shape: { type: "string", enum: ["circle", "square", "diamond", "triangle", "cross", "star"] }, color: { type: "string" }, size: { type: "number" }, label: { type: "boolean" }, fontSize: { type: "number" }, bold: { type: "boolean" }, labelColor: { type: "string" } },
    stage: "Э6",
  },
  {
    id: "holes.trace_style",
    title: "Стволы: линия и риски",
    description: "Стиль стволов набора в 3D: толщина и цвет, риски глубины (шаг, длина, подписи), глубина на забое, пересечения с каркасами и блоками. Без параметров стиля открывает форму.",
    params: { set_id: { type: "integer" }, width: { type: "number" }, color: { type: "string" }, ticks: { type: "boolean" }, tickStep: { type: "number" }, tickLabels: { type: "boolean" }, labelStep: { type: "number" }, endDepth: { type: "boolean" }, intersections: { type: "boolean" } },
    stage: "Э6",
  },
  { id: "holes.data_window", title: "Данные по скважинам", description: "Окно колонок данных вдоль выбранных скважин.", uiOnly: true, stage: "Э6" },
  {
    id: "holes.show_data",
    title: "Показать данные по стволу",
    description: "Открыть форму отображения данных вдоль стволов (ТЗ 7.16). С таблицей и полем сразу создаёт черновик с этим полем: он виден на скважинах, в дерево попадает только по «Сохранить» в форме. form: grade — содержания, lith — литология, event — события (по умолчанию — по виду поля). Без set_id — активный набор.",
    params: { set_id: { type: "integer" }, table: { type: "string", description: "Название таблицы набора, например «Пробы»" }, field: { type: "string", description: "Поле таблицы, например AU" }, form: { type: "string", enum: ["grade", "lith", "event"] } },
  },
  { id: "downhole.open", title: "Отображение по стволу", description: "Форма нового отображения: Содержания…, Литология… или События…", params: { form: { type: "string", enum: ["grade", "lith", "event"] }, set_id: { type: "integer" }, chart_id: { type: "integer" } }, uiOnly: true },
  { id: "styles.export", title: "Экспорт стилей", description: "Сохранить библиотеку стилей проекта в файл .bfstyle.json.", uiOnly: true },
  { id: "styles.import", title: "Импорт стилей", description: "Добавить стили из файла .bfstyle.json; при совпадении названий — заменить, оставить оба или пропустить.", uiOnly: true },
];
