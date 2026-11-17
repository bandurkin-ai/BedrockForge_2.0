import { useEffect, useMemo, useState } from "react";
import type { IntervalTableInfo } from "../../viz/downhole/types";
import type { ApplyTarget } from "./saving";
import { formTables } from "./tables";
import { newTrack, newTrackId, type StripLogSettings } from "../../charts/striplog/model";
import { applyLayerStyle, applyTemplate, dataKind, matchFields, type FieldMatch, type StyleRec, type TemplateBody } from "../../viz/downhole/styles";

/**
 * «Применить к…»: a layer style to fields of other tables and sets, or a display template to other tables.
 * Each choice makes a new display; the style goes as a copy or linked.
 */
export function ApplyDialog({ style, sets, base, onCreate, onClose }: {
  style: StyleRec;
  sets: { id: number; name: string }[];
  /** settings of the current display: the holes, depths, tube */
  base: StripLogSettings;
  onCreate: (targets: ApplyTarget[]) => Promise<string>;
  onClose: () => void;
}) {
  const [tables, setTables] = useState<Record<number, IntervalTableInfo[]>>({});
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [linked, setLinked] = useState(false);
  const [maps, setMaps] = useState<Record<string, FieldMatch[]>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const template = style.level === "display";

  useEffect(() => {
    for (const s of sets) formTables(s.id).then((t) => setTables((prev) => ({ ...prev, [s.id]: t }))).catch(() => undefined);
  }, [sets]);

  const rows = useMemo(() => {
    const q = query.trim().toUpperCase();
    const out: { key: string; set: { id: number; name: string }; table: IntervalTableInfo; field?: string }[] = [];
    for (const s of sets) {
      for (const t of tables[s.id] ?? []) {
        if (!t.columns.length) continue;
        if (template) {
          if (!q || `${s.name} ${t.name}`.toUpperCase().includes(q)) out.push({ key: `${s.id}|${t.key}`, set: s, table: t });
          continue;
        }
        for (const c of t.columns) {
          const kind = t.kind === "event" ? "event" : c.kind === "number" ? "number" : "text";
          if (style.data_kind !== "mixed" && kind !== style.data_kind) continue;
          if (!q || `${s.name} ${t.name} ${c.name}`.toUpperCase().includes(q)) out.push({ key: `${s.id}|${t.key}|${c.name}`, set: s, table: t, field: c.name });
        }
      }
    }
    return out;
  }, [sets, tables, query, template, style.data_kind]);

  const toggle = (key: string, table: IntervalTableInfo, on: boolean) => {
    const next = new Set(picked);
    if (on) next.add(key);
    else next.delete(key);
    setPicked(next);
    if (template && on && !maps[key]) setMaps((m) => ({ ...m, [key]: matchFields(style.body as unknown as TemplateBody, table.columns) }));
  };

  const run = () => {
    const targets: ApplyTarget[] = [];
    for (const r of rows) {
      if (!picked.has(r.key)) continue;
      const common = { ...base, allHoles: true, holes: [], template: undefined };
      if (template) {
        const body = style.body as unknown as TemplateBody;
        const tracks = applyTemplate(body, r.table.key, maps[r.key] ?? [], newTrackId);
        if (tracks.length) targets.push({ setId: r.set.id, settings: { ...common, tube: body.tube ?? base.tube, labels: body.labels ?? base.labels, form: body.form, tracks, template: { uid: style.uid, name: style.name, linked: linked && !style.builtin } } });
      } else {
        const column = r.table.columns.find((c) => c.name === r.field);
        if (!column) continue;
        const t = applyLayerStyle(newTrack(r.table.key, column, 0, r.table.kind === "event"), style, linked);
        const kind = dataKind(t, column);
        targets.push({ setId: r.set.id, settings: { ...common, form: kind === "event" ? "event" : kind === "text" ? "lith" : "grade", tracks: [t] } });
      }
    }
    if (!targets.length) return setNote("Ничего не выбрано или ни одно поле шаблона не сопоставлено");
    setBusy(true);
    onCreate(targets).then((text) => { setNote(text); setPicked(new Set()); }).catch((e: Error) => setNote(`Не применено: ${e.message}`)).finally(() => setBusy(false));
  };

  return (
    <div className="dh-dialog dh-root" role="dialog" aria-label={`Применить «${style.name}»`}>
      <div className="dh-usage-head"><strong className="grow">Применить «{style.name}» к…</strong><button type="button" className="icon" aria-label="Закрыть" onClick={onClose}>✕</button></div>
      <span className="muted small">{template ? "Выберите таблицы: поля сопоставляются по имени без учёта регистра; для несопоставленных выберите поле или пропуск." : "Выберите поля: для каждого будет создано отображение с этим стилем."}</span>
      <input value={query} placeholder="поиск: набор, таблица, поле" aria-label="Поиск" onChange={(e) => setQuery(e.target.value)} />
      <div className="dh-apply-list">
        {rows.map((r) => (
          <div key={r.key}>
            <label className="check">
              <input type="checkbox" checked={picked.has(r.key)} onChange={(e) => toggle(r.key, r.table, e.target.checked)} />
              <span>{r.set.name} · {r.table.name}{r.field ? ` · ${r.field}` : ""}</span>
            </label>
            {template && picked.has(r.key) && (
              <div className="dh-map">
                {(maps[r.key] ?? []).map((m, i) => (
                  <label key={i} className="dh-row">
                    <span className="dh-label">{m.templateField} →</span>
                    <select value={m.field ?? ""} aria-label={`Поле для слоя ${m.templateField}`} onChange={(e) => setMaps((prev) => ({ ...prev, [r.key]: prev[r.key].map((x, j) => (j === i ? { ...x, field: e.target.value || null } : x)) }))}>
                      <option value="">пропустить</option>
                      {r.table.columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                    </select>
                  </label>
                ))}
              </div>
            )}
          </div>
        ))}
        {!rows.length && <span className="muted small">Нет подходящих {template ? "таблиц" : "полей"}.</span>}
      </div>
      <label className="check" title={style.builtin ? "Встроенный стиль применяется только копией" : "Связанные отображения меняются вместе со стилем"}>
        <input type="checkbox" checked={linked} disabled={style.builtin} onChange={(e) => setLinked(e.target.checked)} /> связать со стилем (иначе — копия)
      </label>
      <div className="dh-tools">
        <button type="button" className="primary" disabled={busy || !picked.size} onClick={run}>{busy ? "Создание…" : `Создать отображения${picked.size ? ` (${picked.size})` : ""}`}</button>
        {note && <span className="muted small">{note}</span>}
      </div>
    </div>
  );
}
