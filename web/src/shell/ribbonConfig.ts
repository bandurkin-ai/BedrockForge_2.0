import type { IconName } from "./icons";
import { LAYOUT_TITLES, VIEW_TITLES, type Layout, type ViewKind, type WorkspaceState } from "./workspace";

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

const VIEW_ICONS: Record<ViewKind, IconName> = {
  view3d: "cube", plan: "plan", section: "section", log: "log", table: "table", audit: "audit", import: "import",
};
const VIEW_SHORTCUTS: Partial<Record<ViewKind, string>> = { view3d: "3", plan: "p", section: "s", log: "l", table: "t" };

export const QUICK_PROMPTS: { label: string; prompt: string; needsHole?: boolean }[] = [
  { label: "Сводка по проекту", prompt: "Дай краткую сводку по проекту: объём данных, компоненты, состояние аудита и что стоит проверить в первую очередь." },
  { label: "Разобрать аудит", prompt: "Разбери результаты аудита: какие проблемы самые серьёзные, в каких скважинах они сосредоточены и с чего начать исправление." },
  { label: "Проверить скважину", prompt: "Проверь выбранную скважину: данные устья, инклинометрию, опробование и замечания аудита. Покажи её на плане и в колонке.", needsHole: true },
];

export function buildRibbon(ws: WorkspaceState, ctx: { hasHole: boolean; hasData: boolean }): RibbonTab[] {
  const activeView = ws.panes[ws.active];
  const view = (kind: ViewKind, small = false): RibbonItem => ({
    command: "view.open",
    args: { view: kind },
    label: VIEW_TITLES[kind],
    icon: VIEW_ICONS[kind],
    hint: `Показать «${VIEW_TITLES[kind]}» в активном окне`,
    shortcut: VIEW_SHORTCUTS[kind],
    active: activeView === kind,
    small,
  });
  const layout = (kind: Layout): RibbonItem => ({
    command: "layout.set",
    args: { layout: kind },
    label: LAYOUT_TITLES[kind],
    icon: kind,
    active: ws.layout === kind,
    small: true,
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
            { command: "view.open", args: { view: "import" }, label: "Импорт", icon: "import", hint: "Загрузить файл Micromine DAT", active: activeView === "import" },
            { command: "data.refresh", label: "Обновить", icon: "refresh", hint: "Перечитать данные проекта с сервера", small: true },
          ],
        },
        { title: "Виды", items: [view("view3d"), view("plan"), view("section"), view("log")] },
        {
          title: "Таблицы",
          items: [
            { command: "table.open", args: { kind: "collar" }, label: "Устья", icon: "table", small: true },
            { command: "table.open", args: { kind: "survey" }, label: "Инклинометрия", icon: "table", small: true },
            { command: "table.open", args: { kind: "assay" }, label: "Пробы", icon: "table", small: true },
          ],
        },
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
        { title: "Окно", items: [view("view3d"), view("plan"), view("section"), view("log"), view("table", true), view("audit", true), view("import", true)] },
        { title: "Компоновка", items: [layout("single"), layout("columns"), layout("rows"), layout("quad")] },
        {
          title: "Панели",
          items: [
            { command: "panel.set", args: { panel: "project" }, label: "Проект", icon: "panelLeft", shortcut: "ctrl+b", active: ws.left, small: true },
            { command: "panel.set", args: { panel: "properties" }, label: "Свойства", icon: "properties", active: ws.right && ws.rightTab === "properties", small: true },
            { command: "panel.set", args: { panel: "bottom" }, label: "Нижняя панель", icon: "panelBottom", shortcut: "ctrl+`", active: ws.bottom, small: true },
          ],
        },
        {
          title: "Оформление",
          items: [
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
            { command: "audit.run", label: "Запустить аудит", icon: "play", hint: "27 правил по устьям, инклинометрии и пробам", disabled: !ctx.hasData },
            { command: "view.open", args: { view: "audit" }, label: "Результаты", icon: "audit", active: activeView === "audit" },
            { command: "audit.export", label: "Отчёт Excel", icon: "export", small: true, disabled: !ctx.hasData },
          ],
        },
        {
          title: "Разрез",
          items: [
            { command: "view.open", args: { view: "plan" }, label: "Линия на плане", icon: "plan", hint: "Откройте план и задайте линию разреза двумя кликами", small: true },
            { command: "section.clear", label: "Сбросить разрез", icon: "clear", small: true },
          ],
        },
        {
          title: "Журнал",
          items: [{ command: "panel.set", args: { panel: "bottom", tab: "log" }, label: "Журнал команд", icon: "list", small: true }],
        },
      ],
    },
    {
      id: "claude",
      title: "Claude",
      groups: [
        {
          title: "Диалог",
          items: [assistant, { command: "assistant.new", label: "Новый диалог", icon: "chat" }],
        },
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
        {
          title: "Команды",
          items: [{ command: "palette.open", label: "Все команды", icon: "command", shortcut: "ctrl+k", small: true }],
        },
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
