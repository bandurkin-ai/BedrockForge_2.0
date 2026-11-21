/**
 * «Настройка QAQC → Типы» (ADR 0020 §3, решение 4): the codes of TYPE of every data of the project with their
 * number of records → a type (several codes per type, compared without case and outer spaces); an unrecognised
 * code is marked and counted — it never becomes ORIG silently; a code not met yet may be added; «пустой код —
 * ORIG». «Сохранить» runs `qaqc.config {types}` (codes taken out — null).
 */
import { useState } from "react";
import css from "../../../shell/PropsParts.module.css";
import { QAQC_TYPES, TYPE_TITLES, type QaqcType } from "../setupApi";
import { dataStore } from "./dataStore";
import { codeRows, normCode, setCodeType, typesArg } from "./forms";
import { FormFooter, FormNotes } from "./FormParts";
import own from "./Setup.module.css";
import { useBaseForm } from "./useBaseForm";
import { useSnap } from "./useSnap";

export interface TypesForm {
  types: Record<string, QaqcType>;
  emptyIsOrig: boolean;
}

function TypeSelect({ value, label, onChange }: { value: QaqcType | null; label: string; onChange: (t: QaqcType | null) => void }) {
  return (
    <select className={own.cellInput} value={value ?? ""} aria-label={label} onChange={(e) => onChange((e.target.value || null) as QaqcType | null)}>
      <option value="">— не распознано —</option>
      {QAQC_TYPES.map((t) => (
        <option key={t} value={t}>
          {TYPE_TITLES[t]}
        </option>
      ))}
    </select>
  );
}

export function TypesTab({ onDirty }: { onDirty: (on: boolean) => void }) {
  const f = useBaseForm<TypesForm>("config", (s) => (s.config ? { types: s.config.types, emptyIsOrig: s.config.empty_is_orig } : null), onDirty);
  const data = useSnap(dataStore, (s) => s.list);
  const [code, setCode] = useState("");
  const [newType, setNewType] = useState<QaqcType | null>(null);
  if (!f.form) return <FormNotes error={f.st.error} loading />;
  const form = f.form;
  const rows = codeRows(data, form.types, form.emptyIsOrig);
  const unknown = rows.filter((r) => r.type === null && r.n > 0);
  const unknownN = unknown.reduce((n, r) => n + r.n, 0);
  const canEmpty = !!f.app.registry.get("qaqc.config")?.params?.empty_is_orig;
  const set = (c: string, t: QaqcType | null) => f.setForm({ ...form, types: setCodeType(form.types, c, t) });
  const known = rows.some((r) => normCode(r.code) === normCode(code));
  const save = () => f.save("qaqc.config", { types: JSON.stringify(typesArg(f.base?.types ?? {}, form.types)), ...(canEmpty ? { empty_is_orig: form.emptyIsOrig } : {}) });
  return (
    <div className={css.win} data-testid="qaqc-setup-types">
      <div className={css.winBody}>
        <FormNotes conflict={f.conflict} outside={f.outside} error={f.st.configError && !f.st.configError.param ? f.st.configError.message : null} />
        {unknownN > 0 ? (
          <p className={css.warn} data-testid="qaqc-types-unknown">
            Не распознано кодов: {unknown.length}, записей: {unknownN.toLocaleString("ru-RU")} — они не идут в расчёт. Назначьте им тип.
          </p>
        ) : (
          <p className={css.note}>Все коды файлов распознаны.</p>
        )}
        <div className={css.tableWrap} style={{ maxHeight: "none" }}>
          <table className={css.table}>
            <thead>
              <tr>
                <th>Код в файле</th>
                <th>Записей</th>
                <th>Тип</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={normCode(r.code)} className={r.type === null ? own.unknown : undefined} data-testid={`qaqc-type-${r.code}`} data-unknown={r.type === null ? "" : undefined}>
                  <td>{r.code || "(пусто)"}</td>
                  <td className={own.num}>{r.n.toLocaleString("ru-RU")}</td>
                  <td>{r.code ? <TypeSelect value={r.type} label={`Тип кода ${r.code}`} onChange={(t) => set(r.code, t)} /> : r.type ?? "не распознано"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className={css.inline}>
          <input className={css.input} style={{ maxWidth: 200 }} placeholder="новый код" value={code} aria-label="Новый код" onChange={(e) => setCode(e.target.value)} />
          <TypeSelect value={newType} label="Тип нового кода" onChange={setNewType} />
          <button
            type="button"
            className={css.btn}
            disabled={!code.trim() || known || !newType}
            title={known ? "Такой код уже есть" : !newType ? "Выберите тип" : undefined}
            onClick={() => {
              set(code, newType);
              setCode("");
            }}
          >
            Добавить код
          </button>
        </div>
        <label className={css.check} title={canEmpty ? undefined : "Параметр команды qaqc.config не объявлен"}>
          <input type="checkbox" checked={form.emptyIsOrig} disabled={!canEmpty} onChange={(e) => f.setForm({ ...form, emptyIsOrig: e.target.checked })} />
          Пустой код типа — исходная проба (ORIG)
        </label>
      </div>
      <FormFooter dirty={f.dirty} canSave reason={f.app.registry.reason("qaqc.config")} onUndo={f.undo} onSave={save} />
    </div>
  );
}
