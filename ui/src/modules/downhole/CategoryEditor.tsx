import { useMemo, useRef, useState } from "react";
import type { DownholeData } from "../../viz/downhole/types";
import { categoryColors, MARKER_TITLES, type MarkerShape, type Track } from "../../charts/striplog/model";
import { GRADIENTS, gradientCss, rampColors, rgbToHex } from "../../viz/downhole/gradients";
import { ColorField, MenuItem, openCsvText, Popover, saveCsv } from "./ui";
import { MARKER_ICONS } from "./sections/ViewSection";

const ROW = 26;
const VIEW = 234;

interface Cat { value: string; color: string; count: number; length: number }

/** "code;#rrggbb" lines (comma, semicolon or tab between them) -> colours of codes. */
export function parseColorTable(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const m = /^\s*"?(.*?)"?\s*[;,\t]\s*"?(#[0-9a-f]{6})"?\s*$/i.exec(line);
    if (m && m[1]) out[m[1]] = m[2].toLowerCase();
  }
  return out;
}

/** The values of a text field: search, colours, eyes, order of the legend, colours for many at once, import and export of "code — colour". */
export function CategoryEditor({ data, track, patch, markers }: {
  data: DownholeData | null;
  track: Track;
  patch: (p: Partial<Track>, key?: string) => void;
  /** events: a marker of its own for a value */
  markers?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [scroll, setScroll] = useState(0);
  const [note, setNote] = useState<string | null>(null);
  const last = useRef<string | null>(null);
  const dragged = useRef<string | null>(null);

  const cats = useMemo<Cat[]>(() => {
    if (!data) return [];
    const auto = categoryColors(data, track.table, track.field);
    const stats = new Map<string, { count: number; length: number }>();
    for (const h of data.holes) {
      for (const iv of h.tables[track.table] ?? []) {
        const v = iv.values[track.field];
        if (v === null || v === undefined || v === "") continue;
        const s = stats.get(String(v)) ?? { count: 0, length: 0 };
        s.count += 1;
        if (iv.from !== null && iv.to !== null && iv.to > iv.from) s.length += iv.to - iv.from;
        stats.set(String(v), s);
      }
    }
    const order = [...(track.catOrder ?? []).filter((k) => auto.has(k)), ...[...auto.keys()].filter((k) => !track.catOrder?.includes(k))];
    return order.map((value) => ({ value, color: track.catColors?.[value] ?? (auto.get(value) as string), ...(stats.get(value) ?? { count: 0, length: 0 }) }));
  }, [data, track.table, track.field, track.catColors, track.catOrder]);

  const hidden = useMemo(() => new Set(track.hidden ?? []), [track.hidden]);
  const shown = useMemo(() => {
    const q = query.trim().toUpperCase();
    return q ? cats.filter((c) => c.value.toUpperCase().includes(q)) : cats;
  }, [cats, query]);
  const first = Math.max(0, Math.floor(scroll / ROW) - 4);
  const visible = shown.slice(first, first + Math.ceil(VIEW / ROW) + 8);

  const setColors = (colors: Record<string, string>, key?: string) => patch({ catColors: { ...(track.catColors ?? {}), ...colors } }, key);
  const targets = () => (picked.size ? shown.filter((c) => picked.has(c.value)) : shown);
  const click = (value: string, e: React.MouseEvent) => {
    const next = new Set(e.ctrlKey || e.metaKey || e.shiftKey ? picked : []);
    if (e.shiftKey && last.current) {
      const a = shown.findIndex((c) => c.value === last.current);
      const b = shown.findIndex((c) => c.value === value);
      if (a >= 0 && b >= 0) for (const c of shown.slice(Math.min(a, b), Math.max(a, b) + 1)) next.add(c.value);
    } else if (next.has(value)) next.delete(value);
    else next.add(value);
    last.current = value;
    setPicked(next);
  };
  const drop = (onto: string) => {
    const from = dragged.current;
    dragged.current = null;
    if (!from || from === onto) return;
    const order = cats.map((c) => c.value).filter((v) => v !== from);
    order.splice(order.indexOf(onto), 0, from);
    patch({ catOrder: order });
  };
  const table = () => cats.map((c) => `${c.value};${c.color}`).join("\n");
  const load = (text: string) => {
    const colors = parseColorTable(text);
    const known = Object.keys(colors).filter((k) => cats.some((c) => c.value === k));
    if (!known.length) return setNote("В тексте нет строк «код;#цвет» для значений этого поля");
    setColors(Object.fromEntries(known.map((k) => [k, colors[k]])));
    setNote(`Цвета заданы для ${known.length} значений`);
  };

  if (!data) return <span className="muted small">Загрузка значений…</span>;
  return (
    <div className="dh-cats">
      <div className="dh-tools">
        <input className="grow" value={query} placeholder={`поиск среди ${cats.length}`} aria-label="Поиск значения" onChange={(e) => { setQuery(e.target.value); setScroll(0); }} />
        {picked.size > 0 && (
          <label className="dh-color" title={`Один цвет для выбранных (${picked.size})`}>
            <ColorField value="" label="Цвет выбранных значений" onChange={(color) => setColors(Object.fromEntries([...picked].map((k) => [k, color])), "catmany")} />
          </label>
        )}
        <Popover label="Действия с цветами" title="Цвета для многих значений, импорт и экспорт" className="small-btn" button="Цвета ▾" align="right">
          {(close) => (
            <div className="dh-pop-form">
              <span className="muted small">{picked.size ? `Для выбранных (${picked.size})` : "Для всех показанных"}</span>
              <MenuItem onClick={() => {
                setColors(Object.fromEntries(targets().map((c) => [c.value, rgbToHex(40 + Math.random() * 200, 40 + Math.random() * 200, 40 + Math.random() * 200)])));
                close();
              }}>Случайные цвета</MenuItem>
              {GRADIENTS.map((g) => (
                <button key={g.name} type="button" className="dh-gradient" title={`Цвета из градиента: ${g.title}`} onClick={() => {
                  const list = targets();
                  const colors = rampColors(g.name, list.length);
                  setColors(Object.fromEntries(list.map((c, i) => [c.value, colors[i]])));
                  close();
                }}><i style={{ background: gradientCss(g.name) }} />{g.title}</button>
              ))}
              <hr />
              <MenuItem title="Файл CSV со строками «код;#rrggbb»" onClick={() => { close(); void openCsvText().then((text) => text !== null && load(text)).catch((e: Error) => setNote(e.message)); }}>Импорт «код — цвет» из CSV…</MenuItem>
              <MenuItem title="Строки «код;#rrggbb» из буфера обмена" onClick={() => {
                navigator.clipboard?.readText().then(load).catch(() => setNote("Буфер обмена недоступен: используйте импорт из файла"));
                close();
              }}>Вставить из буфера обмена</MenuItem>
              <MenuItem onClick={() => { close(); void saveCsv(`${track.field}_colors.csv`, table()).then((path) => path && setNote(`Сохранено: ${path}`)).catch((e: Error) => setNote(e.message)); }}>Экспорт в CSV…</MenuItem>
              <MenuItem onClick={() => {
                navigator.clipboard?.writeText(table()).then(() => setNote("Таблица «код;цвет» скопирована")).catch(() => setNote("Буфер обмена недоступен"));
                close();
              }}>Копировать в буфер обмена</MenuItem>
              {hidden.size > 0 && <MenuItem onClick={() => { patch({ hidden: undefined }); close(); }}>Показать все скрытые ({hidden.size})</MenuItem>}
            </div>
          )}
        </Popover>
      </div>
      <div className="dh-cat-list" style={{ maxHeight: VIEW }} role="listbox" aria-label="Значения поля" aria-multiselectable onScroll={(e) => setScroll((e.target as HTMLDivElement).scrollTop)}>
        <div style={{ height: shown.length * ROW, position: "relative" }}>
          {visible.map((c, k) => {
            const off = hidden.has(c.value);
            return (
              <div
                key={c.value} className={`dh-cat-row${off ? " off" : ""}${picked.has(c.value) ? " picked" : ""}`} style={{ top: (first + k) * ROW, height: ROW }}
                role="option" aria-selected={picked.has(c.value)} draggable={!query}
                onDragStart={() => (dragged.current = c.value)} onDragOver={(e) => e.preventDefault()} onDrop={() => drop(c.value)}
              >
                <span className="dh-handle" title="Перетащите, чтобы изменить порядок в легенде" aria-hidden>⠿</span>
                <button type="button" className="icon dh-eye" aria-pressed={!off} title={off ? "Скрыто в сцене: показать" : "Скрыть в сцене"} aria-label={`${off ? "Показать" : "Скрыть"}: ${c.value}`}
                  onClick={() => patch({ hidden: off ? (track.hidden ?? []).filter((x) => x !== c.value) : [...(track.hidden ?? []), c.value] })}>{off ? "◌" : "👁"}</button>
                <ColorField value={c.color} label={`Цвет: ${c.value}`} onChange={(color) => setColors({ [c.value]: color }, `cat${c.value}`)} />
                {markers && (
                  <select className="dh-cat-marker" value={track.catMarkers?.[c.value] ?? ""} aria-label={`Значок: ${c.value}`} title="Свой значок для этого значения"
                    onChange={(e) => {
                      const next = { ...(track.catMarkers ?? {}) };
                      if (e.target.value) next[c.value] = e.target.value as MarkerShape;
                      else delete next[c.value];
                      patch({ catMarkers: next });
                    }}>
                    <option value="">как у слоя</option>
                    {(Object.keys(MARKER_TITLES) as MarkerShape[]).map((m) => <option key={m} value={m}>{MARKER_ICONS[m]} {MARKER_TITLES[m]}</option>)}
                  </select>
                )}
                <button type="button" className="dh-cat-name" title={`${c.value} — щелчок выбирает; Shift и Ctrl — несколько`} onClick={(e) => click(c.value, e)}>{c.value}</button>
                <span className="muted small dh-count" title="Число интервалов и их суммарная длина">{c.count.toLocaleString("ru-RU")}{c.length > 0 ? ` · ${Math.round(c.length).toLocaleString("ru-RU")} м` : ""}</span>
              </div>
            );
          })}
        </div>
      </div>
      {!cats.length && <span className="muted small">Нет значений.</span>}
      {note && <span className="muted small">{note}</span>}
    </div>
  );
}
