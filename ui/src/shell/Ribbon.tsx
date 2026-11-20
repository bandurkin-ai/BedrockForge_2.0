import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { useApp } from "./AppContext";
import { commandContext } from "./store";
import { formatShortcut } from "./keys";
import { Icon } from "./Icon";
import { buildRibbon, itemReason, type RibbonItem } from "./ribbonConfig";
import { effectiveShortcuts, functionKey } from "./shortcuts";
import { useRegistryVersion, useStore } from "./useStore";
import { seriesRibbon, seriesView } from "../modules/section/state";
import css from "./Ribbon.module.css";

export function tooltip(item: RibbonItem, shortcut: string, reason: string | null): string {
  const lines = [item.label];
  if (item.hint) lines.push(item.hint);
  if (shortcut) lines.push(`Клавиша: ${formatShortcut(shortcut)}`);
  if (reason) lines.push(`Недоступно: ${reason}`);
  return lines.join("\n");
}

/** 0 — full, 1 — big buttons become small, 2 — small buttons show only the icon (narrow windows). */
type Density = 0 | 1 | 2;

function ToolButton({ item, shortcut, reason, onRun, density }: { item: RibbonItem; shortcut: string; reason: string | null; onRun: () => void; density: Density }) {
  const small = item.small || density > 0;
  const cls = [small ? css.small : css.big, density === 2 && css.iconOnly, item.active && css.active, reason && css.disabled].filter(Boolean).join(" ");
  return (
    <button
      type="button"
      className={cls}
      title={tooltip(item, shortcut, reason)}
      aria-label={item.label}
      aria-disabled={reason ? true : undefined}
      aria-pressed={item.active ? true : undefined}
      onClick={() => !reason && onRun()}
    >
      <Icon name={item.icon} size={small ? 14 : 22} />
      <span>{item.label}</span>
    </button>
  );
}

export function Ribbon() {
  const app = useApp();
  const state = useStore(app.store, (s) => s);
  useRegistryVersion(app.registry);
  // the pressed buttons of «Разрезы» live outside `app.store`: the view locked, the surfaces, the shadow
  const surfaces = useStore(seriesView, (s) => s.surfaces);
  const { locked, shadow } = useStore(seriesRibbon, (s) => s);
  const tabs = useMemo(() => buildRibbon(state.workspace, state.view, { locked, surfaces, shadow }), [state.workspace, state.view, locked, surfaces, shadow]);
  const ctx = commandContext(state);
  const shortcuts = useMemo(() => effectiveShortcuts(tabs.flatMap((t) => t.groups.flatMap((g) => g.items)), state.shortcuts), [tabs, state.shortcuts]);
  const ws = state.workspace;
  const current = tabs.find((t) => t.id === ws.ribbonTab) ?? tabs[0];
  const bodyRef = useRef<HTMLDivElement>(null);
  // Steps of squeezing: groups from the right lose big buttons first, then labels (ТЗ 7.0.14: 1024 px).
  const [step, setStep] = useState(0);
  const [width, setWidth] = useState(0);
  const n = current.groups.length;
  const densityOf = (gi: number): Density => {
    const fromRight = n - 1 - gi;
    return (step > fromRight ? 1 : 0) + (step > n + fromRight ? 1 : 0) as Density;
  };
  useLayoutEffect(() => setStep(0), [current.id, width, ws.ribbonCollapsed]);
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (el && step < 2 * n && el.scrollWidth > el.clientWidth + 1) setStep(step + 1);
  });
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ws.ribbonCollapsed]);
  const setTab = (id: string) => app.store.set((s) => ({ workspace: { ...s.workspace, ribbonTab: id, ribbonCollapsed: false } }));

  const render = (density: Density) => (item: RibbonItem, i: number) => (
    <ToolButton
      key={`${item.command}-${i}`}
      item={item}
      shortcut={shortcuts[functionKey(item)] ?? ""}
      reason={itemReason(item, ctx, (id) => app.registry.reason(id, ctx))}
      onRun={() => void app.run(item.command, item.args ?? {})}
      density={density}
    />
  );

  return (
    <nav className={css.ribbon} aria-label="Лента">
      <div className={css.tabs} role="tablist">
        {tabs.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={t.id === current.id} className={`${css.tab} ${t.id === current.id ? css.tabActive : ""}`} onClick={() => setTab(t.id)}>
            {t.title}
          </button>
        ))}
        <button type="button" className={css.collapse} title={ws.ribbonCollapsed ? "Развернуть ленту (Ctrl+F1)" : "Свернуть ленту (Ctrl+F1)"} aria-label="Свернуть ленту" onClick={() => void app.run("ribbon.toggle")}>
          <Icon name={ws.ribbonCollapsed ? "chevronDown" : "chevronUp"} size={14} />
        </button>
      </div>
      {!ws.ribbonCollapsed && (
        <div ref={bodyRef} className={css.body} role="tabpanel">
          {current.groups.map((g, gi) => {
            const density = densityOf(gi);
            const big = density === 0 ? g.items.filter((i) => !i.small) : [];
            const small = density === 0 ? g.items.filter((i) => i.small) : g.items;
            const cols: RibbonItem[][] = [];
            for (let i = 0; i < small.length; i += 3) cols.push(small.slice(i, i + 3));
            return (
              <div key={g.title} className={css.group}>
                <div className={css.groupItems}>
                  {big.map(render(density))}
                  {cols.map((col, ci) => (
                    <div key={ci} className={css.smallCol}>
                      {col.map(render(density))}
                    </div>
                  ))}
                </div>
                <div className={css.groupTitle}>{g.title}</div>
              </div>
            );
          })}
        </div>
      )}
    </nav>
  );
}
