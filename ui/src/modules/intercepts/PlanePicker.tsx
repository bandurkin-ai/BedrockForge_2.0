/**
 * «Взять с каркаса» (ADR 0021 §10, decision 22 — desired, the last of the block «Ориентировка»): the mean plane of
 * the marked wireframes (`POST /intercepts/orientation/plane`, the principal vector of Σ S·n·nᵀ) fills α and Aп of
 * the global orientation; the spread of the normals is said beside, so a curved body is seen at once. The list of
 * wireframes is read when the block is opened; the newest answer only is applied.
 */
import { useEffect, useRef, useState } from "react";
import { errorText, toggleId } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { loadWireframes, type WireframeRow } from "../composites/api";
import { numberText } from "../composites/params";
import { meanPlane, type Plane } from "./api";
import own from "./Intercepts.module.css";
import { fmt } from "./TtPreview";

/** At most 16 wireframes (e15-api.md: `bad_param` beyond). */
export const PLANE_MAX = 16;

/** «α 32,5°, Aп 118,0°; разброс нормалей 6,2°; треугольников 1 204; площадь 5 000 м²» — what the core found (one
 * decimal in the text; the fields α and Aп get the exact values). */
export function planeText(p: Plane): string {
  const parts = [`α ${fmt(p.alpha, 1)}°, Aп ${fmt(p.dipdir, 1)}°`];
  if (p.spread !== null) parts.push(`разброс нормалей ${fmt(p.spread, 1)}°`);
  if (p.triangles !== null) parts.push(`треугольников ${p.triangles.toLocaleString("ru-RU")}`);
  if (p.area !== null) parts.push(`площадь ${p.area.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} м²`);
  return parts.join("; ");
}

export function PlanePicker({ onPlane }: { onPlane: (alpha: string, dipdir: string) => void }) {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<WireframeRow[] | null>(null);
  const [ids, setIds] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  useEffect(() => {
    if (!open || list) return;
    loadWireframes().then(setList, (e: unknown) => setError(errorText(e)));
  }, [open]);

  const take = () => {
    const mine = ++seq.current;
    setBusy(true);
    setError(null);
    setDone(null);
    meanPlane(ids).then(
      (p) => {
        if (mine !== seq.current) return;
        setBusy(false);
        if (!Number.isFinite(p.alpha) || !Number.isFinite(p.dipdir)) return setError("Ядро не вернуло плоскость каркаса");
        onPlane(numberText(p.alpha), numberText(p.dipdir));
        setDone(planeText(p));
      },
      (e: unknown) => {
        if (mine !== seq.current) return;
        setBusy(false);
        setError(errorText(e));
      },
    );
  };

  return (
    <details className={css.group} open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)} data-testid="intercepts-plane">
      <summary title="Средняя плоскость отмеченных каркасов: α и Aп глобальной ориентировки">Взять с каркаса</summary>
      {open && (
        <>
          <div className={own.checklist} role="group" aria-label="Каркасы для средней плоскости">
            {(list ?? []).map((w) => (
              <label key={w.id} className={css.check}>
                <input type="checkbox" checked={ids.includes(w.id)} disabled={!ids.includes(w.id) && ids.length >= PLANE_MAX} onChange={(e) => setIds(toggleId(ids, w.id, e.target.checked))} />
                {w.folder ? `${w.folder} › ${w.name}` : w.name}
              </label>
            ))}
            {list && !list.length && <p className={css.note}>В проекте нет каркасов и поверхностей</p>}
            {!list && !error && <p className={css.note}>Чтение…</p>}
          </div>
          <button type="button" className={css.btn} disabled={!ids.length || busy} title={ids.length ? undefined : "Отметьте каркасы"} data-testid="intercepts-plane-take" onClick={take}>
            {busy ? "Расчёт…" : "Взять α и Aп"}
          </button>
          {done && (
            <p className={css.note} data-testid="intercepts-plane-done">
              Средняя плоскость: {done}
            </p>
          )}
          {error && (
            <p className={own.errText} role="alert">
              {error}
            </p>
          )}
        </>
      )}
    </details>
  );
}
