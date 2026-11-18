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
  views: [{ label: "Сохранить текущий вид", command: "views.save" }],
  holes: [
    { label: "Новый набор…", command: "set.create" },
    { label: "Импорт скважин…", command: "import.open" },
    { label: "Импорт канав…", command: "import.open", args: { mode: "trenches" } },
  ],
  lines: [
    { label: "Загрузить линии и точки: STR, DXF, SHP, MIF, XYZ…", command: "lines.upload" },
    { label: "Новый слой…", command: "lines.new_layer" },
  ],
  wireframes: [{ label: "Загрузить каркасы…", command: "wireframes.upload" }],
  rasters: [{ label: "Загрузить растр…", command: "rasters.upload" }],
  blocks: [{ label: "Загрузить блочную модель…", command: "import.files" }],
  audit: [{ label: "Новый swath plot…", command: "stats.swath" }],
};

/** «+» of a section: the command it runs at once, or — without `command` — the menu of the section's actions. */
export interface SectionPlus {
  title: string;
  command?: string;
  args?: Record<string, unknown>;
}

const PLUS: Partial<Record<TreeSection, SectionPlus>> = {
  views: { command: "views.save", title: "Сохранить текущий вид" },
  holes: { title: "Новый набор, импорт скважин, импорт канав" },
  wireframes: { command: "wireframes.upload", title: "Загрузить каркасы" },
  lines: { command: "lines.upload", title: "Загрузить линии и точки: STR, DXF, SHP, MIF, XYZ" },
  rasters: { command: "rasters.upload", title: "Загрузить растр" },
  blocks: { command: "omf.import", title: "Загрузить блочную модель" },
  audit: { command: "stats.swath", title: "Новый swath plot" },
};

export const sectionActions = (s: TreeSection): SectionAction[] => ACTIONS[s] ?? [];
export const sectionPlus = (s: TreeSection): SectionPlus | undefined => PLUS[s];
