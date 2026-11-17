import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DEFAULT_STRIPLOG, FORM_TITLES, LOG_COLORS, fieldRange, newTrack, newTrackId, withDefaults, type DisplayForm, type StripLogSettings, type StyleRef, type Track } from "../../charts/striplog/model";
import { askText } from "../../shell/dialogs";
import { useStore } from "../../shell/useStore";
import { isGrade, type LayerCtx } from "../../viz/downhole/context";
import { displayFields, tubeTracks } from "../../viz/downhole/geometry";
import { fieldValues } from "../../viz/downhole/histogram";
import { loadDownholeAll, loadManifest, type Manifest } from "../../viz/downhole/parts";
import { applyLayerStyle, BUILTIN_STYLES, dataKind, resolveTracks, unlinkStyle, type StyleLevel, type StyleRec } from "../../viz/downhole/styles";
import type { DownholeData, IntervalTableInfo } from "../../viz/downhole/types";
import { ApplyDialog } from "./ApplyDialog";
import { arrange, cleanForSave, firstLayer, layerFor, mapRefs, sameSettings, templateLayers } from "./formModel";
import { EditHistory, historyKey } from "./history";
import { ImportDialog, type StyleFileToImport } from "./ImportDialog";
import { LayerList } from "./LayerList";
import { deleteStyle, exportStyles, libraryStore, patchStyle, readStyleFile, styleUsage, type ChartRow, type StyleRow } from "./library";
import { createDisplays, saveDisplay } from "./saving";
import { ColorSection } from "./sections/ColorSection";
import { DataSection } from "./sections/DataSection";
import { FilterSection } from "./sections/FilterSection";
import { LabelSection } from "./sections/LabelSection";
import { ScaleSection } from "./sections/ScaleSection";
import { ViewSection } from "./sections/ViewSection";
import { errText, needRow, putStyle, rowOf, styleBody } from "./styleActions";
import { StyleBar, type StyleLib } from "./StyleBar";
import { StyleGallery } from "./StyleGallery";
import { formTables, holeNames, panelWidthOf } from "./tables";
import "./downhole.css";

export interface DownholeFormProps {
  setId: number;
  form: DisplayForm;
  /** the saved display being edited; null — a new one (only «Сохранить» puts it in the tree) */
  chart: ChartRow | null;
  /** the field a new display starts with (holes.show_data) */
  hint?: { table?: string | null; field?: string | null };
  /** a layer to select when the form opens (a click in the legend) */
  focus?: string | null;
  /** the data of the set changed: tables and values are read again */
  refreshKey?: number;
  sets: { id: number; name: string }[];
  /** what the view draws while the form is open: the draft with links resolved (null — nothing yet) */
  onPreview: (settings: StripLogSettings | null) => void;
  /** «Сохранить» created or changed the display */
  onSaved: (chart: ChartRow) => void;
  onOpenDisplay: (chartId: number) => void;
  /** the form differs from the saved display (the window asks before it closes) */
  onDirty?: (dirty: boolean) => void;
}

const EMPTY_NOTES: Record<DisplayForm, string> = {
  event: "В наборе нет таблиц событий. Импортируйте файл (скважина, глубина, поля) в мастере скважин с типом «События по скважинам».",
  lith: "В наборе нет интервальных таблиц с текстовыми полями. Импортируйте литологию в мастере скважин с типом «Интервальная таблица».",
  grade: "В наборе нет таблиц с числовыми полями: импортируйте пробы.",
};

/**
 * The form of a display of data along the holes (ТЗ 7.16): a list of layers (fields of any tables of the set),
 * the settings of the selected layer in collapsible sections, styles from the project library. Every change
 * is drawn in the view at once as a draft; only «Сохранить» keeps it.
 */
export function DownholeForm({ setId, form, chart, hint, focus, refreshKey = 0, sets, onPreview, onSaved, onOpenDisplay, onDirty }: DownholeFormProps) {
  const [initial] = useState<StripLogSettings | null>(() => (chart ? { ...withDefaults(chart.settings), form } : null));
  const [tables, setTables] = useState<IntervalTableInfo[] | null>(null);
  const [settings, setSettingsState] = useState<StripLogSettings | null>(initial ? { ...initial, allHoles: initial.allHoles ?? true } : null);
  const current = useRef<StripLogSettings | null>(settings);
  const [baseline, setBaseline] = useState<StripLogSettings | null>(initial ? cleanForSave(current.current as StripLogSettings) : null);
  const [selected, setSelected] = useState<string[]>(() => (focus && initial?.tracks.some((t) => t.id === focus) ? [focus] : initial?.tracks[0] ? [initial.tracks[0].id] : []));
  const [data, setData] = useState<DownholeData | null>(null);
  const [holes, setHoles] = useState<{ hole_id: string }[]>([]);
  const [panelWidth, setPanelWidth] = useState(10);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const [autoNote, setAutoNote] = useState<{ trackId: string; style: string; field: string } | null>(null);
  const [linkEdit, setLinkEdit] = useState<{ trackId: string; styleUid: string; level: "layer" | "palette" } | null>(null);
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState<StyleRec | null>(null);
  const [importing, setImporting] = useState<StyleFileToImport | null>(null);
  const [picking, setPicking] = useState<string | null>(null);
  const history = useRef(new EditHistory<StripLogSettings>());
  const manifest = useRef<Promise<Manifest> | null>(null);

  const rows = useStore(libraryStore, (s) => s.styles);
  const library = useMemo<StyleRec[]>(() => [...rows, ...BUILTIN_STYLES], [rows]);
  const say = (text: string, error = false) => setNote({ text, error });
  const run = (p: Promise<unknown>) => void p.catch((e: unknown) => say(errText(e), true));

  const setSettings = (next: StripLogSettings) => {
    current.current = next;
    setSettingsState(next);
  };

  useEffect(() => {
    formTables(setId).then(setTables).catch((e: unknown) => say(errText(e), true));
    holeNames(setId).then(setHoles).catch(() => setHoles([]));
    panelWidthOf(setId).then(setPanelWidth).catch(() => undefined);
    manifest.current = null;
  }, [setId, refreshKey]);

  // A new display: the asked field, else the first suitable table and field of the form.
  useEffect(() => {
    if (current.current || !tables) return;
    const l = firstLayer(form, tables, rows, hint);
    if (!l) {
      if (hint?.field) say(`Поля «${hint.field}» нет в таблицах набора`, true);
      return;
    }
    setSettings({ ...DEFAULT_STRIPLOG, allHoles: true, holes: [], form, tracks: [l.track] });
    setSelected([l.track.id]);
    if (l.auto) setAutoNote({ trackId: l.track.id, style: l.auto.name, field: l.track.field });
  }, [tables]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Changes the settings as one step of the undo history (quick changes of one control join into one step). */
  const change = useCallback((next: (s: StripLogSettings) => StripLogSettings, key?: string) => {
    const s = current.current;
    if (!s) return;
    history.current.record(s, key);
    setSettings(next(s));
    setNote(null);
  }, []);
  const step = (dir: "undo" | "redo") => {
    const s = current.current;
    if (!s) return;
    const other = dir === "undo" ? history.current.undo(s) : history.current.redo(s);
    if (other) setSettings(other);
  };

  const dirty = useMemo(() => !!settings && !sameSettings(cleanForSave(settings), baseline), [settings, baseline]);
  useEffect(() => onDirty?.(dirty), [dirty]); // eslint-disable-line react-hooks/exhaustive-deps

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
    manifest.current ??= loadManifest(setId);
    manifest.current
      .then((m) => loadDownholeAll(setId, JSON.parse(fieldsKey) as Record<string, string[]>, m))
      .then((d) => !cancelled && setData(d))
      .catch((e: unknown) => {
        manifest.current = null;
        if (!cancelled) say(errText(e), true);
      });
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
    let edit: { trackId: string; styleUid: string; level: "layer" | "palette" } | null = null;
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
            if (style.linked) edit = { trackId: t.id, styleUid: style.uid, level: "layer" };
            style = { ...style, linked: false, modified: true, auto: false };
          }
          if (palette && colour && !("palette" in p)) {
            if (palette.linked) edit = { trackId: t.id, styleUid: palette.uid, level: "palette" };
            palette = { ...palette, linked: false, modified: true };
          }
          return { ...base, ...p, style: "style" in p ? p.style : style, palette };
        }),
      };
    }, key);
    if (edit) setLinkEdit(edit);
  }, [selected, primaryId, library, change]);

  const patchTrack = (id: string, p: Partial<Track>) => change((s) => ({ ...s, tracks: s.tracks.map((t) => (t.id === id ? { ...t, ...p } : t)) }));

  /** A new layer for a field: the default style of the field when the library has one. */
  const newLayer = (tableKey: string, field: string, index: number): Track | null => {
    const l = tables ? layerFor(tables, tableKey, field, index, rows) : null;
    if (l?.auto) setAutoNote({ trackId: l.track.id, style: l.auto.name, field: l.track.field });
    return l?.track ?? null;
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
      const next = newLayer(tb.key, c.name, settings?.tracks.findIndex((t) => t.id === primary.id) ?? 0);
      if (next) change((s) => ({ ...s, tracks: s.tracks.map((t) => (t.id === primary.id ? { ...next, id: t.id } : t)) }));
    }
  };

  // ---- the library

  const lib: StyleLib = {
    styles: library,
    say,
    remove: async (style) => {
      const before = library;
      await deleteStyle(needRow(style)); // the core leaves a copy of the style in the saved displays
      // the open form keeps the style's settings as its own, the saved state too
      change((s) => unlinkStyle(s, style.uid, before));
      setBaseline((b) => b && unlinkStyle(b, style.uid, before));
      say(`Стиль «${style.name}» удалён; слои сохранили его настройки`);
    },
    rename: async (style, name) => {
      await patchStyle(needRow(style), { name });
      change((s) => mapRefs(s, (x) => (x?.uid === style.uid ? { ...x, name } : x)));
    },
    setDefaultFor: async (style, patterns) => {
      await patchStyle(needRow(style), { default_for: patterns });
      say(patterns.length ? `«${style.name}» — стиль по умолчанию для: ${patterns.join(", ")}` : `«${style.name}» больше не стиль по умолчанию`);
    },
    usage: (style) => styleUsage(needRow(style)),
    exportFile: async (styles) => {
      const ids = styles ? styles.filter((s) => !s.builtin).map((s) => s.id) : null;
      if (!ids && !rows.length) throw new Error("В библиотеке проекта нет стилей: сохраните стиль, затем экспортируйте");
      const path = await exportStyles(ids);
      if (path) say(`Стили сохранены: ${path}`);
    },
    importFile: () => run(readStyleFile().then((f) => f && setImporting(f))),
    openDisplay: onOpenDisplay,
  };

  const saveStyle = async (level: StyleLevel, name: string, track?: Track): Promise<StyleRow> => {
    if (!resolved || !tables) throw new Error("Нет настроек");
    const { body, kind: k } = styleBody(level, resolved, track ?? primary, tables);
    return putStyle(name, level, k, body);
  };
  const setRef = (level: "layer" | "palette", trackId: string, ref: StyleRef | undefined) =>
    change((s) => ({ ...s, tracks: s.tracks.map((t) => (t.id === trackId ? { ...t, [level === "layer" ? "style" : "palette"]: ref } : t)) }));

  /** Writes the settings of the layer into its style: every display linked to it changes. */
  const updateStyle = async (level: "layer" | "palette", trackId: string) => {
    const t = resolved?.tracks.find((x) => x.id === trackId);
    const style = rowOf(level === "layer" ? t?.style?.uid : t?.palette?.uid);
    if (!t || !style || !tables) throw new Error("Нет стиля-источника в библиотеке");
    await patchStyle(style, { body: styleBody(level, resolved as StripLogSettings, t, tables).body });
    setRef(level, trackId, { uid: style.uid, name: style.name, linked: true });
    setLinkEdit(null);
    say(`Стиль «${style.name}» обновлён; связанные отображения перестроены`);
  };

  const menu = async (id: string, action: "duplicate" | "saveStyle" | "applyStyle" | "applyTo" | "remove") => {
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
      const name = await askText({ title: "Сохранить как стиль слоя", label: "Название в библиотеке проекта", initial: t.style?.name ?? t.field, okLabel: "Сохранить" });
      if (!name) return;
      run(saveStyle("layer", name, t).then((rec) => { setRef("layer", id, { uid: rec.uid, name: rec.name, linked: false }); say(`Стиль «${rec.name}» сохранён в библиотеке проекта`); }));
    } else if (action === "applyStyle") {
      setSelected([id]);
      setPicking(id);
    } else {
      const source = t.style ? library.find((s) => s.uid === t.style?.uid) : undefined;
      if (source) setApplying(source);
      else {
        const name = await askText({ title: "Применить к другим полям", label: "Чтобы применить настройки к другим полям, сохраните их как стиль. Название:", initial: t.field, okLabel: "Сохранить" });
        if (name) run(saveStyle("layer", name, t).then((rec) => { setRef("layer", id, { uid: rec.uid, name: rec.name, linked: false }); setApplying(rec); }));
      }
    }
  };

  // Ctrl+Z, Ctrl+Y inside the form (not while typing in a text field: the browser undoes the text there).
  const onKey = (e: React.KeyboardEvent) => {
    const k = historyKey(e);
    if (!k || (e.target as HTMLElement).matches("input:not([type=checkbox]):not([type=range]):not([type=color]), textarea")) return;
    e.preventDefault();
    step(k);
  };

  const save = () => {
    if (!settings) return;
    setSaving(true);
    setNote(null);
    const clean = cleanForSave(settings);
    saveDisplay(setId, clean, chart, table?.name)
      .then(({ chart: c, text }) => {
        setBaseline(clean);
        say(text);
        onSaved(c);
      })
      .catch((e: unknown) => say(`Не сохранено: ${errText(e)}`, true))
      .finally(() => setSaving(false));
  };

  if (!tables) return <div className="dh-root"><p className={`small form-note ${note?.error ? "form-error" : "muted"}`}>{note?.error ? note.text : "Загрузка таблиц…"}</p></div>;
  if (!settings) return <div className="dh-root"><p className="muted small form-note">{note?.text ?? EMPTY_NOTES[form]}</p></div>;

  const ctx: LayerCtx | null = primary ? {
    track: primary, table, column, tables, numeric, kind, values, range, data, tube: tubes.has(primary.id), panelWidth, patch: patchSelected,
  } : null;
  const used = [...new Set((resolved?.tracks ?? []).map((t) => t.color))];
  const linkStyle = linkEdit && library.find((s) => s.uid === linkEdit.styleUid);
  const templates = library.filter((s) => s.level === "display");
  const layerStyles = library.filter((s) => s.level === "layer" && (s.data_kind === kind || s.data_kind === "mixed"));

  return (
    <form className="downhole-form dh-form dh-root" aria-label={`Отображение: ${FORM_TITLES[form]}`} onSubmit={(e) => e.preventDefault()} onKeyDown={onKey}>
      <StyleBar
        level="display" lib={lib} options={templates} current={settings.template}
        onApply={(s) => {
          setApplying(null);
          const r = templateLayers(s, tables, table);
          if (!r) return say("В наборе нет таблиц с полями шаблона", true);
          change((cur) => ({ ...cur, tracks: r.tracks, template: { uid: s.uid, name: s.name, linked: false } }));
          setSelected(r.tracks[0] ? [r.tracks[0].id] : []);
          say(`Шаблон «${s.name}» применён к таблице «${r.table.name}»`);
        }}
        onSaveAs={async (name) => {
          const rec = await saveStyle("display", name);
          change((s) => ({ ...s, template: { uid: rec.uid, name: rec.name, linked: false } }));
          say(`Шаблон «${rec.name}» сохранён`);
        }}
        onUpdate={async () => {
          const ref = settings.template;
          const style = rowOf(ref?.uid);
          if (!ref || !style || !resolved) throw new Error("Нет шаблона-источника");
          await patchStyle(style, { body: styleBody("display", resolved, undefined, tables).body });
          change((s) => ({ ...s, template: { uid: style.uid, name: style.name, linked: ref.linked } }));
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
          const added = fields.map((f, i) => newLayer(f.table, f.field, settings.tracks.length + i)).filter((t): t is Track => !!t)
            .map((t, i) => ({ ...t, color: t.style ? t.color : LOG_COLORS[(settings.tracks.length + i) % LOG_COLORS.length] }));
          change((s) => ({ ...s, tracks: [...s.tracks, ...added] }));
          if (added.length) setSelected([added[added.length - 1].id]);
        }}
        onMenu={(id, a) => void menu(id, a)} onArrange={() => change(arrange)}
      />

      {picking && (
        <div className="dh-dialog" role="dialog" aria-label="Применить стиль к слою">
          <div className="dh-usage-head"><strong className="grow">Стиль для слоя</strong><button type="button" className="icon" aria-label="Закрыть выбор стиля" onClick={() => setPicking(null)}>✕</button></div>
          <StyleGallery styles={layerStyles} onPick={(s) => { change((cur) => ({ ...cur, tracks: cur.tracks.map((t) => (t.id === picking ? applyLayerStyle(t, s) : t)) })); setPicking(null); }} />
        </div>
      )}
      {applying && <ApplyDialog style={applying} sets={sets} base={settings} onCreate={createDisplays} onClose={() => setApplying(null)} />}
      {importing && <ImportDialog source={importing} onDone={(r) => say(`Импортировано стилей: ${r.created + r.replaced}`)} onClose={() => setImporting(null)} />}

      {ctx && own && (
        <div className="dh-sections">
          {selected.length > 1 && <div className="dh-banner">Выбрано слоёв: {selected.length}. Правка применяется ко всем выбранным.</div>}
          {autoNote && autoNote.trackId === primary?.id && primary?.style?.auto && (
            <div className="dh-banner">
              Применён стиль «{autoNote.style}» (по умолчанию для {autoNote.field}).
              <button type="button" className="small-btn" onClick={() => {
                const c = table?.columns.find((x) => x.name === primary.field);
                if (table && c) change((s) => ({ ...s, tracks: s.tracks.map((t) => (t.id === primary.id ? { ...newTrack(table.key, c, 0, table.kind === "event"), id: t.id, log: isGrade(c.name, c.unit) } : t)) }));
                setAutoNote(null);
              }}>сбросить</button>
            </div>
          )}
          {(own.style?.linked || own.palette?.linked) && !linkEdit && (
            <div className="dh-banner link">🔗 Связан со {own.style?.linked ? `стилем «${own.style.name}»` : `раскраской «${own.palette?.name}»`}: правка изменит только этот слой, стиль можно будет обновить.</div>
          )}
          {linkEdit && linkEdit.trackId === primary?.id && linkStyle && (
            <div className="dh-banner warn" role="status">
              Был связан со стилем «{linkStyle.name}»: изменения пока только здесь.
              <button type="button" className="small-btn" title="Записать изменения в стиль: все связанные отображения перестроятся" onClick={() => run(updateStyle(linkEdit.level, linkEdit.trackId))}>Изменить стиль</button>
              <button type="button" className="small-btn" title="Оставить изменения только в этом слое (копия)" onClick={() => setLinkEdit(null)}>Только здесь</button>
            </div>
          )}
          {selected.length <= 1 && (
            <StyleBar
              level="layer" lib={lib} options={layerStyles} current={own.style} fieldName={primary?.field}
              onApply={(s) => change((cur) => ({ ...cur, tracks: cur.tracks.map((t) => (selected.includes(t.id) || t.id === primary?.id ? applyLayerStyle(t, s) : t)) }))}
              onSaveAs={async (name) => {
                const rec = await saveStyle("layer", name);
                setRef("layer", (primary as Track).id, { uid: rec.uid, name: rec.name, linked: false });
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
              setRef("palette", (primary as Track).id, { uid: rec.uid, name: rec.name, linked: false });
              say(`Раскраска «${rec.name}» сохранена в проекте`);
            }}
            onUpdatePalette={() => updateStyle("palette", (primary as Track).id)}
          />
          {kind !== "event" && <FilterSection ctx={ctx} />}
          <LabelSection ctx={ctx} displayLabels={settings.labels} />
        </div>
      )}

      <div className="form-actions dh-actions">
        <button type="button" className="primary" disabled={saving || (!!chart && !dirty) || !settings.tracks.length} onClick={save}>{saving ? "Сохранение…" : "Сохранить"}</button>
        <button type="button" disabled={!dirty || !baseline} title="Вернуть последнее сохранённое состояние" onClick={() => {
          if (!baseline) return;
          change(() => baseline);
          setLinkEdit(null);
        }}>Отменить изменения</button>
        <span className="grow" />
        <button type="button" className="icon" title="Отменить (Ctrl+Z)" aria-label="Отменить" disabled={!history.current.canUndo} onClick={() => step("undo")}>↶</button>
        <button type="button" className="icon" title="Повторить (Ctrl+Y)" aria-label="Повторить" disabled={!history.current.canRedo} onClick={() => step("redo")}>↷</button>
        <span className={`small dh-note ${note?.error ? "form-error" : "muted"}`} role="status">
          {note?.text ?? (chart ? (dirty ? "Изменения видны на скважинах; сохранятся по кнопке." : "Сохранено.") : `${FORM_TITLES[form]}: видно на скважинах; в дерево попадёт по кнопке.`)}
        </span>
      </div>
    </form>
  );
}
