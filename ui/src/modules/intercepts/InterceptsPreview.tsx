/**
 * The preview of «Рудные пересечения» on one hole (ADR 0021 §11; ТЗ 8.5.3): on one depth scale — the samples
 * (`charts/column`, coloured by the grade, g ≥ c marked «руда», an empty grade hatched — never the colour of zero),
 * the candidates of steps 1–2 (accepted — Copper Orange, refused — hatched with the reason in the tooltip), the
 * intercepts with their labels (основное — Copper Orange, «в том числе» — its dark tint); then the rows of the
 * intercepts, the refused candidates with the reason («короче L_min», «ИМ меньше m_min») and the summary
 * «скважина / набор». Empty values are «—», never «0».
 */
import { useId } from "react";
import { BRAND, mix } from "../../charts/column/classes";
import { DepthAxis, SampleColumn } from "../../charts/column/SampleColumn";
import { colorOf, columnsRange, depthScale, depthText, valueRange, type Columns, type DepthScale } from "../../charts/column/scale";
import css from "../../shell/PropsParts.module.css";
import { flagText, type Candidate, type RunPreview, type RunSummary } from "./api";
import own from "./Intercepts.module.css";
import { fmt } from "./TtPreview";

export const PREVIEW_HEIGHT = 320;
export const ROWS_SHOWN = 500;
const HEAD = 18;
/** «основное» — Copper Orange (оруденение, docs/brand/README.md); «в том числе» — its tint towards Anthracite. */
export const KIND_COLOR: Readonly<Record<string, string>> = { основное: BRAND.copper, "в том числе": mix(BRAND.copper, BRAND.anthracite, 0.4) };
const INCLUDED = "в том числе";
const ORE_MARK = "__ORE";

/** «5 м @ 1,16 г/т» — a candidate as the rule sees it; an empty grade — «—». */
export function candidateText(c: Candidate, unit: string | null): string {
  const g = c.grade === null ? "—" : `${fmt(c.grade)}${unit ? ` ${unit}` : ""}`;
  const tt = c.tt === null ? "" : ` (ИМ ${fmt(c.tt, 1)} м)`;
  return `${depthText(c.from)}–${depthText(c.to)} м: ${fmt(c.length ?? c.to - c.from, 1)} м @ ${g}${tt}`;
}

/** The samples with the mark of step 1 (g ≥ c) as a flag column of `SampleColumn`. */
export function samplesWithOre(a: Pick<RunPreview, "samples" | "ore">): Columns {
  return { ...a.samples, values: { ...a.samples.values, [ORE_MARK]: a.samples.from.map((_, i) => (a.ore[i] === true ? "руда" : null)) } };
}

/** The candidates as columns (for the common depth range). */
const candidateColumns = (list: readonly Candidate[]): Columns => ({ from: list.map((c) => c.from), to: list.map((c) => c.to), values: {} });

interface Span {
  from: number;
  to: number;
  fill: string | null;
  lane: 0 | 1;
  tip: string;
  label?: string;
  refused?: boolean;
}

/** A column of spans in two lanes (основное — left, «в том числе» — right), labels to the right when given. */
function Spans({ title, spans, scale, width, labels, testId }: { title: string; spans: readonly Span[]; scale: DepthScale; width: number; labels?: number; testId: string }) {
  const hatch = `ihatch-${useId().replace(/:/g, "")}`;
  const lane = width / 2;
  const h = scale.pad + scale.height + 2;
  return (
    <svg width={width + (labels ?? 0)} height={h + HEAD} role="img" aria-label={`${title}: ${spans.length}`} data-testid={testId} style={{ flex: "none", overflow: "visible" }}>
      <defs>
        <pattern id={hatch} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" style={{ stroke: "var(--text-faint)" }} strokeWidth="1.5" />
        </pattern>
      </defs>
      <text x={width / 2} y={12} textAnchor="middle" fontSize="11" fontWeight={600} style={{ fill: "var(--text)", fontFamily: "var(--font-brand)" }}>
        {title}
      </text>
      <g transform={`translate(0, ${HEAD})`}>
        {spans.map((s, i) => {
          const y = scale.y(s.from);
          const hh = Math.max(1, scale.y(s.to) - y);
          return (
            <g key={i} data-refused={s.refused ? "yes" : undefined} data-kind={s.lane ? "included" : "main"}>
              <rect x={1 + s.lane * lane} y={y} width={lane - 2} height={hh} fill={s.fill ?? `url(#${hatch})`} strokeWidth={s.refused ? 1 : 0.5} style={{ stroke: s.refused ? "var(--danger)" : "var(--border-strong)" }}>
                <title>{s.tip}</title>
              </rect>
              {s.label && labels ? (
                <text x={width + 4} y={y + Math.min(hh / 2, 12) + 4} fontSize="10" style={{ fill: "var(--text)" }}>
                  {s.label}
                </text>
              ) : null}
            </g>
          );
        })}
      </g>
    </svg>
  );
}

const at = (c: RunPreview["result"], key: string, i: number): number | string | null => c.values[key]?.[i] ?? null;
const numAt = (c: RunPreview["result"], key: string, i: number): number | null => {
  const v = at(c, key, i);
  return typeof v === "number" ? v : null;
};
const textAt = (c: RunPreview["result"], key: string, i: number): string => {
  const v = at(c, key, i);
  return typeof v === "string" ? v : "";
};

export interface InterceptRow {
  id: number | null;
  kind: string;
  from: number;
  to: number;
  length: number | null;
  tt: number | null;
  grade: number | null;
  lenG: number | null;
  ttG: number | null;
  waste: number | null;
  miss: number | null;
  n: number | null;
  incl: number | null;
  flag: number;
  src: string;
  label: string;
}

/** The rows of `result` (e15-api.md: ID, KIND, LENGTH, TT, ‹field›, LEN_G, TT_G, WASTE_LEN, MISS_LEN, N_INT, INCL, TT_FLAG, TT_SRC, LABEL). */
export function interceptRows(a: Pick<RunPreview, "result">, field: string | null): InterceptRow[] {
  const r = a.result;
  const out: InterceptRow[] = [];
  for (let i = 0; i < r.from.length; i += 1) {
    const from = r.from[i];
    const to = r.to[i];
    if (from === null || to === null) continue;
    out.push({
      id: numAt(r, "ID", i),
      kind: textAt(r, "KIND", i) || "основное",
      from,
      to,
      length: numAt(r, "LENGTH", i),
      tt: numAt(r, "TT", i),
      grade: field ? numAt(r, field, i) : null,
      lenG: numAt(r, "LEN_G", i),
      ttG: numAt(r, "TT_G", i),
      waste: numAt(r, "WASTE_LEN", i),
      miss: numAt(r, "MISS_LEN", i),
      n: numAt(r, "N_INT", i),
      incl: numAt(r, "INCL", i),
      flag: numAt(r, "TT_FLAG", i) ?? 0,
      src: textAt(r, "TT_SRC", i),
      label: textAt(r, "LABEL", i),
    });
  }
  return out;
}

/** LEN_G for г/т is метрограмм, for % — метропроцент (ТЗ 8.5.3). */
export const lenGTitle = (unit: string | null): string => (unit && /г\/т|g\/t|ppm/i.test(unit) ? "метрограмм" : "метропроцент");

function SummaryRows({ hole, set, unit }: { hole: RunSummary | null; set: RunSummary | null; unit: string | null }) {
  const rows: [string, (s: RunSummary) => string][] = [
    ["Пересечений", (s) => fmt(s.intercepts, 0)],
    ["в том числе", (s) => fmt(s.included, 0)],
    ["Σ длина, м", (s) => fmt(s.length, 1)],
    ["Σ TT, м", (s) => fmt(s.tt, 1)],
    [`Σ LEN_G (${lenGTitle(unit)})`, (s) => fmt(s.len_g)],
    [`Среднее содержание${unit ? `, ${unit}` : ""}`, (s) => fmt(s.mean)],
    ["Σ WASTE_LEN, м", (s) => fmt(s.waste_len, 1)],
    ["Σ MISS_LEN, м", (s) => fmt(s.miss_len, 1)],
  ];
  return (
    <table className={own.rows} data-testid="intercepts-run-summary">
      <thead>
        <tr>
          <th className={own.text}>Сводка</th>
          <th>Скважина</th>
          <th>Набор</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([t, f]) => (
          <tr key={t}>
            <td className={own.text}>{t}</td>
            <td>{hole ? f(hole) : "—"}</td>
            <td>{set ? f(set) : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function InterceptsPreview({ answer, field, height = PREVIEW_HEIGHT }: { answer: RunPreview; field: string | null; height?: number }) {
  const samples = samplesWithOre(answer);
  const range = columnsRange(samples, answer.result, candidateColumns(answer.candidates));
  const rows = interceptRows(answer, field);
  const refused = answer.candidates.filter((c) => !c.accepted);
  const unit = answer.unit;
  if (!range) return <p className={css.note}>У скважины {answer.hole || "—"} нет проб для предпросмотра.</p>;
  const scale = depthScale(range[0], range[1], height, 2);
  const color = colorOf(field ? valueRange(field, answer.samples) : null);
  const cand: Span[] = answer.candidates.map((c) => ({
    from: c.from,
    to: c.to,
    lane: c.kind === INCLUDED ? 1 : 0,
    fill: c.accepted ? (KIND_COLOR[c.kind] ?? BRAND.copper) : null,
    refused: !c.accepted,
    tip: `${candidateText(c, unit)} · ${c.accepted ? "принят" : `отказ: ${c.reason ?? "не принят"}`}`,
  }));
  const result: Span[] = rows.map((r) => ({
    from: r.from,
    to: r.to,
    lane: r.kind === INCLUDED ? 1 : 0,
    fill: KIND_COLOR[r.kind] ?? BRAND.copper,
    tip: r.label || `${depthText(r.from)}–${depthText(r.to)} м`,
    label: r.label,
  }));
  return (
    <div data-testid="intercepts-run-preview">
      <p className={css.note}>
        Скважина {answer.hole || "—"}: проб {answer.samples.from.length}, кандидатов {answer.candidates.length}, принято {answer.candidates.filter((c) => c.accepted).length}
      </p>
      {answer.notes.map((n, i) => (
        <p key={i} className={css.note}>
          {n}
        </p>
      ))}
      <div className={own.legend}>
        <span className={own.swatch} style={{ background: KIND_COLOR["основное"] }} />
        <span>основное</span>
        <span className={own.swatch} style={{ background: KIND_COLOR[INCLUDED] }} />
        <span>в том числе</span>
        <span>· отказ — штриховка с красной кромкой · проба g ≥ c — метка справа · пусто — штриховка</span>
      </div>
      <div className={own.runColumns}>
        <DepthAxis scale={scale} />
        <SampleColumn title="Пробы" cols={samples} field={field} scale={scale} color={color} flagField={ORE_MARK} testId="intercepts-run-samples" />
        <Spans title="Кандидаты" spans={cand} scale={scale} width={56} testId="intercepts-run-candidates" />
        <Spans title="Пересечения" spans={result} scale={scale} width={56} labels={220} testId="intercepts-run-result" />
      </div>
      {rows.length > 0 ? (
        <div className={own.rowsWrap}>
          <table className={own.rows} data-testid="intercepts-run-rows">
            <thead>
              <tr>
                <th>№</th>
                <th className={own.text}>Вид</th>
                <th>От</th>
                <th>До</th>
                <th title="Длина по стволу, м">L</th>
                <th title="Истинная мощность, м">ИМ</th>
                <th title={`Среднее ${field ?? ""}${unit ? `, ${unit}` : ""}`}>{field ?? "g"}</th>
                <th title={`LENGTH·g — ${lenGTitle(unit)}`}>LEN_G</th>
                <th title="TT·g">TT_G</th>
                <th title="Пустая порода внутри, м">Пуст.</th>
                <th title="Пропуски внутри, м">Проп.</th>
                <th title="Число проб">N</th>
                <th className={own.text}>Признаки</th>
                <th className={own.text}>Подпись</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, ROWS_SHOWN).map((r, i) => (
                <tr key={i} data-kind={r.kind}>
                  <td>{r.kind === INCLUDED && r.incl !== null ? `${fmt(r.incl, 0)}.${fmt(r.id, 0)}` : fmt(r.id, 0)}</td>
                  <td className={own.text}>{r.kind}</td>
                  <td>{fmt(r.from)}</td>
                  <td>{fmt(r.to)}</td>
                  <td>{fmt(r.length)}</td>
                  <td>{fmt(r.tt)}</td>
                  <td>{fmt(r.grade)}</td>
                  <td>{fmt(r.lenG)}</td>
                  <td>{fmt(r.ttG)}</td>
                  <td>{fmt(r.waste)}</td>
                  <td>{fmt(r.miss)}</td>
                  <td>{fmt(r.n, 0)}</td>
                  <td className={`${own.text} ${r.flag ? own.flag : ""}`} data-flag={r.flag}>
                    {flagText(r.flag) || r.src || "—"}
                  </td>
                  <td className={own.text}>{r.label || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className={css.note}>У скважины нет принятых пересечений.</p>
      )}
      {refused.length > 0 && (
        <div data-testid="intercepts-run-refused">
          <p className={css.note}>Отброшенные кандидаты:</p>
          <ul className={own.refused}>
            {refused.map((c, i) => (
              <li key={i}>
                {c.kind === INCLUDED ? "в т. ч. " : ""}
                {candidateText(c, unit)} — {c.reason ?? "не принят"}
              </li>
            ))}
          </ul>
        </div>
      )}
      <SummaryRows hole={answer.summary} set={answer.setSummary} unit={unit} />
      {!answer.setSummary && <p className={css.note}>Сводка по набору — после расчёта (больше 200 тыс. проб).</p>}
    </div>
  );
}
