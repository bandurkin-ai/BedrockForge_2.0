// The stub of the core for the tests of Э19б (19.38) on the REAL answers of `core/tests/data/implicit/contract_answers.json` (written by
// `BF_REGEN_IMPLICIT=1 uv run pytest` of 19.36): `model_stack` (the model 14 «Стек и разлом»: four domains, intrusion, a fault, the wings) and `model_veins`
// (the model 34 «Система жил»: a domain and a system of two veins, the line of their intersection), the answers of the four new routes
// (`relations`, `fault_post`, `group_faults`, `vein_system_post`) and of their refusals. The list `GET /models` is made from the details the way the core
// makes it (`store.list_rows`: the groups `{id, name, type}`, the outputs without volumes and WITHOUT the lines). A request is answered by its path; the
// bodies sent are kept in `sent`; `force` makes the next request of a route answer with a given answer.
import { vi } from "vitest";
import { installReal, json, R, type RealCore } from "./real";

export const STACK_ID = R.model_stack.body.id as number;
export const VEINS_ID = R.model_veins.body.id as number;

type Obj = Record<string, unknown>;

/** The row of `GET /models` for a model of the details (what the core's list keeps of it). */
export function listRowOf(info: Obj): Obj {
  const meta = info.meta as Obj;
  const stats = meta.stats as Obj;
  return {
    id: info.id,
    name: info.name,
    version: info.version,
    state: info.state,
    stale: info.stale,
    stale_reason: info.stale_reason,
    source_lost: info.source_lost,
    groups: (meta.groups as Obj[]).map((g) => ({ id: g.id, name: g.name, type: g.type })),
    outputs: (info.outputs as Obj[]).filter((o) => o.wireframe_id !== undefined).map((o) => Object.fromEntries(["wireframe_id", "kind", "group", "wing", "threshold", "name"].map((k) => [k, o[k] ?? null]))),
    stats: { points: stats.points, patches: stats.patches, seconds: stats.seconds },
    created_at: info.created_at,
    updated_at: info.updated_at,
  };
}

export interface StackCore extends RealCore {
  /** the PUT / POST of the routes of Э19б as the core answered them (the bodies sent are in `sent`) */
  prefix: string;
}

export function installStack(models: number[] = [STACK_ID, VEINS_ID]): StackCore {
  const core = installReal() as StackCore;
  const prev = globalThis.fetch;
  core.details.set(STACK_ID, R.model_stack.body);
  core.details.set(VEINS_ID, R.model_veins.body);
  core.list = models.map((id) => listRowOf(core.details.get(id) as Obj));
  core.prefix = "/implicit/models";
  const forced = new Map<string, { status: number; body: unknown }>();
  const force = (route: string, a: { status: number; body: unknown }) => void forced.set(route, a);
  core.force = force;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const route = `${method} ${path}`;
      const f = forced.get(route);
      if (f) {
        forced.delete(route);
        core.sent.push({ method, path, body: init?.body ? (JSON.parse(String(init.body)) as Obj) : {} });
        return json(f.body, f.status);
      }
      const record = () => core.sent.push({ method, path, body: init?.body ? (JSON.parse(String(init.body)) as Obj) : {} });
      if (route === "GET /implicit/defaults") return json(R.defaults.body);
      if (/^PUT \/implicit\/models\/\d+\/relations$/.test(route)) {
        record();
        return json(R.relations.body);
      }
      if (/^POST \/implicit\/models\/\d+\/faults$/.test(route)) {
        record();
        return json(R.fault_post.body);
      }
      if (/^PUT \/implicit\/models\/\d+\/groups\/\d+\/faults$/.test(route)) {
        record();
        return json(R.group_faults.body);
      }
      if (/^PUT \/implicit\/models\/\d+\/groups\/\d+$/.test(route)) {
        record();
        return json({ id: STACK_ID, version: 9, group: { ...((init?.body ? JSON.parse(String(init.body)).group : {}) as Obj), id: Number(route.split("/").pop()) } });
      }
      if (/^PUT \/implicit\/models\/\d+$/.test(route)) {
        record();
        return json({ id: STACK_ID, name: "Стек и разлом", version: 8 });
      }
      if (/^POST \/implicit\/models\/\d+\/vein_system$/.test(route)) {
        record();
        return json(R.vein_system_post.body);
      }
      if (/^GET \/implicit\/models\/\d+\/outputs$/.test(route)) return json(R.outputs_veins.body);
      if (route === "GET /intercepts/codes") return json({ codes: ["INT", "ALV", "SS", "SH", "FLT", "VN", "VN2", "VN3"], total: 8, truncated: false });
      return prev(url, init);
    }),
  );
  return core;
}
