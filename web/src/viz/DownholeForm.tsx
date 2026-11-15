import { useEffect, useMemo, useState } from "react";
import { deletePalette, getIntervalTables, getPalettes, savePalette, type DownholeData, type IntervalTableInfo, type SavedPalette } from "../api";
import {
  categoryColors, classLabel, DEFAULT_STRIPLOG, defaultClasses, displayName, fieldRange, FORM_TITLES, LOG_COLORS, MARKER_TITLES, newTrackId, sortClasses,
  TUBE_RANGE, type ColorClass, type DisplayForm, type MarkerShape, type StripLogSettings, type Track,
} from "../charts/stripLog";
import { loadDownhole } from "./useVizData";

type GradeKind = "bars" | "line" | "tube" | "ribbon";
const GRADE_KINDS: [GradeKind, string][] = [["bars", "Столбчатая диаграмма"], ["line", "Линия (график)"], ["tube", "Интервал: цвет по стволу"], ["ribbon", "Интервал: лента рядом со стволом"]];

const isGrade = (name: string, unit: string) => /^(AU|AG|PT|PD)/i.test(name) || /g\/t|ppm|ppb|г\/т/i.test(unit);

/** Tables a form can take its data from: grades — numbers, lithology — text of interval tables, events — event tables. */
function sources(form: DisplayForm, tables: IntervalTableInfo[]): IntervalTableInfo[] {
  if (form === "event") return tables.filter((t) => t.kind === "event" && t.columns.length > 0);
  const kind = form === "grade" ? "number" : "text";
  return tables.filter((t) => t.kind !== "event" && t.columns.some((c) => c.kind === kind));
}

function fieldsOf(form: DisplayForm, table: IntervalTableInfo | undefined) {
  if (!table) return [];
  return form === "event" ? table.columns : table.columns.filter((c) => c.kind === (form === "grade" ? "number" : "text"));
}

function gradeKind(t: Track): GradeKind {
  if (t.type === "bars" || t.type === "line") return t.type;
  return t.form === "panel" ? "ribbon" : "tube";
}

/**
 * The form of one display of data along the holes: grades (graph, bars, colour by classes), lithology
 * (colours of rocks) or events (markers). Every change is previewed in the view; only «Сохранить» keeps it.
 */
export function DownholeForm({ projectId, setId, form, initial, saved, refreshKey, onPreview, onSave }: {
  projectId: number;
  setId: number;
  form: DisplayForm;
  initial: StripLogSettings | null;
  /** true: an existing display is being edited (the button saves the changes) */
  saved: boolean;
  refreshKey: number;
  onPreview: (settings: StripLogSettings | null) => void;
  /** saves; resolves with the text to show, rejects with the reason */
  onSave: (settings: StripLogSettings, name: string) => Promise<string>;
}) {
  const [tables, setTables] = useState<IntervalTableInfo[] | null>(null);
  const [track, setTrack] = useState<Track | null>(initial?.tracks[0] ?? null);
  const [labels, setLabels] = useState(initial?.labels !== false);
  const [tube, setTube] = useState(initial?.tube ?? 8);
  const [data, setData] = useState<DownholeData | null>(null);
  const [palettes, setPalettes] = useState<SavedPalette[]>([]);
  const [paletteName, setPaletteName] = useState("");
  const [pick, setPick] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(!saved);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const patch = (p: Partial<Track>) => {
    setTrack((t) => (t ? { ...t, ...p } : t));
    setDirty(true);
    setNote(null);
  };

  useEffect(() => {
    getIntervalTables(setId).then(setTables).catch((e: Error) => setError(e.message));
    if (form === "grade") getPalettes(projectId).then(setPalettes).catch(() => undefined);
  }, [setId, projectId, form, refreshKey]);

  const options = useMemo(() => (tables ? sources(form, tables) : []), [tables, form]);
  const table = options.find((t) => t.key === track?.table);
  const fields = fieldsOf(form, table);
  const column = fields.find((c) => c.name === track?.field);

  /** A fresh track of this form for a table and field. */
  const make = (t: IntervalTableInfo, fieldName?: string): Track | null => {
    const cols = fieldsOf(form, t);
    const c = cols.find((x) => x.name === fieldName) ?? cols[0];
    if (!c) return null;
    const base: Track = {
      id: track?.id ?? newTrackId(), table: t.key, field: c.name, type: "bars", width: 90, color: track?.color ?? LOG_COLORS[0],
      min: "", max: "", log: false, boundaries: false,
    };
    if (form === "grade") {
      const kind = track ? gradeKind(track) : "bars";
      return { ...base, type: kind === "bars" || kind === "line" ? kind : "color", form: kind === "ribbon" ? "panel" : "tube",
        log: isGrade(c.name, c.unit), colorBy: track?.colorBy ?? (kind === "bars" || kind === "line" ? "single" : "scale"), classes: track?.classes };
    }
    if (form === "lith") return { ...base, type: track?.type === "color" ? "color" : "color_text", form: track?.form ?? "tube", width: track?.width ?? 110 };
    return { ...base, type: "event", marker: track?.marker ?? "diamond", size: track?.size ?? 12, color: track?.color ?? "#e8873a" };
  };

  // First opening: the first suitable table and field.
  useEffect(() => {
    if (track || !options.length) return;
    setTrack(make(options[0]));
  }, [options]); // eslint-disable-line react-hooks/exhaustive-deps

  // The values of the field over the set: the range, the rocks, the kinds of events.
  useEffect(() => {
    if (!track) return;
    let cancelled = false;
    loadDownhole(setId, "all", { [track.table]: [track.field] }, refreshKey)
      .then((d) => !cancelled && setData(d))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [setId, track?.table, track?.field, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const settings: StripLogSettings | null = useMemo(
    () => (track ? { ...DEFAULT_STRIPLOG, ...(initial ?? {}), allHoles: true, holes: [], form, labels, tube, tracks: [track] } : null),
    [track, labels, tube, form], // eslint-disable-line react-hooks/exhaustive-deps
  );
  useEffect(() => {
    onPreview(settings);
  }, [settings]); // eslint-disable-line react-hooks/exhaustive-deps

  const numeric = column?.kind === "number";
  const range = data && track && numeric ? fieldRange(data, track.table, track.field, track.log) : null;
  const categories = useMemo(
    () => (data && track && !numeric ? [...categoryColors(data, track.table, track.field)] : []),
    [data, track?.table, track?.field, numeric], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const classes = track?.classes ?? [];
  const setClasses = (next: ColorClass[]) => patch({ classes: next });
  const kind = track ? gradeKind(track) : "bars";
  const byClasses = track?.colorBy === "classes";

  if (error && !tables) return <div className="alert error small">{error}</div>;
  if (!tables) return <p className="muted small form-note">Загрузка таблиц…</p>;
  if (!options.length) {
    return (
      <p className="muted small form-note">
        {form === "event"
          ? "В наборе нет таблиц событий. Импортируйте файл (скважина, глубина, поля) в мастере скважин с типом «События по скважинам»."
          : form === "lith"
            ? "В наборе нет интервальных таблиц с текстовыми полями. Импортируйте литологию в мастере скважин с типом «Интервальная таблица»."
            : "В наборе нет таблиц с числовыми полями: импортируйте пробы."}
      </p>
    );
  }
  if (!track) return null;

  return (
    <form className="hist-settings downhole-form" onSubmit={(e) => e.preventDefault()}>
      {error && <div className="alert error small">{error}</div>}
      <fieldset>
        <legend>Данные</legend>
        <label>Таблица
          <select value={track.table} aria-label="Таблица" onChange={(e) => {
            const t = options.find((x) => x.key === e.target.value);
            const next = t && make(t);
            if (next) {
              setTrack(next);
              setDirty(true);
            }
          }}>
            {options.map((t) => <option key={t.key} value={t.key}>{t.name} · {t.row_count.toLocaleString("ru-RU")}</option>)}
          </select>
        </label>
        <label>{form === "grade" ? "Содержание" : form === "lith" ? "Поле (порода, код)" : "Поле (вид события, подпись)"}
          <select value={track.field} aria-label="Поле" onChange={(e) => {
            const next = table && make(table, e.target.value);
            if (next) {
              setTrack({ ...next, catColors: undefined });
              setDirty(true);
            }
          }}>
            {fields.map((c) => <option key={c.name} value={c.name}>{c.name}{c.unit ? `, ${c.unit}` : ""}{form === "event" && c.kind === "number" ? " (число)" : ""}</option>)}
          </select>
        </label>
      </fieldset>

      {form === "grade" && (
        <>
          <fieldset>
            <legend>Вид</legend>
            <label>Вид графика
              <select value={kind} aria-label="Вид графика" onChange={(e) => {
                const k = e.target.value as GradeKind;
                const interval = k === "tube" || k === "ribbon";
                patch({ type: interval ? "color" : k, form: k === "ribbon" ? "panel" : "tube", colorBy: interval && track.colorBy === "single" ? "scale" : track.colorBy });
              }}>
                {GRADE_KINDS.map(([k, title]) => <option key={k} value={k}>{title}</option>)}
              </select>
            </label>
            {kind === "tube" ? (
              <label>Толщина по стволу, px
                <input type="number" min={TUBE_RANGE[0]} max={TUBE_RANGE[1]} value={tube} aria-label="Толщина по стволу, px"
                  onChange={(e) => { setTube(Math.max(TUBE_RANGE[0], Math.min(TUBE_RANGE[1], Number(e.target.value) || 8))); setDirty(true); }} />
              </label>
            ) : (
              <label>Ширина колонки (90 — обычная)
                <input type="number" min={30} max={400} step={10} value={track.width} aria-label="Ширина колонки"
                  onChange={(e) => patch({ width: Math.max(30, Math.min(400, Number(e.target.value) || 90)) })} />
              </label>
            )}
            <div className="pair">
              <label>Шкала от<input value={track.min} placeholder={range ? String(Number(range[0].toPrecision(3))) : "авто"} inputMode="decimal" aria-label="Шкала от" onChange={(e) => patch({ min: e.target.value })} /></label>
              <label>до<input value={track.max} placeholder={range ? String(Number(range[1].toPrecision(3))) : "авто"} inputMode="decimal" aria-label="Шкала до" onChange={(e) => patch({ max: e.target.value })} /></label>
            </div>
            <label className="check"><input type="checkbox" checked={track.log} onChange={(e) => patch({ log: e.target.checked })} /> логарифмическая шкала</label>
          </fieldset>
          <fieldset>
            <legend>Раскраска</legend>
            <label>Цвет
              <select value={track.colorBy ?? "single"} aria-label="Раскраска" onChange={(e) => {
                const colorBy = e.target.value as Track["colorBy"];
                patch({ colorBy, classes: colorBy === "classes" && !classes.length ? defaultClasses(range, track.log) : track.classes });
              }}>
                {(kind === "bars" || kind === "line") && <option value="single">один цвет</option>}
                <option value="classes">по градациям содержаний</option>
                <option value="scale">непрерывная шкала</option>
              </select>
            </label>
            {(track.colorBy ?? "single") === "single" && (
              <label className="color-pick">Цвет<input type="color" value={track.color} aria-label="Цвет" onChange={(e) => patch({ color: e.target.value })} /></label>
            )}
            {byClasses && (
              <>
                <div className="class-list">
                  {classes.map((c, i) => (
                    <div key={i} className="class-row">
                      <input type="color" value={c.color} aria-label={`Цвет класса ${i + 1}`} onChange={(e) => setClasses(classes.map((x, j) => (j === i ? { ...x, color: e.target.value } : x)))} />
                      {c.from === null ? (
                        <span className="muted small class-from">ниже</span>
                      ) : (
                        <input className="class-from" type="number" step="any" value={c.from} aria-label={`Нижняя граница класса ${i + 1}`}
                          onChange={(e) => setClasses(classes.map((x, j) => (j === i ? { ...x, from: e.target.value === "" ? 0 : Number(e.target.value) } : x)))}
                          onBlur={() => setClasses(sortClasses(classes))} />
                      )}
                      <span className="muted small grow">{classLabel(classes, i)}</span>
                      <button type="button" className="icon" title="Убрать класс" aria-label={`Убрать класс ${i + 1}`} disabled={classes.length <= 1}
                        onClick={() => setClasses(sortClasses(classes.filter((_, j) => j !== i)))}>×</button>
                    </div>
                  ))}
                </div>
                <div className="pair">
                  <button type="button" onClick={() => {
                    const last = classes[classes.length - 1]?.from ?? range?.[0] ?? 0;
                    const step = classes.length > 1 && classes[classes.length - 2].from !== null ? last - (classes[classes.length - 2].from as number) : last || 1;
                    setClasses(sortClasses([...classes, { from: Number((last + Math.abs(step || 1)).toPrecision(4)), color: LOG_COLORS[classes.length % LOG_COLORS.length] }]));
                  }}>+ класс</button>
                  <button type="button" title="Границы по диапазону значений" onClick={() => setClasses(defaultClasses(range, track.log))}>Авто</button>
                </div>
                <div className="palette-box">
                  <span className="muted small">Сохранённые раскраски проекта</span>
                  <div className="palette-row">
                    <select value={pick} aria-label="Сохранённая раскраска" onChange={(e) => setPick(e.target.value)}>
                      <option value="">— выбрать —</option>
                      {palettes.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                    <button type="button" disabled={!pick} onClick={() => {
                      const p = palettes.find((x) => String(x.id) === pick);
                      if (p) {
                        setClasses(sortClasses(p.classes.map((c) => ({ ...c }))));
                        setNote(`Загружена раскраска «${p.name}»`);
                      }
                    }}>Загрузить</button>
                    <button type="button" className="icon" disabled={!pick} title="Удалить раскраску из проекта" aria-label="Удалить раскраску" onClick={() => {
                      const p = palettes.find((x) => String(x.id) === pick);
                      if (!p || !window.confirm(`Удалить раскраску «${p.name}» из проекта?`)) return;
                      deletePalette(p.id).then(() => {
                        setPalettes((prev) => prev.filter((x) => x.id !== p.id));
                        setPick("");
                      }).catch((e: Error) => setError(e.message));
                    }}>×</button>
                  </div>
                  <div className="palette-row">
                    <input value={paletteName} placeholder={`название, например «${track.field} борта»`} aria-label="Название раскраски" onChange={(e) => setPaletteName(e.target.value)} />
                    <button type="button" disabled={!paletteName.trim() || !classes.length} onClick={() => {
                      savePalette(projectId, paletteName.trim(), sortClasses(classes))
                        .then((p) => {
                          setPalettes((prev) => [...prev.filter((x) => x.id !== p.id), p].sort((a, b) => a.name.localeCompare(b.name)));
                          setPick(String(p.id));
                          setNote(`Раскраска «${p.name}» сохранена в проекте`);
                          setError(null);
                        })
                        .catch((e: Error) => setError(e.message));
                    }}>Сохранить раскраску</button>
                  </div>
                </div>
              </>
            )}
          </fieldset>
        </>
      )}

      {form === "lith" && (
        <>
          <fieldset>
            <legend>Вид</legend>
            <label>Показать как
              <select value={track.form ?? "tube"} aria-label="Показать как" onChange={(e) => patch({ form: e.target.value as "tube" | "panel" })}>
                <option value="tube">цвет по стволу (трубка)</option>
                <option value="panel">лента рядом со стволом</option>
              </select>
            </label>
            {(track.form ?? "tube") === "tube" ? (
              <label>Толщина по стволу, px
                <input type="number" min={TUBE_RANGE[0]} max={TUBE_RANGE[1]} value={tube} aria-label="Толщина по стволу, px"
                  onChange={(e) => { setTube(Math.max(TUBE_RANGE[0], Math.min(TUBE_RANGE[1], Number(e.target.value) || 8))); setDirty(true); }} />
              </label>
            ) : (
              <label>Ширина ленты (90 — обычная)
                <input type="number" min={30} max={400} step={10} value={track.width} aria-label="Ширина ленты" onChange={(e) => patch({ width: Math.max(30, Math.min(400, Number(e.target.value) || 90)) })} />
              </label>
            )}
            <label className="check"><input type="checkbox" checked={track.type === "color_text" && labels}
              onChange={(e) => { patch({ type: e.target.checked ? "color_text" : "color" }); setLabels(true); }} /> подписи пород вдоль ствола</label>
          </fieldset>
          <fieldset>
            <legend>Цвета ({categories.length})</legend>
            <div className="class-list">
              {categories.slice(0, 60).map(([value, auto]) => (
                <div key={value} className="class-row">
                  <input type="color" value={track.catColors?.[value] ?? auto} aria-label={`Цвет: ${value}`}
                    onChange={(e) => patch({ catColors: { ...(track.catColors ?? {}), [value]: e.target.value } })} />
                  <span className="grow name" title={value}>{value}</span>
                </div>
              ))}
              {!categories.length && <span className="muted small">Нет значений.</span>}
              {categories.length > 60 && <span className="muted small">… и ещё {categories.length - 60}: цвета по умолчанию</span>}
            </div>
          </fieldset>
        </>
      )}

      {form === "event" && (
        <>
          <fieldset>
            <legend>Значок</legend>
            <div className="pair">
              <label>Значок
                <select value={track.marker ?? "diamond"} aria-label="Значок" onChange={(e) => patch({ marker: e.target.value as MarkerShape })}>
                  {(Object.keys(MARKER_TITLES) as MarkerShape[]).map((m) => <option key={m} value={m}>{MARKER_TITLES[m]}</option>)}
                </select>
              </label>
              <label>Размер, px
                <input type="number" min={4} max={40} value={track.size ?? 12} aria-label="Размер значка, px" onChange={(e) => patch({ size: Math.max(4, Math.min(40, Number(e.target.value) || 12)) })} />
              </label>
            </div>
            <label className="check"><input type="checkbox" checked={labels} onChange={(e) => { setLabels(e.target.checked); setDirty(true); }} /> подпись у значка</label>
            {numeric && <label className="color-pick">Цвет<input type="color" value={track.color} aria-label="Цвет значка" onChange={(e) => patch({ color: e.target.value })} /></label>}
          </fieldset>
          {!numeric && (
            <fieldset>
              <legend>Цвета по значению ({categories.length})</legend>
              <div className="class-list">
                {categories.slice(0, 60).map(([value, auto]) => (
                  <div key={value} className="class-row">
                    <input type="color" value={track.catColors?.[value] ?? auto} aria-label={`Цвет: ${value}`}
                      onChange={(e) => patch({ catColors: { ...(track.catColors ?? {}), [value]: e.target.value } })} />
                    <span className="grow name" title={value}>{value}</span>
                  </div>
                ))}
                {!categories.length && <span className="muted small">Нет значений.</span>}
              </div>
            </fieldset>
          )}
        </>
      )}

      <div className="form-actions">
        <button type="button" className="primary" disabled={!settings || saving || (saved && !dirty)} onClick={() => {
          if (!settings) return;
          setSaving(true);
          setSaveError(null);
          onSave(settings, displayName(settings, table?.name))
            .then((text) => {
              setDirty(false);
              setNote(text);
            })
            .catch((e: Error) => setSaveError(`Не сохранено: ${e.message}${/Input should be|downhole/i.test(e.message)
              ? ". Сервер (контейнер api) старой версии: запустите BedrockForge.bat, он пересоберёт api" : ""}`))
            .finally(() => setSaving(false));
        }}>{saving ? "Сохранение…" : "Сохранить"}</button>
        {saveError ? <span className="small form-error">{saveError}</span> : <span className="muted small">
          {note ?? (saved ? (dirty ? "Изменения видны на скважинах; сохранятся по кнопке." : "Сохранено.") : `${FORM_TITLES[form]}: видно на скважинах; в дерево попадёт по кнопке.`)}
        </span>}
      </div>
    </form>
  );
}
