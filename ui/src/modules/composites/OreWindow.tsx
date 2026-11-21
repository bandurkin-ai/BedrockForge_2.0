/**
 * The window «Рудные интервалы (ГКЗ)» (ТЗ 7.35 Д-26 «Место», 7.35.10; ADR 0018 §8–11, решения 19–22): on the
 * left the source (set, table, grade field, domain) and Сб, Мрт, Мпп, МС (авто = Сб·Мрт), Смин (пусто — = Сб),
 * the mode, the thickness (length along the hole or a field), the gaps; on the right the preview on the chosen
 * hole (`POST /composites/ore/preview`, the newest answer only) as tabs: «Столбец» — the samples by grade, their
 * classes (элементарные рудные, некондиционные, включённые и исключённые прослои), the прирезки of step 2 and
 * the final intervals with LABEL (`frame` hidden unless asked for); «Протокол» — the form И1, сл. 17 of the
 * preview or of a fresh result (`GET /composites/results/{id}/protocol?hole=`) with «Сохранить как…»; «Сводка» —
 * by class (and domain) of the hole and the set. «Применить» → the job; when it is done, the table opens in the
 * bottom panel and the display «Рудные интервалы» is switched on in 3D.
 */
import { useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { DepthAxis, SampleColumn } from "../../charts/column/SampleColumn";
import { assignLanes, classTitle, CLASS_STYLE, finalRows, laneCount, legendOf, sampleClasses, textOn, TRIM_COLOR } from "../../charts/column/classes";
import { colorOf, columnsRange, depthScale, depthText, valueRange, type DepthScale } from "../../charts/column/scale";
import { AppContext } from "../../shell/AppContext";
import type { App } from "../../shell/app";
import { errorText } from "../../shell/props";
import { setPanel } from "../../shell/workspace";
import css from "../../shell/PropsParts.module.css";
import { chartsOfSet, loadLibrary } from "../downhole/library";
import { showDisplay } from "../downhole/displays";
import { jobsStore } from "../jobs/state";
import { updateView } from "../scene/bind";
import { loadTabs, openTable, selectSet, tablesStore } from "../tables/state";
import own from "./Composites.module.css";
import { createPreviewer, type Previewer, type Started } from "./api";
import { OreProtocol, ProtocolExport } from "./OreProtocol";
import { SourcePicker } from "./SourcePicker";
import { HolePicker, JobView, ParamForm, ToolFooter, useTool } from "./ToolWindow";
import { useSetResults } from "./TopcutWindow";
import { ttFirst, ttResultOf, ttThicknessHint } from "../intercepts/tools";
import { autoValues, DEFAULT_TABLE, fetchOrePreview, fetchProtocol, ore, summaryLines, type OreAnswer, type OreSummary } from "./tools/ore";

export const PREVIEW_HEIGHT = 340;
/** The display the job makes (ADR 0018 §8 «Показ»). */
export const DISPLAY_NAME = "Рудные интервалы";
/** The tool without the generic preview of `useTool`: the window asks `/ore/preview` itself (its own answer). */
const ORE_TOOL = { ...ore, preview: null };

interface BarSpec {
  key: string;
  from: number;
  to: number;
  color: string | null;
  tip: string;
  x?: number;
  w?: number;
  testValue?: string;
}

/** A column of bars on the depth scale; a bar without colour is hatched (no value — never a class). */
function Bars({ title, bars, scale, width, testId, labels = [] }: { title: string; bars: BarSpec[]; scale: DepthScale; width: number; testId: string; labels?: { y: number; text: string }[] }) {
  const h = scale.pad + scale.height + 2;
  return (
    <svg width={width} height={h + 18} role="img" aria-label={`${title}: ${bars.length}`} data-testid={testId} style={{ flex: "none", overflow: "visible" }}>
      <defs>
        <pattern id={`${testId}-hatch`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" style={{ stroke: "var(--text-faint)" }} strokeWidth="1.5" />
        </pattern>
      </defs>
      <text x={4} y={12} fontSize="11" fontWeight={600} style={{ fill: "var(--text)", fontFamily: "var(--font-brand)" }}>
        {title}
      </text>
      <g transform="translate(0, 18)">
        {bars.map((b) => {
          const y = scale.y(b.from);
          return (
            <rect key={b.key} x={b.x ?? 1} y={y} width={b.w ?? 60} height={Math.max(1, scale.y(b.to) - y)} fill={b.color ?? `url(#${testId}-hatch)`} strokeWidth={0.5} style={{ stroke: "var(--border-strong)" }} data-value={b.testValue}>
              <title>{b.tip}</title>
            </rect>
          );
        })}
        {labels.map((l, i) => (
          <text key={i} x={66} y={l.y + 4} fontSize="10" style={{ fill: "var(--text)" }}>
            {l.text}
          </text>
        ))}
      </g>
    </svg>
  );
}

/** The four columns of the preview on one depth scale and the legend of the classes. */
export function OreColumns({ answer, field, cutoff, showFrame, height = PREVIEW_HEIGHT }: { answer: OreAnswer; field: string | null; cutoff: number | null; showFrame: boolean; height?: number }) {
  const range = columnsRange(answer.samples, answer.result);
  if (!range) return <p className={css.note}>У скважины {answer.hole} нет проб для предпросмотра.</p>;
  const scale = depthScale(range[0], range[1], height, 2);
  const color = colorOf(field ? valueRange(field, answer.samples) : null);
  const s = answer.samples;
  const grade = field ? (s.values[field] ?? []) : [];
  const classes = sampleClasses(s.values.ORE_CLS ?? [], grade, cutoff);
  const span = (i: number) => `${depthText(Number(s.from[i]))}–${depthText(Number(s.to[i]))} м`;
  const classBars: BarSpec[] = [];
  for (let i = 0; i < s.from.length; i++) {
    const a = s.from[i];
    const b = s.to[i];
    if (typeof a !== "number" || typeof b !== "number" || !(b > a)) continue;
    const c = classes[i];
    classBars.push({ key: `s${i}`, from: a, to: b, color: c ? CLASS_STYLE[c].color : null, tip: `${span(i)}: ${c ? classTitle(c) : "нет значения или вне линий опробования"}`, testValue: c ?? "none" });
  }
  const trims = answer.events.filter((e) => e.step === 2 && e.kind === "trim" && e.decision === "pass" && e.row0 >= 0 && e.row1 < s.from.length);
  const lanes = assignLanes(trims.map((e) => ({ row0: e.row0, row1: e.row1 })));
  const trimBars: BarSpec[] = trims.map((e, i) => ({
    key: `t${i}`,
    from: Number(s.from[e.row0]),
    to: Number(s.to[e.row1]),
    color: TRIM_COLOR,
    x: 2 + lanes[i] * 7,
    w: 5,
    tip: `Этап ${e.stage}, прирезка ${e.side === "lower" ? "лежачего" : "висячего"} бока: пробы ${e.row0 + 1}–${e.row1 + 1}, C ${e.c?.toLocaleString("ru-RU", { maximumFractionDigits: 3 }) ?? "—"}`,
  }));
  const rows = finalRows(answer.result, showFrame);
  const finalBars: BarSpec[] = rows.map((r) => ({
    key: `r${r.index}`,
    from: r.from,
    to: r.to,
    color: CLASS_STYLE[r.cls].color,
    x: r.cls === "frame" ? 54 : r.nested ? 12 : 1,
    w: r.cls === "frame" ? 8 : r.nested ? 38 : 60,
    tip: `${depthText(r.from)}–${depthText(r.to)} м: ${classTitle(r.cls)}${r.label ? ` · ${r.label}` : ""}`,
    testValue: r.cls,
  }));
  const labels = rows.filter((r) => r.label).map((r) => ({ y: (scale.y(r.from) + scale.y(r.to)) / 2, text: r.label }));
  const legend = legendOf([...classes, ...rows.map((r) => r.cls)], showFrame);
  return (
    <div>
      <div className={own.columns} data-testid="ore-columns">
        <DepthAxis scale={scale} />
        <SampleColumn title={field ?? "Пробы"} cols={s} field={field} scale={scale} color={color} testId="ore-samples" />
        <Bars title="Классы" bars={classBars} scale={scale} width={62} testId="ore-classes" />
        <Bars title="Прир." bars={trimBars} scale={scale} width={Math.max(30, 4 + laneCount(lanes) * 7)} testId="ore-trims" />
        <Bars title="Итог" bars={finalBars} scale={scale} width={210} testId="ore-final" labels={labels} />
      </div>
      <ul className={own.notes} style={{ listStyle: "none", paddingLeft: 0, color: "var(--text-dim)", display: "flex", flexWrap: "wrap", gap: "4px 10px" }} data-testid="ore-legend">
        {legend.map((l) => (
          <li key={l.cls}>
            <span style={{ display: "inline-block", width: 10, height: 10, marginRight: 4, background: l.color, color: textOn(l.color), verticalAlign: "middle" }} />
            {l.title}
          </li>
        ))}
        {trimBars.length > 0 && (
          <li>
            <span style={{ display: "inline-block", width: 4, height: 10, marginRight: 4, background: TRIM_COLOR, verticalAlign: "middle" }} />
            прирезка (C ≥ Сб)
          </li>
        )}
      </ul>
    </div>
  );
}

function SummaryBlock({ summary, title, byDomain }: { summary: OreSummary | null; title: string; byDomain: boolean }) {
  const lines = summaryLines(summary, byDomain);
  if (!lines.length) return null;
  return (
    <table className={own.summary} data-testid="ore-summary">
      <caption>{title}</caption>
      <thead>
        <tr>
          <th />
          <th>число</th>
          <th>Σ M, м</th>
          <th>ср. M, м</th>
          <th>C = ΣMC/ΣM</th>
          <th>Σ MC</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((l) => (
          <tr key={l.key}>
            <th scope="row">{l.title}</th>
            <td>{l.count}</td>
            <td>{l.m}</td>
            <td>{l.mMean}</td>
            <td>{l.c}</td>
            <td>{l.mc}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function OreSummaryView({ answer }: { answer: OreAnswer }) {
  const [byDomain, setByDomain] = useState(false);
  const domains = (answer.summary?.domains.length ?? 0) > 0 || (answer.set_summary?.domains.length ?? 0) > 0;
  return (
    <div>
      {domains && (
        <label className={css.check}>
          <input type="checkbox" checked={byDomain} onChange={(e) => setByDomain(e.target.checked)} />
          По доменам
        </label>
      )}
      <SummaryBlock summary={answer.summary} title={`Скважина ${answer.hole}`} byDomain={byDomain} />
      {answer.set_summary ? <SummaryBlock summary={answer.set_summary} title="Набор" byDomain={byDomain} /> : <p className={css.note}>Сводка по набору — после расчёта (больше 200 тыс. проб).</p>}
    </div>
  );
}

/** After the job: the table «Рудные интервалы» in the bottom panel, its display switched on in 3D. */
export async function showOreResult(app: App | null, result: Record<string, unknown>): Promise<void> {
  const setId = typeof result.set_id === "number" ? result.set_id : tablesStore.get().setId;
  const table = result.table_id !== undefined && result.table_id !== null ? String(result.table_id) : null;
  if (setId !== null && table !== null) {
    if (tablesStore.get().setId !== setId) await selectSet(setId, table);
    else {
      await loadTabs();
      await openTable(table);
    }
    app?.store.set((st) => ({ workspace: setPanel(st.workspace, "bottom", true, "table") }));
  }
  await loadLibrary().catch(() => undefined);
  const name = typeof result.name === "string" ? result.name : DEFAULT_TABLE;
  const chart = typeof result.chart_id === "number" ? result.chart_id : setId !== null ? chartsOfSet(setId).find((c) => c.name === DISPLAY_NAME || c.name === name)?.id : undefined;
  if (chart === undefined) return;
  try {
    updateView("displays", showDisplay(chart, true));
  } catch {
    /* no project view: nothing to switch on */
  }
}

/** Runs `showOreResult` once when the job of the window is done. */
function useShowWhenDone(job: Started | null): void {
  const app = useContext(AppContext);
  const get = () => (job ? jobsStore.get().jobs.find((j) => j.source === job.source && j.id === job.id) : undefined);
  const live = useSyncExternalStore(jobsStore.subscribe, get, get); // a server snapshot too: the window renders statically in tests
  const shownFor = useRef<string | null>(null);
  useEffect(() => {
    if (!job || live?.status !== "done" || !live.result) return;
    const key = `${job.source}:${job.id}`;
    if (shownFor.current === key) return;
    shownFor.current = key;
    void showOreResult(app, live.result as Record<string, unknown>).catch(() => undefined);
  }, [job, live, app]);
}

type Tab = "column" | "protocol" | "summary";
const TABS: { id: Tab; title: string }[] = [
  { id: "column", title: "Столбец" },
  { id: "protocol", title: "Протокол" },
  { id: "summary", title: "Сводка" },
];

export function OreWindow({ winId, args = {} }: { winId: string; args?: Record<string, unknown> }) {
  const t = useTool(ORE_TOOL, args);
  const [activeSet] = useState(() => tablesStore.get().setId); // the set active when the window opened
  const [answer, setAnswer] = useState<OreAnswer | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("column");
  const [showFrame, setShowFrame] = useState(false);
  const [protoOf, setProtoOf] = useState<number | null>(null); // null — the preview; a result id — its protocol
  const [proto, setProto] = useState<{ answer: OreAnswer | null; error: string | null }>({ answer: null, error: null });
  const previewer = useRef<Previewer | null>(null);
  const setResults = useSetResults(t.src.setId);
  const results = setResults.filter((r) => r.op === "ore");
  // ADR 0021 decision 16: TT of «Истинная мощность» — first in «Мощность пробы», with its «устарел»
  const ttHint = ttThicknessHint(t.fields, t.values.thickness_field, ttResultOf(setResults, t.src.table));
  const chosen = results.find((r) => r.id === protoOf) ?? null;
  const field = t.src.fields[0] ?? null;
  const cutoff = typeof t.checked.body?.cutoff === "number" ? t.checked.body.cutoff : null;
  const auto = useMemo(() => autoValues(t.values), [t.values]);
  useShowWhenDone(t.job);

  useEffect(() => {
    const p = createPreviewer<OreAnswer>(
      (a) => {
        setAnswer(a);
        setPreviewError(null);
      },
      (e) => setPreviewError(errorText(e)),
      (_op, params, holeId) => fetchOrePreview(params, holeId),
    );
    previewer.current = p;
    return () => p.stop();
  }, []);
  useEffect(() => {
    if (t.checked.body && t.hole) previewer.current?.request("ore", t.checked.body, t.hole.id);
  }, [t.checked, t.hole]);
  useEffect(() => {
    let alive = true;
    setProto({ answer: null, error: null });
    if (protoOf === null || !t.hole) return;
    fetchProtocol(protoOf, t.hole.name).then(
      (a) => alive && setProto({ answer: a, error: null }),
      (e: unknown) => alive && setProto({ answer: null, error: errorText(e) }),
    );
    return () => {
      alive = false;
    };
  }, [protoOf, t.hole]);

  if (t.job) return <JobView title={ore.title} job={t.job} winId={winId} onBack={() => t.setJob(null)} />;
  const shownProto = protoOf === null ? answer : proto.answer;
  return (
    <div className={css.win} data-testid="composites-ore">
      <div className={css.winBody}>
        <div className={own.split}>
          <div>
            <p className={css.note}>{ore.note}</p>
            <SourcePicker needs={ore.source} value={t.src} onChange={t.setSrc} onFields={t.setFields} activeSet={activeSet} />
            <ParamForm params={ore.params} values={t.values} errors={t.checked.errors} fields={ttFirst(t.fields)} onChange={t.setValues} placeholders={{ ...auto, name: DEFAULT_TABLE }} />
            {ttHint && (
              <p className={ttHint.stale ? css.warn : css.note} data-testid="ore-tt-hint">
                {ttHint.text}{" "}
                {ttHint.pick && (
                  <button type="button" className={css.btn} data-testid="ore-tt-pick" onClick={() => t.setValues({ ...t.values, thickness_field: ttHint.field })}>
                    Взять TT
                  </button>
                )}
              </p>
            )}
          </div>
          <fieldset className={css.group} data-testid="composites-preview">
            <legend>Предпросмотр</legend>
            <HolePicker setId={t.src.setId} value={t.hole?.id ?? null} onChange={(id, name) => t.setHole(id === null ? null : { id, name })} />
            <div className={own.tabs} role="tablist" aria-label="Предпросмотр рудных интервалов">
              {TABS.map((x) => (
                <button key={x.id} type="button" role="tab" className={own.tab} aria-selected={tab === x.id} data-testid={`ore-tab-${x.id}`} onClick={() => setTab(x.id)}>
                  {x.title}
                </button>
              ))}
            </div>
            {t.checked.reason && <p className={css.note}>Предпросмотр — когда заданы параметры: {t.checked.reason}</p>}
            {previewError && <p className={css.warn}>{previewError}</p>}
            {tab === "column" &&
              (answer ? (
                <>
                  <label className={css.check}>
                    <input type="checkbox" checked={showFrame} onChange={(e) => setShowFrame(e.target.checked)} />
                    Показать обрамление (Мпп с обеих сторон)
                  </label>
                  <OreColumns answer={answer} field={field} cutoff={cutoff} showFrame={showFrame} />
                </>
              ) : (
                !t.checked.reason && <p className={css.note}>{t.hole ? "Расчёт предпросмотра…" : "Выберите скважину"}</p>
              ))}
            {tab === "protocol" && (
              <>
                {results.length > 0 && (
                  <label className={css.formRow}>
                    <span>Протокол</span>
                    <select className={css.input} aria-label="Протокол чего" value={protoOf ?? ""} onChange={(e) => setProtoOf(e.target.value ? Number(e.target.value) : null)}>
                      <option value="">предпросмотра (до расчёта)</option>
                      {results.map((r) => (
                        <option key={r.id} value={r.id}>
                          результата «{r.name}»{r.stale ? " — устарел" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {proto.error && <p className={css.warn}>{proto.error}</p>}
                {shownProto ? <OreProtocol answer={shownProto} field={field} /> : !proto.error && <p className={css.note}>{t.hole ? "Чтение протокола…" : "Выберите скважину"}</p>}
                {chosen && <ProtocolExport key={chosen.id} result={chosen} hole={t.hole?.name ?? null} />}
              </>
            )}
            {tab === "summary" && (answer ? <OreSummaryView answer={answer} /> : <p className={css.note}>Сводка — после предпросмотра скважины.</p>)}
            {answer && answer.notes.length > 0 && (
              <ul className={own.notes}>
                {answer.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            )}
          </fieldset>
        </div>
        {t.error && (
          <p className={css.error} role="alert">
            {t.error}
          </p>
        )}
      </div>
      <ToolFooter
        winId={winId}
        reason={t.checked.reason}
        busy={t.busy}
        onApply={() => void t.apply()}
        extra={`Новая таблица «${(typeof t.values.name === "string" && t.values.name.trim()) || DEFAULT_TABLE}» и поля ORE_ID, ORE_CLS проб; отображение «${DISPLAY_NAME}» — в 3D.`}
      />
    </div>
  );
}
