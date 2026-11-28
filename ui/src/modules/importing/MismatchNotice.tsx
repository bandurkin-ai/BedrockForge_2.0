import type { FileEntry, MismatchOption, Wizard } from "./wizard";
import css from "./ImportWizard.module.css";

/**
 * The file found does not fit the section it was added to (Д-41 А2): what it looks like and why, and the ways out.
 * Nothing is assigned until the person chooses; this is the wizard's own plaque, not a browser dialog.
 */
export function MismatchNotice({ wizard, file }: { wizard: Wizard; file: FileEntry }) {
  const m = file.check?.mismatch;
  if (!m) return null;
  const options = wizard.mismatchOptions(file);
  const choose = (o: MismatchOption) => void wizard.resolveMismatch(file.id, o);
  return (
    <div className={css.mismatch} role="group" aria-label="Тип файла не подходит разделу" data-testid="import-mismatch">
      <b>{m.message}</b>
      <div>{m.reason}</div>
      <div className={css.note}>{m.hint}</div>
      <div className={css.choices}>
        {options.map((o, i) => (
          <button key={`${o.action}:${o.kind}`} type="button" className={i === 0 ? `${css.btn} ${css.primary}` : css.btn} disabled={file.busy} onClick={() => choose(o)}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
