/**
 * The window «Проверка по скважинам» (ТЗ 7.35 Д-32; ADR 0026 решение 28; docs/progress/e19-api.md §8.1, §8.2): the model, then two tabs —
 * «Отклонения» (the table of the points of the data: f модели, f данных, невязка, расстояние до каркаса, источник; the sort, the pages, the file,
 * the highlight beyond the threshold, a click goes to the point in 3D) and «Карта опоры» (the surface painted by the distance to the data,
 * D_оп, the share of the area beyond it). The window is floating, not modal; the commands are `implicit.check` and `implicit.support`.
 */
import { useEffect } from "react";
import { requestClose } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import { CheckTable } from "./CheckTable";
import { checkStore, openCheck, reloadCheck, resetCheck, setTab, whenGone, type CheckTab } from "./CheckState";
import mod from "./Check.module.css";
import { useSel } from "./hooks";
import { staleLine } from "./run";
import { implicitStore } from "./state";
import { resetSupport } from "./SupportState";
import { SupportPanel } from "./SupportPanel";

const TABS: readonly { id: CheckTab; title: string }[] = [
  { id: "table", title: "Отклонения" },
  { id: "support", title: "Карта опоры" },
];

export function CheckWindow({ winId }: { winId: string }) {
  const s = useSel(checkStore, (x) => x);
  const rows = useSel(implicitStore, (x) => x.rows);
  // a window that goes takes its points and its map out of the 3D window
  useEffect(
    () =>
      whenGone(winId, () => {
        resetCheck();
        resetSupport();
      }),
    [winId],
  );
  // the tab of the table is shown and nothing is read yet (the window was opened on the map, another model was chosen)
  useEffect(() => {
    if (s.tab === "table" && s.modelId !== null && s.phase === "idle") void reloadCheck();
  }, [s.tab, s.modelId, s.phase]);
  const built = rows.filter((r) => r.stats !== null || r.outputs > 0);
  const pick = (value: string) => {
    if (value) void openCheck(Number(value), s.tab);
  };
  const stale = staleLine(s.page?.model);
  return (
    <div className={mod.window} data-testid="implicit-check">
      <div className={mod.bar}>
        <label>
          <span>Неявная модель</span>
          <select className={mod.select} value={s.modelId ?? ""} aria-label="Неявная модель" data-testid="chk-model" onChange={(e) => pick(e.target.value)}>
            <option value="">— выберите —</option>
            {built.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        <div className={mod.actions}>
          <button type="button" className={css.btn} disabled={s.modelId === null || s.phase === "loading"} title="Прочитать таблицу заново" data-testid="chk-reload" onClick={() => void reloadCheck()}>
            Обновить
          </button>
        </div>
      </div>
      <div className={mod.main}>
        {s.modelId === null && <p className={mod.empty}>Выберите построенную модель: проверка покажет, насколько поле модели расходится с точками данных, и где поверхность ничем не подкреплена.</p>}
        {s.error && (
          <p className={css.error} role="alert" data-testid="chk-error">
            {s.error}
          </p>
        )}
        {s.phase === "loading" && !s.page && (
          <p className={mod.empty} role="status" data-testid="chk-loading">
            Считаю невязки по точкам данных…
          </p>
        )}
        {stale && (
          <p className={mod.banner} role="status" data-testid="chk-stale">
            {stale}
          </p>
        )}
        {s.modelId !== null && (
          <>
            <div className={mod.tabs} role="tablist" aria-label="Что показать">
              {TABS.map((t) => (
                <button key={t.id} type="button" role="tab" aria-selected={s.tab === t.id} data-testid={`chk-tab-${t.id}`} onClick={() => setTab(t.id)}>
                  {t.title}
                </button>
              ))}
            </div>
            {s.tab === "table" && <CheckTable />}
            {s.tab === "support" && <SupportPanel modelId={s.modelId} />}
          </>
        )}
      </div>
      <div className={mod.footer}>
        <div className={css.row}>
          <span style={{ flex: 1 }} />
          <button type="button" className={css.btn} data-testid="chk-close" onClick={() => void requestClose(winId)}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
