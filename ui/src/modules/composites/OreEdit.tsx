/**
 * «Ручная правка» in the window of a result «Рудные интервалы (ГКЗ)» (ТЗ 7.35 Д-26 «Место»; ADR 0018 §12,
 * решение 24): the hole, its column (samples, classes, final intervals), the list of its `ore` / `subgrade`
 * intervals (MANUAL marked); «Разделить» by a sample boundary inside, «Объединить» with the interval below,
 * «Сдвинуть границу» (кровля / подошва) to a sample boundary between the neighbours → `POST …/edit`; the
 * violations of К, Мпп and прирезка the core names are shown, the hole is read again. A stale result, a project
 * opened read-only — no edit, the reason is shown. «Перестроить» of a result with edits warns in its own window
 * (`ResultsTree.runResultAction`).
 */
import { useEffect, useMemo, useState } from "react";
import { CLASS_STYLE } from "../../charts/column/classes";
import { errorText } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import own from "./Composites.module.css";
import type { ResultRow } from "./api";
import { OreColumns } from "./OreWindow";
import { loadSetResults, manualEdits, staleText } from "./results";
import { HolePicker } from "./ToolWindow";
import { fetchProtocol, num, type OreAnswer } from "./tools/ore";
import { ACTION_TITLES, editBody, editIntervals, editText, moveTargets, sampleBounds, sendEdit, splitPoints, violationText, withRows, type EditAction, type Side, type Violation } from "./tools/oreEdit";

/** Why the result cannot be edited; null — it can. */
export function editReason(r: ResultRow, readOnly: boolean): string | null {
  if (readOnly) return "Проект открыт только для чтения";
  if (r.op !== "ore" || r.table_id === null) return "Правка — только у результата «Рудные интервалы (ГКЗ)»";
  const stale = staleText(r);
  return stale ? `Результат ${stale}: сначала «Перестроить»` : null;
}

export function OreEdit({ result, readOnly }: { result: ResultRow; readOnly: boolean }) {
  const [hole, setHole] = useState<{ id: number; name: string } | null>(null);
  const [answer, setAnswer] = useState<OreAnswer | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [pick, setPick] = useState(0);
  const [action, setAction] = useState<EditAction>("split");
  const [side, setSide] = useState<Side>("top");
  const [depth, setDepth] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ text: string; violations: Violation[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reason = editReason(result, readOnly);
  const field = typeof result.params?.field === "string" ? result.params.field : null;
  const cutoff = typeof result.params?.cutoff === "number" ? result.params.cutoff : null;
  const edits = manualEdits(result);

  useEffect(() => {
    let alive = true;
    setReadError(null);
    if (!hole || result.stale) return;
    fetchProtocol(result.id, hole.name).then(
      (a) => alive && setAnswer(a),
      (e: unknown) => alive && (setAnswer(null), setReadError(errorText(e))),
    );
    return () => {
      alive = false;
    };
  }, [result.id, result.stale, hole, reload]);

  const list = useMemo(() => (answer ? editIntervals(answer.result) : []), [answer]);
  const bounds = useMemo(() => (answer ? sampleBounds(answer.samples) : []), [answer]);
  const i = Math.min(pick, Math.max(0, list.length - 1));
  const iv = list[i] ?? null;
  const choices = iv ? (action === "split" ? splitPoints(iv, bounds) : action === "move" ? moveTargets(list, i, side, bounds) : []) : [];
  const body = hole ? editBody(hole.name, action, list, i, { at: depth, side, target: depth }, bounds) : "Выберите скважину";

  useEffect(() => setDepth(null), [action, side, i, answer]);

  const apply = async () => {
    if (typeof body === "string" || reason || busy) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const a = await sendEdit(result.id, body);
      setDone({ text: `${editText(body)} — записано${a.violations.length ? `; нарушений: ${a.violations.length}` : ", нарушений нет"}`, violations: a.violations });
      if (answer) setAnswer(withRows(answer, a.rows));
      setReload((n) => n + 1);
      void loadSetResults(result.set_id).catch(() => undefined); // report.edits, MANUAL
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <fieldset className={css.group} data-testid="ore-edit">
      <legend>Ручная правка</legend>
      <p className={css.note}>
        Разделить, объединить или сдвинуть границу — по границам проб одной скважины. Условия (К, Мпп, прирезка) проверяются заново, нарушения показываются; интервал получает MANUAL = 1.
        {edits > 0 && ` Ручных правок: ${edits} — «Перестроить» их потеряет.`}
      </p>
      {reason ? (
        <p className={css.warn} data-testid="ore-edit-reason">
          {reason}
        </p>
      ) : (
        <>
          <HolePicker setId={result.set_id} value={hole?.id ?? null} onChange={(id, name) => setHole(id === null ? null : { id, name })} />
          {readError && <p className={css.warn}>{readError}</p>}
          {answer && <OreColumns answer={answer} field={field} cutoff={cutoff} showFrame={false} height={240} />}
          {answer && !list.length && <p className={css.note}>У скважины {answer.hole} нет рудных интервалов.</p>}
          {list.length > 0 && (
            <table className={own.summary} data-testid="ore-edit-list">
              <thead>
                <tr>
                  <th />
                  <th>от, м</th>
                  <th>до, м</th>
                  <th>класс</th>
                  <th>M</th>
                  <th>C</th>
                  <th>MC</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.map((x, k) => (
                  <tr key={x.index} data-testid="ore-edit-row" aria-selected={k === i}>
                    <td>
                      <input type="radio" name={`ore-edit-${result.id}`} aria-label={`Интервал ${num(x.from, 2)}–${num(x.to, 2)} м`} checked={k === i} onChange={() => setPick(k)} />
                    </td>
                    <td>{num(x.from, 2)}</td>
                    <td>{num(x.to, 2)}</td>
                    <td>{CLASS_STYLE[x.cls].title}</td>
                    <td>{num(x.m, 2)}</td>
                    <td>{num(x.c, 3)}</td>
                    <td>{num(x.mc, 2)}</td>
                    <td>{x.manual ? "вручную" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {iv && (
            <div className={css.inline} data-testid="ore-edit-actions">
              <select className={css.input} aria-label="Действие правки" value={action} onChange={(e) => setAction(e.target.value as EditAction)}>
                {(Object.keys(ACTION_TITLES) as EditAction[]).map((a) => (
                  <option key={a} value={a}>
                    {ACTION_TITLES[a]}
                  </option>
                ))}
              </select>
              {action === "move" && (
                <select className={css.input} aria-label="Какая граница" value={side} onChange={(e) => setSide(e.target.value === "bottom" ? "bottom" : "top")}>
                  <option value="top">кровля {num(iv.from, 2)} м</option>
                  <option value="bottom">подошва {num(iv.to, 2)} м</option>
                </select>
              )}
              {action !== "merge" && (
                <select className={css.input} aria-label={action === "split" ? "Граница разделения" : "Новая глубина"} value={depth ?? ""} onChange={(e) => setDepth(e.target.value === "" ? null : Number(e.target.value))}>
                  <option value="">{choices.length ? (action === "split" ? "по границе пробы…" : "на глубину…") : "нет границ проб"}</option>
                  {choices.map((b) => (
                    <option key={b} value={b}>
                      {num(b, 2)} м
                    </option>
                  ))}
                </select>
              )}
              <span className={`${css.note} ${css.grow}`} data-testid="ore-edit-what">
                {typeof body === "string" ? body : editText(body)}
              </span>
              <button type="button" className={css.primary} data-testid="ore-edit-apply" disabled={typeof body === "string" || busy} onClick={() => void apply()}>
                Применить правку
              </button>
            </div>
          )}
        </>
      )}
      {done && (
        <div data-testid="ore-edit-done">
          <p className={done.violations.length ? css.warn : css.note}>{done.text}</p>
          {done.violations.length > 0 && (
            <ul className={own.notes} data-testid="ore-edit-violations">
              {done.violations.map((v, k) => (
                <li key={k}>{violationText(v)}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
    </fieldset>
  );
}
