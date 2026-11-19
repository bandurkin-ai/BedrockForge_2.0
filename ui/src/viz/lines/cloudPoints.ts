/**
 * The points of a cloud on the video card (ADR 0014 §2): `THREE.Points` of 2 px without a rim, the colour of
 * a point is the palette 256×1 (a texture) at its `value u1`; a code with alpha 0 (a hidden class) is moved
 * out of the clip volume in the vertex shader, so it is not rasterized at all. One palette and one material
 * per layer, shared by every part and level: a change of the mode or of the hidden classes is one upload of
 * 1 KB, the buffers stay. The shared clipping planes hold, as for every material of the scene.
 */
import * as THREE from "three";
import { shownCodes } from "./cloud";

/** The points of a cloud, px (an ordinary layer of points — 5 px with a rim). */
export const CLOUD_POINT_PX = 2;

export class CloudPalette {
  readonly texture: THREE.DataTexture;
  readonly material: THREE.PointsMaterial;
  private readonly data = new Uint8Array(256 * 4);
  /** 1 — the code is drawn and picked */
  shown = new Uint8Array(256).fill(1);

  constructor(clip: THREE.Plane[], pixelRatio: number) {
    this.texture = new THREE.DataTexture(this.data, 256, 1, THREE.RGBAFormat, THREE.UnsignedByteType);
    this.texture.magFilter = THREE.NearestFilter;
    this.texture.minFilter = THREE.NearestFilter;
    this.texture.generateMipmaps = false;
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.texture.needsUpdate = true;
    const material = new THREE.PointsMaterial({ color: 0xffffff, size: CLOUD_POINT_PX * pixelRatio, sizeAttenuation: false });
    material.clippingPlanes = clip;
    const palette = { value: this.texture };
    material.onBeforeCompile = (shader) => {
      shader.uniforms.bfPalette = palette;
      shader.vertexShader = shader.vertexShader
        .replace("#include <common>", "#include <common>\nattribute float bfValue;\nuniform sampler2D bfPalette;\nvarying vec3 bfColor;")
        .replace(
          "#include <fog_vertex>",
          "#include <fog_vertex>\n\tvec4 bfC = texture2D(bfPalette, vec2((bfValue + 0.5) / 256.0, 0.5));\n\tbfColor = bfC.rgb;\n\tif (bfC.a < 0.5) gl_Position = vec4(2.0, 2.0, 2.0, 1.0);",
        );
      shader.fragmentShader = shader.fragmentShader
        .replace("#include <common>", "#include <common>\nvarying vec3 bfColor;")
        .replace("#include <color_fragment>", "#include <color_fragment>\n\tdiffuseColor.rgb *= bfColor;");
    };
    material.customProgramCacheKey = () => "bf-cloud";
    this.material = material;
  }

  /** New colours or visibility of the codes (`cloud.cloudPalette`); true — something changed. */
  set(rgba: Uint8Array): boolean {
    if (rgba.length !== this.data.length) throw new Error("cloud: the palette is 256 RGBA colours");
    let same = true;
    for (let i = 0; i < rgba.length && same; i++) same = rgba[i] === this.data[i];
    if (same) return false;
    this.data.set(rgba);
    this.shown = shownCodes(this.data);
    this.texture.needsUpdate = true;
    return true;
  }

  dispose(): void {
    this.texture.dispose();
    this.material.dispose();
  }
}

/**
 * The points of one level of a part: the buffers of the Worker as they are, the material of the layer. The
 * bounding sphere comes from the extent of the part (`box`, its own coordinates) — half a million points are
 * not walked on the main thread for it.
 */
export function makeCloudPoints(positions: Float32Array, value: Uint8Array, palette: CloudPalette, box: { min: readonly number[]; max: readonly number[] } | null): THREE.Points {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("bfValue", new THREE.BufferAttribute(value, 1));
  if (box) {
    const b = new THREE.Box3(new THREE.Vector3(box.min[0], box.min[1], box.min[2]), new THREE.Vector3(box.max[0], box.max[1], box.max[2]));
    geometry.boundingBox = b;
    geometry.boundingSphere = b.getBoundingSphere(new THREE.Sphere());
  } else geometry.computeBoundingSphere();
  return new THREE.Points(geometry, palette.material);
}
