import { useEffect, useMemo, useRef, useState } from "react";
import { bridge, saveFile } from "../../bridge";
import { api, CoreError } from "../../shell/api";
import type { DownholeData, IntervalTableInfo } from "../../viz/downhole/types";
import { logCsv, type CsvColumn } from "./csv";
import { loadHoleNames, loadLog, loadTables, MAX_HOLES } from "./data";
import { pngBytes, svgText } from "./export";
import { StripLogChart } from "./StripLogChart";
import { ColorField } from "../../shell/PropsParts";
import {
  DEFAULT_STRIPLOG, defaultClasses, defaultTracks, fieldRange, LOG_COLORS, newTrack, sortClasses, TRACK_TITLES, type ColorClass, type StripLogSettings, type Track, type TrackType,
} from "./model";
import css from "./StripLog.module.css";

const errText = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

const SCALES: [number, string][] = [[0, "вписать"], [0.5, "1:2000"], [1, "1:1000"], [2, "1:500"], [4, "1:250"], [8, "1:125"], [20, "1:50"]];

/** «Данные по скважинам» (ТЗ 7.17; ported from BF): columns of interval tables along up to 30 holes side by side.
 * A viewer: nothing is saved; PNG, SVG and CSV are written through the save dialog. */
export function StripLogWindow({ setId, setName, initial, tableHint, holeHint }: {
  setId: number;
  setName: string;
  initial?: Partial<StripLogSettings>;
  /** opened from this table (its id): its fields become the first columns */
  tableHint?: string | null;
  /** the hole selected in the project, shown first */
  holeHint?: string | null;
}) {
  const [s, setS] = useState<StripLogSettings>({ ...DEFAULT_STRIPLOG, ...initial });
  const [tables, setTables] = useState<IntervalTableInfo[] | null>(null);
  const [holes, setHoles] = useState<{ hole_id: string }[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [data, setData] = useState<DownholeData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  // the table whose fields are the columns, and the table of the «+ колонка» list (fields of other tables)
  const [addTable, setAddTable] = useState<string>("");
  const [mixTable, setMixTable] = useState<string>("");
  const svgRef = useRef<SVGSVGElement>(null);
  const mainRef = useRef<HTMLDivElement>(null);
  const [fitHeight, setFitHeight] = useState(560);
  const set = (patch: Partial<StripLogSettings>) => setS((p) => ({ ...p, ...patch }));

  // colourings of the project's style library (classes only)
  const [palettes, setPalettes] = useState<{ id: number; name: string; classes: ColorClass[] }[]>([]);
  useEffect(() => {
    api
      .get<{ styles: { id: number; name: string; level: string; body: Record<string, unknown> }[] }>("/styles?level=palette")
      .then((r) => setPalettes(r.styles.filter((x) => Array.isArray(x.body.classes) && (x.body.classes as unknown[]).length)
        .map((x) => ({ id: x.id, name: x.name, classes: x.body.classes as ColorClass[] }))))
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    loadTables(setId).then(setTables).catch((e: unknown) => setError(errText(e)));
    loadHoleNames(setId).then((names) => setHoles(names.map((n) => ({ hole_id: n })))).catch((e: unknown) => setError(errText(e)));
  }, [setId]);

  // First opening: columns of the table it was opened from, the selected hole (or the first three).
  useEffect(() => {
    if (!tables || !holes) return;
    setS((p) => {
      let next = p;
      if (!p.tracks.length) {
        const t = tables.find((x) => x.key === tableHint) ?? tables.find((x) => x.columns.length);
        if (t) next = { ...next, tracks: defaultTracks(t) };
      }
      if (!p.holes.length && holes.length) {
        const first = holeHint && holes.some((h) => h.hole_id === holeHint) ? [holeHint] : holes.slice(0, 3).map((h) => h.hole_id);
        next = { ...next, holes: first };
      }
      return next;
    });
    // the table shown: the one of the first column, else the one the columns were taken from
    const shown = s.tracks[0]?.table ?? (tables.find((x) => x.key === tableHint) ?? tables.find((x) => x.columns.length))?.key ?? "";
    setAddTable((prev) => prev || shown);
    setMixTable((prev) => prev || shown);
  }, [tables, holes]); // eslint-disable-line react-hooks/exhaustive-deps

  const wanted = useMemo(() => {
    const by = new Map<string, Set<string>>();
    for (const t of s.tracks) by.set(t.table, (by.get(t.table) ?? new Set()).add(t.field));
    return [...by].map(([table, fields]) => ({ table, fields: [...fields] }));
  }, [s.tracks]);
  const key = JSON.stringify([s.holes, wanted]);
  useEffect(() => {
    if (!s.holes.length || !wanted.length) {
      setData(null);
      return;
    }
    let live = true;
    const timer = window.setTimeout(() => {
      setBusy(true);
      loadLog(setId, s.holes, wanted)
        .then((d) => {
          if (!live) return;
          setData(d);
          setError(null);
        })
        .catch((e: unknown) => live && setError(errText(e)))
        .finally(() => live && setBusy(false));
    }, 200);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setId, key]);

  useEffect(() => {
    const el = mainRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setFitHeight(Math.max(300, el.clientHeight - 40)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const updateTrack = (id: string, patch: Partial<Track>) => set({ tracks: s.tracks.map((t) => (t.id === id ? { ...t, ...patch } : t)) });
  const moveTrack = (id: string, dir: -1 | 1) => {
    const i = s.tracks.findIndex((t) => t.id === id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= s.tracks.length) return;
    const next = [...s.tracks];
    [next[i], next[j]] = [next[j], next[i]];
    set({ tracks: next });
  };
  const table = (k: string) => tables?.find((t) => t.key === k);
  const shownHoles = (holes ?? []).filter((h) => !filter || h.hole_id.toLowerCase().includes(filter.toLowerCase()));
  const fileName = `Данные по скважинам ${setName}`;

  async function save(kind: "png" | "svg" | "csv") {
    const b = bridge();
    if (!b || !data) return;
    try {
      let out: string | Uint8Array;
      if (kind === "csv") {
        const columns: CsvColumn[] = s.tracks.map((t) => ({ table: t.table, field: t.field, title: `${t.field} (${table(t.table)?.name ?? t.table})`, event: table(t.table)?.kind === "event" }));
        out = logCsv(data, columns);
      } else {
        const svg = svgRef.current;
        if (!svg) return;
        out = kind === "svg" ? await svgText(svg) : await pngBytes(svg);
      }
      const path = await saveFile(`${fileName}.${kind}`, kind, out);
      setNote(path ? `Сохранено: ${path}` : null);
    } catch (e) {
      setError(errText(e));
    }
  }

  return (
    <div className={css.window}>
      <form className={css.settings} onSubmit={(e) => e.preventDefault()}>
        <fieldset>
          <legend>Скважины ({s.holes.length})</legend>
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="поиск" aria-label="Поиск скважины" />
          <div className={css.holes}>
            {shownHoles.slice(0, 300).map((h) => (
              <label key={h.hole_id} className={css.check}>
                <input type="checkbox" checked={s.holes.includes(h.hole_id)} disabled={!s.holes.includes(h.hole_id) && s.holes.length >= MAX_HOLES}
                  onChange={(e) => set({ holes: e.target.checked ? [...s.holes, h.hole_id] : s.holes.filter((x) => x !== h.hole_id) })} />
                {h.hole_id}
              </label>
            ))}
          </div>
          {shownHoles.length > 300 && <p className={css.muted}>Показаны первые 300 из {shownHoles.length.toLocaleString("ru-RU")} — уточните поиск.</p>}
          <p className={css.muted}>До {MAX_HOLES} скважин рядом.</p>
        </fieldset>
        <fieldset>
          <legend>Глубина</legend>
          <div className={css.pair}>
            <label>от, м<input value={s.from} onChange={(e) => set({ from: e.target.value })} placeholder="устье" inputMode="decimal" aria-label="Глубина от" /></label>
            <label>до, м<input value={s.to} onChange={(e) => set({ to: e.target.value })} placeholder="забой" inputMode="decimal" aria-label="Глубина до" /></label>
          </div>
          <label>Масштаб
            <select value={s.scale} onChange={(e) => set({ scale: Number(e.target.value) })} aria-label="Масштаб по глубине">
              {SCALES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
            </select>
          </label>
          <label className={css.check}><input type="checkbox" checked={s.grid} onChange={(e) => set({ grid: e.target.checked })} /> общая шкала глубин</label>
        </fieldset>
        <fieldset>
          <legend>Таблица</legend>
          <label>Таблица набора
            <select value={addTable} aria-label="Таблица набора" onChange={(e) => {
              // table first: its fields become the columns
              const t = table(e.target.value);
              setAddTable(e.target.value);
              setMixTable(e.target.value);
              if (t) set({ tracks: defaultTracks(t) });
            }}>
              {(tables ?? []).map((t) => <option key={t.key} value={t.key}>{t.name}{t.kind === "event" ? " · события" : ""} · {t.row_count.toLocaleString("ru-RU")}</option>)}
            </select>
          </label>
          <p className={css.muted}>Смена таблицы показывает её поля. Поля других таблиц добавляются ниже («+ колонка»).</p>
          {tables && tables.length === 0 && <p className={css.muted}>В наборе нет таблиц опробования: импортируйте их в мастере скважин.</p>}
        </fieldset>
        <fieldset>
          <legend>Колонки ({s.tracks.length})</legend>
          {s.tracks.map((t, i) => {
            const tb = table(t.table);
            const col = tb?.columns.find((c) => c.name === t.field);
            const numeric = col?.kind === "number";
            const event = tb?.kind === "event";
            const types: TrackType[] = event ? ["event"] : numeric ? ["bars", "line", "color", "text", "color_text"] : ["color_text", "color", "text"];
            const colorBy = t.colorBy ?? (t.type === "color" || t.type === "color_text" ? "scale" : "single");
            const range = numeric && data ? fieldRange(data, t.table, t.field, t.log) : null;
            return (
              <div key={t.id} className={css.track}>
                <div className={css.trackHead}>
                  <strong title={tb?.name}>{t.field}</strong>
                  <span className={css.muted}>{tb?.name}</span>
                  <span className={css.spacer} />
                  <button type="button" className={css.icon} onClick={() => moveTrack(t.id, -1)} disabled={i === 0} title="Левее" aria-label={`Левее: ${t.field}`}>◀</button>
                  <button type="button" className={css.icon} onClick={() => moveTrack(t.id, 1)} disabled={i === s.tracks.length - 1} title="Правее" aria-label={`Правее: ${t.field}`}>▶</button>
                  <button type="button" className={css.icon} onClick={() => set({ tracks: s.tracks.filter((x) => x.id !== t.id) })} title="Убрать колонку" aria-label={`Убрать: ${t.field}`}>×</button>
                </div>
                <div className={css.pair}>
                  <label>Вид
                    <select value={t.type} onChange={(e) => updateTrack(t.id, { type: e.target.value as TrackType })} aria-label={`Вид: ${t.field}`}>
                      {types.map((k) => <option key={k} value={k}>{TRACK_TITLES[k]}</option>)}
                    </select>
                  </label>
                  <label>Ширина
                    <input type="number" min={30} max={400} step={10} value={t.width} onChange={(e) => updateTrack(t.id, { width: Math.max(30, Math.min(400, Number(e.target.value) || 90)) })} aria-label={`Ширина: ${t.field}`} />
                  </label>
                </div>
                {numeric && !event && (
                  <>
                    <div className={css.pair}>
                      <label>от<input value={t.min} onChange={(e) => updateTrack(t.id, { min: e.target.value })} placeholder="авто" inputMode="decimal" aria-label={`Шкала от: ${t.field}`} /></label>
                      <label>до<input value={t.max} onChange={(e) => updateTrack(t.id, { max: e.target.value })} placeholder="авто" inputMode="decimal" aria-label={`Шкала до: ${t.field}`} /></label>
                    </div>
                    <div className={css.pair}>
                      <label className={css.check}><input type="checkbox" checked={t.log} onChange={(e) => updateTrack(t.id, { log: e.target.checked })} /> лог.</label>
                      {(t.type === "bars" || t.type === "line") && colorBy === "single" && (
                        <label className={css.check}>Цвет<ColorField value={t.color} label={`Цвет: ${t.field}`} onCommit={(color) => updateTrack(t.id, { color })} /></label>
                      )}
                    </div>
                    {t.type !== "text" && t.type !== "line" && (
                      <label>Раскраска
                        <select value={colorBy} aria-label={`Раскраска: ${t.field}`} onChange={(e) => {
                          const next = e.target.value as Track["colorBy"];
                          updateTrack(t.id, { colorBy: next, classes: next === "classes" && !t.classes?.length ? defaultClasses(range, t.log) : t.classes });
                        }}>
                          {t.type === "bars" && <option value="single">один цвет</option>}
                          <option value="classes">по градациям содержаний</option>
                          <option value="scale">непрерывная шкала</option>
                        </select>
                      </label>
                    )}
                    {t.type !== "text" && t.type !== "line" && colorBy === "classes" && (
                      <div className={css.pair}>
                        <select value="" aria-label={`Раскраска проекта: ${t.field}`} onChange={(e) => {
                          const p = palettes.find((x) => String(x.id) === e.target.value);
                          if (p) updateTrack(t.id, { classes: sortClasses(p.classes.map((c) => ({ ...c }))) });
                        }}>
                          <option value="">{palettes.length ? "раскраска проекта…" : "нет сохранённых раскрасок"}</option>
                          {palettes.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                        </select>
                        <button type="button" title="Границы по диапазону значений" onClick={() => updateTrack(t.id, { classes: defaultClasses(range, t.log) })}>Авто</button>
                      </div>
                    )}
                  </>
                )}
                {event && (
                  <div className={css.pair}>
                    <label>Значок, px
                      <input type="number" min={4} max={40} value={t.size ?? 12} onChange={(e) => updateTrack(t.id, { size: Math.max(4, Math.min(40, Number(e.target.value) || 12)) })} aria-label={`Размер значка: ${t.field}`} />
                    </label>
                    {numeric && <label className={css.check}>Цвет<ColorField value={t.color} label={`Цвет: ${t.field}`} onCommit={(color) => updateTrack(t.id, { color })} /></label>}
                  </div>
                )}
                {!event && <label className={css.check}><input type="checkbox" checked={t.boundaries} onChange={(e) => updateTrack(t.id, { boundaries: e.target.checked })} /> границы интервалов</label>}
              </div>
            );
          })}
          <div className={css.pair}>
            <select value={mixTable} onChange={(e) => setMixTable(e.target.value)} aria-label="Таблица для новой колонки">
              {(tables ?? []).map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
            </select>
            <select value="" onChange={(e) => {
              const c = table(mixTable)?.columns.find((x) => x.name === e.target.value);
              if (c) set({ tracks: [...s.tracks, { ...newTrack(mixTable, c, s.tracks.length, table(mixTable)?.kind === "event"), color: LOG_COLORS[s.tracks.length % LOG_COLORS.length] }] });
            }} aria-label="Добавить колонку">
              <option value="">+ колонка…</option>
              {(table(mixTable)?.columns ?? []).map((c) => <option key={c.name} value={c.name}>{c.name}{c.kind === "text" ? " (текст)" : c.unit ? `, ${c.unit}` : ""}</option>)}
            </select>
          </div>
        </fieldset>
        <div className={css.export}>
          <button type="button" disabled={!data} onClick={() => void save("png")}>PNG</button>
          <button type="button" disabled={!data} onClick={() => void save("svg")}>SVG</button>
          <button type="button" disabled={!data} onClick={() => void save("csv")}>CSV</button>
        </div>
      </form>
      <div className={css.main} ref={mainRef}>
        {error && <div className={css.error} role="alert">{error}</div>}
        {note && <p className={css.muted}>{note}</p>}
        {!s.holes.length && <p className={css.muted}>Отметьте скважины слева.</p>}
        {s.holes.length > 0 && !s.tracks.length && <p className={css.muted}>Добавьте колонку: таблица и поле внизу слева.</p>}
        {data && s.tracks.length > 0 && (
          <div className={busy ? `${css.scroll} ${css.busy}` : css.scroll}>
            <StripLogChart ref={svgRef} data={data} settings={s} fitHeight={fitHeight} />
          </div>
        )}
      </div>
    </div>
  );
}
