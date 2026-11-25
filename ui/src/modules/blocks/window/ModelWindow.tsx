/**
 * The window «Блочная модель · <модель>» (ТЗ 8.7.6; ADR 0023 §11, §13, decisions 16–19): five tabs — «Показ» (the mode, the
 * colouring, the scale, the breaks, the categories, the palette, the edges, the opacity, the limit of faces, «упрощено, LOD»),
 * «Фильтр» (the library, «Фильтры…», «Сохранить быстрый ограничитель как фильтр»), «Быстрый ограничитель» (conditions and, or,
 * inverse), «Легенда» (what the colours mean now) and «Свойства» (the grid, the turn, the sub-blocks, the fields, the checks,
 * the block clicked). Everything applies at once and is the style of the model in the view of the project (`block_styles`):
 * it is kept with the project and is there when the project is opened again. Floating, not modal; the command is
 * `blocks.settings` (`bind.ts`).
 */
import { useState, type KeyboardEvent } from "react";
import { blocksStore } from "../state";
import { useStore } from "./hooks";
import { FilterPanel } from "./FilterPanel";
import { LegendPanel } from "./LegendPanel";
import { PropsPanel } from "./PropsPanel";
import { QuickPanel } from "./QuickPanel";
import { StylePanel } from "./StylePanel";
import mod from "./Window.module.css";
import { TABS, tabByKey, type Tab } from "./state";

export function ModelWindow({ winId, modelId, tab: first = "show" }: { winId: string; modelId: number; tab?: Tab }) {
  const [tab, setTab] = useState<Tab>(first);
  const gone = useStore(blocksStore, (s) => s.loaded && !s.list.some((b) => b.id === modelId));
  if (gone) return <p className={mod.gone}>Блочной модели №{modelId} больше нет в проекте. Закройте окно.</p>;
  // roving tabindex: only the chosen tab is in the Tab order, the arrows move between the tabs
  const onKey = (e: KeyboardEvent<HTMLButtonElement>, from: Tab) => {
    const to = tabByKey(from, e.key);
    if (!to) return;
    e.preventDefault();
    setTab(to);
    document.getElementById(`model-tab-${modelId}-${to}`)?.focus();
  };
  return (
    <div className={mod.window} data-testid="model-window" data-model={modelId} data-win={winId} data-tab={tab}>
      <div className={mod.tabs} role="tablist" aria-label="Разделы окна блочной модели">
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" id={`model-tab-${modelId}-${t.id}`} aria-selected={tab === t.id} tabIndex={tab === t.id ? 0 : -1} onKeyDown={(e) => onKey(e, t.id)} aria-controls={`model-pane-${modelId}`} className={mod.tab} data-testid={`model-tab-${t.id}`} onClick={() => setTab(t.id)}>
            {t.title}
          </button>
        ))}
      </div>
      <div className={mod.body} role="tabpanel" id={`model-pane-${modelId}`} aria-labelledby={`model-tab-${modelId}-${tab}`}>
        {tab === "show" && <StylePanel id={modelId} />}
        {tab === "filter" && <FilterPanel id={modelId} />}
        {tab === "quick" && <QuickPanel id={modelId} />}
        {tab === "legend" && <LegendPanel id={modelId} />}
        {tab === "props" && <PropsPanel id={modelId} />}
      </div>
    </div>
  );
}
