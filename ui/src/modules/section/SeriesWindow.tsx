/**
 * «Новая серия» and the parameters of a series (ТЗ 8.1.1–8.1.2; ADR 0016 §1): the type (vertical P1 → P2 or P1 + A,
 * plan, inclined, polyline), the fields of the type, the window and the step, the template of the names, the
 * shadow. Numbers take a comma or a dot. An error of the form or of the core (422 with its field) is shown under
 * the fields and marks the field; a 409 asks in an own window (`seriesForm.saveSeries`). «По видимым данным» sets
 * s0, s1 (a plan — the levels) over what the 3D window shows, multiples of Δ, K ≤ 5 000.
 */
import { useState } from "react";
import { useApp } from "../../shell/AppContext";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import type { SeriesKind } from "../../viz/section/series";
import type { ShadowMode } from "./seriesApi";
import { seriesSaved } from "./seriesActions";
import { FIELD_LABELS, formFromRow, rangeOver, saveSeries, SHADOW_TITLES, TYPE_TITLES, type FieldId, type SeriesForm } from "./seriesForm";
import { boxCorners, seriesById, seriesStore, visibleBoxes } from "./seriesState";

/** The fields of each type, in the order of the window. */
export const TYPE_FIELDS: Record<SeriesKind, (FieldId | "dir")[][]> = {
  vertical: [["p1x", "p1y"], ["dir"], ["s0", "s1", "step"], ["towards", "away"]],
  plan: [["p1x", "p1y"], ["top", "bottom", "step"], ["towards", "away"]],
  inclined: [["p1x", "p1y", "p1z"], ["dir"], ["phi"], ["s0", "s1", "step"], ["towards", "away"]],
  polyline: [["polyline"], ["corridor"]],
};

const HINTS: Partial<Record<FieldId, string>> = {
  towards: "w_к — часть окна к наблюдателю",
  away: "w_от — часть окна за плоскостью; при Δ = w_к + w_от окна соседних разрезов смыкаются",
  step: "Расстояние между разрезами по нормали; по умолчанию — ширина окна",
  phi: "90° — вертикальная плоскость, 0° — горизонтальная",
  template: "Переменные {k} (с 1), {s}, {X}, {Y}, {Z}; пусто — по умолчанию (A = 90° — «{Y:.0f}N»)",
  top: "Первый план — на верхней отметке, следующие ниже на шаг",
  polyline: "По одной вершине в строке: «X; Y»",
};

export function SeriesWindow({ winId, mode, form: initial, seriesId }: { winId: string; mode: "create" | "edit"; form?: SeriesForm; seriesId?: number }) {
  const app = useApp();
  const row = useStore(seriesStore, (s) => (seriesId ? (seriesById(seriesId, s) ?? null) : null));
  const [f, setF] = useState<SeriesForm | null>(() => initial ?? (row ? formFromRow(row) : null));
  const [version, setVersion] = useState(row?.version ?? 0);
  const [error, setError] = useState<{ text: string; hint: string; field: FieldId | null } | null>(null);
  const [busy, setBusy] = useState(false);
  if (!f) return <p className={css.note}>Серия не найдена: её удалили.</p>;
  const set = (patch: Partial<SeriesForm>) => {
    setF({ ...f, ...patch });
    setError(null);
  };
  const submit = async () => {
    setBusy(true);
    const r = await saveSeries(mode === "create" ? { mode } : { mode, id: seriesId!, version }, f);
    setBusy(false);
    if (r.ok) {
      app.store.set({ statusNote: seriesSaved(r.row, mode === "create") });
      return closeWindow(winId);
    }
    if ("reload" in r) {
      setF(formFromRow(r.reload));
      setVersion(r.reload.version);
      return setError({ text: "Показаны текущие параметры серии.", hint: "Проверьте их и сохраните снова.", field: null });
    }
    if ("error" in r) setError({ text: r.error, hint: r.hint, field: r.field });
  };
  const field = (id: FieldId) => {
    const label = FIELD_LABELS[id];
    const value = String(f[id as keyof SeriesForm] ?? "");
    const bad = error?.field === id || undefined;
    return (
      <label key={id} className={css.field} title={HINTS[id]}>
        <span>{label}</span>
        {id === "polyline" ? (
          <textarea className={css.input} rows={5} data-testid="series-field-polyline" value={value} aria-invalid={bad} onChange={(e) => set({ polyline: e.target.value })} />
        ) : (
          <input className={css.input} inputMode={id === "name" || id === "template" ? "text" : "decimal"} data-testid={`series-field-${id}`} value={value} aria-invalid={bad} onChange={(e) => set({ [id]: e.target.value } as Partial<SeriesForm>)} />
        )}
      </label>
    );
  };
  const dir = (
    <div key="dir" className={css.inline} role="radiogroup" aria-label="Направление серии">
      {(["az", "p2"] as const).map((d) => (
        <label key={d} className={css.check}>
          <input type="radio" name={`${winId}-dir`} checked={f.dir === d} data-testid={`series-dir-${d}`} onChange={() => set({ dir: d })} />
          {d === "az" ? "P1 и азимут" : "P1 → P2"}
        </label>
      ))}
      {f.dir === "az" ? field("az") : [field("p2x"), field("p2y")]}
    </div>
  );
  return (
    <form
      className={css.win}
      data-testid="series-window"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className={css.winBody}>
        <div className={css.formRow}>
          <span>Название</span>
          <input className={css.input} data-testid="series-field-name" placeholder={mode === "create" ? "По умолчанию — «Серия N»" : undefined} value={f.name} aria-invalid={error?.field === "name" || undefined} onChange={(e) => set({ name: e.target.value })} />
        </div>
        <div className={css.inline} role="radiogroup" aria-label="Тип серии">
          {(Object.keys(TYPE_TITLES) as SeriesKind[]).map((t) => (
            <label key={t} className={css.check}>
              <input type="radio" name={`${winId}-type`} checked={f.type === t} data-testid={`series-type-${t}`} onChange={() => set({ type: t })} />
              {TYPE_TITLES[t]}
            </label>
          ))}
        </div>
        {TYPE_FIELDS[f.type].map((group, i) => (
          <div key={i} className={css.pair}>
            {group.map((id) => (id === "dir" ? dir : field(id)))}
          </div>
        ))}
        <div className={css.pair}>
          {field("template")}
          <label className={css.field}>
            <span>Тень</span>
            <select className={css.input} data-testid="series-field-shadow" value={f.shadow} onChange={(e) => set({ shadow: e.target.value as ShadowMode })}>
              {(Object.keys(SHADOW_TITLES) as ShadowMode[]).map((s) => (
                <option key={s} value={s}>
                  {SHADOW_TITLES[s]}
                </option>
              ))}
            </select>
          </label>
        </div>
        {error && (
          <p className={css.error} role="alert" data-testid="series-error">
            {error.text}
            {error.hint ? ` ${error.hint}` : ""}
          </p>
        )}
      </div>
      <div className={css.footer}>
        {f.type !== "polyline" && (
          <button type="button" className={css.btn} data-testid="series-range" title="Пределы серии по охвату видимых данных, кратно шагу" onClick={() => set(rangeOver(f, boxCorners(visibleBoxes(app.store.get().view.hidden))))}>
            По видимым данным
          </button>
        )}
        <span className={css.grow} />
        <button type="submit" className={css.primary} data-testid="series-submit" disabled={busy}>
          {mode === "create" ? "Создать" : "Сохранить"}
        </button>
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Отмена
        </button>
      </div>
    </form>
  );
}
