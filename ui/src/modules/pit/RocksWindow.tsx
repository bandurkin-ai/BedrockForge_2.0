/**
 * The window «Породы карьера» (ТЗ 8.11.2, 8.11.5, вкладка «Карьер» → «Сценарий» → «Породы»; ADR 0028 решения 4, 6, 29): how the type of a block is found (a category field or
 * rules), the types met in the data (the labels of the field, the names of the rules, «Все породы», «(пусто)», «Прочие» — and what `validate` calls) with c_p, c_mр, the recovery
 * of every element of the scenario and the kinds «только пустая» and «воздух». «Применить» runs the command `pit.rocks` through the registry (the log, the notice, the question of
 * the assistant) and tells what the scenario still needs to run. A wrong form is told under its place in Russian before anything is sent. Floating, not modal.
 */
import { useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { closeWindow } from "../../shell/windows";
import type { ObjectOption } from "../blocks/filters/RuleEditor";
import { blocksStore } from "../blocks/state";
import { sceneObjects } from "../scene/objects";
import { DEFAULT_LIMITS } from "./answers";
import { byKey, byKeyOfParams, encounteredTypes, rocksArgs, typeNamesOfMissing } from "./formsRocks";
import mod from "./Pit.module.css";
import { FormNotice, Issue, useFormScenario, useSel, useStaleForm } from "./PitParts";
import { ByPart, TypesPart } from "./RocksParts";
import { modelFields, pitStore, setRocksIssue } from "./state";

export function RocksWindow({ winId }: { winId: string }) {
  const app = useApp();
  const form = useSel(pitStore, (s) => s.rocksForm);
  const defaults = useSel(pitStore, (s) => s.defaults);
  const scenario = useSel(pitStore, (s) => s.scenario);
  const validate = useSel(pitStore, (s) => s.validate);
  const models = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const wireframes = useSel(sceneObjects, (s) => s.wireframes);
  const modelId = scenario?.params.model_id ?? null;
  const modelDefaults = useSel(pitStore, (s) => (modelId === null ? null : (s.models[modelId] ?? null)));
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reason = app.registry.reason("pit.rocks");
  const target = useFormScenario("rocks");
  const stale = useStaleForm("rocks");

  const fields = useMemo(() => modelFields(modelId), [modelId, models, details]);
  const categories = useMemo(() => fields.filter((f) => f.kind === "category"), [fields]);
  const closed = useMemo<ObjectOption[]>(() => wireframes.map((w) => ({ id: w.id, name: w.name, dtm: w.dtm !== null })).filter((w) => !w.dtm), [wireframes]);
  const limits = defaults?.limits ?? DEFAULT_LIMITS;
  const elements = useMemo(() => (scenario?.params.elements ?? []).map((e) => e.field), [scenario]);

  // the labels of the field: the details of the model are fuller than the list the core proposes (they come first when read)
  const labelled = useMemo(() => {
    const byName = new Map<string, string[]>();
    for (const c of modelDefaults?.categories ?? []) byName.set(c.field, [...c.labels]);
    for (const c of categories) if (c.labels.length) byName.set(c.name, c.labels);
    return [...byName].map(([field, labels]) => ({ field, labels }));
  }, [modelDefaults, categories]);
  // `validate` is about the saved way of typing: its names count only while the form still has that way
  const sameWay = scenario !== null && byKey(form) === byKeyOfParams(scenario.params);
  const open = sameWay && validate ? typeNamesOfMissing(validate.missing) : [];
  const met = encounteredTypes(form, labelled, open);

  const apply = async () => {
    setNote(null);
    const got = rocksArgs(form, target, elements, limits);
    if (!got.ok) return setRocksIssue(got.issue);
    setBusy(true);
    try {
      const r = await app.registry.execute("pit.rocks", got.args, "user");
      if (!r.ok && !pitStore.get().rocksIssue) setNote(r.result);
    } catch (e) {
      setNote(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const model = models.find((m) => m.id === modelId)?.name;
  return (
    <div className={mod.window} data-testid="pit-rocks-window">
      <div className={mod.body}>
        <p className={mod.head} data-testid="pit-rocks-head">
          {scenario ? `Сценарий «${scenario.name}»` : "Сценарий не выбран"}
          {model ? ` · модель «${model}»` : modelId === null && scenario ? " · модель не выбрана" : ""}
        </p>
        <FormNotice kind="rocks" />
        <ByPart form={form} disabled={busy} categories={categories} fields={fields.map((f) => f.name)} wireframes={closed} max={limits.rock_rules} />
        <TypesPart form={form} disabled={busy} elements={elements} met={met} open={open} limit={limits.rock_types} />
        <Issue which="rocks" field="general" />
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="pit-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={busy || !!reason || !scenario || !!stale} title={reason ?? stale ?? (scenario ? "Сохранить породы в сценарии" : "Выберите или создайте сценарий")} data-testid="pit-rocks-apply" onClick={() => void apply()}>
          Применить
        </button>
        {validate && (
          <span className={mod.ready} data-ready={validate.ready} data-testid="pit-ready">
            {validate.ready ? "Сценарий готов к запуску." : `Для запуска не хватает: ${validate.missing[0]?.message ?? "параметров"}`}
          </span>
        )}
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="pit-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
