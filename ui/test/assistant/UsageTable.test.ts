// «Расход» of the window of the connection (Э23, 23.17; ТЗ 7.32 «Сегодня / 30 дней / всё время: запросы, токены, оценка в $, по моделям»; ADR 0029 решение 13): three
// periods × the models, the estimate in $, an unknown price is «—» (never 0).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { fmtUsd, UsageTable } from "../../src/modules/assistant/UsageTable";
import type { UsagePeriod, UsageReport } from "../../src/modules/assistant/wireTypes";

const tok = (input: number, output: number, cache_read = 0, cache_write = 0) => ({ input, output, cache_read, cache_write });
const row = (model: string, title: string, requests: number, tokens: ReturnType<typeof tok>, cost_usd: number | null) => ({ model, title, requests, tokens, cost_usd });

const TODAY: UsagePeriod = {
  requests: 12,
  tokens: tok(1200, 800, 90000, 30000),
  cost_usd: 0.4123,
  by_model: [row("claude-opus-5-5", "Opus 5.5", 10, tok(1000, 700, 80000, 25000), 0.4), row("claude-haiku-5-5", "Haiku 5.5", 2, tok(200, 100, 10000, 5000), 0.0123)],
};
const DAYS: UsagePeriod = { requests: 40, tokens: tok(5000, 3000, 400000, 90000), cost_usd: 3.5, by_model: [row("claude-opus-5-5", "Opus 5.5", 40, tok(5000, 3000, 400000, 90000), 3.5)] };
const ALL: UsagePeriod = {
  requests: 51,
  tokens: tok(9000, 6000, 700000, 120000),
  cost_usd: 12.3456,
  unpriced_requests: 3,
  by_model: [row("claude-opus-5-5", "Opus 5.5", 40, tok(5000, 3000, 400000, 90000), 12.3456), row("claude-next-9", "claude-next-9", 3, tok(300, 200, 0, 0), null)],
};
const USAGE: UsageReport = { today: TODAY, days30: DAYS, all: ALL, prices_version: "2026-11", since: "2026-11-01T00:00:00Z" };

const draw = (usage: UsageReport | null, error: string | null = null) => renderToStaticMarkup(createElement(UsageTable, { usage, error }));
const text = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").replace(/ /g, " ");
const rowOf = (html: string, testId: string) => text(html.match(new RegExp(`<tr[^>]*data-testid="${testId}"[^>]*>(.*?)</tr>`))?.[1] ?? "").trim();

describe("the table of the usage", () => {
  it("three periods, each with a row per model", () => {
    const out = draw(USAGE);
    expect(rowOf(out, "usage-today")).toMatch(/^Сегодня 12 1 200 800 90 000 30 000 ≈ \$0,4123$/);
    expect(rowOf(out, "usage-today-claude-opus-5-5")).toMatch(/^Opus 5\.5 10 1 000 700 80 000 25 000 ≈ \$0,4000$/);
    expect(rowOf(out, "usage-today-claude-haiku-5-5")).toMatch(/^Haiku 5\.5 2 200 100 10 000 5 000 ≈ \$0,0123$/);
    expect(rowOf(out, "usage-days30")).toMatch(/^30 дней 40 .* ≈ \$3,50$/);
    expect(rowOf(out, "usage-days30-claude-opus-5-5")).toContain("Opus 5.5");
    expect(rowOf(out, "usage-all")).toMatch(/^Всё время 51 .* ≈ \$12,35$/);
    expect(rowOf(out, "usage-all-claude-opus-5-5")).toContain("≈ $12,35");
  });

  it("the columns: requests, input, output, cache read, cache write, the estimate", () => {
    const t = text(draw(USAGE));
    for (const h of ["Запросы", "Вход", "Выход", "Кеш (чтение)", "Кеш (запись)", "Оценка"]) expect(t).toContain(h);
  });

  it("an unknown price is «—», not 0; the requests without a price are told", () => {
    const out = draw(USAGE);
    expect(rowOf(out, "usage-all-claude-next-9")).toMatch(/^claude-next-9 3 300 200 0 0 —$/);
    expect(rowOf(out, "usage-all-claude-next-9")).not.toContain("$0");
    expect(text(out)).toContain("Без цены (модель не из справочника), в оценку не входят: Всё время — 3.");
    expect(draw({ ...USAGE, all: { ...ALL, unpriced_requests: 0 } })).not.toContain("usage-unpriced");
  });

  it("a period without requests: the row is there, «—» for the cost (nothing is guessed), «Запросов не было»", () => {
    const empty: UsagePeriod = { requests: 0, tokens: tok(0, 0), cost_usd: null, by_model: [] };
    const out = draw({ ...USAGE, today: empty });
    expect(rowOf(out, "usage-today")).toMatch(/^Сегодня 0 0 0 0 0 —$/);
    expect(text(out)).toContain("Запросов не было");
  });

  it("the footer: the version of the prices and the date of the start of the account", () => {
    const t = text(draw(USAGE));
    expect(t).toContain("справочнику цен 2026-11");
    expect(t).toContain("учёт с ");
    expect(text(draw({ ...USAGE, since: null }))).not.toContain("учёт с");
  });

  it("no data yet: «Загрузка…» or the error", () => {
    expect(text(draw(null))).toContain("Загрузка…");
    expect(text(draw(null, "Ядро недоступно."))).toContain("Ядро недоступно.");
  });
});

describe("fmtUsd", () => {
  it("below $1 — 4 digits, above — cents, null — «—»", () => {
    expect(fmtUsd(0.4123)).toBe("≈ $0,4123");
    expect(fmtUsd(0)).toBe("≈ $0,0000");
    expect(fmtUsd(12.3456)).toBe("≈ $12,35");
    expect(fmtUsd(null)).toBe("—");
  });
});
