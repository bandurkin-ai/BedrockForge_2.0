// A CLICK on «Прогноз» of the window «Проектная скважина» (Э19б, 19.40в): the button asks the core with the source and the set of the tab — before the fix it
// sent `planned.evaluate {id}` alone, and the command, with no source in the arguments, only opened the tab. No DOM here: the window is rendered to a string,
// and the handlers of the elements with a test id are caught on their way through the JSX runtime, so the test calls the very `onClick` the person would.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext } from "../src/shell/AppContext";
import type { App } from "../src/shell/app";
import { PlannedWindow } from "../src/modules/drillholes/PlannedWindow";
import { plannedCheck, plannedStore, plannedWin, setCheckSet, setSource } from "../src/modules/drillholes/plannedState";
import { FIRST, installCore, MODEL, newApp, SET, teardown, waitFor, type Core } from "./plannedHarness";

type Jsx = (type: unknown, props: Record<string, unknown> | null, ...rest: unknown[]) => unknown;
const { clicks, catching } = vi.hoisted(() => {
  const clicks = new Map<string, () => void>();
  const catching =
    (f: Jsx): Jsx =>
    (type, props, ...rest) => {
      const id = props?.["data-testid"];
      if (typeof id === "string" && typeof props?.onClick === "function") clicks.set(id, props.onClick as () => void);
      return f(type, props, ...rest);
    };
  return { clicks, catching };
});
vi.mock("react/jsx-runtime", async (orig) => {
  const m = await orig<Record<string, Jsx>>();
  return { ...m, jsx: catching(m.jsx), jsxs: catching(m.jsxs) };
});
vi.mock("react/jsx-dev-runtime", async (orig) => {
  const m = await orig<Record<string, Jsx>>();
  return { ...m, jsxDEV: catching(m.jsxDEV) };
});

let app: App;
let core: Core;
beforeEach(async () => {
  clicks.clear();
  core = installCore();
  app = newApp();
  await waitFor(() => plannedStore.get().loaded);
});
afterEach(teardown);

const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const render = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(PlannedWindow, { winId: "w1" })));
const click = (testid: string): void => {
  const f = clicks.get(testid);
  if (!f) throw new Error(`no handler of ${testid}`);
  f();
};
const evaluate = () => requests(`POST /planned_holes/${FIRST.id}/evaluate`);

describe("the button «Прогноз»", () => {
  it("a click with a model asks the core: POST /planned_holes/{id}/evaluate with the model and the set of the tab", async () => {
    await app.registry.execute("planned.evaluate", { id: FIRST.id }, "user"); // the tab opens, nothing is asked yet
    setSource({ kind: "model", modelId: MODEL });
    render();
    expect(evaluate()).toHaveLength(0);
    click("pl-evaluate");
    await waitFor(() => evaluate().length === 1);
    expect(evaluate()[0].body).toEqual({ model_id: MODEL, set_id: SET });
    await waitFor(() => plannedCheck.get().evaluate.phase === "ok");
    expect(plannedCheck.get().evaluate.answer).not.toBeNull();
  });

  it("closed wireframes by their numbers; the nearest holes alone (no bodies) with the set", async () => {
    await app.registry.execute("planned.evaluate", { id: FIRST.id }, "user");
    setSource({ kind: "meshes", meshIds: [50, 51] });
    render();
    click("pl-evaluate");
    await waitFor(() => evaluate().length === 1);
    expect(evaluate()[0].body).toEqual({ mesh_ids: [50, 51], set_id: SET });
    setSource({ kind: "none" });
    render();
    click("pl-evaluate");
    await waitFor(() => evaluate().length === 2);
    expect(evaluate()[1].body).toEqual({ set_id: SET });
  });

  it("the actual hole chosen for «План / факт» is not lost by a forecast of the same set", async () => {
    await app.registry.execute("planned.evaluate", { id: FIRST.id }, "user");
    setCheckSet(SET);
    plannedCheck.set({ hole: "H001" });
    setSource({ kind: "model", modelId: MODEL });
    render();
    click("pl-evaluate");
    await waitFor(() => plannedCheck.get().evaluate.phase === "ok");
    expect(plannedCheck.get().hole).toBe("H001");
    expect(plannedWin.get().id).toBe(FIRST.id);
  });
});
