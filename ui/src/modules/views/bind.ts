/** Commands views.* (ТЗ 7.35 Д-09, ADR 0008): save the view of the 3D window, apply one at a time, manage. */
import { CoreError } from "../../shell/api";
import type { App } from "../../shell/app";
import type { CommandContext } from "../../shell/commands";
import { projectStore } from "../projects/state";
import { viewSyncOf } from "../scene/bind";
import { currentScene } from "../scene/sceneHost";
import { applyView, byId, cameraOf, clearViews, createView, deleteView, loadViews, patchView, viewsStore } from "./state";

const asText = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));
const needProject = (ctx: CommandContext) => (ctx.hasProject ? null : "Откройте или создайте проект");

/** The camera now, in absolute coordinates; the pending view keys are written first (the core snapshots them). */
async function current(): Promise<unknown> {
  const camera = currentScene()?.worldCamera() ?? null;
  if (!camera) throw new Error("В 3D-окне нет объектов: сохранять нечего");
  await viewSyncOf()?.flushAll();
  return camera;
}

function view(a: Record<string, unknown>) {
  const id = Number(a.view_id);
  const v = byId(id);
  if (!v) throw new Error(`Вид №${id} не найден`);
  return v;
}

async function guard<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (e) {
    throw new Error(asText(e));
  }
}

export function bindViews(app: App): void {
  const r = app.registry;
  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    if (cur) void loadViews();
    else clearViews();
  });

  r.bind(
    "views.save",
    async (a) => {
      const camera = await current();
      const saved = await guard(() => createView(camera, typeof a.name === "string" && a.name.trim() ? a.name.trim() : undefined));
      return `Сохранён вид «${saved.name}»`;
    },
    (ctx) => needProject(ctx) ?? (viewsStore.get().views.length >= viewsStore.get().limit ? `В проекте уже ${viewsStore.get().limit} видов` : null),
  );

  r.bind(
    "views.apply",
    async (a) => {
      const v = view(a);
      const out = await guard(() => applyView(v.id));
      viewSyncOf()?.adopt(out.values);
      const camera = cameraOf(out.camera);
      const scene = currentScene();
      if (camera && scene) scene.setWorldCamera(camera);
      return `Вид «${v.name}» применён`;
    },
    needProject,
  );

  r.bind(
    "views.update",
    async (a) => {
      const v = view(a);
      const camera = await current();
      await guard(() => patchView(v.id, { camera }));
      return `Вид «${v.name}» перезаписан текущим`;
    },
    needProject,
  );

  r.bind(
    "views.rename",
    async (a) => {
      const v = view(a);
      const name = String(a.name ?? "").trim();
      if (!name) throw new Error("Название вида не может быть пустым");
      await guard(() => patchView(v.id, { name }));
      return `Вид «${v.name}» переименован в «${name}»`;
    },
    needProject,
  );

  r.bind(
    "views.folder",
    async (a) => {
      const v = view(a);
      const folder = typeof a.folder === "string" && a.folder.trim() ? a.folder.trim() : null;
      await guard(() => patchView(v.id, { folder }));
      return folder ? `Вид «${v.name}» — в папке «${folder}»` : `Вид «${v.name}» — в корне раздела`;
    },
    needProject,
  );

  r.bind(
    "views.delete",
    async (a) => {
      const v = view(a);
      await guard(() => deleteView(v.id));
      return `Вид «${v.name}» удалён`;
    },
    needProject,
  );
}
