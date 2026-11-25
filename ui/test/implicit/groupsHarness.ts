// The stub of the core for the windows of the groups of a model (Э19, 19.24): over the stub of `harness.ts` (the defaults and the ranges that
// `spec.py` wrote, the list of the models, the jobs) it answers the model with shells, a vein and an expansion in its groups and with outputs,
// `POST` / `PUT …/groups` (the version of the model is checked, the group gets the next id), the preview of the points of a group, the results of
// the composites and their fields, `to_wireframe`, `DELETE` of the model and the dependents of `GET /lineage/impact`. The forms are those of the
// contract (docs/progress/e19-api.md §3–§5); the real answers of 19.16 replace `contract_answers.json` later, the types do not change.
import { vi } from "vitest";
import { CANONICAL, ID, installCore, json, NAME, type Core } from "./harness";

export const GID = { shell: 4, vein: 5, expand: 6 };
export const RESULT = { id: 31, table: 21 };

const group = (id: number, name: string, type: string, codes: string[], params: Record<string, unknown>) => ({ id, name, type, codes, relation: "conformable", faults: [], params });

export const SHELL = group(GID.shell, "Au индикатор", "shell", [], { source: "composites", result_id: RESULT.id, set_id: null, table: null, field: "AU", transform: "linear", mode: "indicator", thresholds: [0.5, 1.0], p: 0.5 });
export const VEIN = group(GID.vein, "Жила 1", "vein", ["Q1"], { bound: "polygon", polygon: [[0, 0], [400, 0], [400, 150], [0, 150]], offset: 0, t_min: 0.3 });
export const EXPAND = group(GID.expand, "Рудное тело +5", "expand", [], { wireframe_id: ID.wire, delta: 5 });

const output = (id: number, kind: string, group: number | null, name: string, threshold: number | null = null) => ({ wireframe_id: id, kind, group, wing: null, threshold, name, volume: kind === "body" ? 1250000 : null, area: 81234.5, triangles: 162004 });
export const OUTPUTS = [output(301, "surface", 1, `${NAME} · Дайка · поверхность`), output(302, "body", 1, `${NAME} · Дайка`), output(305, "body", null, `${NAME} · Прочее`)];

export interface GroupsCore extends Core {
  /** the groups the model has besides the domains: what `GET /models/7` and the writes show */
  groups: Record<string, unknown>[];
  stale: { stale: boolean; reason: string | null; lost: boolean };
  /** the dependents of the model for «Влияние изменения» */
  impact: number;
  /** DELETE answers 409 `in_use` with these consumers */
  usage: { id: number; kind: string; name: string; role: string }[] | null;
  /** the results of `to_wireframe` and the group writes are in `sent` */
}

export function installGroupsCore(): GroupsCore {
  const core = installCore() as GroupsCore;
  core.groups = [SHELL, VEIN, EXPAND];
  core.stale = { stale: false, reason: null, lost: false };
  core.impact = 0;
  core.usage = null;
  const prev = globalThis.fetch;
  const forced = new Map<string, { status: number; body: Record<string, unknown> }>();
  core.force = (route, a) => void forced.set(route, a); // the next request of a route answers with it (mine or the base stub's)
  const details = () => ({
    id: ID.model,
    name: NAME,
    version: core.version,
    meta: { ...CANONICAL, groups: [...(CANONICAL.groups as unknown[]), ...core.groups], outputs: OUTPUTS },
    state: "ready",
    error: null,
    stale: core.stale.stale,
    stale_reason: core.stale.reason,
    source_lost: core.stale.lost,
    outputs: OUTPUTS,
    created_at: "2026-11-25T12:00:00Z",
    updated_at: "2026-11-25T12:05:00Z",
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      const route = `${method} ${path}`;
      const record = () => core.sent.push({ method, path, query: u.search, body });
      const f = forced.get(route);
      if (f) {
        forced.delete(route);
        record();
        return json(f.body, f.status);
      }
      const conflict = () => json({ version: core.version, code: "version_conflict", message: "Модель изменена другим окном.", hint: "Перечитайте модель или сохраните как новую." }, 409);
      if (route === `GET /implicit/models/${ID.model}`) {
        record();
        return json(details());
      }
      if (route === `POST /implicit/models/${ID.model}/groups`) {
        record();
        if (body.expected_version !== core.version) return conflict();
        const all = [...(CANONICAL.groups as { id: number }[]), ...(core.groups as { id: number }[])];
        const made = { id: Math.max(...all.map((g) => g.id)) + 1, relation: "conformable", faults: [], codes: [], params: {}, ...(body.group as object) };
        core.groups.push(made);
        core.version += 1;
        return json({ id: ID.model, version: core.version, group: made });
      }
      const put = new RegExp(`^PUT /implicit/models/${ID.model}/groups/(\\d+)$`).exec(route);
      if (put) {
        record();
        if (body.expected_version !== core.version) return conflict();
        const at = core.groups.findIndex((g) => g.id === Number(put[1]));
        if (at < 0) return json({ code: "group_not_found", message: "Группа не найдена.", hint: "Обновите модель." }, 404);
        core.groups[at] = { ...(body.group as object), id: Number(put[1]) };
        core.version += 1;
        return json({ id: ID.model, version: core.version, group: core.groups[at] });
      }
      if (route === `POST /implicit/models/${ID.model}/points/preview`) {
        record();
        return json({ group: null, n: 120, truncated: false, counts: { contact: 80, inside: 20, outside: 20, manual: 0, value: 0, merged: 2, dropped_log: 0, no_position: 0 }, notes: ["Совпавшие точки слиты."], points: { x: [], y: [], z: [], f: [], kind: [] } });
      }
      if (route === `POST /implicit/models/${ID.model}/to_wireframe`) {
        record();
        return json({ wireframe_id: 340, name: (body.name as string) || `${NAME} · Дайка (копия)` });
      }
      if (route === `DELETE /implicit/models/${ID.model}`) {
        record();
        if (core.usage) return json({ code: "in_use", message: "Модель используется.", hint: "Удалите зависимые объекты.", usage: core.usage }, 409);
        core.models = core.models.filter((m) => m.id !== ID.model);
        return json({ deleted: ID.model, wireframes: OUTPUTS.map((o) => o.wireframe_id) });
      }
      if (route === "GET /lineage/impact") {
        record();
        const n = core.impact;
        return json({ total: n, by_kind: n ? { wireframe: n } : {}, nodes: Array.from({ length: n }, (_, i) => ({ kind: "wireframe", id: 400 + i, sub: "", name: `Копия ${i + 1}`, depth: 1 })), truncated: false });
      }
      if (route === `GET /composites/results`) {
        record();
        return json({ results: [{ id: RESULT.id, set_id: ID.set, table_id: RESULT.table, table: "Композиты 2 м", op: "composite", op_title: "Композиты", makes_table: true, name: "Композиты 2 м", params: {}, inputs: {}, sha256: "", algo: "", report: {}, written: [], job_id: null, created_at: null, stale: false, reason: null }] });
      }
      if (route === `GET /drillholes/sets/${ID.set}/tables/${RESULT.table}`) return json({ columns: [{ key: "AU", name: "AU", kind: "number", unit: "г/т" }, { key: "HOLE", name: "HOLE", kind: "text", unit: null }] });
      return prev(url, init);
    }),
  );
  return core;
}
