import { test } from "vitest";
import { EditSession } from "../../src/edit/session";
import * as ops from "../../src/edit/ops";
import { fromArrays } from "../../src/edit/model";
test("bench", () => {
  const L = 1000, V = 1000;
  const xyz = new Float64Array(L * V * 3);
  const off = new Uint32Array(L + 1);
  for (let l = 0; l < L; l++) { off[l + 1] = (l + 1) * V; for (let v = 0; v < V; v++) { const k = (l * V + v) * 3; xyz[k] = 5e5 + v; xyz[k + 1] = 7e6 + l; xyz[k + 2] = 100; } }
  const layer = fromArrays(xyz, off, new Uint8Array(L), []);
  const s = new EditSession({ layerId: 1, version: 1, layer } as never);
  const id = s.layer.lines[500].id;
  for (let k = 0; k < 15; k++) {
    const t0 = performance.now();
    const next = ops.reverseLine(s.layer, id);
    const t1 = performance.now();
    s.edit(next, "r");
    const t2 = performance.now();
    console.log(`op ${(t1 - t0).toFixed(2)} edit ${(t2 - t1).toFixed(2)}`);
  }
});
