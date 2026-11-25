/**
 * The tab «Плотность» of the window «Поля блочной модели» (ТЗ 8.7.5; ADR 0023 §9, decision 13): how the density of a
 * block is set — a constant, a number field of the model, a table «домен → плотность» (by the label of a category or the
 * exact value of a number), a formula of 8.7.4 —, the density «по умолчанию» for a block with no value of its own (it gets
 * `DENS_SRC` = 1; without a default the density stays empty, never 0), the names of the two fields written, the
 * restrictor. «Рассчитать» runs the command `blocks.density` through the registry; a wrong form is told under its field
 * before anything is sent; the refusals of the core stand in their place (a row of the table is marked by its `param`).
 */
import { useMemo, useState } from "react";
import { useApp } from "../../../shell/AppContext";
import css from "../../../shell/PropsParts.module.css";
import { errorText } from "../../../shell/props";
import { closeWindow } from "../../../shell/windows";
import { blocksStore } from "../state";
import { argsOfForm, METHODS, rowsForLabels } from "./densityForm";
import { IssueLine, RestrictBox, RunView, useSel } from "./Common";
import mod from "./Fields.module.css";
import { cancelRun } from "./run";
import { fieldsStore, modelFields, setDensity, setIssue } from "./state";

function Row({ index, bad }: { index: number; bad: (param: string) => boolean }) {
  const f = useSel(fieldsStore, (s) => s.density);
  const modelId = useSel(fieldsStore, (s) => s.modelId);
  const fields = modelFields(modelId);
  const kind = fields.find((x) => x.name === f.tableField)?.kind;
  const labels = fields.find((x) => x.name === f.tableField)?.labels ?? [];
  const row = f.rows[index];
  const set = (patch: Partial<typeof row>) => setDensity({ rows: f.rows.map((r, i) => (i === index ? { ...r, ...patch } : r)) });
  return (
    <div className={mod.rowEdit} data-testid={`density-row-${index}`}>
      {kind === "category" && labels.length ? (
        <select className={css.input} value={row.value} aria-label={`Домен строки ${index + 1}`} aria-invalid={bad(`table.values.${index}.value`) || undefined} data-testid={`density-row-${index}-value`} onChange={(e) => set({ value: e.target.value })}>
          <option value="">— подпись —</option>
          {labels.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      ) : (
        <input className={css.input} value={row.value} aria-label={`Домен строки ${index + 1}`} aria-invalid={bad(`table.values.${index}.value`) || undefined} placeholder={kind === "number" ? "число" : "подпись"} data-testid={`density-row-${index}-value`} onChange={(e) => set({ value: e.target.value })} />
      )}
      <input className={css.input} value={row.density} inputMode="decimal" aria-label={`Плотность строки ${index + 1}, т/м³`} aria-invalid={bad(`table.values.${index}.density`) || undefined} placeholder="т/м³" data-testid={`density-row-${index}-density`} onChange={(e) => set({ density: e.target.value })} />
      <button type="button" className={css.iconBtn} title="Удалить строку" aria-label={`Удалить строку ${index + 1}`} onClick={() => setDensity({ rows: f.rows.filter((_, i) => i !== index) })}>
        ✕
      </button>
    </div>
  );
}

export function DensityPanel({ winId }: { winId: string }) {
  const app = useApp();
  const form = useSel(fieldsStore, (s) => s.density);
  const modelId = useSel(fieldsStore, (s) => s.modelId);
  const run = useSel(fieldsStore, (s) => s.run);
  const readOnly = useSel(app.store, (s) => s.projectReadOnly);
  const models = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const issueParam = useSel(fieldsStore, (s) => (s.issue?.tab === "density" ? s.issue.param : null));
  const [note, setNote] = useState<string | null>(null);
  const running = run.phase === "running";

  const fields = useMemo(() => modelFields(modelId), [modelId, models, details]);
  const numbers = fields.filter((f) => f.kind === "number");
  const domain = fields.find((f) => f.name === form.tableField);
  const names = useMemo(() => fields.map((f) => f.name), [fields]);
  const reason = readOnly ? "Проект открыт только для чтения" : modelId === null ? "Выберите блочную модель" : null;
  const exists = [form.out, form.srcField].filter((n) => n.trim() && fields.some((f) => f.name.toLowerCase() === n.trim().toLowerCase()));
  const bad = (param: string) => issueParam === param;

  const build = async () => {
    setNote(null);
    const got = argsOfForm(modelId, form, fields);
    if (!got.ok) return setIssue(got.issue);
    const r = await app.registry.execute("blocks.density", got.args, "user");
    if (!r.ok && !fieldsStore.get().issue) setNote(r.result);
  };
  const cancel = () => void cancelRun().catch((e: unknown) => setNote(errorText(e)));

  return (
    <>
      <div className={mod.body} data-testid="density-panel">
        <fieldset className={css.group} disabled={running || modelId === null}>
          <legend>Как задана плотность</legend>
          <div className={mod.radios} role="radiogroup" aria-label="Способ задания плотности">
            {METHODS.map((m) => (
              <label key={m.method} title={m.hint}>
                <input type="radio" name="density-method" checked={form.method === m.method} data-testid={`density-method-${m.method}`} onChange={() => setDensity({ method: m.method })} />
                {m.label}
              </label>
            ))}
          </div>
          <IssueLine tab="density" place="method" />
          {form.method === "constant" && (
            <label className={css.field}>
              <span>Плотность, т/м³</span>
              <input className={css.input} value={form.value} inputMode="decimal" aria-label="Плотность, т/м³" data-testid="density-value" onChange={(e) => setDensity({ value: e.target.value })} />
            </label>
          )}
          {form.method === "field" && (
            <label className={css.field}>
              <span>Числовое поле плотности</span>
              <select className={css.input} value={form.field} aria-label="Поле плотности" data-testid="density-field" onChange={(e) => setDensity({ field: e.target.value })}>
                <option value="">— выберите поле —</option>
                {numbers.map((f) => (
                  <option key={f.name} value={f.name}>
                    {f.unit ? `${f.name}, ${f.unit}` : f.name}
                    {f.constant ? " (постоянное)" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          {form.method === "formula" && (
            <label className={css.field}>
              <span>Формула 8.7.4</span>
              <textarea className={mod.expr} rows={3} value={form.expr} spellCheck={false} placeholder="2.5 + CU / 10" aria-label="Формула плотности" data-testid="density-expr" onChange={(e) => setDensity({ expr: e.target.value })} />
            </label>
          )}
          {form.method === "table" && (
            <>
              <label className={css.field}>
                <span>Поле домена</span>
                <select className={css.input} value={form.tableField} aria-label="Поле домена" data-testid="density-table-field" onChange={(e) => setDensity({ tableField: e.target.value, rows: [] })}>
                  <option value="">— выберите поле —</option>
                  {fields.map((f) => (
                    <option key={f.name} value={f.name}>
                      {f.name} ({f.kind === "category" ? "категория" : "число"})
                    </option>
                  ))}
                </select>
              </label>
              <div className={mod.rows} data-testid="density-rows">
                {form.rows.map((_, i) => (
                  <Row key={i} index={i} bad={bad} />
                ))}
              </div>
              <div className={css.row}>
                <button type="button" className={css.btn} disabled={!form.tableField} data-testid="density-add-row" onClick={() => setDensity({ rows: [...form.rows, { value: "", density: "" }] })}>
                  Добавить строку
                </button>
                {domain?.kind === "category" && domain.labels.length > 0 && (
                  <button type="button" className={css.btn} title="Строка на каждую подпись категории; введённые плотности остаются" data-testid="density-fill-labels" onClick={() => setDensity({ rows: rowsForLabels(domain.labels, form.rows) })}>
                    Заполнить подписями
                  </button>
                )}
              </div>
              <label className={css.field}>
                <span>Плотность домена, которого нет в таблице, т/м³</span>
                <input className={css.input} value={form.tableDefault} inputMode="decimal" aria-label="Плотность домена вне таблицы" placeholder="как «по умолчанию» ниже" data-testid="density-table-default" onChange={(e) => setDensity({ tableDefault: e.target.value })} />
              </label>
            </>
          )}
          <IssueLine tab="density" place="value" />
          <IssueLine tab="density" place="field" />
          <IssueLine tab="density" place="expr" />
          <IssueLine tab="density" place="table" />
        </fieldset>

        <fieldset className={css.group} disabled={running || modelId === null}>
          <legend>Блок без плотности</legend>
          <label className={css.field}>
            <span>Плотность «по умолчанию», т/м³</span>
            <input className={css.input} value={form.defaultText} inputMode="decimal" aria-label="Плотность по умолчанию, т/м³" placeholder="не задана" data-testid="density-default" onChange={(e) => setDensity({ defaultText: e.target.value })} />
          </label>
          <p className={css.note}>
            Блок, у которого нет своего значения (пропуск в поле или формуле, домен вне таблицы), получает это число, а в поле источника — 1. Без числа его плотность остаётся пустой (не 0), и такие блоки не войдут в тоннаж отчёта.
          </p>
          <IssueLine tab="density" place="default" />
        </fieldset>

        <fieldset className={css.group} disabled={running || modelId === null}>
          <legend>Поля результата</legend>
          <div className={css.pair}>
            <label className={css.field}>
              <span>Плотность</span>
              <input className={css.input} value={form.out} spellCheck={false} aria-label="Имя поля плотности" data-testid="density-out" onChange={(e) => setDensity({ out: e.target.value })} />
            </label>
            <label className={css.field}>
              <span>Источник (0 — своё значение, 1 — по умолчанию)</span>
              <input className={css.input} value={form.srcField} spellCheck={false} aria-label="Имя поля источника" data-testid="density-src" onChange={(e) => setDensity({ srcField: e.target.value })} />
            </label>
          </div>
          <IssueLine tab="density" place="out" />
          <IssueLine tab="density" place="src_field" />
          {exists.length > 0 && (
            <p className={css.warn} data-testid="density-exists">
              Поле «{exists.join("», «")}» в модели уже есть: перед записью будет вопрос о замене.
            </p>
          )}
        </fieldset>

        <fieldset className={css.group} disabled={running || modelId === null}>
          <legend>Ограничение</legend>
          <RestrictBox tab="density" value={form.r} onChange={(p) => setDensity({ r: { ...form.r, ...p } })} fields={names} disabled={running} testId="density-restrict" />
          <p className={css.note}>Вне ограничителя оба поля остаются пустыми.</p>
        </fieldset>
        <IssueLine tab="density" place="general" />
        <IssueLine tab="density" place="model" />
        <RunView tab="density" />
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="density-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={running || !!reason} title={reason ?? "Рассчитать плотность блоков и записать её в модель"} data-testid="density-build" onClick={() => void build()}>
          Рассчитать
        </button>
        {running && (
          <button type="button" className={css.btn} disabled={!run.job} title="Остановить расчёт: поля записываются целиком или не записываются" data-testid="density-cancel" onClick={cancel}>
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
