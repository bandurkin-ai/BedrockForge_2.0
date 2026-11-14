import type { ReactNode } from "react";

/** Inline **bold** and `code`; everything else is plain text (never HTML). */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>;
    return part;
  });
}

const cells = (line: string) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
const isTableRow = (line: string) => line.trim().startsWith("|");
const isDivider = (line: string) => /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.includes("-");
const listItem = (line: string) => /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);

/** The subset of Markdown the assistant uses: headings, lists, tables, code blocks, paragraphs. */
export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") {
      i++;
    } else if (line.trim().startsWith("```")) {
      const code: string[] = [];
      for (i++; i < lines.length && !lines[i].trim().startsWith("```"); i++) code.push(lines[i]);
      i++;
      out.push(<pre key={out.length}>{code.join("\n")}</pre>);
    } else if (/^#{1,6}\s/.test(line)) {
      out.push(<h4 key={out.length}>{inline(line.replace(/^#+\s*/, ""))}</h4>);
      i++;
    } else if (isTableRow(line) && i + 1 < lines.length && isDivider(lines[i + 1])) {
      const head = cells(line);
      const rows: string[][] = [];
      for (i += 2; i < lines.length && isTableRow(lines[i]); i++) rows.push(cells(lines[i]));
      out.push(
        <div className="table-wrap" key={out.length}>
          <table>
            <thead><tr>{head.map((c, k) => <th key={k}>{inline(c)}</th>)}</tr></thead>
            <tbody>{rows.map((row, r) => <tr key={r}>{row.map((c, k) => <td key={k}>{inline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
    } else if (listItem(line)) {
      const ordered = /^\s*\d/.test(line);
      const items: string[] = [];
      for (; i < lines.length && listItem(lines[i]); i++) items.push(listItem(lines[i])![1]);
      const children = items.map((item, k) => <li key={k}>{inline(item)}</li>);
      out.push(ordered ? <ol key={out.length}>{children}</ol> : <ul key={out.length}>{children}</ul>);
    } else {
      const para: string[] = [];
      for (; i < lines.length && lines[i].trim() !== "" && !lines[i].trim().startsWith("```") && !/^#{1,6}\s/.test(lines[i]) && !listItem(lines[i]) && !isTableRow(lines[i]); i++) {
        para.push(lines[i]);
      }
      // A lone "|" line that is not a table still has to make progress.
      if (para.length === 0) para.push(lines[i++]);
      out.push(<p key={out.length}>{inline(para.join(" "))}</p>);
    }
  }
  return <div className="md">{out}</div>;
}
