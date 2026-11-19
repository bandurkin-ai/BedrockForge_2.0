/**
 * The window «Каркас по серии» (ТЗ 8.1.4; ADR 0016 §8): the contours of the series by sections with their links
 * (the automatic ones of the core first), a click on a row picks a contour, a click on a contour of a neighbouring
 * section links or unlinks it with the picked one (1 → N: the picked one stays); the links are drawn in the 3D
 * window between the centres of the contours; the caps at the ends of the bodies (4 kinds), «Одним каркасом»;
 * «Создать каркас» runs `model.operation stitch_series` through the registry, then the window follows the job —
 * the state, the error naming the sections, «Отчёт». The logic is `seriesStitch.ts`.
 */
import { useEffect, useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import { jobsStore, STATUS_TITLES } from "../jobs/state";
import { openReport, reportProps, startsStore, type Started } from "../model/run";
import {
  bodiesOf, CAP_TITLES, clickContour, conflictsOf, contourTitle, initialState, linkLines, loadLinks, resetLinks, sectionsText, showLinks, stitchParams,
  type CapMode, type StitchState,
} from "./seriesStitch";

const CAPS = Object.keys(CAP_TITLES) as CapMode[];
const HOW_TITLES = { overlap: "перекрытие", centres: "центры ≤ Δ", manual: "вручную" } as const;
const area = (v: number | null) => (v === null ? "—" : v.toLocaleString("ru-RU", { maximumFractionDigits: 1 }));

export function SeriesStitchWindow({ winId, seriesId }: { winId: string; seriesId: number }) {
  const app = useApp();
  const [st, setSt] = useState<StitchState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [started, setStarted] = useState<Started | null>(null);

  const load = (keep?: StitchState | null) => {
    setError(null);
    loadLinks(seriesId, keep?.table.layers.map((l) => l.id))
      .then((t) => setSt(keep ? { ...initialState(t), start: keep.start, end: keep.end, single: keep.single } : initialState(t)))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };
  useEffect(() => load(), [seriesId]); // eslint-disable-line react-hooks/exhaustive-deps -- once per series
  useEffect(() => {
    void showLinks(st ? linkLines(st.table, st.pairs) : null);
  }, [st?.table, st?.pairs]); // eslint-disable-line react-hooks/exhaustive-deps -- the lines follow the links
  useEffect(() => () => void showLinks(null), []);

  const bodies = useMemo(() => (st ? bodiesOf(st.table, st.pairs) : []), [st?.table, st?.pairs]); // eslint-disable-line react-hooks/exhaustive-deps
  const conflicts = useMemo(() => (st ? conflictsOf(st.table, st.pairs) : []), [st?.table, st?.pairs]); // eslint-disable-line react-hooks/exhaustive-deps

  if (started) return <Follow winId={winId} started={started} onBack={() => setStarted(null)} />;
  if (!st) return <div className={css.win}><div className={css.winBody}>{error ? <p className={css.error} role="alert">{error}</p> : <p className={css.note}>Контуры серии и автосвязи…</p>}</div></div>;

  const t = st.table;
  const rows = t.contours.filter((c) => c.k >= 0 && c.uw !== null).sort((a, b) => a.k - b.k || a.lid - b.lid);
  const linked = (i: number) => st.pairs.filter(([a, b]) => a === i || b === i).map(([a, b]) => ({ other: a === i ? b : a, how: st.how[`${a}:${b}`] }));
  const skipped = t.skipped.open + t.skipped.short + t.skipped.outside + t.skipped.no_height;
  const run = async () => {
    setBusy(true);
    setError(null);
    const before = startsStore.get().seq;
    const r = await app.registry.execute("model.operation", { op: "stitch_series", params: JSON.stringify(stitchParams(st)) });
    setBusy(false);
    const s = startsStore.get();
    if (!r.ok) return setError(r.result);
    if (s.seq > before && s.last) setStarted(s.last);
  };

  return (
    <div className={css.win} data-testid="series-stitch">
      <div className={css.winBody}>
        <p className={css.note}>
          Слои: {t.layers.map((l) => `«${l.name}»`).join(", ") || "—"} · контуров {rows.length} · связей {st.pairs.length}
          {st.edited ? " (правлены)" : " (автоматические)"} · тел <b data-testid="stitch-bodies">{bodies.length}</b>
        </p>
        <div className={css.tableWrap} style={{ maxHeight: 300 }}>
          <table className={css.table} data-testid="stitch-table">
            <thead>
              <tr>
                <th>Разрез</th>
                <th>Контур</th>
                <th>Площадь, м²</th>
                <th>Связи</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.i} className={st.picked === c.i ? css.picked : undefined} data-testid={`stitch-contour-${c.i}`} aria-selected={st.picked === c.i} style={{ cursor: "pointer" }} onClick={() => setSt(clickContour(st, c.i))}>
                  <td>{c.name ?? c.k + 1}</td>
                  <td title={c.off_plane !== null && c.off_plane > t.off_plane_limit ? `Вершины дальше ${t.off_plane_limit} м от плоскости разреза` : undefined}>
                    {contourTitle(t, c.i).split(",")[0]}
                    {c.off_plane !== null && c.off_plane > t.off_plane_limit ? " · вне плоскости" : ""}
                  </td>
                  <td>{area(c.area)}</td>
                  <td>{linked(c.i).map((l) => `${contourTitle(t, l.other)} (${HOW_TITLES[l.how ?? "manual"]})`).join("; ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className={css.note}>Щелчок по контуру — выбрать; щелчок по контуру соседнего разреза — связать или развязать с выбранным (выбранный остаётся: 1 → N). Линии связей — в 3D-окне.</p>
        {conflicts.map((g) => (
          <p key={g.contours.join(",")} className={css.error} role="alert" data-testid="stitch-conflict">
            Связи N : M {sectionsText(t, g.sections)} ({g.contours.map((i) => contourTitle(t, i).split(",")[0]).join(", ")}): разделите связи — так каркас не строится
          </p>
        ))}
        {skipped > 0 && (
          <p className={css.note}>
            Пропущено контуров: незамкнутых {t.skipped.open}, вырожденных {t.skipped.short}, вне окон разрезов {t.skipped.outside}, без высот {t.skipped.no_height}
          </p>
        )}
        {t.warnings.map((w) => (
          <p key={w} className={css.warn}>
            {w}
          </p>
        ))}
        <div className={css.pair}>
          <label className={css.field}>
            <span>Начальный торец</span>
            <select className={css.input} value={st.start} data-testid="stitch-start" onChange={(e) => setSt({ ...st, start: e.target.value as CapMode })}>
              {CAPS.map((c) => <option key={c} value={c}>{CAP_TITLES[c]}</option>)}
            </select>
          </label>
          <label className={css.field}>
            <span>Конечный торец</span>
            <select className={css.input} value={st.end} data-testid="stitch-end" onChange={(e) => setSt({ ...st, end: e.target.value as CapMode })}>
              {CAPS.map((c) => <option key={c} value={c}>{CAP_TITLES[c]}</option>)}
            </select>
          </label>
        </div>
        <label className={css.check}>
          <input type="checkbox" checked={st.single} data-testid="stitch-single" onChange={(e) => setSt({ ...st, single: e.target.checked })} />
          Одним каркасом (тела — частями одного каркаса, без сшивки вершин)
        </label>
        {error && <p className={css.error} role="alert" data-testid="stitch-error">{error}</p>}
      </div>
      <div className={css.footer}>
        <span className={`${css.note} ${css.grow}`} data-testid="stitch-message">{st.message ?? "Каркас — задание: ход и журнал — на вкладке «Задания»."}</span>
        <button type="button" className={css.btn} onClick={() => load(st)}>Обновить</button>
        <button type="button" className={css.btn} disabled={!st.edited} data-testid="stitch-reset" onClick={() => setSt(resetLinks(st))}>Автосвязи</button>
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>Отмена</button>
        <button type="button" className={css.primary} disabled={busy || !bodies.length || conflicts.length > 0} data-testid="stitch-run" onClick={() => void run()}>Создать каркас</button>
      </div>
    </div>
  );
}

/** The job of «Создать каркас»: the state, «Отменить»; the error with the names of the sections; the report. */
function Follow({ winId, started, onBack }: { winId: string; started: Started; onBack: () => void }) {
  const app = useApp();
  const job = useStore(jobsStore, (s) => s.jobs.find((j) => j.source === started.source && j.id === started.id));
  const status = job?.status ?? "queued";
  const active = status === "queued" || status === "running";
  return (
    <div className={css.win} data-testid="stitch-follow" data-status={status}>
      <div className={css.winBody}>
        <p>
          «{started.title}» — задание №{started.id}: <b>{STATUS_TITLES[status]}</b>
        </p>
        {active && <progress max={1} value={job?.progress ?? 0} aria-label="Ход сшивки" style={{ width: "100%" }} />}
        {started.warning && <p className={css.warn}>{started.warning}</p>}
        {status === "done" && <p className={css.note}>Каркас построен: отчёт — в отдельном окне, проверка поставлена в очередь.</p>}
        {status === "failed" && job?.error && (
          <p className={css.error} role="alert" data-testid="stitch-failed">
            {job.error.message} {job.error.hint}
          </p>
        )}
        {status === "cancelled" && <p className={css.note}>Сшивка отменена: ничего не записано.</p>}
      </div>
      <div className={css.footer}>
        <span className={css.grow} />
        {active && <button type="button" className={css.btn} onClick={() => void app.run("jobs.cancel", { job_id: started.id })}>Отменить</button>}
        {(status === "failed" || status === "cancelled") && <button type="button" className={css.btn} onClick={onBack}>К связям</button>}
        {status === "done" && job && <button type="button" className={css.btn} onClick={() => openReport(reportProps(job, started))}>Отчёт</button>}
        <button type="button" className={css.primary} onClick={() => closeWindow(winId)}>Закрыть</button>
      </div>
    </div>
  );
}
