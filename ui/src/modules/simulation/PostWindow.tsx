/**
 * The window «Постобработка реализаций» (ТЗ 8.8.1, Д-23; docs/progress/e18-api.md §9): what is written to the block model from the
 * realizations of a simulation. A continuous variable: E-type (MEAN), STD, P10, P50, P90 and the share of the realizations not below a
 * threshold t (PROB_GT_t). The categories of SIS: the share of every category (PROB_код) and the most frequent one (MODE). The prefix
 * (by default «‹ПОЛЕ›_SGS_», «‹ПОЛЕ›_SIS_», «‹ПОЛЕ›_COSGS_»); the fields that the model has already are told before the run and asked about
 * by the command («Заменить поля?»). «Выполнить» runs the command `simulate.post` — the same as the keys and the assistant.
 */
import { useEffect } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { blocksStore, loadBlockDetails } from "../blocks/state";
import { useSel } from "../estimation/hooks";
import mod from "../estimation/Wizard.module.css";
import { cancelJob } from "../jobs/state";
import { CAT_STAT_TITLES, STAT_TITLES, choosePost, defaultPostPrefix, effectiveStats, existingFields, fieldNames, postStore, postThresholds, setPost, sourceOf, statsFor, type PostState } from "./postState";
import { CAT_STATS, KIND_SHORT, POST_STATS, type ContStat, type PostStat } from "./api";
import { isIssue } from "../estimation/formBase";
import { loadDetails, rowsByName, simulationStore } from "./state";

/** The arguments of `simulate.post` the form stands for (what is not touched is absent: the core's defaults). */
export function postArgsOf(simId: number, p: Pick<PostState, "prefix" | "stats" | "thresholds" | "replace">): Record<string, unknown> {
  return {
    id: simId,
    run: true,
    ...(p.prefix.trim() ? { prefix: p.prefix.trim() } : {}),
    ...(p.stats ? { stats: p.stats.join(";") } : {}),
    ...(p.thresholds.trim() ? { thresholds: p.thresholds.trim() } : {}),
    ...(p.replace ? { replace: true } : {}),
  };
}

export function PostWindow(_props: { winId: string }) {
  const app = useApp();
  const p = useSel(postStore, (s) => s);
  const rows = useSel(simulationStore, (s) => s.rows);
  const details = useSel(simulationStore, (s) => s.details);
  const blocks = useSel(blocksStore, (s) => s.details);
  const readOnly = useSel(app.store, (s) => s.projectReadOnly);
  const row = rows.find((r) => r.id === p.simId);
  const info = p.simId === null ? undefined : details[p.simId];
  const src = info ? sourceOf(info.meta) : null;
  useEffect(() => {
    if (p.simId !== null && !details[p.simId]) void loadDetails(p.simId).catch(() => undefined);
  }, [p.simId]);
  useEffect(() => {
    if (row) void loadBlockDetails(row.model_id);
  }, [row?.model_id]);

  const stats = src ? effectiveStats(src, p.stats) : [];
  const prefix = p.prefix.trim() || (src ? defaultPostPrefix(src) : "");
  const t = postThresholds(p.thresholds);
  const names = src ? fieldNames(prefix, stats, isIssue(t) ? [] : t, src.categories) : [];
  const have = existingFields(blocks[row?.model_id ?? -1]?.fields.map((f) => f.name) ?? [], names);
  const busy = p.phase === "running";
  const noRun = row && !row.last_run;
  const reason = readOnly ? "Проект открыт только для чтения" : busy ? "Постобработка уже выполняется" : !row ? "Выберите моделирование" : noRun ? "У моделирования нет реализаций: сначала выполните его" : null;
  const toggle = (k: PostStat, on: boolean) => src && setPost({ stats: statsFor(src).filter((x) => (x === k ? on : stats.includes(x))) });
  const titleOf = (k: PostStat): string => (src?.categories ? CAT_STAT_TITLES[k as "prob" | "mode"] : STAT_TITLES[k as ContStat]);
  const go = () => row && void app.run("simulate.post", postArgsOf(row.id, p)).catch(() => undefined); // a refusal stands under its field, the run — in the store
  /** The words under a field of the form: the first wrong thing of the form or the refusal of the core, with the place of the body. */
  const issue = (at: string, id: string) => {
    const i = p.issue && p.issue.param !== null && (p.issue.param === at || p.issue.param.startsWith(`${at}.`)) ? p.issue : null;
    return i ? (
      <p className={css.error} role="alert" data-testid={`post-issue-${id}`} data-code={i.code}>
        {i.message} {i.hint}
      </p>
    ) : null;
  };

  return (
    <div className={mod.window} data-testid="simulation-post">
      <div className={mod.page} style={{ padding: "8px 12px", flex: 1 }}>
        <label className={css.formRow}>
          <span>Моделирование</span>
          <select className={css.input} value={p.simId ?? ""} aria-label="Моделирование" data-testid="post-select" onChange={(e) => choosePost(e.target.value ? Number(e.target.value) : null)}>
            <option value="">{rows.length ? "— выберите моделирование —" : "нет моделирований в проекте"}</option>
            {rowsByName(rows).map((r) => (
              <option key={r.id} value={r.id}>
                {r.name} · {KIND_SHORT[r.kind]}
              </option>
            ))}
          </select>
        </label>
        {row && src && (
          <p className={css.note} data-testid="post-info">
            {KIND_SHORT[src.kind]}, поле «{src.field}», реализаций {row.last_run ? row.last_run.realizations : "нет"}; поля пишутся в блочную модель №{row.model_id}
            {blocks[row.model_id] ? ` «${blocks[row.model_id].name}»` : ""}.
          </p>
        )}
        {row?.stale && (
          <p className={css.note} data-testid="post-stale">
            Строка устарела{row.stale_reason ? `: ${row.stale_reason}` : ""}: поля будут записаны по прежним реализациям.
          </p>
        )}
        {noRun && (
          <p className={css.note} data-testid="post-empty">
            У моделирования «{row.name}» нет реализаций: выполните его, затем повторите постобработку.
          </p>
        )}
        {src && (
          <>
            <fieldset className={css.group} data-testid="post-stats">
              <legend>Что записать</legend>
              {(src.categories ? CAT_STATS : POST_STATS).map((k) => (
                <label key={k} className={css.check}>
                  <input type="checkbox" checked={stats.includes(k)} data-testid={`post-stat-${k}`} onChange={(e) => toggle(k, e.target.checked)} />
                  {titleOf(k)}
                </label>
              ))}
              {issue("thresholds", "thresholds")}
              {issue("stats", "stats")}
              {!src.categories && stats.includes("prob") && (
                <>
                  <label className={css.formRow}>
                    <span>Пороги t (через «;»)</span>
                    <input className={css.input} value={p.thresholds} placeholder={src.thresholds.length ? src.thresholds.join("; ").replace(/\./g, ",") : "0,5; 1; 2,5"} spellCheck={false} aria-label="Пороги" data-testid="post-thresholds" onChange={(e) => setPost({ thresholds: e.target.value })} />
                  </label>
                  <span className={css.note}>PROB_GT_t — доля реализаций со значением не ниже t в узле.</span>
                </>
              )}
            </fieldset>
            <fieldset className={css.group}>
              <legend>Поля модели</legend>
              <label className={css.formRow}>
                <span>Префикс</span>
                <input className={css.input} value={p.prefix} placeholder={defaultPostPrefix(src)} spellCheck={false} aria-label="Префикс полей" data-testid="post-prefix" onChange={(e) => setPost({ prefix: e.target.value })} />
              </label>
              {issue("prefix", "prefix")}
              <p className={css.note} data-testid="post-names">
                Будут записаны: {names.length ? names.join(", ") : "—"}.
              </p>
              {have.length > 0 && (
                <p className={css.note} data-testid="post-have">
                  В модели уже есть: {have.join(", ")}. {p.replace ? "Они будут заменены." : "Перед записью программа спросит о замене."}
                </p>
              )}
              <label className={css.check}>
                <input type="checkbox" checked={p.replace} data-testid="post-replace" onChange={(e) => setPost({ replace: e.target.checked })} />
                Заменить поля, которые уже есть в модели, без вопроса
              </label>
            </fieldset>
          </>
        )}
        {p.error && (
          <p className={css.error} role="alert" data-testid="post-error" data-code={p.error.code}>
            {p.error.message} {p.error.hint}
          </p>
        )}
        {busy && (
          <div className={mod.links} role="status" data-testid="post-progress">
            <progress max={1} aria-label="Ход постобработки" />
            <span className={css.note}>Выполняется</span>
          </div>
        )}
        {p.phase === "done" && p.result && (
          <div data-testid="post-done">
            <p className={mod.verdict}>
              Записано в модель №{p.result.model_id} (версия {p.result.version}): {p.result.fields.join(", ")}.
            </p>
            <ul className={mod.warn} style={{ color: "var(--text)" }}>
              {p.result.log.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
      <div className={mod.footer}>
        <span className={mod.grow} />
        {busy && p.job && (
          <button type="button" className={css.btn} data-testid="post-cancel" title="Отменить: поля не запишутся" onClick={() => p.job && void cancelJob(p.job).catch(() => undefined)}>
            Отменить
          </button>
        )}
        <button type="button" className={css.primary} disabled={reason !== null} title={reason ?? "Записать поля в блочную модель"} data-testid="post-go" onClick={go}>
          Выполнить
        </button>
      </div>
    </div>
  );
}
