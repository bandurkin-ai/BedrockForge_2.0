/**
 * The parts of the form of the window «Отчёт по ресурсам» (ТЗ 8.10; ADR 0027 решения 3–11, 29): the elements and their units, the cut-off field
 * and the cut-offs (a list or a range), the balance, the density, the restrictor (the filter of the library, «Карьер», «Ниже рельефа», the tree
 * of rules), the keys of the group by dragging («Уступ» among them), the curve, the template and the name. Every part reads the form and
 * writes a patch; the words under a field are the first wrong thing of the form or the refusal of the core (`Issue`).
 */
import { useSyncExternalStore, type DragEvent, type ReactNode } from "react";
import css from "../../shell/PropsParts.module.css";
import type { Store } from "../../shell/store";
import { RuleEditor, type ObjectOption } from "../blocks/filters/RuleEditor";
import type { FilterRow } from "../blocks/filters/api";
import type { FieldInfo } from "../blocks/report/state";
import type { TemplateRow } from "./answers";
import type { Limits } from "./api";
import { keyLabel, MAX_UNIT, UNITS, type CutoffMode, type DensityMode, type FormField, type KeyChoice, type ResourceForm } from "./form";
import { addKey, availableKeys, decodeDrag, dropOnAvailable, dropOnChosen, encodeDrag, moveKey, removeKey } from "./groupOps";
import mod from "./Resources.module.css";
import { resourcesStore, setForm } from "./state";

export function useSel<T, R>(store: Store<T>, select: (s: T) => R): R {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}

/** What every part gets: the form, the fields of the model, whether the form is locked (a run is going), the limits of the core. */
export interface PartProps {
  form: ResourceForm;
  numbers: readonly FieldInfo[];
  cats: readonly FieldInfo[];
  disabled: boolean;
  limits: Limits;
}

const set = (patch: Partial<ResourceForm>) => setForm(patch);
const label = (name: string, unit: string | null) => (unit ? `${name}, ${unit}` : name);

/** The words under a field of the form (the first wrong thing of the form or the refusal of the core). */
export function Issue({ field }: { field: FormField }) {
  const issue = useSel(resourcesStore, (s) => s.issue);
  if (!issue || issue.field !== field) return null;
  return (
    <p className={css.error} role="alert" data-testid={`res-issue-${field}`}>
      {issue.message} {issue.hint}
    </p>
  );
}

function Fieldset({ title, disabled, children }: { title: string; disabled: boolean; children: ReactNode }) {
  return (
    <fieldset className={css.group} disabled={disabled}>
      <legend>{title}</legend>
      {children}
    </fieldset>
  );
}

// ---- elements ----

export function ElementsPart({ form, numbers, disabled, limits }: PartProps) {
  const unitOf = (field: string) => form.elements.find((e) => e.field === field)?.unit ?? "";
  const toggle = (field: string, on: boolean) => set({ elements: on ? [...form.elements, { field, unit: "" }] : form.elements.filter((e) => e.field !== field) });
  const setUnit = (field: string, unit: string) => set({ elements: form.elements.map((e) => (e.field === field ? { ...e, unit } : e)) });
  return (
    <Fieldset title="Элементы" disabled={disabled || form.modelId === null}>
      <div className={mod.list} role="group" aria-label="Элементы" data-testid="res-elements">
        {numbers.map((f) => {
          const on = form.elements.some((e) => e.field === f.name);
          return (
            <div key={f.name} className={mod.element}>
              <label title={f.role === "grade" ? "Поле с ролью «содержание»" : undefined}>
                <input type="checkbox" checked={on} data-testid={`res-element-${f.name}`} onChange={(e) => toggle(f.name, e.target.checked)} />
                <span>
                  {f.name}
                  {f.unit && <span className={mod.unit}>, {f.unit}</span>}
                </span>
              </label>
              {on && <input className={css.input} list="res-units" maxLength={MAX_UNIT} value={unitOf(f.name)} placeholder={f.unit ?? "единица"} aria-label={`Единица ${f.name}`} data-testid={`res-unit-${f.name}`} onChange={(e) => setUnit(f.name, e.target.value)} />}
            </div>
          );
        })}
        {!numbers.length && <span className={css.note}>У модели нет числовых полей</span>}
      </div>
      <datalist id="res-units">
        {UNITS.map((u) => (
          <option key={u} value={u} />
        ))}
      </datalist>
      <span className={form.elements.length > limits.elements ? css.error : css.note}>
        Выбрано {form.elements.length} из {limits.elements}
        {!form.elements.length && " — не выбрано: возьмутся поля *_CUT"}. Единица пустая — единица поля; г/т и ppm — металл в граммах, % — в тоннах, ppb — тысячные доли грамма.
      </span>
      <Issue field="elements" />
    </Fieldset>
  );
}

// ---- the cut-off field, the cut-offs, the balance ----

export function CutoffsPart({ form, numbers, disabled }: PartProps) {
  const mode = (m: CutoffMode) => set({ cutoffMode: m });
  return (
    <Fieldset title="Борта" disabled={disabled}>
      <label className={css.field}>
        <span>Поле борта</span>
        <select className={css.input} value={form.cutoffField} aria-label="Поле борта" data-testid="res-cutoff-field" onChange={(e) => set({ cutoffField: e.target.value })}>
          <option value="">первый из элементов</option>
          {numbers.map((f) => (
            <option key={f.name} value={f.name}>
              {label(f.name, f.unit)}
            </option>
          ))}
        </select>
      </label>
      <Issue field="cutoff_field" />
      <div className={mod.seg} role="group" aria-label="Задание бортов">
        <button type="button" aria-pressed={form.cutoffMode === "list"} data-testid="res-cutoff-list" onClick={() => mode("list")}>
          Список
        </button>
        <button type="button" aria-pressed={form.cutoffMode === "range"} data-testid="res-cutoff-range" onClick={() => mode("range")}>
          Диапазон
        </button>
      </div>
      {form.cutoffMode === "list" ? (
        <>
          <input className={css.input} value={form.cutoffs} spellCheck={false} aria-label="Борта через точку с запятой" data-testid="res-cutoffs" onChange={(e) => set({ cutoffs: e.target.value })} />
          <span className={css.note}>Через «;», например без борта; 0,5; 1. «Без борта» — строка по всем блокам группы, и без значения поля борта.</span>
        </>
      ) : (
        <div className={mod.triple}>
          {([["от", "rangeFrom", form.rangeFrom], ["до", "rangeTo", form.rangeTo], ["шаг", "rangeStep", form.rangeStep]] as const).map(([t, key, v]) => (
            <label key={key} className={css.field}>
              <span>{t}</span>
              <input className={css.input} value={v} inputMode="decimal" aria-label={`Борт: ${t}`} data-testid={`res-${key}`} onChange={(e) => set({ [key]: e.target.value })} />
            </label>
          ))}
        </div>
      )}
      <Issue field="cutoffs" />
    </Fieldset>
  );
}

export function BalancePart({ form, disabled }: PartProps) {
  return (
    <Fieldset title="Балансовые и забалансовые" disabled={disabled}>
      <label className={css.check}>
        <input type="checkbox" checked={form.balanceOn} data-testid="res-balance-on" onChange={(e) => set({ balanceOn: e.target.checked })} />
        Строки по балансовому борту
      </label>
      {form.balanceOn && (
        <div className={mod.triple} style={{ gridTemplateColumns: "1fr 1fr" }}>
          <label className={css.field}>
            <span>Балансовый борт</span>
            <input className={css.input} value={form.balanceCutoff} inputMode="decimal" aria-label="Балансовый борт" data-testid="res-balance-cutoff" onChange={(e) => set({ balanceCutoff: e.target.value })} />
          </label>
          <label className={css.field}>
            <span>Забалансовый, меньше</span>
            <input className={css.input} value={form.balanceOff} inputMode="decimal" placeholder="нет" aria-label="Забалансовый борт" data-testid="res-balance-off" onChange={(e) => set({ balanceOff: e.target.value })} />
          </label>
        </div>
      )}
      <Issue field="balance" />
    </Fieldset>
  );
}

// ---- the density ----

export function DensityPart({ form, numbers, disabled, auto }: PartProps & { auto: string | null }) {
  const pick = (m: DensityMode) => set({ densityMode: m });
  return (
    <Fieldset title="Плотность" disabled={disabled || form.modelId === null}>
      <div className={mod.radios} role="radiogroup" aria-label="Плотность">
        <label>
          <input type="radio" name="res-density" checked={form.densityMode === "auto"} data-testid="res-density-auto" onChange={() => pick("auto")} />
          Автоматически{auto ? `: поле ${auto}` : ": поле SG или поле с ролью «плотность» не найдено"}
        </label>
        <label>
          <input type="radio" name="res-density" checked={form.densityMode === "field"} data-testid="res-density-field-on" onChange={() => pick("field")} />
          Поле модели
        </label>
        {form.densityMode === "field" && (
          <select className={css.input} value={form.densityField} aria-label="Поле плотности" data-testid="res-density-field" onChange={(e) => set({ densityField: e.target.value })}>
            <option value="">— выберите поле —</option>
            {numbers.map((f) => (
              <option key={f.name} value={f.name}>
                {f.name}
                {f.constant ? " (постоянное)" : ""}
              </option>
            ))}
          </select>
        )}
        <label>
          <input type="radio" name="res-density" checked={form.densityMode === "value"} data-testid="res-density-value-on" onChange={() => pick("value")} />
          Число, т/м³
        </label>
        {form.densityMode === "value" && <input className={css.input} value={form.densityValue} inputMode="decimal" aria-label="Плотность, т/м³" data-testid="res-density-value" onChange={(e) => set({ densityValue: e.target.value })} />}
      </div>
      <span className={css.note}>Блок без плотности не считается нулём: он идёт в «Контроль» (тоннаж неизвестен).</span>
      <Issue field="density" />
    </Fieldset>
  );
}

// ---- the restrictor ----

export function RestrictPart({ form, numbers, cats, disabled, filters, wireframes, dtms }: PartProps & { filters: readonly FilterRow[]; wireframes: readonly ObjectOption[]; dtms: readonly ObjectOption[] }) {
  const pit = (p: Partial<ResourceForm["pit"]>) => set({ pit: { ...form.pit, ...p } });
  const below = (p: Partial<ResourceForm["below"]>) => set({ below: { ...form.below, ...p } });
  return (
    <Fieldset title="Ограничение" disabled={disabled || form.modelId === null}>
      <label className={css.field}>
        <span>Фильтр библиотеки</span>
        <select className={css.input} value={form.filterId ?? ""} aria-label="Фильтр библиотеки" data-testid="res-filter" onChange={(e) => set({ filterId: e.target.value ? Number(e.target.value) : null })}>
          <option value="">без фильтра</option>
          {form.filterId !== null && !filters.some((f) => f.id === form.filterId) && <option value={form.filterId}>№{form.filterId} (нет в проекте)</option>}
          {filters.map((f) => (
            <option key={f.id} value={f.id} title={f.title}>
              {f.name}
            </option>
          ))}
        </select>
      </label>
      <Issue field="filter" />

      <div className={mod.shortcut}>
        <label className={css.check}>
          <input type="checkbox" checked={form.pit.on} data-testid="res-pit-on" onChange={(e) => pit({ on: e.target.checked })} />
          Карьер
        </label>
        {form.pit.on && (
          <>
            <div className={mod.seg} role="group" aria-label="Карьер задан">
              <button type="button" aria-pressed={form.pit.mode === "field"} data-testid="res-pit-field" onClick={() => pit({ mode: "field" })}>
                Полем PIT
              </button>
              <button type="button" aria-pressed={form.pit.mode === "wireframe"} data-testid="res-pit-wireframe" onClick={() => pit({ mode: "wireframe" })}>
                Каркасом
              </button>
            </div>
            {form.pit.mode === "field" ? (
              <label className={css.field}>
                <span>Блоки PIT не больше номера</span>
                <input className={css.input} value={form.pit.n} inputMode="decimal" aria-label="Номер карьера" data-testid="res-pit-n" onChange={(e) => pit({ n: e.target.value })} />
              </label>
            ) : (
              <>
                <select className={css.input} value={form.pit.wireframeId ?? ""} aria-label="Каркас карьера" data-testid="res-pit-wire" onChange={(e) => pit({ wireframeId: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">— выберите каркас —</option>
                  {wireframes.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
                <label className={css.check}>
                  <input type="checkbox" checked={form.pit.by === "frac"} data-testid="res-pit-frac" onChange={(e) => pit({ by: e.target.checked ? "frac" : "centre" })} />
                  По доле блока внутри каркаса (иначе — по центру)
                </label>
              </>
            )}
          </>
        )}
        <Issue field="pit" />
      </div>

      <div className={mod.shortcut}>
        <label className={css.check}>
          <input type="checkbox" checked={form.below.on} data-testid="res-below-on" onChange={(e) => below({ on: e.target.checked })} />
          Ниже рельефа
        </label>
        {form.below.on && (
          <>
            <select className={css.input} value={form.below.surfaceId ?? ""} aria-label="Рельеф" data-testid="res-below-surface" onChange={(e) => below({ surfaceId: e.target.value ? Number(e.target.value) : null })}>
              <option value="">{dtms.length ? "— выберите ЦТМ —" : "нет ЦТМ в проекте"}</option>
              {dtms.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
            <label className={css.check}>
              <input type="checkbox" checked={form.below.by === "frac"} data-testid="res-below-frac" onChange={(e) => below({ by: e.target.checked ? "frac" : "centre" })} />
              По доле блока ниже поверхности (иначе — по центру)
            </label>
          </>
        )}
        <Issue field="below" />
      </div>

      <label className={css.check}>
        <input type="checkbox" checked={form.restrictOn} data-testid="res-restrict-on" onChange={(e) => set({ restrictOn: e.target.checked })} />
        Быстрый ограничитель (дерево правил)
      </label>
      {form.restrictOn && <RuleEditor draft={form.restrict} onChange={(restrict) => set({ restrict })} issue={null} fields={numbers.concat(cats).map((f) => f.name)} wireframes={wireframes} testId="res-restrict" />}
      <Issue field="restrict" />
      <span className={css.note}>Фильтр, «Карьер», «Ниже рельефа» и правила работают вместе (И): тоннаж блока — объём × доля каркаса × доля ограничителя × плотность.</span>
    </Fieldset>
  );
}

// ---- the keys of the group ----

function KeyItem({ k, index, from, count, onUp, onDown, onRemove, onAdd }: { k: KeyChoice; index: number; from: "available" | "chosen"; count: number; onUp?: () => void; onDown?: () => void; onRemove?: () => void; onAdd?: () => void }) {
  const drag = (e: DragEvent) => {
    e.dataTransfer.setData("text/plain", encodeDrag({ from, key: k, index }));
    e.dataTransfer.effectAllowed = "move";
  };
  const id = `res-key-${from}-${k.kind === "bench" ? "bench" : k.field}`;
  return (
    <div className={mod.key} draggable onDragStart={drag} data-testid={id}>
      <span title={k.kind === "bench" ? "Уступ: высота H, отметка Z₀" : undefined}>{keyLabel(k)}</span>
      {from === "chosen" ? (
        <>
          <button type="button" className={css.iconBtn} disabled={index === 0} aria-label={`Выше: ${keyLabel(k)}`} title="Выше (столбец левее)" data-testid={`${id}-up`} onClick={onUp}>
            ↑
          </button>
          <button type="button" className={css.iconBtn} disabled={index === count - 1} aria-label={`Ниже: ${keyLabel(k)}`} title="Ниже (столбец правее)" data-testid={`${id}-down`} onClick={onDown}>
            ↓
          </button>
          <button type="button" className={css.iconBtn} aria-label={`Убрать: ${keyLabel(k)}`} title="Убрать из групп" data-testid={`${id}-remove`} onClick={onRemove}>
            ✕
          </button>
        </>
      ) : (
        <button type="button" className={css.iconBtn} aria-label={`Добавить: ${keyLabel(k)}`} title="Добавить в группы" data-testid={`${id}-add`} onClick={onAdd}>
          +
        </button>
      )}
    </div>
  );
}

export function GroupsPart({ form, cats, disabled, limits }: PartProps) {
  const avail = availableKeys(cats.map((c) => c.name), form.groups);
  const full = form.groups.length >= limits.group_keys;
  const over = (e: DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  };
  const dropChosen = (at: number) => (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const d = decodeDrag(e.dataTransfer.getData("text/plain"));
    if (d) set({ groups: dropOnChosen(form.groups, d, at, limits.group_keys) });
  };
  const dropAvail = (e: DragEvent) => {
    e.preventDefault();
    const d = decodeDrag(e.dataTransfer.getData("text/plain"));
    if (d) set({ groups: dropOnAvailable(form.groups, d) });
  };
  const hasBench = form.groups.some((k) => k.kind === "bench");
  return (
    <Fieldset title="Группы" disabled={disabled || form.modelId === null}>
      <div className={mod.keys}>
        <div className={mod.keyBox} onDragOver={over} onDrop={dropAvail} data-testid="res-keys-available">
          <h5>Можно добавить</h5>
          {avail.map((k, i) => (
            <KeyItem key={keyLabel(k)} k={k} index={i} from="available" count={avail.length} onAdd={() => set({ groups: addKey(form.groups, k, limits.group_keys) })} />
          ))}
          {!avail.length && <span className={css.note}>Все ключи выбраны</span>}
        </div>
        <div className={mod.keyBox} onDragOver={over} onDrop={dropChosen(form.groups.length)} data-testid="res-keys-chosen">
          <h5>Группы по порядку (до {limits.group_keys})</h5>
          {form.groups.map((k, i) => (
            <div key={keyLabel(k)} onDragOver={over} onDrop={dropChosen(i)}>
              <KeyItem k={k} index={i} from="chosen" count={form.groups.length} onUp={() => set({ groups: moveKey(form.groups, i, i - 1) })} onDown={() => set({ groups: moveKey(form.groups, i, i + 1) })} onRemove={() => set({ groups: removeKey(form.groups, i) })} />
            </div>
          ))}
          {!form.groups.length && <span className={css.note}>Перетащите сюда ключи: без групп — только итог</span>}
        </div>
      </div>
      {full && <span className={css.note}>Ключей уже {limits.group_keys}: уберите один, чтобы добавить другой.</span>}
      <span className={css.note}>Группировать можно по категориям и по уступу; числовое поле переведите в категории калькулятором блочной модели.</span>
      <Issue field="groups" />
      {hasBench && (
        <div className={mod.shortcut}>
          <div className={mod.triple} style={{ gridTemplateColumns: "1fr 1fr" }}>
            <label className={css.field}>
              <span>Высота уступа H, м</span>
              <input className={css.input} value={form.benchH} inputMode="decimal" aria-label="Высота уступа" data-testid="res-bench-h" onChange={(e) => set({ benchH: e.target.value })} />
            </label>
            <label className={css.field}>
              <span>Отметка Z₀, м</span>
              <input className={css.input} value={form.benchZ0} inputMode="decimal" placeholder="авто" aria-label="Отметка Z0" data-testid="res-bench-z0" onChange={(e) => set({ benchZ0: e.target.value })} />
            </label>
            <label className={css.field}>
              <span>Уступы от отметки, м</span>
              <input className={css.input} value={form.benchZMin} inputMode="decimal" placeholder="охват модели" aria-label="Нижняя отметка" data-testid="res-bench-zmin" onChange={(e) => set({ benchZMin: e.target.value })} />
            </label>
            <label className={css.field}>
              <span>до отметки, м</span>
              <input className={css.input} value={form.benchZMax} inputMode="decimal" placeholder="охват модели" aria-label="Верхняя отметка" data-testid="res-bench-zmax" onChange={(e) => set({ benchZMax: e.target.value })} />
            </label>
          </div>
          <span className={css.note}>Блок делится между уступами пропорционально высоте; часть вне диапазона уступов идёт в «(пусто)».</span>
          <Issue field="bench" />
        </div>
      )}
    </Fieldset>
  );
}

// ---- the curve, the template, the name ----

export function CurvePart({ form, disabled }: PartProps) {
  return (
    <Fieldset title="Кривая «тоннаж — содержание»" disabled={disabled}>
      <label className={css.check}>
        <input type="checkbox" checked={form.curveOn} data-testid="res-curve-on" onChange={(e) => set({ curveOn: e.target.checked })} />
        Строить кривую
      </label>
      {form.curveOn && (
        <label className={css.field}>
          <span>Точек, от 2 до 500</span>
          <input className={css.input} value={form.curvePoints} inputMode="numeric" aria-label="Точек кривой" data-testid="res-curve-points" onChange={(e) => set({ curvePoints: e.target.value })} />
        </label>
      )}
      <Issue field="curve" />
    </Fieldset>
  );
}

export function TemplatePart({ form, disabled, templates }: PartProps & { templates: readonly TemplateRow[] }) {
  return (
    <Fieldset title="Форма отчёта и название" disabled={disabled}>
      <label className={css.field}>
        <span>Шаблон формы</span>
        <select className={css.input} value={form.templateId} aria-label="Шаблон формы" data-testid="res-template" onChange={(e) => set({ templateId: e.target.value })}>
          <option value="">без формы (только таблица)</option>
          {form.templateId && !templates.some((t) => t.id === form.templateId) && <option value={form.templateId}>{form.templateId}</option>}
          {templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      <span className={css.note}>Форма — только показ: смена шаблона не пересчитывает отчёт и не делает его устаревшим.</span>
      <Issue field="template" />
      <label className={css.field}>
        <span>Название сохранённого отчёта</span>
        <input className={css.input} value={form.name} maxLength={200} placeholder="Отчёт по ресурсам" aria-label="Название отчёта" data-testid="res-name" onChange={(e) => set({ name: e.target.value })} />
      </label>
      <Issue field="name" />
    </Fieldset>
  );
}
