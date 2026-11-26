/**
 * The window «Проектная скважина» (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решения 28, 29, № 103): three tabs — «Параметры» (the collar, the direction, the length),
 * «Прогноз» (the intervals by the bodies, the nearest holes at the target) and «План / факт» (the actual hole against the planned one, the chart of the
 * deviation by depth) — and the buttons «Сохранить» (the version is checked: another one stored is «Перечитать»), «Сохранить как новую», «Экспорт CSV»
 * and «Экспорт DXF» (the files are chosen in the save dialog of the application). Every button runs a command of the registry (`planned.save`,
 * `planned.export`, …): the ribbon, Ctrl+K and the assistant do the same. The window is floating, not modal; closing with unsaved changes asks first.
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import { askConfirm } from "../../shell/dialogs";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { requestClose, setCloseGuard, windowStore } from "../../shell/windows";
import { useSel } from "../estimation/hooks";
import mod from "./Planned.module.css";
import { PlannedCompare } from "./PlannedCompare";
import { PlannedForecast } from "./PlannedForecast";
import { PlannedParams } from "./PlannedParams";
import { stopPick } from "./plannedPick";
import { askReason, plannedWin, rereadWin, resetAll, resetWin, setTab, TAB_TITLES, type Tab } from "./plannedState";

const TABS: readonly Tab[] = ["params", "forecast", "compare"];

/** Runs `fn` after the window `winId` is really gone (a closed window, not a re-render). */
function whenGone(winId: string, fn: () => void): () => void {
  return () =>
    queueMicrotask(() => {
      if (!windowStore.get().windows.some((w) => w.id === winId)) fn();
    });
}

export function PlannedWindow({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(plannedWin, (x) => x);
  const readOnly = useSel(app.store, (x) => x.projectReadOnly);
  useEffect(
    () =>
      whenGone(winId, () => {
        stopPick();
        resetWin();
        resetAll();
      }),
    [winId],
  );
  useEffect(() => {
    setCloseGuard(winId, async () => !plannedWin.get().dirty || askConfirm({ title: "Закрыть окно", message: "Проектная скважина изменена и не сохранена. Закрыть окно без сохранения?", okLabel: "Закрыть", danger: true }));
    return () => setCloseGuard(winId, null);
  }, [winId]);

  const run = (id: string, args: Record<string, unknown> = {}) => void app.run(id, args).catch((e: unknown) => app.store.set({ statusNote: errorText(e) }));
  const writeReason = readOnly ? "Проект открыт только для чтения" : null;
  const fileReason = askReason();
  const conflict = s.issue?.reread ? s.issue : null;
  const general = s.issue && s.issue.param === null && !conflict ? s.issue : null;
  return (
    <div className={mod.window} data-testid="planned-window">
      <div className={mod.head}>
        <h3 data-testid="pl-title">{s.form.name.trim() || "Новая проектная скважина"}</h3>
        <span data-testid="pl-version">{s.id === null ? "не сохранена" : s.gone ? "удалена в другом окне" : `№${s.id}, версия ${s.version}`}</span>
        {s.dirty && <span data-testid="pl-dirty">есть несохранённые изменения</span>}
      </div>
      <div className={mod.tabs} role="tablist" aria-label="Разделы окна">
        {TABS.map((t) => (
          <button key={t} type="button" role="tab" aria-selected={s.tab === t} data-testid={`pl-tab-${t}`} onClick={() => setTab(t)}>
            {TAB_TITLES[t]}
          </button>
        ))}
      </div>
      <div className={mod.page} data-testid="pl-page" data-tab={s.tab}>
        {s.tab === "params" && <PlannedParams />}
        {s.tab === "forecast" && <PlannedForecast />}
        {s.tab === "compare" && <PlannedCompare />}
        {general && (
          <p className={css.error} role="alert" data-testid="pl-issue-general">
            {general.message} {general.hint}
          </p>
        )}
      </div>
      {conflict && (
        <p className={`${css.error} ${mod.banner}`} role="alert" data-testid="pl-conflict">
          <span>
            {conflict.message} {conflict.hint}
          </span>
          <button type="button" className={css.btn} data-testid="pl-reread" onClick={() => void rereadWin()}>
            Перечитать
          </button>
        </p>
      )}
      <div className={mod.footer}>
        <span className={mod.grow} data-testid="pl-saved">
          {s.saved ?? ""}
        </span>
        <button type="button" className={css.primary} disabled={s.saving || !!writeReason} title={writeReason ?? "Записать скважину в проект (версия проверяется)"} data-testid="pl-save" onClick={() => run("planned.save")}>
          Сохранить
        </button>
        <button type="button" className={css.btn} disabled={s.saving || !!writeReason || s.id === null} title={writeReason ?? (s.id === null ? "Скважина ещё не записана" : "Записать форму новой скважиной, не заменяя открытую")} data-testid="pl-save-new" onClick={() => run("planned.save", { as_new: true })}>
          Сохранить как новую
        </button>
        <button type="button" className={css.btn} disabled={s.saving || fileReason !== null} title={fileReason ?? "Устье и инклинометрия: collar.csv и survey.csv в выбранной папке (их читает импорт набора)"} data-testid="pl-export-csv" onClick={() => run("planned.export", { id: s.id, format: "csv" })}>
          Экспорт CSV
        </button>
        <button type="button" className={css.btn} disabled={s.saving || fileReason !== null} title={fileReason ?? "Линия траектории в файле DXF"} data-testid="pl-export-dxf" onClick={() => run("planned.export", { id: s.id, format: "dxf" })}>
          Экспорт DXF
        </button>
        <button type="button" className={css.btn} data-testid="pl-close" onClick={() => void requestClose(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
