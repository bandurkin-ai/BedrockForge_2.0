/**
 * «Свойства» of several chosen objects (Д-44 А5): objects of one kind show the fields they share — a value that
 * differs is «разные» (an empty field with a hint); a change is set on every chosen object and is one entry of the stack
 * «Отменить» (`propsUndo.ts`: the button, `props.undo`, Ctrl+Z). Objects of different kinds have no properties: a plaque says so.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { plannedStore, plannedViewStore } from "../modules/drillholes/plannedState";
import { sceneObjects } from "../modules/scene/objects";
import { viewStore } from "../modules/scene/bind";
import { swathsStore } from "../modules/swath/state";
import { tablesStore } from "../modules/tables/state";
import { trendsStore } from "../modules/trends/state";
import { viewsStore } from "../modules/views/state";
import { useApp } from "./AppContext";
import { buildLookup } from "./multiLookup";
import { applyAll, commonKind, commonValue, fieldsOf, kindCount, parseNumber, readField, whyNoFields, type FieldValue, type Lookup, type MultiField } from "./multiPropsModel";
import { NO_COLOR, normalizeColor } from "./props";
import { pushUndo, undoStore } from "./propsUndo";
import { ColorField, useRun } from "./PropsParts";
import type { RowRef } from "./selection";
import { useRegistryVersion, useStore } from "./useStore";
import css from "./PropsParts.module.css";
import own from "./MultiProps.module.css";

const MIXED_HINT = "У выбранных объектов значения разные: новое значение применится ко всем";

/** A flag that can be «разные»: a check box in the middle state. */
function MixedCheck({ checked, mixed, disabled, title, label, onChange }: { checked: boolean; mixed: boolean; disabled: boolean; title: string; label: string; onChange: (next: boolean) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = mixed;
  }, [mixed, checked]);
  return (
    <label className={css.check} title={mixed ? MIXED_HINT : title}>
      <input ref={ref} type="checkbox" checked={checked && !mixed} disabled={disabled} aria-checked={mixed ? "mixed" : checked} aria-label={label} onChange={(e) => onChange(e.target.checked)} />
      {mixed ? <span className={own.mixed}>разные</span> : checked ? "да" : "нет"}
    </label>
  );
}

/** A text or a number typed with a comma; empty while the values differ (the hint «разные»). Saved on Enter or on leaving. */
function TextField({ shown, current, mixed, label, disabled, parse, onCommit, numeric }: { shown: string; current: FieldValue; mixed: boolean; label: string; disabled: boolean; parse: (text: string) => { value: FieldValue; error: string | null }; onCommit: (v: FieldValue) => Promise<boolean>; numeric: boolean }) {
  const [text, setText] = useState(mixed ? "" : shown);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setText(mixed ? "" : shown);
    setDirty(false);
  }, [shown, mixed]);
  const commit = () => {
    if (!dirty) return; // leaving a field that was not typed in sets nothing (an empty «разные» is not a value)
    const r = parse(text);
    setError(r.error);
    if (r.error || (r.value === null && numeric)) return;
    setDirty(false);
    if (!mixed && r.value === current) return; // typed back to what it was: nothing to set, nothing to take back
    void onCommit(r.value).then((ok) => ok === false && setText(mixed ? "" : shown));
  };
  return (
    <>
      <input
        className={`${css.input} ${numeric ? own.number : own.text}`}
        inputMode={numeric ? "decimal" : undefined}
        value={text}
        placeholder={mixed ? "разные" : undefined}
        title={mixed ? MIXED_HINT : label}
        aria-label={label}
        aria-invalid={error !== null}
        disabled={disabled}
        onChange={(e) => {
          setText(e.target.value);
          setDirty(true);
        }}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
      />
      {error && (
        <span className={css.error} role="alert">
          {error}
        </span>
      )}
    </>
  );
}

const asText = (v: FieldValue): string => (typeof v === "number" ? String(v).replace(".", ",") : typeof v === "string" ? v : "");

export function MultiProps({ items }: { items: readonly RowRef[] }) {
  const app = useApp();
  const hidden = useStore(app.store, (s) => s.view.hidden);
  const objects = useStore(sceneObjects, (o) => o);
  const view = useStore(viewStore, (k) => k);
  const planned = useStore(plannedViewStore, (v) => v);
  const sets = useStore(tablesStore, (s) => s.sets);
  const trends = useStore(trendsStore, (s) => s.trends);
  const plannedRows = useStore(plannedStore, (s) => s.rows);
  const swaths = useStore(swathsStore, (s) => s.swaths);
  const views = useStore(viewsStore, (s) => s.views);
  const last = useStore(undoStore, (s) => s.entries.at(-1) ?? null);
  useRegistryVersion(app.registry); // availability of the commands follows the registry
  const { run, error } = useRun();
  const [busy, setBusy] = useState(false);
  const [undoError, setUndoError] = useState<string | null>(null);
  const kind = commonKind(items);

  const lookup = useMemo<Lookup>(
    () => buildLookup({ hidden, objects, view, planned, lists: { sets, trends, planned: plannedRows, swaths, views } }),
    [hidden, objects, view, planned, sets, trends, plannedRows, swaths, views],
  );

  if (!kind) {
    return (
      <section className={css.panel} aria-label="Свойства: разные типы" data-testid="props-multi-mixed">
        <p className={own.plaque} role="status">
          Выбраны объекты разных типов: {items.length}
        </p>
        <p className={css.note}>Свойства недоступны. Выберите объекты одного типа: Ctrl — добавить или убрать, Shift — диапазон раздела, Esc — снять выбор.</p>
      </section>
    );
  }

  const fields = fieldsOf(kind);
  const ids = items.map((x) => x.id);

  /** Sets `value` on every chosen object, one after another; the previous values are put on the stack of «Отменить». */
  const apply = async (f: MultiField, value: FieldValue): Promise<boolean> => {
    setBusy(true);
    setUndoError(null);
    const r = await applyAll(run, f, kind, ids, lookup, value);
    setBusy(false);
    if (r.done > 0) pushUndo({ text: `«${f.label}»: изменено ${kindCount(kind, r.done)}${r.done < r.total ? ` из ${r.total}` : ""}${r.skipped ? `; пропущено удалённых: ${r.skipped}` : ""}`, steps: r.undo });
    return r.done === r.total;
  };

  /** The same step as Ctrl+Z and the command `props.undo`: the last change of the stack. */
  const revert = async () => {
    setBusy(true);
    const r = await app.registry.execute("props.undo");
    setBusy(false);
    setUndoError(r.ok ? null : r.result);
  };

  return (
    <section className={css.panel} aria-label={`Свойства: выбрано ${items.length}`} data-testid="props-multi" data-kind={kind}>
      <header className={css.head}>
        <span className={css.kind}>Выбрано</span>
        <h4 className={css.name}>{kindCount(kind, items.length)}</h4>
      </header>
      {fields.length === 0 && <p className={css.note}>{whyNoFields(kind)}</p>}
      {fields.map((f) => {
        const read = readField(f, kind, ids, lookup);
        if (!read.length) return null;
        const common = commonValue(read.map((r) => r.value));
        const reason = app.registry.reason(f.command(kind));
        const off = busy || reason !== null;
        const title = reason ? `${f.label}\nНедоступно: ${reason}` : f.label;
        return (
          <div key={f.key} className={css.row} data-testid={`props-multi-${f.key}`} data-mixed={common.mixed}>
            <span className={own.label}>{f.label}</span>
            {f.type === "check" && <MixedCheck checked={common.value === true} mixed={common.mixed} disabled={off} title={title} label={f.label} onChange={(next) => void apply(f, next)} />}
            {f.type === "color" && (
              <>
                <ColorField value={normalizeColor(common.value) ?? NO_COLOR} label={`${f.label} выбранных объектов`} disabled={off} onCommit={(color) => apply(f, color)} />
                {common.mixed && (
                  <span className={own.mixed} title={MIXED_HINT}>
                    разные
                  </span>
                )}
                {f.nullable !== false && (
                  <button type="button" className={css.btn} disabled={off || (!common.mixed && common.value === null)} title="Объекты рисуются цветом по умолчанию" onClick={() => void apply(f, null)}>
                    Без цвета
                  </button>
                )}
              </>
            )}
            {f.type === "number" && f.range && (
              <TextField
                numeric
                shown={asText(typeof common.value === "number" ? common.value : 1)}
                current={common.value}
                mixed={common.mixed}
                label={f.label}
                disabled={off}
                parse={(t) => parseNumber(t, f.range!, f.label.replace(/, px$/, ""), f.example)}
                onCommit={(v) => apply(f, v)}
              />
            )}
            {f.type === "text" && <TextField numeric={false} shown={asText(common.value)} current={common.value ?? ""} mixed={common.mixed} label={f.label} disabled={off} parse={(t) => ({ value: t.trim(), error: null })} onCommit={(v) => apply(f, v)} />}
          </div>
        );
      })}
      {last && (
        <div className={own.undo} data-testid="props-multi-undo">
          <span>{last.text}</span>
          <button type="button" className={css.btn} disabled={busy} title="Отменить последнее изменение свойств (Ctrl+Z)" onClick={() => void revert()}>
            Отменить
          </button>
        </div>
      )}
      {(error || undoError) && (
        <p className={css.error} role="alert">
          {undoError ?? error}
        </p>
      )}
    </section>
  );
}
