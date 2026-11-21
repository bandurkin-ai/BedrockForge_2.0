/**
 * The subtab «Партии» (ТЗ 8.4.2; ADR 0020 §4, §10, §11): the batches (LAB, BATCH) of the active element by date —
 * records, CRM, blanks, their failures, the status and its reasons; the head says how many batches failed, how many
 * are «не оценены» and how many records have no batch. A batch fails on a failed CRM (beyond k₃ or two in a row) or
 * a failed blank; a run fails none; one without assessed control samples is «не оценена», not the norm.
 * The table is a window of rows: only the rows in sight are drawn, and only their pages of `POST /qaqc/calc/batches
 * {offset, limit}` (200 rows, the core allows ≤ 5000) are asked — 20 thousand batches are never asked at once.
 * A click on a batch (or Enter) shows it on the charts and sets the filter to it (`qaqc.filter`).
 */
import { useCallback, useEffect, useReducer, useRef, useState, useSyncExternalStore, type KeyboardEvent, type ReactElement } from "react";
import { useApp } from "../../shell/AppContext";
import { errorText } from "../../shell/props";
import { calc, type BatchesAnswer, type BatchRow, type CalcBody } from "./api";
import { dateMs, dateText, int, labTitle, pct, share } from "./format";
import { calcBody, calcKey, qaqcStore, reportCounts, setHighlight, type Highlight } from "./state";
import css from "./Qaqc.module.css";

/** Rows of one request of the batches. */
export const BATCH_PAGE = 200;
/** The height of a row of the table, px (the line 21 and its border). */
export const ROW_H = 22;
/** Rows drawn beyond the sight on each side. */
export const OVERSCAN = 8;

/** The rows drawn for the scroll: [from, to), the sight and `overscan` rows on each side. */
export function rowWindow(scrollTop: number, height: number, total: number, rowH = ROW_H, overscan = OVERSCAN): { from: number; to: number } {
  if (total <= 0) return { from: 0, to: 0 };
  const top = Math.max(0, scrollTop);
  const from = Math.max(0, Math.floor(top / rowH) - overscan);
  const to = Math.min(total, Math.ceil((top + Math.max(height, rowH)) / rowH) + overscan);
  return { from: Math.min(from, to), to };
}

/** The pages that hold the rows [from, to). */
export function pagesOf(from: number, to: number, page = BATCH_PAGE): number[] {
  if (to <= from) return [];
  const out: number[] = [];
  for (let p = Math.floor(from / page); p * page < to; p++) out.push(p);
  return out;
}

/** The status of a batch: 2 — отказ, 0 — норма, −1 (or none) — не оценена. */
export function batchStatusTitle(status: number | null | undefined): string {
  if (typeof status !== "number" || !Number.isFinite(status) || status < 0) return "не оценена";
  return status >= 2 ? "отказ" : status >= 1 ? "предупреждение" : "норма";
}

const plural = (n: number, one: string, few: string, many: string) => {
  const m10 = n % 10;
  const m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
};

/** Why the batch has its status, in Russian: the failed CRM and blanks, or why it is not assessed; «» — the norm. */
export function batchReasons(b: BatchRow): string {
  const out: string[] = [];
  if (b.crm_fail > 0) out.push(`отказ CRM: ${int(b.crm_fail)} из ${int(b.crm)}`);
  if (b.blank_fail > 0) out.push(`отказ ${plural(b.blank_fail, "бланка", "бланков", "бланков")}: ${int(b.blank_fail)} из ${int(b.blanks)}`);
  if (out.length) return out.join("; ");
  if (batchStatusTitle(b.status) !== "не оценена") return "";
  return b.crm + b.blanks > 0 ? "CRM и бланки не оценены (нет справочника CRM или DL)" : "нет CRM и бланков";
}

/**
 * The arguments of `qaqc.filter` that leave only this batch: its name and its laboratory (when it has one), as
 * JSON arrays in text (the parameters are text; a name may hold a comma).
 */
export function batchFilterArgs(b: Pick<BatchRow, "lab" | "batch">): Record<string, string> {
  const batches = JSON.stringify([b.batch]);
  return b.lab ? { batches, labs: JSON.stringify([b.lab]) } : { batches };
}

/** «01.02.2024 — 05.02.2024», one date when they are equal, «—» without. */
export function batchDates(b: Pick<BatchRow, "date_from" | "date_to">): string {
  const a = dateText(dateMs(b.date_from));
  const z = dateText(dateMs(b.date_to));
  return a === z ? a : `${a} — ${z}`;
}

export type BatchesHead = Omit<BatchesAnswer, "batches"> & { not_assessed?: number };

/**
 * The pages of the batches of one calculation: the head and the first page come with the first request, a page is
 * asked once when its rows come in sight; `dispose` drops the answers on their way (a new calculation).
 */
export class BatchPager {
  head: BatchesHead | null = null;
  error: string | null = null;
  /** requests made (the tests) */
  requests = 0;
  private readonly pages = new Map<number, BatchRow[]>();
  private readonly flying = new Map<number, Promise<void>>();
  private disposed = false;

  constructor(
    private readonly body: CalcBody,
    private readonly onChange: () => void = () => undefined,
    readonly page = BATCH_PAGE,
  ) {}

  /** The batches after the filter; unknown before the first answer — the first page. */
  get total(): number {
    return this.head ? this.head.total : this.page;
  }

  row(i: number): BatchRow | undefined {
    return this.pages.get(Math.floor(i / this.page))?.[i % this.page];
  }

  has(p: number): boolean {
    return this.pages.has(p);
  }

  private load(p: number): Promise<void> {
    const flying = this.flying.get(p);
    if (flying) return flying;
    this.requests++;
    const run = calc("batches", { ...this.body, offset: p * this.page, limit: this.page }).then(
      (a) => {
        if (this.disposed) return;
        const { batches, ...head } = a;
        this.head = head;
        this.error = null;
        this.pages.set(p, (batches ?? []).slice(0, this.page));
        this.flying.delete(p);
        this.onChange();
      },
      (e: unknown) => {
        if (this.disposed) return;
        this.flying.delete(p);
        this.error = errorText(e);
        this.onChange();
      },
    );
    this.flying.set(p, run);
    return run;
  }

  /** Asks the pages of the rows [from, to) not asked yet (the first page first: it tells how many there are). */
  async ensure(from: number, to: number): Promise<void> {
    if (this.disposed) return;
    if (!this.head) await this.load(0);
    if (this.disposed || !this.head) return;
    const want = pagesOf(Math.max(0, from), Math.min(to, this.head.total)).filter((p) => !this.pages.has(p));
    await Promise.all(want.map((p) => this.load(p)));
  }

  dispose(): void {
    this.disposed = true;
  }
}

const COLUMNS = ["Лаборатория", "Партия", "Даты", "Записей", "CRM", "Бланков", "Отказ CRM", "Отказ бланков", "Статус", "Причины"];

const sameBatch = (h: Highlight | null, b: BatchRow) => !!h && h.batch === b.batch && (h.lab ?? null) === (b.lab ?? null);

export interface BatchWindowProps {
  total: number;
  from: number;
  to: number;
  row: (i: number) => BatchRow | undefined;
  chosen: Highlight | null;
  onPick: (b: BatchRow) => void;
}

/** The rows [from, to) of the table placed in a box of the height of all of them; a row not read yet — «…». */
export function BatchWindow({ total, from, to, row, chosen, onPick }: BatchWindowProps) {
  const rows: ReactElement[] = [];
  for (let i = from; i < to; i++) {
    const b = row(i);
    if (!b) {
      rows.push(
        <div key={i} className={`${css.bgrid} ${css.pending}`} role="row" aria-rowindex={i + 2} style={{ height: ROW_H }}>
          {COLUMNS.map((c) => (
            <span key={c} role="cell">
              …
            </span>
          ))}
        </div>,
      );
      continue;
    }
    const st = batchStatusTitle(b.status);
    const stClass = st === "отказ" ? css.fail : st === "не оценена" ? css.na : st === "предупреждение" ? css.warn : undefined;
    const pick = () => onPick(b);
    const key = (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick();
      }
    };
    rows.push(
      <div key={i} className={`${css.bgrid} ${css.brow} ${sameBatch(chosen, b) ? css.chosen : ""}`} role="row" aria-rowindex={i + 2} tabIndex={0} data-batch={b.batch} data-status={b.status} style={{ height: ROW_H }} title="Щелчок — фильтр по партии" onClick={pick} onKeyDown={key}>
        <span role="cell">{labTitle(b.lab)}</span>
        <span role="cell">{b.batch}</span>
        <span role="cell">{batchDates(b)}</span>
        <span role="cell">{int(b.n)}</span>
        <span role="cell">{int(b.crm)}</span>
        <span role="cell">{int(b.blanks)}</span>
        <span role="cell" className={b.crm_fail ? css.fail : undefined}>
          {int(b.crm_fail)}
        </span>
        <span role="cell" className={b.blank_fail ? css.fail : undefined}>
          {int(b.blank_fail)}
        </span>
        <span role="cell" className={stClass}>
          {st}
        </span>
        <span role="cell" title={batchReasons(b) || undefined}>
          {batchReasons(b) || "—"}
        </span>
      </div>,
    );
  }
  return (
    <div style={{ position: "relative", height: total * ROW_H }} data-rows={to - from}>
      <div className={css.bwin} style={{ top: from * ROW_H }}>
        {rows}
      </div>
    </div>
  );
}

/** The head of the table: batches, failed, not assessed, records without a batch. */
export function batchesSummary(h: BatchesHead): string[] {
  const out = [`Партий ${int(h.total)}`, `отказ ${int(h.failed)} (${pct(share(h.failed, h.total))})`];
  if (typeof h.not_assessed === "number") out.push(`не оценены ${int(h.not_assessed)}`);
  out.push(`записей без партии ${int(h.no_batch)}`);
  return out;
}

export function BatchesTab() {
  const app = useApp();
  const s = useSyncExternalStore(qaqcStore.subscribe, qaqcStore.get, qaqcStore.get);
  const key = calcKey(s);
  const [, redraw] = useReducer((n: number) => n + 1, 0);
  const [pager, setPager] = useState<BatchPager | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [sight, setSight] = useState({ top: 0, height: 15 * ROW_H });
  // a new calculation: a pager of its own, back to the top; the pages of the old one on their way are dropped
  useEffect(() => {
    const body = calcBody();
    const next = key && body ? new BatchPager(body, redraw) : null;
    setPager(next);
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
    setSight((v) => ({ ...v, top: 0 }));
    return () => next?.dispose();
  }, [key]);
  // the box of the rows appears with the first answer: its size is followed from the moment it is mounted
  const observer = useRef<ResizeObserver | null>(null);
  const scrollBox = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    scrollRef.current = el;
    if (!el) return;
    const fit = () => setSight((v) => (!el.clientHeight || v.height === el.clientHeight ? v : { ...v, height: el.clientHeight }));
    fit();
    if (typeof ResizeObserver === "undefined") return;
    observer.current = new ResizeObserver(fit);
    observer.current.observe(el);
  }, []);
  const total = pager?.head?.total ?? 0;
  const win = rowWindow(sight.top - ROW_H, sight.height, pager?.head ? total : BATCH_PAGE);
  useEffect(() => {
    if (!pager) return;
    void pager.ensure(win.from, win.to).then(() => {
      if (key && pager.head) reportCounts(key, pager.head.counts);
    });
  }, [pager, win.from, win.to, key]);
  if (!pager) return null;
  const head = pager.head;
  if (!head) return pager.error ? <p className={css.error}>{pager.error}</p> : <p className={css.note}>Расчёт…</p>;
  const pick = (b: BatchRow) => {
    setHighlight({ lab: b.lab ?? null, batch: b.batch });
    void app.run("qaqc.filter", batchFilterArgs(b));
  };
  const hl = s.highlight;
  return (
    <div className={css.batches} data-testid="qaqc-batches">
      <div className={css.summary}>
        <span>
          {batchesSummary(head).join(" · ")} · элемент {head.element}
        </span>
        {hl && (
          <span className={css.row}>
            Подсвечена партия «{hl.batch}» ({labTitle(hl.lab)})
            <button type="button" className={css.btn} onClick={() => void app.run("qaqc.filter", batchFilterArgs(hl))}>
              Фильтр по ней
            </button>
            <button type="button" className={css.btn} onClick={() => setHighlight(null)}>
              Снять подсветку
            </button>
          </span>
        )}
      </div>
      {head.notes.map((n) => (
        <p key={n} className={css.note}>
          {n}
        </p>
      ))}
      {pager.error && <p className={css.error}>{pager.error}</p>}
      {total === 0 ? (
        <p className={css.note}>Партий с этим фильтром нет.</p>
      ) : (
        <div ref={scrollBox} className={css.bscroll} role="table" aria-label="Партии" aria-rowcount={total + 1} onScroll={(e) => setSight({ top: e.currentTarget.scrollTop, height: e.currentTarget.clientHeight || sight.height })}>
          <div className={`${css.bgrid} ${css.bhead}`} role="row" aria-rowindex={1}>
            {COLUMNS.map((c) => (
              <span key={c} role="columnheader">
                {c}
              </span>
            ))}
          </div>
          <BatchWindow total={total} from={win.from} to={win.to} row={(i) => pager.row(i)} chosen={hl} onPick={pick} />
        </div>
      )}
      <p className={css.note}>Щелчок по партии ставит фильтр по ней (лаборатория и партия); снять — «Сбросить» в фильтрах.</p>
    </div>
  );
}
