/**
 * «Правка выбранного» (ТЗ 7.22; ADR 0015 §5): the numbers of an action on the selected lines — the vector of a
 * shift or a copy (ΔX, ΔY, ΔZ), the offset, the tolerance of thinning, the step of densifying, the passes of
 * smoothing. A comma and a dot both mark the decimals; the action is the command `lines.modify` with them.
 */
import { useState } from "react";
import { useApp } from "../../shell/AppContext";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import { editStore } from "./editState";
import { modifyBlocked, modifyInfo, readFields, type FieldKey, type ModifyAction } from "./modify";

export function ModifyWindow({ winId, action }: { winId: string; action: ModifyAction }) {
  const app = useApp();
  const info = modifyInfo(action);
  const session = useStore(editStore, (s) => s.session);
  const [values, setValues] = useState<Partial<Record<FieldKey, string>>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!info?.fields) return null;
  const why = session ? modifyBlocked(session, action) : "Редактор стрингов не открыт";
  const apply = async () => {
    const read = readFields(info.fields!, values);
    if ("error" in read) return setError(read.error);
    setBusy(true);
    const r = await app.registry.execute("lines.modify", { action, ...read.values });
    setBusy(false);
    if (!r.ok) return setError(r.result);
    closeWindow(winId);
  };
  return (
    <form
      className={css.panel}
      data-testid="lines-modify"
      onSubmit={(e) => {
        e.preventDefault();
        void apply();
      }}
    >
      <p className={css.note}>{info.action}</p>
      <div className={css.pair}>
        {info.fields.map((f, i) => (
          <label key={f.key} className={css.field} title={f.hint}>
            <span>{f.label}</span>
            <input
              className={css.input}
              data-testid={`modify-field-${f.key}`}
              autoFocus={i === 0}
              inputMode="decimal"
              value={values[f.key] ?? f.initial}
              aria-invalid={error?.startsWith(`«${f.label}»`) ? true : undefined}
              onChange={(e) => {
                setValues((v) => ({ ...v, [f.key]: e.target.value }));
                setError(null);
              }}
            />
          </label>
        ))}
      </div>
      {why && <p className={css.warn}>{why}</p>}
      {error && (
        <p className={css.error} role="alert" data-testid="modify-error">
          {error}
        </p>
      )}
      <div className={css.row}>
        <button type="submit" className={css.primary} data-testid="modify-apply" disabled={busy || !!why}>
          Применить
        </button>
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Отмена
        </button>
      </div>
    </form>
  );
}
