import { useEffect, useState } from "react";
import { bridge } from "../bridge";
import { api } from "./api";
import { useApp } from "./AppContext";
import { useStore } from "./useStore";
import css from "./SettingsWindow.module.css";

/** «Настройка → Общие»: folder of projects (ТЗ 6.5, 7.3) and workers of the general job queue (ТЗ 7.6). */
export function GeneralSettings() {
  const app = useApp();
  const chosen = useStore(app.store, (s) => s.projectsDir);
  const heavy = useStore(app.store, (s) => s.heavyWorkers);
  const [root, setRoot] = useState<string | null>(null);
  useEffect(() => {
    api
      .get<{ root: string }>("/projects")
      .then((r) => setRoot(r.root))
      .catch(() => setRoot(null));
  }, []);
  const pick = async () => {
    const dir = await bridge()?.pickFolder();
    if (dir) app.store.set({ projectsDir: dir });
  };
  const setHeavy = (n: number) => {
    app.store.set({ heavyWorkers: n });
    void api.put("/jobs/workers", { heavy: n }).catch(() => undefined);
  };
  return (
    <div className={css.general}>
      <label className={css.field}>
        <span>Папка данных проектов</span>
        <span className={css.fieldRow}>
          <input readOnly value={chosen ?? root ?? ""} aria-label="Папка данных проектов" />
          <button type="button" onClick={() => void pick()}>
            Выбрать…
          </button>
          {chosen && (
            <button type="button" onClick={() => app.store.set({ projectsDir: null })}>
              По умолчанию
            </button>
          )}
        </span>
      </label>
      {chosen && root !== chosen && <div className={css.note}>Новая папка начнёт действовать после перезапуска программы. Проекты из старой папки не переносятся.</div>}
      <label className={css.field}>
        <span>Исполнителей общей очереди заданий</span>
        <select value={heavy} onChange={(e) => setHeavy(Number(e.target.value))} aria-label="Исполнителей общей очереди">
          {[1, 2, 3, 4, 6, 8].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
      <div className={css.note}>Плюс один быстрый исполнитель для операций с каркасами и линиями: он не ждёт долгого импорта. Каждый исполнитель — отдельный процесс; больше исполнителей — больше памяти.</div>
    </div>
  );
}
