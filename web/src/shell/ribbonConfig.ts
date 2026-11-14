import type { SceneMode } from "../viz/scene3d";
import type { IconName } from "./icons";
import { MODE_TITLES, type WorkspaceState } from "./workspace";

/** A button: a registry command with preset arguments. The palette and hotkeys use the same list. */
export interface RibbonItem {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint?: string;
  shortcut?: string;
  small?: boolean;
  active?: boolean;
  disabled?: boolean;
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

const MODE_ICONS: Record<SceneMode, IconName> = { "3d": "cube", plan: "plan", section: "section" };
const MODE_SHORTCUTS: Record<SceneMode, string> = { "3d": "3", plan: "p", section: "s" };
const MODE_HINTS: Record<SceneMode, string> = {
  "3d": "Свободный обзор всех объектов",
  plan: "Вид сверху; здесь же задаётся линия разреза",
  section: "Вертикальный разрез: видно только то, что попадает в коридор",
};

export const QUICK_PROMPTS: { label: string; prompt: string; needsHole?: boolean }[] = [
  { label: "Сводка по проекту", prompt: "Дай краткую сводку по проекту: наборы скважин и каркасы, объём данных, компоненты, состояние аудита и что стоит проверить в первую очередь." },
  { label: "Разобрать аудит", prompt: "Разбери результаты аудита активного набора: какие проблемы самые серьёзные, в каких скважинах они сосредоточены и с чего начать исправление." },
  { label: "Проверить скважину", prompt: "Проверь выбранную скважину: данные устья, инклинометрию, опробование и замечания аудита. Покажи её на разрезе.", needsHole: true },
];

export function buildRibbon(ws: WorkspaceState, ctx: { hasHole: boolean; hasSet: boolean; hasSection: boolean }): RibbonTab[] {
  const mode = (m: SceneMode, small = false): RibbonItem => ({
    command: "scene.mode",
    args: { mode: m },
    label: MODE_TITLES[m],
    icon: MODE_ICONS[m],
    hint: MODE_HINTS[m],
    shortcut: MODE_SHORTCUTS[m],
    active: ws.mode === m,
    small,
  });
  const bottom = (tab: "table" | "audit" | "log", label: string, icon: IconName): RibbonItem => ({
    command: "panel.set",
    args: { panel: "bottom", tab },
    label,
    icon,
    small: true,
    active: ws.bottom && ws.bottomTab === tab,
  });
  const assistant: RibbonItem = {
    command: "panel.set",
    args: { panel: "assistant" },
    label: "Claude",
    icon: "spark",
    hint: "Панель ассистента Claude",
    shortcut: "ctrl+j",
    active: ws.right && ws.rightTab === "assistant",
  };

  return [
    {
      id: "home",
      title: "Главная",
      groups: [
        {
          title: "Проект",
          items: [
            { command: "project.new", label: "Новый проект", icon: "plus" },
            { command: "project.demo", label: "Демо-проект", icon: "cube", hint: "Учебный проект: 48 скважин, пробы, аудит, каркасы рудного тела", small: true },
            { command: "data.refresh", label: "Обновить", icon: "refresh", hint: "Перечитать данные проекта с сервера", small: true },
          ],
        },
        {
          title: "Загрузка",
          items: [
            { command: "import.open", label: "Скважины", icon: "drillholes", hint: "Импорт Collar, Survey, Assay в набор скважин (DAT, CSV, Excel)" },
            { command: "wireframes.upload", label: "Каркасы", icon: "wireframe", hint: "Micromine TRIDB или DXF" },
            { command: "lines.upload", label: "Линии", icon: "lines", hint: "Стринги: Micromine STR, Surpac STR, DXF", small: true },
            { command: "rasters.upload", label: "Растры", icon: "raster", hint: "Изображение с файлом привязки (.jgw/.pgw/.tfw, .tab, .grf) или GeoTIFF", small: true },
          ],
        },
        { title: "Вид", items: [mode("3d"), mode("plan"), mode("section"), { command: "scene.fit", label: "Вписать", icon: "maximize", shortcut: "f", small: true }] },
        {
          title: "Выделение",
          items: [
            { command: "palette.open", args: { query: "скв " }, label: "Найти скважину", icon: "search", hint: "Поиск скважины по номеру", small: true },
            { command: "hole.select", args: { hole_id: null }, label: "Снять выбор", icon: "clear", shortcut: "escape", disabled: !ctx.hasHole, small: true },
          ],
        },
        { title: "Ассистент", items: [assistant] },
      ],
    },
    {
      id: "view",
      title: "Вид",
      groups: [
        { title: "Режим", items: [mode("3d"), mode("plan"), mode("section")] },
        {
          title: "Разрез",
          items: [
            { command: "section.step", args: { direction: -1 }, label: "Предыдущий профиль", icon: "chevronUp", shortcut: "[", small: true, disabled: !ctx.hasSection },
            { command: "section.step", args: { direction: 1 }, label: "Следующий профиль", icon: "chevronDown", shortcut: "]", small: true, disabled: !ctx.hasSection },
            { command: "section.clear", label: "Сбросить линию", icon: "clear", small: true, disabled: !ctx.hasSection },
          ],
        },
        {
          title: "Панели",
          items: [
            { command: "panel.set", args: { panel: "project" }, label: "Дерево проекта", icon: "panelLeft", shortcut: "ctrl+b", active: ws.left, small: true },
            { command: "panel.set", args: { panel: "properties" }, label: "Свойства", icon: "properties", active: ws.right && ws.rightTab === "properties", small: true },
            { command: "panel.set", args: { panel: "bottom" }, label: "Нижняя панель", icon: "panelBottom", shortcut: "ctrl+`", active: ws.bottom, small: true },
          ],
        },
        {
          title: "Оформление",
          items: [
            { command: "scene.style", args: { grid: !ws.grid }, label: ws.grid ? "Скрыть сетку" : "Показать сетку", icon: "quad", small: true },
            { command: "theme.set", label: ws.theme === "dark" ? "Светлая тема" : "Тёмная тема", icon: "theme", small: true },
            { command: "ribbon.toggle", label: "Свернуть ленту", icon: "chevronUp", shortcut: "ctrl+f1", small: true },
          ],
        },
      ],
    },
    {
      id: "analysis",
      title: "Анализ",
      groups: [
        {
          title: "Аудит данных",
          items: [
            { command: "audit.run", label: "Запустить аудит", icon: "play", hint: "27 правил по устьям, инклинометрии и пробам активного набора", disabled: !ctx.hasSet },
            { command: "panel.set", args: { panel: "bottom", tab: "audit" }, label: "Результаты", icon: "audit", active: ws.bottom && ws.bottomTab === "audit", disabled: !ctx.hasSet },
            { command: "audit.export", label: "Отчёт Excel", icon: "export", small: true, disabled: !ctx.hasSet },
          ],
        },
        {
          title: "Таблицы набора",
          items: [
            { command: "table.open", args: { kind: "collar" }, label: "Устья", icon: "table", small: true, disabled: !ctx.hasSet },
            { command: "table.open", args: { kind: "survey" }, label: "Инклинометрия", icon: "table", small: true, disabled: !ctx.hasSet },
            { command: "table.open", args: { kind: "assay" }, label: "Пробы", icon: "table", small: true, disabled: !ctx.hasSet },
          ],
        },
        { title: "Журнал", items: [bottom("log", "Журнал команд", "list")] },
      ],
    },
    {
      id: "claude",
      title: "Claude",
      groups: [
        { title: "Диалог", items: [assistant, { command: "assistant.new", label: "Новый диалог", icon: "chat" }] },
        {
          title: "Быстрые запросы",
          items: QUICK_PROMPTS.map((q) => ({
            command: "assistant.ask",
            args: { prompt: q.prompt },
            label: q.label,
            icon: "spark" as const,
            small: true,
            disabled: q.needsHole && !ctx.hasHole,
          })),
        },
        { title: "Команды", items: [{ command: "palette.open", label: "Все команды", icon: "command", shortcut: "ctrl+k", small: true }] },
      ],
    },
  ];
}

/** Every distinct button, for the palette and the hotkey table. */
export function flattenRibbon(tabs: RibbonTab[]): (RibbonItem & { group: string })[] {
  const seen = new Set<string>();
  const out: (RibbonItem & { group: string })[] = [];
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
