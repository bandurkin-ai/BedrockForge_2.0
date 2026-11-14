import type { MutableRefObject } from "react";
import type { AuditRun, Hole, Project, Summary } from "../api";
import type { NavigateOptions, Route, TableKind } from "../route";
import type { VizSettings } from "../viz/ComponentPanel";
import type { SectionLine } from "../viz/geometry";
import type { Command } from "./commands";
import {
  LAYOUT_TITLES, PANE_COUNT, setLayout, setPanel, VIEW_KINDS, VIEW_TITLES,
  type BottomTab, type Layout, type PanelId, type ViewKind, type WorkspaceState,
} from "./workspace";

/** The live application state and actions that commands work with. */
export interface Live {
  projectId: number;
  projects: Project[];
  holes: Hole[];
  summary: Summary | null;
  ws: WorkspaceState;
  viz: VizSettings;
  section: SectionLine | null;
  selectedHole: string | null;
  setWs: (update: (prev: WorkspaceState) => WorkspaceState) => void;
  setViz: (next: VizSettings) => void;
  setSection: (next: SectionLine | null) => void;
  setSelectedHole: (holeId: string | null) => void;
  openView: (view: ViewKind, pane?: number) => void;
  navigate: (next: Partial<Route>, options?: NavigateOptions) => void;
  reload: () => void;
  onSelectProject: (id: number) => void;
  onNewProject: (name?: string) => Promise<string>;
  openPalette: (query: string) => void;
  runAudit: () => Promise<AuditRun>;
  exportAudit: () => void;
  ask: (prompt: string) => void;
  resetChat: () => void;
}

const TABLE_TITLES: Record<TableKind, string> = { collar: "Устья", survey: "Инклинометрия", assay: "Пробы" };
const PANEL_TITLES: Record<PanelId, string> = { project: "Проект", properties: "Свойства", assistant: "Claude", bottom: "Нижняя панель" };

/**
 * Every action of the application. To add a feature: add a command here, then (optionally)
 * a ribbon button in ribbon.ts. Claude and MCP clients get the command automatically.
 */
export function buildCommands(live: MutableRefObject<Live>): Command[] {
  const s = () => live.current;
  return [
    {
      id: "view.open",
      title: "Открыть вид",
      description:
        "Показывает вид в окне рабочей области: view3d — 3D-модель скважин, plan — план устьев, section — вертикальный разрез, " +
        "log — колонка выбранной скважины, table — таблица данных, audit — результаты аудита, import — импорт файлов. " +
        "Без window вид открывается в активном окне.",
      params: {
        view: { type: "string", enum: VIEW_KINDS },
        window: { type: "integer", enum: [1, 2, 3, 4], description: "Номер окна; должно быть видно в текущей компоновке" },
      },
      required: ["view"],
      run: ({ view, window }) => {
        const pane = typeof window === "number" ? window - 1 : undefined;
        if (pane !== undefined && pane >= PANE_COUNT[s().ws.layout]) {
          throw new Error(`Окно ${window} не показано: в компоновке «${LAYOUT_TITLES[s().ws.layout]}» окон ${PANE_COUNT[s().ws.layout]}`);
        }
        s().openView(view as ViewKind, pane);
        return `Открыт вид «${VIEW_TITLES[view as ViewKind]}»${pane !== undefined ? ` в окне ${pane + 1}` : ""}`;
      },
    },
    {
      id: "layout.set",
      title: "Компоновка окон",
      description: "Делит рабочую область: single — одно окно, columns — два рядом, rows — два друг над другом, quad — четыре. Удобно сравнивать план, разрез и 3D.",
      params: { layout: { type: "string", enum: Object.keys(LAYOUT_TITLES) } },
      required: ["layout"],
      run: ({ layout }) => {
        s().setWs((prev) => setLayout(prev, layout as Layout));
        return `Компоновка: ${LAYOUT_TITLES[layout as Layout]}`;
      },
    },
    {
      id: "panel.set",
      title: "Панель",
      description: "Открывает или закрывает панель: project — дерево проекта слева, properties — свойства справа, assistant — чат Claude справа, bottom — нижняя панель (tab: table или log). Без open переключает.",
      params: {
        panel: { type: "string", enum: Object.keys(PANEL_TITLES) },
        open: { type: "boolean" },
        tab: { type: "string", enum: ["table", "log"], description: "Вкладка нижней панели" },
      },
      required: ["panel"],
      run: ({ panel, open, tab }) => {
        const wanted = tab ? true : (open as boolean | undefined);
        s().setWs((prev) => {
          const next = setPanel(prev, panel as PanelId, wanted);
          return tab && panel === "bottom" ? { ...next, bottomTab: tab as BottomTab } : next;
        });
        return `Панель «${PANEL_TITLES[panel as PanelId]}»`;
      },
    },
    {
      id: "hole.select",
      title: "Выбрать скважину",
      description: "Выделяет скважину во всех видах, таблицах и панели свойств; колонка показывает именно её. hole_id = null снимает выбор.",
      params: { hole_id: { type: "string", nullable: true, description: "Точный номер скважины из проекта" } },
      required: ["hole_id"],
      run: ({ hole_id }) => {
        if (hole_id === null) {
          s().setSelectedHole(null);
          return "Выбор снят";
        }
        const hole = s().holes.find((h) => h.hole_id.toLowerCase() === String(hole_id).toLowerCase());
        if (!hole) throw new Error(`Скважины «${hole_id}» нет в проекте`);
        s().setSelectedHole(hole.hole_id);
        return `Выбрана скважина ${hole.hole_id}`;
      },
    },
    {
      id: "table.open",
      title: "Открыть таблицу",
      description: "Показывает таблицу в нижней панели: collar — устья, survey — инклинометрия, assay — пробы. При выбранной скважине таблица отфильтрована по ней.",
      params: { kind: { type: "string", enum: Object.keys(TABLE_TITLES) } },
      required: ["kind"],
      run: ({ kind }) => {
        s().navigate({ kind: kind as TableKind }, { replace: true });
        s().setWs((prev) => ({ ...prev, bottom: true, bottomTab: "table" }));
        return `Таблица «${TABLE_TITLES[kind as TableKind]}»`;
      },
    },
    {
      id: "viz.set",
      title: "Раскраска по содержаниям",
      description: "Задаёт компонент и цветовую шкалу для разреза, колонки и 3D. min/max = null возвращают авто-диапазон (5–95 перцентиль).",
      params: {
        component: { type: "string", description: "Имя компонента проекта, например AU" },
        min: { type: "number", nullable: true },
        max: { type: "number", nullable: true },
        log: { type: "boolean", description: "Логарифмическая шкала" },
      },
      run: (args) => {
        const next = { ...s().viz };
        if (typeof args.component === "string") {
          const found = s().summary?.components.find((c) => c.name.toLowerCase() === (args.component as string).toLowerCase());
          if (!found) throw new Error(`Компонента «${args.component}» нет в проекте`);
          next.component = found.name;
        }
        if ("min" in args) next.min = args.min as number | null;
        if ("max" in args) next.max = args.max as number | null;
        if (typeof args.log === "boolean") next.log = args.log;
        s().setViz(next);
        return `Шкала: ${next.component ?? "первый компонент"}, ${next.min ?? "авто"} – ${next.max ?? "авто"}${next.log ? ", логарифмическая" : ""}`;
      },
    },
    {
      id: "section.set",
      title: "Задать разрез",
      description: "Задаёт линию вертикального разреза: начало (x0, y0) в координатах проекта, азимут az в градусах по часовой от севера, половина ширины коридора half_width в метрах. Затем откройте вид section.",
      params: {
        x0: { type: "number" },
        y0: { type: "number" },
        az: { type: "number", description: "0–360" },
        length: { type: "number", nullable: true, description: "Длина профиля, м; null — без ограничения" },
        half_width: { type: "number", description: "По умолчанию 25" },
      },
      required: ["x0", "y0", "az"],
      run: ({ x0, y0, az, length, half_width }) => {
        const halfWidth = Math.max(1, (half_width as number | undefined) ?? s().section?.halfWidth ?? 25);
        s().setSection({ x0: x0 as number, y0: y0 as number, az: (((az as number) % 360) + 360) % 360, length: (length as number | null | undefined) ?? null, halfWidth });
        return `Разрез: азимут ${az}°, коридор ±${halfWidth} м`;
      },
    },
    {
      id: "section.clear",
      title: "Сбросить разрез",
      description: "Удаляет заданную линию разреза; вид разреза вернётся к автоматическому профилю.",
      run: () => {
        s().setSection(null);
        return "Линия разреза сброшена";
      },
    },
    {
      id: "audit.run",
      title: "Запустить аудит",
      description: "Запускает 27 правил проверки данных с порогами по умолчанию и заменяет предыдущий результат аудита проекта.",
      confirm: true,
      run: async () => {
        const run = await s().runAudit();
        s().reload();
        const { error, warning, info } = run.by_severity;
        return `Аудит выполнен: критично ${error}, предупреждений ${warning}, информация ${info}`;
      },
    },
    {
      id: "audit.export",
      title: "Отчёт аудита в Excel",
      description: "Скачивает отчёт последнего аудита.",
      uiOnly: true,
      run: () => {
        s().exportAudit();
        return "Отчёт открыт для скачивания";
      },
    },
    {
      id: "data.refresh",
      title: "Обновить данные",
      description: "Перечитывает данные проекта с сервера (после импорта или внешних изменений).",
      run: () => {
        s().reload();
        return "Данные обновлены";
      },
    },
    {
      id: "workspace.state",
      title: "Состояние интерфейса",
      description: "Возвращает, что сейчас открыто: проект, компоновка, виды в окнах, выбранная скважина, шкала, разрез.",
      silent: true,
      run: () => {
        const { ws, projects, projectId, selectedHole, viz, section, summary } = s();
        return JSON.stringify({
          project: projects.find((p) => p.id === projectId),
          counts: summary?.counts ?? null,
          components: summary?.components ?? [],
          selected_hole: selectedHole,
          layout: ws.layout,
          windows: ws.panes.slice(0, PANE_COUNT[ws.layout]).map((view, i) => ({ window: i + 1, view, active: i === ws.active })),
          color_scale: viz,
          section,
          theme: ws.theme,
        });
      },
    },
    {
      id: "theme.set",
      title: "Тема оформления",
      description: "Переключает тёмную и светлую тему; без theme — на противоположную.",
      params: { theme: { type: "string", enum: ["dark", "light"] } },
      run: ({ theme }) => {
        const next = (theme as "dark" | "light" | undefined) ?? (s().ws.theme === "dark" ? "light" : "dark");
        s().setWs((prev) => ({ ...prev, theme: next }));
        return next === "dark" ? "Тёмная тема" : "Светлая тема";
      },
    },
    {
      id: "project.new",
      title: "Новый проект",
      description: "Создаёт пустой проект с заданным названием и открывает его.",
      params: { name: { type: "string" } },
      required: ["name"],
      confirm: true,
      run: ({ name }) => s().onNewProject(name as string | undefined),
    },
    {
      id: "ribbon.toggle",
      title: "Свернуть ленту",
      description: "Сворачивает или разворачивает ленту инструментов.",
      uiOnly: true,
      silent: true,
      run: () => s().setWs((prev) => ({ ...prev, ribbonCollapsed: !prev.ribbonCollapsed })),
    },
    {
      id: "palette.open",
      title: "Поиск команд",
      description: "Открывает палитру команд.",
      uiOnly: true,
      silent: true,
      run: ({ query }) => s().openPalette(typeof query === "string" ? query : ""),
    },
    {
      id: "assistant.ask",
      title: "Запрос к Claude",
      description: "Отправляет готовый запрос ассистенту.",
      uiOnly: true,
      silent: true,
      run: ({ prompt }) => {
        s().setWs((prev) => setPanel(prev, "assistant", true));
        s().ask(String(prompt));
      },
    },
    {
      id: "assistant.new",
      title: "Новый диалог с Claude",
      description: "Очищает чат и начинает новый диалог.",
      uiOnly: true,
      silent: true,
      run: () => {
        s().setWs((prev) => setPanel(prev, "assistant", true));
        s().resetChat();
      },
    },
  ];
}
