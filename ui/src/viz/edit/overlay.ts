/**
 * The editing layer of the 3D window (ADR 0015 §5; `SceneLayer`, key `edit`): the lines of the working copy in
 * one STATIC_DRAW buffer with a range per line (a changed line is rewritten in place by `bufferSubData`), tie lines dashed, the selected lines
 * over them, the vertex marks of the selected lines and of those near the cursor (≤ 20 000), the selected
 * vertices, the rubber band and the mark of the cursor or of the snap target — float32 from an integer origin
 * (`viz/origin.ts`), clipped like the rest, drawn over the depth of everything else. The box of the selection
 * and the label of the target are drawn over the canvas by the panel (`modules/lines/EditCursor.tsx`).
 */
import * as THREE from "three";
import type { EditLayer, Selection, Vec3 } from "../../edit/model";
import type { Origin } from "../origin";
import type { FrameContext, SceneLayer } from "../sceneTypes";
import { boxOrigin, draftPoints, isTie, layerBbox, markedLines, outOfReach, pointsOf, SegmentBuffer, vertexMarks, type Written } from "./overlayBuild";

export interface OverlayColors {
  line: string;
  selected: string;
  vertex: string;
  cursor: string;
  snap: string;
}

/** What the layer shows: the state of the session at one moment. */
export interface OverlayState {
  layer: EditLayer;
  selection: Selection;
  drawing: { line: number; atStart: boolean } | null;
  polygon: boolean;
  /** the point under the cursor and whether it snapped */
  cursor: Vec3 | null;
  snapped: boolean;
  /** lines near the cursor: their vertices are marked */
  near: readonly number[];
  /** the height a vertex without one is drawn at */
  missingZ: number;
}

const ORDER = 50;

function material(kind: "line" | "dash" | "points", color: string, clip: THREE.Plane[], size = 1): THREE.Material {
  const common = { color: new THREE.Color(color), clippingPlanes: clip, depthTest: false, depthWrite: false, transparent: true };
  if (kind === "points") return new THREE.PointsMaterial({ ...common, size, sizeAttenuation: false });
  if (kind === "dash") return new THREE.LineDashedMaterial({ ...common, dashSize: 1, gapSize: 1 });
  return new THREE.LineBasicMaterial(common);
}

class Segments {
  readonly geometry = new THREE.BufferGeometry();
  readonly object: THREE.LineSegments;
  private attr: THREE.BufferAttribute | null = null;
  private dist: THREE.BufferAttribute | null = null;

  constructor(
    readonly buffer: SegmentBuffer,
    mat: THREE.Material,
  ) {
    this.object = new THREE.LineSegments(this.geometry, mat);
    this.object.renderOrder = ORDER;
    this.object.frustumCulled = false;
  }

  show(w: Written): void {
    const b = this.buffer;
    if (this.attr?.array !== b.positions) {
      this.geometry.dispose();
      this.attr = new THREE.BufferAttribute(b.positions, 3);
      // the first upload of a layer of 1 million vertices (~30 MB) is STATIC_DRAW: DYNAMIC_DRAW of that size takes
      // tens of seconds under SwiftShader (10.12); edits after it go by ranges (`bufferSubData`, below)
      this.attr.setUsage(THREE.StaticDrawUsage);
      this.geometry.setAttribute("position", this.attr);
      if (b.lengths) {
        this.dist = new THREE.BufferAttribute(b.lengths, 1);
        this.geometry.setAttribute("lineDistance", this.dist);
      }
    } else {
      this.attr.clearUpdateRanges();
      for (const [start, count] of w.ranges) this.attr.addUpdateRange(start, count);
      this.attr.needsUpdate = w.all || w.ranges.length > 0;
      if (this.dist) {
        this.dist.clearUpdateRanges();
        for (const [start, count] of w.ranges) this.dist.addUpdateRange(start / 3, count / 3);
        this.dist.needsUpdate = this.attr.needsUpdate;
      }
    }
    this.geometry.setDrawRange(0, b.count * 2);
    this.object.visible = b.count > 0;
  }

  dispose(): void {
    this.geometry.dispose();
    (this.object.material as THREE.Material).dispose();
  }
}

function pointsObject(mat: THREE.Material): THREE.Points {
  const p = new THREE.Points(new THREE.BufferGeometry(), mat);
  p.renderOrder = ORDER + 1;
  p.frustumCulled = false;
  return p;
}

function setPoints(obj: THREE.Points | THREE.LineSegments, xyz: Float32Array): void {
  obj.geometry.dispose();
  obj.geometry = new THREE.BufferGeometry();
  obj.geometry.setAttribute("position", new THREE.BufferAttribute(xyz, 3));
  obj.visible = xyz.length > 0;
}

export class EditOverlay implements SceneLayer {
  readonly object = new THREE.Group();
  origin: Origin;
  bbox: number[] | null = null;
  private readonly lines: Segments;
  private readonly ties: Segments;
  private readonly selected: Segments;
  private readonly marks: THREE.Points;
  private readonly picked: THREE.Points;
  private readonly cursor: THREE.Points;
  private readonly draft: THREE.LineSegments;
  private readonly mats: Record<"cursor" | "snap", THREE.PointsMaterial>;
  /** the box is counted again only for another layer object (a hover changes nothing in it) */
  private boxOf: { layer: EditLayer; missingZ: number; box: number[] | null } | null = null;

  constructor(clip: THREE.Plane[], origin: Origin, colors: OverlayColors, pixelRatio = 1) {
    this.origin = origin;
    this.lines = new Segments(new SegmentBuffer(), material("line", colors.line, clip));
    this.ties = new Segments(new SegmentBuffer(true), material("dash", colors.line, clip));
    this.selected = new Segments(new SegmentBuffer(), material("line", colors.selected, clip));
    this.selected.object.renderOrder = ORDER + 1;
    this.marks = pointsObject(material("points", colors.vertex, clip, 5 * pixelRatio));
    this.picked = pointsObject(material("points", colors.selected, clip, 8 * pixelRatio));
    this.mats = { cursor: material("points", colors.cursor, clip, 9 * pixelRatio) as THREE.PointsMaterial, snap: material("points", colors.snap, clip, 11 * pixelRatio) as THREE.PointsMaterial };
    this.cursor = pointsObject(this.mats.cursor);
    this.cursor.renderOrder = ORDER + 2;
    this.draft = new THREE.LineSegments(new THREE.BufferGeometry(), material("line", colors.cursor, clip));
    this.draft.renderOrder = ORDER;
    this.draft.frustumCulled = false;
    this.object.add(this.lines.object, this.ties.object, this.selected.object, this.draft, this.marks, this.picked, this.cursor);
  }

  /** New colours (the theme or the colour of the layer changed). */
  setColors(c: OverlayColors): void {
    const set = (o: THREE.Object3D & { material: THREE.Material | THREE.Material[] }, color: string) => ((o.material as THREE.LineBasicMaterial).color = new THREE.Color(color));
    set(this.lines.object, c.line);
    set(this.ties.object, c.line);
    set(this.selected.object, c.selected);
    set(this.picked, c.selected);
    set(this.marks, c.vertex);
    set(this.draft, c.cursor);
    this.mats.cursor.color = new THREE.Color(c.cursor);
    this.mats.snap.color = new THREE.Color(c.snap);
  }

  /** Shows the state; true — the box or the origin changed (the scene re-places the layer and counts its extent). */
  update(s: OverlayState): boolean {
    const known = this.boxOf;
    const box = known && known.layer === s.layer && Object.is(known.missingZ, s.missingZ) ? known.box : layerBbox(s.layer, s.missingZ);
    this.boxOf = { layer: s.layer, missingZ: s.missingZ, box };
    let moved = false;
    if (outOfReach(this.origin, box)) {
      this.origin = boxOrigin(box as number[]);
      moved = true;
    }
    const o = this.origin;
    const normal = s.layer.lines.filter((l) => !isTie(l));
    const ties = s.layer.lines.filter(isTie);
    this.lines.show(this.lines.buffer.update(normal, o, s.missingZ));
    this.ties.show(this.ties.buffer.update(ties, o, s.missingZ));
    const chosen = new Set(s.selection.lines);
    const sel = s.layer.lines.filter((l) => chosen.has(l.id));
    this.selected.show(this.selected.buffer.update(sel, o, s.missingZ));
    const drawing = s.drawing ? s.drawing.line : null;
    setPoints(this.marks, vertexMarks(s.layer, markedLines(s.selection, drawing, s.near), o, s.missingZ));
    const pickedPts: Vec3[] = [];
    for (const v of s.selection.vertices) {
      const p = s.layer.lines.find((l) => l.id === v.line)?.points[v.index];
      if (p) pickedPts.push(p);
    }
    setPoints(this.picked, pointsOf(pickedPts, o, s.missingZ));
    setPoints(this.draft, pointsOf(draftPoints(s.layer, s.drawing, s.polygon, s.cursor), o, s.missingZ));
    setPoints(this.cursor, pointsOf(s.cursor ? [s.cursor] : [], o, s.missingZ));
    this.cursor.material = s.snapped ? this.mats.snap : this.mats.cursor;
    const changed = moved || !sameBox(this.bbox, box);
    this.bbox = box;
    return changed;
  }

  /** Dashes of tie lines about 8 px long whatever the zoom. */
  beforeRender(ctx: FrameContext): void {
    const ppu = ctx.pixelsPerUnit(ctx.camera.target);
    const m = this.ties.object.material as THREE.LineDashedMaterial;
    const dash = 8 / Math.max(ppu, 1e-9);
    m.dashSize = dash;
    m.gapSize = dash * 0.6;
  }

  dispose(): void {
    this.lines.dispose();
    this.ties.dispose();
    this.selected.dispose();
    for (const p of [this.marks, this.picked, this.draft]) {
      p.geometry.dispose();
      (p.material as THREE.Material).dispose();
    }
    this.cursor.geometry.dispose();
    this.mats.cursor.dispose();
    this.mats.snap.dispose();
  }
}

function sameBox(a: ArrayLike<number> | null, b: ArrayLike<number> | null): boolean {
  if (!a || !b) return a === b;
  for (let i = 0; i < 6; i++) if (a[i] !== b[i]) return false;
  return true;
}
