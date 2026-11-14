import { useEffect, useMemo, useState } from "react";
import {
  commitImport,
  getSchemas,
  rereadFile,
  uploadFile,
  validateMapping,
  type CommitResponse,
  type ReadOptions,
  type Schemas,
  type UploadResponse,
  type ValidateResponse,
} from "./api";
import { PreviewTable } from "./Table";

type ComponentState = Record<string, { checked: boolean; unit: string }>;

const ACCEPT = ".dat,.csv,.txt,.tsv,.prn,.xlsx,.xlsm";
const FORMAT_LABELS: Record<string, string> = { micromine: "Micromine DAT", text: "текст (CSV/TXT)", excel: "Excel" };
const ENCODINGS = ["utf-8", "utf-8-sig", "cp1251", "cp866", "koi8-r", "utf-16", "latin-1"];
const DELIMITERS: [string, string][] = [[";", "точка с запятой ;"], [",", "запятая ,"], ["\t", "табуляция"], ["|", "черта |"], ["whitespace", "пробелы"]];

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

export function ImportPage({ projectId, onImported }: { projectId: number; onImported: () => void }) {
  const [schemas, setSchemas] = useState<Schemas | null>(null);
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [kind, setKind] = useState("collar");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [components, setComponents] = useState<ComponentState>({});
  const [attributes, setAttributes] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<ValidateResponse | null>(null);
  const [committed, setCommitted] = useState<CommitResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSchemas().then(setSchemas).catch((e: Error) => setError(e.message));
  }, []);

  function applySuggestion(data: UploadResponse, nextKind: string) {
    const suggestion = data.suggestions[nextKind];
    setKind(nextKind);
    setMapping(suggestion?.mapping ?? {});
    const state: ComponentState = {};
    for (const c of suggestion?.components ?? []) state[c.column] = { checked: true, unit: c.unit };
    setComponents(state);
    setAttributes(new Set());
    setResult(null);
    setCommitted(null);
  }

  async function onFile(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const data = await uploadFile(file);
      setUpload(data);
      applySuggestion(data, data.detected_kind ?? "collar");
    } catch (e) {
      setUpload(null);
      setResult(null);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onReadOptions(options: ReadOptions) {
    if (!upload) return;
    setBusy(true);
    setError(null);
    try {
      const data = await rereadFile(upload.file_id, options);
      setUpload(data);
      applySuggestion(data, data.detected_kind ?? kind);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const mappedColumns = useMemo(() => new Set(Object.values(mapping).filter(Boolean)), [mapping]);
  const freeColumns = (upload?.columns ?? []).filter((c) => !mappedColumns.has(c.name));
  const numericFree = freeColumns.filter((c) => c.numeric);
  const fields = schemas?.[kind]?.fields ?? [];

  function buildRequest() {
    const comps = Object.entries(components)
      .filter(([column, s]) => s.checked && !mappedColumns.has(column))
      .map(([column, s]) => ({ column, unit: s.unit }));
    const attrs = [...attributes].filter((a) => !mappedColumns.has(a) && !comps.some((c) => c.column === a));
    return {
      file_id: upload!.file_id,
      kind,
      mapping: Object.fromEntries(Object.entries(mapping).filter(([, v]) => v)),
      components: kind === "assay" ? comps : [],
      attributes: attrs,
    };
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const onValidate = () =>
    run(async () => {
      setCommitted(null);
      setResult(await validateMapping(buildRequest()));
    });

  const onCommit = () =>
    run(async () => {
      const done = await commitImport({ ...buildRequest(), project_id: projectId, filename: upload!.filename });
      setCommitted(done);
      onImported();
    });

  return (
    <main className="page">
      <h1>Импорт данных</h1>
      <p className="muted">
        Загрузите файл Micromine (.DAT), текстовый файл (.csv, .txt) или книгу Excel (.xlsx), проверьте назначение колонок и запустите проверку.
      </p>

      <label
        className={dragOver ? "drop over" : "drop"}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (!busy) onFile(e.dataTransfer.files?.[0]);
        }}
      >
        <input type="file" accept={ACCEPT} onChange={(e) => onFile(e.target.files?.[0])} disabled={busy} />
        <span>{busy ? "Обработка…" : "Выберите файл или перетащите его сюда: .DAT, .csv, .txt, .xlsx"}</span>
      </label>

      {error && <div className="alert error">{error}</div>}

      {upload && schemas && (
        <>
          <section>
            <h2>Файл</h2>
            <p>
              <strong>{upload.filename}</strong> · формат: {FORMAT_LABELS[upload.format] ?? upload.format} · строк:{" "}
              {upload.row_count.toLocaleString("ru-RU")} · колонок: {upload.columns.length}
              {upload.format === "micromine" && upload.options.encoding && <> · кодировка: {upload.options.encoding}</>}
              {upload.detected_kind && <> · определён как: <strong>{schemas[upload.detected_kind].label}</strong></>}
            </p>
            {upload.format !== "micromine" && (
              <ReadOptionsPanel upload={upload} disabled={busy} onChange={onReadOptions} />
            )}
            {!upload.detected_kind && (
              <div className="alert warn">
                Тип таблицы не распознан по названиям колонок.
                {upload.format !== "micromine" && " Проверьте строку заголовка и разделитель выше,"} либо выберите тип и назначьте колонки вручную.
              </div>
            )}
            {upload.notes.length > 0 && (
              <ul className="notes">
                {upload.notes.map((n) => <li key={n}>{n}</li>)}
              </ul>
            )}
            <label className="field">
              Тип таблицы
              <select value={kind} onChange={(e) => applySuggestion(upload, e.target.value)}>
                {Object.entries(schemas).map(([k, s]) => (
                  <option key={k} value={k}>{s.label}</option>
                ))}
              </select>
            </label>
          </section>

          <section>
            <h2>Назначение колонок</h2>
            <table className="mapping">
              <tbody>
                {fields.map((f) => (
                  <tr key={f.key}>
                    <td>{f.label}{f.required && <span className="req"> *</span>}</td>
                    <td>
                      <select
                        value={mapping[f.key] ?? ""}
                        onChange={(e) => {
                          setMapping({ ...mapping, [f.key]: e.target.value });
                          setResult(null);
                        }}
                      >
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
          </section>

          {kind === "assay" && (
            <section>
              <h2>Компоненты (содержания) и единицы</h2>
              {numericFree.length === 0 && <p className="muted">Нет свободных числовых колонок.</p>}
              <table className="mapping">
                <tbody>
                  {numericFree.map((c) => {
                    const state = components[c.name] ?? { checked: false, unit: "" };
                    return (
                      <tr key={c.name}>
                        <td>
                          <label>
                            <input
                              type="checkbox"
                              checked={state.checked}
                              onChange={(e) => {
                                setComponents({ ...components, [c.name]: { ...state, checked: e.target.checked } });
                                setResult(null);
                              }}
                            />{" "}
                            {c.name}
                          </label>
                        </td>
                        <td>
                          <input
                            className="unit"
                            placeholder="единица (г/т, %, ppm)"
                            value={state.unit}
                            onChange={(e) => {
                              setComponents({ ...components, [c.name]: { ...state, unit: e.target.value } });
                              setResult(null);
                            }}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          )}

          <section>
            <h2>Прочие колонки как атрибуты</h2>
            <p className="muted">Отметьте колонки, которые нужно сохранить как есть (например, H, TYPE).</p>
            <div className="chips">
              {freeColumns
                .filter((c) => !(kind === "assay" && components[c.name]?.checked))
                .map((c) => (
                  <label key={c.name} className="chip">
                    <input
                      type="checkbox"
                      checked={attributes.has(c.name)}
                      onChange={(e) => {
                        const next = new Set(attributes);
                        if (e.target.checked) next.add(c.name);
                        else next.delete(c.name);
                        setAttributes(next);
                        setResult(null);
                      }}
                    />{" "}
                    {c.name}
                  </label>
                ))}
            </div>
          </section>

          <section>
            <button className="primary" onClick={onValidate} disabled={busy}>Проверить</button>
          </section>

          {result && (
            <section>
              <h2>Результат проверки</h2>
              <div className={`alert ${result.ok ? "ok" : "error"}`}>
                {result.ok ? "Назначение корректно, можно импортировать." : "Есть ошибки: исправьте назначение колонок."}
              </div>
              {result.issues.length > 0 && (
                <ul className="issues">
                  {result.issues.map((i, n) => (
                    <li key={n} className={i.severity}>
                      <strong>{i.severity === "error" ? "Ошибка" : "Предупреждение"}:</strong> {i.message}
                      {i.count !== null && <> ({i.count})</>}
                    </li>
                  ))}
                </ul>
              )}
              {result.ok && (
                <p>
                  <button className="primary" onClick={onCommit} disabled={busy}>Импортировать в проект</button>{" "}
                  <span className="muted">Существующие данные этого типа в проекте будут заменены.</span>
                </p>
              )}
              {committed && (
                <div className="alert ok">
                  Импортировано строк: {committed.inserted.toLocaleString("ru-RU")}
                  {committed.replaced > 0 && <>, заменено прежних: {committed.replaced.toLocaleString("ru-RU")}</>}.
                  Данные доступны на вкладке «Данные».
                </div>
              )}
              {result.columns.length > 0 && <PreviewTable columns={result.columns} rows={result.preview} />}
            </section>
          )}

          <section>
            <h2>Исходные данные (первые строки)</h2>
            <PreviewTable columns={upload.columns.map((c) => c.name)} rows={upload.preview} />
          </section>
        </>
      )}
    </main>
  );
}
