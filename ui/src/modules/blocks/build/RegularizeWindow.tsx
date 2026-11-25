/**
 * The window «Регуляризация» (ТЗ 8.7.5; ADR 0023 §9, decision 14, № 72): a new regular model in the same axes with a
 * different size of the block — the volume, the tonnes and the metal are kept (the overlaps are exact). On the left the
 * request — the model, the size of the new block, the fields to carry (by default every number field by the volume; a
 * grade may be weighed by the tonnes — that needs a density), the density (a field or a number, t/m³), the categories
 * (the mode by volume and the shares `P_<КОД>`) and the name; on the right the course «Чтение / Перекрытия / Запись» with
 * «Отменить» and the totals before and after (a gap in the data is not a zero: the totals may differ, and the window
 * says so). «Регуляризовать» runs the command `blocks.regularize` through the registry; a wrong form is told under its
 * field in Russian before anything is sent. Floating, not modal.
 */
import { useMemo, useState } from "react";
import { useApp } from "../../../shell/AppContext";
import css from "../../../shell/PropsParts.module.css";
import { errorText } from "../../../shell/props";
import { closeWindow } from "../../../shell/windows";
import { blocksStore } from "../state";
import { fieldsOf } from "../report/state";
import { STAGES, type RegularizeResult, type Weight } from "./api";
import mod from "./Build.module.css";
import { dec } from "./format";
import { Issue, RunPanel, Triple, useSel } from "./Parts";
import { chooseRegModel, modelFieldsOf } from "./bind";
import { argsOfRegularize, bodyOfRegularize } from "./regForm";
import { RegularizeResultView } from "./Results";
import { buildStore, setForm, setIssue, withBlocks } from "./state";

const WEIGHT_TITLES: Record<Weight, string> = { volume: "по объёму", tonnes: "по тоннажу" };

export function RegularizeWindow({ winId }: { winId: string }) {
  const app = useApp();
  const f = useSel(buildStore, (s) => s.forms.regularize);
  const running = useSel(buildStore, (s) => s.runs.regularize.phase === "running");
  const models = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const [note, setNote] = useState<string | null>(null);
  const reason = app.registry.reason("blocks.regularize");
  const list = useMemo(() => withBlocks(), [models]);
  const chosen = list.find((m) => m.id === f.modelId);
  const fields = useMemo(() => fieldsOf(modelFieldsOf(f.modelId)), [f.modelId, models, details]);
  const numbers = fields.filter((x) => x.kind === "number" && x.role !== "frac");
  const cats = fields.filter((x) => x.kind === "category");
  const set = (patch: Parameters<typeof setForm<"regularize">>[1]) => setForm("regularize", patch);

  const weightOf = (name: string): Weight | null => f.grades.find((g) => g.field === name)?.weight ?? null;
  const toggleGrade = (name: string, on: boolean) => set({ grades: on ? [...f.grades, { field: name, weight: "volume" }] : f.grades.filter((g) => g.field !== name) });
  const setWeight = (name: string, weight: Weight) => set({ grades: f.grades.map((g) => (g.field === name ? { ...g, weight } : g)) });
  const toggleCat = (name: string, on: boolean) => set({ categories: on ? [...f.categories, name] : f.categories.filter((c) => c !== name) });

  const build = async () => {
    setNote(null);
    const args = argsOfRegularize(f);
    const got = bodyOfRegularize(args);
    if (!got.ok) {
      setIssue("regularize", got.issue);
      return;
    }
    const r = await app.registry.execute("blocks.regularize", args, "user");
    if (!r.ok && !buildStore.get().issues.regularize) setNote(r.result);
  };

  return (
    <div className={mod.window} data-testid="regularize-window">
      <div className={mod.layout}>
        <div className={mod.form}>
          <fieldset className={css.group} disabled={running}>
            <legend>Модель</legend>
            <select className={css.input} value={f.modelId ?? ""} aria-label="Блочная модель" data-testid="regularize-model" onChange={(e) => chooseRegModel(e.target.value ? Number(e.target.value) : null)}>
              <option value="">{list.length ? "— выберите модель —" : "нет моделей с блоками"}</option>
              {list.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
            {chosen && !chosen.grid.variable && chosen.grid.size && (
              <span className={css.note} data-testid="regularize-model-info">
                Сейчас блок {chosen.grid.size.map((v) => dec(v, 3)).join(" × ")} м.
              </span>
            )}
            <Issue kind="regularize" field="model" />
          </fieldset>

          <fieldset className={css.group} disabled={running || f.modelId === null}>
            <legend>Новый блок</legend>
            <Triple labels={["Размер X, м", "Размер Y, м", "Размер Z, м"]} values={f.size} onChange={(size) => set({ size })} testId="regularize-size" />
            <span className={css.note}>Начало новой сетки кратно новому размеру в тех же осях; поворот прежний.</span>
            <Issue kind="regularize" field="size" />
          </fieldset>

          <fieldset className={css.group} disabled={running || f.modelId === null}>
            <legend>Поля и содержания</legend>
            <label className={css.check}>
              <input type="checkbox" checked={f.gradesAll} data-testid="regularize-grades-all" onChange={(e) => set({ gradesAll: e.target.checked })} />
              Все числовые поля, по объёму
            </label>
            {!f.gradesAll && (
              <div className={mod.list} role="group" aria-label="Поля" data-testid="regularize-grades">
                {numbers.map((x) => {
                  const w = weightOf(x.name);
                  return (
                    <label key={x.name}>
                      <input type="checkbox" checked={w !== null} data-testid={`regularize-grade-${x.name}`} onChange={(e) => toggleGrade(x.name, e.target.checked)} />
                      <span>
                        {x.name}
                        {x.unit && <span className={mod.unit}>, {x.unit}</span>}
                      </span>
                      {w !== null && (
                        <select className={css.input} style={{ width: "auto" }} value={w} aria-label={`Вес поля ${x.name}`} data-testid={`regularize-weight-${x.name}`} onChange={(e) => setWeight(x.name, e.target.value as Weight)}>
                          {(Object.keys(WEIGHT_TITLES) as Weight[]).map((k) => (
                            <option key={k} value={k}>
                              {WEIGHT_TITLES[k]}
                            </option>
                          ))}
                        </select>
                      )}
                    </label>
                  );
                })}
                {!numbers.length && <span className={css.note}>У модели нет числовых полей</span>}
              </div>
            )}
            <Issue kind="regularize" field="grades" />
          </fieldset>

          <fieldset className={css.group} disabled={running || f.modelId === null}>
            <legend>Плотность</legend>
            <div className={mod.radios} role="radiogroup" aria-label="Плотность">
              <label>
                <input type="radio" name="reg-density" checked={f.densityMode === "none"} data-testid="regularize-density-none" onChange={() => set({ densityMode: "none" })} />
                Не задана (без тоннажа)
              </label>
              <label>
                <input type="radio" name="reg-density" checked={f.densityMode === "field"} data-testid="regularize-density-field-on" onChange={() => set({ densityMode: "field" })} />
                Поле модели
              </label>
              {f.densityMode === "field" && (
                <select className={css.input} value={f.densityField} aria-label="Поле плотности" data-testid="regularize-density-field" onChange={(e) => set({ densityField: e.target.value })}>
                  <option value="">— выберите поле —</option>
                  {numbers.map((x) => (
                    <option key={x.name} value={x.name}>
                      {x.name}
                    </option>
                  ))}
                </select>
              )}
              <label>
                <input type="radio" name="reg-density" checked={f.densityMode === "value"} data-testid="regularize-density-value-on" onChange={() => set({ densityMode: "value" })} />
                Число, т/м³
              </label>
              {f.densityMode === "value" && <input className={css.input} value={f.densityValue} inputMode="decimal" aria-label="Плотность, т/м³" data-testid="regularize-density-value" onChange={(e) => set({ densityValue: e.target.value })} />}
            </div>
            <Issue kind="regularize" field="density" />
          </fieldset>

          <fieldset className={css.group} disabled={running || f.modelId === null}>
            <legend>Категории</legend>
            <div className={mod.radios} role="radiogroup" aria-label="Категории">
              {(["all", "some", "none"] as const).map((m) => (
                <label key={m}>
                  <input type="radio" name="reg-cats" checked={f.catMode === m} data-testid={`regularize-cats-${m}`} onChange={() => set({ catMode: m })} />
                  {m === "all" ? "Все категорийные поля" : m === "some" ? "Выбранные" : "Не переносить"}
                </label>
              ))}
            </div>
            {f.catMode === "some" && (
              <div className={mod.list} role="group" aria-label="Категорийные поля" data-testid="regularize-cats">
                {cats.map((x) => (
                  <label key={x.name}>
                    <input type="checkbox" checked={f.categories.includes(x.name)} data-testid={`regularize-cat-${x.name}`} onChange={(e) => toggleCat(x.name, e.target.checked)} />
                    <span>{x.name}</span>
                  </label>
                ))}
                {!cats.length && <span className={css.note}>У модели нет категорийных полей</span>}
              </div>
            )}
            <span className={css.note}>Категория блока — мода по объёму; доли каждого кода — поля P_&lt;КОД&gt;.</span>
            <Issue kind="regularize" field="categories" />
          </fieldset>

          <fieldset className={css.group} disabled={running || f.modelId === null}>
            <legend>Имя</legend>
            <input className={css.input} value={f.name} placeholder="‹модель› (регуляризация)" aria-label="Имя новой модели" data-testid="regularize-name" onChange={(e) => set({ name: e.target.value })} />
            <Issue kind="regularize" field="name" />
            <Issue kind="regularize" field="general" />
          </fieldset>
        </div>

        <div className={mod.result}>
          <RunPanel<RegularizeResult> kind="regularize" stages={STAGES.regularize} idle="Выберите модель и размер блока, нажмите «Регуляризовать»: ход и итоги до и после появятся здесь.">
            {(r) => <RegularizeResultView r={r} />}
          </RunPanel>
        </div>
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="regularize-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={running || !!reason || f.modelId === null} title={reason ?? "Построить новую модель с другим размером блока"} data-testid="regularize-run" onClick={() => void build().catch((e: unknown) => setNote(errorText(e)))}>
          Регуляризовать
        </button>
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="regularize-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
