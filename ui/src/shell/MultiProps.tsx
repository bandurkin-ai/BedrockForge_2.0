/**
 * «Свойства» of several chosen objects (Д-44 А5): objects of one kind show the fields they share — a value that
 * differs is «разные» (an empty field with a hint); a change is set on every chosen object and is one step that
 * «Отменить» takes back. Objects of different kinds have no properties: a plaque says so.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { styleIn } from "../modules/blocks/display";
import { sceneObjects, lineLayers } from "../modules/scene/objects";
import { viewStore } from "../modules/scene/bind";
import { itemExists } from "../modules/wireframes/named";
import { useApp } from "./AppContext";
import { applyAll, commonKind, commonValue, fieldsOf, kindCount, parseOpacity, readField, type FieldValue, type Lookup, type MultiField, type Step } from "./multiPropsModel";
import { NO_COLOR, normalizeColor } from "./props";
import { ColorField, useRun } from "./PropsParts";
import type { Selected } from "./selection";
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

/** A number typed with a comma; empty while the values differ (the hint «разные»). Saved on Enter or on leaving. */
function NumberField({ value, mixed, label, disabled, onCommit }: { value: number; mixed: boolean; label: string; disabled: boolean; onCommit: (v: number) => Promise<boolean> }) {
  const shown = mixed ? "" : String(value).replace(".", ",");
  const [text, setText] = useState(shown);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setText(shown), [shown]);
  const commit = () => {
    const r = parseOpacity(text);
    setError(r.error);
    if (r.value === null || (!mixed && r.value === value)) return;
    void onCommit(r.value).then((ok) => ok === false && setText(shown));
  };
  return (
    <>
      <input
        className={`${css.input} ${own.number}`}
        inputMode="decimal"
        value={text}
        placeholder={mixed ? "разные" : undefined}
        title={mixed ? MIXED_HINT : label}
        aria-label={label}
        aria-invalid={error !== null}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
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

export function MultiProps({ items }: { items: readonly Selected[] }) {
  const app = useApp();
  const hidden = useStore(app.store, (s) => s.view.hidden);
  const objects = useStore(sceneObjects, (o) => o);
  const view = useStore(viewStore, (k) => k);
  useRegistryVersion(app.registry); // availability of the commands follows the registry
  const { run, error } = useRun();
  const [undo, setUndo] = useState<{ text: string; steps: Step[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const kind = commonKind(items);

  const lookup = useMemo<Lookup>(() => {
    const layers = new Map(lineLayers(objects).map((l) => [l.id, l]));
    return {
      exists: itemExists,
      hidden,
      blockShown: (id) => styleIn(view, id).visible,
      layerColor: (id) => layers.get(id)?.color,
      wireframeColor: (id) => objects.wireframes.find((w) => w.id === id)?.color,
      rasterOpacity: (id) => objects.rasters.find((r) => r.id === id)?.opacity,
    };
  }, [hidden, objects, view]);

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

  /** Sets `value` on every chosen object, one after another; the previous values are kept as one step of undo. */
  const apply = async (f: MultiField, value: FieldValue): Promise<boolean> => {
    setBusy(true);
    setUndo(null);
    const r = await applyAll(run, f, kind, ids, lookup, value);
    setBusy(false);
    if (r.done > 0) setUndo({ text: `«${f.label}»: изменено ${kindCount(kind, r.done)}${r.done < r.total ? ` из ${r.total}` : ""}${r.skipped ? `; пропущено удалённых: ${r.skipped}` : ""}`, steps: r.undo });
    return r.done === r.total;
  };

  const revert = async () => {
    if (!undo) return;
    setBusy(true);
    for (const step of undo.steps) if (!(await run(step.command, step.args))) break;
    setBusy(false);
    setUndo(null);
  };

  return (
    <section className={css.panel} aria-label={`Свойства: выбрано ${items.length}`} data-testid="props-multi" data-kind={kind}>
      <header className={css.head}>
        <span className={css.kind}>Выбрано</span>
        <h4 className={css.name}>{kindCount(kind, items.length)}</h4>
      </header>
      {fields.length === 0 && <p className={css.note}>У этих объектов нет общих свойств для изменения: откройте каждый по отдельности.</p>}
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
                <button type="button" className={css.btn} disabled={off || (!common.mixed && common.value === null)} title="Объекты рисуются цветом по умолчанию" onClick={() => void apply(f, null)}>
                  Без цвета
                </button>
              </>
            )}
            {f.type === "number" && <NumberField value={typeof common.value === "number" ? common.value : 1} mixed={common.mixed} label={f.label} disabled={off} onCommit={(v) => apply(f, v)} />}
          </div>
        );
      })}
      {undo && (
        <div className={own.undo} data-testid="props-multi-undo">
          <span>{undo.text}</span>
          <button type="button" className={css.btn} disabled={busy} onClick={() => void revert()}>
            Отменить
          </button>
        </div>
      )}
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
