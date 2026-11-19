/**
 * Buffers of one part of a surface (a wireframe, a DTM), made in the Worker from the BFA1 `mesh` answer of the
 * core (ADR 0011 §3–§4; `viz/mesh/build.ts` does the work): the vertices and the triangles of the answer as
 * they are — decoded without copying, checked, with the extent of the part — go back by transfer of the very
 * buffer that came. Normals are not made: the surface is shaded flat (ADR 0004 §3). Pure: no three.js, no DOM.
 */
import { buildMeshBuffers, decodeMeshPart, type BuiltMesh } from "../mesh/build";

export function buildMesh(buffer: ArrayBuffer): BuiltMesh {
  const { header, xyz, idx } = decodeMeshPart(buffer);
  return buildMeshBuffers(header, xyz, idx);
}

/** The buffers to transfer back to the main thread, each once (both arrays are views of the answer). */
export function meshTransferables(b: BuiltMesh): ArrayBuffer[] {
  const set = new Set<ArrayBuffer>();
  for (const a of [b.positions, b.index]) if (a.buffer instanceof ArrayBuffer) set.add(a.buffer);
  return [...set];
}
