/**
 * Step 4 of the wizard «Оценка содержаний» (ТЗ 8.8.2, 8.8.4; Д-29): the ellipsoid of the search — from the model of variogram, from a
 * trend or by hand (the angles and the radii) — and the passes (≤ 5): the multiplier of the radii, n_min, n_max, the limits of a hole
 * and of the octants, the least number of holes, and the rule of the gold-grade samples (a sample above t farther than q·r1 is dropped
 * or capped to t; off by default). The passes the person did not touch are the core's own (`defaults`). In 3D the ellipsoid, the
 * samples chosen and their weights of the block picked are shown by the layer of 18.21 over the same preview (step 6).
 */
import css from "../../shell/PropsParts.module.css";
import { loadTrends, type Trend } from "../trends/api";
import { PASSES_MAX } from "./formBase";
import { effectiveFrom, hasVariogram, passesOf, type SearchFrom, type Tri } from "./formSearch";
import { useLatest, useSel } from "./hooks";
import { IssueAt, Triple } from "./Parts";
import { chooseView, chosenPass, previewSceneStore } from "./preview";
import mod from "./Wizard.module.css";
import { addPass, removePass, resetPasses, setHy, setPass, setSearch, wizardStore } from "./wizardState";

const SOURCES: [SearchFrom, string, string][] = [
  ["model", "По модели вариограммы", "Углы и радиусы — у структуры модели с наибольшей дальностью."],
  ["trend", "По тренду", "Углы тренда; радиус r1 — заданный или размер тренда, r2 и r3 — по отношениям тренда."],
  ["manual", "Вручную", "Углы и три радиуса задаёт человек; r2 и r3 без значения равны r1."],
];

const ORDER = "эллипсоид → правило ураганных проб → сортировка по расстоянию → n_max, лимиты скважины и октантов → n_min";

type PassNum = "factor" | "nMin" | "nMax" | "nHole" | "nOct" | "kOct" | "nHolesMin";
const PASS_INPUTS: [PassNum, string, string, string][] = [
  ["factor", "Радиусы ×", "factor", "Множитель радиусов эллипсоида в этом проходе (0–100)"],
  ["nMin", "n_min", "n-min", "Наименьшее число проб, 1–128"],
  ["nMax", "n_max", "n-max", "Наибольшее число проб, 1–128"],
  ["nHole", "n_скв", "n-hole", "Наибольшее число проб из одной скважины, 0–64 (0 — без ограничения)"],
  ["nOct", "n_окт", "n-oct", "Наибольшее число проб в октанте, 0–16 (0 — без октантов)"],
  ["kOct", "k_окт", "k-oct", "Наименьшее число заполненных октантов, 0–8 (0 — без ограничения)"],
  ["nHolesMin", "Скважин ≥", "n-holes-min", "Наименьшее число скважин в оценке, 0–128 (0 — без ограничения)"],
];

export function StepSearch() {
  const f = useSel(wizardStore, (s) => s.form);
  const data = useSel(wizardStore, (s) => s.defaults.data);
  const trends = useLatest<Trend[]>(async () => (await loadTrends()).filter((t) => t.kind === "global"), []);
  const hasModel = hasVariogram(f);
  const view = useSel(previewSceneStore, (x) => x);
  const shown = useSel(wizardStore, (x) => x.preview.data);
  const from = effectiveFrom(f.search, hasModel);
  const passes = passesOf(f.search, data?.params);
  const typed = f.search.passes !== null;
  const model = data?.search;
  const trend = trends?.find((t) => t.id === f.search.trendId);
  const fmt = (n: number | null) => (n === null ? "—" : n.toLocaleString("ru-RU", { maximumSignificantDigits: 5 }));

  return (
    <>
      <fieldset className={css.group} data-testid="est-step-4">
        <legend>Эллипсоид поиска</legend>
        <div className={mod.radios} role="radiogroup" aria-label="Источник эллипсоида">
          {SOURCES.map(([k, title, hint]) => (
            <label key={k} title={hint}>
              <input type="radio" name="est-search-from" checked={from === k} data-testid={`est-search-from-${k}`} onChange={() => setSearch({ from: k })} />
              {title}
            </label>
          ))}
        </div>
        <span className={css.note}>{SOURCES.find(([k]) => k === from)?.[2]}</span>
        {from === "model" && (
          <p className={css.note} data-testid="est-search-model">
            {hasModel ? (model ? `Из модели: углы ${[model.ang1, model.ang2, model.ang3].map(fmt).join(" / ")}°, радиусы ${[model.r1, model.r2, model.r3].map(fmt).join(" × ")} м.` : "Углы и радиусы ядро возьмёт из выбранной модели вариограммы.") : "Модель вариограммы не выбрана (шаг «Метод и вариограмма»): выберите её или задайте эллипсоид вручную."}{" "}
            {model && (
              <button type="button" className={css.btn} data-testid="est-search-copy" title="Перенести числа модели в ручной ввод" onClick={() => setSearch({ from: "manual", ang: [model.ang1, model.ang2, model.ang3].map((x) => (x === null ? "" : fmt(x))) as Tri, r: [model.r1, model.r2, model.r3].map((x) => (x === null ? "" : fmt(x))) as Tri })}>
                Править вручную
              </button>
            )}
          </p>
        )}
        {from === "trend" && (
          <>
            <label className={css.formRow}>
              <span>Тренд</span>
              <select className={css.input} aria-label="Тренд" data-testid="est-trend" value={f.search.trendId ?? ""} onChange={(e) => setSearch({ trendId: e.target.value ? Number(e.target.value) : null })}>
                <option value="">{trends ? (trends.length ? "— выберите тренд —" : "нет трендов в проекте") : "Чтение…"}</option>
                {(trends ?? []).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <IssueAt at="trend_id" id="trend" />
            <label className={css.formRow}>
              <span>Радиус r1, м</span>
              <input className={css.input} value={f.search.r[0]} placeholder={trend ? fmt(trend.size) : "размер тренда"} inputMode="decimal" aria-label="Радиус r1" data-testid="est-trend-r1" onChange={(e) => setSearch({ r: [e.target.value, "", ""] })} />
            </label>
            {trend && (
              <span className={css.note} data-testid="est-trend-info">
                Углы тренда {[trend.ang1, trend.ang2, trend.ang3].map(fmt).join(" / ")}°, отношения 1 : {fmt(trend.ratios[1])} : {fmt(trend.ratios[2])}.
              </span>
            )}
          </>
        )}
        {from === "manual" && (
          <>
            <Triple labels={["Угол 1 (азимут), °", "Угол 2 (падение), °", "Угол 3 (поворот), °"]} values={f.search.ang} onChange={(ang) => setSearch({ ang })} testId="est-ang" />
            <Triple labels={["Радиус r1, м", "Радиус r2, м", "Радиус r3, м"]} values={f.search.r} onChange={(r) => setSearch({ r })} testId="est-r" />
            <span className={css.note}>Углы — как в вариографии: азимут 0–360°, падение −90…90°, поворот −180…180°; пусто — 0. Радиус r1 обязателен, r2 и r3 без значения равны r1.</span>
          </>
        )}
        <IssueAt at={["search.from", "search.ang1", "search.ang2", "search.ang3", "search.r1", "search.r2", "search.r3"]} id="search" />
        <label className={css.formRow} title="Какой проход рисовать в 3D-окне: эллипсоид, блок и отобранные пробы предпросмотра">
          <span>В 3D-окне</span>
          <select className={css.input} aria-label="Эллипсоид в 3D" data-testid="est-3d-pass" value={view.wizard ? (view.pass === null ? "auto" : String(view.pass)) : "off"} onChange={(e) => chooseView(e.target.value)}>
            <option value="auto">эллипсоид прохода, давшего оценку</option>
            {passes.map((_, i) => (
              <option key={i} value={String(i + 1)}>
                эллипсоид прохода {i + 1}
              </option>
            ))}
            <option value="off">не показывать</option>
          </select>
        </label>
        <span className={css.note} data-testid="est-3d-note">
          {shown ? `Эллипсоид прохода ${chosenPass(view.pass, shown, passes.length)} стоит в центре блока предпросмотра и следует за правкой радиусов и углов; пробы — как при последнем показе.` : "Покажите блок на шаге «Предпросмотр и запуск»: эллипсоид, блок и пробы с весами появятся в 3D-окне."}
        </span>
      </fieldset>

      <fieldset className={css.group} data-testid="est-passes">
        <legend>Проходы</legend>
        <span className={css.note}>
          Блок оценивается первым проходом, у которого хватило проб; не хватило — берётся следующий, с большим радиусом. До {PASSES_MAX} проходов.
          {typed ? "" : " Сейчас — проходы по умолчанию."}
        </span>
        {passes.map((p, i) => (
          <div key={i} className={mod.pass} data-testid={`est-pass-${i}`}>
            <div className={mod.passHead}>
              <strong>Проход {i + 1}</strong>
              <button type="button" className={css.btn} disabled={passes.length <= 1} data-testid={`est-pass-${i}-remove`} title={passes.length <= 1 ? "Нужен хотя бы один проход" : "Убрать проход"} onClick={() => removePass(i)}>
                Убрать
              </button>
            </div>
            <div className={mod.passGrid}>
              {PASS_INPUTS.map(([prop, label, id, hint]) => (
                <label key={prop} title={hint}>
                  <span>{label}</span>
                  <input className={css.input} value={p[prop]} inputMode="decimal" spellCheck={false} aria-label={`Проход ${i + 1}: ${label}`} data-testid={`est-pass-${i}-${id}`} onChange={(e) => setPass(i, { [prop]: e.target.value })} />
                </label>
              ))}
            </div>
            <label className={mod.hyOn}>
              <input type="checkbox" checked={p.hy.on} data-testid={`est-pass-${i}-hy-on`} onChange={(e) => setHy(i, { on: e.target.checked })} />
              Ограничить ураганные пробы по расстоянию
            </label>
            {p.hy.on && (
              <div className={mod.passGrid} data-testid={`est-pass-${i}-hy`}>
                <label title="Проба со значением выше t влияет на оценку без изменений только вблизи блока">
                  <span>Порог t</span>
                  <input className={css.input} value={p.hy.t} inputMode="decimal" spellCheck={false} aria-label={`Проход ${i + 1}: порог ураганных проб t`} data-testid={`est-pass-${i}-hy-t`} onChange={(e) => setHy(i, { t: e.target.value })} />
                </label>
                <label title="Доля радиуса r1 прохода: дальше неё ураганная проба отбрасывается или урезается (0–1)">
                  <span>Доля радиуса q</span>
                  <input className={css.input} value={p.hy.q} inputMode="decimal" spellCheck={false} aria-label={`Проход ${i + 1}: доля радиуса q`} data-testid={`est-pass-${i}-hy-q`} onChange={(e) => setHy(i, { q: e.target.value })} />
                </label>
                <label>
                  <span>Дальше q·r1</span>
                  <select className={css.input} value={p.hy.mode} aria-label={`Проход ${i + 1}: режим`} data-testid={`est-pass-${i}-hy-mode`} onChange={(e) => setHy(i, { mode: e.target.value === "cap" ? "cap" : "drop" })}>
                    <option value="drop">отбросить пробу</option>
                    <option value="cap">урезать значение до t</option>
                  </select>
                </label>
              </div>
            )}
            <IssueAt at={`search.passes.${i}`} id={`pass-${i}`} />
          </div>
        ))}
        <IssueAt at="search.passes" id="passes" />
        <div className={mod.links}>
          <button type="button" className={css.btn} disabled={passes.length >= PASSES_MAX} data-testid="est-pass-add" title={passes.length >= PASSES_MAX ? `Не больше ${PASSES_MAX} проходов` : "Добавить проход: копия последнего с радиусами ещё дальше"} onClick={addPass}>
            Добавить проход
          </button>
          <button type="button" className={css.btn} disabled={!typed} data-testid="est-passes-reset" title="Вернуть проходы по умолчанию" onClick={resetPasses}>
            Как в ядре
          </button>
        </div>
        <span className={css.note} data-testid="est-hy-order">
          Правило ураганных проб выключено, пока не задан порог t. Порядок отбора проб в проходе: {ORDER}. Поэтому «отбросить» может оставить проходу меньше проб, чем n_min, — блок уйдёт в следующий проход; число затронутых проб пишет поле NHY.
        </span>
      </fieldset>
    </>
  );
}

