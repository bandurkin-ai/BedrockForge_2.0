/** The window «Импорт файлов» for lines, points and rasters (ТЗ 7.8; ADR 0011 §5): the composition of the
 * chosen files (`ObjectsStep`), the operation log in the title (ТЗ 7.9), the start of `import.objects`. */
import { useEffect, useState } from "react";
import { bridge } from "../../bridge";
import { useApp } from "../../shell/AppContext";
import { Icon } from "../../shell/Icon";
import { openLogWindow, opLogStore, startOpLog, stopOpLog } from "../../shell/opLog";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import { setPanel } from "../../shell/workspace";
import { refresh as refreshJobs } from "../jobs/state";
import { projectStore } from "../projects/state";
import { canStartObjects, ObjectsWizard } from "./objects";
import { ObjectsStep } from "./ObjectsStep";
import css from "./ImportWizard.module.css";

export function ObjectsWindow({ winId, paths, pick }: { winId: string; paths?: string[]; pick?: "objects" | "lines" | "rasters" }) {
  const app = useApp();
  const [wizard] = useState(() => {
    const project = projectStore.get().current;
    return new ObjectsWizard(startOpLog("Импорт файлов", "import", { uiVersion: bridge()?.appVersion(), coreVersion: app.store.get().coreVersion, project: project?.name }));
  });
  const s = useStore(wizard.store, (x) => x);
  useStore(opLogStore, (x) => x.version);
  useEffect(() => {
    if (paths?.length) void wizard.add(paths);
    return () => stopOpLog(wizard.log);
  }, [wizard, paths]);

  const add = async () => {
    const files = (await bridge()?.pickFiles(pick ?? "objects", true)) ?? [];
    if (files.length) await wizard.add(files);
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
        <span className={css.steps}>Состав загрузки: что будет создано в проекте и что не загрузится</span>
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
      <div className={css.body}>
        <ObjectsStep wizard={wizard} onAdd={() => void add()} />
      </div>
      <div className={css.footer}>
        {s.error && <span className={css.error}>{s.error}</span>}
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Отмена
        </button>
        <button type="button" className={`${css.btn} ${css.primary}`} disabled={!canStartObjects(s)} onClick={() => void start()}>
          Импортировать
        </button>
      </div>
    </div>
  );
}
