import { Fragment, useEffect, useState } from "react";
import { Icon } from "./icons";

export type AttrValue = string | number | boolean | null;

const show = (v: AttrValue | undefined) => (v === null || v === undefined || v === "" ? "—" : typeof v === "boolean" ? (v ? "да" : "нет") : typeof v === "number" ? v.toLocaleString("ru-RU", { maximumFractionDigits: 6 }) : v);
const asText = (v: AttrValue | undefined) => (v === null || v === undefined ? "" : typeof v === "boolean" ? (v ? "да" : "нет") : String(v));
const NUMBER = /^[-+]?(\d+[.,]?\d*|[.,]\d+)([eE][-+]?\d+)?$/;

/**
 * The value typed for an attribute: empty → null; a number where the old value was a number (or a new
 * attribute that looks like one); yes/no where it was yes/no; text otherwise.
 */
export function parseAttr(text: string, old: AttrValue | undefined): AttrValue {
  const t = text.trim();
  if (!t) return null;
  if (typeof old === "boolean") {
    const l = t.toLowerCase();
    if (["да", "true", "1", "yes"].includes(l)) return true;
    if (["нет", "false", "0", "no"].includes(l)) return false;
  }
  if ((typeof old === "number" || old === undefined || old === null) && NUMBER.test(t)) return Number(t.replace(",", "."));
  return t;
}

/** Attributes of an object (a wireframe): shown as a list, edited as a table — rename, change, add, delete. */
export function AttributeEditor({ attributes, onSave }: {
  attributes: Record<string, AttrValue>;
  onSave: (next: Record<string, AttrValue>) => Promise<unknown>;
}) {
  const [rows, setRows] = useState<{ key: string; value: string; old?: AttrValue }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const entries = Object.entries(attributes);
  useEffect(() => setRows(null), [JSON.stringify(attributes)]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = () => {
    setError(null);
    setRows(entries.map(([key, v]) => ({ key, value: asText(v), old: v })));
  };
  const save = async () => {
    if (!rows) return;
    const out: Record<string, AttrValue> = {};
    for (const r of rows) {
      const key = r.key.trim();
      if (!key && !r.value.trim()) continue;
      if (!key) return setError("У атрибута должно быть название");
      if (key in out) return setError(`Атрибут «${key}» указан дважды`);
      out[key] = parseAttr(r.value, r.old);
    }
    setBusy(true);
    try {
      await onSave(out);
      setRows(null);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const set = (i: number, patch: Partial<{ key: string; value: string }>) => setRows((list) => list!.map((r, k) => (k === i ? { ...r, ...patch } : r)));

  return (
    <div className="attr-editor">
      <div className="attr-head">
        <h2 className="dock-title">Атрибуты{entries.length ? ` (${entries.length})` : ""}</h2>
        {!rows && <button className="small-btn" onClick={start} title="Изменить, добавить или удалить атрибуты"><Icon name="edit" size={13} /> Изменить</button>}
      </div>
      {!rows ? (
        entries.length ? (
          <dl className="props-list">
            {entries.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{show(v)}</dd></Fragment>)}
          </dl>
        ) : <p className="muted small">Атрибутов нет. «Изменить» — добавить.</p>
      ) : (
        <>
          <table className="attr-table">
            <thead><tr><th>Атрибут</th><th>Значение</th><th /></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td><input value={r.key} aria-label="Название атрибута" placeholder="название" onChange={(e) => set(i, { key: e.target.value })} /></td>
                  <td><input value={r.value} aria-label={`Значение: ${r.key}`} placeholder="пусто" onChange={(e) => set(i, { value: e.target.value })}
                    onKeyDown={(e) => e.key === "Enter" && void save()} /></td>
                  <td><button className="icon" title="Удалить атрибут" aria-label={`Удалить ${r.key}`} onClick={() => setRows(rows.filter((_, k) => k !== i))}><Icon name="trash" size={13} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="attr-actions">
            <button className="small-btn" onClick={() => setRows([...rows, { key: "", value: "" }])}><Icon name="plus" size={13} /> Атрибут</button>
            <span className="spacer" />
            <button className="small-btn" disabled={busy} onClick={() => { setRows(null); setError(null); }}>Отмена</button>
            <button className="small-btn primary" disabled={busy} onClick={() => void save()}>{busy ? "Сохраняю…" : "Сохранить"}</button>
          </div>
          <span className="muted small">Числа сохраняются числами, «да/нет» — логическими, пустое значение — пусто.</span>
        </>
      )}
      {error && <div className="alert error small">{error}</div>}
    </div>
  );
}

/** Values of one block: every field of the model, edited in place (numbers, or a category from the list or a new one). */
export function BlockValuesEditor({ values, fields, onSave }: {
  values: Record<string, number | string | null>;
  fields: { name: string; kind: "number" | "category"; labels?: string[] }[];
  onSave: (changed: Record<string, number | string | null>) => Promise<unknown>;
}) {
  const [edit, setEdit] = useState<Record<string, string> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setEdit(null), [JSON.stringify(values)]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    if (!edit) return;
    const changed: Record<string, number | string | null> = {};
    for (const f of fields) {
      const text = (edit[f.name] ?? "").trim();
      if (text === asText(values[f.name])) continue;
      if (f.kind === "number") {
        if (text && !NUMBER.test(text)) return setError(`«${f.name}»: нужно число`);
        changed[f.name] = text ? Number(text.replace(",", ".")) : null;
      } else changed[f.name] = text || null;
    }
    if (!Object.keys(changed).length) return setEdit(null);
    setBusy(true);
    try {
      await onSave(changed);
      setEdit(null);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="attr-editor">
      {!edit ? (
        <>
          <dl className="props-list">
            {fields.map((f) => <Fragment key={f.name}><dt>{f.name}</dt><dd>{show(values[f.name])}</dd></Fragment>)}
          </dl>
          <button className="small-btn" onClick={() => { setError(null); setEdit(Object.fromEntries(fields.map((f) => [f.name, asText(values[f.name])]))); }}>
            <Icon name="edit" size={13} /> Изменить значения блока
          </button>
        </>
      ) : (
        <>
          <table className="attr-table">
            <tbody>
              {fields.map((f) => (
                <tr key={f.name}>
                  <td className="attr-name" title={f.kind === "category" ? "категория: выберите из списка или введите новую" : "число"}>{f.name}</td>
                  <td>
                    <input value={edit[f.name] ?? ""} inputMode={f.kind === "number" ? "decimal" : undefined} placeholder="пусто"
                      list={f.kind === "category" ? `blk-${f.name}` : undefined} aria-label={`Значение блока: ${f.name}`}
                      onChange={(e) => setEdit({ ...edit, [f.name]: e.target.value })} onKeyDown={(e) => e.key === "Enter" && void save()} />
                    {f.kind === "category" && <datalist id={`blk-${f.name}`}>{(f.labels ?? []).map((l) => <option key={l} value={l} />)}</datalist>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="attr-actions">
            <span className="spacer" />
            <button className="small-btn" disabled={busy} onClick={() => { setEdit(null); setError(null); }}>Отмена</button>
            <button className="small-btn primary" disabled={busy} onClick={() => void save()}>{busy ? "Сохраняю…" : "Сохранить"}</button>
          </div>
        </>
      )}
      {error && <div className="alert error small">{error}</div>}
    </div>
  );
}
