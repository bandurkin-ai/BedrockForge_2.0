/**
 * Buffers of one part of a line or point layer, built in the Worker from the BFA1 `lines` answer of the core
 * (ADR 0011 §3–§4; `viz/lines/build.ts` does the work): segments for `LineSegments`, markers for `Points`, the
 * anchors of the texts. The answer buffer is decoded without copying and changed in place (empty heights);
 * what the main thread needs goes back by transfer. Pure: no three.js, no DOM.
 */
import { buildLineBuffers, decodeLinesPart, type BuiltLines } from "../lines/build";

export function buildLines(buffer: ArrayBuffer): BuiltLines {
  const { header, arrays } = decodeLinesPart(buffer);
  return buildLineBuffers(header, arrays);
}

/** The buffers to transfer back to the main thread, each once. */
export function lineTransferables(b: BuiltLines): ArrayBuffer[] {
  const set = new Set<ArrayBuffer>();
  for (const a of [b.positions, b.segStart, b.dots, b.dotPiece, b.line, b.closed, b.labels?.xyz, b.value]) if (a && a.buffer instanceof ArrayBuffer) set.add(a.buffer);
  return [...set];
}
