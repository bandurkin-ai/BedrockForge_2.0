/**
 * The window «Стек отношений» (Э19б; ТЗ 7.35 Д-24, Д-31; ADR 0026 решения 19–21): the domains of the model by age — the youngest on top — that can be dragged
 * or moved by the buttons «выше» / «ниже»; under each, the relation to the older groups below it («Согласно», «Эрозия», «Налегание», «Интрузия») with
 * what it does, and the check boxes «рассечена разломом» (the faults of the model). «Применить» writes the order and the relations
 * (`implicit.relations`), then the faults of each domain that changed (`implicit.group_faults`). A floating window, not modal; closing with unsaved
 * changes asks first (an own dialog).
 */
import { useEffect, useState, type DragEvent } from "react";
import { useApp } from "../../shell/AppContext";
import { askConfirm } from "../../shell/dialogs";
import css from "../../shell/PropsParts.module.css";
import { requestClose, setCloseGuard } from "../../shell/windows";
import { useSel } from "../estimation/hooks";
import mod from "./Stack.module.css";
import { RELATIONS, RELATION_HELP, RELATION_TITLES, readStackStats, relationOf, type Relation } from "./stackApi";
import { domainsOfMeta, draftNotes, faultsOfMeta, metaOf, radiusText, relationLine, relationsChanged, faultsChanged } from "./stackModel";
import { applyStack, dropDomain, moveDomain, resetStack, setDragging, setRelation, stackStore, toggleFault } from "./StackState";
import { staleLine } from "./run";
import { implicitStore } from "./state";

export function StackTab({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(stackStore, (x) => x);
  const info = useSel(implicitStore, (x) => (s.modelId === null ? undefined : x.details[s.modelId]));
  const row = useSel(implicitStore, (x) => (s.modelId === null ? undefined : x.rows.find((r) => r.id === s.modelId)));
  const readOnly = useSel(app.store, (x) => x.projectReadOnly);
  const [over, setOver] = useState<number | null | "end">(null);
  useEffect(() => {
    setCloseGuard(winId, async () => !stackStore.get().dirty || askConfirm({ title: "Закрыть окно", message: "Стек изменён и не записан. Закрыть окно без записи?", okLabel: "Закрыть", danger: true }));
    return () => setCloseGuard(winId, null);
  }, [winId]);
  const meta = metaOf(info);
  const domains = new Map(domainsOfMeta(meta).map((g) => [g.id, g]));
  const faults = faultsOfMeta(meta);
  const d = s.draft;
  const stale = staleLine(info ?? row);
  const stats = readStackStats(meta);
  const notes = d ? draftNotes(meta, d) : [];
  const reason = readOnly ? "Проект открыт только для чтения" : row?.state === "building" ? "Модель сейчас строится: дождитесь окончания задания" : null;
  const changed = !!d && (relationsChanged(meta, d) || faultsChanged(meta, d).length > 0);

  const onDrop = (e: DragEvent, before: number | null) => {
    e.preventDefault();
    const id = s.dragging;
    setOver(null);
    setDragging(null);
    if (id !== null) dropDomain(id, before);
  };

  return (
    <div className={mod.window} data-testid="implicit-stack">
      <div className={mod.main}>
        <fieldset className={css.group}>
          <legend>Модель</legend>
          <p className={css.note} data-testid="stk-model">
            {info ? `«${info.name}», версия ${info.version}` : s.modelId === null ? "Модель не выбрана." : "Чтение модели…"}
          </p>
          {stale && (
            <p className={css.warn} role="status" data-testid="stk-stale">
              {stale}
            </p>
          )}
          <span className={css.note}>Порядок — возраст: сверху самая молодая группа. Отношение берёт на себя молодая группа и говорит, как она обходится с более старыми, лежащими ниже. Расчётный порядок: разломы → группы от молодой к старой.</span>
        </fieldset>

        {d && d.order.length === 0 && (
          <p className={css.note} role="status" data-testid="stk-empty">
            В модели нет доменов литологии: добавьте их командой «Домены литологии».
          </p>
        )}

        {d && d.order.length > 0 && (
          <>
            <div className={mod.legend} aria-hidden>
              <span>▲ моложе</span>
              <span>старше ▼</span>
            </div>
            <ol className={mod.list} data-testid="stk-list" aria-label="Домены по возрасту, сверху самый молодой">
              {d.order.map((id, i) => {
                const g = domains.get(id);
                if (!g) return null;
                const rel = d.relations[id] ?? relationOf(g.relation);
                const last = i === d.order.length - 1;
                return (
                  <li
                    key={id}
                    className={mod.item}
                    data-testid={`stk-row-${id}`}
                    data-drag={s.dragging === id}
                    data-over={over === id}
                    draggable={!reason}
                    onDragStart={(e) => {
                      e.dataTransfer.effectAllowed = "move";
                      e.dataTransfer.setData("text/plain", String(id));
                      setDragging(id);
                    }}
                    onDragOver={(e) => {
                      if (s.dragging === null) return;
                      e.preventDefault();
                      setOver(id);
                    }}
                    onDragLeave={() => setOver(null)}
                    onDrop={(e) => onDrop(e, id)}
                    onDragEnd={() => {
                      setOver(null);
                      setDragging(null);
                    }}
                  >
                    <span className={mod.no} title="Потяните строку, чтобы изменить возраст" aria-hidden>
                      {i + 1}
                    </span>
                    <span className={mod.name}>
                      <strong title={g.name}>{g.name}</strong>
                      <span className={mod.codes}>{g.codes.length ? `коды: ${g.codes.join(", ")}` : "данные: ручные слои"}</span>
                    </span>
                    <span className={mod.moves}>
                      <button type="button" aria-label={`Выше (моложе): ${g.name}`} title="Выше — моложе" disabled={i === 0 || !!reason} data-testid={`stk-up-${id}`} onClick={() => moveDomain(id, -1)}>
                        ↑
                      </button>
                      <button type="button" aria-label={`Ниже (старше): ${g.name}`} title="Ниже — старше" disabled={last || !!reason} data-testid={`stk-down-${id}`} onClick={() => moveDomain(id, 1)}>
                        ↓
                      </button>
                    </span>
                    <span className={mod.rel}>
                      <select
                        value={rel}
                        aria-label={`Отношение группы «${g.name}» к более старым`}
                        disabled={last || !!reason}
                        title={last ? "Самая старая группа: ниже неё нет групп, отношение ничего не меняет" : RELATION_HELP[rel]}
                        data-testid={`stk-relation-${id}`}
                        onChange={(e) => setRelation(id, e.target.value as Relation)}
                      >
                        {RELATIONS.map((r) => (
                          <option key={r} value={r}>
                            {RELATION_TITLES[r]}
                          </option>
                        ))}
                      </select>
                      <p className={mod.help} data-testid={`stk-help-${id}`}>
                        {last ? relationLine(rel, true) : RELATION_HELP[rel]}
                      </p>
                    </span>
                    {faults.length > 0 && (
                      <span className={mod.faults} data-testid={`stk-faults-${id}`}>
                        <span>рассечена разломом:</span>
                        {faults.map((f) => (
                          <label key={f.id} title={`${f.name}: ${radiusText(typeof f.params.radius === "number" ? f.params.radius : null)}`}>
                            <input type="checkbox" checked={(d.faults[id] ?? []).includes(f.id)} disabled={!!reason} data-testid={`stk-fault-${id}-${f.id}`} onChange={(e) => toggleFault(id, f.id, e.target.checked)} />
                            {f.name}
                          </label>
                        ))}
                      </span>
                    )}
                  </li>
                );
              })}
              <li
                aria-hidden
                data-over={over === "end"}
                className={mod.item}
                style={{ gridTemplateColumns: "minmax(0, 1fr)", padding: 2, minHeight: 8, opacity: s.dragging === null ? 0 : 1 }}
                onDragOver={(e) => {
                  if (s.dragging === null) return;
                  e.preventDefault();
                  setOver("end");
                }}
                onDragLeave={() => setOver(null)}
                onDrop={(e) => onDrop(e, null)}
              >
                <span className={mod.help}>В конец списка — самая старая</span>
              </li>
            </ol>
            {faults.length === 0 && (
              <p className={css.note} data-testid="stk-no-faults">
                В модели нет разломов. Разлом добавляет команда «Разлом»: после неё здесь появятся флажки «рассечена разломом».{" "}
                <button type="button" className={css.btn} disabled={!!reason} data-testid="stk-new-fault" onClick={() => void app.run("implicit.fault", { model_id: s.modelId })}>
                  Новый разлом…
                </button>
              </p>
            )}
          </>
        )}

        {notes.map((n) => (
          <p key={n} className={css.warn} role="status" data-testid="stk-note">
            {n}
          </p>
        ))}

        {stats && stats.droppedTotal !== null && (
          <fieldset className={css.group} data-testid="stk-dropped">
            <legend>Последняя сборка</legend>
            <p className={css.note}>
              По отношениям стека отброшено точек данных: {stats.droppedTotal.toLocaleString("ru-RU")}.
              {stats.dropped.length > 0 && (
                <>
                  {" "}
                  {stats.dropped
                    .map((x) => `«${domains.get(x.group ?? -1)?.name ?? `№${x.group}`}»: ${x.points?.toLocaleString("ru-RU") ?? "—"} из ${x.of?.toLocaleString("ru-RU") ?? "—"} (${RELATION_TITLES[x.relation]} группы «${domains.get(x.by ?? -1)?.name ?? `№${x.by}`}»)`)
                    .join("; ")}
                  .
                </>
              )}
            </p>
          </fieldset>
        )}
      </div>
      <div className={mod.footer}>
        <span className={mod.grow} data-testid="stk-saved">
          {s.saved ?? ""}
        </span>
        {s.error && (
          <span className={css.error} role="alert" data-testid="stk-error">
            {s.error}
          </span>
        )}
        <button type="button" className={css.btn} disabled={s.saving || !changed} title="Вернуть стек, как он записан в модели" data-testid="stk-reset" onClick={() => void resetStack()}>
          Сбросить
        </button>
        <button type="button" className={css.primary} disabled={s.saving || !!reason || !changed || s.modelId === null} title={reason ?? (changed ? "Записать порядок, отношения и разломы в модель" : "Нет изменений")} data-testid="stk-apply" onClick={() => void applyStack(app)}>
          Применить
        </button>
        <button type="button" className={css.btn} data-testid="stk-close" onClick={() => void requestClose(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
