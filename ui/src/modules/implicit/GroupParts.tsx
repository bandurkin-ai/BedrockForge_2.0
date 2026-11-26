/**
 * The frame of the windows of the groups «Оболочки содержаний», «Жила», «Расширение каркаса» (ТЗ 8.9.4): the model (its version, «устарела» and
 * why — the graph of ADR 0025), the group to edit («Новая» — added last, the oldest), the words under a field (the first wrong thing of the form or
 * the refusal of the core with its `param`), the data check of a group (the points the core would take, without a solution), the buttons «Сохранить»
 * / «Сохранить и обновить» (the command of the kind; a refusal stands under its field). A window is floating, not modal; closing with unsaved
 * changes asks first (an own dialog).
 */
import { useEffect, useState, type ReactNode } from "react";
import { useApp } from "../../shell/AppContext";
import { askConfirm } from "../../shell/dialogs";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { requestClose, setCloseGuard } from "../../shell/windows";
import { issueAt } from "../estimation/formBase";
import { refusalOf, type PreviewAnswer, type Range } from "./api";
import type { GroupRuntime } from "./groupBase";
import { useSel } from "../estimation/hooks";
import mod from "./Wizard.module.css";
import { staleLine } from "./run";
import { implicitStore, loadDetails } from "./state";
import { rangeText } from "./WizardParts";
import { ensureDefaults, wizardStore } from "./wizardState";

/** The words under the field `at` (the local path of the group: «name», «codes», «params.thresholds» — and what is inside). */
export function FieldIssue<F>({ rt, at, id }: { rt: GroupRuntime<F>; at: string | string[]; id: string }) {
  const issue = useSel(rt.store, (s) => s.issue);
  const hit = issueAt(issue, ...(Array.isArray(at) ? at : [at]));
  if (!hit) return null;
  return (
    <p className={css.error} role="alert" data-testid={`grp-issue-${id}`} data-code={hit.code}>
      {hit.message} {hit.hint}
    </p>
  );
}

/** A typed text field: the label, the input, the range under it (as the core gives it), the refusal. */
export function TextRow<F>({ rt, label, value, onChange, id, at, placeholder, range, note, disabled }: { rt: GroupRuntime<F>; label: string; value: string; onChange: (v: string) => void; id: string; at: string; placeholder?: string; range?: Range; note?: string; disabled?: boolean }) {
  const hint = rangeText(range);
  return (
    <>
      <label className={css.formRow}>
        <span>{label}</span>
        <input className={css.input} value={value} placeholder={placeholder} inputMode="decimal" spellCheck={false} disabled={disabled} aria-label={label} title={hint ? `Диапазон: ${hint}` : undefined} data-testid={`grp-${id}`} onChange={(e) => onChange(e.target.value)} />
      </label>
      {(hint || note) && (
        <span className={css.note} data-testid={`grp-${id}-note`}>
          {[hint ? `Диапазон: ${hint}.` : "", note ?? ""].filter(Boolean).join(" ")}
        </span>
      )}
      <FieldIssue rt={rt} at={at} id={id} />
    </>
  );
}

const COUNTS: readonly [keyof PreviewAnswer["counts"], string][] = [
  ["contact", "контактов"],
  ["inside", "внутри"],
  ["outside", "снаружи"],
  ["manual", "ручных"],
  ["value", "значений"],
  ["merged", "слито совпавших"],
  ["conflicts", "пар совпавших точек с разным значением"],
  ["no_trajectory", "без траектории"],
  ["no_depth", "без глубины"],
];

/** «Данные группы»: the points the core would take for the form as typed (`POST …/points/preview`, no solution), before anything is written. */
export function DataCheck<F>({ rt }: { rt: GroupRuntime<F> }) {
  const s = useSel(rt.store, (x) => x);
  const info = useSel(implicitStore, (x) => (s.modelId === null ? undefined : x.details[s.modelId]));
  const [state, setState] = useState<{ busy: boolean; answer: PreviewAnswer | null; error: string | null }>({ busy: false, answer: null, error: null });
  const run = async () => {
    setState({ busy: true, answer: null, error: null });
    try {
      setState({ busy: false, answer: await rt.preview(), error: null });
    } catch (e) {
      const r = refusalOf(e);
      setState({ busy: false, answer: null, error: `${r.message} ${r.hint}`.trim() });
    }
  };
  const a = state.answer;
  const parts = a ? COUNTS.filter(([k]) => a.counts[k] > 0).map(([k, t]) => `${t} ${a.counts[k].toLocaleString("ru-RU")}`) : [];
  return (
    <div className={css.row}>
      <button type="button" className={css.btn} disabled={state.busy || !info} title="Показать, сколько точек данных возьмёт ядро для этой группы (решение не считается)" data-testid="grp-check" onClick={() => void run()}>
        Данные группы
      </button>
      {state.busy && <span className={css.note}>Чтение…</span>}
      {a && (
        <span className={css.note} data-testid="grp-check-answer">
          Точек данных {a.n.toLocaleString("ru-RU")}{parts.length ? `: ${parts.join(", ")}` : ""}.{a.notes.length ? ` ${a.notes.join(" ")}` : ""}
        </span>
      )}
      {state.error && (
        <span className={css.error} role="alert" data-testid="grp-check-error">
          {state.error}
        </span>
      )}
    </div>
  );
}

/** The frame of a window of a group. */
export function GroupFrame<F>({ rt, winId, children, intro }: { rt: GroupRuntime<F>; winId: string; children: ReactNode; intro?: string }) {
  const app = useApp();
  const s = useSel(rt.store, (x) => x);
  const info = useSel(implicitStore, (x) => (s.modelId === null ? undefined : x.details[s.modelId]));
  const row = useSel(implicitStore, (x) => (s.modelId === null ? undefined : x.rows.find((r) => r.id === s.modelId)));
  const readOnly = useSel(app.store, (x) => x.projectReadOnly);
  const defaults = useSel(wizardStore, (x) => x.defaults);
  useEffect(() => {
    void ensureDefaults();
  }, []);
  useEffect(() => {
    setCloseGuard(winId, async () => !rt.store.get().dirty || askConfirm({ title: "Закрыть окно", message: "Группа изменена и не сохранена. Закрыть окно без сохранения?", okLabel: "Закрыть", danger: true }));
    return () => setCloseGuard(winId, null);
  }, [winId, rt]);

  const groups = rt.groupsOf(info);
  const stale = staleLine(info ?? row);
  const reason = readOnly ? "Проект открыт только для чтения" : null;
  const conflict = s.issue?.reread ? s.issue : null;
  const submit = (update: boolean) => void rt.submit(app, update).catch((e: unknown) => app.store.set({ statusNote: errorText(e) }));

  return (
    <div className={mod.window} data-testid={`grp-${rt.spec.kind}`}>
      <div className={mod.body} style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
        <div className={mod.page} data-testid="grp-page">
          <fieldset className={css.group}>
            <legend>Модель</legend>
            <p className={mod.grid} data-testid="grp-model">
              {info ? `«${info.name}», версия ${info.version}` : s.modelId === null ? "Модель не выбрана." : "Чтение модели…"}
            </p>
            {stale && (
              <p className={css.warn} role="status" data-testid="grp-stale">
                {stale}
              </p>
            )}
            <label className={css.formRow}>
              <span>Группа</span>
              <select className={css.input} value={s.groupId ?? ""} aria-label="Группа для правки" data-testid="grp-choose" onChange={(e) => rt.choose(e.target.value ? Number(e.target.value) : null)}>
                <option value="">Новая группа (последняя, самая старая)</option>
                {groups.map((g) => (
                  <option key={g.id ?? g.name} value={g.id ?? ""}>
                    {g.name}
                  </option>
                ))}
              </select>
            </label>
            {intro && <span className={css.note}>{intro}</span>}
          </fieldset>
          {defaults.phase === "error" && (
            <p className={css.error} role="alert" data-testid="grp-defaults-error">
              {defaults.error}
            </p>
          )}
          {children}
          {s.issue && s.issue.param === null && !conflict && (
            <p className={css.error} role="alert" data-testid="grp-issue-general">
              {s.issue.message} {s.issue.hint}
            </p>
          )}
        </div>
      </div>
      {conflict && (
        <p className={`${css.error} ${mod.banner}`} role="alert" data-testid="grp-conflict">
          <span>
            {conflict.message} {conflict.hint}
          </span>
          <button type="button" className={css.btn} data-testid="grp-reread" onClick={() => void (s.modelId === null ? Promise.resolve(null) : loadDetails(s.modelId)).catch(() => null).then(() => rt.choose(s.groupId))}>
            Перечитать
          </button>
        </p>
      )}
      <div className={mod.footer}>
        <span className={mod.grow} data-testid="grp-saved">
          {s.saved ?? ""}
        </span>
        <button type="button" className={css.btn} disabled={s.saving || !!reason || s.modelId === null} title={reason ?? "Записать группу в модель"} data-testid="grp-save" onClick={() => submit(false)}>
          Сохранить
        </button>
        <button type="button" className={css.primary} disabled={s.saving || !!reason || s.modelId === null} title={reason ?? "Записать группу и сразу обновить модель"} data-testid="grp-save-update" onClick={() => submit(true)}>
          Сохранить и обновить
        </button>
        <button type="button" className={css.btn} data-testid="grp-close" onClick={() => void requestClose(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
