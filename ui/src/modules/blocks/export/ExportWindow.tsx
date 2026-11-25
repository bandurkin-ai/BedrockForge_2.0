/**
 * The window «Экспорт блочной модели · <модель>» (ТЗ 8.7.7, 7.35 Д-17; ADR 0023 §12): the model, the format (Micromine DAT,
 * CSV, Parquet, OMF v1) with its options — the encoding, the delimiter and the decimal mark of a CSV, the header of
 * Micromine, the code of «пусто» —, the fields (all, or chosen in the order asked), the filter of the library and the
 * quick restrictor (only the blocks that pass are written); on the right the run (the stage, «Отменить») and the file
 * written. «Сохранить…» asks for the file in the save window of the system and runs the command `blocks.export` through
 * the registry (the log, the notice, the question of the assistant); a wrong form is told under its field in Russian
 * before anything is sent. Floating, not modal.
 */
import { useMemo, useState, useSyncExternalStore } from "react";
import { bridge } from "../../../bridge";
import { useApp } from "../../../shell/AppContext";
import css from "../../../shell/PropsParts.module.css";
import { errorText } from "../../../shell/props";
import type { Store } from "../../../shell/store";
import { closeWindow } from "../../../shell/windows";
import { jobsStore, STATUS_TITLES } from "../../jobs/state";
import { sceneObjects } from "../../scene/objects";
import { blocksStore } from "../state";
import { RuleEditor, type ObjectOption } from "../filters/RuleEditor";
import { filtersByName, filtersStore } from "../filters/state";
import mod from "../report/Report.module.css";
import { cancelExport, chooseFile, chooseModel, fieldNamesOf, hasSubBlocks, saveFromWindow } from "./bind";
import {
  bodyOfForm,
  chosenPath,
  CSV_ENCODINGS,
  DAT_ENCODINGS,
  DELIMITERS,
  ENCODING_TITLES,
  formatInfo,
  FORMATS,
  omfReason,
  selectionText,
  sizeText,
  takesEmptyCode,
  type Decimal,
  type Delimiter,
  type FormField,
  type Format,
} from "./model";
import { changeFormat, exportStore, setForm, setIssue } from "./state";

function useSel<T, R>(store: Store<T>, select: (s: T) => R): R {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}

/** The words under a field of the form (the first wrong thing of the form or the refusal of the core). */
function Issue({ field }: { field: FormField }) {
  const issue = useSel(exportStore, (s) => s.issue);
  if (!issue || issue.field !== field) return null;
  return (
    <p className={css.error} role="alert" data-testid={`export-issue-${field}`}>
      {issue.message} {issue.hint}
    </p>
  );
}

const n = (v: number) => v.toLocaleString("ru-RU");

export function ExportWindow({ winId }: { winId: string }) {
  const app = useApp();
  const form = useSel(exportStore, (s) => s.form);
  const run = useSel(exportStore, (s) => s.run);
  const models = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const library = useSel(filtersStore, (s) => s.rows);
  const wireframes = useSel(sceneObjects, (s) => s.wireframes);
  const live = useSel(jobsStore, (s) => (run.job ? s.jobs.find((j) => j.source === run.job?.source && j.id === run.job?.id) : undefined));
  const [note, setNote] = useState<string | null>(null);
  const reason = app.registry.reason("blocks.export");
  const running = run.phase === "running";

  const model = models.find((m) => m.id === form.modelId);
  const fields = useMemo(() => (form.modelId === null ? [] : ((details[form.modelId] ?? model)?.fields ?? [])), [form.modelId, model, details]);
  const filters = useMemo(() => filtersByName(library), [library]);
  const options = useMemo<ObjectOption[]>(() => wireframes.map((w) => ({ id: w.id, name: w.name, dtm: w.dtm !== null })), [wireframes]);
  const info = formatInfo(form.format);
  const path = chosenPath(form);
  const omfWhy = form.modelId !== null && model ? omfReason(hasSubBlocks(form.modelId), model.name) : null;
  const encodings = form.format === "dat" ? DAT_ENCODINGS : form.format === "csv" ? CSV_ENCODINGS : [];
  const set = setForm;

  const save = async () => {
    setNote(null);
    // the checks of the form first: the file is asked for only when the rest is right
    const check = bodyOfForm({ ...form, path: path ?? "?", pathFormat: form.format }, fieldNamesOf(form.modelId));
    if (!check.ok) return setIssue(check.issue);
    try {
      await saveFromWindow(app);
    } catch (e) {
      if (!exportStore.get().issue) setNote(errorText(e));
    }
  };
  const cancel = () => void cancelExport().catch((e: unknown) => setNote(errorText(e)));
  const toggleField = (name: string, on: boolean) => set({ fields: on ? (form.fields.includes(name) ? form.fields : [...form.fields, name]) : form.fields.filter((x) => x !== name) });
  const pct = Math.round((live?.progress ?? 0) * 100);

  return (
    <div className={mod.window} data-testid="export-window">
      <div className={mod.layout}>
        <div className={mod.form}>
          <fieldset className={css.group} disabled={running}>
            <legend>Модель</legend>
            <select className={css.input} value={form.modelId ?? ""} aria-label="Блочная модель" data-testid="export-model" onChange={(e) => chooseModel(e.target.value ? Number(e.target.value) : null)}>
              <option value="">{models.length ? "— выберите модель —" : "нет блочных моделей"}</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
            <Issue field="model" />
          </fieldset>

          <fieldset className={css.group} disabled={running || form.modelId === null}>
            <legend>Формат</legend>
            <div className={mod.radios} role="radiogroup" aria-label="Формат файла">
              {FORMATS.map((f) => {
                const off = f.id === "omf" ? omfWhy : null;
                return (
                  <label key={f.id} title={off ?? undefined}>
                    <input type="radio" name="export-format" checked={form.format === f.id} disabled={off !== null} data-testid={`export-format-${f.id}`} onChange={() => changeFormat(f.id as Format)} />
                    {f.title} ({f.ext}){off ? " — только после регуляризации" : ""}
                  </label>
                );
              })}
            </div>
            <span className={css.note} data-testid="export-format-note">
              {info.note}
            </span>
            {omfWhy && <span className={css.warn}>{omfWhy}</span>}
            <Issue field="format" />
          </fieldset>

          <fieldset className={css.group} disabled={running || form.modelId === null}>
            <legend>Поля</legend>
            <div className={mod.radios} role="radiogroup" aria-label="Поля">
              <label>
                <input type="radio" name="export-fields" checked={form.allFields} data-testid="export-fields-all" onChange={() => set({ allFields: true })} />
                Все поля модели
              </label>
              <label>
                <input type="radio" name="export-fields" checked={!form.allFields} data-testid="export-fields-chosen" onChange={() => set({ allFields: false })} />
                Выбранные, в порядке отметки
              </label>
            </div>
            {!form.allFields && (
              <div className={mod.list} role="group" aria-label="Поля для записи" data-testid="export-fields">
                {fields.map((f) => (
                  <label key={f.name}>
                    <input type="checkbox" checked={form.fields.includes(f.name)} data-testid={`export-field-${f.name}`} onChange={(e) => toggleField(f.name, e.target.checked)} />
                    <span>
                      {f.name}
                      {f.unit && <span className={mod.unit}>, {f.unit}</span>}
                      {f.kind === "category" && <span className={mod.unit}> · категория</span>}
                    </span>
                  </label>
                ))}
                {!fields.length && <span className={css.note}>У модели нет полей</span>}
              </div>
            )}
            {!form.allFields && form.fields.length > 0 && <span className={css.note}>Порядок столбцов: {form.fields.join(", ")}</span>}
            <Issue field="fields" />
          </fieldset>

          <fieldset className={css.group} disabled={running || form.modelId === null}>
            <legend>Отбор блоков</legend>
            <label className={css.field}>
              <span>Фильтр библиотеки</span>
              <select className={css.input} value={form.filterId ?? ""} aria-label="Фильтр библиотеки" data-testid="export-filter" onChange={(e) => set({ filterId: e.target.value ? Number(e.target.value) : null })}>
                <option value="">без фильтра</option>
                {form.filterId !== null && !filters.some((f) => f.id === form.filterId) && <option value={form.filterId}>№{form.filterId} (нет в проекте)</option>}
                {filters.map((f) => (
                  <option key={f.id} value={f.id} title={f.title}>
                    {f.name}
                  </option>
                ))}
              </select>
            </label>
            <Issue field="filter" />
            <label className={css.check}>
              <input type="checkbox" checked={form.restrictOn} data-testid="export-restrict-on" onChange={(e) => set({ restrictOn: e.target.checked })} />
              Быстрый ограничитель
            </label>
            {form.restrictOn && <RuleEditor draft={form.restrict} onChange={(restrict) => set({ restrict })} issue={null} fields={fields.map((f) => f.name)} wireframes={options} testId="export-restrict" />}
            <Issue field="restrict" />
            <span className={css.note}>Записываются только блоки, прошедшие фильтр и ограничитель вместе; блок проходит, если его доля больше нуля.</span>
          </fieldset>

          {(form.format === "dat" || form.format === "csv") && (
            <fieldset className={css.group} disabled={running || form.modelId === null}>
              <legend>Запись</legend>
              <label className={css.field}>
                <span>Кодировка</span>
                <select className={css.input} value={form.encoding} aria-label="Кодировка" data-testid="export-encoding" onChange={(e) => set({ encoding: e.target.value })}>
                  <option value="">по умолчанию ({ENCODING_TITLES[encodings[0]]})</option>
                  {encodings.map((e) => (
                    <option key={e} value={e}>
                      {ENCODING_TITLES[e] ?? e}
                    </option>
                  ))}
                </select>
              </label>
              <Issue field="encoding" />
              {form.format === "csv" && (
                <>
                  <label className={css.field}>
                    <span>Заголовок</span>
                    <select className={css.input} value={form.header} aria-label="Заголовок" data-testid="export-header" onChange={(e) => set({ header: e.target.value as "plain" | "micromine" })}>
                      <option value="plain">одна строка — имена столбцов</option>
                      <option value="micromine">Micromine — три строки: имена, типы, сетка</option>
                    </select>
                  </label>
                  <Issue field="header" />
                  <label className={css.field}>
                    <span>Разделитель</span>
                    <select className={css.input} value={form.delimiter} aria-label="Разделитель столбцов" data-testid="export-delimiter" onChange={(e) => set({ delimiter: e.target.value as Delimiter })}>
                      {DELIMITERS.map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.title}
                        </option>
                      ))}
                    </select>
                  </label>
                  <Issue field="delimiter" />
                  <label className={css.field}>
                    <span>Десятичный знак</span>
                    <select className={css.input} value={form.decimal} aria-label="Десятичный знак" data-testid="export-decimal" onChange={(e) => set({ decimal: e.target.value as Decimal })}>
                      <option value=",">запятая</option>
                      <option value=".">точка</option>
                    </select>
                  </label>
                  <Issue field="decimal" />
                </>
              )}
              {takesEmptyCode(form.format) && (
                <>
                  <label className={css.check}>
                    <input type="checkbox" checked={form.emptyOn} data-testid="export-empty-on" onChange={(e) => set({ emptyOn: e.target.checked })} />
                    Писать пропуск числом
                  </label>
                  {form.emptyOn && <input className={css.input} value={form.empty} inputMode="decimal" aria-label="Число вместо пропуска" data-testid="export-empty" onChange={(e) => set({ empty: e.target.value })} />}
                  <span className={css.note}>Без числа пропуск пишется пустым значением — не нулём.</span>
                  <Issue field="empty" />
                </>
              )}
            </fieldset>
          )}

          <fieldset className={css.group} disabled={running}>
            <legend>Файл</legend>
            <div className={mod.bar}>
              <button type="button" className={css.btn} data-testid="export-choose" disabled={form.modelId === null} onClick={() => void chooseFile(form).catch((e: unknown) => setNote(errorText(e)))}>
                Выбрать файл…
              </button>
              <span className={`${css.note} ${mod.grow}`} data-testid="export-path" title={path ?? undefined}>
                {path ?? "не выбран"}
              </span>
            </div>
            <Issue field="path" />
          </fieldset>
          <Issue field="general" />
        </div>

        <div className={mod.result} data-testid="export-result">
          <header className={mod.head}>
            <h4>{run.model ? `Экспорт · ${run.model}` : "Экспорт модели"}</h4>
            <span className={css.note}>
              {run.phase === "idle" && "Выберите файл и нажмите «Сохранить…»: модель записывается заданием, ход виден здесь и на вкладке «Задания»."}
              {run.phase === "running" && `${STATUS_TITLES[live?.status ?? "queued"]} · ${pct}%${live?.message ? ` · ${live.message}` : ""}`}
              {run.phase === "done" && "Файл записан"}
              {run.phase === "cancelled" && "Экспорт отменён: файл не создан"}
              {run.phase === "failed" && "Экспорт не выполнен: файл не создан"}
            </span>
          </header>
          {running && (
            <div className={mod.progress} data-testid="export-progress">
              <div style={{ width: `${pct}%` }} />
            </div>
          )}
          {run.phase === "failed" && run.error && (
            <p className={css.error} role="alert" data-testid="export-error" data-code={run.error.code}>
              {run.error.message} {run.error.hint}
            </p>
          )}
          {run.result && (
            <div data-testid="export-done">
              <table className={css.table}>
                <tbody>
                  <tr>
                    <th>Файл</th>
                    <td>{run.result.path}</td>
                  </tr>
                  <tr>
                    <th>Формат</th>
                    <td>{formatInfo(run.result.format).title}</td>
                  </tr>
                  <tr>
                    <th>Блоков</th>
                    <td>{run.result.rows === run.result.total ? n(run.result.rows) : `${n(run.result.rows)} из ${n(run.result.total)}`}</td>
                  </tr>
                  {run.result.bytes !== null && (
                    <tr>
                      <th>Размер</th>
                      <td>{sizeText(run.result.bytes)}</td>
                    </tr>
                  )}
                </tbody>
              </table>
              {selectionText(run.result) && <p className={css.note}>{selectionText(run.result)}</p>}
              {run.result.warnings.length > 0 && (
                <ul className={css.list} data-testid="export-warnings">
                  {run.result.warnings.map((w) => (
                    <li key={w} className={css.warn}>
                      {w}
                    </li>
                  ))}
                </ul>
              )}
              <button type="button" className={css.btn} data-testid="export-show" onClick={() => bridge()?.showInFolder(run.result?.path ?? "")}>
                Показать в папке
              </button>
            </div>
          )}
          {note && (
            <p className={css.error} role="alert" data-testid="export-note">
              {note}
            </p>
          )}
        </div>
      </div>
      <div className={mod.footer}>
        <button type="button" className={`${css.btn} ${css.primary}`} disabled={running || reason !== null} title={reason ?? undefined} data-testid="export-save" onClick={() => void save()}>
          Сохранить…
        </button>
        {running && (
          <button type="button" className={css.btn} data-testid="export-cancel" onClick={cancel}>
            Отменить
          </button>
        )}
        <span className={mod.grow} />
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
