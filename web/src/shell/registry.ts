import type { MutableRefObject } from "react";
import type { AuditRun, Dataset, Hole, JobInfo, Project, RasterPatch, Summary, VerticalPlacement } from "../api";
import type { TableKind } from "../route";
import { ANALYSES, type StatsAnalysis } from "../stats/analyses";
import type { VizSettings } from "../viz/ComponentPanel";
import { DEFAULT_WINDOW, shiftSection, type SectionLine } from "../viz/geometry";
import type { Command } from "./commands";
import {
  BOTTOM_TITLES, VEXAG_STEPS, VIEW_PRESETS, VIEW_TITLES, isShown, layerKey, openBottom, setPanel, setShown,
  type BottomTab, type ObjectKind, type PanelId, type SelectedObject, type ViewPreset, type WorkspaceState,
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
  openStats: (analysis: StatsAnalysis, component: string | null) => void;
  reload: () => void;
  onSelectProject: (id: number) => void;
  onNewProject: (name?: string) => Promise<string>;
  onDemoProject: () => Promise<string>;
  onDeleteProject: (id: number) => Promise<string>;
  moveDataset: (id: number, folder: string) => Promise<void>;
  /** Background jobs of the project, newest first. */
  jobs: JobInfo[];
  cancelJob: (id: number) => Promise<void>;
  onCreateSet: (name: string) => Promise<string>;
  openPalette: (query: string) => void;
  openImport: (setId: number | null) => void;
  upload: (kind: "wireframes" | "lines" | "rasters") => void;
  patchRaster: (id: number, patch: RasterPatch) => Promise<void>;
  sectionFrame: () => VerticalPlacement | null;
  sectionThroughHole: (setId: number, holeId: string) => SectionLine | null;
  runAudit: (setId: number) => Promise<AuditRun>;
  exportAudit: (setId: number) => void;
  fit: () => void;
  view: (preset: ViewPreset) => void;
  drawSection: () => void;
  openSection: (line: SectionLine) => void;
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
      id: "scene.view",
      title: "Повернуть вид",
      description:
        "Главное окно всегда 3D и свободно вращается; команда только поворачивает камеру. top — сверху (Y вверх, X вправо), " +
        "front — сбоку на север (Z вверх, X вправо), side — сбоку на запад (Z вверх, Y вправо), iso — изометрия, " +
        "section — в плоскость линии разреза (Z вверх).",
      params: { view: { type: "string", enum: VIEW_PRESETS } },
      required: ["view"],
      run: ({ view }) => {
        if (view === "section" && !s().section) throw new Error("Линия разреза не задана: сначала section.set");
        s().view(view as ViewPreset);
        return `Вид: ${VIEW_TITLES[view as ViewPreset]}`;
      },
    },
    {
      id: "scene.fit",
      title: "Вписать",
      description: "Показывает все видимые объекты целиком, не поворачивая вид.",
      run: () => {
        s().fit();
        return "Вид вписан";
      },
    },
    {
      id: "scene.style",
      title: "Настройки вида",
      description: "vexag — вертикальное преувеличение (1, 2, 3, 5), grid — сетка, wireframe_opacity — непрозрачность каркасов 0.1–1, perspective — перспектива (false — ортогональная проекция).",
      params: {
        vexag: { type: "integer", enum: VEXAG_STEPS },
        grid: { type: "boolean" },
        perspective: { type: "boolean" },
        wireframe_opacity: { type: "number" },
      },
      run: ({ vexag, grid, wireframe_opacity, perspective }) => {
        s().setWs((prev) => ({
          ...prev,
          vexag: (vexag as number | undefined) ?? prev.vexag,
          grid: (grid as boolean | undefined) ?? prev.grid,
          perspective: (perspective as boolean | undefined) ?? prev.perspective,
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
      id: "stats.open",
      title: "Статистика",
      description:
        "Открывает вкладку «Статистика» нижней панели для набора скважин. analysis: declus — декластеризация, nscore — нормальные оценки, " +
        "variogram — вариограмма, map — карта вариограммы, bootstrap — бутстрап статистики, local — карта локальной неопределённости оценки. component — компонент (по умолчанию первый в наборе). " +
        "Расчёт считается по серединам интервалов в плане и запускается кнопкой «Рассчитать».",
      params: {
        analysis: { type: "string", enum: ANALYSES.map(([id]) => id) },
        component: { type: "string" },
        set_id: { type: "integer" },
      },
      required: ["analysis"],
      run: ({ analysis, component, set_id }) => {
        const set = targetSet(set_id);
        if (set.id !== s().activeSet) s().selectSet(set.id);
        const names = (s().summary?.components ?? []).map((c) => c.name);
        if (typeof component === "string" && s().summary && !names.includes(component)) {
          throw new Error(`Компонента «${component}» нет в наборе. Есть: ${names.join(", ") || "нет компонентов"}`);
        }
        s().openStats(analysis as StatsAnalysis, typeof component === "string" ? component : null);
        return `Статистика «${ANALYSES.find(([id]) => id === analysis)?.[1]}» набора «${set.name}»${component ? `, ${component}` : ""}`;
      },
    },
    {
      id: "panel.set",
      title: "Панель",
      description:
        "Открывает или закрывает панель: project — дерево проекта слева, properties — свойства справа, assistant — чат Claude, " +
        "bottom — нижняя панель (tab: table, audit, stats или log). Без open переключает.",
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
        "Линия вертикального разреза: начало (x0, y0) в координатах проекта, азимут az по часовой от севера, length — длина (null — без ограничения). " +
        "Окно отсечения в единицах карты: towards — к наблюдателю, away — от наблюдателя (наблюдатель справа от линии). " +
        "Включает отсечение и поворачивает вид в плоскость разреза, Z вверх.",
      params: {
        x0: { type: "number" },
        y0: { type: "number" },
        az: { type: "number", description: "0–360" },
        length: { type: "number", nullable: true },
        towards: { type: "number", description: `По умолчанию ${DEFAULT_WINDOW}` },
        away: { type: "number", description: `По умолчанию ${DEFAULT_WINDOW}` },
      },
      required: ["x0", "y0", "az"],
      run: ({ x0, y0, az, length, towards, away }) => {
        const previous = s().section;
        const line: SectionLine = {
          x0: x0 as number, y0: y0 as number, az: (((az as number) % 360) + 360) % 360,
          length: (length as number | null | undefined) ?? null,
          towards: Math.max(0, (towards as number | undefined) ?? previous?.towards ?? DEFAULT_WINDOW),
          away: Math.max(0, (away as number | undefined) ?? previous?.away ?? DEFAULT_WINDOW),
        };
        s().setSection(line);
        s().setWs((prev) => ({ ...prev, clip: true }));
        s().openSection(line);
        return `Разрез: азимут ${line.az}°, окно от ${line.towards} до ${line.away}`;
      },
    },
    {
      id: "section.through_hole",
      title: "Разрез по скважине",
      description:
        "Линия разреза через устье скважины вдоль её направления в плане (азимут от устья к забою), так что вся скважина " +
        "лежит в плоскости разреза. У вертикальной скважины азимут остаётся прежним. Включает отсечение и поворачивает вид в плоскость.",
      params: { hole_id: { type: "string" }, set_id: { type: "integer" } },
      required: ["hole_id"],
      run: ({ hole_id, set_id }) => {
        const set = targetSet(set_id);
        const hole = set.id === s().activeSet ? s().holes.find((h) => h.hole_id.toLowerCase() === String(hole_id).toLowerCase())?.hole_id : String(hole_id);
        if (!hole) throw new Error(`Скважины «${hole_id}» нет в наборе «${set.name}»`);
        const line = s().sectionThroughHole(set.id, hole);
        if (!line) throw new Error(`Скважина ${hole} не показана в 3D или у неё нет координат устья`);
        s().selectHole(set.id, hole);
        s().setSection(line);
        s().setWs((prev) => ({ ...prev, clip: true }));
        s().openSection(line);
        return `Разрез по скважине ${hole}: азимут ${Math.round(line.az * 10) / 10}°, окно от ${line.towards} до ${line.away}`;
      },
    },
    {
      id: "raster.place_on_section",
      title: "Растр в плоскость разреза",
      description:
        "Ставит растр (например, скан разреза) вертикально в плоскость текущей линии разреза: края — по линии в пределах данных, " +
        "верх и низ — по высотам данных. Можно задать свои края и высоты: x1, y1 (левый край), x2, y2 (правый край), z_top, z_bottom.",
      params: {
        raster_id: { type: "integer" },
        x1: { type: "number" }, y1: { type: "number" }, x2: { type: "number" }, y2: { type: "number" },
        z_top: { type: "number" }, z_bottom: { type: "number" },
      },
      required: ["raster_id"],
      confirm: true,
      run: async (args) => {
        const raster = findObject("raster", args.raster_id as number);
        const frame = s().sectionFrame();
        const keys = ["x1", "y1", "x2", "y2", "z_top", "z_bottom"] as const;
        const given = keys.every((k) => typeof args[k] === "number");
        if (!frame && !given) throw new Error("Линия разреза не задана: проведите разрез или передайте x1, y1, x2, y2, z_top, z_bottom");
        const placement = Object.fromEntries(keys.map((k) => [k, typeof args[k] === "number" ? args[k] : frame![k]])) as unknown as VerticalPlacement;
        await s().patchRaster(raster.id, { vertical: placement });
        return `Растр «${raster.name}» стоит вертикально: от (${Math.round(placement.x1)}, ${Math.round(placement.y1)}) до (${Math.round(placement.x2)}, ${Math.round(placement.y2)}), Z ${Math.round(placement.z_top)}…${Math.round(placement.z_bottom)}`;
      },
    },
    {
      id: "section.window",
      title: "Окно разреза",
      description: "Меняет окно отсечения у текущей линии разреза (в единицах карты): towards — к наблюдателю, away — от наблюдателя; clip — включить или выключить отсечение.",
      params: { towards: { type: "number" }, away: { type: "number" }, clip: { type: "boolean" } },
      run: ({ towards, away, clip }) => {
        const sec = s().section;
        if (!sec) throw new Error("Линия разреза не задана");
        const next = { ...sec, towards: Math.max(0, (towards as number | undefined) ?? sec.towards), away: Math.max(0, (away as number | undefined) ?? sec.away) };
        s().setSection(next);
        if (typeof clip === "boolean") s().setWs((prev) => ({ ...prev, clip }));
        return `Окно разреза: от ${next.towards} до ${next.away}${typeof clip === "boolean" ? `, отсечение ${clip ? "включено" : "выключено"}` : ""}`;
      },
    },
    {
      id: "section.clip",
      title: "Отсечение по разрезу",
      description: "Включает или выключает отсечение сцены окном разреза (без on — переключает). Вид не поворачивается.",
      params: { on: { type: "boolean" } },
      run: ({ on }) => {
        if (!s().section) throw new Error("Линия разреза не задана");
        const next = (on as boolean | undefined) ?? !s().ws.clip;
        s().setWs((prev) => ({ ...prev, clip: next }));
        return next ? "Отсечение включено" : "Отсечение выключено";
      },
    },
    {
      id: "section.draw",
      title: "Провести разрез",
      description: "Инструмент: два клика на виде сверху задают линию разреза.",
      uiOnly: true,
      silent: true,
      run: () => s().drawSection(),
    },
    {
      id: "section.step",
      title: "Следующий профиль",
      description: "Сдвигает линию разреза поперёк на ширину окна: direction = 1 — от наблюдателя, -1 — к наблюдателю.",
      params: { direction: { type: "integer", enum: [1, -1] } },
      required: ["direction"],
      run: ({ direction }) => {
        const sec = s().section;
        if (!sec) throw new Error("Линия разреза не задана");
        const d = (sec.towards + sec.away) * (direction as number);
        s().setSection(shiftSection(sec, -d));
        return `Разрез сдвинут на ${Math.abs(d)} ${d > 0 ? "от наблюдателя" : "к наблюдателю"}`;
      },
    },
    {
      id: "section.clear",
      title: "Сбросить разрез",
      description: "Удаляет линию разреза и выключает отсечение.",
      run: () => {
        s().setSection(null);
        s().setWs((prev) => ({ ...prev, clip: false }));
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
      description: "Что сейчас на экране: проект, отсечение и проекция, видимые объекты, активный набор, выбранная скважина или каркас, шкала, разрез.",
      silent: true,
      run: () => {
        const { ws, projects, projectId, activeSet, selectedHole, selected, viz, section } = s();
        return JSON.stringify({
          project: projects.find((p) => p.id === projectId),
          clip: ws.clip && section !== null,
          perspective: ws.perspective,
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
      id: "project.delete",
      title: "Удалить проект",
      description: "Удаляет проект со всеми данными (наборы скважин, каркасы, линии, растры, задания). Без project_id — текущий. Пользователь подтверждает удаление в окне.",
      params: { project_id: { type: "integer" } },
      confirm: true,
      run: ({ project_id }) => s().onDeleteProject(typeof project_id === "number" ? project_id : s().projectId),
    },
    {
      id: "dataset.move",
      title: "Переместить в папку",
      description: "Кладёт объект дерева (набор скважин, файл каркасов, линий, растр) в папку внутри его группы. folder — путь через «/», пустая строка — в корень группы.",
      params: { dataset_id: { type: "integer" }, folder: { type: "string" } },
      required: ["dataset_id", "folder"],
      confirm: true,
      run: async ({ dataset_id, folder }) => {
        const d = s().datasets.find((x) => x.id === dataset_id);
        if (!d) throw new Error(`Объекта с id ${dataset_id} нет в проекте`);
        await s().moveDataset(d.id, String(folder));
        return String(folder).trim() ? `«${d.name}» → папка «${String(folder).trim()}»` : `«${d.name}» → корень группы`;
      },
    },
    {
      id: "jobs.list",
      title: "Фоновые задания",
      description: "Показывает фоновые задания проекта (импорт файлов и расчёты): состояние, ход, ошибки. Открывает вкладку «Задания».",
      run: () => {
        s().setWs((prev) => openBottom(prev, "jobs"));
        const list = s().jobs.slice(0, 20);
        if (list.length === 0) return "Заданий нет";
        return list
          .map((j) => `#${j.id} ${j.title || j.kind}: ${j.status}${j.status === "running" ? ` ${Math.round(j.progress * 100)}%` : ""}${j.error ? ` — ${j.error}` : ""}`)
          .join("\n");
      },
    },
    {
      id: "jobs.cancel",
      title: "Отменить задание",
      description: "Останавливает фоновое задание: в очереди — сразу, выполняющееся — на ближайшем шаге; его частичный результат не сохраняется.",
      params: { job_id: { type: "integer" } },
      required: ["job_id"],
      confirm: true,
      run: async ({ job_id }) => {
        const j = s().jobs.find((x) => x.id === job_id);
        if (!j) throw new Error(`Задания ${job_id} нет`);
        await s().cancelJob(j.id);
        return `Задание ${j.id} отменяется`;
      },
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
