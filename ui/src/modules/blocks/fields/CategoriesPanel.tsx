/**
 * The tab «Категории» of the window «Поля блочной модели» (ТЗ 8.7.5; ADR 0023 §9, decision 13, № 68): the scheme of the
 * project (JORC, ГКЗ, the user's own) and its rules in order — the first rule a block fulfils gives it the label —, the
 * field of the estimate (a block with no estimate gets no category), the wireframes of the categories that override a
 * rule, the majority filter 3×3×3, the restrictor; the editor of the schemes of the project (the id, the name, the version,
 * «Сохранить схему»: 409 asked in our own window, 423 told). «Присвоить категории» runs the command `blocks.classify` through
 * the registry; a wrong form is told under its field before anything is sent; the refusals of the core stand in their
 * place (a rule of the table is marked by its `param`, `rules.2.expr`).
 */
import { useMemo, useState } from "react";
import { useApp } from "../../../shell/AppContext";
import css from "../../../shell/PropsParts.module.css";
import { errorText } from "../../../shell/props";
import { closeWindow } from "../../../shell/windows";
import { sceneObjects } from "../../scene/objects";
import { blocksStore } from "../state";
import { argsOfForm, MAX_MAJORITY, MAX_RULES, pickScheme, sameRules, schemeArgsOfForm } from "./classifyForm";
import { IssueLine, RestrictBox, RunView, useSel } from "./CommonParts";
import mod from "./Fields.module.css";
import { checkRules, refreshSchemes } from "./opsBind";
import { cancelRun } from "./run";
import { fieldsStore, modelFields, setClassify, setIssue } from "./state";

const OWN = "";

export function CategoriesPanel({ winId }: { winId: string }) {
  const app = useApp();
  const form = useSel(fieldsStore, (s) => s.classify);
  const schemes = useSel(fieldsStore, (s) => s.schemes);
  const rulesCheck = useSel(fieldsStore, (s) => s.rulesCheck);
  const modelId = useSel(fieldsStore, (s) => s.modelId);
  const run = useSel(fieldsStore, (s) => s.run);
  const readOnly = useSel(app.store, (s) => s.projectReadOnly);
  const models = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const wireframes = useSel(sceneObjects, (s) => s.wireframes);
  const issueParam = useSel(fieldsStore, (s) => (s.issue?.tab === "classify" ? s.issue.param : null));
  const issue = useSel(fieldsStore, (s) => (s.issue?.tab === "classify" ? s.issue : null));
  const [note, setNote] = useState<string | null>(null);
  const running = run.phase === "running";

  const fields = useMemo(() => modelFields(modelId), [modelId, models, details]);
  const names = useMemo(() => fields.map((f) => f.name), [fields]);
  const scheme = schemes.list.find((s) => s.id === form.schemeId);
  const edited = scheme !== undefined && !sameRules(form.rules, scheme);
  const labels = form.rules.map((r) => r.label).filter((l) => l.trim());
  const reason = readOnly ? "Проект открыт только для чтения" : modelId === null ? "Выберите блочную модель" : null;
  const exists = form.out.trim() !== "" && fields.some((f) => f.name.toLowerCase() === form.out.trim().toLowerCase());
  const editing = schemes.list.find((s) => s.id === form.editId.trim());
  const rowBad = (i: number, key: "label" | "expr") => issueParam === `rules.${i}.${key}`;

  const setRule = (i: number, patch: Partial<{ label: string; expr: string }>) => setClassify({ rules: form.rules.map((r, k) => (k === i ? { ...r, ...patch } : r)) });
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= form.rules.length) return;
    const rules = [...form.rules];
    [rules[i], rules[j]] = [rules[j], rules[i]];
    setClassify({ rules });
  };
  const build = async () => {
    setNote(null);
    const got = argsOfForm(modelId, form, schemes.list);
    if (!got.ok) return setIssue(got.issue);
    const r = await app.registry.execute("blocks.classify", got.args, "user");
    if (!r.ok && !fieldsStore.get().issue) setNote(r.result);
  };
  const saveScheme = async () => {
    setNote(null);
    const got = schemeArgsOfForm(form, schemes.list);
    if (!got.ok) return setIssue(got.issue);
    const r = await app.registry.execute("blocks.scheme.save", got.args, "user");
    if (!r.ok && !fieldsStore.get().issue) setNote(r.result);
  };
  const cancel = () => void cancelRun().catch((e: unknown) => setNote(errorText(e)));

  return (
    <>
      <div className={mod.body} data-testid="classify-panel">
        <fieldset className={css.group} disabled={running}>
          <legend>Схема категорий</legend>
          <label className={css.field}>
            <span>Схема проекта</span>
            <select className={css.input} value={form.schemeId} aria-label="Схема категорий" data-testid="classify-scheme" onChange={(e) => setClassify(pickScheme(form, schemes.list, e.target.value))}>
              {schemes.list.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.id}, версия {s.version})
                </option>
              ))}
              <option value={OWN}>Свои правила (не сохранены)</option>
            </select>
          </label>
          {schemes.error && (
            <p className={css.error} role="alert" data-testid="classify-schemes-error">
              {schemes.error}{" "}
              <button type="button" className={css.btn} onClick={() => void refreshSchemes()}>
                Перечитать
              </button>
            </p>
          )}
          <IssueLine tab="classify" place="scheme" />
          <div className={mod.rulesList} data-testid="classify-rules">
            {form.rules.map((r, i) => (
              <div key={i}>
                <div className={`${mod.rowEdit} ${mod.rules}`} data-testid={`classify-rule-${i}`}>
                  <input className={css.input} value={r.label} placeholder="Подпись" aria-label={`Подпись категории ${i + 1}`} aria-invalid={rowBad(i, "label") || undefined} data-testid={`classify-rule-${i}-label`} onChange={(e) => setRule(i, { label: e.target.value })} />
                  <input className={`${css.input} ${mod.mono}`} value={r.expr} spellCheck={false} placeholder={scheme && scheme.rules[i] && scheme.rules[i].expr === null ? "порог не задан" : "условие 8.7.4"} aria-label={`Условие категории ${i + 1}`} aria-invalid={rowBad(i, "expr") || rulesCheck[i] != null || undefined} data-testid={`classify-rule-${i}-expr`} onChange={(e) => setRule(i, { expr: e.target.value })} />
                  <span className={css.row}>
                    <button type="button" className={css.iconBtn} disabled={i === 0} title="Выше: правила идут по порядку" aria-label={`Выше: правило ${i + 1}`} data-testid={`classify-rule-${i}-up`} onClick={() => move(i, -1)}>
                      ↑
                    </button>
                    <button type="button" className={css.iconBtn} disabled={i === form.rules.length - 1} title="Ниже: правила идут по порядку" aria-label={`Ниже: правило ${i + 1}`} data-testid={`classify-rule-${i}-down`} onClick={() => move(i, 1)}>
                      ↓
                    </button>
                    <button type="button" className={css.iconBtn} title="Удалить правило" aria-label={`Удалить правило ${i + 1}`} data-testid={`classify-rule-${i}-remove`} onClick={() => setClassify({ rules: form.rules.filter((_, k) => k !== i) })}>
                      ✕
                    </button>
                  </span>
                </div>
                {rulesCheck[i] != null && (
                  <p className={css.error} role="alert" data-testid={`classify-rule-${i}-check`}>
                    {rulesCheck[i]}
                  </p>
                )}
                {issue && (issue.param === `rules.${i}.expr` || issue.param === `rules.${i}.label`) && (
                  <p className={css.error} role="alert" data-testid={`classify-rule-${i}-issue`}>
                    {issue.message} {issue.hint}
                  </p>
                )}
              </div>
            ))}
            {!form.rules.length && <p className={css.note}>Правил нет: выберите схему или добавьте правило.</p>}
          </div>
          <IssueLine tab="classify" place="rules" />
          <div className={css.row}>
            <button type="button" className={css.btn} disabled={form.rules.length >= MAX_RULES} data-testid="classify-add-rule" onClick={() => setClassify({ rules: [...form.rules, { label: "", expr: "" }] })}>
              Добавить правило
            </button>
            <button type="button" className={css.btn} disabled={modelId === null || !form.rules.length} title="Проверить условия правил на выбранной модели" data-testid="classify-check-rules" onClick={() => void checkRules()}>
              Проверить правила
            </button>
          </div>
          <p className={css.note} data-testid="classify-scheme-note">
            {edited ? "Правила изменены: расчёт пойдёт по ним, а не по схеме проекта; «Сохранить схему» запишет их в справочник." : "Первое выполненное правило задаёт категорию блока. Блок с оценкой, которому не подошло ни одно правило, остаётся без категории."}
          </p>
        </fieldset>

        <fieldset className={css.group} disabled={running}>
          <legend>Схема в справочнике проекта</legend>
          <div className={css.pair}>
            <label className={css.field}>
              <span>Код (латиница, цифры, «_», «-»)</span>
              <input className={css.input} value={form.editId} spellCheck={false} aria-label="Код схемы" data-testid="classify-edit-id" onChange={(e) => setClassify({ editId: e.target.value })} />
            </label>
            <label className={css.field}>
              <span>Название</span>
              <input className={css.input} value={form.editName} aria-label="Название схемы" data-testid="classify-edit-name" onChange={(e) => setClassify({ editName: e.target.value })} />
            </label>
          </div>
          <IssueLine tab="classify" place="scheme_id" />
          <IssueLine tab="classify" place="scheme_name" />
          <div className={css.row}>
            <button type="button" className={css.btn} disabled={!!readOnly} title={readOnly ? "Проект открыт только для чтения" : "Записать правила таблицы в справочник схем проекта"} data-testid="classify-save-scheme" onClick={() => void saveScheme()}>
              Сохранить схему
            </button>
            <span className={css.note} data-testid="classify-edit-version">
              {editing ? `Схема «${editing.name}», версия ${editing.version}: запись заменит её по этой версии.` : form.editId.trim() ? "Новая схема." : "Задайте код, чтобы записать схему."}
            </span>
          </div>
        </fieldset>

        <fieldset className={css.group} disabled={running || modelId === null}>
          <legend>Оценка и фильтр</legend>
          <label className={css.field}>
            <span>Поле оценки (пусто — блок без оценки, категории нет)</span>
            <select className={css.input} value={form.estimate} aria-label="Поле оценки" data-testid="classify-estimate" onChange={(e) => setClassify({ estimate: e.target.value })}>
              <option value="">нет: оценка есть у всех блоков</option>
              {fields.map((f) => (
                <option key={f.name} value={f.name}>
                  {f.name}
                </option>
              ))}
            </select>
          </label>
          <IssueLine tab="classify" place="estimate" />
          <label className={css.field}>
            <span>Мажоритарный фильтр 3×3×3 по родителям, проходов от 0 до {MAX_MAJORITY}</span>
            <input className={css.input} value={form.majority} inputMode="numeric" aria-label="Проходов мажоритарного фильтра" data-testid="classify-majority" onChange={(e) => setClassify({ majority: e.target.value })} />
          </label>
          <IssueLine tab="classify" place="majority" />
        </fieldset>

        <fieldset className={css.group} disabled={running || modelId === null}>
          <legend>Каркасы категорий</legend>
          <p className={css.note}>Блок с оценкой внутри каркаса получает категорию каркаса вместо правила; при нескольких каркасах — первый.</p>
          {form.overrides.map((o, i) => (
            <div key={i} className={`${mod.rowEdit} ${mod.over}`} data-testid={`classify-over-${i}`}>
              <select className={css.input} value={o.wireframeId ?? ""} aria-label={`Каркас категории ${i + 1}`} aria-invalid={issueParam === `overrides.${i}.wireframe_id` || undefined} data-testid={`classify-over-${i}-wireframe`} onChange={(e) => setClassify({ overrides: form.overrides.map((x, k) => (k === i ? { ...x, wireframeId: e.target.value ? Number(e.target.value) : null } : x)) })}>
                <option value="">— каркас —</option>
                {wireframes.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </select>
              <select className={css.input} value={o.label} aria-label={`Категория каркаса ${i + 1}`} aria-invalid={issueParam === `overrides.${i}.label` || undefined} data-testid={`classify-over-${i}-label`} onChange={(e) => setClassify({ overrides: form.overrides.map((x, k) => (k === i ? { ...x, label: e.target.value } : x)) })}>
                <option value="">— категория —</option>
                {labels.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
              <button type="button" className={css.iconBtn} title="Удалить каркас категории" aria-label={`Удалить каркас категории ${i + 1}`} onClick={() => setClassify({ overrides: form.overrides.filter((_, k) => k !== i) })}>
                ✕
              </button>
            </div>
          ))}
          <div className={css.row}>
            <button type="button" className={css.btn} disabled={!wireframes.length || !labels.length} title={wireframes.length ? "Добавить каркас категории" : "В проекте нет каркасов"} data-testid="classify-add-over" onClick={() => setClassify({ overrides: [...form.overrides, { wireframeId: null, label: "" }] })}>
              Добавить каркас
            </button>
          </div>
          <IssueLine tab="classify" place="overrides" />
        </fieldset>

        <fieldset className={css.group} disabled={running || modelId === null}>
          <legend>Ограничение и поле результата</legend>
          <RestrictBox tab="classify" value={form.r} onChange={(p) => setClassify({ r: { ...form.r, ...p } })} fields={names} disabled={running} testId="classify-restrict" />
          <label className={css.field}>
            <span>Имя поля категорий</span>
            <input className={css.input} value={form.out} spellCheck={false} aria-label="Имя поля категорий" data-testid="classify-out" onChange={(e) => setClassify({ out: e.target.value })} />
          </label>
          <IssueLine tab="classify" place="out" />
          {exists && (
            <p className={css.warn} data-testid="classify-exists">
              Поле «{form.out.trim()}» в модели уже есть: перед записью будет вопрос о замене.
            </p>
          )}
        </fieldset>
        <IssueLine tab="classify" place="general" />
        <IssueLine tab="classify" place="model" />
        <RunView tab="classify" />
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="classify-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={running || !!reason} title={reason ?? "Присвоить категории блокам и записать поле в модель"} data-testid="classify-build" onClick={() => void build()}>
          Присвоить категории
        </button>
        {running && (
          <button type="button" className={css.btn} disabled={!run.job} title="Остановить расчёт: поле записывается целиком или не записывается" data-testid="classify-cancel" onClick={cancel}>
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
