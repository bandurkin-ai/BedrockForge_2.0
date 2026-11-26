/**
 * The window «Сравнить оценки» (ТЗ 7.35 Д-37, ТД37-2; ADR 0027 решение 25; вопросы № 108, 109): the model, the fields A and B (numeric: the estimates `*_EST` and others),
 * the domain (not required) and the name of the field Δ; «Сравнить» is the command `estimate.compare` (the same for Ctrl+K and the assistant), the job writes Δ = A − B into the
 * model. Then the statistics of Δ, the histogram of Δ (by the number of blocks), the table by the domains (the means of A, B and Δ, the blocks whose STATUS changed, «—» when
 * there is none) and the notes of the core. «Показать Δ в 3D» colours the field Δ with the display of the block models. A gap is never 0. Floating, not modal.
 */
import { useEffect, useSyncExternalStore } from "react";
import { useApp } from "../../../shell/AppContext";
import css from "../../../shell/PropsParts.module.css";
import { closeWindow } from "../../../shell/windows";
import { jobsStore } from "../../jobs/state";
import type { Store } from "../../../shell/store";
import { blocksStore } from "../state";
import { fmtCount } from "../report/format";
import { DeltaHistogram } from "./DeltaHistogram";
import { cancelCompare, chooseModel, showDelta } from "./bind";
import { categoryFields, defaultOut, numberFields } from "./model";
import { Domains, Stats, Words } from "./Parts";
import { compareStore, setForm, syncCompare } from "./state";
import mod from "./Compare.module.css";

/** A part of a store (with the server snapshot: the windows are also drawn to a string in the tests). */
function useSel<T, R>(store: Store<T>, select: (s: T) => R): R {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}

export function EstimateCompareWindow({ winId }: { winId: string }) {
  const app = useApp();
  const st = useSel(compareStore, (s) => s);
  const models = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const jobs = useSel(jobsStore, (s) => s.jobs);
  const readOnly = useSel(app.store, (s) => s.projectReadOnly);
  useEffect(() => {
    syncCompare();
  }, []);
  const f = st.form;
  const run = st.run;
  const model = f.modelId === null ? undefined : (details[f.modelId] ?? models.find((m) => m.id === f.modelId));
  const nums = numberFields(model);
  const cats = categoryFields(model);
  const busy = run.phase === "running";
  const job = run.job ? jobs.find((j) => j.source === run.job?.source && j.id === run.job?.id) : undefined;
  const r = run.result;
  const out = f.out.trim() || defaultOut(f.a, f.b);
  const runReason = app.registry.reason("estimate.compare") ?? (readOnly ? "Проект открыт только для чтения: поле Δ не записать" : f.modelId === null ? "Выберите блочную модель" : !f.a || !f.b ? "Выберите оба поля: A и B" : busy ? "Сравнение уже идёт" : null);
  const exists = !!out && !!model && model.fields.some((x) => x.name === out);
  const start = () => void app.run("estimate.compare", { model_id: f.modelId, field_a: f.a, field_b: f.b, ...(f.domain.trim() ? { domain: f.domain.trim() } : {}), ...(f.out.trim() ? { out: f.out.trim() } : {}) }).catch(() => undefined);
  const to3d = () => void showDelta(app).catch(() => undefined);

  const pick = (key: "a" | "b", title: string) => (
    <label>
      <span>{title}</span>
      <select className={mod.select} value={f[key]} disabled={busy} aria-label={title} data-testid={`ecmp-${key}`} onChange={(e) => setForm({ [key]: e.target.value })}>
        <option value="">— выберите —</option>
        {nums.map((n) => (
          <option key={n} value={n}>
            {n}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className={mod.window} data-testid="ecmp-window">
      <div className={mod.bar}>
        <label>
          <span>Блочная модель</span>
          <select className={mod.select} value={f.modelId ?? ""} disabled={busy} aria-label="Блочная модель" data-testid="ecmp-model" onChange={(e) => chooseModel(e.target.value ? Number(e.target.value) : null)}>
            <option value="">— выберите —</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        {pick("a", "Поле A")}
        {pick("b", "Поле B")}
        <label>
          <span>Домен (не обязательно)</span>
          <select className={mod.select} value={f.domain} disabled={busy} aria-label="Поле доменов" data-testid="ecmp-domain-field" onChange={(e) => setForm({ domain: e.target.value })}>
            <option value="">— по умолчанию —</option>
            {cats.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Имя поля Δ</span>
          <input className={mod.input} value={f.out} placeholder={defaultOut(f.a, f.b) || "<A>_<B>_D"} disabled={busy} aria-label="Имя поля Δ" aria-invalid={exists || undefined} title={exists ? "Поле с таким именем уже есть: оно будет заменено" : "Новое поле модели: Δ = A − B"} data-testid="ecmp-out" onChange={(e) => setForm({ out: e.target.value })} />
        </label>
        <div className={mod.actions}>
          <button type="button" className={css.primary} disabled={runReason !== null} title={runReason ?? "Δ = A − B по каждому блоку: поле Δ записывается в модель, где в A или в B пропуск — в Δ тоже пропуск"} data-testid="ecmp-run" onClick={start}>
            Сравнить
          </button>
          {busy && (
            <button type="button" className={css.btn} title="Отменить: поле Δ не будет записано" data-testid="ecmp-cancel" onClick={() => void cancelCompare()}>
              Отменить
            </button>
          )}
        </div>
      </div>
      <div className={mod.main}>
        {exists && !busy && (
          <p className={css.note} data-testid="ecmp-replace">
            Поле «{out}» уже есть в модели: сравнение заменит его.
          </p>
        )}
        {run.error && (
          <p className={css.error} role="alert" data-testid="ecmp-error" data-code={run.error.code}>
            {run.error.message} {run.error.hint}
          </p>
        )}
        {run.phase === "cancelled" && (
          <p className={css.note} role="status" data-testid="ecmp-cancelled">
            Сравнение отменено: поле Δ не записано.
          </p>
        )}
        {run.phase === "idle" && (
          <p className={mod.empty} data-testid="ecmp-hint">
            {models.length ? "Выберите модель и два поля оценки (например AU_OK_EST и AU_ID_EST) и нажмите «Сравнить»: в модель запишется поле разности Δ = A − B, а ниже появятся её статистика, гистограмма и средние по доменам." : "В проекте нет блочных моделей."}
          </p>
        )}
        {r && (
          <>
            <p className={mod.info} data-testid="ecmp-info">
              Δ = {r.a} − {r.b} · поле «{r.field}» {r.field_written ? "записано в модель" : "не записано"} · блоков с Δ: {fmtCount(r.stats.n)}
              {r.rows !== null ? ` из ${fmtCount(r.rows)}` : ""}
              {r.status ? ` · STATUS сменился у ${fmtCount(r.status.changed)} бл.` : ""}
            </p>
            <Words result={r} />
            <Stats result={r} />
            <h5 className={mod.section}>Гистограмма Δ по числу блоков</h5>
            <DeltaHistogram result={r} />
            <h5 className={mod.section}>По доменам</h5>
            <Domains result={r} />
          </>
        )}
      </div>
      <div className={mod.footer}>
        {busy && (
          <div className={mod.progress} data-testid="ecmp-progress" role="status">
            <progress max={1} value={job?.progress ?? 0} aria-label="Ход сравнения" />
            <span className={mod.stage}>{job?.message ?? (job ? "Выполняется" : "Запуск")}</span>
          </div>
        )}
        {st.note && (
          <p className={css.note} role="status" data-testid="ecmp-3d-note">
            {st.note}
          </p>
        )}
        <div className={mod.footRow}>
          <button type="button" className={css.btn} disabled={!r || !r.field_written || busy} title={r?.field_written ? "Показать блочную модель в 3D, раскрашенную полем Δ: шкала симметрична, нуль — середина, серые блоки — пропуск" : "Сначала выполните сравнение: поле Δ должно быть записано в модель"} data-testid="ecmp-3d" onClick={to3d}>
            Показать Δ в 3D
          </button>
          <span className={mod.grow} />
          <button type="button" className={css.btn} data-testid="ecmp-close" onClick={() => closeWindow(winId)}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
