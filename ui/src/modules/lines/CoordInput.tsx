/**
 * The field of coordinates of the editor (ТЗ 7.22): the next point of the line being drawn — or the selected
 * vertex — by `X Y Z`, `X Y` (on the Z of the panel), from the previous one `@азимут расстояние [наклон]` or
 * `@@dX dY [dZ]`; a comma and a dot both mark the decimals. Enter puts the point, Esc leaves the field.
 */
import { useState } from "react";
import { parseCoordInput } from "../../edit/coords";
import type { EditSession } from "../../edit/session";
import * as cmd from "../../edit/tools";
import css from "./EditToolbar.module.css";

/** What Enter in the field does: a point of the drawing tool or new coordinates of the selected vertex. */
export function enterCoordinates(s: EditSession, text: string): string | null {
  if (!text.trim()) return "Введите координаты: X Y Z, @азимут расстояние [наклон] или @@dX dY [dZ]";
  const planZ = s.clip && s.section ? undefined : s.zLevel;
  const parsed = parseCoordInput(text, s.anchor(), planZ);
  if ("error" in parsed) return parsed.error;
  const one = s.selection.vertices.length === 1 ? s.selection.vertices[0] : null;
  if (s.tool === "select" && one) {
    cmd.moveVertexTo(s, one, parsed.point);
    return null;
  }
  if (s.tool === "select" || s.tool === "insert") return "Выберите инструмент рисования (линия, полигон, точка) или одну вершину, чтобы задать её координаты";
  s.addPoint(parsed.point);
  return null;
}

export function CoordInput({ session }: { session: EditSession }) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submit = () => {
    const e = enterCoordinates(session, text);
    setError(e);
    if (!e) setText("");
  };
  return (
    <form
      className={css.coord}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <input
        data-testid="edit-coord"
        value={text}
        className={error ? css.invalid : ""}
        placeholder="X Y Z · @азимут длина [наклон] · @@dX dY [dZ]"
        aria-label="Координаты точки"
        aria-invalid={error ? true : undefined}
        title={error ?? "Координаты следующей точки (или выбранной вершины): X Y Z; от предыдущей — @азимут расстояние [наклон] или @@dX dY [dZ]; Enter — поставить"}
        onChange={(e) => {
          setText(e.target.value);
          setError(null);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") (e.target as HTMLInputElement).blur();
        }}
      />
      {error && (
        <span className={css.error} role="alert" data-testid="edit-coord-error">
          {error}
        </span>
      )}
    </form>
  );
}
