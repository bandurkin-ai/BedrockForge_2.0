import { useEffect, useMemo, useState } from "react";
import {
  commitImport,
  getSchemas,
  rereadFile,
  uploadFile,
  validateMapping,
  type Dataset,
  type ReadOptions,
  type Schemas,
  type UploadResponse,
} from "./api";
import { Icon } from "./shell/icons";
import { buildRequest, distribute, makeDraft, MAIN_ASSAYS, missingFields, TABLE_KINDS, tableKind, uniqueName, type Draft } from "./shell/importDrafts";
import { PreviewTable } from "./Table";

const ACCEPT = ".dat,.csv,.txt,.tsv,.prn,.xlsx,.xlsm";
const FORMAT_LABELS: Record<string, string> = { micromine: "Micromine DAT", text: "текст (CSV/TXT)", excel: "Excel" };
const ENCODINGS = ["utf-8", "utf-8-sig", "cp1251", "cp866", "koi8-r", "utf-16", "latin-1"];
const DELIMITERS: [string, string][] = [[";", "точка с запятой ;"], [",", "запятая ,"], ["\t", "табуляция"], ["|", "черта |"], ["whitespace", "пробелы"]];
const KIND_SHORT: Record<string, string> = { collar: "Устья", survey: "Инклинометрия", assay: "Пробы", interval: "Интервалы", event: "События" };
const fmt = (n: number) => n.toLocaleString("ru-RU");

function ReadOptionsPanel({
  upload,
  disabled,
  onChange,
}: {
  upload: UploadResponse;
  disabled: boolean;
  onChange: (options: ReadOptions) => void;
}) {
  const o = upload.options;
  const set = (patch: Partial<ReadOptions>) => onChange({ ...o, ...patch });
  return (
    <div className="read-options">
      {upload.format === "text" && (
        <>
          <label className="field">
            Кодировка
            <select value={o.encoding ?? ""} disabled={disabled} onChange={(e) => set({ encoding: e.target.value || null })}>
              {ENCODINGS.map((enc) => <option key={enc} value={enc}>{enc}</option>)}
            </select>
          </label>
          <label className="field">
            Разделитель
            <select value={o.delimiter ?? ""} disabled={disabled} onChange={(e) => set({ delimiter: e.target.value || null })}>
              {DELIMITERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
        </>
      )}
      {upload.format === "excel" && (
        <label className="field">
          Лист
          <select value={o.sheet ?? ""} disabled={disabled} onChange={(e) => set({ sheet: e.target.value })}>
            {upload.sheets.map((sheet) => <option key={sheet} value={sheet}>{sheet}</option>)}
          </select>
        </label>
      )}
      <label className="field">
        Строка заголовка
        <input
          type="number"
          min={0}
          max={1000}
          value={o.header_row}
          disabled={disabled}
          onChange={(e) => {
            const row = Number(e.target.value);
            if (Number.isInteger(row) && row >= 0) set({ header_row: row });
          }}
        />
        <span className="muted small">пустые строки не считаются; 0 — заголовка нет</span>
      </label>
    </div>
  );
}

function FileDrop({ multiple, busy, text, onFiles }: { multiple?: boolean; busy: boolean; text: string; onFiles: (files: File[]) => void }) {
  const [over, setOver] = useState(false);
  return (
    <label
      className={over ? "drop over" : "drop"}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!busy) onFiles([...e.dataTransfer.files]);
      }}
    >
      <input type="file" accept={ACCEPT} multiple={multiple} disabled={busy}
        onChange={(e) => { onFiles([...(e.target.files ?? [])]); e.target.value = ""; }} />
      <span>{busy ? "Обработка…" : text}</span>
    </label>
  );
}

/** One file of the wizard: how it is read, which columns go where, and what the check says. */
function DraftEditor({ draft, schemas, kinds, busy, onChange, onReread }: {
  draft: Draft;
  schemas: Schemas;
  /** kinds the user may choose on this step; one kind: no choice */
  kinds: string[];
  busy: boolean;
  onChange: (next: Draft) => void;
  onReread: (options: ReadOptions) => void;
}) {
  const { upload, kind, mapping, components } = draft;
  const set = (patch: Partial<Draft>) => onChange({ ...draft, ...patch, result: null, committed: null, error: null });
  const mappedColumns = useMemo(() => new Set(Object.values(mapping).filter(Boolean)), [mapping]);
  const freeColumns = upload.columns.filter((c) => !mappedColumns.has(c.name));
  const numericFree = freeColumns.filter((c) => c.numeric);
  const fields = schemas[kind]?.fields ?? [];
  const attributes = new Set(draft.attributes);
  const table = TABLE_KINDS.includes(kind);
  const result = draft.result;

  return (
    <div className="wiz-editor">
      <p className="wiz-file">
        <strong>{upload.filename}</strong> · {FORMAT_LABELS[upload.format] ?? upload.format} · строк: {fmt(upload.row_count)} · колонок: {upload.columns.length}
        {upload.format === "micromine" && upload.options.encoding && <> · кодировка: {upload.options.encoding}</>}
      </p>
      {upload.format !== "micromine" && <ReadOptionsPanel upload={upload} disabled={busy} onChange={onReread} />}
      {upload.detected_kind && upload.detected_kind !== kind && !(table && TABLE_KINDS.includes(upload.detected_kind)) && (
        <div className="alert warn small">По названиям колонок файл похож на «{schemas[upload.detected_kind]?.label}». Проверьте, тот ли это файл.</div>
      )}
      {upload.notes.length > 0 && <ul className="notes">{upload.notes.map((n) => <li key={n}>{n}</li>)}</ul>}

      {(kinds.length > 1 || table) && (
        <div className="wiz-row">
          {kinds.length > 1 && (
            <label className="field">
              Тип таблицы
              <select value={kind} aria-label="Тип таблицы" onChange={(e) => onChange({ ...makeDraft(upload, e.target.value, draft.tableName), key: draft.key })}>
                {kinds.map((k) => <option key={k} value={k}>{schemas[k]?.label ?? k}</option>)}
              </select>
            </label>
          )}
          {table && (
            <label className="field">
              Название таблицы в наборе
              <input value={draft.tableName} maxLength={255} aria-label="Название таблицы в наборе" onChange={(e) => set({ tableName: e.target.value })} />
              <span className="muted small">
                {kind === "assay"
                  ? `«${MAIN_ASSAYS}» — основное опробование набора; другое название — отдельная таблица (композиты, другая лаборатория).`
                  : "Таблица с таким названием в наборе будет заменена."}
              </span>
            </label>
          )}
        </div>
      )}

      <h3>Назначение колонок</h3>
      <table className="mapping">
        <tbody>
          {fields.map((f) => (
            <tr key={f.key}>
              <td>{f.label}{f.required && <span className="req"> *</span>}</td>
              <td>
                <select value={mapping[f.key] ?? ""} aria-label={f.label} onChange={(e) => set({ mapping: { ...mapping, [f.key]: e.target.value } })}>
                  <option value="">— не назначено —</option>
                  {upload.columns.map((c) => (
                    <option key={c.name} value={c.name} disabled={mappedColumns.has(c.name) && mapping[f.key] !== c.name}>
                      {c.name} ({c.numeric ? "число" : "текст"})
                    </option>
                  ))}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {kind === "assay" && (
        <>
          <h3>Компоненты (содержания) и единицы</h3>
          {numericFree.length === 0 && <p className="muted">Нет свободных числовых колонок.</p>}
          <table className="mapping">
            <tbody>
              {numericFree.map((c) => {
                const state = components[c.name] ?? { checked: false, unit: "" };
                return (
                  <tr key={c.name}>
                    <td>
                      <label>
                        <input type="checkbox" checked={state.checked}
                          onChange={(e) => set({ components: { ...components, [c.name]: { ...state, checked: e.target.checked } } })} />{" "}
                        {c.name}
                      </label>
                    </td>
                    <td>
                      <input className="unit" placeholder="единица (г/т, %, ppm)" value={state.unit}
                        onChange={(e) => set({ components: { ...components, [c.name]: { ...state, unit: e.target.value } } })} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}

      {freeColumns.some((c) => !(kind === "assay" && components[c.name]?.checked)) && (
        <>
          <h3>{kind === "interval" || kind === "event" ? "Колонки таблицы" : "Прочие колонки как атрибуты"}</h3>
          <div className="chips">
            {freeColumns.filter((c) => !(kind === "assay" && components[c.name]?.checked)).map((c) => (
              <label key={c.name} className="chip">
                <input type="checkbox" checked={attributes.has(c.name)}
                  onChange={(e) => {
                    const next = new Set(attributes);
                    if (e.target.checked) next.add(c.name);
                    else next.delete(c.name);
                    set({ attributes: [...next] });
                  }} />{" "}
                {c.name}
              </label>
            ))}
          </div>
        </>
      )}

      {draft.error && <div className="alert error">{draft.error}</div>}
      {(draft.force || result?.fixable) && (
        <label className="check import-force" title="Нечисловое значение останется пустым, его текст — в атрибуте «<колонка> (исходное)»; строка без номера скважины получит «БЕЗ НОМЕРА (строка N)»; повтор устья — «<номер> (повтор k)». Исправить: таблицы скважин и аудит">
          <input type="checkbox" checked={draft.force} disabled={busy} onChange={(e) => {
            // Checked again at once with the new choice: the user sees what will be loaded.
            const next: Draft = { ...draft, force: e.target.checked, result: null, committed: null, error: null };
            onChange(next);
            void validateMapping(buildRequest(next)).then((r) => onChange({ ...next, result: r })).catch((err) => onChange({ ...next, error: (err as Error).message }));
          }} />{" "}
          Загрузить с ошибками в данных — исправлю в BedrockForge (таблицы и аудит)
        </label>
      )}
      {result && (
        <>
          <div className={`alert ${result.ok ? "ok" : "error"}`}>
            {result.ok
              ? `Проверка пройдена: строк ${fmt(result.row_count)}.${draft.force ? " Ошибки в данных будут загружены как есть." : ""}`
              : result.fixable ? "Есть ошибки в данных. Их можно загрузить как есть и исправить в BedrockForge — или исправить файл."
              : "Есть ошибки: исправьте назначение колонок или файл."}
          </div>
          {result.issues.length > 0 && (
            <ul className="issues">
              {result.issues.map((i, n) => (
                <li key={n} className={i.severity}>
                  <strong>{i.severity === "error" ? "Ошибка" : "Предупреждение"}:</strong> {i.message}{i.count !== null && <> ({i.count})</>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <details className="wiz-preview" open>
        <summary>Исходные данные (первые строки)</summary>
        <PreviewTable columns={upload.columns.map((c) => c.name)} rows={upload.preview} />
      </details>
    </div>
  );
}

const STEPS = ["Устья", "Инклинометрия", "Интервалы и события", "Импорт"];

/**
 * Import of drillholes step by step: collars, then the survey (or none), then any number of interval
 * and event tables; the last step shows what goes into the set and writes it.
 */
export function DrillholeImport({ projectId, sets, initialSet, onImported, onClose }: {
  projectId: number;
  sets: Dataset[];
  /** null: a new set */
  initialSet: number | null;
  onImported: (setId: number) => void;
  onClose: () => void;
}) {
  const [schemas, setSchemas] = useState<Schemas | null>(null);
  const [setId, setSetId] = useState<number | null>(initialSet);
  const [newName, setNewName] = useState(() => `Скважины ${sets.length + 1}`);
  const [step, setStep] = useState(0);
  const [collar, setCollar] = useState<Draft | null>(null);
  const [survey, setSurvey] = useState<Draft | null>(null);
  const [noSurvey, setNoSurvey] = useState(false);
  const [tables, setTables] = useState<Draft[]>([]);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [finished, setFinished] = useState(false);

  useEffect(() => {
    getSchemas().then(setSchemas).catch((e: Error) => setError(e.message));
  }, []);

  const existing = setId === null ? undefined : sets.find((d) => d.id === setId);
  const inSet = existing?.counts ?? { collar: 0, survey: 0, assay: 0 };
  // An existing set with collars: the collar file is optional.
  const [keepCollar, setKeepCollar] = useState(() => (sets.find((d) => d.id === initialSet)?.counts?.collar ?? 0) > 0);
  useEffect(() => setKeepCollar(inSet.collar > 0), [setId]); // eslint-disable-line react-hooks/exhaustive-deps

  const update = (key: string, next: Draft) => {
    setCollar((d) => (d?.key === key ? next : d));
    setSurvey((d) => (d?.key === key ? next : d));
    setTables((list) => list.map((d) => (d.key === key ? next : d)));
  };

  const newTable = (u: UploadResponse, others: Draft[]): Draft => {
    const kind = tableKind(u);
    const used = others.map((d) => d.tableName);
    const main = kind === "assay" && inSet.assay === 0 && !used.some((n) => n.trim().toLowerCase() === MAIN_ASSAYS.toLowerCase());
    return makeDraft(u, kind, main ? MAIN_ASSAYS : uniqueName(u.filename.replace(/\.[^.]+$/, ""), used));
  };

  /** Uploads the files and puts them on their steps; the current step takes the first file if no file fits it by its columns. */
  async function addFiles(files: File[]) {
    if (!files.length) return;
    setBusy(true);
    setError(null);
    setNote(null);
    const uploads: UploadResponse[] = [];
    const failed: string[] = [];
    for (const f of files) {
      try {
        uploads.push(await uploadFile(f));
      } catch (e) {
        failed.push(`${f.name}: ${(e as Error).message}`);
      }
    }
    let rest = uploads;
    let nextCollar: UploadResponse | null = null;
    let nextSurvey: UploadResponse | null = null;
    if (step === 0) {
      const d = distribute(uploads, { collar: false, survey: !!survey });
      nextCollar = d.collar ?? uploads.find((u) => u !== d.survey) ?? null;
      nextSurvey = d.survey;
      rest = uploads.filter((u) => u !== nextCollar && u !== nextSurvey);
    } else if (step === 1) {
      nextSurvey = uploads.find((u) => u.detected_kind === "survey") ?? uploads[0] ?? null;
      rest = uploads.filter((u) => u !== nextSurvey);
    }
    if (nextCollar) { setCollar(makeDraft(nextCollar, "collar")); setKeepCollar(false); }
    if (nextSurvey) { setSurvey(makeDraft(nextSurvey, "survey")); setNoSurvey(false); }
    if (rest.length) {
      const added: Draft[] = [];
      for (const u of rest) added.push(newTable(u, [...tables, ...added]));
      setTables([...tables, ...added]);
      setActiveKey(added[0].key);
    }
    if (uploads.length > 1 || (rest.length && step < 2)) {
      const parts = [nextCollar && `устья — ${nextCollar.filename}`, nextSurvey && `инклинометрия — ${nextSurvey.filename}`, rest.length && `таблиц на шаге 3 — ${rest.length}`].filter(Boolean);
      setNote(`Файлы разложены по шагам: ${parts.join("; ")}.`);
    }
    if (failed.length) setError(failed.join("\n"));
    setBusy(false);
  }

  async function reread(d: Draft, options: ReadOptions) {
    setBusy(true);
    setError(null);
    try {
      const data = await rereadFile(d.upload.file_id, options);
      update(d.key, { ...makeDraft(data, d.kind, d.tableName), key: d.key });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** Checks the drafts on the server; true when every one is fine. */
  async function validate(drafts: Draft[]): Promise<boolean> {
    if (!schemas) return false;
    setBusy(true);
    setError(null);
    let ok = true;
    for (const d of drafts) {
      const missing = missingFields(d, schemas[d.kind]?.fields ?? []);
      if (missing.length) {
        update(d.key, { ...d, result: null, error: `Назначьте колонки: ${missing.join(", ")}` });
        ok = false;
        continue;
      }
      try {
        const result = d.result ?? (await validateMapping(buildRequest(d)));
        update(d.key, { ...d, result, error: null });
        if (!result.ok) ok = false;
      } catch (e) {
        update(d.key, { ...d, error: (e as Error).message });
        ok = false;
      }
    }
    setBusy(false);
    return ok;
  }

  async function next() {
    setNote(null);
    if (step === 0) {
      if (!collar && !keepCollar) return setError("Загрузите файл устьев: без него скважины не построить.");
      if (collar && !(await validate([collar]))) return;
    } else if (step === 1) {
      if (!survey && !noSurvey) return setError("Загрузите файл инклинометрии или отметьте, что её нет.");
      if (survey && !noSurvey && !(await validate([survey]))) return;
    } else if (step === 2) {
      const names = tables.map((d) => d.tableName.trim().toLowerCase());
      const twice = tables.find((_d, i) => names.indexOf(names[i]) !== i);
      if (twice) { setActiveKey(twice.key); return setError(`Два файла идут в одну таблицу «${twice.tableName}»: дайте им разные названия.`); }
      const empty = tables.find((d) => !d.tableName.trim());
      if (empty) { setActiveKey(empty.key); return setError("Укажите название таблицы."); }
      if (!(await validate(tables))) {
        return setError("В таблицах есть ошибки: файлы с ошибкой отмечены в списке.");
      }
    }
    setError(null);
    setStep(step + 1);
  }

  const queue = [collar, noSurvey ? null : survey, ...tables].filter((d): d is Draft => !!d);

  async function runImport() {
    setBusy(true);
    setError(null);
    let target = setId;
    let stopped = false;
    for (const d of queue) {
      if (d.committed) continue;
      try {
        const done = await commitImport({
          ...buildRequest(d),
          ...(target !== null ? { set_id: target } : { project_id: projectId, set_name: newName.trim() || "Скважины" }),
          filename: d.upload.filename,
          ...(TABLE_KINDS.includes(d.kind) ? { table_name: d.tableName.trim() || undefined } : {}),
        });
        target = done.set_id;
        setSetId(target);
        update(d.key, { ...d, committed: done, error: null });
      } catch (e) {
        update(d.key, { ...d, error: (e as Error).message });
        stopped = true;
        if (d.kind === "collar") break; // nothing to attach the rest to
      }
    }
    if (target !== null) onImported(target);
    setFinished(!stopped);
    if (stopped) setError("Часть файлов не импортирована. Исправьте их на шаге и нажмите «Импортировать» ещё раз — уже записанные не повторяются.");
    setBusy(false);
  }

  const active = tables.find((d) => d.key === activeKey) ?? tables[0] ?? null;
  const status = (d: Draft) => (d.committed ? "ok" : d.error || (d.result && !d.result.ok) ? "error" : d.result ? "checked" : "");
  const warnings = (d: Draft) => d.result?.issues.filter((i) => i.severity !== "error").length ?? 0;
  const locked = busy || finished;

  return (
    <>
      <div className="modal-target">
        <label>
          Набор{" "}
          <select value={setId ?? "new"} disabled={locked || queue.some((d) => d.committed)} aria-label="Набор скважин"
            onChange={(e) => setSetId(e.target.value === "new" ? null : Number(e.target.value))}>
            <option value="new">+ Новый набор</option>
            {sets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </label>
        {setId === null && (
          <label>
            Название{" "}
            <input value={newName} disabled={locked} onChange={(e) => setNewName(e.target.value)} maxLength={255} aria-label="Название нового набора" />
          </label>
        )}
        {existing && <span className="muted small">в наборе: скважин {fmt(inSet.collar)}, замеров {fmt(inSet.survey)}, проб {fmt(inSet.assay)}</span>}
      </div>

      <ol className="wiz-steps" aria-label="Шаги импорта">
        {STEPS.map((title, i) => (
          <li key={title} className={i === step ? "current" : i < step ? "done" : ""}>
            <button disabled={locked || i >= step} onClick={() => { setError(null); setStep(i); }} aria-current={i === step ? "step" : undefined}>
              <span className="wiz-num">{i < step ? "✓" : i + 1}</span> {title}
            </button>
          </li>
        ))}
      </ol>

      <div className="modal-body wiz-body">
        {!schemas ? <p className="muted">Загрузка…</p> : (
          <>
            {step === 0 && (
              <>
                <p className="muted">
                  Таблица устьев: номер скважины, координаты X, Y, Z и глубина. Можно выбрать сразу все файлы набора — устья, инклинометрию
                  и таблицы интервалов: мастер разложит их по шагам по названиям колонок.
                </p>
                {inSet.collar > 0 && (
                  <label className="wiz-check">
                    <input type="checkbox" checked={keepCollar} disabled={busy}
                      onChange={(e) => { setKeepCollar(e.target.checked); if (e.target.checked) setCollar(null); }} />{" "}
                    Оставить устья набора как есть ({fmt(inSet.collar)} скв.) — добавляю только другие таблицы
                  </label>
                )}
                {!keepCollar && <FileDrop multiple busy={busy} onFiles={(f) => void addFiles(f)}
                  text={collar ? "Заменить файл устьев: выберите или перетащите другой" : "Выберите файл устьев или перетащите сюда: .DAT, .csv, .txt, .xlsx"} />}
                {collar && !keepCollar && <DraftEditor draft={collar} schemas={schemas} kinds={["collar"]} busy={busy} onChange={(d) => update(d.key, d)} onReread={(o) => void reread(collar, o)} />}
              </>
            )}

            {step === 1 && (
              <>
                <p className="muted">Таблица инклинометрии: номер скважины, глубина замера, азимут и наклон (отрицательный — вниз).</p>
                <label className="wiz-check">
                  <input type="checkbox" checked={noSurvey} disabled={busy} onChange={(e) => setNoSurvey(e.target.checked)} />{" "}
                  Инклинометрии нет
                </label>
                {noSurvey ? (
                  <div className="alert small">
                    {inSet.survey > 0
                      ? `Замеры набора (${fmt(inSet.survey)}) остаются как есть.`
                      : "Скважины будут построены по азимуту и наклону из таблицы устьев, а где их нет — вертикально вниз. Инклинометрию можно добавить позже."}
                  </div>
                ) : (
                  <>
                    <FileDrop multiple busy={busy} onFiles={(f) => void addFiles(f)}
                      text={survey ? "Заменить файл инклинометрии: выберите или перетащите другой" : "Выберите файл инклинометрии или перетащите сюда"} />
                    {survey && <DraftEditor draft={survey} schemas={schemas} kinds={["survey"]} busy={busy} onChange={(d) => update(d.key, d)} onReread={(o) => void reread(survey, o)} />}
                  </>
                )}
              </>
            )}

            {step === 2 && (
              <>
                <p className="muted">
                  Таблицы по стволу: опробование, литология, изменения, RQD (интервалы «от–до») и события (одна глубина). Файлов может быть
                  несколько — каждый станет своей таблицей набора. Шаг можно пропустить и добавить таблицы позже.
                </p>
                <FileDrop multiple busy={busy} onFiles={(f) => void addFiles(f)} text="Добавить файлы: выберите несколько или перетащите сюда" />
                {tables.length > 0 && active && (
                  <div className="wiz-split">
                    <ul className="wiz-files" aria-label="Файлы таблиц">
                      {tables.map((d) => (
                        <li key={d.key} className={`${d.key === active.key ? "current" : ""} ${status(d)}`}>
                          <button className="wiz-file-pick" onClick={() => setActiveKey(d.key)} title={d.upload.filename}>
                            <span className="wiz-file-name">{d.tableName || d.upload.filename}</span>
                            <span className="muted small">{KIND_SHORT[d.kind]} · {fmt(d.upload.row_count)} строк{status(d) === "error" ? " · ошибка" : ""}</span>
                          </button>
                          <button className="icon" disabled={busy} title="Убрать файл из импорта" aria-label={`Убрать ${d.upload.filename}`}
                            onClick={() => { setTables(tables.filter((t) => t.key !== d.key)); if (activeKey === d.key) setActiveKey(null); }}>
                            <Icon name="close" size={13} />
                          </button>
                        </li>
                      ))}
                    </ul>
                    <DraftEditor key={active.key} draft={active} schemas={schemas} kinds={TABLE_KINDS} busy={busy} onChange={(d) => update(d.key, d)} onReread={(o) => void reread(active, o)} />
                  </div>
                )}
              </>
            )}

            {step === 3 && (
              <>
                <p className="muted">
                  {finished ? "Импорт завершён." : `Будет записано в набор «${existing?.name ?? (newName.trim() || "Скважины")}». Таблицы с теми же названиями заменяются, остальные данные набора не меняются.`}
                </p>
                {queue.length === 0 ? <div className="alert warn">Нет файлов для импорта: вернитесь и добавьте таблицы.</div> : (
                  <table className="wizard-table">
                    <thead><tr><th>Таблица</th><th>Файл</th><th className="num">Строк</th><th>Состояние</th></tr></thead>
                    <tbody>
                      {queue.map((d) => (
                        <tr key={d.key}>
                          <td>{TABLE_KINDS.includes(d.kind) ? `${d.tableName} (${KIND_SHORT[d.kind].toLowerCase()})` : KIND_SHORT[d.kind]}</td>
                          <td className="name" title={d.upload.filename}>{d.upload.filename}</td>
                          <td className="num">{fmt(d.committed?.inserted ?? d.result?.row_count ?? d.upload.row_count)}</td>
                          <td>
                            {d.committed ? <span className="wiz-ok">импортировано{d.committed.replaced > 0 && `, заменено ${fmt(d.committed.replaced)}`}</span>
                              : d.error ? <span className="wiz-err">{d.error}</span>
                              : warnings(d) ? <span className="wiz-warn" title={d.result!.issues.map((i) => i.message).join("\n")}>проверено, предупреждений: {warnings(d)}</span>
                              : "проверено"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {!collar && <span className="muted small">Устья: остаются из набора ({fmt(inSet.collar)} скв.).</span>}
                {(noSurvey || !survey) && <span className="muted small">Инклинометрия: {inSet.survey > 0 ? "остаётся из набора" : "нет — стволы по устьям или вертикально"}.</span>}
              </>
            )}
            {note && <div className="alert ok small">{note}</div>}
            {error && <div className="alert error wiz-pre">{error}</div>}
          </>
        )}
      </div>

      <footer className="modal-foot">
        <span className="muted small">Шаг {step + 1} из {STEPS.length}</span>
        <span className="spacer" />
        {!finished && <button disabled={busy || step === 0} onClick={() => { setError(null); setStep(step - 1); }}>← Назад</button>}
        {step < 3 && <button className="primary" disabled={busy || !schemas} onClick={() => void next()}>{step === 2 && tables.length === 0 ? "Пропустить →" : "Далее →"}</button>}
        {step === 3 && !finished && <button className="primary" disabled={busy || queue.length === 0} onClick={() => void runImport()}>{busy ? "Импорт…" : "Импортировать"}</button>}
        {finished && <button className="primary" onClick={onClose}>Готово</button>}
      </footer>
    </>
  );
}
