/** The window «Импорт файлов» for lines, points, rasters and wireframes (ТЗ 7.8; ADR 0011 §5; ADR 0013 §6): the
 * composition of the chosen files (`ObjectsStep`), the operation log in the title (ТЗ 7.9), the start of
 * `import.objects`; the wireframes it stores are checked when it ends (`afterImport.ts`). `cloud` — the window
 * «Облако точек» (ADR 0014 §6): every table of the selection is loaded as a point cloud. */
import { useEffect, useState } from "react";
import { bridge } from "../../bridge";
import { useApp } from "../../shell/AppContext";
import { Icon } from "../../shell/Icon";
import { openLogWindow, opLogStore, startOpLog, stopOpLog } from "../../shell/opLog";
import { pickFilesIn } from "../../shell/lastFolder";
import { useStore } from "../../shell/useStore";
import { closeWindow, windowStore } from "../../shell/windows";
import { syncOda } from "../../shell/odaTool";
import { setPanel } from "../../shell/workspace";
import { refresh as refreshJobs } from "../jobs/state";
import { projectStore } from "../projects/state";
import { afterStart } from "./afterImport";
import type { ObjectsPick } from "./bind";
import type { CloudMode } from "./cloud";
import { canStartObjects, ObjectsWizard } from "./objects";
import { ObjectsStep } from "./ObjectsStep";
import css from "./ImportWizard.module.css";

export function ObjectsWindow({ winId, paths, pick, cloud }: { winId: string; paths?: string[]; pick?: ObjectsPick; cloud?: CloudMode }) {
  const app = useApp();
  // `import.files {folder}`: the folder is a prop of the window (the shell passes the others by name)
  const [folder] = useState(() => {
    const f = windowStore.get().windows.find((w) => w.id === winId)?.props?.folder;
    return typeof f === "string" && f ? f : null;
  });
  // opened from the job that refused the file (`import.blocks_choice`): why, shown above the composition
  const [choice] = useState(() => {
    const c = windowStore.get().windows.find((w) => w.id === winId)?.props?.choice as { file?: unknown; message?: unknown } | undefined;
    return c && typeof c.file === "string" ? { file: c.file, message: typeof c.message === "string" ? c.message : "" } : null;
  });
  const [wizard] = useState(() => {
    const project = projectStore.get().current;
    const log = startOpLog(cloud ? "Облако точек" : "Импорт файлов", "import", { uiVersion: bridge()?.appVersion(), coreVersion: app.store.get().coreVersion, project: project?.name });
    return new ObjectsWizard(log, () => syncOda(app.store), cloud ?? null, folder);
  });
  const s = useStore(wizard.store, (x) => x);
  useStore(opLogStore, (x) => x.version);
  useEffect(() => {
    if (paths?.length) void wizard.add(paths);
    return () => stopOpLog(wizard.log);
  }, [wizard, paths]);

  const add = async () => {
    const files = await pickFilesIn(cloud ?? pick ?? "objects", true, `wizard:${winId}`);
    if (files.length) await wizard.add(files);
  };
  const start = async () => {
    const r = await wizard.start();
    if (!r) return;
    app.store.set((st) => ({ workspace: setPanel(st.workspace, "bottom", true, "jobs") }));
    await refreshJobs();
    afterStart(r);
    closeWindow(winId);
  };

  return (
    <div className={css.wrap}>
      <div className={css.top}>
        <span className={css.steps}>{cloud ? "Облако точек: что будет загружено и сколько точек" : "Состав загрузки: что будет создано в проекте и что не загрузится"}</span>
        {folder && (
          <span className={css.note} data-testid="objects-folder">
            Папка в дереве: «{folder}»
          </span>
        )}
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
      {choice && (
        <div className={css.note} role="status" data-testid="objects-choice-note">
          Файл «{choice.file}» не загрузился в задании{choice.message ? `: ${choice.message}` : "."} Выберите ниже, что с ним сделать.
        </div>
      )}
      <div className={css.body}>
        <ObjectsStep wizard={wizard} onAdd={() => void add()} onSettings={() => void app.run("settings.open", { tab: "tools" })} />
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
