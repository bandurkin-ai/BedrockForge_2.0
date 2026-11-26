// The cards of the calls of tools (Э23, 23.14; ADR 0029 решение 33; docs/progress/e23-api.md §5, §7): a `data_*` call of the core and a command of the window
// (`ui_*`): the title in Russian, the status, the brief result; folded by default; the arguments and the whole result under the fold; a gap is «—», never 0.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { argLines, argValue, briefOf, statusText, ToolCard } from "../../src/modules/assistant/ToolCard";
import type { ToolItem } from "../../src/modules/assistant/wireTypes";

const T0 = "2026-11-26T10:00:00Z";
const tool = (over: Partial<ToolItem> = {}): ToolItem => ({
  kind: "tool",
  id: "t1",
  at: T0,
  tool_use_id: "toolu_1",
  name: "data_summary",
  side: "data",
  title: "Сводка проекта",
  args: {},
  status: "waiting",
  summary: "",
  ...over,
});
const html = (item: ToolItem, actions?: string): string => renderToStaticMarkup(createElement(ToolCard, { item, actions: actions ? createElement("button", { type: "button" }, actions) : undefined }));

describe("a data_* card", () => {
  it("is folded by default, the title is the core's, the status runs while there is no end", () => {
    const out = html(tool());
    expect(out).toContain('data-testid="tool-card"');
    expect(out).toContain('data-side="data"');
    expect(out).toContain("Сводка проекта");
    expect(out).toContain("Выполняется…");
    expect(out).toMatch(/<details[^>]*>/);
    expect(out).not.toMatch(/<details[^>]*\sopen/);
  });

  it("finished: «Готово», the brief of a JSON result is its size, the whole result is under the fold", () => {
    const summary = JSON.stringify({ name: "Демо", counts: { sets: 1, holes: 48 } });
    const out = html(tool({ status: "ok", summary, args: { set_id: 3 } }));
    expect(out).toContain('data-status="ok"');
    expect(out).toContain("Готово");
    expect(out).toContain(`Получено: ${summary.length} зн.`);
    expect(out).toContain("set_id = 3");
    expect(out).toContain("&quot;holes&quot;:48");
  });

  it("an error shows the text of the error as the brief", () => {
    const out = html(tool({ status: "error", summary: "Нет активного набора скважин: укажите set_id\nподробности" }));
    expect(out).toContain('data-status="error"');
    expect(out).toContain("Ошибка");
    expect(briefOf({ status: "error", side: "data", summary: "Нет активного набора скважин: укажите set_id\nподробности" })).toBe("Нет активного набора скважин: укажите set_id");
  });

  it("a text of the result is text, not markup", () => {
    const out = html(tool({ status: "ok", summary: "<img src=x onerror=1> и <script>1</script>" }));
    expect(out).not.toContain("<img");
    expect(out).not.toContain("<script");
    expect(out).toContain("&lt;img src=x onerror=1&gt;");
  });
});

describe("a ui_* card", () => {
  const ui = (over: Partial<ToolItem> = {}) => tool({ name: "ui_hole_select", side: "ui", title: "Выбрать скважину", args: { hole: "DH-012" }, ...over });

  it("waits for the window; the buttons of 23.15 are outside the fold, so they are seen on a folded card", () => {
    const out = html(ui(), "Выполнить");
    expect(out).toContain('data-side="ui"');
    expect(out).toContain("Ожидает выполнения в окне");
    expect(out.indexOf("Выполнить")).toBeGreaterThan(out.indexOf("</details>"));
    expect(html(ui())).not.toContain("Выполнить");
  });

  it("done, declined", () => {
    expect(html(ui({ status: "ok", summary: "Выбрана скважина DH-012" }))).toContain("Выбрана скважина DH-012");
    const declined = html(ui({ status: "declined", summary: "Пользователь отклонил действие" }));
    expect(declined).toContain("Отклонено");
    expect(declined).toContain('data-status="declined"');
  });
});

describe("the parts", () => {
  it("the arguments as key = value; a gap is «—», never 0 or empty", () => {
    expect(argLines({ set_id: 3, search: "DH", with_findings: true, columns: ["AU", "CU"], none: null })).toEqual(["set_id = 3", "search = DH", "with_findings = true", 'columns = ["AU","CU"]', "none = —"]);
    expect(argValue(undefined)).toBe("—");
    expect(argValue("x".repeat(500)).length).toBeLessThanOrEqual(200);
  });

  it("the status of a card", () => {
    expect(statusText({ status: "waiting", side: "data" })).toBe("Выполняется…");
    expect(statusText({ status: "waiting", side: "ui" })).toBe("Ожидает выполнения в окне");
    expect(statusText({ status: "ok", side: "ui" })).toBe("Готово");
    expect(briefOf({ status: "ok", side: "ui", summary: "" })).toBe("Готово");
  });
});
