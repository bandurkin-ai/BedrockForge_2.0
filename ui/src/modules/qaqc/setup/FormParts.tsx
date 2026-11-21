/** Shared parts of the forms of «Настройка QAQC»: the notes above a form and its footer with «Сохранить». */
import type { ReactNode } from "react";
import css from "../../../shell/PropsParts.module.css";

export function FormNotes({ conflict, outside, error, loading }: { conflict?: string | null; outside?: boolean; error?: string | null; loading?: boolean }) {
  return (
    <>
      {loading && !error && <p className={css.note}>Чтение настройки…</p>}
      {conflict && (
        <p className={css.warn} role="alert" data-testid="qaqc-setup-conflict">
          {conflict}
        </p>
      )}
      {outside && !conflict && <p className={css.warn}>Настройку изменили в другом окне: правки этой формы не будут записаны поверх. «Отменить правки» покажет сохранённые значения.</p>}
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
    </>
  );
}

export function FormFooter({ dirty, canSave, reason, onUndo, onSave, children }: { dirty: boolean; canSave: boolean; reason: string | null; onUndo: () => void; onSave: () => void; children?: ReactNode }) {
  return (
    <div className={css.footer}>
      {children}
      <span className={css.grow}>{dirty && <span className={css.note}>После сохранения отчёты QAQC по прежней настройке станут устаревшими, вкладка «QAQC» пересчитается.</span>}</span>
      <button type="button" className={css.btn} disabled={!dirty} onClick={onUndo}>
        Отменить правки
      </button>
      <button type="button" className={css.primary} disabled={!dirty || !canSave || reason !== null} title={reason ?? undefined} onClick={onSave} data-testid="qaqc-setup-save">
        Сохранить
      </button>
    </div>
  );
}
