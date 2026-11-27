/**
 * «Расход» of the window of the connection (Э23, 23.17; ТЗ 7.32): «Сегодня» / «30 дней» / «Всё время» × the requests, the tokens and the estimate in $, a row
 * per model under each period. The price of a model the reference does not know is `null` and shows «—» (never 0; ADR 0029 решение 13); the requests without
 * a price are counted under the table. The numbers are not rounded beyond the cents of the estimate (4 digits below one dollar).
 */
import type { TokenCounts, UsagePeriod, UsageReport } from "./wireTypes";
import css from "./UsageTable.module.css";

const NUMBER = new Intl.NumberFormat("ru-RU");
export const UNKNOWN = "—";

export const fmtTokens = (n: number): string => NUMBER.format(n);

/** «≈ $0,4123» (below $1: 4 digits), «≈ $12,35»; `null` — the price is unknown, «—». */
export function fmtUsd(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return UNKNOWN;
  return `≈ $${v.toFixed(v < 1 ? 4 : 2).replace(".", ",")}`;
}

export const PERIODS: readonly { id: "today" | "days30" | "all"; title: string }[] = [
  { id: "today", title: "Сегодня" },
  { id: "days30", title: "30 дней" },
  { id: "all", title: "Всё время" },
];

function Cells({ requests, tokens, cost }: { requests: number; tokens: TokenCounts; cost: number | null }) {
  return (
    <>
      <td>{NUMBER.format(requests)}</td>
      <td>{fmtTokens(tokens.input)}</td>
      <td>{fmtTokens(tokens.output)}</td>
      <td>{fmtTokens(tokens.cache_read)}</td>
      <td>{fmtTokens(tokens.cache_write)}</td>
      <td className={cost === null ? css.unknown : undefined} title={cost === null ? "Цена модели неизвестна: стоимость не определена" : undefined}>
        {fmtUsd(cost)}
      </td>
    </>
  );
}

function PeriodRows({ title, period, id }: { title: string; period: UsagePeriod; id: string }) {
  return (
    <>
      <tr className={css.period} data-testid={`usage-${id}`}>
        <td>{title}</td>
        <Cells requests={period.requests} tokens={period.tokens} cost={period.cost_usd} />
      </tr>
      {period.by_model.length === 0 ? (
        <tr className={css.empty}>
          <td colSpan={7}>Запросов не было</td>
        </tr>
      ) : (
        period.by_model.map((m) => (
          <tr key={m.model} className={css.model} data-testid={`usage-${id}-${m.model}`}>
            <td title={m.model}>{m.title || m.model}</td>
            <Cells requests={m.requests} tokens={m.tokens} cost={m.cost_usd} />
          </tr>
        ))
      )}
    </>
  );
}

export interface UsageTableProps {
  usage: UsageReport | null;
  error: string | null;
}

export function UsageTable({ usage, error }: UsageTableProps) {
  if (!usage) return <p className={css.foot}>{error ?? "Загрузка…"}</p>;
  const unpriced = PERIODS.map((p) => ({ title: p.title, n: usage[p.id].unpriced_requests ?? 0 })).filter((x) => x.n > 0);
  return (
    <div data-testid="usage-table">
      <table className={css.table}>
        <thead>
          <tr>
            <th scope="col">Период / модель</th>
            <th scope="col">Запросы</th>
            <th scope="col">Вход</th>
            <th scope="col">Выход</th>
            <th scope="col">Кеш (чтение)</th>
            <th scope="col">Кеш (запись)</th>
            <th scope="col">Оценка</th>
          </tr>
        </thead>
        <tbody>
          {PERIODS.map((p) => (
            <PeriodRows key={p.id} id={p.id} title={p.title} period={usage[p.id]} />
          ))}
        </tbody>
      </table>
      {unpriced.length > 0 ? (
        <p className={css.foot} data-testid="usage-unpriced">
          Без цены (модель не из справочника), в оценку не входят: {unpriced.map((x) => `${x.title} — ${NUMBER.format(x.n)}`).join("; ")}.
        </p>
      ) : null}
      <p className={css.foot}>
        Оценка по справочнику цен {usage.prices_version}
        {usage.since ? `, учёт с ${new Date(usage.since).toLocaleDateString("ru-RU")}` : ""}; фактический счёт — в Anthropic Console. «Сегодня» — сутки по времени этого компьютера.
      </p>
    </div>
  );
}
