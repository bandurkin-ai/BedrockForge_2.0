/**
 * The window «Сценарий карьера» (ТЗ 8.11.5, вкладка «Карьер» → «Сценарий»; ADR 0028 решения 5, 29): a new scenario — the name, the block model, the solver —
 * and the relief (a DTM of the project) with the restrictor (a filter of the library and the tree of rules — the constructor of the filters); for an
 * existing scenario (`pit.relief`) only the relief and the restrictor. «Создать» / «Применить» run the command `pit.new` / `pit.relief` through the registry
 * (the log, the notice, the question of the assistant). A wrong form is told under its field in Russian before anything is sent. Floating, not modal.
 */
import { useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { closeWindow } from "../../shell/windows";
import type { ObjectOption } from "../blocks/filters/RuleEditor";
import { RuleEditor } from "../blocks/filters/RuleEditor";
import { filtersByName, filtersStore } from "../blocks/filters/state";
import { blocksStore } from "../blocks/state";
import { sceneObjects } from "../scene/objects";
import { scenarioArgs, nameIssue } from "./forms";
import mod from "./Pit.module.css";
import { Fieldset, Issue, useSel } from "./PitParts";
import { modelFields, pitStore, setScenarioForm, setScenarioIssue } from "./state";
import { chooseModel } from "./bind";
import { DEFAULT_LIMITS, type Solver } from "./answers";

const SOLVER_TITLES: Record<Solver, string> = { auto: "Авто (HPF)", hpf: "HPF", ortools: "OR-Tools" };

export function ScenarioWindow({ winId }: { winId: string }) {
  const app = useApp();
  const form = useSel(pitStore, (s) => s.scenarioForm);
  const defaults = useSel(pitStore, (s) => s.defaults);
  const modelDefaults = useSel(pitStore, (s) => (s.scenarioForm.modelId === null ? null : (s.models[s.scenarioForm.modelId] ?? null)));
  const models = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const library = useSel(filtersStore, (s) => s.rows);
  const wireframes = useSel(sceneObjects, (s) => s.wireframes);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isNew = form.editId === null;
  const command = isNew ? "pit.new" : "pit.relief";
  const reason = app.registry.reason(command);

  const fields = useMemo(() => modelFields(form.modelId).map((f) => f.name), [form.modelId, models, details]);
  const filters = useMemo(() => filtersByName(library), [library]);
  const closed = useMemo<ObjectOption[]>(() => wireframes.map((w) => ({ id: w.id, name: w.name, dtm: w.dtm !== null })), [wireframes]);
  // the DTMs the core offers for the model and the DTMs of the project (the list of the scene is live)
  const terrains = useMemo(() => {
    const byId = new Map<number, string>();
    for (const t of modelDefaults?.terrains ?? []) byId.set(t.id, t.name);
    for (const w of closed) if (w.dtm) byId.set(w.id, w.name);
    return [...byId].map(([id, name]) => ({ id, name }));
  }, [modelDefaults, closed]);
  const modelName = models.find((m) => m.id === form.modelId)?.name ?? "";
  const limits = defaults?.limits ?? DEFAULT_LIMITS;

  const apply = async () => {
    setNote(null);
    const bad = isNew ? nameIssue(form.name, limits) : null;
    if (bad) return setScenarioIssue(bad);
    const got = scenarioArgs(form);
    if (!got.ok) return setScenarioIssue(got.issue);
    setBusy(true);
    try {
      const r = await app.registry.execute(command, got.args, "user");
      if (r.ok) {
        closeWindow(winId);
        if (isNew) void app.run("pit.economics", {}); // the next step of a new scenario: the prices
      } else if (!pitStore.get().scenarioIssue) setNote(r.result);
    } catch (e) {
      setNote(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={mod.window} data-testid="pit-scenario-window">
      <div className={mod.body}>
        {isNew ? (
          <Fieldset title="Сценарий" disabled={busy}>
            <label className={css.field}>
              <span>Имя</span>
              <input className={css.input} value={form.name} maxLength={limits.name} placeholder="Сценарий карьера" aria-label="Имя сценария" data-testid="pit-name" onChange={(e) => setScenarioForm({ name: e.target.value })} />
            </label>
            <Issue which="scenario" field="name" />
            <label className={css.field}>
              <span>Блочная модель</span>
              <select className={css.input} value={form.modelId ?? ""} aria-label="Блочная модель" data-testid="pit-model" onChange={(e) => chooseModel(e.target.value ? Number(e.target.value) : null)}>
                <option value="">{models.length ? "— выберите модель —" : "нет блочных моделей"}</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
            <span className={css.note}>Сетка модели должна быть с постоянным шагом: иначе сначала «Блочные модели → Регуляризация».</span>
            <Issue which="scenario" field="model" />
            <label className={css.field}>
              <span>Решатель</span>
              <select className={css.input} value={form.solver} aria-label="Решатель" data-testid="pit-solver" onChange={(e) => setScenarioForm({ solver: e.target.value as Solver })}>
                {(Object.keys(SOLVER_TITLES) as Solver[]).map((s) => (
                  <option key={s} value={s}>
                    {SOLVER_TITLES[s]}
                  </option>
                ))}
              </select>
            </label>
            <span className={css.note}>Результат не зависит от решателя; OR-Tools — запасной.</span>
          </Fieldset>
        ) : (
          <p className={mod.head} data-testid="pit-edit-head">
            Сценарий №{form.editId}
            {modelName ? ` · модель «${modelName}»` : ""}
          </p>
        )}

        <Fieldset title="Рельеф" disabled={busy || form.modelId === null}>
          <select className={css.input} value={form.terrainId ?? ""} aria-label="Рельеф" data-testid="pit-terrain" onChange={(e) => setScenarioForm({ terrainId: e.target.value ? Number(e.target.value) : null })}>
            <option value="">{terrains.length ? "без рельефа" : "нет ЦТМ в проекте — без рельефа"}</option>
            {form.terrainId !== null && !terrains.some((t) => t.id === form.terrainId) && <option value={form.terrainId}>№{form.terrainId} (нет в проекте)</option>}
            {terrains.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
          <span className={css.note}>Блоки выше поверхности — «воздух»: они вне карьера и не считаются пустой породой. Без рельефа блоки не обрезаются.</span>
          <Issue which="scenario" field="relief" />
        </Fieldset>

        <Fieldset title="Ограничитель" disabled={busy || form.modelId === null}>
          <label className={css.field}>
            <span>Фильтр библиотеки</span>
            <select className={css.input} value={form.filterId ?? ""} aria-label="Фильтр библиотеки" data-testid="pit-filter" onChange={(e) => setScenarioForm({ filterId: e.target.value ? Number(e.target.value) : null })}>
              <option value="">без фильтра</option>
              {form.filterId !== null && !filters.some((f) => f.id === form.filterId) && <option value={form.filterId}>№{form.filterId} (нет в проекте)</option>}
              {filters.map((f) => (
                <option key={f.id} value={f.id} title={f.title}>
                  {f.name}
                </option>
              ))}
            </select>
          </label>
          <Issue which="scenario" field="filter" />
          <label className={css.check}>
            <input type="checkbox" checked={form.restrictOn} data-testid="pit-restrict-on" onChange={(e) => setScenarioForm({ restrictOn: e.target.checked })} />
            Быстрый ограничитель (дерево правил)
          </label>
          {form.restrictOn && <RuleEditor draft={form.restrict} onChange={(restrict) => setScenarioForm({ restrict })} issue={null} fields={fields} wireframes={closed.filter((w) => !w.dtm)} testId="pit-restrict" />}
          <Issue which="scenario" field="restrict" />
          <span className={css.note}>Фильтр и правила работают вместе (И). Блок вне ограничителя в карьер не входит; доля блока внутри — часть его тоннажа.</span>
        </Fieldset>
        <Issue which="scenario" field="general" />
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="pit-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={busy || !!reason || (isNew && form.modelId === null)} title={reason ?? (isNew && form.modelId === null ? "Выберите блочную модель" : isNew ? "Создать сценарий; следом откроется «Экономика»" : "Сохранить рельеф и ограничитель")} data-testid="pit-apply" onClick={() => void apply()}>
          {isNew ? "Создать" : "Применить"}
        </button>
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="pit-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
