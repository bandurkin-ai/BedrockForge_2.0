/**
 * The preview of «Истинная мощность» on one hole (ADR 0021 §11): a column of the intervals coloured by TT / LENGTH
 * (an interval without TT is drawn empty, dashed — never as zero; «вдоль тела» — an orange edge), the rows with the
 * angles and the source, the counts by TT_SRC and by the bits of TT_FLAG. Empty values are «—», never «0».
 */
import css from "../../shell/PropsParts.module.css";
import { FLAG_BITS, flagText, TT_SOURCES, type TtPreview as Answer, type TtRow } from "./api";
import own from "./Intercepts.module.css";

/** 1.2345 → «1,23»; null — «—» (empty is not zero). */
export function fmt(v: number | null, digits = 2): string {
  if (v === null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: false });
}

/** The fill of an interval by TT / LENGTH ∈ [0; 1] (brand blue over the input colour); null — no fill. */
export function ratioFill(ratio: number | null): string | null {
  if (ratio === null || !Number.isFinite(ratio)) return null;
  const pct = Math.round(8 + 92 * Math.min(1, Math.max(0, ratio)));
  return `color-mix(in srgb, var(--accent) ${pct}%, var(--bg-input))`;
}

/** The core's note of a skipped mesh (MESH_SKIPPED of routes_preview.py). */
export const isMeshNote = (n: string): boolean => n.includes("2 млн треугольников");

export const ROWS_SHOWN = 500;
const H = 240;
const W = 76;
const BAR = { x: 30, w: 40 };

function Column({ rows }: { rows: readonly TtRow[] }) {
  if (!rows.length) return null;
  const top = Math.min(...rows.map((r) => r.from));
  const bottom = Math.max(...rows.map((r) => r.to));
  const span = bottom - top || 1;
  const y = (d: number) => 4 + ((d - top) / span) * (H - 8);
  return (
    <svg className={own.column} viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label="Столбец интервалов: окраска по TT / LENGTH" data-testid="intercepts-tt-column">
      <text x={0} y={10}>
        {fmt(top, 1)}
      </text>
      <text x={0} y={H - 2}>
        {fmt(bottom, 1)}
      </text>
      {rows.map((r, i) => {
        const fill = ratioFill(r.ratio);
        const cls = [own.cell, fill ? "" : own.empty, r.flag & 1 ? own.along : ""].filter(Boolean).join(" ");
        return (
          <rect key={i} x={BAR.x} width={BAR.w} y={y(r.from)} height={Math.max(0.5, y(r.to) - y(r.from))} className={cls} style={fill ? { fill } : undefined}>
            <title>{`${fmt(r.from)}–${fmt(r.to)} м: ИМ ${fmt(r.tt)} м, ИМ/L ${fmt(r.ratio)}${r.flag ? ` · ${flagText(r.flag)}` : ""}`}</title>
          </rect>
        );
      })}
    </svg>
  );
}

/** The counts of the hole: every TT_SRC met (the seven of the core first), every bit met. */
export function countRows(a: Pick<Answer, "bySrc" | "byFlag">): { src: [string, number][]; flags: [string, number, string][] } {
  const known = TT_SOURCES.filter((s) => (a.bySrc[s] ?? 0) > 0).map((s): [string, number] => [s, a.bySrc[s]]);
  const other = Object.entries(a.bySrc).filter(([s, n]) => n > 0 && !(TT_SOURCES as readonly string[]).includes(s));
  const flags = FLAG_BITS.filter((b) => (a.byFlag[b.bit] ?? 0) > 0).map((b): [string, number, string] => [b.title, a.byFlag[b.bit], b.hint]);
  return { src: [...known, ...other], flags };
}

function Counts({ title, counts, testId }: { title: string; counts: ReturnType<typeof countRows>; testId: string }) {
  if (!counts.src.length && !counts.flags.length) return null;
  return (
    <div className={own.counts} data-testid={testId}>
      <span className={own.countsTitle}>{title}:</span>
      {counts.src.map(([s, n]) => (
        <span key={s}>
          {s}
          <b>{n}</b>
        </span>
      ))}
      {counts.flags.map(([t, n, hint]) => (
        <span key={t} className={own.flag} title={hint}>
          {t}
          <b>{n}</b>
        </span>
      ))}
    </div>
  );
}

export function TtPreview({ answer }: { answer: Answer }) {
  const rows = answer.rows.slice(0, ROWS_SHOWN);
  const counts = countRows(answer);
  // `mesh_skipped` comes with the core's note MESH_SKIPPED (routes_preview.py): said once, as the warning
  const meshNote = answer.meshSkipped ? (answer.notes.find(isMeshNote) ?? null) : null;
  const notes = meshNote === null ? answer.notes : answer.notes.filter((n) => n !== meshNote);
  return (
    <div data-testid="intercepts-tt-preview">
      <p className={css.note}>
        Скважина {answer.hole || "—"}: интервалов {answer.rows.length}
        {answer.rows.length > ROWS_SHOWN ? ` (показаны первые ${ROWS_SHOWN})` : ""}
      </p>
      {answer.meshSkipped && (
        <p className={css.warn} data-testid="intercepts-tt-mesh-skipped">
          {meshNote ?? "Каркасов больше 2 млн треугольников: ориентировка по каркасу в предпросмотре не считается — её посчитает задание."}
        </p>
      )}
      {notes.map((n, i) => (
        <p key={i} className={css.note}>
          {n}
        </p>
      ))}
      {rows.length > 0 && (
        <>
          <div className={own.legend}>
            <span>TT / LENGTH</span>
            <span>0</span>
            <span className={own.ramp} />
            <span>1</span>
            <span>· пусто — пунктир · вдоль тела — оранжевая кромка</span>
          </div>
          <div className={own.preview}>
            <Column rows={rows} />
            <div className={own.rowsWrap}>
              <table className={own.rows} data-testid="intercepts-tt-rows">
                <thead>
                  <tr>
                    <th>От</th>
                    <th>До</th>
                    <th title="Длина по стволу, м">L</th>
                    <th title="Истинная мощность, м">ИМ</th>
                    <th title="TT / LENGTH">ИМ/L</th>
                    <th title="Угол падения тела α, °">α</th>
                    <th title="Азимут падения тела Aп, °">Aп</th>
                    <th title="Зенитный угол ствола θ, °">θ</th>
                    <th title="Азимут ствола Aс, °">Aс</th>
                    <th className={own.text}>Источник</th>
                    <th className={own.text}>Признаки</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i}>
                      <td>{fmt(r.from)}</td>
                      <td>{fmt(r.to)}</td>
                      <td>{fmt(r.length)}</td>
                      <td>{fmt(r.tt)}</td>
                      <td>{fmt(r.ratio)}</td>
                      <td>{fmt(r.alpha, 1)}</td>
                      <td>{fmt(r.dipdir, 1)}</td>
                      <td>{fmt(r.theta, 1)}</td>
                      <td>{fmt(r.azimuth, 1)}</td>
                      <td className={own.text}>{r.src || "—"}</td>
                      <td className={`${own.text} ${r.flag ? own.flag : ""}`} title={FLAG_BITS.filter((b) => r.flag & b.bit).map((b) => b.hint).join("; ") || undefined} data-flag={r.flag}>
                        {flagText(r.flag)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
      <Counts title={`Скважина ${answer.hole || "—"}`} counts={counts} testId="intercepts-tt-counts" />
      {answer.setCounts ? <Counts title="Таблица" counts={countRows(answer.setCounts)} testId="intercepts-tt-set-counts" /> : <p className={css.note}>Счётчики по всей таблице — после расчёта (больше 200 тыс. строк).</p>}
    </div>
  );
}
