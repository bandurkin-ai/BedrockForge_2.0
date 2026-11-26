// The messages of the panel «Claude» (Э23, 23.14; ADR 0029 решения 33, 34, S9): the Markdown subset of an answer drawn as React elements — a table, a code
// block, a list, a block that is not closed yet — and nothing of it is ever markup (no HTML injection, no link outside the list of the shell).
// The components are drawn with `renderToStaticMarkup` (the tests of the window have no DOM; the same as `test/pit/panel.test.ts`).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MessageView } from "../../src/modules/assistant/MessageView";
import type { AssistantItem, ErrorItem, Item, NoticeItem, UserItem } from "../../src/modules/assistant/wireTypes";

const T0 = "2026-11-26T10:00:00Z";
const answer = (text: string, over: Partial<AssistantItem> = {}): AssistantItem => ({ kind: "assistant", id: "a1", at: T0, model: "claude-opus-5-5", text, stopped: false, interrupted: false, ...over });
const html = (item: AssistantItem | UserItem | NoticeItem | ErrorItem, streaming = false): string => renderToStaticMarkup(createElement(MessageView, { item, streaming }));

describe("Markdown of an answer", () => {
  it("a table: header, rows, alignment; the numbers are as written", () => {
    const out = html(answer("| Скважина | Глубина, м |\n|:--|--:|\n| DH-012 | 152.40 |\n| DH-013 | 0.50 |"));
    expect(out).toContain("<table");
    expect(out).toContain("<th");
    expect(out).toMatch(/<th[^>]*>Скважина<\/th>/);
    expect(out).toMatch(/text-align:right/);
    expect(out).toContain(">152.40<");
    expect(out).toContain(">0.50<");
  });

  it("a code block keeps its text and its language; a list is a list; the heading is a heading", () => {
    const out = html(answer("## Итог\n\n- первая\n- вторая\n\n1. раз\n2. два\n\n```python\nprint('a < b')\n```"));
    expect(out).toContain('role="heading"');
    expect(out).toContain("<ul");
    expect(out).toContain("<ol");
    expect(out).toContain("<li");
    expect(out).toContain("<pre");
    expect(out).toContain("print(&#x27;a &lt; b&#x27;)");
    expect(out).toContain(">python<");
  });

  it("bold, italic and inline code", () => {
    const out = html(answer("**жирный**, *курсив* и `код`"));
    expect(out).toContain("<strong>жирный</strong>");
    expect(out).toContain("<em>курсив</em>");
    expect(out).toMatch(/<code[^>]*>код<\/code>/);
  });

  it("a block that is not closed yet: shown while the stream goes, literal after it", () => {
    const text = "Смотрите:\n\n```\nSELECT 1";
    const live = html(answer(text), true);
    expect(live).toContain("SELECT 1");
    expect(live).toContain('data-open="true"');
    expect(live).toContain('data-streaming="true"');
    const done = html(answer("Значение **не закрыто"), false);
    expect(done).toContain("**не закрыто");
    expect(done).not.toContain("<strong>");
    expect(done).toContain('data-streaming="false"');
  });

  it("a stopped or an interrupted answer says so", () => {
    expect(html(answer("текст", { stopped: true }))).toContain("Остановлено");
    expect(html(answer("текст", { interrupted: true }))).toContain("Ответ прерван");
  });
});

describe("no markup is ever produced (S9)", () => {
  it("HTML in an answer is text", () => {
    const out = html(answer('<script>alert(1)</script> <img src=x onerror="alert(2)"> <b>x</b>'));
    expect(out).not.toContain("<script");
    expect(out).not.toContain("<img");
    expect(out).not.toContain("<b>");
    expect(out).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(out).toContain("&lt;img src=x onerror=&quot;alert(2)&quot;&gt;");
  });

  it("HTML in a table cell, a code block, a heading and a quote is text", () => {
    const out = html(answer("# <i>h</i>\n\n> <u>q</u>\n\n| <s>a</s> |\n|--|\n| <iframe src=x></iframe> |\n\n```\n<svg onload=1>\n```"));
    for (const tag of ["<i>", "<u>", "<s>", "<iframe", "<svg"]) expect(out, tag).not.toContain(tag);
    expect(out).toContain("&lt;iframe src=x&gt;&lt;/iframe&gt;");
    expect(out).toContain("&lt;svg onload=1&gt;");
  });

  it("only the links of the list of the shell are links; the rest stays text with its address", () => {
    const ok = html(answer("[справка](https://docs.anthropic.com/a?b=1&c=2)"));
    expect(ok).toContain('<a href="https://docs.anthropic.com/a?b=1&amp;c=2"');
    expect(ok).toContain('rel="noopener noreferrer"');
    expect(ok).not.toContain("target=");
    for (const bad of ["[x](javascript:alert(1))", "[x](http://anthropic.com)", "[x](https://evil.example/p)", "[x](data:text/html;base64,AAAA)", "[x](https://user:pw@claude.ai/)"]) {
      const out = html(answer(bad));
      expect(out, bad).not.toContain("<a ");
      expect(out, bad).not.toContain("href=");
    }
    expect(html(answer("[x](https://evil.example/p)"))).toContain("https://evil.example/p");
  });

  it("the question of the user and the texts of a notice and an error are text too", () => {
    expect(html({ kind: "user", id: "u1", at: T0, text: "<script>1</script>\nвторая строка" })).toContain("&lt;script&gt;1&lt;/script&gt;");
    const notice = html({ kind: "notice", id: "n1", at: T0, code: "temporary", message: "<b>Диалог</b> не сохранится" });
    expect(notice).toContain("&lt;b&gt;");
    expect(notice).toContain('role="status"');
    const err = html({ kind: "error", id: "e1", at: T0, code: "assistant_offline", message: "Нет связи с Anthropic", hint: "Проверьте интернет" });
    expect(err).toContain('role="alert"');
    expect(err).toContain("Нет связи с Anthropic");
    expect(err).toContain("Проверьте интернет");
  });
});

describe("the feed is drawn from the items the reducer gives", () => {
  it("a long answer with every block draws without a gap", () => {
    const parts: Item[] = [answer("Первый абзац.\n\nВторой абзац со **сводкой**:\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- пункт\n- ещё пункт")];
    const out = parts.map((p) => html(p as AssistantItem)).join("");
    expect(out).toContain("Первый абзац.");
    expect(out).toContain("<strong>сводкой</strong>");
    expect(out).toContain("<table");
    expect(out).toContain("<li");
  });
});
