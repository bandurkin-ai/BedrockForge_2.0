/**
 * The parts of the form of the window «Экономика карьера» (ТЗ 8.11.2, 8.11.5; ADR 0028 решения 3, 6): the elements (the grade field, the unit, the price P,
 * the sales S, the royalty), the density (a field or a number, and the one for the blocks that have none), the mining costs (c_m0, Z_ref, H_гл, c_гл),
 * the RF (a list or a range), the prefix of the fields. Every part reads the form and writes a patch; the words under a field are the first wrong thing
 * of the form or the refusal of the core (`Issue`).
 */
import css from "../../shell/PropsParts.module.css";
import type { FieldInfo } from "../blocks/report/state";
import { DEFAULT_GRADE_LABELS, DEFAULT_LIMITS, DEFAULT_PRICE_LABELS, type Limits } from "./answers";
import { newElement, shellCount, UNIT_CODES, type EconomicsForm, type ElementRow } from "./forms";
import { fmt, priceLabel } from "./format";
import mod from "./Pit.module.css";
import { Fieldset, Issue, issueAtRow, NumberField, useSel } from "./PitParts";
import { pitStore, setEconForm } from "./state";

export interface EconProps {
  form: EconomicsForm;
  /** the numeric fields of the model */
  numbers: readonly FieldInfo[];
  disabled: boolean;
  limits: Limits;
  gradeLabels?: Record<string, string>;
  priceLabels?: Record<string, string>;
}

// ---- the elements ----

export function ElementsPart({ form, numbers, disabled, limits, gradeLabels = DEFAULT_GRADE_LABELS, priceLabels = DEFAULT_PRICE_LABELS, proposed }: EconProps & { proposed: readonly ElementRow[] }) {
  const issue = useSel(pitStore, (s) => s.econIssue);
  // the core finds a field by its name without the case («au» is AU): the rows are matched the same way
  const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();
  const rowOf = (field: string) => form.elements.findIndex((e) => same(e.field, field));
  const change = (field: string, patch: Partial<ElementRow>) => setEconForm({ elements: form.elements.map((e) => (same(e.field, field) ? { ...e, ...patch } : e)) });
  const toggle = (field: string, on: boolean) => setEconForm({ elements: on ? [...form.elements, newElement(field)] : form.elements.filter((e) => !same(e.field, field)) });
  const extra = form.elements.filter((e) => !numbers.some((f) => same(f.name, e.field))); // a row of a field the model does not have is shown, to be taken away
  const list: { name: string; unit: string | null; role: string | null; missing: boolean }[] = [
    ...numbers.map((f) => ({ name: f.name, unit: f.unit, role: f.role, missing: false })),
    ...extra.map((e) => ({ name: e.field, unit: null, role: null, missing: true })),
  ];
  return (
    <Fieldset title="Элементы" disabled={disabled}>
      <div className={mod.elements} role="group" aria-label="Элементы" data-testid="pit-elements">
        <div className={`${mod.element} ${mod.elementHead}`}>
          <span>Поле</span>
          <span>Единица</span>
          <span>Цена P</span>
          <span>Сбыт S</span>
          <span>Роялти, доля</span>
        </div>
        {list.map((f) => {
          const i = rowOf(f.name);
          const row = i >= 0 ? form.elements[i] : null;
          const marked = row !== null && issueAtRow(issue, i);
          return (
            <div key={f.name} className={`${mod.element} ${marked ? mod.marked : ""}`} data-testid={`pit-row-${f.name}`}>
              <label title={f.role === "grade" ? "Поле с ролью «содержание»" : f.missing ? "Поля нет в модели: уберите элемент" : undefined}>
                <input type="checkbox" checked={row !== null} data-testid={`pit-el-${f.name}`} onChange={(e) => toggle(f.name, e.target.checked)} />
                <span>
                  {f.name}
                  {f.unit && <span className={mod.unit}>, {f.unit}</span>}
                  {f.missing && <span className={mod.unit}> (нет в модели)</span>}
                </span>
              </label>
              {row && (
                <>
                  <select className={css.input} value={row.unit} aria-label={`Единица ${f.name}`} data-testid={`pit-el-unit-${f.name}`} onChange={(e) => change(f.name, { unit: e.target.value })}>
                    <option value="">{f.unit ? `по полю (${f.unit})` : "по полю"}</option>
                    {UNIT_CODES.map((u) => (
                      <option key={u} value={u}>
                        {gradeLabels[u] ?? u}
                      </option>
                    ))}
                  </select>
                  <input className={css.input} value={row.price} inputMode="decimal" placeholder={priceLabel(row.unit || null, f.unit, priceLabels)} aria-label={`Цена ${f.name}`} title="Цена за единицу металла; пусто — не задана (запуск недоступен)" data-testid={`pit-el-price-${f.name}`} onChange={(e) => change(f.name, { price: e.target.value })} />
                  <input className={css.input} value={row.sales} inputMode="decimal" placeholder="0" aria-label={`Сбыт ${f.name}`} title="Затраты на сбыт на единицу металла (тот же знаменатель, что у цены)" data-testid={`pit-el-sales-${f.name}`} onChange={(e) => change(f.name, { sales: e.target.value })} />
                  <input className={css.input} value={row.royalty} inputMode="decimal" placeholder="0" aria-label={`Роялти ${f.name}`} title="Доля от выручки за вычетом сбыта: 0,03 — 3 %" data-testid={`pit-el-roy-${f.name}`} onChange={(e) => change(f.name, { royalty: e.target.value })} />
                </>
              )}
            </div>
          );
        })}
        {!list.length && <span className={css.note}>У модели нет числовых полей: выберите модель в сценарии.</span>}
      </div>
      <div className={css.row}>
        <button type="button" className={css.btn} disabled={!proposed.length} title={proposed.length ? "Добавить поля *_CUT или с ролью «содержание»" : "У модели нет полей содержаний, которые можно предложить"} data-testid="pit-el-proposed" onClick={() => setEconForm({ elements: [...form.elements, ...proposed.filter((p) => rowOf(p.field) < 0)] })}>
          Взять поля по умолчанию
        </button>
        <span className={form.elements.length > limits.elements ? css.error : css.note}>
          Выбрано {form.elements.length} из {limits.elements}. Цена — за единицу металла: $/унц для г/т, ppm, ppb; $/т для %.
        </span>
      </div>
      <Issue which="economics" field="elements" />
    </Fieldset>
  );
}

// ---- the density ----

export function DensityPart({ form, numbers, disabled, auto }: EconProps & { auto: string | null }) {
  return (
    <Fieldset title="Плотность" disabled={disabled}>
      <div className={mod.radios} role="radiogroup" aria-label="Плотность">
        <label>
          <input type="radio" name="pit-density" checked={form.densityMode === "auto"} data-testid="pit-density-auto" onChange={() => setEconForm({ densityMode: "auto" })} />
          Автоматически{auto ? `: поле ${auto}` : ": поле SG или поле с ролью «плотность» не найдено"}
        </label>
        <label>
          <input type="radio" name="pit-density" checked={form.densityMode === "field"} data-testid="pit-density-field-on" onChange={() => setEconForm({ densityMode: "field" })} />
          Поле модели
        </label>
        {form.densityMode === "field" && (
          <select className={css.input} value={form.densityField} aria-label="Поле плотности" data-testid="pit-density-field" onChange={(e) => setEconForm({ densityField: e.target.value })}>
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
          <input type="radio" name="pit-density" checked={form.densityMode === "value"} data-testid="pit-density-value-on" onChange={() => setEconForm({ densityMode: "value" })} />
          Число, т/м³
        </label>
        {form.densityMode === "value" && <input className={css.input} value={form.densityValue} inputMode="decimal" aria-label="Плотность, т/м³" data-testid="pit-density-value" onChange={(e) => setEconForm({ densityValue: e.target.value })} />}
      </div>
      <NumberField label="Плотность по умолчанию, т/м³" value={form.defaultDensity} placeholder="нет" hint="Для блоков, у которых поле плотности пусто; пусто — такие блоки не считаются (запуск откажет)" testId="pit-default-density" onChange={(v) => setEconForm({ defaultDensity: v })} />
      <span className={css.note}>Блок без плотности нулём не считается: без плотности по умолчанию запуск откажет и назовёт число таких блоков.</span>
      <Issue which="economics" field="density" />
    </Fieldset>
  );
}

// ---- the mining costs ----

export function MiningPart({ form, disabled, zRef }: EconProps & { zRef: number | null }) {
  return (
    <Fieldset title="Затраты на добычу" disabled={disabled}>
      <div className={mod.pair}>
        <NumberField label="c_m0, $/т" value={form.cM0} placeholder="не задано" hint="Затраты на добычу тонны на отметке Z_ref; пусто — не задано (запуск недоступен)" testId="pit-c-m0" onChange={(v) => setEconForm({ cM0: v })} />
        <NumberField label="Z_ref, м" value={form.zRef} placeholder={zRef === null ? "верх сетки" : `верх сетки: ${fmt(zRef)}`} hint="Отметка, от которой растут затраты; пусто — верх сетки модели" testId="pit-z-ref" onChange={(v) => setEconForm({ zRef: v })} />
        <NumberField label="H_гл, м" value={form.hGl} placeholder="10" hint="Каждые H_гл метров ниже Z_ref добавляют c_гл" testId="pit-h-gl" onChange={(v) => setEconForm({ hGl: v })} />
        <NumberField label="c_гл, $/т" value={form.cGl} placeholder="0" hint="Прирост затрат на добычу за каждые H_гл метров глубины" testId="pit-c-gl" onChange={(v) => setEconForm({ cGl: v })} />
      </div>
      <Issue which="economics" field="mining" />
    </Fieldset>
  );
}

// ---- RF, the prefix ----

export function RfPart({ form, disabled, limits }: EconProps) {
  const count = shellCount(form, limits);
  return (
    <Fieldset title="Коэффициенты цены RF" disabled={disabled}>
      <div className={mod.seg} role="group" aria-label="Задание RF">
        <button type="button" aria-pressed={form.rfMode === "range"} data-testid="pit-rf-range" onClick={() => setEconForm({ rfMode: "range" })}>
          Диапазон
        </button>
        <button type="button" aria-pressed={form.rfMode === "list"} data-testid="pit-rf-list" onClick={() => setEconForm({ rfMode: "list" })}>
          Список
        </button>
      </div>
      {form.rfMode === "range" ? (
        <div className={mod.triple}>
          <NumberField label="от" value={form.rfFrom} testId="pit-rf-from" onChange={(v) => setEconForm({ rfFrom: v })} />
          <NumberField label="до" value={form.rfTo} testId="pit-rf-to" onChange={(v) => setEconForm({ rfTo: v })} />
          <NumberField label="шаг" value={form.rfStep} testId="pit-rf-step" onChange={(v) => setEconForm({ rfStep: v })} />
        </div>
      ) : (
        <input className={css.input} value={form.rfList} spellCheck={false} aria-label="RF через точку с запятой" data-testid="pit-rf-text" onChange={(e) => setEconForm({ rfList: e.target.value })} />
      )}
      <span className={css.note} data-testid="pit-rf-count">
        {count === null ? "Оболочек: не определено — исправьте RF." : `Оболочек: ${count} из ${limits.shells}.`} RF от 0,01 до 5; 1 — базовая цена (по ней считается pit-by-pit).
      </span>
      <Issue which="economics" field="rf" />
    </Fieldset>
  );
}

export function PrefixPart({ form, disabled, limits }: EconProps) {
  return (
    <Fieldset title="Поля результата" disabled={disabled}>
      <label className={css.field}>
        <span>Префикс имён полей</span>
        <input className={css.input} value={form.prefix} maxLength={limits.prefix || DEFAULT_LIMITS.prefix} placeholder="без префикса" aria-label="Префикс" data-testid="pit-prefix" onChange={(e) => setEconForm({ prefix: e.target.value })} />
      </label>
      <span className={css.note}>
        В модель пишутся поля {form.prefix}PIT (номер наименьшей оболочки) и {form.prefix}ORE (руда или пустая при RF = 1). Если такое поле уже есть от другого расчёта, задайте префикс.
      </span>
      <Issue which="economics" field="prefix" />
    </Fieldset>
  );
}
