/**
 * The tree of the rules of a filter as the window edits it (ТЗ 7.35 Д-02, 8.7.5; ADR 0023 §8): a group («И», «ИЛИ»,
 * «НЕ») holds rules; a rule is «поле — оператор — значение» (the categories of the model by their labels — a list of
 * values one per line for «в списке»), an expression of 8.7.4, «внутри или снаружи каркаса» (by the share or by the
 * centre, `m` — the sub-grid of the share) or «выше или ниже поверхности». The node a refusal names (ours or the core's:
 * `rules.items.1.cond.value`) is marked and told under itself. Pure view: every change goes up as a new tree.
 */
import type { ReactNode } from "react";
import css from "../../../shell/PropsParts.module.css";
import mod from "./Filters.module.css";
import {
  addTo,
  COND_OPS,
  GROUP_LABEL,
  newCond,
  newExpr,
  newGroup,
  newSurface,
  newWireframe,
  NO_VALUE_OPS,
  pathOfParam,
  removeAt,
  sameParam,
  slabTitle,
  updateAt,
  type CondOp,
  type Draft,
  type GroupOp,
  type Path,
  type RuleIssue,
} from "./constructor";

/** An object of the project a leaf may name. */
export interface ObjectOption {
  id: number;
  name: string;
  /** a DTM («ЦТМ») — listed first for a surface */
  dtm: boolean;
}

export interface EditorProps {
  draft: Draft;
  onChange: (next: Draft) => void;
  issue: RuleIssue | null;
  /** names of the fields of the model chosen (all models when none) for the suggestions */
  fields: readonly string[];
  wireframes: readonly ObjectOption[];
  /** the id the test names the editor with */
  testId?: string;
}

const key = (p: Path) => (p.length ? p.join("-") : "root");

function Remove({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button type="button" className={css.iconBtn} title={`Удалить: ${label}`} aria-label={`Удалить: ${label}`} onClick={onClick}>
      ✕
    </button>
  );
}

function ObjectSelect({ value, list, kind, onPick, id }: { value: number | null; list: readonly ObjectOption[]; kind: "wireframe" | "surface"; onPick: (id: number | null) => void; id: string }) {
  const first = list.filter((o) => (kind === "surface" ? o.dtm : !o.dtm));
  const rest = list.filter((o) => (kind === "surface" ? !o.dtm : o.dtm));
  const known = value === null || list.some((o) => o.id === value);
  return (
    <select className={css.input} value={value ?? ""} data-testid={id} aria-label={kind === "wireframe" ? "Каркас" : "Поверхность"} onChange={(e) => onPick(e.target.value ? Number(e.target.value) : null)}>
      <option value="">{kind === "wireframe" ? "— выберите каркас —" : "— выберите поверхность —"}</option>
      {!known && <option value={value ?? ""}>№{value} (нет в проекте)</option>}
      {first.map((o) => (
        <option key={o.id} value={o.id}>
          {o.name}
        </option>
      ))}
      {rest.length > 0 && (
        <optgroup label={kind === "surface" ? "Каркасы" : "ЦТМ"}>
          {rest.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </optgroup>
      )}
    </select>
  );
}

export function RuleEditor({ draft, onChange, issue, fields, wireframes, testId = "filter-rules" }: EditorProps) {
  const marked = issue ? pathOfParam(issue.param) : null;
  const set = (path: Path, next: (d: Draft) => Draft) => onChange(updateAt(draft, path, next));
  const listId = `${testId}-fields`;

  const view = (node: Draft, path: Path): ReactNode => {
    const bad = marked !== null && sameParam(marked, path);
    const id = `${testId}-${key(path)}`;
    const note = bad && issue ? (
      <p className={css.error} role="alert" data-testid={`${id}-error`}>
        {issue.message} {issue.hint}
      </p>
    ) : null;
    const remove = path.length ? <Remove onClick={() => onChange(removeAt(draft, path))} label="правило" /> : null;

    if ("items" in node) {
      const full = node.op === "not" && node.items.length >= 1;
      return (
        <div key={key(path)} className={mod.group} data-testid={id} aria-invalid={bad || undefined}>
          <div className={css.row}>
            <select className={css.input} style={{ width: "auto" }} value={node.op} aria-label="Вид группы" data-testid={`${id}-op`} onChange={(e) => set(path, (d) => ("items" in d ? { ...d, op: e.target.value as GroupOp } : d))}>
              {(["and", "or", "not"] as const).map((op) => (
                <option key={op} value={op}>
                  {GROUP_LABEL[op]}
                </option>
              ))}
            </select>
            <button type="button" className={css.btn} disabled={full} data-testid={`${id}-add-cond`} onClick={() => onChange(addTo(draft, path, newCond()))}>
              + Условие
            </button>
            <button type="button" className={css.btn} disabled={full} data-testid={`${id}-add-group`} title="Вложенная группа «И», «ИЛИ» или «НЕ»" onClick={() => onChange(addTo(draft, path, newGroup()))}>
              + Группа
            </button>
            <button type="button" className={css.btn} disabled={full} data-testid={`${id}-add-expr`} title="Выражение языка 8.7.4, например IF(AU > 1, 1, 0) = 1" onClick={() => onChange(addTo(draft, path, newExpr()))}>
              + Выражение
            </button>
            <button type="button" className={css.btn} disabled={full} data-testid={`${id}-add-wireframe`} title="Блоки внутри или снаружи каркаса" onClick={() => onChange(addTo(draft, path, newWireframe()))}>
              + Каркас
            </button>
            <button type="button" className={css.btn} disabled={full} data-testid={`${id}-add-surface`} title="Блоки выше или ниже поверхности" onClick={() => onChange(addTo(draft, path, newSurface()))}>
              + Поверхность
            </button>
            {remove}
          </div>
          {note}
          <div className={mod.children}>{node.items.map((it, i) => view(it, [...path, i]))}</div>
        </div>
      );
    }

    if ("cond" in node) {
      const c = node.cond;
      const patch = (p: Partial<typeof c>) => set(path, (d) => ("cond" in d ? { cond: { ...d.cond, ...p } } : d));
      return (
        <div key={key(path)} className={mod.rule} data-testid={id} aria-invalid={bad || undefined}>
          <div className={css.row}>
            <input className={css.input} style={{ width: 140 }} list={listId} value={c.field} placeholder="Поле" aria-label="Поле" aria-invalid={bad && issue?.param.endsWith(".field")} data-testid={`${id}-field`} onChange={(e) => patch({ field: e.target.value })} />
            <select className={css.input} style={{ width: "auto" }} value={c.op} aria-label="Оператор" data-testid={`${id}-cmp`} onChange={(e) => patch({ op: e.target.value as CondOp })}>
              {COND_OPS.map((o) => (
                <option key={o.op} value={o.op}>
                  {o.label}
                </option>
              ))}
            </select>
            {c.op === "in" ? (
              <textarea className={`${css.input} ${mod.values}`} rows={Math.min(Math.max(c.list.split("\n").length, 2), 6)} value={c.list} placeholder={"Значения, по одному в строке"} aria-label="Значения" data-testid={`${id}-values`} onChange={(e) => patch({ list: e.target.value })} />
            ) : NO_VALUE_OPS.includes(c.op) ? null : (
              <input className={css.input} style={{ width: 140 }} value={c.value} placeholder="Значение" aria-label="Значение" aria-invalid={bad && issue?.param.endsWith(".value")} data-testid={`${id}-value`} onChange={(e) => patch({ value: e.target.value })} />
            )}
            {remove}
          </div>
          {note}
        </div>
      );
    }

    if ("expr" in node) {
      const e = bad ? issue?.expr : undefined;
      return (
        <div key={key(path)} className={mod.rule} data-testid={id} aria-invalid={bad || undefined}>
          <div className={css.row}>
            <input className={`${css.input} ${mod.mono}`} style={{ flex: 1 }} value={node.expr} placeholder="Выражение 8.7.4, например AU * 2 > 1" aria-label="Выражение" aria-invalid={bad} data-testid={`${id}-expr`} onChange={(ev) => set(path, () => ({ expr: ev.target.value }))} />
            {remove}
          </div>
          {note}
          {e && <p className={css.note}>Место ошибки: строка {e.line}, столбец {e.column}.</p>}
        </div>
      );
    }

    if ("wireframe" in node) {
      const w = node.wireframe;
      const patch = (p: Partial<typeof w>) => set(path, (d) => ("wireframe" in d ? { wireframe: { ...d.wireframe, ...p } } : d));
      return (
        <div key={key(path)} className={mod.rule} data-testid={id} aria-invalid={bad || undefined}>
          <div className={css.row}>
            <select className={css.input} style={{ width: "auto" }} value={w.side} aria-label="Сторона" data-testid={`${id}-side`} onChange={(e) => patch({ side: e.target.value as typeof w.side })}>
              <option value="inside">внутри каркаса</option>
              <option value="outside">снаружи каркаса</option>
            </select>
            <ObjectSelect value={w.id} list={wireframes} kind="wireframe" id={`${id}-object`} onPick={(v) => patch({ id: v })} />
            <select className={css.input} style={{ width: "auto" }} value={w.by} aria-label="Способ" title="По доле — доля блока внутри каркаса; по центру — центр блока" data-testid={`${id}-by`} onChange={(e) => patch({ by: e.target.value as typeof w.by })}>
              <option value="frac">по доле</option>
              <option value="centre">по центру</option>
            </select>
            {w.by === "frac" && (
              <label className={css.check} title="Подсетка доли: блок делится на m × m × m точек">
                m
                <input className={css.input} style={{ width: 52 }} inputMode="numeric" value={w.m} aria-label="Подсетка доли m" aria-invalid={bad && issue?.param.endsWith(".m")} data-testid={`${id}-m`} onChange={(e) => patch({ m: Number(e.target.value) })} />
              </label>
            )}
            {remove}
          </div>
          {note}
        </div>
      );
    }

    if ("slab" in node) {
      return (
        <div key={key(path)} className={mod.rule} data-testid={id} aria-invalid={bad || undefined}>
          <div className={css.row}>
            <span style={{ flex: 1 }} title="Слой задан переходом от отчёта или полосой swath; здесь его можно только увидеть или убрать" data-testid={`${id}-slab`}>
              Слой: {slabTitle(node.slab)}
            </span>
            {remove}
          </div>
          {note}
        </div>
      );
    }

    const s = node.surface;
    const patch = (p: Partial<typeof s>) => set(path, (d) => ("surface" in d ? { surface: { ...d.surface, ...p } } : d));
    return (
      <div key={key(path)} className={mod.rule} data-testid={id} aria-invalid={bad || undefined}>
        <div className={css.row}>
          <select className={css.input} style={{ width: "auto" }} value={s.side} aria-label="Сторона" data-testid={`${id}-side`} onChange={(e) => patch({ side: e.target.value as typeof s.side })}>
            <option value="above">выше поверхности</option>
            <option value="below">ниже поверхности</option>
          </select>
          <ObjectSelect value={s.id} list={wireframes} kind="surface" id={`${id}-object`} onPick={(v) => patch({ id: v })} />
          <select className={css.input} style={{ width: "auto" }} value={s.by} aria-label="Способ" title="По центру — центр блока; по доле — доля блока выше или ниже" data-testid={`${id}-by`} onChange={(e) => patch({ by: e.target.value as typeof s.by })}>
            <option value="centre">по центру</option>
            <option value="frac">по доле</option>
          </select>
          {remove}
        </div>
        {note}
      </div>
    );
  };

  return (
    <div className={mod.tree} role="group" aria-label="Правила фильтра" data-testid={testId}>
      <datalist id={listId}>
        {fields.map((f) => (
          <option key={f} value={f} />
        ))}
      </datalist>
      {view(draft, [])}
    </div>
  );
}
