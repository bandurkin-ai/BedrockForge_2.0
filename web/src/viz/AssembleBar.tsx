import { useEffect, useState, type MutableRefObject } from "react";
import { stitchPreview, type StitchPreview } from "../api";
import { lineById } from "../edit/model";
import type { EditSession } from "../edit/session";
import { ToolButton } from "../shell/ToolButton";
import { useEdit } from "./EditToolbar";
import type { GeoScene } from "./scene3d";

const fmt = (v: number) => v.toLocaleString("ru-RU", { maximumFractionDigits: 1 });

/**
 * «Собрать из контуров» (as the wireframe linking of Micromine): the user clicks contours one after
 * another, the wireframe grows from contour to contour in the view; «Замкнуть» caps the open ends;
 * «Создать каркас» makes the object (a background operation, the layer is saved first).
 */
export function AssembleBar({ edit, sceneRef, onCreate }: {
  edit: EditSession;
  sceneRef: MutableRefObject<GeoScene | null>;
  onCreate: (lineIndices: number[], closed: boolean) => void;
}) {
  const revision = useEdit(edit);
  const [preview, setPreview] = useState<StitchPreview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const a = edit.assemble;
  const order = a ? edit.assembled() : [];
  const key = a ? `${order.join(",")}|${a.closed}|${order.map((id) => lineById(edit.layer, id)?.points.length).join(",")}` : "";

  // The preview follows the picks (and edits of the picked contours), a moment after the last change.
  useEffect(() => {
    const scene = sceneRef.current;
    if (!a || order.length < 2) {
      setPreview(null);
      setProblem(null);
      scene?.setMarks("assemble", null);
      return;
    }
    const abort = new AbortController();
    const timer = window.setTimeout(() => {
      const contours = order.map((id) => lineById(edit.layer, id)!.points.map((p) => [...p]));
      // Tie lines of the layer guide the stitching, as in «Сшить контуры».
      const ties = edit.layer.lines.filter((l) => !l.closed && l.points.length >= 2 && String(l.attrs.ROLE ?? "").toUpperCase() === "TIE").map((l) => [[...l.points[0]], [...l.points[l.points.length - 1]]]);
      setBusy(true);
      stitchPreview({ contours, ties, start_cap: a.closed ? "flat" : "none", end_cap: a.closed ? "flat" : "none" }, abort.signal)
        .then((r) => {
          setPreview(r);
          setProblem(null);
          sceneRef.current?.setMarks("assemble", r.corners, "#2fbf71");
        })
        .catch((e) => {
          if ((e as Error).name === "AbortError") return;
          setPreview(null);
          setProblem((e as Error).message);
          sceneRef.current?.setMarks("assemble", null);
        })
        .finally(() => setBusy(false));
    }, 200);
    return () => {
      abort.abort();
      window.clearTimeout(timer);
    };
  }, [key, revision]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => sceneRef.current?.setMarks("assemble", null), [sceneRef]);
  if (!a) return null;

  const create = () => {
    const indices = order.map((id) => edit.layer.lines.findIndex((l) => l.id === id)).filter((i) => i >= 0);
    onCreate(indices, a.closed);
  };
  return (
    <div className="edit-toolbar assemble-bar" aria-label="Сборка каркаса из контуров">
      <div className="edit-row">
        <strong className="edit-layer">Сборка каркаса</strong>
        <span className="small">контуров: <b>{order.length}</b></span>
        <div className="tool-group" role="toolbar" aria-label="Сборка">
          <ToolButton icon="edUndo" label="Убрать последний контур" hint="Каркас становится на контур короче; повторный щелчок по контуру тоже убирает его"
            off="Контуры ещё не выбраны" disabled={!order.length} onClick={() => edit.dropLastContour()} />
          <ToolButton icon="edClose" label={a.closed ? "Открыть торцы" : "Замкнуть торцы"} active={a.closed}
            hint="Закрыть открытые торцы каркаса плоскими крышками (по первому и последнему контуру) — получится замкнутое тело"
            onClick={() => edit.setAssembleClosed(!a.closed)} />
        </div>
        <span className={`small assemble-info ${problem ? "error-text" : "muted"}`} title={problem ?? preview?.warnings.join("; ") ?? undefined}>
          {busy ? "строится…" : problem ? problem : preview ? `треугольников ${preview.triangles.toLocaleString("ru-RU")}${preview.volume !== null ? ` · объём ${fmt(preview.volume)} м³` : " · торцы открыты"}` : "выберите хотя бы два контура"}
        </span>
        <span className="toolbar-sep" />
        <button className="primary" disabled={order.length < 2 || !!problem || busy} onClick={create}
          title="Создать каркас из выбранных контуров (слой сохраняется, каркас строится в фоне)">Создать каркас</button>
        <button onClick={() => edit.stopAssemble()} title="Выйти из сборки (Esc)">Отмена</button>
      </div>
    </div>
  );
}
