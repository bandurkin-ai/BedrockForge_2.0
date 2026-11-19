/**
 * The panel of the string editor over the 3D window (ADR 0015 §5; ТЗ 7.22): the layer and its version, the
 * tools and the actions in the manner of AutoCAD (every button — its title, what it does, its keys and why it is
 * unavailable; each runs a command of the registry), undo and redo, the field of coordinates, the Z of new points
 * in plan (on a section — its plane), «Привязка», measure, versions, move, save and finish; below — the hint of
 * the tool or the message of the session, the measurements of the selection and of the layer, the point under
 * the cursor. Over the canvas — the label of the snap target and the box of the selection.
 */
import { useState, useSyncExternalStore } from "react";
import { formatCoord } from "../../edit/coords";
import type { EditSession } from "../../edit/session";
import { useApp } from "../../shell/AppContext";
import type { App } from "../../shell/app";
import { Icon } from "../../shell/Icon";
import { useStore } from "../../shell/useStore";
import { CoordInput } from "./CoordInput";
import { editStore } from "./editState";
import { buttonState, PANEL, TOOL_HINTS, tooltip, type PanelButton, type PanelGroup } from "./editTools";
import { layerText, selectionText } from "./measures";
import { parseNumber } from "./modify";
import { SnapPanel } from "./SnapPanel";
import css from "./EditToolbar.module.css";

/** Re-renders with the session. */
export function useSession(s: EditSession): number {
  return useSyncExternalStore(s.subscribe, s.getRevision);
}

/** The editor over the 3D window: the panel and the marks at the cursor; nothing without a session. */
export function EditorHud() {
  const app = useApp();
  const session = useStore(editStore, (s) => s.session);
  if (!session) return null;
  return (
    <>
      <EditToolbar app={app} session={session} />
      <EditCursor session={session} />
    </>
  );
}

const GROUPS: { id: PanelGroup; label: string }[] = [
  { id: "tools", label: "Инструмент" },
  { id: "modify", label: "Правка линий" },
  { id: "history", label: "История и измерения" },
  { id: "layer", label: "Слой" },
];

function Tool({ app, b, session }: { app: App; b: PanelButton; session: EditSession }) {
  const st = buttonState(b, session, (c) => app.registry.reason(c));
  const run = () => {
    if (!st.reason) void app.run(b.command, b.args ?? {});
  };
  return (
    <button
      type="button"
      className={`${css.tool} ${b.id === "save" ? css.save : ""}`}
      data-testid={`edit-${b.id}`}
      aria-label={b.title.replace("…", "")}
      aria-pressed={b.command === "lines.tool" ? st.active : undefined}
      aria-disabled={!!st.reason}
      title={tooltip(b, st)}
      onClick={run}
    >
      <Icon name={b.icon} />
    </button>
  );
}

/** The level of new points in plan: Enter or leaving the field sets it (a comma or a dot). */
function ZField({ session }: { session: EditSession }) {
  const [text, setText] = useState<string | null>(null);
  const shown = text ?? String(session.zLevel).replace(".", ",");
  const commit = () => {
    const v = text === null ? null : parseNumber(text);
    if (v !== null && v !== session.zLevel) {
      session.zLevel = v;
      session.viewChanged();
      session.touch();
    }
    setText(null);
  };
  return (
    <label className={css.field} title="Уровень Z, на который ложатся новые точки в плане (вершины без привязки)">
      Z
      <input data-testid="edit-z" value={shown} aria-label="Уровень Z новых точек" onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />
    </label>
  );
}

export function EditToolbar({ app, session }: { app: App; session: EditSession }) {
  useSession(session);
  useSyncExternalStore(app.registry.subscribe, app.registry.version);
  useStore(app.store, (s) => s.editing);
  const saving = useStore(editStore, (s) => s.saving);
  const s = session;
  const onSection = s.clip && s.section !== null;
  const measured = selectionText(s);
  return (
    <div className={css.panel} data-edit-panel data-testid="edit-panel" role="toolbar" aria-label="Редактор стрингов">
      <div className={css.row}>
        <span className={css.layer} title={`Слой на правке: «${s.layerName}», версия ${s.baseVersion}${s.dirty ? "; есть несохранённые изменения" : ""}`}>
          <i className={css.swatch} style={{ background: s.color || "var(--accent)" }} />
          <span data-testid="edit-layer">{s.layerName}</span>
          <span className={css.version} data-testid="edit-version">в. {s.baseVersion}</span>
          {s.dirty && (
            <span className={css.dirty} data-testid="edit-dirty" aria-label="Есть несохранённые изменения">
              ●
            </span>
          )}
        </span>
        {GROUPS.map((g) => (
          <span key={g.id} className={css.group} role="group" aria-label={g.label}>
            {PANEL.filter((b) => b.group === g.id).map((b) => (
              <Tool key={b.id} app={app} b={b} session={s} />
            ))}
            {g.id === "history" && <SnapPanel app={app} session={s} />}
          </span>
        ))}
      </div>
      <div className={css.row}>
        {onSection ? (
          <span className={css.muted} title="Новые точки ложатся в вертикальную плоскость середины окна разреза">
            в плоскости разреза
          </span>
        ) : (
          <ZField key={`${s.layerId}:${s.zLevel}`} session={s} />
        )}
        <CoordInput session={s} />
      </div>
      <div className={css.status} data-testid="edit-status">
        <span className={s.message ? "" : css.muted} title={s.message ?? TOOL_HINTS[s.tool]}>
          {saving ? "Сохранение…" : (s.message ?? TOOL_HINTS[s.tool])}
        </span>
        <span className={css.grow} />
        <span data-testid="edit-measures" title={measured ?? layerText(s)}>
          {measured ?? layerText(s)}
        </span>
        {s.hover?.point && (
          <span className={css.xyz} data-testid="edit-xyz">
            {formatCoord(s.hover.point, 2)}
          </span>
        )}
      </div>
    </div>
  );
}

/** The label of the snap target at the cursor and the box of the selection, over the canvas. */
export function EditCursor({ session }: { session: EditSession }) {
  useSession(session);
  const h = session.hover;
  const box = session.drag?.kind === "box" ? session.drag : null;
  return (
    <>
      {h?.snap && (
        <div className={css.snapLabel} style={{ left: h.x + 14, top: h.y + 12 }} data-testid="edit-snap-label">
          {h.snap.label}
        </div>
      )}
      {box && <div className={css.box} style={{ left: Math.min(box.x0, box.x1), top: Math.min(box.y0, box.y1), width: Math.abs(box.x1 - box.x0), height: Math.abs(box.y1 - box.y0) }} />}
    </>
  );
}
