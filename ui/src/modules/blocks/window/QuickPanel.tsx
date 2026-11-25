/**
 * The tab «Быстрый ограничитель» of the window «Блочная модель · …» (ТЗ 8.7.6; ADR 0023 §11, decision 16): conditions «поле —
 * оператор — значение» (the categories by their labels, «в списке» one value per line), joined by «И» or «ИЛИ», «обратный»
 * shows what the conditions do not pass. It applies at once, together with the filter of the library (both). The rows are
 * typed, so they are kept as drafts and the style is written when every row is right (`quick.ts`); a wrong row tells why under
 * itself and nothing is written. The state is the style of the model in the view of the project.
 */
import { useEffect, useRef, useState } from "react";
import css from "../../../shell/PropsParts.module.css";
import { NO_VALUE_OPS, type CondOp, type DraftCond } from "../filters/constructor";
import { quickText } from "../showCommands";
import mod from "./Window.module.css";
import { currentStyle } from "./actions";
import { clearQuick, commitQuickRows, setInvert, setJoin } from "./edits";
import { useInfo, useModel, useStyle } from "./hooks";
import { Issue, TextField } from "./parts";
import { blankRow, itemsKey, labelsOf, OPERATORS, rowIssues, rowsOf, type RowIssue } from "./quick";

function ListField({ value, onCommit, testId }: { value: string; onCommit: (text: string) => void; testId: string }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <textarea
      className={`${css.input} ${mod.list}`}
      rows={Math.min(Math.max(draft.split("\n").length, 2), 6)}
      value={draft}
      placeholder="Значения, по одному в строке"
      aria-label="Значения"
      data-testid={testId}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => draft !== value && onCommit(draft)}
    />
  );
}

export function QuickPanel({ id }: { id: number }) {
  const style = useStyle(id);
  const { model } = useModel(id);
  const info = useInfo(id);
  const q = style.quick;
  const key = itemsKey(q);
  const [rows, setRows] = useState<DraftCond[]>(() => rowsOf(q));
  const [failure, setFailure] = useState<string | null>(null);
  const synced = useRef(key);
  // the restrictor changed elsewhere (`blocks.quick`, another window, a project that was opened): the rows show it
  useEffect(() => {
    if (key === synced.current) return;
    synced.current = key;
    setRows(rowsOf(currentStyle(id).quick));
  }, [key, id]);
  const fieldNames = model?.fields.map((f) => f.name) ?? [];

  const apply = (next: DraftCond[]) => {
    setRows(next);
    const r = commitQuickRows(id, next, model);
    setFailure(r.failure?.text ?? null);
    if (r.written) synced.current = itemsKey(currentStyle(id).quick);
  };
  const edit = (i: number, patch: Partial<DraftCond>) => apply(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const clear = () => {
    const bad = clearQuick(id);
    setRows([]);
    setFailure(bad?.text ?? null);
    synced.current = itemsKey(currentStyle(id).quick);
  };
  const join = (j: "and" | "or") => {
    const bad = setJoin(id, j);
    setFailure(bad?.text ?? null);
  };
  const invert = (on: boolean) => {
    const bad = setInvert(id, on);
    setFailure(bad?.text ?? null);
  };
  const shownIssues = rowIssues(rows, model);
  const header = info?.header;

  return (
    <div className={mod.panel} data-testid="quick-panel">
      <fieldset className={css.group}>
        <legend>Условия</legend>
        <datalist id={`quick-fields-${id}`}>
          {fieldNames.map((f) => (
            <option key={f} value={f} />
          ))}
        </datalist>
        {rows.map((r, i) => {
          const mine = shownIssues.filter((x) => x.row === i);
          const labels = labelsOf(model, r.field);
          const here = (part: RowIssue["part"]) => mine.find((x) => x.part === part)?.text;
          return (
            <div key={i} className={mod.cond} data-testid={`quick-row-${i}`}>
              <div className={css.row}>
                <select className={css.input} style={{ width: 150 }} value={r.field} aria-label="Поле" aria-invalid={!!here("field") || undefined} data-testid={`quick-row-${i}-field`} onChange={(e) => edit(i, { field: e.target.value })}>
                  <option value="">— поле —</option>
                  {r.field && !fieldNames.includes(r.field) && <option value={r.field}>{r.field} (нет в модели)</option>}
                  {fieldNames.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
                <select className={css.input} style={{ width: "auto" }} value={r.op} aria-label="Оператор" data-testid={`quick-row-${i}-op`} onChange={(e) => edit(i, { op: e.target.value as CondOp })}>
                  {OPERATORS.map((o) => (
                    <option key={o.op} value={o.op}>
                      {o.label}
                    </option>
                  ))}
                </select>
                {r.op === "in" ? (
                  <ListField value={r.list} testId={`quick-row-${i}-values`} onCommit={(t) => edit(i, { list: t })} />
                ) : NO_VALUE_OPS.includes(r.op) ? null : (
                  <>
                    <TextField value={r.value} width={140} placeholder="Значение" label="Значение" testId={`quick-row-${i}-value`} invalid={!!here("value")} onCommit={(t) => edit(i, { value: t })} />
                    {labels.length > 0 && (
                      <select className={css.input} style={{ width: "auto" }} value="" aria-label="Подпись категории" data-testid={`quick-row-${i}-label`} onChange={(e) => e.target.value && edit(i, { value: e.target.value })}>
                        <option value="">подпись…</option>
                        {labels.map((l) => (
                          <option key={l} value={l}>
                            {l}
                          </option>
                        ))}
                      </select>
                    )}
                  </>
                )}
                <button type="button" className={css.iconBtn} title="Удалить условие" aria-label="Удалить условие" data-testid={`quick-row-${i}-remove`} onClick={() => apply(rows.filter((_, j) => j !== i))}>
                  ✕
                </button>
              </div>
              {mine.map((x, k) => (
                <Issue key={k} text={x.text} testId={`quick-row-${i}-issue-${x.part}`} />
              ))}
            </div>
          );
        })}
        {!rows.length && <p className={css.note}>Условий нет: быстрый ограничитель ничего не отсекает.</p>}
        <div className={css.row}>
          <button type="button" className={css.btn} data-testid="quick-add" onClick={() => setRows([...rows, blankRow()])}>
            + Условие
          </button>
          <button type="button" className={css.btn} disabled={!rows.length} data-testid="quick-clear" onClick={clear}>
            Снять
          </button>
        </div>
      </fieldset>

      <fieldset className={css.group}>
        <legend>Как объединить</legend>
        <div className={css.row}>
          <div className={mod.seg} role="radiogroup" aria-label="Объединение условий">
            <button type="button" role="radio" aria-checked={q.join === "and"} aria-pressed={q.join === "and"} title="Блок проходит, если выполнены все условия" data-testid="quick-join-and" onClick={() => join("and")}>
              И
            </button>
            <button type="button" role="radio" aria-checked={q.join === "or"} aria-pressed={q.join === "or"} title="Блок проходит, если выполнено любое условие" data-testid="quick-join-or" onClick={() => join("or")}>
              ИЛИ
            </button>
          </div>
          <label className={css.check} title="Показать блоки, которые условия не пропускают (НЕ)">
            <input type="checkbox" checked={q.invert} data-testid="quick-invert" onChange={(e) => invert(e.target.checked)} />
            Обратный (НЕ)
          </label>
        </div>
        <p className={css.note} data-testid="quick-summary">
          Быстрый ограничитель: {quickText(q)}.
        </p>
        {header && (
          <p className={css.note} data-testid="quick-passed">
            Сейчас прошло {header.passed.toLocaleString("ru-RU")} из {header.rows.toLocaleString("ru-RU")} блоков.
          </p>
        )}
        {info?.warnings.map((w) => (
          <p key={w} className={css.warn}>
            {w}
          </p>
        ))}
        <Issue text={failure} testId="quick-issue" />
      </fieldset>
    </div>
  );
}
