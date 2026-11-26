/**
 * The Markdown subset of the answers of Claude → a tree of nodes (Э23; ADR 0029 решение 34, S9). No dependencies, no HTML: a view turns the nodes
 * into React elements, every string is a text node, so nothing of the answer is ever interpreted as markup.
 *
 * The subset: paragraphs, headings `#`–`###` (deeper ones are level 3), `**bold**`, `*italic*` / `_italic_`, `` `code` ``, fenced code blocks, lists
 * (ordered, unordered, nested), GFM tables, quotes, a rule; links `[text](https://…)` and bare `https://…` only for the hosts of the shell
 * (`LINK_HOSTS`, as in `app/src/main/protocol.ts`), any other link stays text («текст (адрес)»). Numbers and text are never reformatted.
 *
 * A stream may end anywhere: with `streaming` the unclosed construction of the last block is shown as closed (`**кусок` — bold, an open fence —
 * a code block with `open: true`); without it an unclosed marker stays literal. `parseMarkdownAppend` parses only the tail of a growing text:
 * the blocks before the last one keep their identity (a memoised view of them is not touched by the next delta).
 */

export type Inline =
  | { t: "text"; text: string }
  | { t: "strong"; children: Inline[] }
  | { t: "em"; children: Inline[] }
  | { t: "code"; text: string }
  | { t: "link"; href: string; children: Inline[] }
  | { t: "br" };

export type Align = "left" | "right" | "center" | null;

export interface ListItem {
  children: Inline[];
  /** nested lists and code blocks of the item */
  blocks: Block[];
}

export type Block =
  | { t: "p"; children: Inline[] }
  | { t: "h"; level: 1 | 2 | 3; children: Inline[] }
  | { t: "code"; lang: string | null; text: string; open: boolean }
  | { t: "list"; ordered: boolean; start: number; items: ListItem[] }
  | { t: "table"; align: Align[]; head: Inline[][]; rows: Inline[][][] }
  | { t: "quote"; children: Inline[] }
  | { t: "hr" };

export interface MarkdownDoc {
  text: string;
  blocks: Block[];
  /** the line each block starts at */
  starts: number[];
  streaming: boolean;
}

/** Hosts of the links that open (the same list as `isAllowedExternal` of the shell: `app/src/main/protocol.ts`). */
export const LINK_HOSTS: readonly string[] = ["anthropic.com", "claude.ai", "claude.com", "github.com"];

/** The address the shell opens: `https`, no credentials, a host of the list or its subdomain; null — not a link. */
export function allowedLink(raw: string): string | null {
  const url = raw.trim();
  if (!url || /[\s\u0000-\u001f<>"\\]/.test(url)) return null;
  try {
    const u = new URL(url);
    if (u.protocol !== "https:" || u.username || u.password) return null;
    return LINK_HOSTS.some((d) => u.hostname === d || u.hostname.endsWith(`.${d}`)) ? u.href : null;
  } catch {
    return null;
  }
}

// ---- inline ----

const isSpace = (c: string | undefined): boolean => c === undefined || /\s/.test(c);
const isAlnum = (c: string | undefined): boolean => c !== undefined && /[\p{L}\p{N}]/u.test(c);

/** The text a list of inline nodes shows (tests, search, the title of a dialog). */
export function inlineText(nodes: readonly Inline[]): string {
  let s = "";
  for (const n of nodes) s += n.t === "text" || n.t === "code" ? n.text : n.t === "br" ? "\n" : inlineText(n.children);
  return s;
}

/** The end of a run of backticks of exactly `k` after `from`, or -1. */
function closingTicks(s: string, from: number, k: number): number {
  const mark = "`".repeat(k);
  let j = s.indexOf(mark, from);
  while (j >= 0) {
    let m = k;
    while (s[j + m] === "`") m++;
    if (m === k) return j;
    j = s.indexOf(mark, j + m);
  }
  return -1;
}

/** The `]` that closes the `[` at `open`, or -1 (brackets nest, `\]` does not count; a label is short: 1000 characters at most, on one line). */
function closingBracket(s: string, open: number): number {
  let depth = 0;
  for (let j = open; j < Math.min(s.length, open + 1000); j++) {
    const c = s[j];
    if (c === "\\") j++;
    else if (c === "[") depth++;
    else if (c === "]" && --depth === 0) return j;
    else if (c === "\n") return -1;
  }
  return -1;
}

/** The `)` of `(url)` that starts at `open`, or -1 (parentheses of the address nest). */
function closingParen(s: string, open: number): number {
  let depth = 0;
  for (let j = open; j < Math.min(s.length, open + 2000); j++) {
    const c = s[j];
    if (c === "(") depth++;
    else if (c === ")" && --depth === 0) return j;
    else if (c === "\n") return -1;
  }
  return -1;
}

const ESCAPABLE = "\\`*_[]|";

function parseInline(s: string, streaming: boolean, inLink = false, depth = 0): Inline[] {
  const out: Inline[] = [];
  let buf = "";
  const flush = () => {
    if (buf) out.push({ t: "text", text: buf });
    buf = "";
  };
  // a search for a closer that failed from here on fails for every later opener too (no quadratic scan of unmatched markers)
  const exhausted = new Map<string, number>();
  const special = /[\\`*_[<\n]|https:\/\//g;
  let i = 0;
  while (i < s.length) {
    special.lastIndex = i;
    const m = special.exec(s);
    if (!m) {
      buf += s.slice(i);
      break;
    }
    buf += s.slice(i, m.index);
    i = m.index;
    const c = s[i];
    if (c === "\n") {
      buf = buf.replace(/ +$/, "");
      flush();
      out.push({ t: "br" });
      i++;
    } else if (c === "\\") {
      if (i + 1 < s.length && ESCAPABLE.includes(s[i + 1])) {
        buf += s[i + 1];
        i += 2;
      } else {
        buf += c;
        i++;
      }
    } else if (c === "`") {
      let k = 1;
      while (s[i + k] === "`") k++;
      const j = closingTicks(s, i + k, k);
      if (j >= 0) {
        let code = s.slice(i + k, j);
        if (code.length > 2 && code.startsWith(" ") && code.endsWith(" ") && code.trim()) code = code.slice(1, -1);
        flush();
        out.push({ t: "code", text: code });
        i = j + k;
      } else if (streaming && i + k < s.length) {
        flush();
        out.push({ t: "code", text: s.slice(i + k) });
        i = s.length;
      } else {
        buf += "`".repeat(k);
        i += k;
      }
    } else if (c === "*" || c === "_") {
      let k = 1;
      while (s[i + k] === c && k < 3) k++;
      const prev = s[i - 1];
      const next = s[i + k];
      const canOpen = depth < 6 && !isSpace(next) && (c === "*" && k >= 2 ? true : !isAlnum(prev));
      const key = `${c}${k}`;
      let close = -1;
      if (canOpen && i + k < (exhausted.get(key) ?? Infinity)) {
        for (let j = s.indexOf(c, i + k); j >= 0; j = s.indexOf(c, j)) {
          let run = 1;
          while (s[j + run] === c) run++;
          const valid = run === k && s[j - 1] !== "\\" && !isSpace(s[j - 1]) && (c === "*" || !isAlnum(s[j + run]));
          if (valid && j > i + k) {
            close = j;
            break;
          }
          j += run;
        }
        if (close < 0) exhausted.set(key, i + k);
      }
      const wrap = (inner: Inline[]): Inline => (k === 1 ? { t: "em", children: inner } : k === 2 ? { t: "strong", children: inner } : { t: "strong", children: [{ t: "em", children: inner }] });
      if (close >= 0) {
        flush();
        out.push(wrap(parseInline(s.slice(i + k, close), false, inLink, depth + 1)));
        i = close + k;
      } else if (canOpen && streaming) {
        flush();
        out.push(wrap(parseInline(s.slice(i + k), true, inLink, depth + 1)));
        i = s.length;
      } else {
        buf += c.repeat(k);
        i += k;
      }
    } else if (c === "[" && !inLink) {
      const close = closingBracket(s, i);
      if (close > 0 && s[close + 1] === "(") {
        const end = closingParen(s, close + 1);
        if (end > 0) {
          const label = s.slice(i + 1, close);
          const target = s.slice(close + 2, end).replace(/\s+"[^"]*"\s*$/, "");
          const href = allowedLink(target);
          flush();
          if (href) out.push({ t: "link", href, children: parseInline(label, false, true, depth + 1) });
          else out.push(...parseInline(label, false, true, depth + 1), { t: "text", text: ` (${target.trim()})` });
          i = end + 1;
          continue;
        }
      }
      buf += c;
      i++;
    } else if (c === "<" || c === "h") {
      // `<https://…>` and a bare `https://…`: a link when the host is of the list, else text
      const at = c === "<" ? i + 1 : i;
      if (!s.startsWith("https://", at) || (c === "h" && (isAlnum(s[i - 1]) || inLink))) {
        buf += c;
        i++;
        continue;
      }
      let end = at;
      while (end < s.length && !/[\s<>"]/.test(s[end])) end++;
      let url = s.slice(at, end);
      if (c === "<" && s[end] !== ">") {
        buf += c;
        i++;
        continue;
      }
      if (c === "h") {
        while (/[.,;:!?'”»]$/.test(url) || (url.endsWith(")") && !url.includes("("))) url = url.slice(0, -1);
        end = at + url.length;
      }
      const stop = c === "<" ? end + 1 : end;
      const href = allowedLink(url);
      if (href) {
        flush();
        out.push({ t: "link", href, children: [{ t: "text", text: url }] });
      } else buf += s.slice(i, stop);
      i = stop;
    } else {
      buf += c;
      i++;
    }
  }
  flush();
  return out;
}

// ---- blocks ----

const FENCE = /^(\s*)(`{3,}|~{3,})\s*([^\s`]*)[^`]*$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/;
const RULE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const DELIMITER = /^\|?:?-+:?(\|:?-+:?)*\|?$/;

const blank = (l: string): boolean => l.trim() === "";
/** Only blank lines are left from `i`: the block that ended here is the last one of the text (a stream may still extend it). */
const atEnd = (lines: string[], i: number): boolean => {
  for (; i < lines.length; i++) if (!blank(lines[i])) return false;
  return true;
};
const indentOf = (l: string): number => l.length - l.trimStart().length;

/** Cells of a table row; `\|` is a pipe inside a cell. */
function splitRow(line: string): string[] {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  const cells: string[] = [];
  let cur = "";
  for (let i = 0; i < row.length; i++) {
    if (row[i] === "\\" && row[i + 1] === "|") {
      cur += "|";
      i++;
    } else if (row[i] === "|") {
      cells.push(cur.trim());
      cur = "";
    } else cur += row[i];
  }
  cells.push(cur.trim());
  return cells;
}

function tableAt(lines: string[], i: number): boolean {
  return i + 1 < lines.length && lines[i].includes("|") && lines[i + 1].includes("|") && DELIMITER.test(lines[i + 1].replace(/\s+/g, "")) && splitRow(lines[i]).length === splitRow(lines[i + 1]).length;
}

function startsBlock(lines: string[], i: number): boolean {
  const l = lines[i];
  if (FENCE.test(l) || HEADING.test(l) || RULE.test(l) || QUOTE.test(l) || tableAt(lines, i)) return true;
  const m = ITEM.exec(l);
  return m !== null && (!/\d/.test(m[2][0]) || parseInt(m[2], 10) === 1);
}

/** A fenced block from line `i`; `strip` columns of indent (a block inside a list item) are cut from its lines. */
function parseFence(lines: string[], i: number, strip = 0): { block: Block; next: number } {
  const m = FENCE.exec(lines[i]) as RegExpExecArray;
  const mark = m[2];
  const body: string[] = [];
  let j = i + 1;
  let closed = false;
  for (; j < lines.length; j++) {
    const t = lines[j].trim();
    const run = t.length - t.replace(/^[`~]+/, "").length;
    if (t[0] === mark[0] && run >= mark.length && t.length === run) {
      closed = true;
      j++;
      break;
    }
    body.push(strip ? lines[j].slice(Math.min(strip, indentOf(lines[j]))) : lines[j]);
  }
  return { block: { t: "code", lang: m[3] || null, text: body.join("\n"), open: !closed }, next: j };
}

function parseList(lines: string[], start: number, streaming: boolean): { block: Block; next: number } {
  const first = ITEM.exec(lines[start]) as RegExpExecArray;
  const base = first[1].length;
  const ordered = /\d/.test(first[2][0]);
  const items: ListItem[] = [];
  let i = start;
  while (i < lines.length) {
    let j = i;
    while (j < lines.length && blank(lines[j])) j++;
    if (j >= lines.length) break;
    const head = ITEM.exec(lines[j]);
    if (!head || head[1].length !== base || /\d/.test(head[2][0]) !== ordered) break;
    i = j + 1;
    const text = [head[3]];
    const blocks: Block[] = [];
    while (i < lines.length) {
      let k = i;
      while (k < lines.length && blank(lines[k])) k++;
      if (k >= lines.length || indentOf(lines[k]) <= base) break;
      i = k;
      if (ITEM.test(lines[i])) {
        const sub = parseList(lines, i, streaming);
        blocks.push(sub.block);
        i = sub.next;
      } else if (FENCE.test(lines[i])) {
        const f = parseFence(lines, i, indentOf(lines[i]));
        blocks.push(f.block);
        i = f.next;
      } else text.push(lines[i++].trim());
    }
    items.push({ children: parseInline(text.join("\n"), streaming && atEnd(lines, i), false), blocks });
  }
  const n = parseInt(first[2], 10);
  return { block: { t: "list", ordered, start: ordered && n >= 0 ? n : 1, items }, next: i };
}

function parseTable(lines: string[], start: number): { block: Block; next: number } {
  const head = splitRow(lines[start]).map((c) => parseInline(c, false));
  const align: Align[] = splitRow(lines[start + 1]).map((c) => (c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : null));
  const rows: Inline[][][] = [];
  let i = start + 2;
  while (i < lines.length && !blank(lines[i]) && lines[i].includes("|") && !FENCE.test(lines[i]) && !HEADING.test(lines[i])) {
    const cells = splitRow(lines[i]);
    rows.push(head.map((_, c) => parseInline(cells[c] ?? "", false)));
    i++;
  }
  return { block: { t: "table", align, head, rows }, next: i };
}

function parseLines(lines: string[], streaming: boolean): { blocks: Block[]; starts: number[] } {
  const blocks: Block[] = [];
  const starts: number[] = [];
  let i = 0;
  const add = (b: Block, at: number) => {
    blocks.push(b);
    starts.push(at);
  };
  while (i < lines.length) {
    const l = lines[i];
    if (blank(l)) {
      i++;
      continue;
    }
    const at = i;
    let h: RegExpExecArray | null;
    let q: RegExpExecArray | null;
    if (FENCE.test(l)) {
      const f = parseFence(lines, i);
      add(f.block, at);
      i = f.next;
    } else if ((h = HEADING.exec(l))) {
      add({ t: "h", level: Math.min(h[1].length, 3) as 1 | 2 | 3, children: parseInline(h[2], streaming && atEnd(lines, i + 1)) }, at);
      i++;
    } else if (RULE.test(l)) {
      add({ t: "hr" }, at);
      i++;
    } else if (tableAt(lines, i)) {
      const t = parseTable(lines, i);
      add(t.block, at);
      i = t.next;
    } else if (ITEM.test(l)) {
      const li = parseList(lines, i, streaming);
      add(li.block, at);
      i = li.next;
    } else if ((q = QUOTE.exec(l))) {
      const text = [q[1]];
      i++;
      while (i < lines.length && (q = QUOTE.exec(lines[i]))) {
        text.push(q[1]);
        i++;
      }
      add({ t: "quote", children: parseInline(text.join("\n"), streaming && atEnd(lines, i)) }, at);
    } else {
      const text = [l.trim()];
      i++;
      while (i < lines.length && !blank(lines[i]) && !startsBlock(lines, i)) text.push(lines[i++].trim());
      add({ t: "p", children: parseInline(text.join("\n"), streaming && atEnd(lines, i)) }, at);
    }
  }
  return { blocks, starts };
}

const toLines = (text: string): string[] => text.replace(/\r\n?/g, "\n").replace(/\t/g, "    ").split("\n");

/** The nodes of a whole text. */
export function parseMarkdown(text: string, opts: { streaming?: boolean } = {}): MarkdownDoc {
  const streaming = opts.streaming ?? false;
  return { text, ...parseLines(toLines(text), streaming), streaming };
}

/**
 * The nodes of a text that grew since `prev` (a stream): the last two blocks and what follows them are parsed again (a new line may join the last
 * block to the one before it: `-` alone is a paragraph, `- ` makes it an item of the list above), the blocks before them are the same objects as
 * in `prev`. A text that is not a continuation of `prev` is parsed whole.
 */
export function parseMarkdownAppend(prev: MarkdownDoc | null, text: string, opts: { streaming?: boolean } = {}): MarkdownDoc {
  const streaming = opts.streaming ?? false;
  if (prev && prev.text === text && prev.streaming === streaming) return prev;
  if (!prev || prev.blocks.length === 0 || !text.startsWith(prev.text) || text.includes("\r")) return parseMarkdown(text, opts);
  const keep = Math.max(0, prev.blocks.length - 2);
  const from = prev.starts[keep];
  const tail = parseLines(toLines(text).slice(from), streaming);
  return {
    text,
    blocks: [...prev.blocks.slice(0, keep), ...tail.blocks],
    starts: [...prev.starts.slice(0, keep), ...tail.starts.map((s) => s + from)],
    streaming,
  };
}
