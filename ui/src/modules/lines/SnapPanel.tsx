/**
 * «Привязка» of the editor (ТЗ 7.22, ADR 0015 §4): on / off, the targets one by one, the radius in px, the step
 * of the grid, the projection of hole and wireframe targets into the plane of the section, and what the snap
 * does not take (big layers, clouds, sets cut to collars). Every change is the command `lines.snap`.
 */
import { useState } from "react";
import { SNAP_KINDS, SNAP_TITLES, type SnapKind } from "../../edit/snap";
import type { EditSession } from "../../edit/session";
import type { App } from "../../shell/app";
import { Icon } from "../../shell/Icon";
import { useStore } from "../../shell/useStore";
import { parseNumber } from "./modify";
import { editStore } from "./editState";
import css from "./EditToolbar.module.css";

/** The kinds after one is switched. */
export const toggledKinds = (kinds: readonly SnapKind[], kind: SnapKind): SnapKind[] => (kinds.includes(kind) ? kinds.filter((k) => k !== kind) : SNAP_KINDS.filter((k) => k === kind || kinds.includes(k)));

export function SnapPanel({ app, session }: { app: App; session: EditSession }) {
  const [open, setOpen] = useState(false);
  const notes = useStore(editStore, (s) => s.snapNotes);
  const snap = session.snap;
  const run = (args: Record<string, unknown>) => void app.run("lines.snap", args);
  const reason = app.registry.reason("lines.snap");
  const title = `Привязка — ${snap.enabled ? "включена" : "выключена"}: ${snap.kinds.map((k) => SNAP_TITLES[k].toLowerCase()).join(", ") || "цели не выбраны"}`;
  return (
    <span className={css.snapWrap}>
      <button type="button" className={css.tool} data-testid="edit-snap" aria-label="Привязка" aria-pressed={snap.enabled} aria-disabled={!!reason} title={reason ? `${title}. Недоступно: ${reason}` : title} onClick={() => !reason && run({ enabled: !snap.enabled })}>
        <Icon name="edSnap" />
      </button>
      <button type="button" className={css.tool} data-testid="edit-snap-more" aria-label="Цели привязки" aria-expanded={open} title="Цели привязки — что ловит привязка, радиус, шаг сетки, проекция в плоскость разреза" onClick={() => setOpen((v) => !v)}>
        <Icon name="edMore" />
      </button>
      {open && (
        <div className={css.popover} data-testid="edit-snap-panel" role="group" aria-label="Цели привязки">
          <h5>Цели привязки</h5>
          {SNAP_KINDS.map((k) => (
            <label key={k} className={css.check}>
              <input type="checkbox" checked={snap.kinds.includes(k)} onChange={() => run({ kinds: toggledKinds(snap.kinds, k).join(",") })} /> {SNAP_TITLES[k]}
            </label>
          ))}
          <label className={css.field} title="Радиус привязки на экране, px (2–60)">
            Радиус, px
            <input key={`r${snap.radius}`} defaultValue={snap.radius} aria-label="Радиус привязки, px" onBlur={(e) => {
              const v = parseNumber(e.target.value);
              if (v !== null && v !== snap.radius) run({ radius: Math.round(v) });
            }} />
          </label>
          <label className={css.field} title="Шаг узлов сетки для цели «Узел сетки», м">
            Шаг сетки, м
            <input key={`g${snap.grid}`} defaultValue={String(snap.grid).replace(".", ",")} aria-label="Шаг сетки, м" onBlur={(e) => {
              const v = parseNumber(e.target.value);
              if (v !== null && v > 0 && v !== snap.grid) run({ grid: v });
            }} />
          </label>
          <label className={css.check} title="На разрезе точки скважин и каркасов проецируются в плоскость разреза; выключено — берётся их истинное положение">
            <input type="checkbox" checked={snap.project} onChange={(e) => run({ project: e.target.checked })} /> проецировать в плоскость разреза
          </label>
          {notes.map((n) => (
            <span key={n} className={css.note}>
              {n}
            </span>
          ))}
        </div>
      )}
    </span>
  );
}
