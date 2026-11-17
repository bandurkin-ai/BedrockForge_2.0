/**
 * Ribbon layout (ТЗ 7.0.4): a button = registry command + preset arguments. The palette and the
 * hotkey table use the same list. Pure module; availability comes from the registry.
 */
import type { CommandContext } from "./commands";
import type { IconName } from "./Icon";
import type { ViewState, WorkspaceState } from "./workspace";

export interface RibbonItem {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint?: string;
  shortcut?: string;
  small?: boolean;
  active?: boolean;
  /** Extra availability of this button (on top of the command's own). */
  unavailable?: (ctx: CommandContext) => string | null;
}

export interface RibbonGroup {
  title: string;
  items: RibbonItem[];
}

export interface RibbonTab {
  id: string;
  title: string;
  groups: RibbonGroup[];
}

const NO_SECTION = (c: CommandContext) => (c.hasSection ? null : "Сначала проведите линию разреза");
const NO_SET = (c: CommandContext) => (c.hasSet ? null : "Нет активного набора скважин");
const NO_HOLE = (c: CommandContext) => (c.hasHole ? null : "Скважина не выбрана");

export const QUICK_PROMPTS: { label: string; prompt: string; needsHole?: boolean }[] = [
  { label: "Сводка по проекту", prompt: "Дай краткую сводку по проекту: наборы скважин и каркасы, объём данных, компоненты, состояние аудита и что стоит проверить в первую очередь." },
  { label: "Разобрать аудит", prompt: "Разбери результаты аудита активного набора: какие проблемы самые серьёзные, в каких скважинах они сосредоточены и с чего начать исправление." },
  { label: "Проверить скважину", prompt: "Проверь выбранную скважину: данные устья, инклинометрию, опробование и замечания аудита. Покажи её на разрезе.", needsHole: true },
];

export function buildRibbon(ws: WorkspaceState, view: ViewState): RibbonTab[] {
  const views: RibbonItem[] = [
    { command: "scene.view", args: { view: "top" }, label: "В план", icon: "cubeTop", hint: "Вид сверху на то, что сейчас на экране", shortcut: "t" },
    { command: "scene.view", args: { view: "front" }, label: "Сбоку Z/X", icon: "cubeLeft", hint: "Вид сбоку на север: Z вверх, X вправо", shortcut: "x" },
    { command: "scene.view", args: { view: "side" }, label: "Сбоку Z/Y", icon: "cubeRight", hint: "Вид сбоку на запад: Z вверх, Y вправо", shortcut: "y" },
  ];
  const section: RibbonItem[] = [
    { command: "section.draw", label: "Разрез", icon: "section2", hint: "Провести линию разреза: два щелчка на виде сверху", shortcut: "s" },
    { command: "section.clip", label: "Отсечение", icon: "columns", hint: "Отсекать всё за пределами окна разреза", shortcut: "c", small: true, active: view.clip, unavailable: NO_SECTION },
    { command: "scene.view", args: { view: "section" }, label: "В плоскость разреза", icon: "section", hint: "Повернуть вид в плоскость разреза, Z вверх", small: true, unavailable: NO_SECTION },
    { command: "section.clear", label: "Сбросить разрез", icon: "clear", small: true, unavailable: NO_SECTION },
  ];
  const assistant: RibbonItem = { command: "panel.set", args: { panel: "assistant" }, label: "Claude", icon: "spark", hint: "Панель ассистента Claude", shortcut: "ctrl+j", active: ws.right && ws.rightTab === "assistant" };

  return [
    {
      id: "home",
      title: "Главная",
      groups: [
        {
          title: "Проект",
          items: [
            { command: "project.new", label: "Новый проект", icon: "plus" },
            { command: "project.manage", label: "Проекты", icon: "folder", hint: "Управление проектами: название, описание, состав, все проекты" },
            { command: "project.export", label: "Экспорт проекта", icon: "export", hint: "Весь проект одним файлом .bfggis…" },
            { command: "project.import", label: "Импорт проекта", icon: "import", hint: "Открыть проект из файла .bfggis" },
            { command: "project.demo", label: "Демо-проект", icon: "cube", hint: "48 скважин, пробы, аудит, каркасы рудного тела", small: true },
            { command: "data.refresh", label: "Обновить", icon: "refresh", hint: "Перечитать данные из ядра", small: true },
            { command: "project.delete", label: "Удалить проект", icon: "trash", small: true },
            { command: "jobs.list", label: "Задания", icon: "list", small: true },
          ],
        },
        {
          title: "Загрузка",
          items: [
            { command: "import.files", label: "Импорт файлов", icon: "import", hint: "Можно перетащить файлы в окно" },
            { command: "import.open", label: "Скважины", icon: "drillholes", hint: "Collar, Survey, Assay (DAT, CSV, Excel)" },
            { command: "wireframes.upload", label: "Каркасы", icon: "wireframe", hint: "Micromine TRIDB, DXF, OBJ, STL" },
            { command: "lines.upload", label: "Линии", icon: "lines", hint: "Micromine STR, Surpac STR, DXF", small: true },
            { command: "rasters.upload", label: "Растры", icon: "raster", hint: "Изображение + файл привязки или GeoTIFF", small: true },
          ],
        },
        {
          title: "OMF",
          items: [
            { command: "omf.import", label: "Импорт OMF", icon: "import", hint: "OMF v1 (Leapfrog OMF 0.9)" },
            { command: "omf.export", label: "Экспорт OMF", icon: "export", hint: "OMF v1 (Leapfrog OMF 0.9)" },
            { command: "logs.open", label: "Журналы", icon: "logFile", small: true },
          ],
        },
        {
          title: "Выделение",
          items: [
            { command: "palette.open", args: { query: "скв " }, label: "Найти скважину", icon: "search", small: true },
            { command: "hole.select", args: { hole_id: null }, label: "Снять выбор", icon: "clear", shortcut: "escape", small: true, unavailable: NO_HOLE },
          ],
        },
        { title: "Ассистент", items: [assistant] },
        { title: "Настройка", items: [{ command: "settings.open", label: "Настройка", icon: "gear", hint: "Быстрые клавиши, папка данных проектов, число исполнителей" }, { command: "license.open", label: "Лицензия", icon: "audit", small: true }, { command: "app.about", label: "О производителе", icon: "info", small: true }] },
      ],
    },
    {
      id: "view",
      title: "Вид",
      groups: [
        { title: "Поворот вида", items: [...views, { command: "scene.fit", label: "Вписать", icon: "maximize", hint: "Показать всё, что сейчас на экране", shortcut: "f", small: true }] },
        {
          title: "Разрез",
          items: [
            ...section,
            { command: "section.step", args: { direction: -1 }, label: "Предыдущий профиль", icon: "chevronUp", shortcut: "[", small: true, unavailable: NO_SECTION },
            { command: "section.step", args: { direction: 1 }, label: "Следующий профиль", icon: "chevronDown", shortcut: "]", small: true, unavailable: NO_SECTION },
          ],
        },
        { title: "Окно", items: [{ command: "scene.clear", label: "Очистить окно", icon: "clear", hint: "Скрыть все объекты; камера, разрез и сетка остаются", shortcut: "ctrl+shift+h" }] },
        {
          title: "Панели",
          items: [
            { command: "panel.set", args: { panel: "project" }, label: "Дерево", icon: "panelLeft", shortcut: "ctrl+b", active: ws.left, small: true },
            { command: "panel.set", args: { panel: "properties" }, label: "Свойства", icon: "properties", active: ws.right && ws.rightTab === "properties", small: true },
            { command: "panel.set", args: { panel: "bottom" }, label: "Нижняя панель", icon: "panelBottom", shortcut: "ctrl+`", active: ws.bottom, small: true },
          ],
        },
        { title: "Просмотр", items: [{ command: "holes.data_window", label: "Данные по скважинам", icon: "striplog", unavailable: NO_SET }] },
        {
          title: "По стволу",
          items: [
            { command: "downhole.open", args: { form: "grade" }, label: "Содержания…", icon: "histogram", hint: "Отображение содержаний вдоль стволов активного набора", small: true, unavailable: NO_SET },
            { command: "downhole.open", args: { form: "lith" }, label: "Литология…", icon: "log", hint: "Отображение литологии вдоль стволов", small: true, unavailable: NO_SET },
            { command: "downhole.open", args: { form: "event" }, label: "События…", icon: "hole", hint: "Отображение событий по скважинам", small: true, unavailable: NO_SET },
            { command: "styles.export", label: "Экспорт стилей", icon: "export", hint: "Библиотека стилей проекта в файл .bfstyle.json", small: true },
            { command: "styles.import", label: "Импорт стилей", icon: "import", hint: "Стили из файла .bfstyle.json", small: true },
          ],
        },
        {
          title: "Оформление",
          items: [
            { command: "scene.style", args: { grid: !view.grid }, label: view.grid ? "Скрыть сетку" : "Показать сетку", icon: "vwGrid", small: true },
            { command: "scene.style", args: { perspective: !view.perspective }, label: view.perspective ? "Ортогональная проекция" : "Перспектива", icon: "vwPerspective", small: true },
            { command: "diag.fps", label: "Счётчик кадров", icon: "chart", hint: "Кадры/с, треугольники, вызовы отрисовки, видеопамять; дымовой сценарий", shortcut: "ctrl+shift+f", small: true },
            { command: "theme.set", label: ws.theme === "dark" ? "Светлая тема" : "Тёмная тема", icon: ws.theme === "dark" ? "sun" : "moon", small: true },
            { command: "ribbon.toggle", label: ws.ribbonCollapsed ? "Развернуть ленту" : "Свернуть ленту", icon: ws.ribbonCollapsed ? "chevronDown" : "chevronUp", shortcut: "ctrl+f1", small: true },
          ],
        },
      ],
    },
    {
      id: "model",
      title: "Моделирование",
      groups: [
        {
          title: "Стринги",
          items: [
            { command: "lines.new_layer", label: "Новый слой", icon: "plus" },
            { command: "lines.edit", label: "Редактировать", icon: "edit" },
            { command: "lines.save", label: "Сохранить", icon: "edSave", shortcut: "ctrl+s", small: true },
            { command: "lines.versions", label: "Версии", icon: "list", small: true },
            { command: "lines.finish", label: "Завершить", icon: "close", small: true },
            { command: "lines.export", args: { format: "str" }, label: "В STR", icon: "export", small: true },
            { command: "lines.export", args: { format: "dxf" }, label: "В DXF", icon: "export", small: true },
          ],
        },
        {
          title: "Каркас из контуров",
          items: [
            { command: "model.stitch", label: "Сшить контуры", icon: "wireframe" },
            { command: "model.assemble", label: "Собрать из контуров", icon: "edPolygon" },
            { command: "model.extrude", label: "Выдавить", icon: "cube", small: true },
            { command: "model.triangles", label: "Треугольники", icon: "edit", small: true },
            { command: "model.rebuild", label: "Перестроить", icon: "refresh", small: true },
          ],
        },
        {
          title: "Правка каркасов",
          items: [
            { command: "model.repair", label: "Исправить", icon: "audit" },
            { command: "model.self_intersections", label: "Самопересечения", icon: "search", small: true },
            { command: "model.simplify", label: "Упростить", icon: "edSimplify", small: true },
            { command: "model.smooth", label: "Сгладить", icon: "edSmooth", small: true },
            { command: "model.merge", label: "В один объект", icon: "plus", small: true },
            { command: "model.split", label: "На части", icon: "columns", small: true },
          ],
        },
        {
          title: "Операции",
          items: [
            { command: "model.boolean", label: "Булева", icon: "quad" },
            { command: "model.clip", label: "Обрезать", icon: "section" },
            { command: "model.intersection", label: "Линии пересечения", icon: "lines", small: true },
            { command: "wireframe.export", args: { format: "dxf" }, label: "Каркас в DXF", icon: "export", small: true },
            { command: "wireframe.export", args: { format: "obj" }, label: "Каркас в OBJ", icon: "export", small: true },
          ],
        },
        {
          title: "Поверхности",
          items: [
            { command: "model.dtm", label: "Построить ЦТМ", icon: "plan" },
            { command: "model.contours", label: "Изолинии", icon: "lines", small: true },
            { command: "model.volume", label: "Объём", icon: "histogram", small: true },
            { command: "wireframe.dtm", args: { on: true }, label: "В ЦТМ", icon: "dtm", small: true },
          ],
        },
      ],
    },
    {
      id: "analysis",
      title: "Анализ",
      groups: [
        {
          title: "Аудит и статистика",
          items: [
            { command: "audit.run", label: "Запустить аудит", icon: "play", hint: "27 правил по устьям, инклинометрии и пробам активного набора", unavailable: NO_SET },
            { command: "panel.set", args: { panel: "bottom", tab: "audit" }, label: "Результаты", icon: "audit", unavailable: NO_SET, active: ws.bottom && ws.bottomTab === "audit" },
            { command: "audit.export", label: "Отчёт Excel", icon: "export", small: true, unavailable: NO_SET },
          ],
        },
        {
          title: "Графики",
          items: [
            { command: "chart.histogram", label: "Гистограмма", icon: "histogram", unavailable: NO_SET },
            { command: "chart.qq", label: "Q-Q график", icon: "qq", small: true, unavailable: NO_SET },
            { command: "chart.dendrogram", label: "Дендрограмма", icon: "dendrogram", small: true, unavailable: NO_SET },
          ],
        },
        {
          title: "Статистика",
          items: [
            ...(
              [
                ["declus", "Декластеризация"],
                ["nscore", "Нормальные оценки"],
                ["variogram", "Вариограмма"],
                ["map", "Карта вариограммы"],
                ["bootstrap", "Бутстрап"],
                ["local", "Карта неопределённости"],
              ] as const
            ).map(([analysis, label], i): RibbonItem => ({ command: "stats.open", args: { analysis }, label, icon: "chart", small: i > 0, unavailable: NO_SET })),
            { command: "stats.swath", label: "SWATH PLOT", icon: "swath", small: true },
          ],
        },
        {
          title: "Таблицы набора",
          items: (
            [
              ["collar", "Устья"],
              ["survey", "Инклинометрия"],
              ["assay", "Пробы"],
            ] as const
          ).map(([kind, label]): RibbonItem => ({ command: "table.open", args: { kind }, label, icon: "table", small: true, unavailable: NO_SET })),
        },
        { title: "Журнал", items: [{ command: "panel.set", args: { panel: "bottom", tab: "log" }, label: "Журнал команд", icon: "list", small: true, active: ws.bottom && ws.bottomTab === "log" }] },
      ],
    },
    {
      id: "projsettings",
      title: "Настройки проекта",
      groups: [
        {
          title: "Обслуживание",
          items: [{ command: "jobs.clear_finished", label: "Очистить выполненные задания", icon: "clear", hint: "Убрать из списка «Задания» готовые и отменённые задания с их журналами" }],
        },
      ],
    },
    {
      id: "claude",
      title: "Claude",
      groups: [
        { title: "Диалог", items: [assistant, { command: "assistant.new", label: "Новый диалог", icon: "chat" }] },
        {
          title: "Быстрые запросы",
          items: QUICK_PROMPTS.map((q): RibbonItem => ({ command: "assistant.ask", args: { prompt: q.prompt }, label: q.label, icon: "spark", small: true, unavailable: q.needsHole ? NO_HOLE : undefined })),
        },
        { title: "Команды", items: [{ command: "palette.open", label: "Все команды", icon: "command", shortcut: "ctrl+k", small: true }] },
      ],
    },
  ];
}

export type FlatItem = RibbonItem & { group: string };

/** Every distinct button (command + arguments), for the palette and the hotkey table. */
export function flattenRibbon(tabs: RibbonTab[]): FlatItem[] {
  const seen = new Set<string>();
  const out: FlatItem[] = [];
  for (const tab of tabs) {
    for (const group of tab.groups) {
      for (const item of group.items) {
        const key = `${item.command}:${JSON.stringify(item.args ?? {})}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ ...item, group: `${tab.title} › ${group.title}` });
      }
    }
  }
  return out;
}

/** Why the button is disabled: the command's reason first, then the button's own. */
export function itemReason(item: RibbonItem, ctx: CommandContext, commandReason: (id: string) => string | null): string | null {
  return commandReason(item.command) ?? item.unavailable?.(ctx) ?? null;
}
