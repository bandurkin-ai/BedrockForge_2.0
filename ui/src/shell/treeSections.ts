/**
 * What the header of a section of the data tree offers (ТЗ 7.1 «+», 7.0.13): the commands of its context menu
 * and of its «+». A section with one way to add runs it by «+»; one with several opens them as a menu
 * («Скважины»: a new set, the import of holes, the import of trenches — ТЗ 7.35 Д-16). Pure.
 */
import type { TreeSection } from "./workspace";

export interface SectionAction {
  label: string;
  command: string;
  args?: Record<string, unknown>;
}

const ACTIONS: Partial<Record<TreeSection, SectionAction[]>> = {
  trends: [{ label: "Новый тренд…", command: "trend.open" }],
  variograms: [{ label: "Новое окно «Вариография»", command: "variography.open" }],
  estimation: [{ label: "Новая оценка (мастер)…", command: "estimate.open" }, { label: "План оценки…", command: "estimate.plan" }],
  simulation: [{ label: "SGS…", command: "simulate.sgs" }, { label: "SIS…", command: "simulate.sis" }, { label: "Ко-SGS…", command: "simulate.cosgs" }, { label: "Сводка реализаций…", command: "simulate.summary" }],
  planned: [{ label: "Новая проектная скважина…", command: "planned.new" }],
  implicit: [{ label: "Новая модель…", command: "implicit.new" }, { label: "Домены литологии…", command: "implicit.domains" }],
  views: [{ label: "Сохранить текущий вид", command: "views.save" }],
  series: [{ label: "Новая серия…", command: "series.new" }, { label: "Менеджер серий…", command: "series.manager" }],
  holes: [
    { label: "Новый набор…", command: "set.create" },
    { label: "Импорт скважин…", command: "import.open" },
    { label: "Импорт канав…", command: "import.open", args: { mode: "trenches" } },
  ],
  lines: [
    { label: "Загрузить линии и точки: STR, DXF, SHP, MIF, XYZ…", command: "lines.upload" },
    { label: "Загрузить облако точек: LAS, XYZ…", command: "points.import", args: { mode: "las" } },
    { label: "Новый слой…", command: "lines.new_layer" },
  ],
  wireframes: [
    { label: "Загрузить каркасы…", command: "wireframes.upload" },
    { label: "Экспорт каркасов…", command: "wireframe.export", args: { section: "wireframes" } },
  ],
  dtm: [{ label: "Экспорт ЦТМ…", command: "wireframe.export", args: { section: "dtm" } }],
  rasters: [{ label: "Загрузить растр…", command: "rasters.upload" }],
  blocks: [{ label: "Загрузить блочную модель…", command: "import.files", args: { kind: "blocks" } }],
  filters: [{ label: "Новый фильтр…", command: "filter.save" }],
  audit: [
    { label: "Новый swath plot…", command: "stats.swath" },
    { label: "Загрузить контрольные пробы QAQC…", command: "qaqc.import" },
    { label: "Настройка QAQC…", command: "qaqc.setup", args: { tab: "data" } },
  ],
  reports: [
    { label: "Сформировать отчёт QAQC…", command: "qaqc.open", args: { section: "report" } },
    { label: "Отчёт по ресурсам…", command: "resources.report" },
  ],
};

/** «+» of a section: the command it runs at once, or — without `command` — the menu of the section's actions. */
export interface SectionPlus {
  title: string;
  command?: string;
  args?: Record<string, unknown>;
}

const PLUS: Partial<Record<TreeSection, SectionPlus>> = {
  trends: { command: "trend.open", title: "Новый тренд" },
  variograms: { command: "variography.open", title: "Новое окно «Вариография»" },
  estimation: { command: "estimate.open", title: "Новая оценка (мастер)" },
  simulation: { command: "simulate.open", title: "Новое моделирование (мастер)" },
  implicit: { command: "implicit.new", title: "Новая неявная модель (мастер)" },
  planned: { command: "planned.new", title: "Новая проектная скважина" },
  views: { command: "views.save", title: "Сохранить текущий вид" },
  series: { command: "series.new", title: "Новая серия разрезов" },
  holes: { title: "Новый набор, импорт скважин, импорт канав" },
  wireframes: { command: "wireframes.upload", title: "Загрузить каркасы" },
  lines: { command: "lines.upload", title: "Загрузить линии и точки: STR, DXF, SHP, MIF, XYZ" },
  rasters: { command: "rasters.upload", title: "Загрузить растр" },
  blocks: { command: "import.files", args: { kind: "blocks" }, title: "Загрузить блочную модель" },
  filters: { command: "filter.save", title: "Новый фильтр" },
  audit: { command: "stats.swath", title: "Новый swath plot" },
  reports: { command: "qaqc.open", args: { section: "report" }, title: "Сформировать отчёт QAQC" },
};

export const sectionActions = (s: TreeSection): SectionAction[] => ACTIONS[s] ?? [];
export const sectionPlus = (s: TreeSection): SectionPlus | undefined => PLUS[s];
