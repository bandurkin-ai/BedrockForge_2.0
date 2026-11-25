/**
 * The rows of a plan in the window «План оценки» (ТЗ 8.8.4): domain × field × method in the order of the run, the prefix of the
 * fields written, the state («устарел» with its reason, «источник удалён»), the last run; «вверх» / «вниз», «убрать», the tick for
 * «Выполнить выбранные»; below — adding a row of the model (or a new one in the wizard). Only the working copy changes here
 * (`planState.ts`); the plan is stored by «Сохранить». A row of the plan that is not in the list any more is shown and can be removed.
 */
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { METHOD_SHORT, METHOD_TITLES, type EstimationInfo, type EstimationRow } from "./api";
import { useSel } from "./hooks";
import { addAll, addItem, moveItem, PLAN_MAX, pick, pickAll, planStore, removeItem, rowsOfModel } from "./planState";
import { domainText, fieldsText, lastRunText, prefixWarnings, sampleField, stateText } from "./planView";
import mod from "./PlanWindow.module.css";
import { estimationStore } from "./state";

export function PlanRows({ readOnly }: { readOnly: boolean }) {
  const app = useApp();
  const s = useSel(planStore, (x) => x);
  const list = useSel(estimationStore, (x) => x.rows);
  const details = useSel(estimationStore, (x) => x.details);
  const rows = s.items.map((id) => list.find((r) => r.id === id) ?? null);
  const found = rows.filter((r): r is EstimationRow => r !== null);
  const free = rowsOfModel(s.modelId).filter((r) => !s.items.includes(r.id));
  const warnings = prefixWarnings(found);
  const full = s.items.length >= PLAN_MAX;
  const all = s.items.length > 0 && s.picked.length === s.items.length;
  const info = (id: number): EstimationInfo | undefined => details[id];

  return (
    <>
      {s.items.length === 0 ? (
        <p className={mod.empty} data-testid="plan-empty">
          {s.modelId === null ? "Выберите блочную модель." : "В плане нет строк: добавьте строки «Параметры оценки» ниже или создайте новую в мастере."}
        </p>
      ) : (
        <div className={mod.tableWrap}>
          <table className={mod.table} data-testid="plan-rows" aria-label="Строки плана оценки">
            <thead>
              <tr>
                <th>
                  <input type="checkbox" checked={all} aria-label="Выбрать все строки" data-testid="plan-pick-all" onChange={(e) => pickAll(e.target.checked)} />
                </th>
                <th className={mod.num}>№</th>
                <th>Домен</th>
                <th>Поле</th>
                <th>Метод</th>
                <th>Поля оценки</th>
                <th>Состояние</th>
                <th>Последний запуск</th>
                <th aria-label="Порядок и удаление" />
              </tr>
            </thead>
            <tbody>
              {s.items.map((id, i) => {
                const r = rows[i];
                const picked = s.picked.includes(id);
                const state = r ? stateText(r) : "строка удалена";
                const last = r ? lastRunText(r) : null;
                return (
                  <tr key={id} data-testid={`plan-row-${id}`} data-picked={picked} data-bad={r === null || r.source_lost}>
                    <td>
                      <input type="checkbox" checked={picked} aria-label={`Выбрать строку ${r?.name ?? `№${id}`}`} data-testid={`plan-pick-${id}`} onChange={(e) => pick(id, e.target.checked)} />
                    </td>
                    <td className={mod.no}>{i + 1}</td>
                    <td title={r ? r.name : undefined}>{r ? domainText(r) : `№${id}`}</td>
                    <td>{(r && sampleField(info(id))) ?? "—"}</td>
                    <td title={r ? METHOD_TITLES[r.method] : undefined}>{r ? METHOD_SHORT[r.method] : "—"}</td>
                    <td>{r ? fieldsText(r) : "—"}</td>
                    <td className={`${mod.state} ${r === null || r.source_lost ? mod.lost : r.stale ? mod.stale : mod.ok}`} data-testid={`plan-state-${id}`} title={state ?? undefined}>
                      {state ?? "актуальна"}
                    </td>
                    <td>{last ?? "не выполнялась"}</td>
                    <td>
                      <span className={mod.move}>
                        <button type="button" className={css.iconBtn} disabled={i === 0} aria-label="Выше" title="Выше в порядке выполнения" data-testid={`plan-up-${id}`} onClick={() => moveItem(id, -1)}>
                          ▲
                        </button>
                        <button type="button" className={css.iconBtn} disabled={i === s.items.length - 1} aria-label="Ниже" title="Ниже в порядке выполнения" data-testid={`plan-down-${id}`} onClick={() => moveItem(id, 1)}>
                          ▼
                        </button>
                        <button type="button" className={css.iconBtn} aria-label="Убрать из плана" title="Убрать из плана (строка «Параметры оценки» останется)" data-testid={`plan-remove-${id}`} onClick={() => removeItem(id)}>
                          ✕
                        </button>
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {warnings.length > 0 && (
        <ul className={mod.notes} data-testid="plan-warnings">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      <div className={mod.add}>
        <select className={mod.select} aria-label="Строка для добавления" disabled={s.modelId === null || full || free.length === 0} data-testid="plan-add-select" value="" onChange={(e) => e.target.value && addItem(Number(e.target.value))}>
          <option value="">{free.length ? "Добавить строку…" : s.modelId === null ? "—" : "Все строки модели уже в плане"}</option>
          {free.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
        <button type="button" className={css.btn} disabled={free.length === 0 || full} data-testid="plan-add-all" onClick={addAll}>
          Добавить все
        </button>
        <button type="button" className={css.btn} disabled={s.modelId === null || !!app.registry.reason("estimate.open")} title={app.registry.reason("estimate.open") ?? "Мастер «Оценка содержаний»: новая строка этой модели"} data-testid="plan-new-row" onClick={() => void app.run("estimate.open", { model_id: s.modelId })}>
          Новая строка…
        </button>
        {full && <span className={css.note}>В плане не больше {PLAN_MAX} строк.</span>}
        {readOnly && <span className={css.note}>Проект открыт только для чтения: план можно смотреть, но не сохранять.</span>}
      </div>
    </>
  );
}
