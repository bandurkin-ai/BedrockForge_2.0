/**
 * A raster in the scene (ТЗ 7.19, ADR 0011 §4; BF `setRaster` 1812–1893, `layerRaster` 2297): a quad on the
 * four corners of the image — float32 from an integer origin (`placement.ts`) — with the display copy of the
 * image as its texture. In plan and as an ordinary vertical object it takes the shared clipping planes and
 * the depth test; its opacity blends. Under clipping a vertical raster is a backdrop, as in Micromine:
 * out of the clipping planes, drawn first, without writing or testing depth — holes and lines on either side
 * of it stay on top; it cannot blend there (transparent objects are drawn last), so its opacity mixes the
 * image toward the background instead. The texture belongs to the caller (it outlives a moved raster).
 */
import * as THREE from "three";
import type { Vec3 } from "../core/vec3";
import type { Origin } from "../origin";
import type { LayerPick, PickQuery, SceneLayer } from "../sceneTypes";
import { isBackdrop, pickQuad, type RasterPlacement } from "./placement";

export interface RasterLayerContext {
  clip: THREE.Plane[];
  /** the clear colour of the view (the backdrop dims toward it) */
  background(out: THREE.Color): THREE.Color;
}

export interface RasterStyle {
  /** 0.05…1 */
  opacity: number;
  visible: boolean;
  /** the image stands off the plan (ТЗ 7.19: a scanned section) */
  vertical: boolean;
  /** the display copy has transparent pixels (PNG) */
  alpha: boolean;
}

/** What a click on a raster gives (`LayerHit.hit` of the scene). */
export interface RasterPick {
  rasterId: number;
  /** the place on the image, absolute float64 */
  xyz: Vec3;
}

/** Scores of surfaces start here: any line or point under the cursor (0…1) wins over a raster. */
export const RASTER_SCORE = 2;

const WHITE = new THREE.Color(0xffffff);

export class RasterLayerView implements SceneLayer {
  readonly object = new THREE.Group();
  origin: Origin;
  readonly bbox = new Float64Array(6);
  private readonly mesh: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
  private readonly local = new Float32Array(12);
  private backdrop: boolean | null = null;
  private readonly tint = new THREE.Color();
  private style: RasterStyle;

  constructor(
    readonly rasterId: number,
    placement: RasterPlacement,
    private readonly ctx: RasterLayerContext,
    style: RasterStyle,
  ) {
    this.style = style;
    this.origin = placement.origin;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(this.local, 3));
    // corners: top left, top right, bottom right, bottom left; the texture is not flipped (row 0 is the top)
    geometry.setAttribute("uv", new THREE.BufferAttribute(Float32Array.of(0, 0, 1, 0, 1, 1, 0, 1), 2));
    geometry.setIndex([0, 3, 1, 1, 3, 2]);
    const material = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide, color: 0xffffff });
    // lines and points at the level of the raster stay in front of it
    material.polygonOffset = true;
    material.polygonOffsetFactor = 1;
    material.polygonOffsetUnits = 1;
    this.mesh = new THREE.Mesh(geometry, material);
    this.mesh.visible = false; // until the image comes
    this.object.add(this.mesh);
    this.object.visible = style.visible;
    this.setPlacement(placement);
  }

  /** New corners (the raster moved, the floor changed): the caller tells the scene (`layerChanged`). */
  setPlacement(p: RasterPlacement): void {
    this.origin = p.origin;
    this.bbox.set(p.bbox);
    this.local.set(p.local);
    const position = this.mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
    position.needsUpdate = true;
    this.mesh.geometry.computeBoundingSphere();
  }

  /** The image to show (null — none yet: nothing is drawn). The texture is not disposed here. */
  setTexture(texture: THREE.Texture | null): void {
    const m = this.mesh.material;
    if (m.map === texture) return;
    m.map = texture;
    m.needsUpdate = true;
    this.mesh.visible = texture !== null;
  }

  get texture(): THREE.Texture | null {
    return this.mesh.material.map;
  }

  setStyle(style: RasterStyle): void {
    this.style = style;
    this.object.visible = style.visible;
    this.backdrop = null; // applied again before the next frame
  }

  get visible(): boolean {
    return this.object.visible;
  }

  /** Is it drawn as a backdrop now (a vertical raster under clipping). */
  get isBackdrop(): boolean {
    return isBackdrop(this.style.vertical, this.ctx.clip.length > 0);
  }

  beforeRender(): void {
    const backdrop = this.isBackdrop;
    const m = this.mesh.material;
    if (backdrop !== this.backdrop) {
      this.backdrop = backdrop;
      const blend = !backdrop && (this.style.opacity < 1 || this.style.alpha);
      m.clippingPlanes = backdrop ? null : this.ctx.clip;
      m.depthTest = !backdrop;
      m.depthWrite = !backdrop && !blend;
      m.transparent = blend;
      m.opacity = backdrop ? 1 : this.style.opacity;
      m.alphaTest = backdrop && this.style.alpha ? 0.5 : 0;
      m.color.copy(WHITE);
      this.mesh.renderOrder = backdrop ? -10 : -1;
      m.needsUpdate = true;
    }
    // a dim copy instead of blending: the image mixed toward the background by the opacity
    if (backdrop) m.color.copy(WHITE).lerp(this.ctx.background(this.tint), 1 - this.style.opacity);
  }

  pick(q: PickQuery): LayerPick | null {
    if (!this.mesh.visible) return null;
    const hit = pickQuad(this.local, q.offset, q.vexag, q.ray);
    if (!hit || (!this.isBackdrop && !q.inWindow(hit.point[0], hit.point[1]))) return null;
    const o = this.origin;
    const xyz: Vec3 = [hit.point[0] - q.offset[0] + o.x, hit.point[1] - q.offset[1] + o.y, hit.point[2] / (q.vexag || 1) - q.offset[2] + o.z];
    const pick: RasterPick = { rasterId: this.rasterId, xyz };
    return { score: RASTER_SCORE + hit.t / (1 + hit.t), hit: pick };
  }

  dispose(): void {
    this.mesh.geometry.dispose();
    this.mesh.material.dispose();
    this.object.clear();
  }
}
