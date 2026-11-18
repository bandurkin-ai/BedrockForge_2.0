/**
 * The geometry Worker of the 3D window (ADR 0004 §4, ADR 0007 §7): takes the BFA1 answer of the core with
 * ownership, decodes it without copying and builds the line buffers of the traces (`paths.ts`), of a part of
 * a line layer (`lines.ts`) or the checked buffers of a part of a surface (`mesh.ts`), then gives them back by
 * transfer — the main thread only makes the three.js objects.
 */
import { buildLines, lineTransferables } from "./lines";
import { buildMesh, meshTransferables } from "./mesh";
import { buildPaths, transferables } from "./paths";
import type { WorkerRequest, WorkerResponse } from "./client";

interface WorkerScope {
  onmessage: ((e: MessageEvent<WorkerRequest>) => void) | null;
  postMessage(message: WorkerResponse, transfer?: Transferable[]): void;
}

const scope = self as unknown as WorkerScope;

scope.onmessage = (e) => {
  const req = e.data;
  const id = req.id;
  try {
    if (req.kind === "lines") {
      const result = buildLines(req.buffer);
      scope.postMessage({ id, ok: true, result }, lineTransferables(result));
      return;
    }
    if (req.kind === "mesh") {
      const result = buildMesh(req.buffer);
      scope.postMessage({ id, ok: true, result }, meshTransferables(result));
      return;
    }
    const result = buildPaths(req.buffer, req.kinds);
    scope.postMessage({ id, ok: true, result }, transferables(result));
  } catch (err) {
    const v = err as { code?: unknown; message?: unknown; hint?: unknown };
    scope.postMessage({
      id,
      ok: false,
      error: { code: typeof v.code === "string" ? v.code : "worker_error", message: typeof v.message === "string" ? v.message : String(err), hint: typeof v.hint === "string" ? v.hint : "" },
    });
  }
};
