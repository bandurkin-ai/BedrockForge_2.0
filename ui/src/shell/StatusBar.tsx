import { useApp } from "./AppContext";
import { auditStore, statusText } from "../modules/audit/state";
import { useStore } from "./useStore";
import css from "./StatusBar.module.css";

const CORE_TEXT = { starting: "Запуск ядра…", online: "Ядро работает", offline: "Нет связи с ядром" } as const;

/** Status bar (ТЗ 7.0.5): core · project · set · selection · view · Claude · audit. */
export function StatusBar() {
  const app = useApp();
  const s = useStore(app.store, (x) => x);
  const audit = useStore(auditStore, (x) => x.result);
  const nf = new Intl.NumberFormat("ru-RU");
  const parts = [
    <span key="core" data-testid="core-status">
      <i className={`${css.dot} ${css[s.core]}`} />
      {CORE_TEXT[s.core]}
    </span>,
    <span key="project">Проект: {s.projectName ?? "—"}</span>,
    <span key="set">Набор: {s.setName ? `${s.setName} (${nf.format(s.setHoles)} скв.)` : "—"}</span>,
  ];
  if (s.holeId) parts.push(<span key="hole">Выбрана: <b>{s.holeId}</b></span>);
  parts.push(<span key="view">Вид: {s.view.perspective ? "перспектива" : "ортогональная"}</span>);
  if (s.statusNote)
    parts.push(
      <span key="note" role="status" title="Закрыть" onClick={() => app.store.set({ statusNote: null })}>
        {s.statusNote}
      </span>,
    );
  return (
    <footer className={css.bar}>
      {parts.flatMap((p, i) => (i ? [<span key={`s${i}`} className={css.sep}>·</span>, p] : [p]))}
      <span className={css.right}>
        <button type="button" className={css.link} onClick={() => void app.run("panel.set", { panel: "assistant", open: true })}>
          Claude: не подключён
        </button>
        <span className={css.sep}>·</span>
        <button type="button" className={css.link} onClick={() => void app.run("panel.set", { panel: "bottom", tab: "audit", open: true })}>
          {statusText(audit)}
        </button>
      </span>
    </footer>
  );
}
