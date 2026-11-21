/**
 * The table of domains «код → α, Aп» (ADR 0021 §4, §11): a row per code, a value out of range is marked in its cell
 * (and never sent), «Добавить строку», «Заполнить кодами поля» (the codes met in the field, or the names of the
 * wireframes-domains), «Вставить из буфера» and a paste into the grid (Excel: tabs; CSV: «;»). Up to 1 000 rows.
 */
import { useState } from "react";
import { errorText } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { codesNote, type CodeList } from "./api";
import own from "./Intercepts.module.css";
import { ATTITUDES_MAX, fillCodes, parseAttitudeTable, type AttitudeRow } from "./params";

export interface DomainGridProps {
  rows: readonly AttitudeRow[];
  errors: Record<string, string>;
  onChange: (rows: AttitudeRow[]) => void;
  /** The codes to fill with; null — «Заполнить кодами поля» is unavailable (`fillReason` says why). */
  loadCodes: (() => Promise<CodeList>) | null;
  fillReason?: string;
}

export function DomainGrid({ rows, errors, onChange, loadCodes, fillReason }: DomainGridProps) {
  const [note, setNote] = useState<string | null>(null);
  const shownRows: AttitudeRow[] = rows.length ? [...rows] : [{ code: "", alpha: "", dipdir: "" }];
  const edit = (i: number, k: keyof AttitudeRow, v: string) => onChange(shownRows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const remove = (i: number) => onChange(shownRows.filter((_, j) => j !== i));
  const paste = (text: string): boolean => {
    const got = parseAttitudeTable(text);
    if (!got.length) return false;
    const kept = shownRows.filter((r) => r.code.trim() || r.alpha.trim() || r.dipdir.trim());
    const next = [...kept, ...got].slice(0, ATTITUDES_MAX);
    onChange(next);
    setNote(`Вставлено строк: ${next.length - kept.length}${kept.length + got.length > ATTITUDES_MAX ? ` (не больше ${ATTITUDES_MAX})` : ""}`);
    return true;
  };
  const fill = async () => {
    if (!loadCodes) return;
    setNote("Чтение кодов…");
    try {
      const got = await loadCodes();
      onChange(fillCodes(shownRows, got.codes));
      setNote(codesNote(got));
    } catch (e) {
      setNote(errorText(e));
    }
  };
  const fromClipboard = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (!paste(text)) setNote("В буфере обмена нет строк «код, α, Aп»");
    } catch {
      setNote("Буфер обмена недоступен: вставьте в таблицу сочетанием Ctrl+V");
    }
  };
  return (
    <div data-testid="intercepts-domains">
      <div
        className={own.gridWrap}
        onPaste={(e) => {
          const text = e.clipboardData.getData("text");
          if (/[\t;\n]/.test(text) && paste(text)) e.preventDefault();
        }}
      >
        <table className={own.grid}>
          <thead>
            <tr>
              <th>Код</th>
              <th>α, °</th>
              <th>Aп, °</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {shownRows.map((r, i) => (
              <tr key={i}>
                {(["code", "alpha", "dipdir"] as const).map((k) => {
                  const err = errors[`attitudes.${i}.${k}`];
                  return (
                    <td key={k}>
                      <input
                        className={`${css.input} ${err ? own.bad : ""}`}
                        aria-label={`${k === "code" ? "Код" : k === "alpha" ? "α" : "Aп"}, строка ${i + 1}`}
                        aria-invalid={err ? true : undefined}
                        title={err}
                        inputMode={k === "code" ? undefined : "decimal"}
                        placeholder={k === "alpha" ? "0–90" : k === "dipdir" ? "0–360" : ""}
                        value={r[k]}
                        onChange={(e) => edit(i, k, e.target.value)}
                      />
                    </td>
                  );
                })}
                <td>
                  <button type="button" className={css.iconBtn} aria-label={`Удалить строку ${i + 1}`} title="Удалить строку" onClick={() => remove(i)}>
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={own.gridTools}>
        <button type="button" className={css.btn} disabled={shownRows.length >= ATTITUDES_MAX} onClick={() => onChange([...shownRows, { code: "", alpha: "", dipdir: "" }])}>
          Добавить строку
        </button>
        <button type="button" className={css.btn} disabled={!loadCodes} title={loadCodes ? "Коды, встреченные в поле домена" : fillReason} onClick={() => void fill()}>
          Заполнить кодами поля
        </button>
        <button type="button" className={css.btn} title="Строки «код, α, Aп» из Excel или CSV" onClick={() => void fromClipboard()}>
          Вставить из буфера
        </button>
      </div>
      {errors.attitudes && <p className={own.errText}>{errors.attitudes}</p>}
      {note && <p className={css.note}>{note}</p>}
    </div>
  );
}
