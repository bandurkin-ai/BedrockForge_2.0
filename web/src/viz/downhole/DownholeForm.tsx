import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  deleteStyle, exportStyles, getIntervalTables, patchStyle, putStyle, type DownholeData, type Hole, type IntervalTableInfo, type StyleRecord,
} from "../../api";
import {
  DEFAULT_STRIPLOG, displayName, fieldRange, FORM_TITLES, LOG_COLORS, newTrack, newTrackId, type DisplayForm, type StripLogSettings, type StyleRef, type Track,
} from "../../charts/stripLog";
import { autoPanelWidth, displayFields, tubeTracks } from "../downholeGeometry";
import { loadDownhole, loadHoles, loadTrajectories } from "../useVizData";
import { ApplyDialog, type ApplyTarget } from "./ApplyDialog";
import { isGrade, type LayerCtx } from "./context";
import { fieldValues } from "./histogram";
import { ImportDialog } from "./ImportDialog";
import { LayerList } from "./LayerList";
import { ColorSection } from "./sections/ColorSection";
import { DataSection } from "./sections/DataSection";
import { FilterSection } from "./sections/FilterSection";
import { LabelSection } from "./sections/LabelSection";
import { ScaleSection } from "./sections/ScaleSection";
import { ViewSection } from "./sections/ViewSection";
import { StyleBar, type StyleLib } from "./StyleBar";
import { StyleGallery } from "./StyleGallery";
import {
  applyLayerStyle, BUILTIN_STYLES, dataKind, defaultStyleFor, layerBody, paletteBody, parseStyleFile, resolveTracks, templateBody,
  STYLE_FILE_EXT, type StyleFile, type StyleRec,
} from "./styles";
import { downloadText, pickFile } from "./ui";

/** Which table and field a new display of a form starts with. */
function firstLayer(form: DisplayForm, tables: IntervalTableInfo[]): Track | null {
  if (form === "event") {
    const t = tables.find((x) => x.kind === "event" && x.columns.length);
    return t ? newTrack(t.key, t.columns[0], 0, true) : null;
  }
  const kind = form === "grade" ? "number" : "text";
  const t = tables.find((x) => x.kind !== "event" && x.columns.some((c) => c.kind === kind));
  const c = t?.columns.find((x) => x.kind === kind);
  if (!t || !c) return null;
  const track = newTrack(t.key, c, 0);
  return form === "grade" ? { ...track, log: isGrade(c.name, c.unit), colorBy: "single" } : { ...track, type: "color_text", form: "tube" };
}

const HISTORY = 100;

/**
 * The form of a display of data along the holes: a list of layers (fields of any tables of the set), the
 * settings of the selected layer in collapsible sections, styles from the project library. Every change is
 * drawn in the view at once; only «Сохранить» keeps it.
 */
export function DownholeForm({ projectId, setId, form, initial, saved, refreshKey, focus, styles: projectStyles, sets, onStyles, onUnlinkCharts, onCreate, onOpenDisplay, onPreview, onSave }: {
  projectId: number;
  setId: number;
  form: DisplayForm;
  initial: StripLogSettings | null;
  /** true: an existing display is being edited (the button saves the changes) */
  saved: boolean;
  refreshKey: number;
  /** a layer to select when the form opens (a click in the legend) */
  focus?: string | null;
  /** the library of the project */
  styles: StyleRecord[];
  sets: { id: number; name: string }[];
  onStyles: (next: StyleRecord[]) => void;
  /** before a style is deleted: the saved displays linked to it get a copy of its settings */
  onUnlinkCharts: (style: StyleRec) => Promise<void>;
  onCreate: (targets: ApplyTarget[]) => Promise<string>;
  onOpenDisplay: (chartId: number) => void;
  onPreview: (settings: StripLogSettings | null) => void;
  /** saves; resolves with the text to show, rejects with the reason */
  onSave: (settings: StripLogSettings, name: string) => Promise<string>;
}) {
  const [tables, setTables] = useState<IntervalTableInfo[] | null>(null);
  const [settings, setSettings] = useState<StripLogSettings | null>(initial ? { ...DEFAULT_STRIPLOG, ...initial, allHoles: initial.allHoles ?? true, form } : null);
  const baseline = useRef<StripLogSettings | null>(initial);
  const [selected, setSelected] = useState<string[]>(() => (focus && initial?.tracks.some((t) => t.id === focus) ? [focus] : initial?.tracks[0] ? [initial.tracks[0].id] : []));
  const [data, setData] = useState<DownholeData | null>(null);
  const [holes, setHoles] = useState<Hole[]>([]);
  const [panelWidth, setPanelWidth] = useState(10);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const [autoNote, setAutoNote] = useState<{ trackId: string; style: string; field: string } | null>(null);
  const [linkEdit, setLinkEdit] = useState<{ trackId: string; styleId: number; level: "layer" | "palette" } | null>(null);
  const [dirty, setDirty] = useState(!saved);
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState<StyleRec | null>(null);
  const [importing, setImporting] = useState<{ file: StyleFile; name: string } | null>(null);
  const [picking, setPicking] = useState<string | null>(null);
  const history = useRef<{ past: StripLogSettings[]; future: StripLogSettings[]; key: string | null; at: number }>({ past: [], future: [], key: null, at: 0 });

  const library = useMemo<StyleRec[]>(() => [...projectStyles, ...BUILTIN_STYLES], [projectStyles]);
  const say = (text: string, error = false) => setNote({ text, error });

  useEffect(() => {
    getIntervalTables(setId).then(setTables).catch((e: Error) => say(e.message, true));
    loadHoles(setId, refreshKey).then(setHoles).catch(() => setHoles([]));
    loadTrajectories(setId, refreshKey).then((t) => setPanelWidth(autoPanelWidth(t.holes))).catch(() => undefined);
  }, [setId, refreshKey]);

  // A new display: the first suitable table and field of the form.
  useEffect(() => {
    if (settings || !tables) return;
    const t = firstLayer(form, tables);
    if (!t) return;
    setSettings({ ...DEFAULT_STRIPLOG, allHoles: true, holes: [], form, tracks: [t] });
    setSelected([t.id]);
  }, [tables]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Changes the settings, as one step of the undo history (quick changes of one control join into one step). */
  const change = useCallback((next: (s: StripLogSettings) => StripLogSettings, key?: string) => {
    setSettings((s) => {
      if (!s) return s;
      const h = history.current;
      const now = Date.now();
      if (!(key && h.key === key && now - h.at < 1500)) {
        h.past.push(s);
        if (h.past.length > HISTORY) h.past.shift();
      }
      h.future = [];
      h.key = key ?? null;
      h.at = now;
      return next(s);
    });
    setDirty(true);
    setNote(null);
  }, []);
  const undo = () => {
    const h = history.current;
    const prev = h.past.pop();
    if (!prev || !settings) return;
    h.future.push(settings);
    h.key = null;
    setSettings(prev);
    setDirty(true);
  };
  const redo = () => {
    const h = history.current;
    const next = h.future.pop();
    if (!next || !settings) return;
    h.past.push(settings);
    h.key = null;
    setSettings(next);
    setDirty(true);
  };

  // What is drawn: links to styles resolved with the library.
  const resolved = useMemo(() => (settings ? resolveTracks(settings, library) : null), [settings, library]);
  useEffect(() => {
    onPreview(resolved);
  }, [resolved]); // eslint-disable-line react-hooks/exhaustive-deps

  // The values of every field the display draws, over the whole set (the histogram, categories, ranges).
  const fieldsKey = JSON.stringify(settings ? displayFields(settings) : {});
  useEffect(() => {
    if (!settings?.tracks.length) return;
    let cancelled = false;
    loadDownhole(setId, "all", JSON.parse(fieldsKey) as Record<string, string[]>, refreshKey)
      .then((d) => !cancelled && setData(d))
      .catch((e: Error) => !cancelled && say(e.message, true));
    return () => {
      cancelled = true;
    };
  }, [setId, fieldsKey, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const tubes = useMemo(() => tubeTracks(resolved?.tracks ?? []), [resolved]);
  const primaryId = selected.find((id) => settings?.tracks.some((t) => t.id === id)) ?? settings?.tracks[0]?.id;
  const primary = resolved?.tracks.find((t) => t.id === primaryId);
  const own = settings?.tracks.find((t) => t.id === primaryId);
  const table = tables?.find((t) => t.key === primary?.table);
  const column = table?.columns.find((c) => c.name === primary?.field);
  const kind = primary ? dataKind(primary, column) : "number";
  const numeric = kind === "number";
  const values = useMemo(() => (data && primary && numeric ? fieldValues(data, primary.table, primary.field) : null), [data, primary?.table, primary?.field, numeric]); // eslint-disable-line react-hooks/exhaustive-deps
  const range = data && primary && numeric ? fieldRange(data, primary.table, primary.field, primary.log) : null;

  /** A change of the selected layers. A linked layer becomes its own copy, and the form offers to change the style instead. */
  const patchSelected = useCallback((p: Partial<Track>, key?: string) => {
    const ids = new Set(selected.length ? selected : primaryId ? [primaryId] : []);
    change((s) => {
      const lib = resolveTracks(s, library);
      return {
        ...s,
        tracks: s.tracks.map((t, i) => {
          if (!ids.has(t.id)) return t;
          const base = t.style?.linked || t.palette?.linked ? lib.tracks[i] : t;
          const colour = "classes" in p || "gradient" in p || "catColors" in p || "colorBy" in p || "hidden" in p || "catOrder" in p;
          let style: StyleRef | undefined = base.style;
          let palette: StyleRef | undefined = "palette" in p ? p.palette : base.palette;
          if (style && !("style" in p)) {
            if (style.linked) setLinkEdit({ trackId: t.id, styleId: style.id, level: "layer" });
            style = { ...style, linked: false, modified: true, auto: false };
          }
          if (palette && colour && !("palette" in p)) {
            if (palette.linked) setLinkEdit({ trackId: t.id, styleId: palette.id, level: "palette" });
            palette = { ...palette, linked: false, modified: true };
          }
          return { ...base, ...p, style: "style" in p ? p.style : style, palette };
        }),
      };
    }, key);
  }, [selected, primaryId, library, change]);

  const patchTrack = (id: string, p: Partial<Track>) => change((s) => ({ ...s, tracks: s.tracks.map((t) => (t.id === id ? { ...t, ...p } : t)) }));

  /** A new layer for a field: the default style of the field when the library has one. */
  const layerFor = (tableKey: string, field: string, index: number): Track | null => {
    const tb = tables?.find((t) => t.key === tableKey);
    const c = tb?.columns.find((x) => x.name === field);
    if (!tb || !c) return null;
    let t = newTrack(tb.key, c, index, tb.kind === "event");
    if (c.kind === "number" && tb.kind !== "event") t = { ...t, log: isGrade(c.name, c.unit), colorBy: "single" };
    const style = defaultStyleFor(c.name, dataKind(t, c), projectStyles as StyleRec[]);
    if (style) {
      t = applyLayerStyle(t, style, false, { auto: true });
      setAutoNote({ trackId: t.id, style: style.name, field: c.name });
    }
    return t;
  };

  const bind = (tableKey: string, field?: string) => {
    const tb = tables?.find((t) => t.key === tableKey);
    if (!tb || !primary) return;
    const c = tb.columns.find((x) => x.name === field) ?? tb.columns.find((x) => x.kind === column?.kind) ?? tb.columns[0];
    if (!c) return;
    const sameKind = (c.kind === "number") === (column?.kind === "number") && (tb.kind === "event") === (table?.kind === "event");
    if (sameKind) {
      // the view and the style stay; what depends on the values (colours of categories) goes
      patchSelected({ table: tb.key, field: c.name, catColors: undefined, hidden: undefined, catOrder: undefined, catMarkers: undefined });
    } else {
      const next = layerFor(tb.key, c.name, settings?.tracks.findIndex((t) => t.id === primary.id) ?? 0);
      if (next) change((s) => ({ ...s, tracks: s.tracks.map((t) => (t.id === primary.id ? { ...next, id: t.id } : t)) }));
    }
  };

  // ---- the library

  const refresh = (next: StyleRecord) => onStyles([...projectStyles.filter((s) => s.id !== next.id), next].sort((a, b) => a.name.localeCompare(b.name)));
  const lib: StyleLib = {
    styles: library,
    say,
    remove: async (style) => {
      await onUnlinkCharts(style);
      await deleteStyle(style.id);
      onStyles(projectStyles.filter((s) => s.id !== style.id));
      // the open form keeps the style's settings as its own
      change((s) => {
        const r = resolveTracks(s, library);
        const drop = (x: StyleRef | undefined) => (x?.id === style.id ? undefined : x);
        return { ...r, template: drop(r.template), tracks: r.tracks.map((t) => ({ ...t, style: drop(t.style), palette: drop(t.palette) })) };
      });
      say(`Стиль «${style.name}» удалён`);
    },
    rename: async (style, name) => {
      const next = await patchStyle(style.id, { name });
      refresh(next);
      change((s) => {
        const ren = (x: StyleRef | undefined) => (x?.id === style.id ? { ...x, name } : x);
        return { ...s, template: ren(s.template), tracks: s.tracks.map((t) => ({ ...t, style: ren(t.style), palette: ren(t.palette) })) };
      });
    },
    setDefaultFor: async (style, patterns) => {
      refresh(await patchStyle(style.id, { default_for: patterns }));
      say(patterns.length ? `«${style.name}» — стиль по умолчанию для: ${patterns.join(", ")}` : `«${style.name}» больше не стиль по умолчанию`);
    },
    exportFile: async (ids) => {
      const file = ids ? await exportStyles(projectId, ids) : await exportStyles(projectId);
      const one = ids?.length === 1 ? projectStyles.find((s) => s.id === ids[0])?.name : null;
      downloadText(`${(one ?? "styles").replace(/[\\/:*?"<>|]+/g, "_")}${STYLE_FILE_EXT}`, JSON.stringify(file, null, 2));
    },
    importFile: () => {
      void pickFile(".json,application/json").then((f) => {
        if (!f) return;
        try {
          setImporting({ file: parseStyleFile(f.text), name: f.name });
        } catch (e) {
          say((e as Error).message, true);
        }
      });
    },
    openDisplay: onOpenDisplay,
  };

  const saveStyle = async (level: "layer" | "palette" | "display", name: string, track?: Track): Promise<StyleRecord> => {
    if (!resolved) throw new Error("Нет настроек");
    const t = track ?? primary;
    const c = t && tables?.find((x) => x.key === t.table)?.columns.find((x) => x.name === t.field);
    const body = level === "display" ? templateBody(resolved) : level === "layer" ? layerBody(t as Track) : paletteBody(t as Track, c?.kind === "number" && t?.type !== "event");
    if (!body) throw new Error("У слоя один цвет: сохранять нечего");
    const existing = projectStyles.find((s) => s.level === level && s.name === name);
    const rec = await putStyle(projectId, {
      name, level, data_kind: level === "display" ? "mixed" : level === "palette" ? (c?.kind === "number" ? "number" : "text") : dataKind(t as Track, c),
      body: body as unknown as Record<string, unknown>, default_for: existing?.default_for ?? [],
    });
    refresh(rec);
    return rec;
  };
  const setRef = (level: "layer" | "palette", trackId: string, ref: StyleRef | undefined) =>
    change((s) => ({ ...s, tracks: s.tracks.map((t) => (t.id === trackId ? { ...t, [level === "layer" ? "style" : "palette"]: ref } : t)) }));

  /** Writes the settings of the layer into its style: every display linked to it changes. */
  const updateStyle = async (level: "layer" | "palette", trackId: string) => {
    const t = resolved?.tracks.find((x) => x.id === trackId);
    const ref = level === "layer" ? t?.style : t?.palette;
    const style = ref && projectStyles.find((s) => s.id === ref.id);
    if (!t || !style) throw new Error("Нет стиля-источника в библиотеке");
    const c = tables?.find((x) => x.key === t.table)?.columns.find((x) => x.name === t.field);
    const body = level === "layer" ? layerBody(t) : paletteBody(t, c?.kind === "number");
    if (!body) throw new Error("Сохранять нечего");
    refresh(await patchStyle(style.id, { body: body as unknown as Record<string, unknown> }));
    setRef(level, trackId, { id: style.id, name: style.name, linked: true });
    setLinkEdit(null);
    say(`Стиль «${style.name}» обновлён; связанные отображения перестроены`);
  };

  const menu = (id: string, action: "duplicate" | "saveStyle" | "applyStyle" | "applyTo" | "remove") => {
    const t = resolved?.tracks.find((x) => x.id === id);
    if (!t) return;
    if (action === "duplicate") {
      const copy = { ...(settings?.tracks.find((x) => x.id === id) as Track), id: newTrackId() };
      change((s) => {
        const i = s.tracks.findIndex((x) => x.id === id);
        return { ...s, tracks: [...s.tracks.slice(0, i + 1), copy, ...s.tracks.slice(i + 1)] };
      });
      setSelected([copy.id]);
    } else if (action === "remove") {
      change((s) => ({ ...s, tracks: s.tracks.filter((x) => x.id !== id) }));
      setSelected((sel) => sel.filter((x) => x !== id));
    } else if (action === "saveStyle") {
      const name = window.prompt("Стиль слоя: название", t.style?.name ?? `${t.field}`)?.trim();
      if (!name) return;
      saveStyle("layer", name, t).then((rec) => { setRef("layer", id, { id: rec.id, name: rec.name, linked: false }); say(`Стиль «${rec.name}» сохранён в библиотеке проекта`); }).catch((e: Error) => say(e.message, true));
    } else if (action === "applyStyle") {
      setSelected([id]);
      setPicking(id);
    } else {
      const source = t.style ? library.find((s) => s.id === t.style?.id) : undefined;
      if (source) setApplying(source);
      else {
        const name = window.prompt("Чтобы применить настройки к другим полям, сохраните их как стиль. Название:", t.field)?.trim();
        if (name) saveStyle("layer", name, t).then((rec) => { setRef("layer", id, { id: rec.id, name: rec.name, linked: false }); setApplying(rec as StyleRec); }).catch((e: Error) => say(e.message, true));
      }
    }
  };

  const arrange = () => change((s) => {
    const tubesNow = tubeTracks(s.tracks);
    let k = 0;
    return {
      ...s,
      tracks: s.tracks.map((t) => {
        if (tubesNow.has(t.id) || t.type === "event") return { ...t, offsetM: undefined };
        const side = k++ % 2 === 0 ? "right" : "left";
        return { ...t, offsetM: undefined, split: null, side };
      }),
    };
  });

  // Ctrl+Z, Ctrl+Y inside the form (not while typing in a text field: the browser undoes the text there).
  const onKey = (e: React.KeyboardEvent) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    const typing = (e.target as HTMLElement).matches("input:not([type=checkbox]):not([type=range]):not([type=color]), textarea");
    if (typing) return;
    const k = e.key.toLowerCase();
    if ((k === "z" && !e.shiftKey) || k === "я") {
      e.preventDefault();
      undo();
    } else if (k === "y" || (k === "z" && e.shiftKey) || k === "н") {
      e.preventDefault();
      redo();
    }
  };

  if (!tables) return <p className="muted small form-note">{note?.error ? note.text : "Загрузка таблиц…"}</p>;
  if (!settings) {
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

  const ctx: LayerCtx | null = primary ? {
    track: primary, table, column, tables, numeric, kind, values, range, data, tube: tubes.has(primary.id), panelWidth, patch: patchSelected,
  } : null;
  const used = [...new Set((resolved?.tracks ?? []).map((t) => t.color))];
  const linkStyle = linkEdit && library.find((s) => s.id === linkEdit.styleId);
  const linkedCount = linkEdit ? 1 : 0;
  const templates = library.filter((s) => s.level === "display");
  const layerStyles = library.filter((s) => s.level === "layer" && (s.data_kind === kind || s.data_kind === "mixed"));

  return (
    <form className="downhole-form dh-form" onSubmit={(e) => e.preventDefault()} onKeyDown={onKey}>
      <StyleBar
        level="display" lib={lib} options={templates} current={settings.template}
        onApply={(s) => {
          const body = s.body as { tracks?: Track[] };
          const fields = (body.tracks ?? []).map((x) => String(x.field).toUpperCase());
          const tb = tables.find((x) => x.columns.some((c) => fields.includes(c.name.toUpperCase()))) ?? table;
          setApplying(null);
          if (!tb) return say("В наборе нет таблиц с полями шаблона", true);
          change((cur) => {
            const tracks = (body.tracks ?? []).map((x) => {
              const c = tb.columns.find((y) => y.name.toUpperCase() === String(x.field).toUpperCase());
              const { field: _f, ...rest } = x as Track;
              return c ? { ...newTrack(tb.key, c, 0), ...rest, id: newTrackId(), table: tb.key, field: c.name } : null;
            }).filter((x): x is Track => !!x);
            setSelected(tracks[0] ? [tracks[0].id] : []);
            return { ...cur, tracks, template: { id: s.id, name: s.name, linked: false } };
          });
          say(`Шаблон «${s.name}» применён к таблице «${tb.name}»`);
        }}
        onSaveAs={async (name) => {
          const rec = await saveStyle("display", name);
          change((s) => ({ ...s, template: { id: rec.id, name: rec.name, linked: false } }));
          say(`Шаблон «${rec.name}» сохранён`);
        }}
        onUpdate={async () => {
          const ref = settings.template;
          const style = ref && projectStyles.find((s) => s.id === ref.id);
          if (!style || !resolved) throw new Error("Нет шаблона-источника");
          refresh(await patchStyle(style.id, { body: templateBody(resolved) as unknown as Record<string, unknown> }));
          change((s) => ({ ...s, template: { id: style.id, name: style.name, linked: ref.linked } }));
          say(`Шаблон «${style.name}» обновлён`);
        }}
        onLink={(linked) => settings.template && change((s) => ({ ...s, template: s.template && { ...s.template, linked, modified: false } }))}
        onApplyTo={(s) => setApplying(s)}
        onClear={() => change((s) => ({ ...s, template: undefined }))}
      />

      <LayerList
        tracks={resolved?.tracks ?? []} tubes={tubes} tables={tables} selected={selected}
        onSelect={setSelected} onOrder={(ids) => change((s) => ({ ...s, tracks: ids.map((id) => s.tracks.find((t) => t.id === id) as Track) }))}
        onPatch={patchTrack}
        onAdd={(fields) => {
          const added = fields.map((f, i) => layerFor(f.table, f.field, settings.tracks.length + i)).filter((t): t is Track => !!t)
            .map((t, i) => ({ ...t, color: t.style ? t.color : LOG_COLORS[(settings.tracks.length + i) % LOG_COLORS.length] }));
          change((s) => ({ ...s, tracks: [...s.tracks, ...added] }));
          if (added.length) setSelected([added[added.length - 1].id]);
        }}
        onMenu={menu} onArrange={arrange}
      />

      {picking && (
        <div className="dh-dialog" role="dialog" aria-label="Применить стиль к слою">
          <div className="dh-usage-head"><strong className="grow">Стиль для слоя</strong><button type="button" className="icon" aria-label="Закрыть" onClick={() => setPicking(null)}>✕</button></div>
          <StyleGallery styles={layerStyles} onPick={(s) => { change((cur) => ({ ...cur, tracks: cur.tracks.map((t) => (t.id === picking ? applyLayerStyle(t, s) : t)) })); setPicking(null); }} />
        </div>
      )}
      {applying && <ApplyDialog style={applying} sets={sets} base={settings} onCreate={onCreate} onClose={() => setApplying(null)} />}
      {importing && (
        <ImportDialog projectId={projectId} file={importing.file} fileName={importing.name} styles={library}
          onDone={(r) => onStyles(r.styles)} onClose={() => setImporting(null)} />
      )}

      {ctx && own && (
        <div className="dh-sections">
          {selected.length > 1 && <div className="dh-banner">Выбрано слоёв: {selected.length}. Правка применяется ко всем выбранным.</div>}
          {autoNote && autoNote.trackId === primary?.id && primary?.style?.auto && (
            <div className="dh-banner">
              Применён стиль «{autoNote.style}» (по умолчанию для {autoNote.field}).
              <button type="button" className="small-btn" onClick={() => {
                const tb = tables.find((x) => x.key === primary.table);
                const c = tb?.columns.find((x) => x.name === primary.field);
                if (tb && c) change((s) => ({ ...s, tracks: s.tracks.map((t) => (t.id === primary.id ? { ...newTrack(tb.key, c, 0, tb.kind === "event"), id: t.id, log: isGrade(c.name, c.unit) } : t)) }));
                setAutoNote(null);
              }}>сбросить</button>
            </div>
          )}
          {(own.style?.linked || own.palette?.linked) && !linkEdit && (
            <div className="dh-banner link">🔗 Связан со {own.style?.linked ? `стилем «${own.style.name}»` : `раскраской «${own.palette?.name}»`}: правка изменит только этот слой, стиль можно будет обновить.</div>
          )}
          {linkEdit && linkEdit.trackId === primary?.id && linkStyle && (
            <div className="dh-banner warn">
              Был связан со стилем «{linkStyle.name}»: изменения пока только здесь.
              <button type="button" className="small-btn" title="Записать изменения в стиль: все связанные отображения перестроятся" onClick={() => void updateStyle(linkEdit.level, linkEdit.trackId).catch((e: Error) => say(e.message, true))}>Изменить стиль{linkedCount ? "" : ""}</button>
              <button type="button" className="small-btn" title="Оставить изменения только в этом слое (копия)" onClick={() => setLinkEdit(null)}>Только здесь</button>
            </div>
          )}
          {selected.length <= 1 && (
            <StyleBar
              level="layer" lib={lib} options={layerStyles} current={own.style} fieldName={primary?.field}
              onApply={(s) => change((cur) => ({ ...cur, tracks: cur.tracks.map((t) => (selected.includes(t.id) || t.id === primary?.id ? applyLayerStyle(t, s) : t)) }))}
              onSaveAs={async (name) => {
                const rec = await saveStyle("layer", name);
                setRef("layer", (primary as Track).id, { id: rec.id, name: rec.name, linked: false });
                say(`Стиль «${rec.name}» сохранён в библиотеке проекта`);
              }}
              onUpdate={() => updateStyle("layer", (primary as Track).id)}
              onLink={(linked) => own.style && setRef("layer", own.id, { ...own.style, linked, modified: false, auto: false })}
              onApplyTo={(s) => setApplying(s)}
              onClear={() => setRef("layer", own.id, undefined)}
            />
          )}
          <DataSection ctx={ctx} settings={settings} holes={holes} single={selected.length <= 1} onBind={bind}
            onSettings={(p, key) => change((s) => ({ ...s, ...p }), key)} />
          <ViewSection ctx={ctx} tubePx={settings.tube ?? 8} onTube={(px) => change((s) => ({ ...s, tube: px }), "tube")} />
          {numeric && <ScaleSection ctx={ctx} />}
          <ColorSection
            ctx={ctx} lib={lib} used={used}
            onSavePalette={async (name) => {
              const rec = await saveStyle("palette", name);
              setRef("palette", (primary as Track).id, { id: rec.id, name: rec.name, linked: false });
              say(`Раскраска «${rec.name}» сохранена в проекте`);
            }}
            onUpdatePalette={() => updateStyle("palette", (primary as Track).id)}
          />
          {kind !== "event" && <FilterSection ctx={ctx} />}
          <LabelSection ctx={ctx} displayLabels={settings.labels} />
        </div>
      )}

      <div className="form-actions dh-actions">
        <button type="button" className="primary" disabled={saving || (saved && !dirty) || !settings.tracks.length} onClick={() => {
          setSaving(true);
          setNote(null);
          const clean = { ...settings, tracks: settings.tracks.map((t) => (t.style?.auto ? { ...t, style: { ...t.style, auto: undefined } } : t)) };
          onSave(clean, displayName(clean, table?.name))
            .then((text) => {
              baseline.current = clean;
              setDirty(false);
              say(text);
            })
            .catch((e: Error) => say(`Не сохранено: ${e.message}${/Input should be|downhole/i.test(e.message) ? ". Сервер (контейнер api) старой версии: запустите BedrockForge.bat, он пересоберёт api" : ""}`, true))
            .finally(() => setSaving(false));
        }}>{saving ? "Сохранение…" : "Сохранить"}</button>
        <button type="button" disabled={!dirty || !baseline.current} title="Вернуть последнее сохранённое состояние" onClick={() => {
          if (!baseline.current) return;
          change(() => ({ ...DEFAULT_STRIPLOG, ...(baseline.current as StripLogSettings) }));
          setDirty(false);
          setLinkEdit(null);
        }}>Отменить изменения</button>
        <span className="grow" />
        <button type="button" className="icon" title="Отменить (Ctrl+Z)" aria-label="Отменить" disabled={!history.current.past.length} onClick={undo}>↶</button>
        <button type="button" className="icon" title="Повторить (Ctrl+Y)" aria-label="Повторить" disabled={!history.current.future.length} onClick={redo}>↷</button>
        <span className={`small dh-note ${note?.error ? "form-error" : "muted"}`}>
          {note?.text ?? (saved ? (dirty ? "Изменения видны на скважинах; сохранятся по кнопке." : "Сохранено.") : `${FORM_TITLES[form]}: видно на скважинах; в дерево попадёт по кнопке.`)}
        </span>
      </div>
    </form>
  );
}
