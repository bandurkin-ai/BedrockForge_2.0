/**
 * The window «Кодировать каркасами» (ТЗ 8.2.2–8.2.3; ADR 0018 §6, §10–11): the source table; the bodies — every
 * wireframe of the project, the chosen ones first in the order of priority (↑/↓ — an overlap takes the upper),
 * an open body (or a DTM surface) cannot be chosen and says why; «Делить пробы на границе тела» — да (8.2.2, by
 * default) / нет (the code by the middle and INSIDE_FRAC); the name «‹Таблица› — коды»; the preview on one hole;
 * «Применить» → the job; 409 `table_exists` → «Заменить?» in our own window.
 */
import { useEffect, useState } from "react";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { tablesStore } from "../tables/state";
import own from "./Composites.module.css";
import { loadTables, type TableRow } from "./api";
import { PreviewBox } from "./MergeWindow";
import { SourcePicker } from "./SourcePicker";
import { JobView, ParamForm, ToolFooter, useTool } from "./ToolWindow";
import { chosenProblem, coding, codingName, loadBodies, moveId, orderBodies, type CodingBody } from "./tools/coding";

const SOURCE = { ...coding.source, wireframes: false }; // the bodies are this window's own list
const NAME_PARAM = coding.params.filter((p) => p.key === "name");

/** The list of bodies: a check box, the priority with ↑/↓, the state (closed / why not). */
export function BodyList({ bodies, chosen, onChange }: { bodies: readonly CodingBody[]; chosen: readonly number[]; onChange: (ids: number[]) => void }) {
  const rows = orderBodies(bodies, chosen);
  if (!rows.length) return <p className={css.note}>В проекте нет каркасов</p>;
  return (
    <table className={own.rows} data-testid="composites-bodies">
      <thead>
        <tr>
          <th />
          <th>Приоритет</th>
          <th>Каркас</th>
          <th>Состояние</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((b) => {
          const i = chosen.indexOf(b.id);
          const on = i >= 0;
          const blocked = b.reason !== null && !on;
          return (
            <tr key={b.id} aria-disabled={b.reason !== null ? true : undefined} data-testid={`composites-body-${b.id}`}>
              <td>
                <input
                  type="checkbox"
                  aria-label={`Кодировать каркасом «${b.name}»`}
                  checked={on}
                  disabled={blocked}
                  title={b.reason ?? undefined}
                  onChange={(e) => onChange(e.target.checked ? [...chosen, b.id] : chosen.filter((x) => x !== b.id))}
                />
              </td>
              <td>
                {on ? (
                  <span className={own.rowButtons}>
                    <span className={own.num} style={{ minWidth: 18 }}>
                      {i + 1}
                    </span>
                    <button type="button" className={own.iconBtn} aria-label={`Выше: «${b.name}»`} disabled={i === 0} onClick={() => onChange(moveId(chosen, b.id, -1))}>
                      ↑
                    </button>
                    <button type="button" className={own.iconBtn} aria-label={`Ниже: «${b.name}»`} disabled={i === chosen.length - 1} onClick={() => onChange(moveId(chosen, b.id, 1))}>
                      ↓
                    </button>
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td>{b.folder ? `${b.folder} › ${b.name}` : b.name}</td>
              <td className={b.reason ? css.warn : undefined}>{b.reason ?? (b.closed ? "замкнут" : "проверит ядро")}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function CodingWindow({ winId, args = {} }: { winId: string; args?: Record<string, unknown> }) {
  const t = useTool(coding, args);
  const [activeSet] = useState(() => tablesStore.get().setId);
  const [bodies, setBodies] = useState<CodingBody[] | null>(null);
  const [tables, setTables] = useState<TableRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    loadBodies().then(
      (b) => alive && setBodies(b),
      (e: unknown) => alive && setLoadError(errorText(e)),
    );
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    let alive = true;
    if (t.src.setId !== null) loadTables(t.src.setId).then((x) => alive && setTables(x), () => undefined);
    return () => {
      alive = false;
    };
  }, [t.src.setId]);
  const table = tables.find((x) => x.table === t.src.table)?.name ?? null;
  const problem = bodies ? chosenProblem(bodies, t.src.wireframeIds) : null;
  const reason = t.checked.reason ?? problem ?? (bodies ? null : "Чтение каркасов…");
  const split = t.values.split !== false;
  const name = (typeof t.values.name === "string" && t.values.name.trim()) || codingName(table);
  if (t.job) return <JobView title={coding.title} job={t.job} winId={winId} onBack={() => t.setJob(null)} />;
  return (
    <div className={css.win} data-testid="composites-coding">
      <div className={css.winBody}>
        <p className={css.note}>{coding.note}</p>
        <SourcePicker needs={SOURCE} value={t.src} onChange={t.setSrc} onFields={t.setFields} activeSet={activeSet} />
        <fieldset className={css.group}>
          <legend>Тела (каркасы)</legend>
          <p className={css.note}>Отмеченные — в порядке приоритета: проба в перекрытии тел получает код верхнего. Незамкнутые каркасы не кодируют.</p>
          {loadError && <p className={css.error}>{loadError}</p>}
          {bodies ? <BodyList bodies={bodies} chosen={t.src.wireframeIds} onChange={(ids) => t.setSrc({ ...t.src, wireframeIds: ids })} /> : !loadError && <p className={css.note}>Чтение…</p>}
          {problem && (
            <p className={css.warn} role="alert">
              {problem}
            </p>
          )}
        </fieldset>
        <fieldset className={css.group} data-testid="composites-split">
          <legend>Деление проб на границе тела</legend>
          <label className={css.check}>
            <input type="radio" name={`${winId}-split`} checked={split} onChange={() => t.setValues({ ...t.values, split: true })} />
            да — пробы режутся в точках входа и выхода, значения копируются в части
          </label>
          <label className={css.check}>
            <input type="radio" name={`${winId}-split`} checked={!split} onChange={() => t.setValues({ ...t.values, split: false })} />
            нет — код по середине пробы и доля длины внутри (INSIDE_FRAC)
          </label>
        </fieldset>
        <ParamForm params={NAME_PARAM} values={t.values} errors={t.checked.errors} fields={t.fields} onChange={t.setValues} placeholders={{ name: codingName(table) }} />
        <PreviewBox t={t} first={["CODE", "INSIDE_FRAC"]} />
        {t.error && (
          <p className={css.error} role="alert">
            {t.error}
          </p>
        )}
      </div>
      <ToolFooter winId={winId} reason={reason} busy={t.busy} onApply={() => void t.apply()} extra={`Новая таблица «${name}» в наборе; исходная таблица не меняется.`} />
    </div>
  );
}
