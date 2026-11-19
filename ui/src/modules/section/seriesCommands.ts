/**
 * The commands of series of sections (ТЗ 8.1; ADR 0016 §4) and the ribbon tab «Разрезы» with a button for each:
 * «Серия» — Менеджер серий, Новая серия, Из линии разреза, Через скважину, Из одного разреза; «Навигация» —
 * Первый, Предыдущий (`[`), Следующий (`]`), Последний, Перейти; «Вид» — Зафиксировать вид (`L`), Отсечение,
 * Тень; «Оцифровка» — Контур на разрезе, Каркас по серии; «Печать» — Печать серии (Э22, unavailable with a
 * reason). `Home` / `End` / `Ctrl+G` work while the 3D window has the focus (`seriesBind.ts`), not as ribbon keys.
 * Commands that change the project (create, copy, delete) are confirmed when Claude runs them.
 */
import type { Command } from "../../shell/commands";
import type { RibbonTab } from "../../shell/ribbonConfig";
import type { ViewState } from "../../shell/workspace";
import { lockCommand } from "./lockView";

const id = { series_id: { type: "integer" as const, description: "Номер объекта серии (id из списка серий)" } };
const shadow = { type: "string" as const, enum: ["none", "prev", "next", "both"] };

export const PRINT_REASON = "Печать серии — этап Э22";

export const seriesCommands: Command[] = [
  { id: "series.manager", title: "Менеджер серий", description: "Окно «Менеджер серий»: список серий разрезов проекта, параметры, таблица разрезов со своими окнами, линии серии на плане.", uiOnly: true },
  { id: "series.new", title: "Новая серия", description: "Окно «Новая серия» с параметрами по линии разреза и видимым данным (вертикальная, план, наклонная, ломаная).", uiOnly: true },
  { id: "series.from_line", title: "Серия из линии разреза", description: "Окно «Новая серия» по текущей линии разреза: вертикальная серия из одного разреза на этой линии (пределы можно расширить).", uiOnly: true },
  { id: "series.through_hole", title: "Серия через скважину", description: "Окно «Новая серия» с разрезом через устье по азимуту скважины в плане. Без hole_id — выбранная скважина.", params: { hole_id: { type: "string" }, set_id: { type: "integer" } }, uiOnly: true },
  { id: "series.from_section", title: "Серия из одного разреза", description: "Окно «Новая серия»: текущий разрез активной серии (или линия разреза) — серией из одного разреза.", uiOnly: true },
  {
    id: "series.create",
    title: "Создать серию разрезов",
    description:
      "Создать серию разрезов и сделать её активной. type: vertical (x1, y1 и az или x2, y2), plan (x1, y1, top, bottom — верхняя и нижняя отметки, шаг по Z), inclined (x1, y1, z1, az или x2, y2, phi — наклон 0–90°), polyline (polyline — вершины «X; Y» по строкам, corridor). s0, s1 — пределы вдоль нормали, step — шаг Δ, towards / away — окно к себе / от себя, м (по умолчанию 25 / 25, шаг — ширина окна). template — шаблон имён ({k}, {s}, {X}, {Y}, {Z}).",
    params: {
      type: { type: "string", enum: ["vertical", "plan", "inclined", "polyline"] }, name: { type: "string" },
      x1: { type: "number" }, y1: { type: "number" }, z1: { type: "number" }, x2: { type: "number" }, y2: { type: "number" }, az: { type: "number" }, phi: { type: "number" },
      s0: { type: "number" }, s1: { type: "number" }, step: { type: "number" }, towards: { type: "number" }, away: { type: "number" }, top: { type: "number" }, bottom: { type: "number" },
      polyline: { type: "string" }, corridor: { type: "number" }, template: { type: "string" }, shadow,
    },
    required: ["type"],
    confirm: true,
  },
  { id: "series.copy", title: "Копировать серию", description: "Копия серии под новым номером (те же параметры).", params: id, required: ["series_id"], confirm: true },
  { id: "series.delete", title: "Удалить серию", description: "Удалить серию разрезов; контуры, оцифрованные на её разрезах, остаются.", params: id, required: ["series_id"], confirm: true },
  { id: "series.activate", title: "Сделать серию активной", description: "Показать разрез серии: k — номер разреза, как в интерфейсе (с 1; у ломаного — сегмент); без k — первый. Включает отсечение по окну разреза.", params: { ...id, k: { type: "integer" } }, required: ["series_id"] },
  { id: "series.first", title: "Первый разрез", description: "Первый разрез активной серии (Home при фокусе 3D-окна)." },
  { id: "series.prev", title: "Предыдущий разрез", description: "Предыдущий разрез активной серии (у ломаного — сегмент); без серии — линия разреза на ширину окна назад." },
  { id: "series.next", title: "Следующий разрез", description: "Следующий разрез активной серии (у ломаного — сегмент); без серии — линия разреза на ширину окна вперёд." },
  { id: "series.last", title: "Последний разрез", description: "Последний разрез активной серии (End при фокусе 3D-окна)." },
  { id: "series.goto", title: "Перейти к разрезу", description: "Разрез активной серии по имени (например «5050N») или номеру с 1; без них — окно ввода с подсказками (Ctrl+G).", params: { name: { type: "string" }, number: { type: "integer" } } },
  lockCommand,
  { id: "series.shadow", title: "Тень", description: "Тень активной серии — контуры соседних разрезов: none (нет), prev (предыдущий), next (следующий), both (оба).", params: { mode: shadow }, required: ["mode"] },
  { id: "series.digitize", title: "Контур на разрезе", description: "Оцифровка контура на текущем разрезе серии.", stage: "Э10" },
  { id: "series.stitch", title: "Каркас по серии", description: "Окно «Каркас по серии»: связи контуров соседних разрезов и построение каркаса.", stage: "Э10" },
  { id: "series.print", title: "Печать серии", description: "Пакетная печать разрезов серии по макету (этап Э22)." },
];

/** The ribbon tab «Разрезы» (ТЗ 8.1.5): a button for every command of series. */
export function seriesTab(view: Pick<ViewState, "clip">, locked = false): RibbonTab {
  return {
    id: "sections",
    title: "Разрезы",
    groups: [
      {
        title: "Серия",
        items: [
          { command: "series.manager", label: "Менеджер серий", icon: "list", hint: "Серии проекта: параметры, таблица разрезов, свои окна" },
          { command: "series.new", label: "Новая серия", icon: "plus", hint: "Вертикальная, план, наклонная или ломаная серия разрезов" },
          { command: "series.from_line", label: "Из линии разреза", icon: "vwSection", small: true },
          { command: "series.through_hole", label: "Через скважину", icon: "vwHole", small: true },
          { command: "series.from_section", label: "Из одного разреза", icon: "section", small: true },
        ],
      },
      {
        title: "Навигация",
        items: [
          { command: "series.first", label: "Первый", icon: "chevronUp", hint: "Первый разрез серии — Home при фокусе 3D-окна", small: true },
          { command: "series.prev", label: "Предыдущий", icon: "vwPrev", hint: "Предыдущий разрез; без серии — линия разреза на ширину окна", shortcut: "[" },
          { command: "series.next", label: "Следующий", icon: "vwNext", hint: "Следующий разрез; без серии — линия разреза на ширину окна", shortcut: "]" },
          { command: "series.last", label: "Последний", icon: "chevronDown", hint: "Последний разрез серии — End при фокусе 3D-окна", small: true },
          { command: "series.goto", label: "Перейти…", icon: "search", hint: "Разрез по имени или номеру — Ctrl+G при фокусе 3D-окна", small: true },
        ],
      },
      {
        title: "Вид",
        items: [
          { command: "series.lock", label: "Зафиксировать вид", icon: "vwToPlane", hint: "Камера по нормали к разрезу, без вращения", shortcut: "l", active: locked },
          { command: "section.clip", label: "Отсечение", icon: "vwClip", small: true, active: view.clip },
          { command: "series.shadow", args: { mode: "prev" }, label: "Тень", icon: "copy", hint: "Контуры предыдущего разреза серым пунктиром", small: true },
        ],
      },
      {
        title: "Оцифровка",
        items: [
          { command: "series.digitize", label: "Контур на разрезе", icon: "edPolygon" },
          { command: "series.stitch", label: "Каркас по серии", icon: "wireframe" },
        ],
      },
      { title: "Печать", items: [{ command: "series.print", label: "Печать серии", icon: "export" }] },
    ],
  };
}
