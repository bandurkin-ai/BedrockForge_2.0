/**
 * The window «Блочная модель · <модель>» (ТЗ 8.7.6; ADR 0023 §11, §13): its kind, its title and the tabs. One window per
 * model; the model is the property `model` of the window. The style it edits is not here — it is the view of the project
 * (`block_styles`, `display.ts`), so a reopened project, the commands `blocks.style`, `blocks.quick`, `blocks.filter`
 * and the window always show the same. No React.
 */
export const MODEL_WINDOW = "blocks-model";

export const modelTitle = (name: string | null): string => (name ? `Блочная модель · ${name}` : "Блочная модель");

export type Tab = "show" | "filter" | "quick" | "legend" | "props";

export const TABS: readonly { id: Tab; title: string }[] = [
  { id: "show", title: "Показ" },
  { id: "filter", title: "Фильтр" },
  { id: "quick", title: "Быстрый ограничитель" },
  { id: "legend", title: "Легенда" },
  { id: "props", title: "Свойства" },
];

export const isTab = (v: unknown): v is Tab => TABS.some((t) => t.id === v);

/** The tab a key of the tab list goes to (←/→ round the list, Home / End the ends); null — not a key of the list. */
export function tabByKey(from: Tab, key: string): Tab | null {
  const i = TABS.findIndex((t) => t.id === from);
  const last = TABS.length - 1;
  if (key === "ArrowRight") return TABS[i >= last ? 0 : i + 1].id;
  if (key === "ArrowLeft") return TABS[i <= 0 ? last : i - 1].id;
  if (key === "Home") return TABS[0].id;
  if (key === "End") return TABS[last].id;
  return null;
}
