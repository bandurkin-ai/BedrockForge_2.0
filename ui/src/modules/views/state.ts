/** Saved views of the open project (ТЗ 7.35 Д-09, ADR 0008): the list for the «Виды» section, save, apply. */
import { api } from "../../shell/api";
import { Store } from "../../shell/store";

export interface ViewRow {
  id: number;
  name: string;
  folder: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface Applied {
  camera: unknown;
  values: Record<string, { value: unknown; version: number }>;
}

export const viewsStore = new Store<{ views: ViewRow[]; limit: number; error: string | null }>({ views: [], limit: 200, error: null });

const text = (e: unknown) => (e instanceof Error ? e.message : String(e));

export async function loadViews(): Promise<void> {
  try {
    const r = await api.get<{ views: ViewRow[]; limit: number }>("/views");
    viewsStore.set({ views: r.views, limit: r.limit, error: null });
  } catch (e) {
    viewsStore.set({ views: [], error: text(e) });
  }
}

export function clearViews(): void {
  viewsStore.set({ views: [], error: null });
}

export const byId = (id: number): ViewRow | undefined => viewsStore.get().views.find((v) => v.id === id);

export async function createView(camera: unknown, name?: string): Promise<ViewRow> {
  const r = await api.post<ViewRow>("/views", { camera, ...(name ? { name } : {}) });
  await loadViews();
  return r;
}

export async function patchView(id: number, patch: { name?: string; folder?: string | null; camera?: unknown }): Promise<ViewRow> {
  const cur = byId(id);
  if (!cur) throw new Error("Вид не найден: обновите раздел «Виды»");
  const r = await api.put<ViewRow>(`/views/${id}`, { expected_version: cur.version, ...patch });
  await loadViews();
  return r;
}

export async function deleteView(id: number): Promise<void> {
  await api.del(`/views/${id}`);
  await loadViews();
}

export function applyView(id: number): Promise<Applied> {
  return api.post<Applied>(`/views/${id}/apply`);
}

/** A camera of a saved view as the scene takes it, or null (an old or damaged one: the view keys still apply). */
export function cameraOf(v: unknown): { position: [number, number, number]; target: [number, number, number]; up: [number, number, number]; zoom: number; ortho: boolean; fov: number } | null {
  if (!v || typeof v !== "object") return null;
  const c = v as Record<string, unknown>;
  const vec = (x: unknown): [number, number, number] | null =>
    Array.isArray(x) && x.length === 3 && x.every((n) => typeof n === "number" && Number.isFinite(n)) ? [x[0], x[1], x[2]] : null;
  const position = vec(c.position), target = vec(c.target), up = vec(c.up) ?? [0, 0, 1];
  const zoom = typeof c.zoom === "number" && c.zoom > 0 ? c.zoom : null;
  if (!position || !target || zoom === null) return null;
  return { position, target, up, zoom, ortho: c.ortho !== false, fov: typeof c.fov === "number" ? c.fov : 45 };
}
