/**
 * «Текущий разрез / все» (ADR 0016 §7): while a section of a series is the drawing plane, only the lines on it —
 * all their vertices in its window, the rule of the core (`SeriesGeometry.lineSection`) — are picked, selected
 * and edited; the others stay visible. The verdict is kept per line object (the layer is immutable and shares
 * untouched lines), so an edit tests only the lines it changed. No three.js.
 */
import type { EditLayer, EditLine, Vec3 } from "./model";

export type EditScope = "current" | "all";

/** Is a line (its vertices, project float64) on the current section. */
export type LineTest = (points: readonly Vec3[]) => boolean;

export const SCOPE_TITLES: Record<EditScope, string> = { current: "Текущий разрез", all: "Все линии" };

export class SectionLock {
  scope: EditScope = "current";
  private test: LineTest | null = null;
  private verdict = new WeakMap<EditLine, boolean>();
  private view: { layer: EditLayer; lines: EditLayer } | null = null;

  /** The test of the current section; null — no section of a series (everything is editable). */
  setTest(test: LineTest | null): void {
    if (test === this.test) return;
    this.test = test;
    this.verdict = new WeakMap();
    this.view = null;
  }

  setScope(scope: EditScope): void {
    if (scope === this.scope) return;
    this.scope = scope;
    this.view = null;
  }

  /** Lines off the section are locked now. */
  get active(): boolean {
    return this.scope === "current" && this.test !== null;
  }

  editable(line: EditLine): boolean {
    const test = this.test;
    if (!test || this.scope === "all") return true;
    let v = this.verdict.get(line);
    if (v === undefined) {
      v = test(line.points);
      this.verdict.set(line, v);
    }
    return v;
  }

  /** The lines that may be picked: the layer itself, or a layer of its lines on the section (kept per layer). */
  pickable(layer: EditLayer): EditLayer {
    if (!this.active) return layer;
    if (this.view?.layer === layer) return this.view.lines;
    const lines = layer.lines.filter((l) => this.editable(l));
    const view = lines.length === layer.lines.length ? layer : { lines, nextId: layer.nextId };
    this.view = { layer, lines: view };
    return view;
  }
}
