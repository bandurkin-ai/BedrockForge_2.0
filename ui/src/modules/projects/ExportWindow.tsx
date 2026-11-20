import { useState } from "react";
import { bridge } from "../../bridge";
import { api, CoreError } from "../../shell/api";
import { useApp } from "../../shell/AppContext";
import { useStore } from "../../shell/useStore";
import { closeWindow, windowStore } from "../../shell/windows";
import { setPanel } from "../../shell/workspace";
import { refresh } from "../jobs/state";
import { ExportObjects, type ObjectsInitial } from "./ExportObjects";
import { exportFileName, projectStore, SUMMARY_TITLES } from "./state";
import css from "./Projects.module.css";

export type ExportMode = "bfggis" | "objects";

/**
 * «Экспорт проекта»: «Проект целиком (.bfggis)» (ТЗ 7.4 — as before: composition, file, password AES-256, view
 * settings → job) or «Объекты в файлы» (Д-17, ADR 0017 §5 — ExportObjects.tsx). The props of the window
 * (`mode`, `formats`, `types`, `folder`, `ids` — `project.export` of Claude, the data manager) are read from the
 * window store: Shell gives the window only its id.
 */
export function ExportWindow({ winId }: { winId: string }) {
  const props = useStore(windowStore, (s) => s.windows.find((w) => w.id === winId)?.props);
  const [mode, setMode] = useState<ExportMode>(props?.mode === "objects" ? "objects" : "bfggis");
  const current = useStore(projectStore, (s) => s.current);
  if (!current) return <div className={css.wrap}>Проект не открыт.</div>;
  return (
    <div className={css.wrap} data-testid="project-export">
      <div className={css.choice} role="radiogroup" aria-label="Что экспортировать" style={{ flexDirection: "row", gap: 16 }}>
        <label>
          <input type="radio" name={`${winId}-mode`} checked={mode === "bfggis"} onChange={() => setMode("bfggis")} data-testid="export-mode-bfggis" /> Проект целиком (.bfggis)
        </label>
        <label>
          <input type="radio" name={`${winId}-mode`} checked={mode === "objects"} onChange={() => setMode("objects")} data-testid="export-mode-objects" /> Объекты в файлы
        </label>
      </div>
      {mode === "bfggis" ? <BfggisExport winId={winId} /> : <ExportObjects winId={winId} project={current.name} initial={props as ObjectsInitial | undefined} />}
    </div>
  );
}

/** The whole project into one .bfggis (ТЗ 7.4). */
function BfggisExport({ winId }: { winId: string }) {
  const app = useApp();
  const current = useStore(projectStore, (s) => s.current);
  const [path, setPath] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [view, setView] = useState(true);
  const [error, setError] = useState<string | null>(null);
  if (!current) return null;

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
    <>
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
    </>
  );
}
