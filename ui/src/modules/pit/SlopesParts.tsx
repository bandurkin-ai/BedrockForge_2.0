/**
 * The parts of the window «Углы бортов карьера» (ТЗ 8.11.2, 8.11.5; ADR 0028 решения 4, 9–12): the table of sectors {azimuth, angle} (up to 16), the domains (a category field, a table of
 * its own for a label — up to 16 labels), K (auto or 1…20) and the tolerance of the angle error. Every part reads the form and writes it through `setSlopesForm`; the words under a part
 * are the first wrong thing of the form or the refusal of the core (`Issue`); a row the refusal names (`slopes.table.2.angle`) is marked.
 */
import css from "../../shell/PropsParts.module.css";
import { MAX_ANGLE, MAX_K, MAX_TOLERANCE, MIN_ANGLE, MIN_TOLERANCE, newSector, type DomainRow, type SectorRow, type SlopesForm } from "./formsSlopes";
import mod from "./Pit.module.css";
import { Fieldset, Issue, useSel } from "./PitParts";
import { pitStore, setSlopesForm } from "./state";

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

/** Is the row `i` of the table at `prefix` (`slopes.table`, `slopes.domains.1.table`) the one the issue names? */
const marked = (param: string | null | undefined, prefix: string, i: number): boolean => !!param && param.startsWith(`${prefix}.${i}.`);

export interface SectorTableProps {
  rows: readonly SectorRow[];
  onChange: (rows: SectorRow[]) => void;
  disabled: boolean;
  max: number;
  /** the path of the table in the params: the rows named by a refusal are marked */
  prefix: string;
  testId: string;
}

/** A table of sectors: the azimuth (from the north, clockwise) and the angle of the slope. */
export function SectorTable({ rows, onChange, disabled, max, prefix, testId }: SectorTableProps) {
  const issue = useSel(pitStore, (s) => s.slopesIssue);
  const set = (i: number, patch: Partial<SectorRow>) => onChange(rows.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  return (
    <div className={mod.sectors} role="group" aria-label="Секторы" data-testid={testId} data-rows={rows.length}>
      <div className={`${mod.sector} ${mod.elementHead}`}>
        <span>№</span>
        <span title="От севера по часовой стрелке, 0…360°">Азимут, °</span>
        <span title="Угол борта карьера, 10…80°">Угол борта, °</span>
        <span />
      </div>
      {rows.map((r, i) => (
        <div key={i} className={`${mod.sector} ${marked(issue?.param, prefix, i) ? mod.marked : ""}`} data-testid={`${testId}-row-${i}`}>
          <span className={mod.ruleNo}>{i + 1}</span>
          <input className={css.input} value={r.az} inputMode="decimal" placeholder={rows.length === 1 ? "0" : "азимут"} disabled={disabled} aria-label={`Азимут сектора ${i + 1}`} data-testid={`${testId}-az-${i}`} onChange={(e) => set(i, { az: e.target.value })} />
          <input className={css.input} value={r.angle} inputMode="decimal" placeholder="угол" disabled={disabled} aria-label={`Угол сектора ${i + 1}`} data-testid={`${testId}-angle-${i}`} onChange={(e) => set(i, { angle: e.target.value })} />
          <button type="button" className={css.iconBtn} title={`Удалить сектор ${i + 1}`} aria-label={`Удалить сектор ${i + 1}`} disabled={disabled || rows.length <= 1} data-testid={`${testId}-del-${i}`} onClick={() => onChange(rows.filter((_, k) => k !== i))}>
            ✕
          </button>
        </div>
      ))}
      <div className={mod.add}>
        <button type="button" className={css.btn} disabled={disabled || rows.length >= max} title={rows.length >= max ? `Секторов не больше ${max}` : "Добавить сектор"} data-testid={`${testId}-add`} onClick={() => onChange([...rows, newSector()])}>
          Добавить сектор
        </button>
        <span className={css.note}>
          {rows.length} из {max}
        </span>
      </div>
    </div>
  );
}

/** The table of the angles of the whole pit. */
export function TablePart({ form, disabled, max }: { form: SlopesForm; disabled: boolean; max: number }) {
  return (
    <Fieldset title="Углы бортов" disabled={disabled}>
      <SectorTable rows={form.table} onChange={(table) => setSlopesForm({ table })} disabled={disabled} max={max} prefix="slopes.table" testId="pit-sectors" />
      <span className={css.note}>Азимут — от севера по часовой стрелке (0…360°), угол — от 10 до 80°. Один сектор без азимута — постоянный угол.</span>
      <Issue which="slopes" field="slopes" />
    </Fieldset>
  );
}

export interface DomainsProps {
  form: SlopesForm;
  disabled: boolean;
  /** the category fields of the model and their labels */
  categories: readonly { name: string; labels: readonly string[] }[];
  max: number;
  maxSectors: number;
}

/** The domains: a category field; a label with its own table of angles (the others take the common one). */
export function DomainsPart({ form, disabled, categories, max, maxSectors }: DomainsProps) {
  const issue = useSel(pitStore, (s) => s.slopesIssue);
  const labels = categories.find((c) => same(c.name, form.domainField))?.labels ?? [];
  const extra = form.domains.filter((d) => !labels.some((l) => same(l, d.label)));
  const rowOf = (label: string): number => form.domains.findIndex((d) => same(d.label, label));
  const setDomain = (label: string, patch: Partial<DomainRow>) => setSlopesForm((f) => ({ ...f, domains: f.domains.map((d) => (same(d.label, label) ? { ...d, ...patch } : d)) }));
  const own = (label: string, on: boolean) => setSlopesForm((f) => ({ ...f, domains: on ? [...f.domains, { label, table: f.table.map((r) => ({ ...r })) }] : f.domains.filter((d) => !same(d.label, label)) }));
  const shown = [...labels.map((l) => ({ label: l, extra: false })), ...extra.map((d) => ({ label: d.label, extra: true }))];
  return (
    <Fieldset title="Домены" disabled={disabled}>
      <label className={css.check}>
        <input type="checkbox" checked={form.domainOn} data-testid="pit-domains-on" onChange={(e) => setSlopesForm({ domainOn: e.target.checked })} />
        Свои углы для доменов
      </label>
      {form.domainOn && (
        <>
          <label className={css.field}>
            <span>Поле доменов</span>
            <select className={css.input} value={form.domainField} aria-label="Поле доменов" data-testid="pit-domain-field" onChange={(e) => setSlopesForm({ domainField: e.target.value, domains: [] })}>
              <option value="">{categories.length ? "— выберите поле —" : "в модели нет категорийных полей"}</option>
              {form.domainField && !categories.some((c) => same(c.name, form.domainField)) && <option value={form.domainField}>{form.domainField} (нет в модели)</option>}
              {categories.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <div className={mod.domains} role="group" aria-label="Домены" data-testid="pit-domains" data-own={form.domains.length}>
            {shown.map(({ label, extra: isExtra }) => {
              const i = rowOf(label);
              const d = i >= 0 ? form.domains[i] : null;
              const bad = !!issue?.param && issue.param.startsWith(`slopes.domains.${i}.`);
              return (
                <div key={label} className={`${mod.domain} ${bad ? mod.marked : ""}`} data-testid={`pit-domain-${label}`}>
                  <label className={css.check} title={isExtra ? "Подписи нет в поле доменов: уберите таблицу" : undefined}>
                    <input type="checkbox" checked={d !== null} disabled={disabled || (d === null && form.domains.length >= max)} data-testid={`pit-domain-own-${label}`} onChange={(e) => own(label, e.target.checked)} />
                    <b>{label}</b>
                    <span className={css.note}>{d ? "своя таблица" : isExtra ? "" : "общая таблица"}</span>
                  </label>
                  {d && <SectorTable rows={d.table} onChange={(table) => setDomain(label, { table })} disabled={disabled} max={maxSectors} prefix={`slopes.domains.${i}.table`} testId={`pit-domain-table-${label}`} />}
                </div>
              );
            })}
            {!shown.length && <span className={css.note}>Выберите поле: его подписи станут доменами.</span>}
          </div>
          <span className={css.note}>Не больше {max} доменов со своей таблицей. Дуга берёт угол из таблицы домена нижнего блока; блок домена без таблицы — из общей.</span>
        </>
      )}
      <Issue which="slopes" field="domains" />
    </Fieldset>
  );
}

/** K and the tolerance of the angle error. */
export function TemplatePart({ form, disabled, kAuto }: { form: SlopesForm; disabled: boolean; kAuto: number | null }) {
  return (
    <Fieldset title="Шаблон дуг" disabled={disabled}>
      <div className={mod.pair}>
        <div className={css.field}>
          <span>Число уровней K</span>
          <div className={mod.seg} role="group" aria-label="Число уровней K">
            <button type="button" aria-pressed={form.kAuto} title="Наименьшее K от 8 до 20, при котором ошибка угла в допуске" data-testid="pit-k-auto" onClick={() => setSlopesForm({ kAuto: true })}>
              Авто{kAuto !== null ? ` (${kAuto})` : ""}
            </button>
            <button type="button" aria-pressed={!form.kAuto} title={`Своё число уровней, 1…${MAX_K}`} data-testid="pit-k-own" onClick={() => setSlopesForm({ kAuto: false, k: form.k || (kAuto === null ? "8" : String(kAuto)) })}>
              Своё
            </button>
          </div>
          {!form.kAuto && <input className={css.input} value={form.k} inputMode="numeric" aria-label="Число уровней K" placeholder={`1…${MAX_K}`} data-testid="pit-k" onChange={(e) => setSlopesForm({ k: e.target.value })} />}
        </div>
        <label className={css.field} title={`Допустимая ошибка угла, ${MIN_TOLERANCE}…${MAX_TOLERANCE}°`}>
          <span>Допуск ошибки угла, °</span>
          <input className={css.input} value={form.tolerance} inputMode="decimal" aria-label="Допуск ошибки угла" placeholder="2,5" data-testid="pit-tolerance" onChange={(e) => setSlopesForm({ tolerance: e.target.value })} />
        </label>
      </div>
      <Issue which="slopes" field="k" />
      <Issue which="slopes" field="tolerance" />
      <span className={css.note}>
        Шаблон дуг приближает заданный угол {MIN_ANGLE}…{MAX_ANGLE}° уровнями K: чем больше K, тем точнее угол и тем больше смещений. Если ошибка угла больше допуска, «Проверить шаблон» посоветует увеличить K.
      </span>
    </Fieldset>
  );
}
