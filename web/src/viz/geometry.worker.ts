/// <reference lib="webworker" />
import { load, type LoadRequest } from "./geometryLoader";

self.addEventListener("message", async (e: MessageEvent<LoadRequest>) => {
  const result = await load(e.data);
  (self as unknown as DedicatedWorkerGlobalScope).postMessage(result, result.buffers ?? []);
});
