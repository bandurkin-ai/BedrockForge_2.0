import type { Command } from "../../shell/commands";

/** Panels, themes, palette, settings: implemented on Э1 (handlers are bound by the shell). */
export const commands: Command[] = [
  {
    id: "panel.set",
    title: "Панель",
    description: "Открыть или закрыть панель: дерево (project), «Свойства» (properties), Claude (assistant), нижнюю (bottom, вкладка tab). Повторный вызов для открытой вкладки закрывает док.",
    params: {
      panel: { type: "string", enum: ["project", "properties", "assistant", "bottom"] },
      open: { type: "boolean", description: "true — открыть, false — закрыть; без параметра — переключить" },
      tab: { type: "string", enum: ["table", "audit", "stats", "qaqc", "jobs", "log"], description: "Вкладка нижней панели" },
    },
    required: ["panel"],
  },
  { id: "theme.set", title: "Тема", description: "Сменить тему оформления: тёмная или светлая; без параметра — переключить.", params: { theme: { type: "string", enum: ["dark", "light"] } } },
  { id: "ribbon.toggle", title: "Свернуть ленту", description: "Свернуть или развернуть ленту.", uiOnly: true, silent: true },
  { id: "palette.open", title: "Все команды", description: "Палитра команд Ctrl+K.", params: { query: { type: "string" } }, uiOnly: true, silent: true },
  {
    id: "settings.open",
    title: "Настройка",
    description: "Окно настроек: быстрые клавиши (shortcuts), папка проектов и число исполнителей (general), внешние программы — путь к ODA File Converter для чертежей DWG (tools).",
    params: { tab: { type: "string", enum: ["shortcuts", "general", "tools"], description: "Вкладка окна" } },
    uiOnly: true,
  },
  { id: "workspace.state", title: "Что на экране", description: "Короткая сводка состояния окна: открытые панели, тема, вид, проект, набор, выбор.", silent: true },
  { id: "selection.clear", title: "Снять выбор", description: "Снять выбор скважины и объекта проекта (слоя линий, растра, каркаса, ЦТМ); пока рисуется линия разреза — отменить рисование.", stage: "Э7" },
  { id: "app.about", title: "О производителе", description: "Плашка «О производителе»: название, слоган, производитель, версии программы и ядра.", uiOnly: true, stage: "Э6.1" },
  { id: "license.open", title: "Лицензия", description: "Окно «Лицензия»: код ПК, пробный период, файл лицензии.", uiOnly: true },
  { id: "logs.open", title: "Журналы", description: "Журналы импорта и экспорта этого сеанса.", uiOnly: true, stage: "Э3" },
  { id: "logs.last", title: "Последний журнал", description: "Текст последнего журнала операции (index — номер с конца).", params: { index: { type: "integer" } }, stage: "Э3" },
];
