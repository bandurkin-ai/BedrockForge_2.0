import { useEffect, useState } from "react";
import { bridge } from "../../bridge";
import { useApp } from "../../shell/AppContext";
import { Icon } from "../../shell/Icon";
import { logStep, openLogWindow, opLogStore, startOpLog, stopOpLog } from "../../shell/opLog";
import { pickFilesIn } from "../../shell/lastFolder";
import { useStore } from "../../shell/useStore";
import { closeWindow, updateWindow } from "../../shell/windows";
import { setPanel } from "../../shell/workspace";
import { refresh as refreshJobs } from "../jobs/state";
import { projectStore } from "../projects/state";
import { FileCard } from "./FileCard";
import { TrenchesStep } from "./TrenchesStep";
import { trenchSummary } from "./trenches";
import { canStart, clashOf, filesOf, MODE_TITLES, needsReplace, ready, replaces, sampleFile, stepsOf, tableNameOf, trenchBlocker, Wizard, type StepId, type WizardMode } from "./wizard";
import css from "./ImportWizard.module.css";

const STEP_HINT: Record<WizardMode, Partial<Record<StepId, string>>> = {
  holes: {
    collar: "Файл устьев: номер скважины, X, Y, Z, глубина. Можно пропустить, если устья уже загружены в набор.",
    survey: "Инклинометрия: номер скважины, глубина замера, азимут, наклон. Можно пропустить.",
    tables: "Пробы, интервальные таблицы (литология, RQD…) и события. Несколько файлов — каждый своей таблицей.",
    import: "Набор, в который загрузить, и система координат.",
  },
  trenches: {
    trenches: "Канава — одна полилиния: устье — первая вершина, длина — 3D-длина линии, траектория проходит через все вершины.",
    tables: "Таблица проб «канава, от, до, …»: «от» и «до» — по 3D-длине полилинии от первой вершины. Можно пропустить.",
    import: "Набор, в который загрузить канавы, и система координат.",
  },
};

/** «Импорт скважин» (ТЗ 7.7): four steps, several files at once, the operation log in the title (ТЗ 7.9).
 * With `mode: "trenches"` — «Канавы по полилиниям» (ТЗ 7.35 Д-16): the file of polylines, samples, the import. */
export function ImportWizard({ winId, paths, mode }: { winId: string; paths?: string[]; mode?: WizardMode }) {
  const app = useApp();
  const [wizard] = useState(() => {
    const project = projectStore.get().current;
    const m = mode ?? "holes";
    return new Wizard(startOpLog(MODE_TITLES[m], "import", { uiVersion: bridge()?.appVersion(), coreVersion: app.store.get().coreVersion, project: project?.name }), m);
  });
  const s = useStore(wizard.store, (x) => x);
  useStore(opLogStore, (x) => x.version);
  useEffect(() => {
    if (paths?.length) void wizard.add(paths);
    return () => {
      stopOpLog(wizard.log);
      if (!wizard.store.get().started) wizard.close();
    };
  }, [wizard, paths]);

  const steps = stepsOf(s.mode);
  const stepIndex = Math.max(0, steps.findIndex((x) => x.id === s.step));
  const stepFiles = filesOf(s, s.step);
  const trenches = s.mode === "trenches";
  const setMode = (m: WizardMode) => {
    wizard.setMode(m);
    updateWindow(winId, { title: MODE_TITLES[m] }); // the window is called by what it loads
  };
  const pick = async () => {
    const files = await pickFilesIn("tables", !trenches, `wizard:${winId}`);
    if (files.length) await wizard.add(files, s.step);
  };
  const pickPrj = async () => {
    const [file] = await pickFilesIn("prj", false, `wizard:${winId}`);
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
          {steps.map((st, i) => (
            <li key={st.id}>
              <button type="button" className={st.id === s.step ? css.stepActive : css.step} onClick={() => wizard.go(st.id)}>
                {i + 1}. {st.title}
                {st.id !== "import" && filesOf(s, st.id).length > 0 && <span className={css.badge}>{filesOf(s, st.id).length}</span>}
              </button>
            </li>
          ))}
        </ol>
        <span className={css.note}>
          Шаг {stepIndex + 1} из {steps.length}
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
      {stepIndex === 0 && (
        <div className={css.modes} role="radiogroup" aria-label="Что загружать">
          <label>
            <input type="radio" name={`mode-${winId}`} checked={!trenches} onChange={() => setMode("holes")} /> Скважины: устья и инклинометрия из таблиц
          </label>
          <label>
            <input type="radio" name={`mode-${winId}`} checked={trenches} onChange={() => setMode("trenches")} /> Канавы по полилиниям
          </label>
        </div>
      )}
      <div className={css.hint}>{STEP_HINT[s.mode][s.step]}</div>
      <div className={css.body}>
        {s.step === "trenches" ? (
          <TrenchesStep wizard={wizard} />
        ) : s.step !== "import" ? (
          <>
            {stepFiles.map((f) => (
              <FileCard key={f.id} wizard={wizard} file={f} />
            ))}
            <button type="button" className={css.btn} disabled={trenches && s.files.length > 0} title={trenches && s.files.length > 0 ? "Пробы канав — одна таблица" : undefined} onClick={() => void pick()}>
              {trenches ? "Выбрать таблицу проб…" : "Добавить файлы…"}
            </button>
            <div className={css.note}>
              {trenches
                ? "DAT (Micromine), CSV / TXT, Excel. Тип — «Пробы (Assay)» или «Интервальная таблица»; в колонке «Скважина» — имя канавы."
                : "DAT (Micromine), CSV / TXT, Excel .xlsx / .xlsm / .xls. Если тип файла не подходит шагу, мастер предложит, как его загрузить."}
            </div>
          </>
        ) : (
          <ImportStep wizard={wizard} onPrj={() => void pickPrj()} />
        )}
      </div>
      <div className={css.footer}>
        {s.error && <span className={css.error}>{s.error}</span>}
        <button type="button" className={css.btn} disabled={stepIndex === 0} onClick={() => wizard.go(steps[stepIndex - 1].id)}>
          Назад
        </button>
        {s.step !== "import" ? (
          <button type="button" className={`${css.btn} ${css.primary}`} onClick={() => wizard.go(steps[stepIndex + 1].id)}>
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
  const trenches = s.mode === "trenches";
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
        <div className={css.note}>
          {trenches
            ? "Канавы добавляются к скважинам набора; канава с именем, которое уже есть в наборе, получает новые устье и траекторию."
            : "Таблица того же типа и названия заменяется только в этом наборе («Заменить»)."}
        </div>
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
        <ul className={css.summary} data-testid="import-summary">
          {trenches && (
            <li className={s.trench.preview?.trenches ? "" : css.error}>
              {s.trench.path ? `${s.trench.name}: ${s.trench.preview ? trenchSummary(s.trench.preview) : (s.trench.error ?? "читается…")}` : "Файл линий канав не выбран."}
            </li>
          )}
          {s.files.map((f) => (
            <li key={f.id} className={ready(f) ? "" : css.error}>
              {f.name}: {f.kind ? `${f.check?.rows.toLocaleString("ru-RU")} строк` : "тип не выбран"}
              {f.force ? " · с ошибками" : ""}
              {f.kind && f.kind !== "collar" && f.kind !== "survey" ? ` → таблица «${tableNameOf(f)}»` : ""}
              {replaces(s, f) ? <span className={css.warn}> · заменит {replaces(s, f)}</span> : null}
            </li>
          ))}
          {!s.files.length && <li className={css.note}>{trenches ? "Без проб: только траектории канав." : "Файлы не выбраны."}</li>}
        </ul>
        {trenches ? (
          <>
            {needsReplace(s) && (
              <label className={css.force}>
                <input type="checkbox" checked={s.replaceSamples} onChange={(e) => wizard.store.set({ replaceSamples: e.target.checked })} /> Заменить {replaces(s, sampleFile(s)!)} целиком — пробы всех
                скважин набора
              </label>
            )}
            {trenchBlocker(s) && <div className={css.error}>{trenchBlocker(s)}</div>}
          </>
        ) : (
          <>
            {notReady.length > 0 && <div className={css.error}>Исправьте файлы, отмеченные красным, на их шагах.</div>}
            {clashOf(s) && <div className={css.error}>{clashOf(s)} Уберите лишний файл или дайте таблице другое название.</div>}
          </>
        )}
      </section>
    </div>
  );
}
