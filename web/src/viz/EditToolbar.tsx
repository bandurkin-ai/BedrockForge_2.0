import { useState, useSyncExternalStore } from "react";
import { formatCoord, parseCoordInput } from "../edit/coords";
import { lineMeasures } from "../edit/measure";
import { lineById } from "../edit/model";
import { TOOL_TITLES, type EditSession, type Tool } from "../edit/session";
import { SNAP_KINDS, SNAP_TITLES, type SnapKind } from "../edit/snap";
import { askForm } from "../shell/FormDialog";

const TOOLS: Tool[] = ["select", "line", "polygon", "point", "tie", "insert"];
const TOOL_HINTS: Record<Tool, string> = {
  select: "Щелчок — выбрать линию или вершину, Shift — добавить; тянуть вершину или выбранную линию — переместить; Ctrl + тянуть — рамка; двойной щелчок по сегменту — вставить вершину",
  line: "Щелчки ставят вершины; Enter или двойной щелчок — закончить; щелчок по концу готовой линии — продолжить её; Backspace — убрать последнюю точку",
  polygon: "Щелчки ставят вершины; Enter, двойной щелчок или щелчок по первой вершине — замкнуть",
  point: "Каждый щелчок — отдельная точка",
  tie: "Тай-линия для сшивки: два щелчка по соответствующим вершинам контуров соседних разрезов",
  insert: "Щелчок по сегменту вставляет в него вершину",
};

const num = (v: unknown) => Number(v);
const fmt = (v: number, digits = 2) => v.toLocaleString("ru-RU", { maximumFractionDigits: digits });

/** Re-renders with the session. */
export function useEdit(edit: EditSession): number {
  return useSyncExternalStore(edit.subscribe, edit.getRevision);
}

export function EditToolbar({ edit, onSave, onExit, sectionOn }: { edit: EditSession; onSave: () => void; onExit: () => void; sectionOn: boolean }) {
  useEdit(edit);
  const [coord, setCoord] = useState("");
  const [coordError, setCoordError] = useState<string | null>(null);
  const [snapOpen, setSnapOpen] = useState(false);
  const sel = edit.shownSelection;
  const lines = edit.targetLines();
  const has = lines.length > 0;
  const one = sel.vertices.length === 1;

  const enterCoord = () => {
    const parsed = parseCoordInput(coord, edit.anchor());
    if ("error" in parsed) return setCoordError(parsed.error);
    setCoordError(null);
    if (edit.tool === "select" && one) edit.moveVertexTo(sel.vertices[0], parsed.point);
    else if (edit.tool === "select" || edit.tool === "insert") return setCoordError("Выберите инструмент рисования или одну вершину, чтобы задать её координаты");
    else edit.addPoint(parsed.point);
    setCoord("");
  };
  const setSnap = (patch: Partial<EditSession["snap"]>) => {
    edit.snap = { ...edit.snap, ...patch };
    edit.touch();
  };
  const toggleKind = (kind: SnapKind) =>
    setSnap({ kinds: edit.snap.kinds.includes(kind) ? edit.snap.kinds.filter((k) => k !== kind) : [...edit.snap.kinds, kind] });

  const shift = async () => {
    const v = await askForm({
      title: "Сдвиг или копия линий", ok: "Применить",
      fields: [
        { key: "dx", label: "ΔX, м", type: "number", value: 0 }, { key: "dy", label: "ΔY, м", type: "number", value: 0 },
        { key: "dz", label: "ΔZ, м", type: "number", value: 0 }, { key: "copy", label: "оставить исходные линии (копия)", type: "checkbox", value: false },
      ],
    });
    if (v) edit.translate([num(v.dx), num(v.dy), num(v.dz)], Boolean(v.copy));
  };
  const ask = async (title: string, label: string, value: number, hint: string, run: (x: number) => void, min?: number) => {
    const v = await askForm({ title, ok: "Применить", fields: [{ key: "x", label, type: "number", value, hint, min }] });
    if (v) run(num(v.x));
  };

  const m = lines.length === 1 ? lineMeasures(lineById(edit.layer, lines[0])!) : null;
  return (
    <div className="edit-toolbar" aria-label="Редактор стрингов">
      <div className="edit-row">
        <strong className="edit-layer" title="Редактируемый слой линий">
          <i style={{ background: edit.color }} />{edit.layerName}{edit.dirty && <span className="edit-dirty" title="Есть несохранённые изменения"> ●</span>}
        </strong>
        <div className="segmented" aria-label="Инструмент">
          {TOOLS.map((t) => (
            <button key={t} className={edit.tool === t ? "active" : ""} title={TOOL_HINTS[t]} onClick={() => edit.setTool(t)}>{TOOL_TITLES[t].split(" ")[0]}{t === "insert" ? " вершину" : ""}</button>
          ))}
        </div>
        <button onClick={() => edit.undo()} disabled={!edit.history.canUndo} title={`Отменить${edit.history.undoLabel ? `: ${edit.history.undoLabel}` : ""} (Ctrl+Z)`}>↶</button>
        <button onClick={() => edit.redo()} disabled={!edit.history.canRedo} title={`Вернуть${edit.history.redoLabel ? `: ${edit.history.redoLabel}` : ""} (Ctrl+Y)`}>↷</button>
        <span className="toolbar-sep" />
        <button className="primary" onClick={onSave} disabled={!edit.dirty || edit.saving} title="Сохранить слой: новая версия в журнале (Ctrl+S)">{edit.saving ? "Сохранение…" : "Сохранить"}</button>
        <button onClick={onExit} title="Выйти из режима правки">Завершить</button>
      </div>
      <div className="edit-row">
        <button onClick={() => edit.setClosed(true)} disabled={!has} title="Замкнуть выбранные линии">Замкнуть</button>
        <button onClick={() => edit.setClosed(false)} disabled={!has} title="Разомкнуть выбранные линии">Разомкнуть</button>
        <button onClick={() => edit.breakAtVertex()} disabled={!one} title="Разорвать линию в выбранной вершине">Разорвать</button>
        <button onClick={() => edit.join()} disabled={lines.length !== 2} title="Соединить две выбранные линии ближайшими концами">Соединить</button>
        <button onClick={() => edit.reverse()} disabled={!has} title="Развернуть направление линий">Развернуть</button>
        <button onClick={shift} disabled={!has} title="Сдвинуть на вектор (в том числе по Z) или сделать копию">Сдвиг…</button>
        <button onClick={() => ask("Копия со смещением", "Смещение, м", 5, "Для полигона: больше нуля — наружу, меньше — внутрь", (x) => edit.offset(x))} disabled={!has} title="Параллельная копия линии; для полигона — наружу или внутрь">Смещение…</button>
        <button onClick={() => ask("Проредить", "Допуск, м", 0.5, "Вершины, отстоящие от линии меньше допуска, удаляются (Дуглас–Пекер)", (x) => edit.simplify(x), 0)} disabled={!has}>Проредить…</button>
        <button onClick={() => ask("Сгустить", "Шаг, м", 5, "Вершины добавляются так, чтобы сегменты были не длиннее шага", (x) => edit.densify(x), 1e-6)} disabled={!has}>Сгустить…</button>
        <button onClick={() => edit.smooth(1)} disabled={!has} title="Сгладить выбранные линии (срезание углов)">Сгладить</button>
        <button onClick={() => edit.deleteSelection()} disabled={!has && !sel.vertices.length} title="Удалить выбранные вершины или линии (Delete)">Удалить</button>
        <span className="toolbar-sep" />
        <span className="edit-snap">
          <button className={edit.snap.enabled ? "active" : ""} onClick={() => setSnap({ enabled: !edit.snap.enabled })} title="Привязка к объектам">Привязка</button>
          <button onClick={() => setSnapOpen((v) => !v)} title="Цели привязки" aria-expanded={snapOpen}>▾</button>
          {snapOpen && (
            <div className="edit-popover" onMouseLeave={() => setSnapOpen(false)}>
              {SNAP_KINDS.map((k) => (
                <label key={k} className="check"><input type="checkbox" checked={edit.snap.kinds.includes(k)} onChange={() => toggleKind(k)} /> {SNAP_TITLES[k]}</label>
              ))}
              <label title="Радиус привязки на экране">радиус, px <input className="num" type="number" min={2} max={60} value={edit.snap.radius} onChange={(e) => setSnap({ radius: Math.max(2, Number(e.target.value) || 12) })} /></label>
              <label title="Шаг узлов сетки для привязки">шаг сетки, м <input className="num" type="number" min={0.01} value={edit.snap.grid} onChange={(e) => setSnap({ grid: Math.max(0.01, Number(e.target.value) || 10) })} /></label>
              <label className="check" title="На разрезе точки скважин и каркасов проецируются в плоскость разреза; выключено — берётся истинное положение">
                <input type="checkbox" checked={edit.snap.project} onChange={(e) => setSnap({ project: e.target.checked })} /> проецировать в плоскость разреза
              </label>
            </div>
          )}
        </span>
        {sectionOn ? (
          <span className="muted small" title="Новые точки ложатся в плоскость разреза (середина окна отсечения)">в плоскости разреза</span>
        ) : (
          <label title="Уровень, на который ложатся новые точки в плане">Z <input className="num wide" type="number" step={1} value={edit.zLevel}
            onChange={(e) => { edit.zLevel = Number(e.target.value) || 0; edit.touch(); }} aria-label="Уровень Z новых точек" /></label>
        )}
        <form className="edit-coord" onSubmit={(e) => { e.preventDefault(); enterCoord(); }}>
          <input value={coord} placeholder="X Y Z · @азимут длина наклон · @@dX dY dZ" aria-label="Координаты точки" title={coordError ?? "Координаты следующей точки (или выбранной вершины): X Y Z; от предыдущей — @азимут расстояние [угол наклона] или @@dX dY [dZ]; Enter"}
            className={coordError ? "invalid" : ""} onChange={(e) => { setCoord(e.target.value); setCoordError(null); }} />
        </form>
      </div>
      <div className="edit-status small">
        {coordError ? <span className="error-text">{coordError}</span> : edit.message ? <span>{edit.message}</span> : <span className="muted">{TOOL_HINTS[edit.tool]}</span>}
        <span className="grow" />
        {m && <span title="Выбранная линия">вершин {m.vertices} · длина {fmt(m.length)} м (в плане {fmt(m.planLength)}){m.area !== null ? ` · площадь ${fmt(m.area)} м² (в плане ${fmt(m.planArea ?? 0)})` : ""}</span>}
        {!m && lines.length > 1 && <span>выбрано линий: {lines.length}</span>}
        {edit.hover?.point && <span className="edit-xyz">{formatCoord(edit.hover.point, 2)}</span>}
      </div>
    </div>
  );
}

/** The snap label at the cursor and the selection frame, drawn over the canvas. */
export function EditCursor({ edit }: { edit: EditSession }) {
  useEdit(edit);
  const h = edit.hover;
  const box = edit.drag?.kind === "box" ? edit.drag : null;
  return (
    <>
      {h?.snap && <div className="snap-label" style={{ left: h.x + 14, top: h.y + 12 }}>{h.snap.label || SNAP_TITLES[h.snap.kind]}</div>}
      {box && <div className="select-box" style={{ left: Math.min(box.x0, box.x1), top: Math.min(box.y0, box.y1), width: Math.abs(box.x1 - box.x0), height: Math.abs(box.y1 - box.y0) }} />}
    </>
  );
}
