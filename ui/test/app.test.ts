import { describe, expect, it } from "vitest";
import { createApp } from "../src/shell/app";
import { dialogStore } from "../src/shell/dialogs";
import { windowStore } from "../src/shell/windows";

describe("shell commands of Э1", () => {
  it("panel.set, theme.set, ribbon.toggle change the state and are logged", async () => {
    const app = createApp({ persist: false });
    await app.run("panel.set", { panel: "bottom", tab: "log" });
    expect(app.store.get().workspace.bottom).toBe(true);
    await app.run("theme.set");
    expect(app.store.get().workspace.theme).toBe("light");
    await app.run("ribbon.toggle");
    expect(app.store.get().workspace.ribbonCollapsed).toBe(true);
    const logged = app.registry.entries().map((e) => e.commandId);
    expect(logged).toContain("theme.set");
    expect(logged).not.toContain("ribbon.toggle");
  });

  it("settings and license open floating windows; palette opens with a query", async () => {
    const app = createApp({ persist: false });
    await app.run("settings.open");
    await app.run("license.open");
    await app.run("settings.open");
    expect(windowStore.get().windows.map((w) => w.kind).sort()).toEqual(["license", "settings"]);
    await app.run("palette.open", { query: "скв " });
    expect(app.palette.get().query).toBe("скв ");
  });

  it("a confirm command from Claude waits for the user's dialog", async () => {
    const app = createApp({ persist: false });
    app.registry.bind("project.delete", () => "Удалён", () => null); // available: the test is about the confirmation
    const pending = app.registry.execute("project.delete", {}, "claude");
    await Promise.resolve();
    const req = dialogStore.get().queue.at(-1)!;
    expect(req.kind).toBe("confirm");
    (req.resolve as (ok: boolean) => void)(false);
    expect((await pending).result).toBe("Пользователь отклонил действие");
  });

  it("scene.style validates and stores the view", async () => {
    const app = createApp({ persist: false });
    expect((await app.registry.execute("scene.style", { vexag: 3, grid: false })).ok).toBe(true);
    expect(app.store.get().view).toMatchObject({ vexag: 3, grid: false });
    expect((await app.registry.execute("scene.style", { vexag: 4 })).result).toContain("допустимые значения");
    expect((await app.registry.execute("workspace.state", {}, "claude")).result).toContain("преувеличение 3");
  });
});
