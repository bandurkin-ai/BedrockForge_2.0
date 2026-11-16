import { useEffect, useState } from "react";
import { api, CoreError } from "../../shell/api";
import { useApp } from "../../shell/AppContext";
import { closeWindow } from "../../shell/windows";
import { setPanel } from "../../shell/workspace";
import { refresh } from "../jobs/state";
import { formatDate, SUMMARY_TITLES, type Description, type Summary } from "./state";
import css from "./Projects.module.css";

interface Inspect {
  encrypted: boolean;
  needs_password: boolean;
  from_bf: boolean;
  name?: string;
  description?: Partial<Description>;
  created_at?: string;
  exported_at?: string;
  app_version?: string;
  schema_revision?: string;
  summary?: Summary;
  tables?: Record<string, number>;
  view_settings?: boolean;
  name_taken?: boolean;
  copy_name?: string;
}

type Mode = "copy" | "rename" | "replace";

/** «Импорт проекта» (ТЗ 7.4): file → check (name, versions, composition, password) → job `import.project`. */
export function ImportWindow({ winId, path }: { winId: string; path: string }) {
  const app = useApp();
  const [info, setInfo] = useState<Inspect | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("copy");
  const [name, setName] = useState("");
  const [view, setView] = useState(true);

  const check = async (pw: string | null) => {
    try {
      const r = await api.post<Inspect>("/projects/import/inspect", { path, password: pw });
      setInfo(r);
      setError(null);
      if (r.name) setName(`${r.name} (новый)`);
    } catch (e) {
      setError(e instanceof CoreError ? e.full : String(e));
    }
  };
  useEffect(() => {
    void check(null);
  }, [path]);

  const start = async () => {
    try {
      const body = { path, password: password || null, mode: info?.name_taken ? mode : "new", name: mode === "rename" ? name : null, apply_view: view };
      await api.post("/projects/import", body);
      app.store.set((s) => ({ workspace: setPanel(s.workspace, "bottom", true, "jobs") }));
      await refresh();
      closeWindow(winId);
    } catch (e) {
      setError(e instanceof CoreError ? e.full : String(e));
    }
  };

  const file = path.split(/[\\/]/).pop();
  return (
    <div className={css.wrap}>
      <div className={css.row}>
        <span className={css.label}>Файл</span>
        <span title={path}>{file}</span>
      </div>
      {info?.needs_password && (
        <section className={css.section}>
          <h3>Файл защищён паролем</h3>
          <div style={{ display: "flex", gap: 8 }}>
            <input className={css.input} type="password" autoFocus aria-label="Пароль файла" value={password} onChange={(e) => setPassword(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void check(password)} />
            <button type="button" className={css.btn} onClick={() => void check(password)}>
              Проверить
            </button>
          </div>
        </section>
      )}
      {error && <div className={css.error} data-testid="import-error">{error}</div>}
      {info && !info.needs_password && (
        <>
          <section className={css.section}>
            <h3>Проверка</h3>
            <div className={css.title} data-testid="import-name">
              {info.name}
            </div>
            {info.from_bf && <div className={css.warn}>Файл BedrockForge первой версии: перенос его данных включается вместе с этапами наборов, каркасов, линий и растров.</div>}
            <div className={css.note}>
              {[info.description?.deposit, info.description?.license, info.description?.stage].filter(Boolean).join(" · ") || "Без описания"}
            </div>
            <div className={css.row}>
              <span className={css.label}>Экспортирован</span>
              <span>{formatDate(info.exported_at)}</span>
            </div>
            <div className={css.row}>
              <span className={css.label}>Версия программы / схемы</span>
              <span>
                {info.app_version ?? "—"} / {info.schema_revision ?? "—"}
              </span>
            </div>
            <div className={css.counts}>
              {SUMMARY_TITLES.filter(([k]) => (info.summary?.[k] ?? 0) > 0).map(([k, t]) => (
                <span key={k}>
                  {t}:<b>{info.summary![k]}</b>
                </span>
              ))}
            </div>
          </section>
          {info.name_taken && (
            <section className={css.section}>
              <h3>Проект «{info.name}» уже есть</h3>
              <div className={css.choice}>
                <label>
                  <input type="radio" checked={mode === "copy"} onChange={() => setMode("copy")} /> Создать копию «{info.copy_name}»
                </label>
                <label>
                  <input type="radio" checked={mode === "rename"} onChange={() => setMode("rename")} /> Другое название
                  {mode === "rename" && <input className={css.input} aria-label="Название" value={name} onChange={(e) => setName(e.target.value)} />}
                </label>
                <label>
                  <input type="radio" checked={mode === "replace"} onChange={() => setMode("replace")} /> Заменить существующий (старый удаляется только после успешного импорта)
                </label>
              </div>
            </section>
          )}
          {info.view_settings && (
            <label className={css.choice}>
              <span>
                <input type="checkbox" checked={view} onChange={(e) => setView(e.target.checked)} /> Применить настройки вида из файла
              </span>
            </label>
          )}
          <div className={css.footer}>
            <button type="button" className={`${css.btn} ${css.primary}`} onClick={() => void start()} disabled={info.from_bf}>
              Импортировать
            </button>
            <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
              Отмена
            </button>
          </div>
        </>
      )}
    </div>
  );
}
