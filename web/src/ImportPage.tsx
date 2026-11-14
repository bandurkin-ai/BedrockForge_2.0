import { useEffect, useMemo, useState } from "react";
import {
  getSchemas,
  uploadFile,
  validateMapping,
  type Schemas,
  type UploadResponse,
  type ValidateResponse,
} from "./api";

type ComponentState = Record<string, { checked: boolean; unit: string }>;

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(8)));
  return String(value);
}

function PreviewTable({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>{columns.map((c) => <td key={c}>{formatCell(row[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ImportPage() {
  const [schemas, setSchemas] = useState<Schemas | null>(null);
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [kind, setKind] = useState("collar");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [components, setComponents] = useState<ComponentState>({});
  const [attributes, setAttributes] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<ValidateResponse | null>(null);
  const [busy, setBusy] = useState(false);
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

  const mappedColumns = useMemo(() => new Set(Object.values(mapping).filter(Boolean)), [mapping]);
  const freeColumns = (upload?.columns ?? []).filter((c) => !mappedColumns.has(c.name));
  const numericFree = freeColumns.filter((c) => c.numeric);
  const fields = schemas?.[kind]?.fields ?? [];

  async function onValidate() {
    if (!upload) return;
    setBusy(true);
    setError(null);
    try {
      const comps = Object.entries(components)
        .filter(([column, s]) => s.checked && !mappedColumns.has(column))
        .map(([column, s]) => ({ column, unit: s.unit }));
      const attrs = [...attributes].filter((a) => !mappedColumns.has(a) && !comps.some((c) => c.column === a));
      const cleanMapping = Object.fromEntries(Object.entries(mapping).filter(([, v]) => v));
      setResult(
        await validateMapping({
          file_id: upload.file_id,
          kind,
          mapping: cleanMapping,
          components: kind === "assay" ? comps : [],
          attributes: attrs,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="page">
      <h1>Импорт данных</h1>
      <p className="muted">Загрузите файл Micromine (.DAT), проверьте назначение колонок и запустите проверку.</p>

      <label className="drop">
        <input type="file" accept=".dat,.DAT" onChange={(e) => onFile(e.target.files?.[0])} disabled={busy} />
        <span>{busy ? "Обработка…" : "Выберите файл .DAT"}</span>
      </label>

      {error && <div className="alert error">{error}</div>}

      {upload && schemas && (
        <>
          <section>
            <h2>Файл</h2>
            <p>
              <strong>{upload.filename}</strong> · формат: {upload.format} · строк: {upload.row_count.toLocaleString("ru-RU")} · колонок:{" "}
              {upload.columns.length}
              {upload.detected_kind && <> · определён как: <strong>{schemas[upload.detected_kind].label}</strong></>}
            </p>
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
