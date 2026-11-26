import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { allowedLink, inlineText, parseMarkdown, parseMarkdownAppend, type Block, type Inline, type MarkdownDoc } from "../../src/modules/assistant/markdown";

const first = (md: string, streaming = false): Block => parseMarkdown(md, { streaming }).blocks[0];
const inl = (md: string, streaming = false): Inline[] => {
  const b = first(md, streaming);
  if (b.t !== "p") throw new Error(`not a paragraph: ${b.t}`);
  return b.children;
};

describe("inline", () => {
  it("bold, italic, code", () => {
    expect(inl("a **жирный** и *курсив* и `код`")).toEqual([
      { t: "text", text: "a " },
      { t: "strong", children: [{ t: "text", text: "жирный" }] },
      { t: "text", text: " и " },
      { t: "em", children: [{ t: "text", text: "курсив" }] },
      { t: "text", text: " и " },
      { t: "code", text: "код" },
    ]);
  });

  it("an underscore inside an identifier is not emphasis; markers of a calculation are not either", () => {
    expect(inlineText(inl("вызван ui_hole_select и hole_id"))).toBe("вызван ui_hole_select и hole_id");
    expect(inl("ui_hole_select")).toEqual([{ t: "text", text: "ui_hole_select" }]);
    expect(inl("2 * 3 * 4 = 24 и 3*4*5")).toEqual([{ t: "text", text: "2 * 3 * 4 = 24 и 3*4*5" }]);
    expect(inl("_наклон_ скважины")[0]).toEqual({ t: "em", children: [{ t: "text", text: "наклон" }] });
  });

  it("numbers are not reformatted", () => {
    const t = "Au 1 234,56 г/т; Cu 0.0310 %; −12,50; 1e-3; 007";
    expect(inl(t)).toEqual([{ t: "text", text: t }]);
  });

  it("a strong inside an italic and nested content", () => {
    expect(inlineText(inl("*один **два** три*"))).toBe("один два три");
    expect(inl("***всё***")).toEqual([{ t: "strong", children: [{ t: "em", children: [{ t: "text", text: "всё" }] }] }]);
  });

  it("an escaped marker is text; a code span keeps its markers", () => {
    expect(inl("\\*не курсив\\*")).toEqual([{ t: "text", text: "*не курсив*" }]);
    expect(inl("`a **b** c`")).toEqual([{ t: "code", text: "a **b** c" }]);
    expect(inl("`` a`b ``")).toEqual([{ t: "code", text: "a`b" }]);
  });

  it("a line break inside a paragraph", () => {
    expect(inl("первая\nвторая")).toEqual([{ t: "text", text: "первая" }, { t: "br" }, { t: "text", text: "вторая" }]);
  });
});

describe("blocks", () => {
  it("paragraphs and headings", () => {
    const { blocks } = parseMarkdown("# Один\n\nабзац\nещё строка\n\n## Два\n### Три\n#### Четыре\n#нет");
    expect(blocks.map((b) => b.t)).toEqual(["h", "p", "h", "h", "h", "p"]);
    expect(blocks.filter((b) => b.t === "h").map((b) => (b.t === "h" ? b.level : 0))).toEqual([1, 2, 3, 3]);
  });

  it("fenced code: the language, the text as is, an open fence while streaming", () => {
    const b = first("```python\nx = **2**\n\n  y = 3\n```\nпосле");
    expect(b).toEqual({ t: "code", lang: "python", text: "x = **2**\n\n  y = 3", open: false });
    const open = parseMarkdown("до\n\n```\nстрока 1\nстрока 2", { streaming: true }).blocks;
    expect(open[1]).toEqual({ t: "code", lang: null, text: "строка 1\nстрока 2", open: true });
    expect(parseMarkdown("~~~\nа\n~~~").blocks[0]).toMatchObject({ t: "code", open: false, text: "а" });
  });

  it("lists: bullets, numbers from a start, nesting, code inside an item", () => {
    const b = first("3. три\n4. четыре\n   - вложенный\n   - ещё\n5. пять\n   ```sh\n   run\n   ```");
    expect(b.t).toBe("list");
    if (b.t !== "list") return;
    expect(b.ordered).toBe(true);
    expect(b.start).toBe(3);
    expect(b.items).toHaveLength(3);
    const nested = b.items[1].blocks[0];
    expect(nested.t === "list" && nested.items.map((i) => inlineText(i.children))).toEqual(["вложенный", "ещё"]);
    expect(b.items[2].blocks[0]).toEqual({ t: "code", lang: "sh", text: "run", open: false });
    const ul = parseMarkdown("- a\n- b\n\n- c\n* d").blocks;
    expect(ul.map((x) => x.t)).toEqual(["list"]);
  });

  it("a list may follow a line of text without a blank line", () => {
    expect(parseMarkdown("Итоги:\n- один\n- два").blocks.map((b) => b.t)).toEqual(["p", "list"]);
  });

  it("GFM tables: alignment, a ragged row, an escaped pipe, inline in cells", () => {
    const md = "| Скв. | Au, г/т | Прим. |\n|:--|--:|:-:|\n| DH-1 | 1,25 | **ок** |\n| DH-2 | 0,5 |\n| DH-3 | 0 | a \\| b |";
    const b = first(md);
    expect(b.t).toBe("table");
    if (b.t !== "table") return;
    expect(b.align).toEqual(["left", "right", "center"]);
    expect(b.head.map(inlineText)).toEqual(["Скв.", "Au, г/т", "Прим."]);
    expect(b.rows.map((r) => r.map(inlineText))).toEqual([
      ["DH-1", "1,25", "ок"],
      ["DH-2", "0,5", ""],
      ["DH-3", "0", "a | b"],
    ]);
  });

  it("a table needs its delimiter row; an incomplete one is text", () => {
    expect(parseMarkdown("| a | b |").blocks[0].t).toBe("p");
    expect(parseMarkdown("| a | b |\n|--").blocks[0].t).toBe("p");
    expect(parseMarkdown("a | b\n---|---\n1 | 2").blocks[0].t).toBe("table");
  });

  it("quote and rule", () => {
    expect(parseMarkdown("> цитата\n> дальше\n\n---\n\nконец").blocks.map((b) => b.t)).toEqual(["quote", "hr", "p"]);
  });
});

describe("links", () => {
  it("opens only https links of the hosts of the shell", () => {
    expect(allowedLink("https://github.com/org/repo")).toBe("https://github.com/org/repo");
    expect(allowedLink("https://docs.anthropic.com/x")).toBe("https://docs.anthropic.com/x");
    expect(allowedLink("https://claude.ai")).toBe("https://claude.ai/");
    for (const bad of [
      "http://github.com/x",
      "javascript:alert(1)",
      "data:text/html,<b>",
      "https://evil.example.com",
      "https://claude.ai.evil.com/",
      "https://notclaude.ai/",
      "https://claude.ai@evil.com/",
      "https://user:pw@github.com/",
      "https://github.com/a b",
      "//github.com",
      "github.com",
    ]) expect(allowedLink(bad), bad).toBeNull();
  });

  it("a link from the list is a link node, any other is text with its address", () => {
    expect(inl("см. [репозиторий](https://github.com/o/r) тут")[1]).toEqual({ t: "link", href: "https://github.com/o/r", children: [{ t: "text", text: "репозиторий" }] });
    expect(inl("[жми](https://evil.example.com/x)")).toEqual([{ t: "text", text: "жми" }, { t: "text", text: " (https://evil.example.com/x)" }]);
    expect(inl("[x](javascript:alert(1))").some((n) => n.t === "link")).toBe(false);
    expect(inlineText(inl("[x](javascript:alert(1))"))).toBe("x (javascript:alert(1))");
  });

  it("a bare address: a link from the list, trailing punctuation outside; other hosts stay text", () => {
    const nodes = inl("Читайте https://github.com/o/r/issues/5. Ещё https://evil.example.com/z тут");
    expect(nodes[1]).toEqual({ t: "link", href: "https://github.com/o/r/issues/5", children: [{ t: "text", text: "https://github.com/o/r/issues/5" }] });
    expect(nodes.filter((n) => n.t === "link")).toHaveLength(1);
    expect(inlineText(nodes)).toBe("Читайте https://github.com/o/r/issues/5. Ещё https://evil.example.com/z тут");
    expect(inl("<https://claude.ai/docs>")[0]).toMatchObject({ t: "link", href: "https://claude.ai/docs" });
  });
});

describe("no markup is ever produced (S9)", () => {
  const KNOWN = new Set(["text", "strong", "em", "code", "link", "br", "p", "h", "list", "table", "quote", "hr"]);
  const kinds = (v: unknown, into = new Set<string>()): Set<string> => {
    if (Array.isArray(v)) for (const x of v) kinds(x, into);
    else if (v && typeof v === "object") {
      const o = v as Record<string, unknown>;
      if (typeof o.t === "string") into.add(o.t);
      for (const x of Object.values(o)) kinds(x, into);
    }
    return into;
  };

  it("HTML in an answer stays text", () => {
    const md = '<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n\n[a](<javascript:alert(1)>) <b>x</b> &amp; &lt;';
    const doc = parseMarkdown(md);
    for (const k of kinds(doc.blocks)) expect(KNOWN.has(k), k).toBe(true);
    expect(JSON.stringify(doc.blocks)).not.toContain('"href":"javascript');
    const text = doc.blocks.map((b) => (b.t === "p" ? inlineText(b.children) : "")).join("|");
    expect(text).toContain("<script>alert(1)</script>");
    expect(text).toContain('<img src=x onerror="alert(1)">');
  });

  it("the source of the assistant has no raw HTML injection", () => {
    const dir = fileURLToPath(new URL("../../src/modules/assistant", import.meta.url));
    const needle = ["dangerously", "SetInnerHTML"].join("");
    const walk = (d: string): string[] => readdirSync(d).flatMap((f) => (statSync(join(d, f)).isDirectory() ? walk(join(d, f)) : [join(d, f)]));
    const files = walk(dir).filter((f) => /\.(ts|tsx)$/.test(f));
    expect(files.length).toBeGreaterThan(0);
    for (const f of files) {
      const src = readFileSync(f, "utf8");
      expect(src.includes(needle), f).toBe(false);
      expect(/\binnerHTML\b|\bouterHTML\b|insertAdjacentHTML|document\.write/.test(src), f).toBe(false);
    }
  });
});

describe("a stream that ends anywhere", () => {
  it("an unclosed marker of the last block is shown closed only while streaming", () => {
    expect(inl("это **жир", true)).toEqual([{ t: "text", text: "это " }, { t: "strong", children: [{ t: "text", text: "жир" }] }]);
    expect(inl("это **жир", false)).toEqual([{ t: "text", text: "это **жир" }]);
    expect(inl("*курс", true)[0]).toEqual({ t: "em", children: [{ t: "text", text: "курс" }] });
    expect(inl("тут `код", true)[1]).toEqual({ t: "code", text: "код" });
    expect(inl("тут `код", false)).toEqual([{ t: "text", text: "тут `код" }]);
  });

  it("an unclosed marker of a block that is not the last stays text", () => {
    const { blocks } = parseMarkdown("это **жир\n\nследующий абзац", { streaming: true });
    expect(blocks[0]).toEqual({ t: "p", children: [{ t: "text", text: "это **жир" }] });
  });

  it("a half-written link or table is text, never a broken node", () => {
    expect(inlineText(inl("[текст](https://github.com/o", true))).toBe("[текст](https://github.com/o");
    expect(inl("[текст](", true).some((n) => n.t === "link")).toBe(false);
    expect(parseMarkdown("| a | b |\n|---|", { streaming: true }).blocks[0].t).toBe("p");
    expect(parseMarkdown("| a | b |\n|---|-", { streaming: true }).blocks[0].t).toBe("table");
    expect(parseMarkdown("| a | b |\n|---|---|\n| 1", { streaming: true }).blocks[0]).toMatchObject({ t: "table", rows: [[[{ t: "text", text: "1" }], []]] });
  });

  it("every prefix of a text parses without an exception and with known nodes only", () => {
    const text = "# Итоги\n\nСкважина **DH-012** — `Au` *1,25* г/т, [док](https://github.com/o/r).\n\n- а\n  - б\n\n| a | b |\n|:-|-:|\n| 1 | 2 |\n\n```py\nx = 1\n```\n> q";
    for (let n = 0; n <= text.length; n++) {
      const doc = parseMarkdown(text.slice(0, n), { streaming: true });
      expect(Array.isArray(doc.blocks)).toBe(true);
    }
  });
});

describe("a text that grows", () => {
  const text = "# Заголовок\n\nПервый **абзац** с `кодом`.\n\n- один\n- два\n\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\n```js\nlet a = 1;\n\nlet b = 2;\n```\n\nИ последний *абзац*, [ссылка](https://claude.ai/x).\nВторая строка.";

  it("the tail parse gives the same nodes as the whole parse at every length", () => {
    let doc: MarkdownDoc | null = null;
    for (let n = 1; n <= text.length; n++) {
      const part = text.slice(0, n);
      doc = parseMarkdownAppend(doc, part, { streaming: true });
      const whole = parseMarkdown(part, { streaming: true });
      expect(doc.blocks, `length ${n}`).toEqual(whole.blocks);
      expect(doc.starts, `length ${n}`).toEqual(whole.starts);
    }
    const done = parseMarkdownAppend(doc, text, { streaming: false });
    expect(done.blocks).toEqual(parseMarkdown(text).blocks);
  });

  it("the blocks before the last two are the same objects after the next piece", () => {
    const a = parseMarkdownAppend(null, "# Т\n\nабзац один\n\nабзац два\n\n- пункт\n\nабзац три начало", { streaming: true });
    const b = parseMarkdownAppend(a, "# Т\n\nабзац один\n\nабзац два\n\n- пункт\n\nабзац три начало и продолжение", { streaming: true });
    expect(b.blocks).toHaveLength(5);
    expect(b.blocks[0]).toBe(a.blocks[0]);
    expect(b.blocks[1]).toBe(a.blocks[1]);
    expect(b.blocks[2]).toBe(a.blocks[2]);
    expect(b.blocks[4]).not.toBe(a.blocks[4]);
    expect(parseMarkdownAppend(b, b.text, { streaming: true })).toBe(b);
  });

  it("a new line that joins the last block to the one above (an item after a blank line) gives the whole parse", () => {
    const steps = ["- а\n- б\n\n-", "- а\n- б\n\n- ", "- а\n- б\n\n- в", "- а\n- б\n\n- в\n\nконец"];
    let doc: MarkdownDoc | null = null;
    for (const t of steps) {
      doc = parseMarkdownAppend(doc, t, { streaming: true });
      expect(doc.blocks, t).toEqual(parseMarkdown(t, { streaming: true }).blocks);
    }
  });

  it("a text that is not a continuation is parsed whole", () => {
    const a = parseMarkdownAppend(null, "один\n\nдва", { streaming: true });
    const b = parseMarkdownAppend(a, "другое", { streaming: true });
    expect(inlineText((b.blocks[0] as { children: Inline[] }).children)).toBe("другое");
  });
});

describe("speed", () => {
  const para = "Скважина **DH-012** пересекла рудное тело на глубине 123,45 м: содержание Au *1,25* г/т, Cu 0.031 %, см. `data_hole` и [отчёт](https://github.com/o/r). Это обычный абзац ответа с несколькими предложениями и числами 1 234,5 и 6,7.\n\n";
  const list = "- пункт один с **жирным**\n- пункт два с `кодом`\n  - вложенный пункт\n- пункт три\n\n";
  const table = "| Скв. | Au, г/т | Cu, % | Длина, м |\n|:--|--:|--:|--:|\n" + Array.from({ length: 12 }, (_, i) => `| DH-${i} | ${i}.25 | 0.0${i} | ${i + 3} |`).join("\n") + "\n\n";
  const code = "```python\nfor i in range(10):\n    print(i * 2)\n```\n\n";
  const build = (bytes: number): string => {
    let s = "";
    while (s.length < bytes) s += `## Раздел ${s.length}\n\n${para}${list}${table}${code}`;
    return s;
  };
  const best = (f: () => void, n = 7): number => {
    f();
    let m = Infinity;
    for (let i = 0; i < n; i++) {
      const t = performance.now();
      f();
      m = Math.min(m, performance.now() - t);
    }
    return m;
  };

  it("50 KB of Markdown in at most 20 ms", () => {
    const text = build(50 * 1024);
    expect(text.length).toBeGreaterThanOrEqual(50 * 1024);
    const ms = best(() => void parseMarkdown(text));
    expect(ms).toBeLessThanOrEqual(20);
  });

  it("a stream of 2000 pieces is parsed by its tail, not whole", () => {
    const text = build(30 * 1024);
    const step = Math.ceil(text.length / 2000);
    let doc: MarkdownDoc | null = null;
    const t = performance.now();
    for (let n = step; n < text.length + step; n += step) doc = parseMarkdownAppend(doc, text.slice(0, Math.min(n, text.length)), { streaming: true });
    const incremental = performance.now() - t;
    expect(doc?.blocks).toEqual(parseMarkdown(text, { streaming: true }).blocks);
    const whole = best(() => void parseMarkdown(text), 3);
    expect(incremental).toBeLessThan(whole * 2000 * 0.5);
  });

  it("text made of unmatched markers does not take quadratic time", () => {
    const n = 50 * 1024;
    for (const unit of ["*a ", "_a ", "[a", "`", "**a ", "[a](", "<https://x", "| a "]) {
      const text = unit.repeat(Math.ceil(n / unit.length));
      const ms = best(() => void parseMarkdown(text, { streaming: true }), 2);
      expect(ms, JSON.stringify(unit)).toBeLessThan(500);
    }
  });
});
