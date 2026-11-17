import { useState } from "react";
import { blockers, KIND_TITLES, type FileEntry, type TableKind, type Wizard } from "./wizard";
import css from "./ImportWizard.module.css";

const ENCODINGS = ["", "utf-8", "utf-8-sig", "utf-16", "cp1251", "cp866", "koi8-r", "latin-1"];
const DELIMITERS: [string, string][] = [
  ["", "авто"],
  [";", "точка с запятой"],
  [",", "запятая"],
  ["\t", "табуляция"],
  ["|", "вертикальная черта"],
  ["whitespace", "пробелы"],
];

/** One file of the wizard: how it is read, its kind, column assignment, components, issues, preview (ТЗ 7.7). */
export function FileCard({ wizard, file }: { wizard: Wizard; file: FileEntry }) {
  const [showRaw, setShowRaw] = useState(false);
  const c = file.check;
  const columns = c?.columns.map((x) => x.name) ?? [];
  const used = new Set([...Object.values(file.mapping), ...file.components.map((x) => x.column)]);
  const numberCols = c?.columns.filter((x) => !Object.values(file.mapping).includes(x.name)) ?? [];
  const issues = c?.issues ?? [];
  const blocking = blockers(file);
  const opt = file.options;
  const isText = file.inspect && (file.inspect.format === "csv");
  const isBook = file.inspect && (file.inspect.format === "xlsx" || file.inspect.format === "xls");

  const remap = (key: string, col: string) => {
    const mapping = { ...file.mapping };
    if (col) mapping[key] = col;
    else delete mapping[key];
    void wizard.check(file.id, { mapping });
  };
  const toggleComp = (col: string, on: boolean) => {
    const components = on ? [...file.components, { column: col, unit: /au|ag|pt|pd/i.test(col) ? "g/t" : "%" }] : file.components.filter((x) => x.column !== col);
    void wizard.check(file.id, { components });
  };
  const setUnit = (col: string, unit: string) => {
    void wizard.check(file.id, { components: file.components.map((x) => (x.column === col ? { ...x, unit } : x)) });
  };
  const toggleAttr = (col: string, on: boolean) => {
    void wizard.check(file.id, { attributes: on ? [...file.attributes, col] : file.attributes.filter((x) => x !== col) });
  };

  return (
    <section className={css.card} aria-label={`Файл ${file.name}`} data-testid="import-file">
      <header className={css.cardHead}>
        <b>{file.name}</b>
        {file.inspect && <span className={css.note}>{file.inspect.summary}</span>}
        {file.busy && <span className={css.note}>Чтение…</span>}
        <button type="button" className={css.linkBtn} onClick={() => wizard.remove(file.id)}>
          Убрать
        </button>
      </header>
      {file.error && <div className={css.error}>{file.error}</div>}
      {file.inspect && (
        <div className={css.options}>
          {isText && (
            <>
              <label>
                Кодировка
                <select value={opt.encoding ?? ""} onChange={(e) => void wizard.reread(file.id, { ...opt, encoding: e.target.value || null })}>
                  {ENCODINGS.map((e) => (
                    <option key={e} value={e}>
                      {e || `авто (${file.inspect?.info.encoding ?? "—"})`}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Разделитель
                <select value={opt.delimiter ?? ""} onChange={(e) => void wizard.reread(file.id, { ...opt, delimiter: e.target.value || null })}>
                  {DELIMITERS.map(([v, t]) => (
                    <option key={v} value={v}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          {isBook && file.inspect.info.sheets.length > 1 && (
            <label>
              Лист
              <select value={opt.sheet ?? file.inspect.info.sheet ?? ""} onChange={(e) => void wizard.reread(file.id, { ...opt, sheet: e.target.value })}>
                {file.inspect.info.sheets.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
          )}
          {file.inspect.format !== "dat" && (
            <label>
              Строка заголовка
              <input type="number" min={0} max={1000} value={opt.header_row} onChange={(e) => void wizard.reread(file.id, { ...opt, header_row: Number(e.target.value) })} aria-label="Строка заголовка" />
            </label>
          )}
          <button type="button" className={css.linkBtn} onClick={() => setShowRaw(!showRaw)}>
            {showRaw ? "Скрыть строки файла" : "Строки файла"}
          </button>
        </div>
      )}
      {showRaw && file.inspect && (
        <table className={css.raw}>
          <tbody>
            {file.inspect.rows.slice(0, 12).map((r, i) => (
              <tr key={i}>
                <th>{file.inspect!.numbers[i] || ""}</th>
                {r.map((v, j) => (
                  <td key={j}>{v}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {c && (
        <>
          <label className={css.kind}>
            Тип таблицы
            <select value={file.kind ?? ""} onChange={(e) => void wizard.check(file.id, { kind: (e.target.value || null) as TableKind | null, mapping: null })} aria-label="Тип таблицы">
              <option value="">— выберите —</option>
              {(Object.keys(KIND_TITLES) as TableKind[]).map((k) => (
                <option key={k} value={k}>
                  {KIND_TITLES[k]}
                </option>
              ))}
            </select>
            <span className={css.note}>строк: {c.rows.toLocaleString("ru-RU")}</span>
          </label>
          {file.kind && file.kind !== "collar" && file.kind !== "survey" && (
            <label className={css.kind}>
              Название таблицы
              <input value={file.tableName} onChange={(e) => wizard.setTableName(file.id, e.target.value)} onBlur={() => void wizard.check(file.id)} aria-label="Название таблицы" />
            </label>
          )}
          {c.fields.length > 0 && (
            <table className={css.map}>
              <tbody>
                {c.fields.map((f) => (
                  <tr key={f.key}>
                    <th>
                      {f.label}
                      {f.required && <span className={css.req}> *</span>}
                    </th>
                    <td>
                      <select value={file.mapping[f.key] ?? ""} onChange={(e) => remap(f.key, e.target.value)} aria-label={f.label}>
                        <option value="">—</option>
                        {columns.map((col) => (
                          <option key={col} value={col} disabled={used.has(col) && file.mapping[f.key] !== col}>
                            {col}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {numberCols.length > 0 && file.kind && (
            <div className={css.extra}>
              <div className={css.note}>{file.kind === "assay" ? "Компоненты (галочка) и другие колонки, которые сохранить:" : "Колонки, которые сохранить:"}</div>
              {numberCols.map((col) => {
                const comp = file.components.find((x) => x.column === col.name);
                return (
                  <div key={col.name} className={css.extraRow}>
                    {file.kind === "assay" && (
                      <label title="Компонент (содержание)">
                        <input type="checkbox" checked={!!comp} onChange={(e) => toggleComp(col.name, e.target.checked)} aria-label={`Компонент ${col.name}`} /> компонент
                      </label>
                    )}
                    {comp ? (
                      <input className={css.unit} value={comp.unit} onChange={(e) => setUnit(col.name, e.target.value)} aria-label={`Единица ${col.name}`} />
                    ) : (
                      <label>
                        <input type="checkbox" checked={file.attributes.includes(col.name)} onChange={(e) => toggleAttr(col.name, e.target.checked)} /> атрибут
                      </label>
                    )}
                    <span>{col.name}</span>
                    <span className={css.note}>{col.sample.filter((v) => v !== null).slice(0, 3).join("; ")}</span>
                  </div>
                );
              })}
            </div>
          )}
          {issues.length > 0 && (
            <ul className={css.issues} data-testid="import-issues">
              {issues.map((i, k) => (
                <li key={k} className={i.severity === "error" && blocking.includes(i) ? css.error : css.warn}>
                  {i.message}
                  {i.count ? ` — ${i.count.toLocaleString("ru-RU")}` : ""}
                </li>
              ))}
            </ul>
          )}
          {c.can_force && (
            <label className={css.force}>
              <input type="checkbox" checked={file.force} onChange={(e) => void wizard.check(file.id, { force: e.target.checked })} /> Загрузить с ошибками — исправлю в BedrockForge
            </label>
          )}
          {c.preview.length > 0 && <Preview rows={c.preview} />}
        </>
      )}
    </section>
  );
}

function Preview({ rows }: { rows: Record<string, unknown>[] }) {
  const keys = Object.keys(rows[0] ?? {});
  return (
    <div className={css.previewWrap}>
      <table className={css.preview} aria-label="Предпросмотр">
        <thead>
          <tr>
            {keys.map((k) => (
              <th key={k}>{k}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 10).map((r, i) => (
            <tr key={i}>
              {keys.map((k) => (
                <td key={k}>{r[k] === null || r[k] === undefined ? "" : String(r[k])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
