/**
 * The window «Тренд» (ТЗ 7.35 Д-15; ADR 0022 §10, §15): the name, the angles ang1–ang3 (8.6.3), the ratios a2 / a1
 * and a3 / a1, the size shown, the centre (empty — the centre of the view), the colour; «Из модели вариограммы…» —
 * a copy of the axes of a structure (a new trend — into the form, saved as a copy of the model; a stored one —
 * «Обновить из модели» at once); «Эллипсоид в 3D» — the same ellipsoid with the handles as the models (`scene.ts`),
 * a drag fills the form. «Сохранить» — `trend.save` through the registry (`bind.saveWindow`); «Где используется»,
 * «Удалить…» — `trend.usage`, `trend.delete`. Floating, not modal; a window with changes asks before it closes.
 * `SaveAsTrendButton` — «Сохранить как тренд» of the window «Вариография» (its working model, `trendArgsOfWork`).
 * A structural trend (Д-15, Э19; `StructuralForm`): the kind is chosen when the trend is new; the source (measurements
 * or a wireframe), R_тр, the ratios, the size (the diameter of a disc), the colour; «Диски в 3D» — `trend.show`.
 */
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { useApp } from "../../shell/AppContext";
import { askConfirm } from "../../shell/dialogs";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import type { Store } from "../../shell/store";
import { closeWindow, setCloseGuard } from "../../shell/windows";
import { viewStore } from "../scene/bind";
import { loadModel, loadModels } from "../variography/api";
import { effectiveStructures, type Structure } from "../variography/model";
import { chosenStructure, setModels, variographyStore, workOf } from "../variography/state";
import { saveAsTrend, saveWindow } from "./bind";
import { StructuralForm } from "./StructuralForm";
import { checkSource } from "./structural";
import { checkValues, discInfoText, editOf, ELLIPSOIDS_KEY, findTrend, originText, setEdit, shownObjects, trendArgsOfWork, trendsStore, valuesOfStructure, type TrendParam, type TrendValues } from "./state";

/** A value of a store (with the server snapshot: the window renders on the server in the tests too). */
function useSel<T, R>(store: Store<T>, select: (s: T) => R): R {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}

const SHAPE_KEYS: readonly (keyof TrendValues)[] = ["ang1", "ang2", "ang3", "r2", "r3"];

interface FieldDef {
  key: keyof TrendValues;
  label: string;
  param: TrendParam;
  hint: string;
}

const ANGLES: FieldDef[] = [
  { key: "ang1", label: "Азимут ang1, °", param: "ang1", hint: "Азимут оси a1 от севера по часовой, 0–360°" },
  { key: "ang2", label: "ang2 = −погружение, °", param: "ang2", hint: "ang2 = −погружение оси a1, −90…90° (8.6.3)" },
  { key: "ang3", label: "Поворот ang3, °", param: "ang3", hint: "Поворот вокруг оси a1, −180…180°" },
];
const RATIOS: FieldDef[] = [
  { key: "r2", label: "a2 / a1", param: "r2", hint: "Отношение средней оси к большой, больше 0" },
  { key: "r3", label: "a3 / a1", param: "r3", hint: "Отношение малой оси к большой, больше 0" },
  { key: "size", label: "Размер показа a1, м", param: "size", hint: "Длина большой оси эллипсоида в 3D, м (на матрицу A не влияет)" },
];
/** The shape of a structural trend: no angles; the size is the diameter of a disc in 3D. */
const RATIOS_STRUCTURAL: FieldDef[] = [RATIOS[0], RATIOS[1], { key: "size", label: "Диаметр диска в 3D, м", param: "size", hint: "Диаметр диска замера в 3D, м (на оси в точках не влияет)" }];
const CENTRE: FieldDef[] = [
  { key: "cx", label: "X, м", param: "centre", hint: "Центр эллипсоида в 3D; пусто — центр вида" },
  { key: "cy", label: "Y, м", param: "centre", hint: "Центр эллипсоида в 3D; пусто — центр вида" },
  { key: "cz", label: "Z, м", param: "centre", hint: "Центр эллипсоида в 3D; пусто — центр вида" },
];

function Inputs({ defs, values, bad, onChange, winId }: { defs: FieldDef[]; values: TrendValues; bad: TrendParam | null; onChange: (k: keyof TrendValues, v: string) => void; winId: string }) {
  return (
    <div className={css.row}>
      {defs.map((d) => (
        <label key={d.key} className={css.field} title={d.hint}>
          <span>{d.label}</span>
          <input className={css.input} inputMode="decimal" value={values[d.key]} aria-invalid={bad === d.param} data-testid={`trend-${d.key}-${winId}`} onChange={(e) => onChange(d.key, e.target.value)} />
        </label>
      ))}
    </div>
  );
}

/** «Из модели вариограммы…»: the model and its structure (by default — the largest a1). */
function FromModel({ winId, stored, busy, run }: { winId: string; stored: boolean; busy: boolean; run: (model: number, structure: number, list: Structure[]) => void }) {
  const models = useSel(variographyStore, (s) => s.models);
  const loaded = useSel(variographyStore, (s) => s.modelsLoaded);
  const origin = useSel(trendsStore, (s) => s.edits[winId]?.origin);
  const [model, setModel] = useState<number | null>(typeof origin?.model_id === "number" ? origin.model_id : null);
  const [list, setList] = useState<Structure[] | null>(null);
  const [structure, setStructure] = useState(1);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!loaded) void loadModels().then(setModels, () => undefined);
  }, [loaded]);
  useEffect(() => {
    setList(null);
    setError(null);
    if (model === null) return;
    let live = true;
    loadModel(model).then(
      (m) => {
        if (!live) return;
        const s = effectiveStructures(m.model);
        setList(s);
        const keep = origin?.model_id === model && origin.structure ? origin.structure : chosenStructure({ model: m.model }) + 1;
        setStructure(Math.min(Math.max(keep, 1), Math.max(s.length, 1)));
      },
      (e: unknown) => live && setError(errorText(e)),
    );
    return () => {
      live = false;
    };
  }, [model, origin?.model_id, origin?.structure]);
  return (
    <fieldset className={css.group}>
      <legend>Из модели вариограммы</legend>
      <div className={css.row}>
        <label className={css.field}>
          <span>Модель</span>
          <select className={css.input} value={model ?? ""} data-testid={`trend-model-${winId}`} onChange={(e) => setModel(e.target.value ? Number(e.target.value) : null)}>
            <option value="">{models.length ? "— выберите —" : loaded ? "нет сохранённых моделей" : "…"}</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
                {m.stale ? " (устарела)" : ""}
              </option>
            ))}
          </select>
        </label>
        <label className={css.field}>
          <span>Структура</span>
          <select className={css.input} value={structure} disabled={!list?.length} data-testid={`trend-structure-${winId}`} onChange={(e) => setStructure(Number(e.target.value))}>
            {(list ?? []).map((s, i) => (
              <option key={i} value={i + 1}>
                {i + 1}: a1 {s.a1.toLocaleString("ru-RU")} м, ang1 {s.ang1.toLocaleString("ru-RU")}°
              </option>
            ))}
          </select>
        </label>
      </div>
      {error && <p className={css.error}>{error}</p>}
      <div className={css.row}>
        <button type="button" className={css.btn} disabled={busy || model === null || !list?.length} data-testid={`trend-from-model-${winId}`} onClick={() => model !== null && list && run(model, structure, list)}>
          {stored ? "Обновить из модели" : "Взять оси модели"}
        </button>
        <span className={css.note}>Копия углов и отношений; правка модели тренд не меняет.</span>
      </div>
    </fieldset>
  );
}

export function TrendWindow({ winId }: { winId: string }) {
  const app = useApp();
  const getEdit = () => trendsStore.get().edits[winId] ?? null;
  const edit = useSyncExternalStore(trendsStore.subscribe, getEdit, getEdit);
  const models = useSel(variographyStore, (s) => s.models);
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const structural = edit?.kind === "structural";
  const checked = useMemo(() => (edit ? checkValues(structural ? { ...edit.values, ang1: "0", ang2: "0", ang3: "0", cx: "", cy: "", cz: "" } : edit.values) : null), [edit, structural]);
  const source = useMemo(() => (edit && structural ? checkSource(edit.structural) : null), [edit, structural]);
  const discInfo = useSel(trendsStore, (s) => (edit?.trendId != null ? s.discs?.[edit.trendId] : undefined));
  const viewKey = useSyncExternalStore(viewStore.subscribe, () => viewStore.get()[ELLIPSOIDS_KEY], () => viewStore.get()[ELLIPSOIDS_KEY]);
  const reason = app.registry.reason("trend.save");

  useEffect(() => {
    setCloseGuard(winId, async () => !editOf(winId)?.dirty || (await askConfirm({ title: "Тренд", message: "Закрыть окно без сохранения изменений тренда?", okLabel: "Закрыть", danger: true })));
    return () => setCloseGuard(winId, null);
  }, [winId]);

  if (!edit) return <div className={css.panel}>Окно тренда не готово.</div>;
  const stored = edit.trendId !== null ? findTrend(edit.trendId) : undefined;
  const bad = checked && !checked.ok ? checked.param : null;
  const problem = checked && !checked.ok ? checked.error : source && !source.ok ? source.error : null;
  const change = (k: keyof TrendValues, v: string) => setEdit(winId, (e) => ({ values: { ...e.values, [k]: v }, dirty: true, fromModel: SHAPE_KEYS.includes(k) ? null : e.fromModel }));
  const act = async (fn: () => Promise<string | null>) => {
    setBusy(true);
    setNote(null);
    try {
      const out = await fn();
      if (out) setNote({ text: out, error: true });
    } catch (e) {
      setNote({ text: errorText(e), error: true });
    } finally {
      setBusy(false);
    }
  };
  const save = () =>
    act(async () => {
      const out = await saveWindow(app, winId);
      if (!out) setNote({ text: "Тренд сохранён", error: false });
      return out;
    });
  const fromModel = (model: number, structure: number, list: Structure[]) =>
    act(async () => {
      if (edit.trendId !== null) {
        const r = await app.registry.execute("trend.save", { trend_id: edit.trendId, model_id: model, structure }, "user");
        return r.ok ? (setNote({ text: r.result, error: false }), null) : r.result;
      }
      const s = list[structure - 1];
      if (!s) return "Нет такой структуры";
      setEdit(winId, (e) => ({ values: valuesOfStructure(e.values, s), origin: { type: "model", model_id: model, structure }, fromModel: { model_id: model, structure }, dirty: true }));
      return null;
    });
  const run = (id: string, extra: Record<string, unknown> = {}) => act(async () => {
    if (edit.trendId === null) return null;
    const r = await app.registry.execute(id, { trend_id: edit.trendId, ...extra }, "user");
    return r.ok ? null : r.result;
  });
  const modelName = (id: number) => models.find((m) => m.id === id)?.name ?? null;

  return (
    <div className={css.panel} style={{ padding: 10 }} data-testid={`trend-window-${winId}`}>
      <label className={css.field}>
        <span>Имя</span>
        <input className={css.input} value={edit.values.name} placeholder={structural ? "Структурный тренд" : edit.fromModel ? "Тренд · ‹модель›" : "Тренд"} data-testid={`trend-name-${winId}`} onChange={(e) => change("name", e.target.value)} />
      </label>
      <div className={css.row} role="radiogroup" aria-label="Вид тренда" data-testid={`trend-kind-${winId}`}>
        {(["global", "structural"] as const).map((k) => (
          <label key={k} className={css.check} title={k === "global" ? "Одни оси на всё месторождение: углы и отношения" : "Оси меняются от точки к точке по замерам α, Aп или опорному каркасу (8.9.2)"}>
            <input type="radio" name={`trend-kind-${winId}`} checked={edit.kind === k} disabled={edit.trendId !== null} data-testid={`trend-kind-${k}-${winId}`} onChange={() => setEdit(winId, { kind: k, dirty: true })} />
            {k === "global" ? "Глобальный" : "Структурный"}
          </label>
        ))}
      </div>
      {!structural && (
        <p className={css.note} data-testid={`trend-origin-${winId}`}>
          Происхождение: {originText(edit.origin, modelName)}
          {stored?.used ? " · используется" : ""}
        </p>
      )}
      {structural && stored?.used && <p className={css.note}>Используется: изменение источника пересчитает оси и в зависимых объектах.</p>}
      {!structural && (
      <fieldset className={css.group}>
        <legend>Углы осей (8.6.3)</legend>
        <Inputs defs={ANGLES} values={edit.values} bad={bad} onChange={change} winId={winId} />
      </fieldset>
      )}
      {structural && source && (
        <StructuralForm
          winId={winId}
          value={edit.structural}
          checked={source}
          stored={stored?.kind === "structural" ? stored : null}
          onChange={(patch) => setEdit(winId, (e) => ({ structural: { ...e.structural, ...patch }, dirty: true }))}
          discs={{
            shown: edit.trendId !== null && shownObjects(viewKey).includes(edit.trendId),
            reason: edit.trendId === null ? "Сначала сохраните тренд" : app.registry.reason("trend.show") ?? (stored?.sourceLost ? "Источник тренда удалён" : null),
            info: discInfo ? discInfoText(discInfo) : null,
            onToggle: (on) => void run("trend.show", { visible: on }),
          }}
        />
      )}
      <fieldset className={css.group}>
        <legend>Отношения a1 : a2 : a3 = 1 : a2/a1 : a3/a1</legend>
        <Inputs defs={structural ? RATIOS_STRUCTURAL : RATIOS} values={edit.values} bad={bad} onChange={change} winId={winId} />
      </fieldset>
      {!structural && (
      <fieldset className={css.group}>
        <legend>Показ в 3D</legend>
        <Inputs defs={CENTRE} values={edit.values} bad={bad} onChange={change} winId={winId} />
        <div className={css.row}>
          <label className={css.check} title="Эллипсоид тренда в 3D с ручками: кольцо азимута, дуги погружения и поворота, концы осей">
            <input type="checkbox" checked={edit.show} data-testid={`trend-show-${winId}`} onChange={(e) => setEdit(winId, { show: e.target.checked })} />
            Эллипсоид в 3D
          </label>
          <label className={css.check}>
            Цвет
            <input type="color" className={css.color} value={/^#[0-9a-f]{6}$/i.test(edit.values.color) ? edit.values.color : "#f46a1a"} data-testid={`trend-color-${winId}`} onChange={(e) => change("color", e.target.value)} />
          </label>
        </div>
      </fieldset>
      )}
      {structural && (
        <div className={css.row}>
          <label className={css.check}>
            Цвет дисков
            <input type="color" className={css.color} value={/^#[0-9a-f]{6}$/i.test(edit.values.color) ? edit.values.color : "#f46a1a"} data-testid={`trend-color-${winId}`} onChange={(e) => change("color", e.target.value)} />
          </label>
        </div>
      )}
      {!structural && <FromModel winId={winId} stored={edit.trendId !== null} busy={busy || !!reason} run={fromModel} />}
      {problem && <p className={css.error} data-testid={`trend-error-${winId}`}>{problem}</p>}
      {note && <p className={note.error ? css.error : css.note} data-testid={`trend-note-${winId}`}>{note.text}</p>}
      <div className={css.row}>
        <button type="button" className={css.primary} disabled={busy || !!problem || !!reason} title={reason ?? "Сохранить тренд в разделе «Тренды»"} data-testid={`trend-save-${winId}`} onClick={() => void save()}>
          Сохранить
        </button>
        {edit.trendId !== null && (
          <>
            <button type="button" className={css.btn} disabled={busy} onClick={() => void run("trend.usage")}>
              Где используется
            </button>
            <button type="button" className={css.btn} disabled={busy || !!app.registry.reason("trend.delete")} onClick={() => void run("trend.delete")}>
              Удалить…
            </button>
          </>
        )}
        <button type="button" className={css.btn} onClick={() => void (async () => (!edit.dirty || (await askConfirm({ title: "Тренд", message: "Закрыть окно без сохранения изменений тренда?", okLabel: "Закрыть", danger: true }))) && closeWindow(winId))()}>
          Закрыть
        </button>
      </div>
    </div>
  );
}

/** «Сохранить как тренд» of the window «Вариография» `winId`: the axes of the structure chosen of its working model. */
export function SaveAsTrendButton({ winId, className }: { winId: string; className?: string }) {
  const app = useApp();
  const work = useSel(variographyStore, (s) => s.works[winId] ?? null);
  const args = work ? trendArgsOfWork(work) : null;
  const reason = app.registry.reason("trend.save") ?? (args ? null : "В модели нет структуры с положительными дальностями");
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className={className ?? css.btn}
      disabled={busy || !!reason}
      title={reason ?? (work?.modelId !== null && !work?.dirty ? "Копия осей выбранной структуры сохранённой модели — в раздел «Тренды»" : "Оси выбранной структуры рабочей модели — в раздел «Тренды»")}
      data-testid={`vario-save-trend-${winId}`}
      onClick={() => {
        const w = workOf(winId);
        const a = w ? trendArgsOfWork(w) : null;
        if (!a) return;
        setBusy(true);
        void saveAsTrend(app, a).finally(() => setBusy(false));
      }}
    >
      Сохранить как тренд
    </button>
  );
}
