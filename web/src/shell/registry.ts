import type { MutableRefObject } from "react";
import type { AuditRun, Dataset, Hole, Project, Summary } from "../api";
import type { TableKind } from "../route";
import type { VizSettings } from "../viz/ComponentPanel";
import type { SectionLine } from "../viz/geometry";
import type { SceneMode } from "../viz/scene3d";
import type { Command } from "./commands";
import {
  BOTTOM_TITLES, MODE_TITLES, MODES, VEXAG_STEPS, isShown, layerKey, openBottom, setPanel, setShown,
  type BottomTab, type ObjectKind, type PanelId, type SelectedObject, type WorkspaceState,
} from "./workspace";

/** The live application state and actions that commands work with. */
export interface Live {
  projectId: number;
  projects: Project[];
  datasets: Dataset[];
  /** Holes, summary and audit of the active drillhole set. */
  holes: Hole[];
  summary: Summary | null;
  ws: WorkspaceState;
  viz: VizSettings;
  section: SectionLine | null;
  activeSet: number | null;
  selectedHole: string | null;
  selected: SelectedObject | null;
  setWs: (update: (prev: WorkspaceState) => WorkspaceState) => void;
  setViz: (next: VizSettings) => void;
  setSection: (next: SectionLine | null) => void;
  selectSet: (setId: number) => void;
  selectHole: (setId: number, holeId: string | null) => void;
  selectObject: (object: SelectedObject | null) => void;
  openTable: (kind: TableKind) => void;
  reload: () => void;
  onSelectProject: (id: number) => void;
  onNewProject: (name?: string) => Promise<string>;
  onDemoProject: () => Promise<string>;
  onCreateSet: (name: string) => Promise<string>;
  openPalette: (query: string) => void;
  openImport: (setId: number | null) => void;
  upload: (kind: "wireframes" | "lines" | "rasters") => void;
  patchRaster: (id: number, patch: { elevation?: number; clear_elevation?: boolean; opacity?: number }) => Promise<void>;
  runAudit: (setId: number) => Promise<AuditRun>;
  exportAudit: (setId: number) => void;
  fit: () => void;
  ask: (prompt: string) => void;
  resetChat: () => void;
}

const TABLE_TITLES: Record<TableKind, string> = { collar: "Устья", survey: "Инклинометрия", assay: "Пробы" };
const PANEL_TITLES: Record<PanelId, string> = { project: "Дерево проекта", properties: "Свойства", assistant: "Claude", bottom: "Нижняя панель" };
const LAYER_PARTS = ["set", "paths", "intervals", "collars", "wireframe", "lines", "raster"] as const;
const OBJECT_TITLES: Record<ObjectKind, string> = { wireframe: "каркас", lines: "слой линий", raster: "растр" };
type LayerPart = (typeof LAYER_PARTS)[number];

/**
 * Every action of the application. To add a feature: add a command here, then (optionally)
 * a ribbon button in ribbonConfig.ts. Claude and MCP clients get the command automatically.
 */
export function buildCommands(live: MutableRefObject<Live>): Command[] {
  const s = () => live.current;
  const sets = () => s().datasets.filter((d) => d.kind === "drillholes");
  const wireframes = () => s().datasets.flatMap((d) => d.wireframes ?? []);
  const lineLayers = () => s().datasets.flatMap((d) => d.lines ?? []);
  const rasters = () => s().datasets.flatMap((d) => d.rasters ?? []);
  /** A tree object by kind and id, or an error Claude can read. */
  const findObject = (kind: ObjectKind, id: number): { id: number; name: string } => {
    const list = kind === "wireframe" ? wireframes() : kind === "lines" ? lineLayers() : rasters();
    const found = list.find((x) => x.id === id);
    if (!found) throw new Error(`Объекта «${OBJECT_TITLES[kind]}» с id ${id} нет в проекте`);
    return found;
  };
  /** The set a command works on: the given one, or the active one. */
  const targetSet = (value: unknown): Dataset => {
    const id = typeof value === "number" ? value : s().activeSet;
    const set = sets().find((d) => d.id === id);
    if (!set) throw new Error(id === null ? "Нет активного набора скважин: укажите set_id" : `Набора скважин ${id} нет в проекте`);
    return set;
  };

  return [
    {
      id: "scene.mode",
      title: "Режим вида",
      description:
        "Главное окно одно — 3D-сцена со всеми объектами проекта. Режимы: 3d — свободный обзор, plan — вид сверху (север вверх), " +
        "section — вертикальный разрез по линии section.set: видно только то, что попадает в коридор.",
      params: { mode: { type: "string", enum: MODES } },
      required: ["mode"],
      run: ({ mode }) => {
        s().setWs((prev) => ({ ...prev, mode: mode as SceneMode }));
        return `Режим: ${MODE_TITLES[mode as SceneMode]}`;
      },
    },
    {
      id: "scene.fit",
      title: "Вписать",
      description: "Показывает все видимые объекты целиком в текущем режиме.",
      run: () => {
        s().fit();
        return "Вид вписан";
      },
    },
    {
      id: "scene.style",
      title: "Настройки вида",
      description: "vexag — вертикальное преувеличение (1, 2, 3, 5), grid — сетка, wireframe_opacity — непрозрачность каркасов 0.1–1.",
      params: {
        vexag: { type: "integer", enum: VEXAG_STEPS },
        grid: { type: "boolean" },
        wireframe_opacity: { type: "number" },
      },
      run: ({ vexag, grid, wireframe_opacity }) => {
        s().setWs((prev) => ({
          ...prev,
          vexag: (vexag as number | undefined) ?? prev.vexag,
          grid: (grid as boolean | undefined) ?? prev.grid,
          wireframeOpacity: typeof wireframe_opacity === "number" ? Math.min(1, Math.max(0.1, wireframe_opacity)) : prev.wireframeOpacity,
        }));
        return "Настройки вида изменены";
      },
    },
    {
      id: "layer.show",
      title: "Показать или скрыть слой",
      description:
        "Видимость объекта в 3D. layer: set — весь набор скважин, paths / intervals / collars — стволы, содержания, устья набора (id = set_id), " +
        "wireframe — каркас, lines — слой линий, raster — растр (id объекта из data_list_datasets).",
      params: {
        layer: { type: "string", enum: LAYER_PARTS },
        id: { type: "integer" },
        visible: { type: "boolean" },
      },
      required: ["layer", "id", "visible"],
      run: ({ layer, id, visible }) => {
        const part = layer as LayerPart;
        const n = id as number;
        let key: string;
        let name: string;
        if (part === "wireframe" || part === "lines" || part === "raster") {
          const object = findObject(part, n);
          key = part === "wireframe" ? layerKey.wireframe(n) : part === "lines" ? layerKey.lines(n) : layerKey.raster(n);
          name = `${OBJECT_TITLES[part]} «${object.name}»`;
        } else {
          const set = targetSet(n);
          key = part === "set" ? layerKey.set(n) : layerKey.setPart(n, part);
          name = `${part === "set" ? "набор" : part === "paths" ? "стволы набора" : part === "intervals" ? "содержания набора" : "устья набора"} «${set.name}»`;
        }
        s().setWs((prev) => setShown(prev, key, visible as boolean));
        return `${visible ? "Показан" : "Скрыт"} ${name}`;
      },
    },
    {
      id: "set.select",
      title: "Активный набор скважин",
      description: "Делает набор активным: его таблицы, аудит и список скважин открываются в панелях.",
      params: { set_id: { type: "integer" } },
      required: ["set_id"],
      run: ({ set_id }) => {
        const set = targetSet(set_id);
        s().selectSet(set.id);
        return `Активный набор: ${set.name}`;
      },
    },
    {
      id: "hole.select",
      title: "Выбрать скважину",
      description: "Выделяет скважину в 3D, таблицах и панели свойств. hole_id = null снимает выбор. set_id — набор (по умолчанию активный).",
      params: {
        hole_id: { type: "string", nullable: true, description: "Точный номер скважины" },
        set_id: { type: "integer" },
      },
      required: ["hole_id"],
      run: ({ hole_id, set_id }) => {
        const set = targetSet(set_id);
        if (hole_id === null) {
          s().selectHole(set.id, null);
          return "Выбор снят";
        }
        if (set.id === s().activeSet) {
          const hole = s().holes.find((h) => h.hole_id.toLowerCase() === String(hole_id).toLowerCase());
          if (!hole) throw new Error(`Скважины «${hole_id}» нет в наборе «${set.name}»`);
          s().selectHole(set.id, hole.hole_id);
          return `Выбрана скважина ${hole.hole_id} (${set.name})`;
        }
        s().selectHole(set.id, String(hole_id));
        return `Выбрана скважина ${hole_id} (${set.name})`;
      },
    },
    {
      id: "object.select",
      title: "Выбрать объект",
      description:
        "Показывает свойства объекта дерева: wireframe — каркас (площадь, объём, замкнутость), lines — слой линий (число, длина), " +
        "raster — растр (привязка, отклонения опорных точек, высота). id = null снимает выбор.",
      params: { kind: { type: "string", enum: ["wireframe", "lines", "raster"] }, id: { type: "integer", nullable: true } },
      required: ["kind", "id"],
      run: ({ kind, id }) => {
        if (id === null) {
          s().selectObject(null);
          return "Выбор снят";
        }
        const object = findObject(kind as ObjectKind, id as number);
        s().selectObject({ kind: kind as ObjectKind, id: object.id });
        s().setWs((prev) => setPanel(prev, "properties", true));
        return `Выбран ${OBJECT_TITLES[kind as ObjectKind]} «${object.name}»`;
      },
    },
    {
      id: "raster.set",
      title: "Высота и прозрачность растра",
      description: "elevation — высота плоскости растра, м (null — под самым низким объектом сцены); opacity — непрозрачность 0.05–1.",
      params: { raster_id: { type: "integer" }, elevation: { type: "number", nullable: true }, opacity: { type: "number" } },
      required: ["raster_id"],
      run: async ({ raster_id, elevation, opacity }) => {
        const raster = findObject("raster", raster_id as number);
        const patch: { elevation?: number; clear_elevation?: boolean; opacity?: number } = {};
        if (elevation === null) patch.clear_elevation = true;
        else if (typeof elevation === "number") patch.elevation = elevation;
        if (typeof opacity === "number") patch.opacity = Math.min(1, Math.max(0.05, opacity));
        await s().patchRaster(raster.id, patch);
        return `Растр «${raster.name}»: ${elevation === null ? "высота авто" : typeof elevation === "number" ? `высота ${elevation} м` : ""}${typeof opacity === "number" ? `, непрозрачность ${patch.opacity}` : ""}`;
      },
    },
    {
      id: "table.open",
      title: "Открыть таблицу",
      description: "Таблица набора скважин в нижней панели: collar — устья, survey — инклинометрия, assay — пробы. При выбранной скважине — только её строки.",
      params: { kind: { type: "string", enum: Object.keys(TABLE_TITLES) }, set_id: { type: "integer" } },
      required: ["kind"],
      run: ({ kind, set_id }) => {
        const set = targetSet(set_id);
        if (set.id !== s().activeSet) s().selectSet(set.id);
        s().openTable(kind as TableKind);
        return `Таблица «${TABLE_TITLES[kind as TableKind]}» набора «${set.name}»`;
      },
    },
    {
      id: "panel.set",
      title: "Панель",
      description:
        "Открывает или закрывает панель: project — дерево проекта слева, properties — свойства справа, assistant — чат Claude, " +
        "bottom — нижняя панель (tab: table, audit или log). Без open переключает.",
      params: {
        panel: { type: "string", enum: Object.keys(PANEL_TITLES) },
        open: { type: "boolean" },
        tab: { type: "string", enum: Object.keys(BOTTOM_TITLES) },
      },
      required: ["panel"],
      run: ({ panel, open, tab }) => {
        s().setWs((prev) => (tab && panel === "bottom" ? openBottom(prev, tab as BottomTab) : setPanel(prev, panel as PanelId, open as boolean | undefined)));
        return `Панель «${PANEL_TITLES[panel as PanelId]}»${tab ? ` · ${BOTTOM_TITLES[tab as BottomTab]}` : ""}`;
      },
    },
    {
      id: "viz.set",
      title: "Раскраска по содержаниям",
      description: "Компонент и цветовая шкала содержаний для всех наборов скважин. min/max = null — авто-диапазон (5–95 перцентиль).",
      params: {
        component: { type: "string", description: "Имя компонента, например AU" },
        min: { type: "number", nullable: true },
        max: { type: "number", nullable: true },
        log: { type: "boolean", description: "Логарифмическая шкала" },
      },
      run: (args) => {
        const next = { ...s().viz };
        if (typeof args.component === "string") {
          const all = sets().flatMap((d) => d.components ?? []);
          const found = all.find((c) => c.name.toLowerCase() === (args.component as string).toLowerCase());
          if (!found) throw new Error(`Компонента «${args.component}» нет ни в одном наборе`);
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
      description:
        "Линия вертикального разреза: начало (x0, y0) в координатах проекта, азимут az по часовой от севера, half_width — полуширина коридора, м, " +
        "length — длина профиля (null — без ограничения). С open = true сразу включает режим section.",
      params: {
        x0: { type: "number" },
        y0: { type: "number" },
        az: { type: "number", description: "0–360" },
        length: { type: "number", nullable: true },
        half_width: { type: "number", description: "По умолчанию 25" },
        open: { type: "boolean" },
      },
      required: ["x0", "y0", "az"],
      run: ({ x0, y0, az, length, half_width, open }) => {
        const halfWidth = Math.max(1, (half_width as number | undefined) ?? s().section?.halfWidth ?? 25);
        s().setSection({ x0: x0 as number, y0: y0 as number, az: (((az as number) % 360) + 360) % 360, length: (length as number | null | undefined) ?? null, halfWidth });
        if (open) s().setWs((prev) => ({ ...prev, mode: "section" }));
        return `Разрез: азимут ${az}°, коридор ±${halfWidth} м`;
      },
    },
    {
      id: "section.step",
      title: "Следующий профиль",
      description: "Сдвигает линию разреза поперёк на ширину коридора: direction = 1 — вправо от линии, -1 — влево.",
      params: { direction: { type: "integer", enum: [1, -1] } },
      required: ["direction"],
      run: ({ direction }) => {
        const sec = s().section;
        if (!sec) throw new Error("Линия разреза не задана");
        const d = sec.halfWidth * 2 * (direction as number);
        const a = (sec.az * Math.PI) / 180;
        s().setSection({ ...sec, x0: sec.x0 + Math.cos(a) * d, y0: sec.y0 - Math.sin(a) * d });
        return `Разрез сдвинут на ${Math.abs(d)} м`;
      },
    },
    {
      id: "section.clear",
      title: "Сбросить разрез",
      description: "Удаляет линию разреза; в режиме section она будет выбрана заново поперёк простирания устьев.",
      run: () => {
        s().setSection(null);
        return "Линия разреза сброшена";
      },
    },
    {
      id: "audit.run",
      title: "Запустить аудит",
      description: "Запускает 27 правил проверки набора скважин (по умолчанию активного) и заменяет его прошлый результат аудита.",
      params: { set_id: { type: "integer" } },
      confirm: true,
      run: async ({ set_id }) => {
        const set = targetSet(set_id);
        const run = await s().runAudit(set.id);
        s().reload();
        const { error, warning, info } = run.by_severity;
        return `Аудит «${set.name}»: критично ${error}, предупреждений ${warning}, информация ${info}`;
      },
    },
    {
      id: "audit.export",
      title: "Отчёт аудита в Excel",
      description: "Скачивает отчёт аудита активного набора.",
      uiOnly: true,
      run: () => {
        s().exportAudit(targetSet(undefined).id);
        return "Отчёт открыт для скачивания";
      },
    },
    {
      id: "set.create",
      title: "Новый набор скважин",
      description: "Создаёт пустой набор скважин; данные в него загружаются через импорт.",
      params: { name: { type: "string" } },
      required: ["name"],
      confirm: true,
      run: ({ name }) => s().onCreateSet(String(name)),
    },
    {
      id: "import.open",
      title: "Импорт скважин",
      description: "Открывает мастер импорта таблиц Collar, Survey, Assay.",
      uiOnly: true,
      run: ({ set_id }) => s().openImport(typeof set_id === "number" ? set_id : null),
    },
    {
      id: "wireframes.upload",
      title: "Загрузить каркасы",
      description: "Выбор файлов Micromine TRIDB или DXF.",
      uiOnly: true,
      run: () => s().upload("wireframes"),
    },
    {
      id: "lines.upload",
      title: "Загрузить линии",
      description: "Выбор файлов Micromine STR, Surpac STR или DXF.",
      uiOnly: true,
      run: () => s().upload("lines"),
    },
    {
      id: "rasters.upload",
      title: "Загрузить растры",
      description: "Выбор изображений вместе с файлами привязки.",
      uiOnly: true,
      run: () => s().upload("rasters"),
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
      description: "Что сейчас на экране: проект, режим вида, видимые объекты, активный набор, выбранная скважина или каркас, шкала, разрез.",
      silent: true,
      run: () => {
        const { ws, projects, projectId, activeSet, selectedHole, selected, viz, section } = s();
        return JSON.stringify({
          project: projects.find((p) => p.id === projectId),
          mode: ws.mode,
          drillhole_sets: sets().map((d) => ({ id: d.id, name: d.name, shown: isShown(ws, layerKey.set(d.id)), active: d.id === activeSet, collars: d.counts?.collar ?? 0 })),
          wireframes: wireframes().map((w) => ({ id: w.id, name: w.name, shown: isShown(ws, layerKey.wireframe(w.id)) })),
          lines: lineLayers().map((l) => ({ id: l.id, name: l.name, lines: l.line_count, shown: isShown(ws, layerKey.lines(l.id)) })),
          rasters: rasters().map((r) => ({ id: r.id, name: r.name, elevation: r.elevation, shown: isShown(ws, layerKey.raster(r.id)) })),
          selected_hole: selectedHole,
          selected_object: selected,
          color_scale: viz,
          section,
          vertical_exaggeration: ws.vexag,
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
      id: "project.demo",
      title: "Демо-проект",
      description: "Создаёт учебный проект «Демо: участок Северный» (48 скважин, пробы AU и CU, аудит, каркасы рудного тела, контуры, геологическая карта) и открывает его. Если он уже есть — просто открывает.",
      confirm: true,
      run: () => s().onDemoProject(),
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
