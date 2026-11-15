import { useEffect, useMemo, useRef, useState } from "react";
import { getDownhole, getIntervalTables, type DownholeData, type Hole, type IntervalTableInfo } from "../api";
import { loadHoles } from "../viz/useVizData";
import { download, savePng, saveSvg, toCsv } from "./exportChart";
import { StripLogChart } from "./StripLogChart";
import {
  DEFAULT_STRIPLOG, defaultTracks, LOG_COLORS, newTrack, stripLogName, TRACK_TITLES, type StripLogSettings, type Track, type TrackType,
} from "./stripLog";

const SCALES: [number, string][] = [[0, "вписать"], [0.5, "1:2000"], [1, "1:1000"], [2, "1:500"], [4, "1:250"], [8, "1:125"], [20, "1:50"]];

/** Downhole log: columns of interval tables along the chosen holes (graphs, bars, colour, text). */
export function StripLogWindow({ setId, setName, initial, tableHint, holeHint, onSettings }: {
  setId: number;
  setName: string;
  initial?: Partial<StripLogSettings>;
  /** opened from this table ("assay" or an id): its fields become the first columns */
  tableHint?: string | null;
  /** the hole selected in the project, shown first */
  holeHint?: string | null;
  onSettings?: (settings: StripLogSettings, autoName: string) => void;
}) {
  const [s, setS] = useState<StripLogSettings>({ ...DEFAULT_STRIPLOG, ...initial });
  const [tables, setTables] = useState<IntervalTableInfo[] | null>(null);
  const [holes, setHoles] = useState<Hole[] | null>(null);
  const [data, setData] = useState<DownholeData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  const [addTable, setAddTable] = useState<string>("");
  const svgRef = useRef<SVGSVGElement>(null);
  const mainRef = useRef<HTMLDivElement>(null);
  const [fitHeight, setFitHeight] = useState(560);
  const set = (patch: Partial<StripLogSettings>) => setS((p) => ({ ...p, ...patch }));

  useEffect(() => {
    getIntervalTables(setId).then(setTables).catch((e: Error) => setError(e.message));
    loadHoles(setId, 0).then(setHoles).catch((e: Error) => setError(e.message));
  }, [setId]);

  // First opening: columns of the table it was opened from, the selected hole (or the first three).
  useEffect(() => {
    if (!tables || !holes) return;
    setS((p) => {
      let next = p;
      if (!p.tracks.length) {
        const t = tables.find((x) => x.key === (tableHint ?? "assay")) ?? tables.find((x) => x.columns.length);
        if (t) next = { ...next, tracks: defaultTracks(t) };
      }
      if (!p.holes.length && holes.length) {
        const first = holeHint && holes.some((h) => h.hole_id === holeHint) ? [holeHint] : holes.slice(0, 3).map((h) => h.hole_id);
        next = { ...next, holes: first };
      }
      return next;
    });
    setAddTable((prev) => prev || tableHint || tables[0]?.key || "");
  }, [tables, holes]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (s.holes.length) onSettings?.(s, stripLogName(s));
  }, [s]); // eslint-disable-line react-hooks/exhaustive-deps

  const wanted = useMemo(() => [...new Set(s.tracks.map((t) => t.table))], [s.tracks]);
  const key = JSON.stringify([s.holes, wanted]);
  useEffect(() => {
    if (!s.holes.length || !wanted.length) {
      setData(null);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setBusy(true);
      getDownhole(setId, s.holes, wanted, controller.signal)
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch((e: Error) => e.name !== "AbortError" && setError(e.message))
        .finally(() => setBusy(false));
    }, 200);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
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
  const fileName = `Данные по стволу ${setName}`;

  function saveCsv() {
    if (!data) return;
    const rows: (string | number | null)[][] = [];
    for (const h of data.holes) {
      for (const t of s.tracks) {
        for (const iv of h.tables[t.table] ?? []) {
          const v = iv.values[t.field];
          rows.push([h.hole_id, table(t.table)?.name ?? t.table, t.field, iv.from, iv.to, v === undefined ? null : (v as string | number | null)]);
        }
      }
    }
    download(`${fileName}.csv`, new Blob([toCsv(["Скважина", "Таблица", "Поле", "От", "До", "Значение"], rows)], { type: "text/csv" }));
  }

  return (
    <div className="hist-window strip-window">
      <form className="hist-settings" onSubmit={(e) => e.preventDefault()}>
        <fieldset>
          <legend>Скважины ({s.holes.length})</legend>
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="поиск" aria-label="Поиск скважины" />
          <div className="dendro-fields strip-holes">
            {shownHoles.slice(0, 300).map((h) => (
              <label key={h.hole_id} className="check">
                <input type="checkbox" checked={s.holes.includes(h.hole_id)} disabled={!s.holes.includes(h.hole_id) && s.holes.length >= 30}
                  onChange={(e) => set({ holes: e.target.checked ? [...s.holes, h.hole_id] : s.holes.filter((x) => x !== h.hole_id) })} />
                {h.hole_id}
              </label>
            ))}
          </div>
          <p className="muted small">До 30 скважин рядом.</p>
        </fieldset>
        <fieldset>
          <legend>Глубина</legend>
          <div className="pair">
            <label>от, м<input value={s.from} onChange={(e) => set({ from: e.target.value })} placeholder="устье" inputMode="decimal" aria-label="Глубина от" /></label>
            <label>до, м<input value={s.to} onChange={(e) => set({ to: e.target.value })} placeholder="забой" inputMode="decimal" aria-label="Глубина до" /></label>
          </div>
          <label>Масштаб
            <select value={s.scale} onChange={(e) => set({ scale: Number(e.target.value) })} aria-label="Масштаб по глубине">
              {SCALES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
            </select>
          </label>
          <label className="check"><input type="checkbox" checked={s.grid} onChange={(e) => set({ grid: e.target.checked })} /> общая шкала глубин</label>
        </fieldset>
        <fieldset>
          <legend>Колонки ({s.tracks.length})</legend>
          {s.tracks.map((t, i) => {
            const tb = table(t.table);
            const col = tb?.columns.find((c) => c.name === t.field);
            const numeric = col?.kind === "number";
            const types: TrackType[] = numeric ? ["bars", "line", "color", "text", "color_text"] : ["color_text", "color", "text"];
            return (
              <div key={t.id} className="strip-track">
                <div className="strip-track-head">
                  <strong title={tb?.name}>{t.field}</strong>
                  <span className="muted small">{tb?.name}</span>
                  <span className="spacer" />
                  <button type="button" className="icon" onClick={() => moveTrack(t.id, -1)} disabled={i === 0} title="Левее" aria-label={`Левее: ${t.field}`}>◀</button>
                  <button type="button" className="icon" onClick={() => moveTrack(t.id, 1)} disabled={i === s.tracks.length - 1} title="Правее" aria-label={`Правее: ${t.field}`}>▶</button>
                  <button type="button" className="icon" onClick={() => set({ tracks: s.tracks.filter((x) => x.id !== t.id) })} title="Убрать колонку" aria-label={`Убрать: ${t.field}`}>×</button>
                </div>
                <div className="pair">
                  <label>Вид
                    <select value={t.type} onChange={(e) => updateTrack(t.id, { type: e.target.value as TrackType })} aria-label={`Вид: ${t.field}`}>
                      {types.map((k) => <option key={k} value={k}>{TRACK_TITLES[k]}</option>)}
                    </select>
                  </label>
                  <label>Ширина
                    <input type="number" min={30} max={400} step={10} value={t.width} onChange={(e) => updateTrack(t.id, { width: Math.max(30, Math.min(400, Number(e.target.value) || 90)) })} aria-label={`Ширина: ${t.field}`} />
                  </label>
                </div>
                {numeric && (
                  <>
                    <div className="pair">
                      <label>от<input value={t.min} onChange={(e) => updateTrack(t.id, { min: e.target.value })} placeholder="авто" inputMode="decimal" aria-label={`Шкала от: ${t.field}`} /></label>
                      <label>до<input value={t.max} onChange={(e) => updateTrack(t.id, { max: e.target.value })} placeholder="авто" inputMode="decimal" aria-label={`Шкала до: ${t.field}`} /></label>
                    </div>
                    <div className="pair">
                      <label className="check"><input type="checkbox" checked={t.log} onChange={(e) => updateTrack(t.id, { log: e.target.checked })} /> лог.</label>
                      {(t.type === "bars" || t.type === "line") && (
                        <label className="color-pick">Цвет<input type="color" value={t.color} onChange={(e) => updateTrack(t.id, { color: e.target.value })} aria-label={`Цвет: ${t.field}`} /></label>
                      )}
                    </div>
                  </>
                )}
                <label className="check"><input type="checkbox" checked={t.boundaries} onChange={(e) => updateTrack(t.id, { boundaries: e.target.checked })} /> границы интервалов</label>
              </div>
            );
          })}
          <div className="strip-add">
            <select value={addTable} onChange={(e) => setAddTable(e.target.value)} aria-label="Таблица для новой колонки">
              {(tables ?? []).map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
            </select>
            <select value="" onChange={(e) => {
              const c = table(addTable)?.columns.find((x) => x.name === e.target.value);
              if (c) set({ tracks: [...s.tracks, { ...newTrack(addTable, c, s.tracks.length), color: LOG_COLORS[s.tracks.length % LOG_COLORS.length] }] });
            }} aria-label="Добавить колонку">
              <option value="">+ колонка…</option>
              {(table(addTable)?.columns ?? []).map((c) => <option key={c.name} value={c.name}>{c.name}{c.kind === "text" ? " (текст)" : c.unit ? `, ${c.unit}` : ""}</option>)}
            </select>
          </div>
        </fieldset>
        <div className="hist-export">
          <button type="button" disabled={!data} onClick={() => svgRef.current && void savePng(svgRef.current, fileName)}>PNG</button>
          <button type="button" disabled={!data} onClick={() => svgRef.current && saveSvg(svgRef.current, fileName)}>SVG</button>
          <button type="button" disabled={!data} onClick={saveCsv}>CSV</button>
        </div>
      </form>
      <div className="hist-main strip-main" ref={mainRef}>
        {error && <div className="alert error small">{error}</div>}
        {!s.holes.length && <p className="muted small">Отметьте скважины слева.</p>}
        {s.holes.length > 0 && !s.tracks.length && <p className="muted small">Добавьте колонку: таблица и поле внизу слева.</p>}
        {data && s.tracks.length > 0 && (
          <div className={busy ? "strip-scroll busy" : "strip-scroll"}>
            <StripLogChart ref={svgRef} data={data} settings={s} fitHeight={fitHeight} />
          </div>
        )}
      </div>
    </div>
  );
}
