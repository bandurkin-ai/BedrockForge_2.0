import { useEffect, useState } from "react";
import { bridge } from "../../bridge";
import { useApp } from "../../shell/AppContext";
import { Icon } from "../../shell/Icon";
import { logStep, openLogWindow, opLogStore, startOpLog, stopOpLog } from "../../shell/opLog";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import { setPanel } from "../../shell/workspace";
import { refresh as refreshJobs } from "../jobs/state";
import { projectStore } from "../projects/state";
import { FileCard } from "./FileCard";
import { canStart, filesOf, ready, STEPS, Wizard, type StepId } from "./wizard";
import css from "./ImportWizard.module.css";

const STEP_HINT: Record<StepId, string> = {
  collar: "Файл устьев: номер скважины, X, Y, Z, глубина. Можно пропустить, если устья уже загружены в набор.",
  survey: "Инклинометрия: номер скважины, глубина замера, азимут, наклон. Можно пропустить.",
  tables: "Пробы, интервальные таблицы (литология, RQD…) и события. Несколько файлов — каждый своей таблицей.",
  import: "Набор, в который загрузить, и система координат.",
};

/** «Импорт скважин» (ТЗ 7.7): four steps, several files at once, the operation log in the title (ТЗ 7.9). */
export function ImportWizard({ winId, paths }: { winId: string; paths?: string[] }) {
  const app = useApp();
  const [wizard] = useState(() => {
    const project = projectStore.get().current;
    return new Wizard(startOpLog("Импорт скважин", "import", { uiVersion: bridge()?.appVersion(), coreVersion: app.store.get().coreVersion, project: project?.name }));
  });
  const s = useStore(wizard.store, (x) => x);
  useStore(opLogStore, (x) => x.version);
  useEffect(() => {
    if (paths?.length) void wizard.add(paths);
    return () => stopOpLog(wizard.log);
  }, [wizard, paths]);

  const stepIndex = STEPS.findIndex((x) => x.id === s.step);
  const stepFiles = filesOf(s, s.step);
  const pick = async () => {
    const files = (await bridge()?.pickFiles("tables", true)) ?? [];
    if (files.length) await wizard.add(files);
  };
  const pickPrj = async () => {
    const [file] = (await bridge()?.pickFiles("prj", false)) ?? [];
    if (!file) return;
    logStep(wizard.log, `Система координат: ${file}`);
    try {
      const { api } = await import("../../shell/api");
      const info = await api.post<{ wkt: string; name: string }>("/drillholes/crs/describe", { path: file });
      wizard.store.set({ crs: info.wkt, crsName: info.name });
    } catch (e) {
      wizard.store.set({ error: e instanceof Error ? e.message : String(e) });
    }
  };
  const start = async () => {
    const r = await wizard.start();
    if (!r) return;
    app.store.set((st) => ({ workspace: setPanel(st.workspace, "bottom", true, "jobs") }));
    await refreshJobs();
    closeWindow(winId);
  };

  return (
    <div className={css.wrap}>
      <div className={css.top}>
        <ol className={css.steps}>
          {STEPS.map((st, i) => (
            <li key={st.id}>
              <button type="button" className={st.id === s.step ? css.stepActive : css.step} onClick={() => wizard.go(st.id)}>
                {i + 1}. {st.title}
                {st.id !== "import" && filesOf(s, st.id).length > 0 && <span className={css.badge}>{filesOf(s, st.id).length}</span>}
              </button>
            </li>
          ))}
        </ol>
        <span className={css.note}>
          Шаг {stepIndex + 1} из {STEPS.length}
        </span>
        <button
          type="button"
          className={`${css.iconBtn} ${wizard.log.failed ? css.logFailed : ""}`}
          title={wizard.log.failed ? "Журнал операции (есть ошибки)" : "Журнал операции"}
          aria-label="Журнал операции"
          onClick={() => openLogWindow(wizard.log.id)}
        >
          <Icon name="log" size={15} />
        </button>
      </div>
      <div className={css.hint}>{STEP_HINT[s.step]}</div>
      <div className={css.body}>
        {s.step !== "import" ? (
          <>
            {stepFiles.map((f) => (
              <FileCard key={f.id} wizard={wizard} file={f} />
            ))}
            <button type="button" className={css.btn} onClick={() => void pick()}>
              Добавить файлы…
            </button>
            <div className={css.note}>DAT (Micromine), CSV / TXT, Excel .xlsx / .xlsm / .xls. Файлы другого типа сами перейдут на свой шаг.</div>
          </>
        ) : (
          <ImportStep wizard={wizard} onPrj={() => void pickPrj()} />
        )}
      </div>
      <div className={css.footer}>
        {s.error && <span className={css.error}>{s.error}</span>}
        <button type="button" className={css.btn} disabled={stepIndex === 0} onClick={() => wizard.go(STEPS[stepIndex - 1].id)}>
          Назад
        </button>
        {s.step !== "import" ? (
          <button type="button" className={`${css.btn} ${css.primary}`} onClick={() => wizard.go(STEPS[stepIndex + 1].id)}>
            Далее
          </button>
        ) : (
          <button type="button" className={`${css.btn} ${css.primary}`} disabled={!canStart(s)} onClick={() => void start()}>
            Импортировать
          </button>
        )}
      </div>
    </div>
  );
}

function ImportStep({ wizard, onPrj }: { wizard: Wizard; onPrj: () => void }) {
  const s = useStore(wizard.store, (x) => x);
  const notReady = s.files.filter((f) => !ready(f));
  return (
    <div className={css.importStep}>
      <section className={css.card}>
        <h4>Набор для загрузки</h4>
        <label className={css.radio}>
          <input type="radio" checked={s.setMode === "new"} onChange={() => wizard.store.set({ setMode: "new" })} /> Новый набор
          <input value={s.setName} onChange={(e) => wizard.store.set({ setName: e.target.value })} disabled={s.setMode !== "new"} aria-label="Название нового набора" />
        </label>
        <label className={css.radio}>
          <input type="radio" checked={s.setMode === "existing"} disabled={!s.sets.length} onChange={() => wizard.store.set({ setMode: "existing", setId: s.setId ?? s.sets[0]?.id ?? null })} /> В существующий
          <select value={s.setId ?? ""} disabled={s.setMode !== "existing"} onChange={(e) => wizard.store.set({ setId: Number(e.target.value) })} aria-label="Набор">
            {s.sets.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name} ({x.holes} скв.)
              </option>
            ))}
          </select>
        </label>
        <div className={css.note}>Таблица того же типа и названия заменяется только в этом наборе («Заменить»).</div>
      </section>
      <section className={css.card}>
        <h4>Система координат</h4>
        <div>
          {s.crsName ?? "Не задана"}{" "}
          <button type="button" className={css.linkBtn} onClick={onPrj}>
            Из файла .prj…
          </button>
        </div>
      </section>
      <section className={css.card}>
        <h4>Состав</h4>
        <ul className={css.summary}>
          {s.files.map((f) => (
            <li key={f.id} className={ready(f) ? "" : css.error}>
              {f.name}: {f.kind ? `${f.check?.rows.toLocaleString("ru-RU")} строк` : "тип не выбран"}
              {f.force ? " · с ошибками" : ""}
            </li>
          ))}
          {!s.files.length && <li className={css.note}>Файлы не выбраны.</li>}
        </ul>
        {notReady.length > 0 && <div className={css.error}>Исправьте файлы, отмеченные красным, на их шагах.</div>}
      </section>
    </div>
  );
}
