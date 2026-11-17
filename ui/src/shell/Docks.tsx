import { useApp } from "./AppContext";
import { AuditPanel } from "../modules/audit/AuditPanel";
import { JobsPanel } from "../modules/jobs/JobsPanel";
import { HoleCard } from "../modules/tables/HoleCard";
import { TablePanel } from "../modules/tables/TablePanel";
import { CommandLog } from "./CommandLog";
import { Icon } from "./Icon";
import { useStore } from "./useStore";
import { BOTTOM_TITLES, type BottomTab } from "./workspace";
import logoMark from "../assets/logo-mark.png";
import css from "./Shell.module.css";

const STAGE_TEXT: Partial<Record<BottomTab, string>> = {
  stats: "Статистика появится на этапе Э13.",
  swath: "Swath plot появится на этапе Э13.",
};

export function BottomDock() {
  const app = useApp();
  const tab = useStore(app.store, (s) => s.workspace.bottomTab);
  const height = useStore(app.store, (s) => s.workspace.sizes.bottom);
  return (
    <section className={css.bottom} style={{ height }} aria-label="Нижняя панель">
      <div className={css.dockTabs} role="tablist">
        {(Object.keys(BOTTOM_TITLES) as BottomTab[]).map((t) => (
          <button key={t} type="button" role="tab" aria-selected={t === tab} className={`${css.dockTab} ${t === tab ? css.dockTabActive : ""}`} onClick={() => void app.run("panel.set", { panel: "bottom", tab: t, open: true })}>
            {BOTTOM_TITLES[t]}
          </button>
        ))}
        <button type="button" className={css.dockClose} title="Закрыть нижнюю панель (Ctrl+Ё)" aria-label="Закрыть нижнюю панель" onClick={() => void app.run("panel.set", { panel: "bottom", open: false })}>
          <Icon name="close" size={14} />
        </button>
      </div>
      <div className={css.dockBody}>{tab === "log" ? <CommandLog /> : tab === "jobs" ? <JobsPanel /> : tab === "table" ? <TablePanel /> : tab === "audit" ? <AuditPanel /> : <div className={css.empty}>{STAGE_TEXT[tab]}</div>}</div>
    </section>
  );
}

export function RightDock() {
  const app = useApp();
  const tab = useStore(app.store, (s) => s.workspace.rightTab);
  const width = useStore(app.store, (s) => s.workspace.sizes.right);
  return (
    <aside className={css.right} style={{ width }} aria-label="Правая панель">
      <div className={css.dockTabs} role="tablist">
        <button type="button" role="tab" aria-selected={tab === "properties"} className={`${css.dockTab} ${tab === "properties" ? css.dockTabActive : ""}`} onClick={() => void app.run("panel.set", { panel: "properties", open: true })}>
          Свойства
        </button>
        <button type="button" role="tab" aria-selected={tab === "assistant"} className={`${css.dockTab} ${tab === "assistant" ? css.dockTabActive : ""}`} onClick={() => void app.run("panel.set", { panel: "assistant", open: true })}>
          Claude
        </button>
        <button type="button" className={css.dockClose} title="Закрыть панель" aria-label="Закрыть правую панель" onClick={() => app.store.set((s) => ({ workspace: { ...s.workspace, right: false } }))}>
          <Icon name="close" size={14} />
        </button>
      </div>
      <div className={css.dockBody}>
        {tab === "properties" ? (
          <HoleCard />
        ) : (
          <div className={css.empty}>Ассистент Claude появится на этапе Э23. Без интернета ассистент недоступен, остальное работает.</div>
        )}
      </div>
    </aside>
  );
}

export function Viewport() {
  return (
    <main className={css.viewport} aria-label="Главное окно 3D">
      <div className={css.viewportBrand}>
        <img src={logoMark} alt="" className={css.viewportMark} />
        <strong>
          Bedrock<span className={css.forge}>Forge</span>
        </strong>
        Главное 3D-окно появится на этапе Э6
      </div>
    </main>
  );
}
