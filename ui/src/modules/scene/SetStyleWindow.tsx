import { useStore } from "../../shell/useStore";
import { normalizeCollarStyle, type CollarStyle } from "../../viz/holes/collarStyle";
import { normalizeTraceStyle, type TraceStyle } from "../../viz/holes/traceStyle";
import { updateView, viewStore } from "./bind";
import { CollarStyleForm } from "./CollarStyleForm";
import { TraceStyleForm } from "./TraceStyleForm";
import { patchEntry } from "./viewSync";

export const STYLE_KEYS = { collars: "collar_styles", traces: "trace_styles" } as const;

/** The style of a set from the project view settings (defaults when none). */
export function setStyle<K extends keyof typeof STYLE_KEYS>(kind: K, setId: number, keys = viewStore.get()): K extends "collars" ? CollarStyle : TraceStyle {
  const map = (keys[STYLE_KEYS[kind]] ?? {}) as Record<string, unknown>;
  const raw = map[String(setId)];
  return (kind === "collars" ? normalizeCollarStyle(raw) : normalizeTraceStyle(raw)) as K extends "collars" ? CollarStyle : TraceStyle;
}

export function saveSetStyle(kind: keyof typeof STYLE_KEYS, setId: number, style: CollarStyle | TraceStyle): void {
  updateView(STYLE_KEYS[kind], patchEntry(String(setId), style));
}

/** «Устья · набор» / «Стволы · набор» (ТЗ 7.15): applied at once, remembered for the set in the project. */
export function SetStyleWindow({ kind, setId }: { kind: keyof typeof STYLE_KEYS; setId: number }) {
  const keys = useStore(viewStore, (x) => x);
  if (kind === "collars") return <CollarStyleForm style={setStyle("collars", setId, keys)} onChange={(s) => saveSetStyle("collars", setId, s)} />;
  const notes = ["Пересечения с каркасами и ЦТМ считает ядро по треугольникам: значок ◆ и подпись «глубина · объект» с точностью до миллиметра. Блочные модели — на этапе Э17."];
  return <TraceStyleForm style={setStyle("traces", setId, keys)} onChange={(s) => saveSetStyle("traces", setId, s)} notes={notes} />;
}
