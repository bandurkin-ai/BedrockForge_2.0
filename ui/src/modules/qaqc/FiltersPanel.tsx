/**
 * The filters of the tab «QAQC» (ТЗ 8.4.2; ADR 0020 §5, §11): labs, batches, dates, CRM, types, the prefix of
 * the hole and conditions «любое поле», with the counter «записей N из M» and «Сбросить». Every edit runs the
 * command `qaqc.filter` (the same as Ctrl+K and the assistant); lists go as JSON arrays (a name may hold a comma).
 * Values of batches and CRM are offered from the data (`GET /qaqc/data/{id}/values`, ≤ 1000).
 */
import { useState, useSyncExternalStore } from "react";
import { parseNumber } from "../../charts/core/axisScale";
import { useApp } from "../../shell/AppContext";
import { fieldValues, type QaqcData, type QaqcFieldInfo } from "./api";
import { QAQC_TYPES, TYPE_TITLES } from "./commands";
import { checkWhere, conditionCount, countLabel, dateTitle, filterSummary, isEmptyFilter, MAX_WHERE, OP_TITLES, opsFor, parseDate, toggleValue, type WhereCond, type WhereOp } from "./filters";
import { currentCounts, qaqcStore } from "./state";
import css from "./Qaqc.module.css";

/** A list of the data (labs) that may come as a count: only a list is offered. */
const asList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

function Chips({ values, onRemove }: { values: readonly string[]; onRemove: (v: string) => void }) {
  if (!values.length) return null;
  return (
    <div className={css.chips}>
      {values.map((v) => (
        <span key={v} className={css.chip} title={v}>
          <span>{v}</span>
          <button type="button" aria-label={`Убрать «${v}»`} onClick={() => onRemove(v)}>
            ×
          </button>
        </span>
      ))}
    </div>
  );
}

/** Chosen values with a field to add one; the values of the data are offered as the field gets the focus. */
function ValuesField({ title, field, dataId, values, onChange }: { title: string; field: string; dataId: number; values: readonly string[]; onChange: (v: string[]) => void }) {
  const [text, setText] = useState("");
  const [options, setOptions] = useState<string[] | null>(null);
  const listId = `qaqc-values-${field}`;
  const load = () => {
    if (options) return;
    setOptions([]);
    fieldValues(dataId, field).then(
      (a) => setOptions(a.values.map((v) => String(v.value))),
      () => setOptions([]),
    );
  };
  const add = (v = text) => {
    const t = v.trim();
    if (t) onChange(toggleValue(values, t, true));
    setText("");
  };
  return (
    <fieldset className={css.group}>
      <legend>{title}</legend>
      <Chips values={values} onRemove={(v) => onChange(toggleValue(values, v, false))} />
      <div className={css.row}>
        <input
          className={css.input}
          aria-label={`${title}: добавить`}
          placeholder="добавить…"
          list={listId}
          value={text}
          onFocus={load}
          onChange={(e) => {
            const v = e.target.value;
            // a value picked from the list is added at once
            if (options?.includes(v) && !values.includes(v)) add(v);
            else setText(v);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") add();
          }}
        />
        <button type="button" className={css.btn} disabled={!text.trim()} onClick={() => add()}>
          +
        </button>
      </div>
      <datalist id={listId}>
        {(options ?? []).map((v) => (
          <option key={v} value={v} />
        ))}
      </datalist>
    </fieldset>
  );
}

/** A text applied on Enter or when the field is left. */
function TextApply({ label, value, onApply }: { label: string; value: string; onApply: (v: string) => void }) {
  const [text, setText] = useState<string | null>(null);
  const apply = () => {
    if (text !== null && text.trim() !== value) onApply(text.trim());
    setText(null);
  };
  return <input className={css.input} aria-label={label} value={text ?? value} onChange={(e) => setText(e.target.value)} onBlur={apply} onKeyDown={(e) => e.key === "Enter" && apply()} />;
}

/** The value typed for a condition in the type of its field: a number, a date, a text; a list — by «;». */
function typed(kind: QaqcFieldInfo["kind"], text: string): string | number | null {
  const t = text.trim();
  if (!t) return null;
  if (kind === "number") return parseNumber(t);
  if (kind === "date") return parseDate(t);
  return t;
}

export function whereDraft(field: QaqcFieldInfo, op: WhereOp, a: string, b: string): WhereCond | string {
  if (op === "empty") return { field: field.name, op };
  if (op === "in") return checkWhere({ field: field.name, op, value: a.split(";").map((x) => typed(field.kind, x)).filter((x) => x !== null) });
  if (op === "between") return checkWhere({ field: field.name, op, value: [typed(field.kind, a), typed(field.kind, b)] });
  return checkWhere({ field: field.name, op, value: typed(field.kind, a) });
}

const condTitle = (c: WhereCond) => (c.op === "empty" ? `${c.field} ${OP_TITLES[c.op]}` : `${c.field} ${OP_TITLES[c.op]} ${Array.isArray(c.value) ? c.value.join(c.op === "between" ? " — " : "; ") : String(c.value)}`);

function WhereEditor({ fields, where, onChange }: { fields: readonly QaqcFieldInfo[]; where: readonly WhereCond[]; onChange: (w: WhereCond[]) => void }) {
  const [name, setName] = useState("");
  const [op, setOp] = useState<WhereOp>("eq");
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const field = fields.find((f) => f.name === name) ?? fields[0];
  const ops = field ? opsFor(field.kind) : [];
  const cur = ops.includes(op) ? op : "eq";
  const add = () => {
    if (!field) return;
    const c = whereDraft(field, cur, a, b);
    if (typeof c === "string") return setProblem(c);
    setProblem(null);
    setA("");
    setB("");
    onChange([...where, c]);
  };
  return (
    <fieldset className={css.group}>
      <legend>Любое поле</legend>
      {where.map((c, i) => (
        <div key={`${c.field}-${i}`} className={css.cond} title={condTitle(c)}>
          <span>{condTitle(c)}</span>
          <button type="button" className={css.x} aria-label={`Убрать условие «${condTitle(c)}»`} onClick={() => onChange(where.filter((_, j) => j !== i))}>
            ×
          </button>
        </div>
      ))}
      {fields.length === 0 ? (
        <p className={css.note}>В данных нет других полей</p>
      ) : (
        <>
          <div className={css.row}>
            <select aria-label="Поле условия" value={field?.name ?? ""} onChange={(e) => setName(e.target.value)} className={css.input}>
              {fields.map((f) => (
                <option key={f.name} value={f.name}>
                  {f.name}
                </option>
              ))}
            </select>
            <select aria-label="Операция условия" value={cur} onChange={(e) => setOp(e.target.value as WhereOp)} className={css.input}>
              {ops.map((o) => (
                <option key={o} value={o}>
                  {OP_TITLES[o]}
                </option>
              ))}
            </select>
          </div>
          {cur !== "empty" && (
            <div className={css.row}>
              <input className={css.input} aria-label="Значение условия" placeholder={cur === "in" ? "значения через «;»" : cur === "between" ? "от" : "значение"} value={a} onChange={(e) => setA(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
              {cur === "between" && <input className={css.input} aria-label="Значение условия: до" placeholder="до" value={b} onChange={(e) => setB(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />}
            </div>
          )}
          <button type="button" className={css.btn} disabled={where.length >= MAX_WHERE} title={where.length >= MAX_WHERE ? `Не больше ${MAX_WHERE} условий` : undefined} onClick={add}>
            Добавить условие
          </button>
          {problem && <p className={css.error}>{problem}</p>}
        </>
      )}
    </fieldset>
  );
}

export function FiltersPanel({ data }: { data: QaqcData }) {
  const app = useApp();
  const s = useSyncExternalStore(qaqcStore.subscribe, qaqcStore.get, qaqcStore.get);
  const f = s.filter;
  const counts = currentCounts(s);
  const apply = (args: Record<string, unknown>) => void app.run("qaqc.filter", args);
  const labs = asList(data.labs);
  return (
    <div className={css.filters} aria-label="Фильтры QAQC">
      <div className={css.counter} title={filterSummary(f)} data-testid="qaqc-counter">
        <span>
          {countLabel(counts?.filtered, counts?.rows ?? data.rows)}
          {conditionCount(f) > 0 && <span className={css.note}> · условий {conditionCount(f)}</span>}
        </span>
        <button type="button" className={css.btn} disabled={isEmptyFilter(f)} onClick={() => apply({ reset: true })}>
          Сбросить
        </button>
      </div>
      {labs.length > 0 && (
        <fieldset className={css.group}>
          <legend>Лаборатории</legend>
          <div className={css.checks}>
            {labs.map((lab) => (
              <label key={lab} className={css.check}>
                <input type="checkbox" checked={f.labs.includes(lab)} onChange={(e) => apply({ labs: JSON.stringify(toggleValue(f.labs, lab, e.target.checked)) })} />
                {lab}
              </label>
            ))}
          </div>
          <Chips values={f.labs.filter((l) => !labs.includes(l))} onRemove={(v) => apply({ labs: JSON.stringify(toggleValue(f.labs, v, false)) })} />
        </fieldset>
      )}
      <ValuesField key={`b${data.id}`} title="Партии" field="batch" dataId={data.id} values={f.batches} onChange={(v) => apply({ batches: JSON.stringify(v) })} />
      <fieldset className={css.group}>
        <legend>Даты</legend>
        <label className={css.pair}>
          с
          <input type="date" className={css.input} aria-label="Дата с" value={f.date_from ?? ""} min={data.date_from?.slice(0, 10)} max={data.date_to?.slice(0, 10)} onChange={(e) => apply({ date_from: e.target.value })} />
        </label>
        <label className={css.pair}>
          по
          <input type="date" className={css.input} aria-label="Дата по" value={f.date_to ?? ""} min={data.date_from?.slice(0, 10)} max={data.date_to?.slice(0, 10)} onChange={(e) => apply({ date_to: e.target.value })} />
        </label>
        {data.date_from && data.date_to && (
          <p className={css.note}>
            в данных: {dateTitle(data.date_from.slice(0, 10))} — {dateTitle(data.date_to.slice(0, 10))}
          </p>
        )}
      </fieldset>
      <ValuesField key={`c${data.id}`} title="Стандарты CRM" field="crm_id" dataId={data.id} values={f.crm} onChange={(v) => apply({ crm: JSON.stringify(v) })} />
      <fieldset className={css.group}>
        <legend>Типы</legend>
        <div className={css.checks}>
          {QAQC_TYPES.map((t) => (
            <label key={t} className={css.check} title={TYPE_TITLES[t]}>
              <input type="checkbox" checked={f.types.includes(t)} onChange={(e) => apply({ types: JSON.stringify(toggleValue(f.types, t, e.target.checked)) })} />
              {t}
            </label>
          ))}
        </div>
      </fieldset>
      <label className={css.field}>
        <span>Скважины, начало имени</span>
        <TextApply label="Скважины, начало имени" value={f.hole_prefix ?? ""} onApply={(v) => apply({ hole_prefix: v })} />
      </label>
      <WhereEditor fields={data.fields ?? []} where={f.where} onChange={(w) => apply({ where: JSON.stringify(w) })} />
    </div>
  );
}
