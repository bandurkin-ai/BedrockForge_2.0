import { useState } from "react";
import { bridge } from "../../bridge";
import { api, CoreError } from "../../shell/api";
import { useApp } from "../../shell/AppContext";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import { setPanel } from "../../shell/workspace";
import { refresh } from "../jobs/state";
import { exportFileName, projectStore, SUMMARY_TITLES } from "./state";
import css from "./Projects.module.css";

/** «Экспорт проекта» (ТЗ 7.4): composition, file («Сохранить как»), password (AES-256), view settings → job. */
export function ExportWindow({ winId }: { winId: string }) {
  const app = useApp();
  const current = useStore(projectStore, (s) => s.current);
  const [path, setPath] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [view, setView] = useState(true);
  const [error, setError] = useState<string | null>(null);
  if (!current) return <div className={css.wrap}>Проект не открыт.</div>;

  const mismatch = password !== repeat;
  const choose = async () => {
    const p = await bridge()?.pickSave(exportFileName(current.name));
    if (p) setPath(p);
  };
  const start = async () => {
    if (!path || mismatch) return;
    try {
      await api.post("/projects/export", { path, password: password || null, include_view: view });
      app.store.set((s) => ({ workspace: setPanel(s.workspace, "bottom", true, "jobs") }));
      await refresh();
      closeWindow(winId);
    } catch (e) {
      setError(e instanceof CoreError ? e.full : String(e));
    }
  };
  const counts = SUMMARY_TITLES.filter(([k]) => (current.summary[k] ?? 0) > 0);

  return (
    <div className={css.wrap}>
      <section className={css.section}>
        <h3>Состав</h3>
        <div className={css.title}>{current.name}</div>
        <div className={css.counts}>
          {counts.length ? (
            counts.map(([k, t]) => (
              <span key={k}>
                {t}:<b>{current.summary[k]}</b>
              </span>
            ))
          ) : (
            <span className={css.note}>Проект пуст: в файл войдут название и описание.</span>
          )}
        </div>
      </section>
      <label className={css.row}>
        <span className={css.label}>Файл</span>
        <span style={{ display: "flex", gap: 8, minWidth: 0 }}>
          <input className={css.input} readOnly value={path ?? ""} placeholder={exportFileName(current.name)} aria-label="Файл экспорта" />
          <button type="button" className={css.btn} onClick={() => void choose()}>
            Сохранить как…
          </button>
        </span>
      </label>
      <label className={css.row}>
        <span className={css.label}>Пароль</span>
        <input className={css.input} type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} aria-label="Пароль" />
      </label>
      {password && (
        <label className={css.row}>
          <span className={css.label}>Повтор пароля</span>
          <input className={css.input} type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} aria-label="Повтор пароля" />
        </label>
      )}
      <div className={css.note}>С паролем файл шифруется AES-256; пароль нигде не сохраняется — без него файл не открыть.</div>
      <label className={css.choice}>
        <span>
          <input type="checkbox" checked={view} onChange={(e) => setView(e.target.checked)} /> Настройки вида (видимость слоёв, стили, порядок дерева)
        </span>
      </label>
      {password && mismatch && <div className={css.error}>Пароли не совпадают.</div>}
      {error && <div className={css.error}>{error}</div>}
      <div className={css.footer}>
        <button type="button" className={`${css.btn} ${css.primary}`} disabled={!path || mismatch} onClick={() => void start()}>
          Экспортировать
        </button>
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Отмена
        </button>
      </div>
    </div>
  );
}
