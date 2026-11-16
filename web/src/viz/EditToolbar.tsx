import { useState, useSyncExternalStore } from "react";
import { formatCoord, parseCoordInput } from "../edit/coords";
import { lineMeasures } from "../edit/measure";
import { lineById } from "../edit/model";
import { TOOL_TITLES, type EditSession, type Tool } from "../edit/session";
import { SNAP_KINDS, SNAP_TITLES, type SnapKind } from "../edit/snap";
import { askForm } from "../shell/FormDialog";
import type { IconName } from "../shell/icons";
import { ToolButton } from "../shell/ToolButton";

const TOOLS: Tool[] = ["select", "line", "polygon", "point", "tie", "insert"];
const TOOL_HINTS: Record<Tool, string> = {
  select: "Щелчок — выбрать линию или вершину, Shift — добавить; тянуть вершину или выбранную линию — переместить; Ctrl + тянуть — рамка; двойной щелчок по сегменту — вставить вершину",
  line: "Щелчки ставят вершины; правая кнопка, Enter или двойной щелчок — закончить; щелчок по концу готовой линии — продолжить её; Backspace — убрать последнюю точку",
  polygon: "Щелчки ставят вершины; правая кнопка, Enter, двойной щелчок или щелчок по первой вершине — замкнуть",
  point: "Каждый щелчок — отдельная точка",
  tie: "Тай-линия для сшивки: два щелчка по соответствующим вершинам контуров соседних разрезов",
  insert: "Щелчок по сегменту вставляет в него вершину",
};

const TOOL_ICONS: Record<Tool, IconName> = { select: "edSelect", line: "edPline", polygon: "edPolygon", point: "edPoint", tie: "edTie", insert: "edInsert" };

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
  const need = "Сначала выберите линии (инструмент «Выбор»)";
  return (
    <div className="edit-toolbar" aria-label="Редактор стрингов">
      <div className="edit-row">
        <strong className="edit-layer" title="Редактируемый слой линий">
          <i style={{ background: edit.color }} />{edit.layerName}{edit.dirty && <span className="edit-dirty" title="Есть несохранённые изменения"> ●</span>}
        </strong>
        <div className="tool-group" role="toolbar" aria-label="Инструмент">
          {TOOLS.map((t) => (
            <ToolButton key={t} icon={TOOL_ICONS[t]} label={TOOL_TITLES[t]} hint={TOOL_HINTS[t]} active={edit.tool === t} onClick={() => edit.setTool(t)} />
          ))}
        </div>
        <div className="tool-group" role="toolbar" aria-label="Правка линий">
          <ToolButton icon="edClose" label="Замкнуть" hint="Замкнуть выбранные линии в полигоны" off={need} disabled={!has} onClick={() => edit.setClosed(true)} />
          <ToolButton icon="edOpen" label="Разомкнуть" hint="Разомкнуть выбранные полигоны" off={need} disabled={!has} onClick={() => edit.setClosed(false)} />
          <ToolButton icon="edBreak" label="Разорвать" hint="Разорвать линию в выбранной вершине (одна вершина)" off="Выберите одну вершину" disabled={!one} onClick={() => edit.breakAtVertex()} />
          <ToolButton icon="edJoin" label="Соединить" hint="Соединить две выбранные линии ближайшими концами" off="Выберите ровно две линии" disabled={lines.length !== 2} onClick={() => edit.join()} />
          <ToolButton icon="edReverse" label="Развернуть" hint="Развернуть направление выбранных линий" off={need} disabled={!has} onClick={() => edit.reverse()} />
        </div>
        <div className="tool-group" role="toolbar" aria-label="Изменение формы">
          <ToolButton icon="edMove" label="Сдвиг…" hint="Сдвинуть выбранные линии на вектор (в том числе по Z) или сделать копию; мышью — тянуть выбранную линию в режиме «Выбор»" off={need} disabled={!has} onClick={shift} />
          <ToolButton icon="edOffset" label="Смещение…" hint="Параллельная копия линии на заданном расстоянии; для полигона: больше нуля — наружу, меньше — внутрь" off={need} disabled={!has}
            onClick={() => ask("Копия со смещением", "Смещение, м", 5, "Для полигона: больше нуля — наружу, меньше — внутрь", (x) => edit.offset(x))} />
          <ToolButton icon="edSimplify" label="Проредить…" hint="Удалить вершины, отстоящие от линии меньше допуска (Дуглас–Пекер)" off={need} disabled={!has}
            onClick={() => ask("Проредить", "Допуск, м", 0.5, "Вершины, отстоящие от линии меньше допуска, удаляются (Дуглас–Пекер)", (x) => edit.simplify(x), 0)} />
          <ToolButton icon="edDensify" label="Сгустить…" hint="Добавить вершины, чтобы сегменты были не длиннее шага" off={need} disabled={!has}
            onClick={() => ask("Сгустить", "Шаг, м", 5, "Вершины добавляются так, чтобы сегменты были не длиннее шага", (x) => edit.densify(x), 1e-6)} />
          <ToolButton icon="edSmooth" label="Сгладить" hint="Сгладить выбранные линии срезанием углов" off={need} disabled={!has} onClick={() => edit.smooth(1)} />
          <ToolButton icon="edErase" label="Удалить" hint="Удалить выбранные вершины или линии" keys="Delete" off="Выберите вершины или линии" disabled={!has && !sel.vertices.length} onClick={() => edit.deleteSelection()} />
        </div>
        <div className="tool-group" role="toolbar" aria-label="История">
          <ToolButton icon="edUndo" label="Отменить" hint={edit.history.undoLabel ?? undefined} keys="Ctrl+Z" disabled={!edit.history.canUndo} onClick={() => edit.undo()} />
          <ToolButton icon="edRedo" label="Вернуть" hint={edit.history.redoLabel ?? undefined} keys="Ctrl+Y" disabled={!edit.history.canRedo} onClick={() => edit.redo()} />
        </div>
        <span className="edit-snap">
          <ToolButton icon="edSnap" label="Привязка" hint={`Привязка к объектам: ${edit.snap.kinds.map((k) => SNAP_TITLES[k].toLowerCase()).join(", ") || "цели не выбраны"}`}
            active={edit.snap.enabled} onClick={() => setSnap({ enabled: !edit.snap.enabled })} />
          <ToolButton icon="edMore" label="Цели привязки" hint="Что ловит привязка, радиус, шаг сетки" className="narrow" aria-expanded={snapOpen} onClick={() => setSnapOpen((v) => !v)} />
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
        <div className="tool-group" role="toolbar" aria-label="Слой">
          <ToolButton icon="edSave" label={edit.saving ? "Сохранение…" : "Сохранить"} hint="Сохранить слой: новая версия в журнале" keys="Ctrl+S" className="save" off="Изменений нет" disabled={!edit.dirty || edit.saving} onClick={onSave} />
          <ToolButton icon="edExit" label="Завершить правку" hint="Выйти из режима правки (несохранённые изменения будут предложено сохранить)" onClick={onExit} />
        </div>
      </div>
      <div className="edit-status small">
        {coordError ? <span className="error-text" title={coordError}>{coordError}</span> : edit.message ? <span title={edit.message}>{edit.message}</span> : <span className="muted" title={TOOL_HINTS[edit.tool]}>{TOOL_HINTS[edit.tool]}</span>}
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
