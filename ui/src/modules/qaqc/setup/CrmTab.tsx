/**
 * «Настройка QAQC → CRM» (ADR 0020 §3): the reference of standards — CRM_ID, element, expected μ, σ, unit, source;
 * a row with μ ≤ 0, σ ≤ 0, an empty key or a repeated CRM_ID × element (without case) is marked and the reference is
 * not sent; a refusal of the core (`bad_crm {row}`) marks its row. «Загрузить из файла…» reads a CSV / XLSX into the
 * form (`POST /crm/read`, nothing is written). «Сохранить» runs `qaqc.crm {rows, remove}`. Pages of 200 rows.
 */
import { useState } from "react";
import { pickFilesIn } from "../../../shell/lastFolder";
import { errorText } from "../../../shell/props";
import css from "../../../shell/PropsParts.module.css";
import { readCrmFile, type CrmProblem } from "../setupApi";
import { checkCrm, crmArgs, crmKey, crmTexts, MAX_CRM, mergeRead, type CrmText } from "./forms";
import { FormFooter, FormNotes } from "./FormParts";
import own from "./Setup.module.css";
import { useBaseForm } from "./useBaseForm";

export const CRM_PAGE = 200;
const EMPTY_ROW: CrmText = { crm_id: "", element: "", mean: "", sd: "", unit: "", source: "" };
const COLS: [keyof CrmText, string, boolean][] = [
  ["crm_id", "CRM_ID", false],
  ["element", "Элемент", false],
  ["mean", "μ", true],
  ["sd", "σ", true],
  ["unit", "Единица", false],
  ["source", "Источник", false],
];

/** The rows of a file the core refused (`POST /crm/read` `problems`, `row` from 0), up to `max`, then how many more. */
export function crmProblemLines(problems: readonly CrmProblem[], max = 10): string[] {
  const out = problems.slice(0, max).map((p) => (/строка\s+\d/i.test(p.message) ? p.message : `Строка ${p.row + 1} файла: ${p.message}`));
  if (problems.length > max) out.push(`…и ещё ${(problems.length - max).toLocaleString("ru-RU")}`);
  return out;
}

/** The errors of the rows of the form: its own check, then the row a refusal of the core names. */
export function crmErrors(rows: readonly CrmText[], server: { key: string | null; message: string } | null): Map<number, string> {
  const { errors } = checkCrm(rows);
  if (server?.key) {
    const i = rows.findIndex((r) => crmKey(r) === server.key);
    if (i >= 0 && !errors.has(i)) errors.set(i, server.message);
  }
  return errors;
}

export function CrmTab({ onDirty }: { onDirty: (on: boolean) => void }) {
  const f = useBaseForm<CrmText[]>("crm", (s) => (s.crm ? crmTexts(s.crm.rows) : null), onDirty);
  const [page, setPage] = useState(0);
  const [readNote, setReadNote] = useState<string | null>(null);
  const [readProblems, setReadProblems] = useState<string[]>([]);
  if (!f.form) return <FormNotes error={f.st.error} loading />;
  const rows = f.form;
  const checked = checkCrm(rows);
  const errors = crmErrors(rows, f.st.crmError);
  const pages = Math.max(1, Math.ceil(rows.length / CRM_PAGE));
  const at = Math.min(page, pages - 1);
  const shown = rows.slice(at * CRM_PAGE, (at + 1) * CRM_PAGE);
  const patch = (i: number, p: Partial<CrmText>) => f.setForm(rows.map((r, j) => (j === i ? { ...r, ...p } : r)));
  const load = async () => {
    const [file] = await pickFilesIn("tables", false);
    if (!file) return;
    try {
      const read = await readCrmFile(file);
      f.setForm(mergeRead(rows, read.rows));
      const bad = read.problems.length ? ` С ошибками: ${read.problems.length.toLocaleString("ru-RU")} — исправьте их в таблице.` : "";
      setReadNote(`Из файла прочитано строк: ${read.rows.length.toLocaleString("ru-RU")}.${bad} Проверьте и сохраните.`);
      setReadProblems(crmProblemLines(read.problems));
    } catch (e) {
      setReadNote(errorText(e));
      setReadProblems([]);
    }
  };
  const save = () => {
    if (!checked.rows) return;
    const { rows: all, remove } = crmArgs(f.st.crm?.rows ?? [], checked.rows);
    f.save("qaqc.crm", { rows: JSON.stringify(all), remove: JSON.stringify(remove) });
  };
  const serverGeneral = f.st.crmError && !f.st.crmError.key ? f.st.crmError.message : null;
  const firstBad = [...errors.keys()].sort((a, b) => a - b)[0];
  return (
    <div className={css.win} data-testid="qaqc-setup-crm">
      <div className={css.winBody}>
        <FormNotes conflict={f.conflict} outside={f.outside} error={serverGeneral ?? checked.general} />
        <div className={own.bar}>
          <span className={css.note}>
            Строк: {rows.length.toLocaleString("ru-RU")} из {MAX_CRM.toLocaleString("ru-RU")}
          </span>
          <span className={own.grow} />
          <button type="button" className={css.btn} onClick={() => void load()}>
            Загрузить из файла…
          </button>
          <button
            type="button"
            className={css.btn}
            onClick={() => {
              f.setForm([...rows, { ...EMPTY_ROW }]);
              setPage(Math.floor(rows.length / CRM_PAGE));
            }}
          >
            Добавить строку
          </button>
        </div>
        {readNote && <p className={css.note}>{readNote}</p>}
        {readProblems.length > 0 && (
          <ul className={css.warn} data-testid="qaqc-crm-problems" style={{ margin: 0, paddingLeft: 18 }}>
            {readProblems.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        )}
        {errors.size > 0 && (
          <p className={css.error} data-testid="qaqc-crm-errors">
            Строк с ошибками: {errors.size}; первая — строка {firstBad + 1}: {errors.get(firstBad)}
          </p>
        )}
        <div className={css.tableWrap} style={{ maxHeight: "none" }}>
          <table className={css.table}>
            <thead>
              <tr>
                <th className={css.rowNo}>№</th>
                {COLS.map(([k, t]) => (
                  <th key={k}>{t}</th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody>
              {shown.map((r, j) => {
                const i = at * CRM_PAGE + j;
                const err = errors.get(i);
                return (
                  <tr key={i} className={err ? own.bad : undefined} title={err} data-testid={`qaqc-crm-${i}`} data-error={err}>
                    <td className={css.rowNo}>{i + 1}</td>
                    {COLS.map(([k, t, num]) => (
                      <td key={k}>
                        <input className={`${own.cellInput} ${num ? own.num : ""}`} inputMode={num ? "decimal" : undefined} value={r[k]} aria-label={`CRM, строка ${i + 1}: ${t}`} aria-invalid={!!err} onChange={(e) => patch(i, { [k]: e.target.value })} />
                      </td>
                    ))}
                    <td>
                      <button type="button" className={css.btn} onClick={() => f.setForm(rows.filter((_, x) => x !== i))}>
                        Удалить
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {pages > 1 && (
          <div className={css.pager}>
            <button type="button" className={css.btn} disabled={at === 0} onClick={() => setPage(at - 1)}>
              Назад
            </button>
            <span>
              Страница {at + 1} из {pages}
            </span>
            <button type="button" className={css.btn} disabled={at >= pages - 1} onClick={() => setPage(at + 1)}>
              Вперёд
            </button>
          </div>
        )}
      </div>
      <FormFooter dirty={f.dirty} canSave={!!checked.rows} reason={f.app.registry.reason("qaqc.crm")} onUndo={f.undo} onSave={save} />
    </div>
  );
}
