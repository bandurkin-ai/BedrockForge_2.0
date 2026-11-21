/**
 * The form of loading control samples (ADR 0020 §2, §11): the source — a file (the shell's own file dialog; CSV,
 * TXT, DAT, XLSX, XLS; a sheet of a book) or a table of a set; the preview of 50 rows; the mapping of the columns
 * chosen automatically and edited by hand (the required SAMPLE_ID and TYPE, elements with their units, the column
 * of DL, other fields for the filter); the name and «заменить данные». «Загрузить» runs `qaqc.import`; a refusal of
 * the core is shown at its field. Only the newest reading of the source is applied.
 */
import { useEffect, useRef, useState } from "react";
import { bridge } from "../../../bridge";
import { useApp } from "../../../shell/AppContext";
import { errorText } from "../../../shell/props";
import { useSnap } from "./useSnap";
import css from "../../../shell/PropsParts.module.css";
import { loadSets, loadTables, type SetRow, type TableRow } from "../../composites/api";
import { inspectSource, type FieldKey, type InspectAnswer, type InspectSource, type Mapping, type QaqcData } from "../setupApi";
import { closeForm, dataStore, type FormRequest } from "./dataStore";
import { cellOf, defaultDataName, FIELDS, mappingProblems, patchElement, problemAt, setField, startMapping, toggleElement } from "./mapping";
import own from "./Setup.module.css";

const UNITS = ["г/т", "ppm", "ppb", "%"];
const PREVIEW_ROWS = 50;

/** The body of `qaqc.import` from the form: the source, the name, the mapping (with how the file is read). */
export function importArgs(source: InspectSource, name: string, mapping: Mapping, replaceId: number | null): Record<string, unknown> {
  const read = source.kind === "file" && source.sheet ? { read: { sheet: source.sheet } } : {};
  const where = source.kind === "file" ? { path: source.path } : { set_id: source.set_id, table: source.table };
  return { ...where, name: name.trim(), mapping: JSON.stringify({ ...mapping, ...read }), ...(replaceId !== null ? { replace_id: replaceId } : {}) };
}

function useInspect(source: InspectSource | null) {
  const [answer, setAnswer] = useState<InspectAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  const key = source ? JSON.stringify(source) : "";
  useEffect(() => {
    const mine = ++seq.current;
    setAnswer(null);
    setError(null);
    if (!source) return;
    setBusy(true);
    inspectSource(source).then(
      (a) => mine === seq.current && (setAnswer(a), setBusy(false)),
      (e: unknown) => mine === seq.current && (setError(errorText(e)), setBusy(false)),
    );
  }, [key]);
  return { answer, error, busy };
}

function TablePick({ setId, table, onChange }: { setId: number | null; table: string | null; onChange: (setId: number | null, table: string | null, label: string) => void }) {
  const [sets, setSets] = useState<SetRow[]>([]);
  const [tables, setTables] = useState<TableRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => void loadSets().then(setSets, (e: unknown) => setError(errorText(e))), []);
  useEffect(() => {
    setTables([]);
    if (setId !== null) void loadTables(setId).then(setTables, (e: unknown) => setError(errorText(e)));
  }, [setId]);
  const setName = sets.find((s) => s.id === setId)?.name ?? "";
  return (
    <div className={css.pair}>
      <label className={css.field}>
        <span>Набор</span>
        <select className={css.input} value={setId ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null, null, "")}>
          <option value="">— выберите —</option>
          {sets.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      <label className={css.field}>
        <span>Таблица проб</span>
        <select className={css.input} value={table ?? ""} disabled={setId === null} onChange={(e) => onChange(setId, e.target.value || null, `${setName} · ${tables.find((t) => t.table === e.target.value)?.name ?? ""}`)}>
          <option value="">— выберите —</option>
          {tables.map((t) => (
            <option key={t.table} value={t.table}>
              {t.name} ({t.rows.toLocaleString("ru-RU")})
            </option>
          ))}
        </select>
      </label>
      {error && <p className={css.error}>{error}</p>}
    </div>
  );
}

export function ImportForm({ request, list }: { request: FormRequest; list: readonly QaqcData[] }) {
  const app = useApp();
  const refused = useSnap(dataStore, (s) => s.refused);
  const [path, setPath] = useState<string | null>(request.path ?? null);
  const [sheet, setSheet] = useState<string | null>(null);
  const [setId, setSetId] = useState<number | null>(request.setId ?? null);
  const [table, setTable] = useState<string | null>(request.table ?? null);
  const [name, setName] = useState("");
  const [mapping, setMapping] = useState<Mapping>({ fields: {}, elements: [], extra: [] });
  const [replaceId, setReplaceId] = useState<number | null>(request.replaceId ?? null);
  const [touched, setTouched] = useState(false);
  const source: InspectSource | null = request.mode === "file" ? (path ? { kind: "file", path, ...(sheet ? { sheet } : {}) } : null) : setId !== null && table ? { kind: "table", set_id: setId, table } : null;
  const { answer, error, busy } = useInspect(source);

  const pick = async () => {
    const [file] = (await bridge()?.pickFiles("tables", false)) ?? [];
    if (file) {
      setPath(file);
      setSheet(null);
      setName(defaultDataName({ kind: "file", path: file }));
    }
  };
  useEffect(() => {
    if (request.mode === "file" && !request.path) void pick();
    else if (request.path) setName(defaultDataName({ kind: "file", path: request.path }));
  }, [request.seq]);
  useEffect(() => {
    if (answer) setMapping(startMapping(answer));
  }, [answer]);

  const problems = answer ? mappingProblems(mapping, name) : [];
  const at = (place: string) => problemAt(problems, place) ?? (touched && refused?.at === place ? refused.message : null);
  const columns = answer?.columns ?? [];
  const used = new Set(Object.values(mapping.fields));
  const numeric = columns.filter((c) => c.kind === "number" && !used.has(c.name));
  const others = columns.filter((c) => c.kind !== "number" && !used.has(c.name));
  const roleOf = (col: string): string => {
    const f = Object.entries(mapping.fields).find(([, c]) => c === col)?.[0];
    if (f) return f.toUpperCase();
    const e = mapping.elements.find((x) => x.column === col);
    if (e) return e.unit ? `${e.name}, ${e.unit}` : e.name;
    return mapping.extra.includes(col) ? "поле" : "";
  };
  const submit = () => {
    if (!source || problems.length) return;
    setTouched(true);
    void app.run("qaqc.import", importArgs(source, name, mapping, replaceId));
  };
  const general = touched && refused && refused.at === null ? refused.message : null;

  return (
    <div className={css.win} data-testid="qaqc-import-form">
      <div className={css.winBody}>
        <fieldset className={css.group}>
          <legend>{request.mode === "file" ? "Файл контрольных проб" : "Таблица проб набора"}</legend>
          {request.mode === "file" ? (
            <div className={css.inline}>
              <span className={css.attrName} title={path ?? ""}>
                {path ?? "Файл не выбран"}
              </span>
              <button type="button" className={css.btn} onClick={() => void pick()}>
                {path ? "Другой файл…" : "Выбрать файл…"}
              </button>
              {answer?.sheets && answer.sheets.length > 1 && (
                <label className={css.check}>
                  Лист
                  <select className={css.input} value={sheet ?? answer.sheet ?? ""} onChange={(e) => setSheet(e.target.value || null)}>
                    {answer.sheets.map((s) => (
                      <option key={s}>{s}</option>
                    ))}
                  </select>
                </label>
              )}
            </div>
          ) : (
            <TablePick
              setId={setId}
              table={table}
              onChange={(s, t, label) => {
                setSetId(s);
                setTable(t);
                if (t) setName(label);
              }}
            />
          )}
          {busy && <p className={css.note}>Чтение…</p>}
          {error && <p className={css.error} role="alert">{error}</p>}
          {at("source") && <p className={css.error} role="alert">{at("source")}</p>}
          {answer && <p className={css.note}>Строк: {answer.rows.toLocaleString("ru-RU")}; колонок: {columns.length}</p>}
          {answer?.warnings?.map((w) => (
            <p key={w} className={css.warn}>
              {w}
            </p>
          ))}
        </fieldset>
        {answer && (
          <>
            <fieldset className={css.group}>
              <legend>Колонки</legend>
              <div className={own.mapGrid}>
                {FIELDS.map((f) => (
                  <FieldRow key={f.key} k={f.key} title={f.title} hint={f.hint} required={f.required} mapping={mapping} columns={columns.map((c) => c.name)} error={at(f.key)} onChange={(col) => setMapping(setField(mapping, f.key, col))} />
                ))}
              </div>
              <button type="button" className={css.btn} style={{ alignSelf: "flex-start" }} onClick={() => setMapping(startMapping({ columns, mapping: null }))}>
                Автоподбор
              </button>
            </fieldset>
            <fieldset className={css.group}>
              <legend>Элементы</legend>
              {at("elements") && <p className={css.error}>{at("elements")}</p>}
              <div className={own.elements}>
                <span className={own.head} />
                <span className={own.head}>Колонка</span>
                <span className={own.head}>Элемент</span>
                <span className={own.head}>Единица</span>
                <span className={own.head}>DL полем</span>
                {numeric.map((c) => {
                  const e = mapping.elements.find((x) => x.column === c.name);
                  const err = at(`el:${c.name}`);
                  return (
                    <ElementRow key={c.name} column={c.name} on={!!e} name={e?.name ?? ""} unit={e?.unit ?? ""} dl={e?.dl ?? ""} dlColumns={numeric.map((x) => x.name).filter((x) => x !== c.name)} error={err} onToggle={(on) => setMapping(toggleElement(mapping, c.name, on))} onPatch={(p) => setMapping(patchElement(mapping, c.name, p))} />
                  );
                })}
              </div>
              {!numeric.length && <p className={css.note}>Нет числовых колонок.</p>}
              <datalist id="qaqc-units">
                {UNITS.map((u) => (
                  <option key={u} value={u} />
                ))}
              </datalist>
            </fieldset>
            {others.length > 0 && (
              <fieldset className={css.group}>
                <legend>Прочие поля (фильтр «любое поле»)</legend>
                <div className={css.row}>
                  {others.map((c) => (
                    <label key={c.name} className={css.check}>
                      <input type="checkbox" checked={mapping.extra.includes(c.name)} onChange={(e) => setMapping({ ...mapping, extra: e.target.checked ? [...mapping.extra, c.name] : mapping.extra.filter((x) => x !== c.name) })} />
                      {c.name}
                    </label>
                  ))}
                </div>
                {at("extra") && <p className={css.error}>{at("extra")}</p>}
              </fieldset>
            )}
            <fieldset className={css.group}>
              <legend>Предпросмотр</legend>
              <div className={css.tableWrap}>
                <table className={css.table} data-testid="qaqc-import-preview">
                  <thead>
                    <tr>
                      {columns.map((c) => (
                        <th key={c.name} title={roleOf(c.name) || "не используется"}>
                          {c.name}
                          <br />
                          <small>{roleOf(c.name) || "—"}</small>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {answer.preview.slice(0, PREVIEW_ROWS).map((r, i) => (
                      <tr key={i}>
                        {columns.map((c) => (
                          <td key={c.name}>{cellOf(r, columns, c.name)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </fieldset>
            <div className={css.formRow}>
              <span>Название данных</span>
              <input className={css.input} value={name} aria-invalid={!!at("name")} onChange={(e) => setName(e.target.value)} data-testid="qaqc-import-name" />
              {at("name") && <span className={own.fieldError}>{at("name")}</span>}
            </div>
            <div className={css.formRow}>
              <span>Куда</span>
              <select className={css.input} value={replaceId ?? ""} onChange={(e) => setReplaceId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">Новые данные</option>
                {list.map((d) => (
                  <option key={d.id} value={d.id}>
                    Заменить «{d.name}»
                  </option>
                ))}
              </select>
            </div>
          </>
        )}
        {general && <p className={css.error} role="alert">{general}</p>}
      </div>
      <div className={css.footer}>
        <span className={css.grow}>{problems.length > 0 && <span className={css.warn}>{problems[0].message}</span>}</span>
        <button type="button" className={css.btn} onClick={closeForm}>
          Отмена
        </button>
        <button type="button" className={css.primary} disabled={!source || !answer || problems.length > 0} onClick={submit} data-testid="qaqc-import-start">
          Загрузить
        </button>
      </div>
    </div>
  );
}

function FieldRow(p: { k: FieldKey; title: string; hint: string; required: boolean; mapping: Mapping; columns: string[]; error: string | null; onChange: (col: string) => void }) {
  return (
    <>
      <span className={p.required ? own.req : undefined} title={p.hint}>
        {p.title}
      </span>
      <select className={css.input} value={p.mapping.fields[p.k] ?? ""} aria-invalid={!!p.error} aria-label={p.title} data-testid={`qaqc-map-${p.k}`} onChange={(e) => p.onChange(e.target.value)}>
        <option value="">— нет —</option>
        {p.columns.map((c) => (
          <option key={c}>{c}</option>
        ))}
      </select>
      {p.error && <span className={own.fieldError}>{p.error}</span>}
    </>
  );
}

function ElementRow(p: { column: string; on: boolean; name: string; unit: string; dl: string; dlColumns: string[]; error: string | null; onToggle: (on: boolean) => void; onPatch: (patch: { name?: string; unit?: string; dl?: string | null }) => void }) {
  return (
    <>
      <input type="checkbox" checked={p.on} aria-label={`${p.column}: элемент`} onChange={(e) => p.onToggle(e.target.checked)} />
      <span className={css.attrName} title={p.error ?? p.column} style={p.error ? { color: "var(--danger)" } : undefined}>
        {p.column}
      </span>
      <input className={own.cellInput} disabled={!p.on} value={p.name} aria-invalid={!!p.error} aria-label={`${p.column}: имя элемента`} onChange={(e) => p.onPatch({ name: e.target.value })} />
      <input className={own.cellInput} disabled={!p.on} list="qaqc-units" value={p.unit} aria-label={`${p.column}: единица`} placeholder="единица" onChange={(e) => p.onPatch({ unit: e.target.value })} />
      <select className={own.cellInput} disabled={!p.on} value={p.dl} aria-label={`${p.column}: колонка DL`} onChange={(e) => p.onPatch({ dl: e.target.value || null })}>
        <option value="">— нет —</option>
        {p.dlColumns.map((c) => (
          <option key={c}>{c}</option>
        ))}
      </select>
    </>
  );
}
