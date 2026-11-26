/**
 * One message of the feed (Э23, 23.14; ADR 0029 решения 33, 34, S9): the question of the user (plain text), the answer of Claude (the Markdown subset of
 * `markdown.ts` → React elements), a notice, an error. Every string of an answer is a React text node — nothing of it is ever interpreted as markup
 * (no raw HTML injection of any kind, S9). A link is an `<a href>` only for an address of `allowedLink`; the shell opens it in the system browser
 * (`will-navigate` of `app/src/main/main.ts`), the window never navigates (no `window.open`). During a stream the text is parsed with `streaming: true`
 * (`parseMarkdownAppend`: the blocks before the last two keep their identity, so a memoised `BlockView` of them is not touched by the next delta).
 */
import { Fragment, memo, useMemo, useRef, type ReactNode } from "react";
import type { AssistantItem, ErrorItem, NoticeItem, UserItem } from "./wireTypes";
import { allowedLink, parseMarkdownAppend, type Align, type Block, type Inline, type MarkdownDoc } from "./markdown";
import css from "./MessageView.module.css";

function renderInline(nodes: readonly Inline[]): ReactNode[] {
  return nodes.map((n, i) => {
    switch (n.t) {
      case "text":
        return <Fragment key={i}>{n.text}</Fragment>;
      case "strong":
        return <strong key={i}>{renderInline(n.children)}</strong>;
      case "em":
        return <em key={i}>{renderInline(n.children)}</em>;
      case "code":
        return (
          <code key={i} className={css.inlineCode}>
            {n.text}
          </code>
        );
      case "br":
        return <br key={i} />;
      case "link": {
        // the parser keeps only allowed addresses; the view checks them once more
        const href = allowedLink(n.href);
        return href ? (
          <a key={i} href={href} className={css.link} rel="noopener noreferrer" title={href}>
            {renderInline(n.children)}
          </a>
        ) : (
          <Fragment key={i}>{renderInline(n.children)}</Fragment>
        );
      }
    }
  });
}

const alignOf = (a: Align): "left" | "right" | "center" | undefined => a ?? undefined;

const BlockView = memo(function BlockView({ block }: { block: Block }) {
  switch (block.t) {
    case "p":
      return <p className={css.p}>{renderInline(block.children)}</p>;
    case "h":
      return (
        <div className={`${css.h} ${css[`h${block.level}`]}`} role="heading" aria-level={block.level + 2}>
          {renderInline(block.children)}
        </div>
      );
    case "code":
      return (
        <div className={css.codeBox} data-open={block.open ? "true" : undefined}>
          {block.lang ? <span className={css.lang}>{block.lang}</span> : null}
          <pre className={css.pre}>
            <code>{block.text}</code>
          </pre>
        </div>
      );
    case "list": {
      const items = block.items.map((it, i) => (
        <li key={i} className={css.li}>
          {renderInline(it.children)}
          {it.blocks.map((b, j) => (
            <BlockView key={j} block={b} />
          ))}
        </li>
      ));
      return block.ordered ? (
        <ol className={css.list} start={block.start}>
          {items}
        </ol>
      ) : (
        <ul className={css.list}>{items}</ul>
      );
    }
    case "table":
      return (
        <div className={css.tableWrap}>
          <table className={css.table}>
            <thead>
              <tr>
                {block.head.map((c, i) => (
                  <th key={i} style={{ textAlign: alignOf(block.align[i] ?? null) }}>
                    {renderInline(c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((c, i) => (
                    <td key={i} style={{ textAlign: alignOf(block.align[i] ?? null) }}>
                      {renderInline(c)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "quote":
      return <blockquote className={css.quote}>{renderInline(block.children)}</blockquote>;
    case "hr":
      return <hr className={css.hr} />;
  }
});

/** The nodes of a text that grows: each render continues the parse of the previous one (`parseMarkdownAppend`). */
export function useMarkdown(text: string, streaming: boolean): MarkdownDoc {
  const prev = useRef<MarkdownDoc | null>(null);
  return useMemo(() => {
    const doc = parseMarkdownAppend(prev.current, text, { streaming });
    prev.current = doc;
    return doc;
  }, [text, streaming]);
}

export function MarkdownView({ doc }: { doc: MarkdownDoc }) {
  return (
    <>
      {doc.blocks.map((b, i) => (
        <BlockView key={i} block={b} />
      ))}
    </>
  );
}

function AssistantView({ item, streaming }: { item: AssistantItem; streaming: boolean }) {
  const doc = useMarkdown(item.text, streaming);
  return (
    <div className={css.assistant} data-testid="msg-assistant" data-streaming={streaming ? "true" : "false"}>
      <div className={css.who}>Claude</div>
      <div className={css.md}>
        <MarkdownView doc={doc} />
        {streaming ? <span className={css.caret} aria-hidden="true" /> : null}
      </div>
      {item.interrupted ? <div className={css.mark}>Ответ прерван: связь оборвалась.</div> : item.stopped ? <div className={css.mark}>Остановлено.</div> : null}
    </div>
  );
}

function UserView({ item }: { item: UserItem }) {
  return (
    <div className={css.user} data-testid="msg-user">
      {item.text}
    </div>
  );
}

function NoticeView({ item }: { item: NoticeItem }) {
  return (
    <div className={css.notice} role="status" data-testid="msg-notice" data-code={item.code}>
      {item.message}
    </div>
  );
}

function ErrorView({ item }: { item: ErrorItem }) {
  return (
    <div className={css.error} role="alert" data-testid="msg-error" data-code={item.code}>
      <div>{item.message}</div>
      {item.hint ? <div className={css.hint}>{item.hint}</div> : null}
    </div>
  );
}

export interface MessageViewProps {
  item: AssistantItem | UserItem | NoticeItem | ErrorItem;
  /** the answer is still being written (the last answer of a running turn) */
  streaming?: boolean;
}

export const MessageView = memo(function MessageView({ item, streaming = false }: MessageViewProps) {
  switch (item.kind) {
    case "user":
      return <UserView item={item} />;
    case "assistant":
      return <AssistantView item={item} streaming={streaming} />;
    case "notice":
      return <NoticeView item={item} />;
    case "error":
      return <ErrorView item={item} />;
  }
});
