/** Handlers of the commands project.* (ТЗ 7.3–7.5). The user gets own dialogs; Claude passes arguments. */
import { bridge } from "../../bridge";
import { CoreError } from "../../shell/api";
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { askConfirm, askText, showMessage } from "../../shell/dialogs";
import { openWindow } from "../../shell/windows";
import { refresh as refreshJobs } from "../jobs/state";
import { createProject, deleteProject, demoProject, projectStore, refreshCurrent, updateProject } from "./state";

const needProject = (ctx: { hasProject: boolean }) => (ctx.hasProject ? null : "Проект не открыт");
const needCore = (ctx: { coreOnline: boolean }) => (ctx.coreOnline ? null : "Ядро недоступно");

function viewport(): [number, number] {
  return typeof window === "undefined" ? [1280, 800] : [window.innerWidth, window.innerHeight];
}

/** Errors of the core reach the log (and Claude) as «message hint»; the user also sees them in a window. */
async function guarded<T>(fn: () => Promise<T>, source: CommandSource = "claude"): Promise<T> {
  try {
    return await fn();
  } catch (e) {
    const text = e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e);
    if (source === "user") await showMessage({ title: "Проекты", message: text });
    throw new Error(text);
  }
}

export function openImport(path: string): void {
  openWindow("project-import", "Импорт проекта", "form", { path }, ...viewport());
}

export function bindProjects(app: App): void {
  const r = app.registry;

  r.bind(
    "project.new",
    async (a, source) => {
      let name = typeof a.name === "string" ? a.name : null;
      if (name === null && source === "user") name = await askText({ title: "Новый проект", label: "Название проекта", okLabel: "Создать" });
      if (!name) return "Отменено";
      const p = await guarded(() => createProject(app, name!), source);
      return `Создан и открыт проект «${p.name}»`;
    },
    needCore,
  );

  r.bind(
    "project.demo",
    async (_a, source) => {
      const p = await guarded(() => demoProject(app), source);
      return p.created ? `Создан демо-проект «${p.name}»` : `Открыт демо-проект «${p.name}»`;
    },
    needCore,
  );

  r.bind(
    "project.rename",
    async (a, source) => {
      const cur = projectStore.get().current;
      if (!cur) throw new Error("Проект не открыт");
      let name = typeof a.name === "string" ? a.name : null;
      if (name === null && source === "user" && typeof a.description !== "string") {
        name = await askText({ title: "Переименовать проект", label: "Новое название", initial: cur.name, okLabel: "Переименовать" });
        if (name === null) return "Отменено";
      }
      const patch: { name?: string; description?: typeof cur.description } = {};
      if (name !== null) patch.name = name;
      if (typeof a.description === "string") patch.description = { ...cur.description, deposit: a.description };
      const p = await guarded(() => updateProject(app, cur.uid, patch), source);
      return `Проект: «${p.name}»`;
    },
    (ctx) => needProject(ctx),
  );

  r.bind(
    "project.delete",
    async (_a, source) => {
      const cur = projectStore.get().current;
      if (!cur) throw new Error("Проект не открыт");
      if (source === "user") {
        const ok = await askConfirm({
          title: "Удалить проект",
          message: `Удалить проект «${cur.name}» со всеми данными: наборы, каркасы, линии, растры, блочные модели, графики, задания и файлы? Отменить удаление нельзя.`,
          okLabel: "Удалить",
          danger: true,
        });
        if (!ok) return "Отменено";
      }
      await guarded(() => deleteProject(app, cur.uid), source);
      return `Проект «${cur.name}» удалён`;
    },
    needProject,
  );

  r.bind("project.manage", () => {
    openWindow("projects", "Проекты", "normal", undefined, ...viewport());
  });

  r.bind(
    "project.export",
    () => {
      openWindow("project-export", "Экспорт проекта", "form", undefined, ...viewport());
      return "Открыто окно экспорта";
    },
    needProject,
  );

  r.bind(
    "project.import",
    async (a) => {
      const path = typeof a.path === "string" ? a.path : await bridge()?.pickOpen();
      if (!path) return "Отменено";
      openImport(path);
      return "Открыто окно импорта";
    },
    needCore,
  );

  r.bind(
    "data.refresh",
    async () => {
      await guarded(async () => {
        await refreshCurrent(app);
        await refreshJobs();
      });
      return "Данные перечитаны";
    },
    needCore,
  );
}
