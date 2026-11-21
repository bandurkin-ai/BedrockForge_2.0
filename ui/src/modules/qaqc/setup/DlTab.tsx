/**
 * «Настройка QAQC → DL» (ADR 0020 §3): detection limits by element × laboratory or «любая». The order of lookup —
 * the DL field of a record, (element, laboratory), (element, any); without DL a blank is «не оценён» (not «норма»)
 * and the cut-off of duplicates by k_д·DL is not applied. «Сохранить» runs `qaqc.config {dl}`.
 */
import css from "../../../shell/PropsParts.module.css";
import { dataStore } from "./dataStore";
import { checkDl, dlTexts, type DlText } from "./forms";
import { FormFooter, FormNotes } from "./FormParts";
import own from "./Setup.module.css";
import { useBaseForm } from "./useBaseForm";
import { useSnap } from "./useSnap";

export const UNITS = ["г/т", "ppm", "ppb", "%"];
const EMPTY_ROW: DlText = { element: "", lab: "", value: "", unit: "" };

/** The elements of every data with their units (for the lists of the form). */
export function knownElements(list: readonly { elements: { name: string; unit: string | null }[] }[]): { name: string; unit: string }[] {
  const by = new Map<string, { name: string; unit: string }>();
  for (const d of list) for (const e of d.elements) if (!by.has(e.name.toUpperCase())) by.set(e.name.toUpperCase(), { name: e.name, unit: e.unit ?? "" });
  return [...by.values()];
}

export function DlTab({ onDirty }: { onDirty: (on: boolean) => void }) {
  const f = useBaseForm<DlText[]>("config", (s) => (s.config ? dlTexts(s.config.dl) : null), onDirty);
  const data = useSnap(dataStore, (s) => s.list);
  if (!f.form) return <FormNotes error={f.st.error} loading />;
  const rows = f.form;
  const { rows: checked, errors } = checkDl(rows);
  const elements = knownElements(data);
  const patch = (i: number, p: Partial<DlText>) => f.setForm(rows.map((r, j) => (j === i ? { ...r, ...p } : r)));
  const save = () => checked && f.save("qaqc.config", { dl: JSON.stringify(checked) });
  return (
    <div className={css.win} data-testid="qaqc-setup-dl">
      <div className={css.winBody}>
        <FormNotes conflict={f.conflict} outside={f.outside} error={f.st.configError && !f.st.configError.param ? f.st.configError.message : null} />
        <p className={css.note}>DL ищется так: поле DL записи → элемент и лаборатория → элемент и «любая». Без DL бланк «не оценён», отсев пар дубликатов по k_д·DL не применяется.</p>
        <div className={css.tableWrap} style={{ maxHeight: "none" }}>
          <table className={css.table}>
            <thead>
              <tr>
                <th>Элемент</th>
                <th>Лаборатория</th>
                <th>DL</th>
                <th>Единица</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const err = errors.get(i);
                return (
                  <tr key={i} className={err ? own.bad : undefined} title={err} data-testid={`qaqc-dl-${i}`}>
                    <td>
                      <input className={own.cellInput} list="qaqc-dl-elements" value={r.element} aria-label={`DL, строка ${i + 1}: элемент`} aria-invalid={err === "Не задан элемент"} onChange={(e) => patch(i, { element: e.target.value, ...(r.unit ? {} : { unit: elements.find((x) => x.name.toUpperCase() === e.target.value.trim().toUpperCase())?.unit ?? "" }) })} />
                    </td>
                    <td>
                      <input className={own.cellInput} value={r.lab} placeholder="любая" aria-label={`DL, строка ${i + 1}: лаборатория`} onChange={(e) => patch(i, { lab: e.target.value })} />
                    </td>
                    <td>
                      <input className={`${own.cellInput} ${own.num}`} inputMode="decimal" value={r.value} aria-label={`DL, строка ${i + 1}: значение`} aria-invalid={err === "DL — число больше 0"} onChange={(e) => patch(i, { value: e.target.value })} />
                    </td>
                    <td>
                      <input className={own.cellInput} list="qaqc-dl-units" value={r.unit} aria-label={`DL, строка ${i + 1}: единица`} onChange={(e) => patch(i, { unit: e.target.value })} />
                    </td>
                    <td>
                      <button type="button" className={css.btn} onClick={() => f.setForm(rows.filter((_, j) => j !== i))}>
                        Удалить
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {errors.size > 0 && <p className={css.error}>{[...errors].map(([i, e]) => `строка ${i + 1}: ${e}`).slice(0, 3).join("; ")}</p>}
        <datalist id="qaqc-dl-units">
          {UNITS.map((u) => (
            <option key={u} value={u} />
          ))}
        </datalist>
        <datalist id="qaqc-dl-elements">
          {elements.map((e) => (
            <option key={e.name} value={e.name} />
          ))}
        </datalist>
        <button type="button" className={css.btn} style={{ alignSelf: "flex-start" }} onClick={() => f.setForm([...rows, { ...EMPTY_ROW }])}>
          Добавить строку
        </button>
      </div>
      <FormFooter dirty={f.dirty} canSave={!!checked} reason={f.app.registry.reason("qaqc.config")} onUndo={f.undo} onSave={save} />
    </div>
  );
}
