/**
 * Collars of a part as points (ТЗ 7.15; BF `setDrillholes` 531–540, `applyCollarStyle` 690–722): the marker
 * of the set's CollarStyle, a colour by the audit of each hole or one for the set, a size in pixels, and the
 * hole names as labels. Positions are float32 from the part origin (the collar in float64 minus the integer
 * origin), so the point is within 0.5 mm of the data. The material takes the shared clipping planes.
 */
import * as THREE from "three";
import type { CollarStyle } from "./collarStyle";
import { collarColors } from "./colors";
import type { HolesManifest, HolesPart } from "./manifest";
import type { MarkerTextures } from "./markers";
import { LABEL_PRIORITY } from "../labels/declutter";
import { makeLabel, type LabelCache, type LabelItem } from "../labels/sprites";

/** Labels with hole names per set at most (BF MAX_COLLAR_LABELS). */
export const MAX_COLLAR_LABELS = 3000;

/** Collar positions of a part: float32 from the part origin, by slot. */
export function collarPositions(m: HolesManifest, part: HolesPart): Float32Array {
  const idx = m.byPart[part.k];
  const out = new Float32Array(idx.length * 3);
  const [ox, oy, oz] = part.origin;
  for (let s = 0; s < idx.length; s++) {
    const i = idx[s] * 3;
    out[s * 3] = m.collar[i] - ox;
    out[s * 3 + 1] = m.collar[i + 1] - oy;
    out[s * 3 + 2] = m.collar[i + 2] - oz;
  }
  return out;
}

export function partAudit(m: HolesManifest, part: HolesPart): Uint8Array {
  const idx = m.byPart[part.k];
  const out = new Uint8Array(idx.length);
  for (let s = 0; s < idx.length; s++) out[s] = m.auditLevel[idx[s]];
  return out;
}

export class CollarPoints {
  readonly points: THREE.Points;
  readonly positions: Float32Array;
  private readonly audit: Uint8Array;
  labels: LabelItem[] = [];
  readonly labelGroup = new THREE.Group();

  constructor(
    private readonly manifest: HolesManifest,
    readonly part: HolesPart,
    clip: THREE.Plane[],
  ) {
    this.positions = collarPositions(manifest, part);
    this.audit = partAudit(manifest, part);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(this.positions, 3));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(this.positions.length), 3));
    g.computeBoundingSphere();
    const material = new THREE.PointsMaterial({ size: 10, sizeAttenuation: false, vertexColors: true, transparent: true, alphaTest: 0.4, clippingPlanes: clip });
    this.points = new THREE.Points(g, material);
    this.points.renderOrder = 3;
    this.labelGroup.renderOrder = 10;
  }

  get count(): number {
    return this.audit.length;
  }

  /**
   * Marker, colour, size and labels. `labelBudget` — how many names this part may still label (the set
   * keeps to MAX_COLLAR_LABELS); gives the number used.
   */
  apply(style: CollarStyle, textures: MarkerTextures, pixelRatio: number, labels: LabelCache, labelBudget: number): number {
    const material = this.points.material as THREE.PointsMaterial;
    material.size = style.size * pixelRatio;
    material.map = textures.get(style.shape);
    material.needsUpdate = true;
    const colors = this.points.geometry.getAttribute("color") as THREE.BufferAttribute;
    (colors.array as Float32Array).set(collarColors(this.audit, style.color));
    colors.needsUpdate = true;
    this.labelGroup.clear(); // label materials are shared and stay in the cache
    this.labels = [];
    if (!style.label || labelBudget <= 0) return 0;
    const font = { size: style.fontSize, family: style.fontFamily, bold: style.bold, color: style.labelColor };
    const shift = style.size / 2 + 3;
    const idx = this.manifest.byPart[this.part.k];
    const n = Math.min(idx.length, labelBudget);
    const at = new THREE.Vector3();
    for (let s = 0; s < n; s++) {
      at.set(this.positions[s * 3], this.positions[s * 3 + 1], this.positions[s * 3 + 2]);
      const item = makeLabel(labels, this.manifest.names[idx[s]], font, at, shift, LABEL_PRIORITY.name);
      this.labelGroup.add(item.sprite);
      this.labels.push(item);
    }
    return n;
  }

  dispose(): void {
    this.labelGroup.clear();
    this.points.geometry.dispose();
    (this.points.material as THREE.Material).dispose();
  }
}
