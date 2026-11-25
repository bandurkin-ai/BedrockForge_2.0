/**
 * The window «Поля блочной модели» (ТЗ 8.7.4, 8.7.5; ADR 0023 §7, §9) and its first tab, the calculator: the expression
 * of 8.7.4 in a text box with the check of the core under it (the kind of the result, the fields read, the canonical
 * text — or the line, the column and a caret under the character of the error, as the core counted it), the name, the
 * type, the unit and the description of the new field, the restrictor (a filter of the library or the quick one) with
 * «оставить» / «NaN» outside it, the course of the job and the result. «Вычислить» runs the command `blocks.calc` through
 * the registry (the log, the notice, the question of the assistant); a field that is there is replaced after a question
 * in our own window (№ 69). The other tabs are in `DensityPanel.tsx`, `CategoriesPanel.tsx`, `FieldProps.tsx`.
 * Floating, not modal.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../../../shell/AppContext";
import css from "../../../shell/PropsParts.module.css";
import { errorText } from "../../../shell/props";
import { closeWindow } from "../../../shell/windows";
import { blocksStore } from "../state";
import type { Dtype } from "./api";
import { argsOfForm, DTYPE_LABEL, errorMarker, fieldRef, FUNCTIONS } from "./calcForm";
import { chooseModel, checkNow, showTab } from "./bind";
import { CategoriesPanel } from "./CategoriesPanel";
import { DensityPanel } from "./DensityPanel";
import { FieldProps } from "./FieldProps";
import type { Tab } from "./common";
import { IssueLine, ModelPicker, RestrictBox, RunView, useSel } from "./Common";
import mod from "./Fields.module.css";
import { cancelRun } from "./run";
import { checkKey, fieldsStore, hasField, isRunning, modelFields, NO_CHECK, setCalc, setCheck, setIssue, TAB_TITLES } from "./state";
import { DTYPES } from "./api";

/** The tabs of the window, in the order of the ribbon group «Поля». */
export const TABS: readonly Tab[] = ["calc", "density", "classify", "props"];

const KIND_TEXT = { number: "число", bool: "условие (0 или 1)", category: "категория" } as const;

function CalcPanel({ winId }: { winId: string }) {
  const app = useApp();
  const form = useSel(fieldsStore, (s) => s.calc);
  const modelId = useSel(fieldsStore, (s) => s.modelId);
  const check = useSel(fieldsStore, (s) => s.check);
  const run = useSel(fieldsStore, (s) => s.run);
  const readOnly = useSel(app.store, (s) => s.projectReadOnly);
  const models = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const [note, setNote] = useState<string | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const running = run.phase === "running";

  const fields = useMemo(() => modelFields(modelId), [modelId, models, details]);
  const names = useMemo(() => fields.map((f) => f.name), [fields]);
  const key = checkKey(modelId, form.dtype, form.expr);
  const fresh = check.key === key;
  const marker = fresh && check.issue ? errorMarker(form.expr, check.issue.pos) : null;
  const exists = form.field.trim() !== "" && hasField(modelId, form.field);
  const reason = readOnly ? "Проект открыт только для чтения" : modelId === null ? "Выберите блочную модель" : null;

  // the expression is checked by the core a moment after the last key (nothing is written; the log does not see it)
  useEffect(() => {
    if (modelId === null || !form.expr.trim()) {
      if (fieldsStore.get().check.key !== "") setCheck(NO_CHECK);
      return;
    }
    const t = setTimeout(() => void checkNow(), 450);
    return () => clearTimeout(t);
  }, [modelId, form.expr, form.dtype]);

  const insert = (text: string) => {
    const el = area.current;
    const at = el ? el.selectionStart : form.expr.length;
    const end = el ? el.selectionEnd : at;
    setCalc({ expr: form.expr.slice(0, at) + text + form.expr.slice(end) });
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(at + text.length, at + text.length);
    });
  };
  const toError = () => {
    if (!marker) return;
    area.current?.focus();
    area.current?.setSelectionRange(marker.from, marker.to);
  };
  const doCheck = async () => {
    setNote(null);
    const got = argsOfForm(modelId, form, true);
    if (!got.ok) return setIssue(got.issue);
    const r = await app.registry.execute("blocks.calc.check", got.args, "user");
    if (!r.ok && !fieldsStore.get().check.issue) setNote(r.result);
  };
  const build = async () => {
    setNote(null);
    const got = argsOfForm(modelId, form);
    if (!got.ok) return setIssue(got.issue);
    const r = await app.registry.execute("blocks.calc", got.args, "user");
    if (!r.ok && !fieldsStore.get().issue && !fieldsStore.get().check.issue) setNote(r.result);
  };
  const cancel = () => void cancelRun().catch((e: unknown) => setNote(errorText(e)));

  return (
    <>
      <div className={mod.body} data-testid="calc-panel">
        <fieldset className={css.group} disabled={running || modelId === null}>
          <legend>Выражение</legend>
          <textarea
            ref={area}
            className={mod.expr}
            value={form.expr}
            spellCheck={false}
            rows={5}
            placeholder="IF(AU > 1, AU * 2, 0)"
            aria-label="Выражение калькулятора"
            aria-invalid={(fresh && check.phase === "error") || undefined}
            data-testid="calc-expr"
            onChange={(e) => setCalc({ expr: e.target.value })}
          />
          {marker && check.issue && (
            <div role="alert" data-testid="calc-error">
              <pre className={mod.marker} data-testid="calc-marker">
                {`${marker.lineText}\n`}
                <b>{marker.caret}</b>
              </pre>
              <p className={css.error}>
                {check.issue.message} {check.issue.hint}
              </p>
              <button type="button" className={css.btn} data-testid="calc-to-error" onClick={toError}>
                К месту ошибки
              </button>
            </div>
          )}
          {fresh && check.phase === "error" && !check.issue && (
            <p className={css.error} role="alert" data-testid="calc-check-failure">
              {check.failure}
            </p>
          )}
          {fresh && check.phase === "ok" && check.result && (
            <p className={mod.ok} data-testid="calc-ok">
              Выражение верно: результат — {KIND_TEXT[check.result.out_kind]}, тип поля {check.result.dtype}
              {check.result.fields_used.length ? `; читает поля: ${check.result.fields_used.join(", ")}` : ""}.
              {check.result.text !== form.expr.trim() && <span className={css.note}> Запись: {check.result.text}</span>}
            </p>
          )}
          {check.phase === "checking" && fresh && (
            <p className={css.note} data-testid="calc-checking">
              Проверка выражения…
            </p>
          )}
          <IssueLine tab="calc" place="expr" />
          <div className={mod.chips} role="group" aria-label="Поля модели" data-testid="calc-fields">
            {names.map((n) => (
              <button key={n} type="button" title={`Вставить поле ${n}`} data-testid={`calc-field-${n}`} onClick={() => insert(fieldRef(n))}>
                {n}
              </button>
            ))}
            {!names.length && <span className={css.note}>У модели нет полей</span>}
          </div>
          <div className={mod.chips} role="group" aria-label="Функции" data-testid="calc-functions">
            {FUNCTIONS.map((f) => (
              <button key={f.name} type="button" title={f.hint} data-testid={`calc-fn-${f.name}`} onClick={() => insert(f.text)}>
                {f.name}
              </button>
            ))}
          </div>
          <div className={css.row}>
            <button type="button" className={css.btn} disabled={!form.expr.trim()} title="Проверить выражение на модели: тип результата, прочитанные поля, место ошибки" data-testid="calc-check" onClick={() => void doCheck()}>
              Проверить
            </button>
          </div>
        </fieldset>

        <fieldset className={css.group} disabled={running || modelId === null}>
          <legend>Новое поле</legend>
          <div className={css.pair}>
            <label className={css.field}>
              <span>Имя</span>
              <input className={css.input} value={form.field} list="calc-field-names" spellCheck={false} aria-label="Имя нового поля" data-testid="calc-name" onChange={(e) => setCalc({ field: e.target.value })} />
            </label>
            <datalist id="calc-field-names">
              {names.map((n) => (
                <option key={n} value={n} />
              ))}
            </datalist>
            <label className={css.field}>
              <span>Тип</span>
              <select className={css.input} value={form.dtype} aria-label="Тип поля" data-testid="calc-dtype" onChange={(e) => setCalc({ dtype: e.target.value as "" | Dtype })}>
                <option value="">по выражению{check.result && fresh ? `: ${check.result.dtype}` : ""}</option>
                {DTYPES.map((d) => (
                  <option key={d} value={d}>
                    {DTYPE_LABEL[d]}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <IssueLine tab="calc" place="field" />
          <IssueLine tab="calc" place="dtype" />
          {exists && (
            <p className={css.warn} data-testid="calc-exists">
              Поле «{form.field.trim()}» в модели уже есть: перед записью будет вопрос о замене.
            </p>
          )}
          <div className={css.pair}>
            <label className={css.field}>
              <span>Единица</span>
              <input className={css.input} value={form.unit} aria-label="Единица" placeholder="г/т" data-testid="calc-unit" onChange={(e) => setCalc({ unit: e.target.value })} />
            </label>
            <label className={css.field}>
              <span>Описание</span>
              <input className={css.input} value={form.description} aria-label="Описание" data-testid="calc-description" onChange={(e) => setCalc({ description: e.target.value })} />
            </label>
          </div>
          <IssueLine tab="calc" place="unit" />
          <IssueLine tab="calc" place="description" />
        </fieldset>

        <fieldset className={css.group} disabled={running || modelId === null}>
          <legend>Ограничение</legend>
          <RestrictBox tab="calc" value={form.r} onChange={(p) => setCalc({ r: { ...form.r, ...p } })} fields={names} disabled={running} testId="calc-restrict" />
          <div className={mod.radios} role="radiogroup" aria-label="Вне ограничителя">
            <label>
              <input type="radio" name="calc-outside" checked={form.outside === "keep"} data-testid="calc-outside-keep" onChange={() => setCalc({ outside: "keep" })} />
              Вне ограничителя оставить прежнее значение (у нового поля — пусто)
            </label>
            <label>
              <input type="radio" name="calc-outside" checked={form.outside === "nan"} data-testid="calc-outside-nan" onChange={() => setCalc({ outside: "nan" })} />
              Вне ограничителя — пусто (NaN)
            </label>
          </div>
          <IssueLine tab="calc" place="outside" />
        </fieldset>
        <IssueLine tab="calc" place="general" />
        <IssueLine tab="calc" place="model" />
        <RunView tab="calc" />
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="calc-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={running || !!reason} title={reason ?? "Вычислить поле по выражению и записать его в модель"} data-testid="calc-build" onClick={() => void build()}>
          Вычислить
        </button>
        {running && (
          <button type="button" className={css.btn} disabled={!run.job} title="Остановить расчёт: поле записывается целиком или не записывается" data-testid="calc-cancel" onClick={cancel}>
            Отменить
          </button>
        )}
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="fields-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </>
  );
}

/** The window: the model and the tabs on top, the tab below. */
export function CalcWindow({ winId }: { winId: string }) {
  const tab = useSel(fieldsStore, (s) => s.tab);
  const run = useSel(fieldsStore, (s) => s.run);
  const running = run.phase === "running" && isRunning();
  return (
    <div className={mod.window} data-testid="fields-window">
      <div className={mod.head}>
        <ModelPicker disabled={running} onChoose={chooseModel} />
        <div className={mod.tabs} role="tablist" aria-label="Операции над полями">
          {TABS.map((t) => (
            <button key={t} type="button" role="tab" aria-selected={tab === t} data-testid={`fields-tab-${t}`} onClick={() => showTab(t)}>
              {TAB_TITLES[t]}
            </button>
          ))}
        </div>
      </div>
      {tab === "calc" && <CalcPanel winId={winId} />}
      {tab === "density" && <DensityPanel winId={winId} />}
      {tab === "classify" && <CategoriesPanel winId={winId} />}
      {tab === "props" && <FieldProps winId={winId} />}
    </div>
  );
}
