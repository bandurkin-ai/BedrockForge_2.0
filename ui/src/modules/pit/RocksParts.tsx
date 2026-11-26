/**
 * The parts of the window «Породы карьера» (ТЗ 8.11.2, 8.11.5; ADR 0028 решения 4, 6): how the type of a block is found (one type for all, a category field, an ordered list of
 * rules — the constructor of the filters per rule) and the table of the types (c_p, c_mр, the recovery of every element, the kind: ordinary, «только пустая», «воздух»).
 * Every part reads the form and writes it through `setRocksForm`; the words under a part are the first wrong thing of the form or the refusal of the core (`Issue`).
 */
import css from "../../shell/PropsParts.module.css";
import { RuleEditor, type ObjectOption } from "../blocks/filters/RuleEditor";
import type { FieldInfo } from "../blocks/report/state";
import { MODE_HINTS, MODE_TITLES, newRule, newRock, rowsOf, withRow, type RockBy, type RockMode, type RocksForm, type RockRule } from "./formsRocks";
import mod from "./Pit.module.css";
import { Fieldset, Issue } from "./PitParts";
import { setRocksForm } from "./state";

const BY_TITLES: Record<RockBy, string> = { all: "Один тип для всех блоков", field: "По категорийному полю", rules: "По правилам" };
const BY_HINTS: Record<RockBy, string> = {
  all: "Все блоки — один тип «Все породы»: одни затраты и извлечения.",
  field: "Каждая подпись поля и «(пусто)» — отдельный тип.",
  rules: "Блок получает тип первого правила, по которому его доля больше 0; остальные — «Прочие».",
};

export interface ByProps {
  form: RocksForm;
  disabled: boolean;
  /** the category fields of the model */
  categories: readonly FieldInfo[];
  /** the names of the fields of the model and the closed wireframes: for the rules */
  fields: readonly string[];
  wireframes: readonly ObjectOption[];
  max: number;
}

function RuleRow({ rule, i, last, props }: { rule: RockRule; i: number; last: boolean; props: ByProps }) {
  const { form, disabled, fields, wireframes } = props;
  const set = (patch: Partial<RockRule>) => setRocksForm({ rules: form.rules.map((r, k) => (k === i ? { ...r, ...patch } : r)) });
  const move = (d: -1 | 1) => {
    const next = [...form.rules];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    setRocksForm({ rules: next });
  };
  return (
    <div className={mod.rule} data-testid={`pit-rule-${i}`}>
      <div className={mod.ruleHead}>
        <span className={mod.ruleNo}>{i + 1}</span>
        <input className={css.input} value={rule.name} placeholder="Имя типа, например Богатая" aria-label={`Имя правила ${i + 1}`} data-testid={`pit-rule-name-${i}`} disabled={disabled} onChange={(e) => set({ name: e.target.value })} />
        <button type="button" className={css.iconBtn} title="Выше: правила применяются по порядку" aria-label="Правило выше" disabled={disabled || i === 0} data-testid={`pit-rule-up-${i}`} onClick={() => move(-1)}>
          ↑
        </button>
        <button type="button" className={css.iconBtn} title="Ниже: правила применяются по порядку" aria-label="Правило ниже" disabled={disabled || last} data-testid={`pit-rule-down-${i}`} onClick={() => move(1)}>
          ↓
        </button>
        <button type="button" className={css.iconBtn} title={`Удалить правило ${i + 1}`} aria-label={`Удалить правило ${i + 1}`} disabled={disabled} data-testid={`pit-rule-del-${i}`} onClick={() => setRocksForm({ rules: form.rules.filter((_, k) => k !== i) })}>
          ✕
        </button>
      </div>
      <RuleEditor draft={rule.draft} onChange={(draft) => set({ draft })} issue={null} fields={fields} wireframes={wireframes} testId={`pit-rule-tree-${i}`} />
    </div>
  );
}

/** How the type of a block is found. */
export function ByPart(props: ByProps) {
  const { form, disabled, categories, max } = props;
  return (
    <Fieldset title="Тип блока" disabled={disabled}>
      <div className={mod.seg} role="group" aria-label="Как определяется тип блока">
        {(Object.keys(BY_TITLES) as RockBy[]).map((b) => (
          <button key={b} type="button" aria-pressed={form.by === b} title={BY_HINTS[b]} data-testid={`pit-by-${b}`} onClick={() => setRocksForm({ by: b, rules: b === "rules" && !form.rules.length ? [newRule()] : form.rules })}>
            {BY_TITLES[b]}
          </button>
        ))}
      </div>
      <span className={css.note}>{BY_HINTS[form.by]}</span>
      {form.by === "field" && (
        <label className={css.field}>
          <span>Поле пород</span>
          <select className={css.input} value={form.field} aria-label="Поле пород" data-testid="pit-rock-field" onChange={(e) => setRocksForm({ field: e.target.value })}>
            <option value="">{categories.length ? "— выберите поле —" : "в модели нет категорийных полей"}</option>
            {form.field && !categories.some((c) => c.name.toLowerCase() === form.field.toLowerCase()) && <option value={form.field}>{form.field} (нет в модели)</option>}
            {categories.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {form.by === "rules" && (
        <div className={mod.rules} role="group" aria-label="Правила пород" data-testid="pit-rules">
          {form.rules.map((r, i) => (
            <RuleRow key={i} rule={r} i={i} last={i === form.rules.length - 1} props={props} />
          ))}
          <button type="button" className={css.btn} disabled={disabled || form.rules.length >= max} title={form.rules.length >= max ? `Правил не больше ${max}` : "Добавить правило в конец списка"} data-testid="pit-rule-add" onClick={() => setRocksForm({ rules: [...form.rules, newRule()] })}>
            Добавить правило
          </button>
        </div>
      )}
      <Issue which="rocks" field="rock_by" />
    </Fieldset>
  );
}

export interface TypesProps {
  form: RocksForm;
  disabled: boolean;
  /** the fields of the elements of the scenario: a column of the recovery each */
  elements: readonly string[];
  /** the types the data has */
  met: readonly string[];
  /** the types `validate` says are still not set: marked */
  open: readonly string[];
  limit: number;
}

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

/** The table of the types: the types met in the data first, then the rows of the scenario the data does not have. */
export function TypesPart({ form, disabled, elements, met, open, limit }: TypesProps) {
  const rows = rowsOf(form, met);
  const set = (name: string, patch: Parameters<typeof withRow>[2]) => setRocksForm((f) => withRow(f, name, patch));
  const setRec = (name: string, el: string, value: string) =>
    setRocksForm((f) => {
      const t = f.types.find((x) => same(x.name, name));
      return withRow(f, name, { rec: { ...(t?.rec ?? {}), [el]: value } });
    });
  const cols = `minmax(110px, 1.4fr) minmax(112px, 1fr) repeat(${2 + elements.length}, minmax(64px, 84px)) 24px`;
  return (
    <Fieldset title="Типы пород" disabled={disabled}>
      {!elements.length && <span className={css.warn}>У сценария нет элементов: извлечение задать не к чему. Элементы — в команде «Экономика».</span>}
      <div className={mod.rocks} role="group" aria-label="Типы пород" data-testid="pit-types">
        <div className={`${mod.rock} ${mod.elementHead}`} style={{ gridTemplateColumns: cols }}>
          <span>Тип</span>
          <span>Вид</span>
          <span title="Затраты на переработку, $/т">c_p, $/т</span>
          <span title="Доплата за добычу руды, $/т (пусто — 0)">c_mр, $/т</span>
          {elements.map((e) => (
            <span key={e} title={`Извлечение ${e}, доля 0…1`}>
              rec {e}
            </span>
          ))}
          <span />
        </div>
        {rows.map(({ row, met: isMet, touched }, i) => {
          const off = row.mode !== "rock";
          const unset = open.some((n) => same(n, row.name));
          return (
            <div key={`${i}-${row.name}`} className={`${mod.rock} ${unset ? mod.marked : ""}`} style={{ gridTemplateColumns: cols }} data-testid={`pit-type-${row.name}`} data-met={isMet} data-set={touched}>
              <span className={mod.typeName} title={isMet ? row.name : `${row.name}: в данных не встречается`}>
                {row.name}
                {!isMet && <span className={mod.unit}> · нет в данных</span>}
              </span>
              <select className={css.input} value={row.mode} aria-label={`Вид типа ${row.name}`} title={MODE_HINTS[row.mode]} data-testid={`pit-mode-${row.name}`} onChange={(e) => set(row.name, { mode: e.target.value as RockMode })}>
                {(Object.keys(MODE_TITLES) as RockMode[]).map((m) => (
                  <option key={m} value={m} title={MODE_HINTS[m]}>
                    {MODE_TITLES[m]}
                  </option>
                ))}
              </select>
              <input className={css.input} value={row.cP} inputMode="decimal" disabled={off} placeholder={off ? "—" : "не задано"} aria-label={`c_p типа ${row.name}`} data-testid={`pit-cp-${row.name}`} onChange={(e) => set(row.name, { cP: e.target.value })} />
              <input className={css.input} value={row.cMr} inputMode="decimal" disabled={off} placeholder={off ? "—" : "0"} aria-label={`c_mр типа ${row.name}`} data-testid={`pit-cmr-${row.name}`} onChange={(e) => set(row.name, { cMr: e.target.value })} />
              {elements.map((el) => {
                const key = Object.keys(row.rec).find((k) => same(k, el)) ?? el;
                return <input key={el} className={css.input} value={row.rec[key] ?? ""} inputMode="decimal" disabled={off} placeholder={off ? "—" : "не задано"} aria-label={`Извлечение ${el} типа ${row.name}`} data-testid={`pit-rec-${row.name}-${el}`} onChange={(e) => setRec(row.name, key, e.target.value)} />;
              })}
              {!isMet || touched ? (
                <button type="button" className={css.iconBtn} title={isMet ? "Сбросить строку: тип снова не задан" : "Убрать тип, которого нет в данных"} aria-label={`Убрать тип ${row.name}`} data-testid={`pit-type-del-${row.name}`} onClick={() => setRocksForm((f) => ({ ...f, types: f.types.filter((t) => !same(t.name, row.name)) }))}>
                  ✕
                </button>
              ) : (
                <span />
              )}
            </div>
          );
        })}
        {!rows.length && <span className={css.note}>Типов пока нет: выберите, как определяется тип блока.</span>}
      </div>
      <AddType form={form} disabled={disabled || form.types.length >= limit} limit={limit} />
      <span className={css.note}>Тип, который не задан, считается недостающим параметром: «Запустить» недоступна. «Только пустая» — блок не руда никогда; «Воздух» — вне карьера. Извлечение — доля 0…1.</span>
      <Issue which="rocks" field="rock_types" />
    </Fieldset>
  );
}

function AddType({ form, disabled, limit }: { form: RocksForm; disabled: boolean; limit: number }) {
  return (
    <form
      className={mod.add}
      onSubmit={(e) => {
        e.preventDefault();
        const input = e.currentTarget.elements.namedItem("name") as HTMLInputElement | null;
        const name = input?.value.trim() ?? "";
        if (!name || form.types.some((t) => same(t.name, name))) return;
        setRocksForm({ types: [...form.types, newRock(name)] });
        if (input) input.value = "";
      }}
    >
      <input name="name" className={css.input} placeholder="Имя ещё одного типа" aria-label="Имя нового типа" data-testid="pit-type-new" disabled={disabled} />
      <button type="submit" className={css.btn} disabled={disabled} title={disabled ? `Типов не больше ${limit}` : "Добавить тип, которого нет в списке выше"} data-testid="pit-type-add">
        Добавить тип
      </button>
    </form>
  );
}
