/**
 * The window «Блок · i, j, k» (ТЗ 7.35 Д-30): the centre and the size of the block, the row of the plan (tabs when the plan has several rows for
 * the block), the status and the values of 8.8.2, the comparison of the recalculation with the fields written («Параметры или входы изменились»),
 * the table of the probes with the reasons (`Candidates.tsx`), what the 3D draws (`scene.ts`), «Скопировать снимок». Floating, not modal; the
 * block is asked by `estimate.inspect` or by a click on a block of a model with an estimation (`bind.ts`).
 */
import { useApp } from "../../../shell/AppContext";
import css from "../../../shell/PropsParts.module.css";
import { requestClose } from "../../../shell/windows";
import { blockById } from "../../blocks/state";
import { useSel } from "../hooks";
import type { Candidate, InspectResult } from "./api";
import { Candidates } from "./Candidates";
import mod from "./Inspect.module.css";
import { openInterval } from "./interval";
import { activeResult, inspectStore, reinspect, setActive, setNote, setShow, failText } from "./state";
import { blockTitle, COLORS, compareText, dash, numText, pointText, snapshotText, tabLabel, valueRows, weightSums } from "./view";

/** The snapshot to the clipboard; the note says whether it got there. */
export async function copySnapshot(): Promise<string> {
  const s = inspectStore.get();
  const r = activeResult(s);
  if (!s.answer || !r) return "Нет снимка: блок ещё не проверен";
  const text = snapshotText(s.answer, r);
  try {
    if (typeof navigator === "undefined" || !navigator.clipboard) throw new Error("Буфер обмена недоступен");
    await navigator.clipboard.writeText(text);
    return `Снимок блока скопирован: ${text.length} символов (параметры, пробы, веса)`;
  } catch (e) {
    return `Снимок не скопирован: ${failText(e)}`;
  }
}

function Values({ r }: { r: InspectResult }) {
  const rows = valueRows(r);
  return (
    <section className={mod.block} data-testid="inspect-values">
      <h3>Оценка блока</h3>
      <dl className={mod.values}>
        {rows.map((v) => (
          <div key={v.key} style={{ display: "contents" }}>
            <dt>{v.title}</dt>
            <dd data-testid={`inspect-value-${v.key}`}>{v.text}</dd>
          </div>
        ))}
        {r.mu !== null && (
          <div style={{ display: "contents" }}>
            <dt>μ — среднее простого кригинга</dt>
            <dd data-testid="inspect-value-mu">{numText(r.mu, 6)}</dd>
          </div>
        )}
      </dl>
    </section>
  );
}

function Passes({ r }: { r: InspectResult }) {
  const e = r.ellipsoid;
  const sums = weightSums(r.candidates.filter((c) => c.reason === "selected"));
  return (
    <section className={mod.block} data-testid="inspect-passes">
      <h3>Проходы поиска</h3>
      {r.passes.length === 0 ? (
        <p className={css.note}>Проходы не выполнялись.</p>
      ) : (
        <ul className={css.list}>
          {r.passes.map((p) => (
            <li key={p.pass} data-ok={p.ok}>
              Проход {p.pass}: проб {p.n} — {p.ok ? "принят" : p.reason === "few" ? "мало проб" : (p.reason ?? "не принят")}
            </li>
          ))}
        </ul>
      )}
      {e && (
        <p className={css.note} data-testid="inspect-ellipsoid">
          Эллипсоид прохода {e.pass || dash}: дальности {numText(e.r1, 2)} × {numText(e.r2, 2)} × {numText(e.r3, 2)} м; углы {numText(e.ang1, 1)}°, {numText(e.ang2, 1)}°, {numText(e.ang3, 1)}°
        </p>
      )}
      {sums.sum !== null && (
        <p className={css.note} data-testid="inspect-sum">
          Сумма весов {numText(sums.sum, 6)}
          {sums.negative < 0 ? `; отрицательные ${numText(sums.negative, 6)}` : ""}
        </p>
      )}
    </section>
  );
}

function Compare({ r }: { r: InspectResult }) {
  if (!r.fields.length) return null;
  return (
    <section className={mod.block} data-testid="inspect-fields">
      <h3>Сравнение с полями модели</h3>
      <div className={`${mod.tableWrap} ${mod.small}`}>
        <table className={mod.table}>
          <thead>
            <tr>
              <th>Поле</th>
              <th className={mod.num}>Записано</th>
              <th className={mod.num}>Пересчёт</th>
              <th>Итог</th>
            </tr>
          </thead>
          <tbody>
            {r.fields.map((f) => {
              const c = compareText(f);
              return (
                <tr key={f.key} data-testid={`inspect-field-${f.key}`} data-same={String(f.same)}>
                  <td title={f.name}>{f.name}</td>
                  <td className={mod.num}>{c.stored}</td>
                  <td className={mod.num}>{c.calc}</td>
                  <td className={f.same === false ? mod.bad : f.same ? mod.good : undefined}>{c.mark}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {r.missing_fields.length > 0 && <p className={css.warn}>В модели нет полей: {r.missing_fields.join(", ")}.</p>}
    </section>
  );
}

export function InspectWindow({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(inspectStore, (x) => x);
  useSel(app.store, (x) => x.projectName);
  const a = s.answer;
  const r = activeResult(s);
  const model = a ? blockById(a.model_id) : undefined;
  const changed = a?.results.filter((x) => x.changed) ?? [];

  const interval = (c: Candidate) => {
    if (!r) return;
    const id = openInterval(r, c);
    setNote(id ? `Открыто «Данные по скважинам»: скважина ${c.hole}, интервал ${numText(c.from, 3)} – ${numText(c.to, 3)} м` : "Для этой строки неизвестен набор скважин или у пробы нет скважины.");
  };

  return (
    <div className={mod.window} data-testid="estimate-inspect" data-state={s.loading ? "loading" : s.error ? "error" : a ? "ready" : "empty"}>
      <div className={mod.head}>
        {a ? (
          <dl data-testid="inspect-block">
            <dt>Блок</dt>
            <dd data-testid="inspect-ijk">{blockTitle(s.answer)}</dd>
            <dt>Центр</dt>
            <dd>{pointText(a.block.centre, 3)}</dd>
            <dt>Размер</dt>
            <dd>{a.block.size.map((v) => numText(v, 3)).join(" × ")} м</dd>
            {a.block.sub && (
              <>
                <dt>Субблок</dt>
                <dd>
                  смещение {a.block.sub.slice(0, 3).join(", ")}; родитель {a.block.parent ? `${a.block.parent.size.map((v) => numText(v, 3)).join(" × ")} м` : dash}
                </dd>
              </>
            )}
            <dt>Модель</dt>
            <dd>
              {model?.name ?? `№${a.model_id}`}
              {a.model_version !== null ? `, версия ${a.model_version}` : ""}
            </dd>
          </dl>
        ) : (
          <p className={css.note} data-testid="inspect-empty">
            {s.loading ? "Блок проверяется…" : "Щёлкните блок модели в 3D или выполните «Инспектор блока»."}
          </p>
        )}
        <div className={mod.actions}>
          <button type="button" className={css.btn} disabled={!s.request || s.loading} title="Пересчитать блок заново по сохранённым параметрам" data-testid="inspect-reread" onClick={() => void reinspect()}>
            Пересчитать
          </button>
          <button type="button" className={css.btn} disabled={!r} title="Скопировать JSON блока (параметры, пробы, веса) для ассистента и замечаний" data-testid="inspect-copy" onClick={() => void copySnapshot().then(setNote)}>
            Скопировать снимок
          </button>
        </div>
      </div>
      <div className={mod.body}>
        {s.error && (
          <p className={css.error} role="alert" data-testid="inspect-error" data-code={s.error.code}>
            {s.error.message} {s.error.hint}
          </p>
        )}
        {a?.warnings.map((w) => (
          <p key={w} className={css.warn} data-testid="inspect-warning">
            {w}
          </p>
        ))}
        {changed.length > 0 && (
          <p className={mod.banner} role="alert" data-testid="inspect-changed">
            <b>Параметры или входы изменились</b>
            <span>{(r?.changed ? r : changed[0]).warning_detail ?? "Пересчёт блока не совпал с записанными полями: запустите оценку снова."}</span>
          </p>
        )}
        {a && a.results.length > 1 && (
          <div className={mod.tabs} role="tablist" aria-label="Строки плана оценки" data-testid="inspect-tabs">
            {a.results.map((x) => (
              <button key={x.estimation_id} type="button" role="tab" className={mod.tab} aria-selected={x.estimation_id === r?.estimation_id} data-active={String(x.active)} data-changed={String(x.changed)} data-testid={`inspect-tab-${x.estimation_id}`} title={`${x.name}: ${x.status_label}`} onClick={() => setActive(x.estimation_id)}>
                {tabLabel(x)}
              </button>
            ))}
          </div>
        )}
        {a && a.results.length === 0 && (
          <p className={css.note} data-testid="inspect-no-results">
            Для блока нет строк «Параметры оценки», по которым он оценён. Выполните оценку и повторите.
          </p>
        )}
        {r && (
          <>
            <p className={mod.summary} data-testid="inspect-summary">
              <span>
                Строка <b>№{r.estimation_id} «{r.name}»</b>
              </span>
              <span>
                Статус <b data-testid="inspect-status" data-status={r.status ?? ""}>{r.status_label || dash}</b>
              </span>
              <span>
                Проход <b>{r.pass ? r.pass : dash}</b>
              </span>
              <span>
                Домен <b>{r.domain.label ?? dash}</b>
                {r.domain.row_label && r.domain.row_label !== r.domain.label ? `, блок: ${r.domain.row_label}` : ""}
              </span>
              <span>
                Проб в системе <b>{r.counts.selected}</b> из {r.counts.candidates}
              </span>
              {r.soft_dist !== null && r.soft_dist > 0 && <span>Мягкая граница d_м = {numText(r.soft_dist, 2)} м</span>}
            </p>
            {r.stale_reason && r.warning === null && (
              <p className={css.warn} data-testid="inspect-stale">
                Строка устарела: {r.stale_reason}
              </p>
            )}
            <div className={mod.two}>
              <Values r={r} />
              <Passes r={r} />
              <Compare r={r} />
            </div>
            <div className={mod.toggles} data-testid="inspect-3d">
              <strong>В 3D:</strong>
              <label>
                <input type="checkbox" checked={s.show.ellipsoid} data-testid="inspect-show-ellipsoid" onChange={(e) => setShow({ ellipsoid: e.target.checked })} /> эллипсоид прохода
              </label>
              <label>
                <input type="checkbox" checked={s.show.segments} data-testid="inspect-show-segments" onChange={(e) => setShow({ segments: e.target.checked })} /> отрезки к пробам
              </label>
              <label>
                <input type="checkbox" checked={s.show.excluded} data-testid="inspect-show-excluded" onChange={(e) => setShow({ excluded: e.target.checked })} /> исключённые
              </label>
              <span className={mod.legend}>
                <span>
                  <i className={mod.chip} style={{ background: COLORS.positive }} /> вес больше нуля
                </span>
                <span>
                  <i className={mod.chip} style={{ background: COLORS.negative }} /> вес меньше нуля
                </span>
                <span>
                  <i className={mod.chip} style={{ background: COLORS.excluded }} /> исключённые
                </span>
              </span>
            </div>
            <Candidates result={r} state={s} onInterval={interval} />
          </>
        )}
      </div>
      <div className={mod.footer}>
        <p className={mod.note} role="status" data-testid="inspect-note">
          {s.note ?? ""}
        </p>
        <button type="button" className={css.btn} data-testid="inspect-close" onClick={() => void requestClose(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
