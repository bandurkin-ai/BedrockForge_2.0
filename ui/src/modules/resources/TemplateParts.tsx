/**
 * The parts of the editor of the window «Шаблоны форм отчёта» (ТЗ 8.10 «Формы»; ADR 0027 решения 16–19): the name, the rows (the order by dragging or
 * ↑ ↓), the subtotals, the total, the keys, the columns, the units, the decimals and the sample of the form. A part changes the draft of
 * `templatesStore`; a template of the box is shown locked (`off`) — «Создать копию» makes one that can be changed. A wrong thing is told
 * under its part in Russian with a hint.
 */
import { useMemo, useState } from "react";
import css from "../../shell/PropsParts.module.css";
import { useSel } from "./ReportParts";
import { previewForm, SAMPLE_ELEMENTS } from "./templatePreview";
import { MAX_LABELS, MAX_SUBTOTALS, METAL_UNITS, METRIC_TITLES, moveItem, removeLabel, renameLabel, ROLES, TONNES_UNITS, toggleMember, VOLUME_UNITS, type Draft, type Metric, type Place, type TemplateBody } from "./templates";
import mod from "./Templates.module.css";
import { putDraft, setDraft, templatesStore } from "./templatesState";

export interface PartProps {
  d: Draft;
  /** the template is of the box (or the editor is busy): nothing can be changed */
  off: boolean;
}

/** The wrong thing of a place of the editor: the words and the hint (`index` marks a row). */
export function Issue({ place }: { place: Place }) {
  const issue = useSel(templatesStore, (s) => s.issue);
  if (!issue || issue.place !== place) return null;
  return (
    <p className={css.error} role="alert" data-testid={`tpl-issue-${place}`}>
      {issue.message} {issue.hint}
    </p>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset className={css.group}>
      <legend>{title}</legend>
      {children}
    </fieldset>
  );
}

const Move = ({ onUp, onDown, up, down, off, what }: { onUp: () => void; onDown: () => void; up: boolean; down: boolean; off: boolean; what: string }) => (
  <>
    <button type="button" className={css.iconBtn} disabled={off || !up} aria-label={`${what}: выше`} title="Выше" onClick={onUp}>
      ↑
    </button>
    <button type="button" className={css.iconBtn} disabled={off || !down} aria-label={`${what}: ниже`} title="Ниже" onClick={onDown}>
      ↓
    </button>
  </>
);

export function NamePart({ d, off }: PartProps) {
  const wrong = useSel(templatesStore, (s) => s.issue?.place === "name");
  return (
    <Group title="Название">
      <input className={css.input} value={d.name} maxLength={100} disabled={off} placeholder="Например, «ГКЗ, участок 2»" aria-label="Название шаблона" aria-invalid={wrong} data-testid="tpl-name" onChange={(e) => setDraft({ name: e.target.value })} />
      <Issue place="name" />
    </Group>
  );
}

/** The rows of the form in order: the groups of the key (the categories of the report); dragging or ↑ ↓. */
export function LabelsPart({ d, off }: PartProps) {
  const bad = useSel(templatesStore, (s) => (s.issue?.place === "labels" ? s.issue.index : null));
  const [over, setOver] = useState<number | null>(null);
  const [from, setFrom] = useState<number | null>(null);
  const move = (a: number, b: number) => putDraft({ ...d, labels: moveItem(d.labels, a, b) });
  return (
    <Group title="Строки по порядку">
      <div className={mod.rows} data-testid="tpl-labels">
        {d.labels.map((l, i) => (
          <div
            key={i}
            className={`${mod.rowEdit} ${bad === i ? mod.rowBad : ""}`}
            draggable={!off}
            data-over={over === i}
            data-testid={`tpl-label-${i}`}
            onDragStart={() => setFrom(i)}
            onDragOver={(e) => {
              if (from !== null) {
                e.preventDefault();
                setOver(i);
              }
            }}
            onDragLeave={() => setOver(null)}
            onDrop={(e) => {
              e.preventDefault();
              if (from !== null) move(from, i);
              setFrom(null);
              setOver(null);
            }}
            onDragEnd={() => {
              setFrom(null);
              setOver(null);
            }}
          >
            <input className={css.input} value={l} maxLength={100} disabled={off} placeholder="Подпись группы, например «A»" aria-label={`Строка ${i + 1}`} data-testid={`tpl-label-input-${i}`} onChange={(e) => putDraft(renameLabel(d, i, e.target.value))} />
            <Move off={off} what={`Строка ${i + 1}`} up={i > 0} down={i < d.labels.length - 1} onUp={() => move(i, i - 1)} onDown={() => move(i, i + 1)} />
            <button type="button" className={css.iconBtn} disabled={off || d.labels.length < 2} aria-label={`Убрать строку ${i + 1}`} title="Убрать строку" data-testid={`tpl-label-del-${i}`} onClick={() => putDraft(removeLabel(d, i))}>
              ×
            </button>
          </div>
        ))}
      </div>
      <div className={css.row}>
        <button type="button" className={css.btn} disabled={off || d.labels.length >= MAX_LABELS} data-testid="tpl-label-add" onClick={() => putDraft({ ...d, labels: [...d.labels, ""] })}>
          Добавить строку
        </button>
        <span className={css.note}>Группы отчёта, которых нет в списке, попадут в строку «Прочие».</span>
      </div>
      <Issue place="labels" />
    </Group>
  );
}

/** A subtotal — a label and its members (at least two rows of the template); it stands after its last member. */
export function SubtotalsPart({ d, off }: PartProps) {
  const bad = useSel(templatesStore, (s) => (s.issue?.place === "subtotals" ? s.issue.index : null));
  const labels = d.labels.map((l) => l.trim()).filter(Boolean);
  return (
    <Group title="Подытоги">
      {d.subtotals.map((s, i) => (
        <div key={i} className={`${mod.subtotal} ${bad === i ? mod.rowBad : ""}`} data-testid={`tpl-sub-${i}`}>
          <div className={css.row}>
            <input className={css.input} value={s.label} maxLength={100} disabled={off} placeholder="Подпись, например «A + B + C1»" aria-label={`Подпись подытога ${i + 1}`} data-testid={`tpl-sub-label-${i}`} onChange={(e) => putDraft({ ...d, subtotals: d.subtotals.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) })} />
            <button type="button" className={css.iconBtn} disabled={off} aria-label={`Убрать подытог ${i + 1}`} title="Убрать подытог" data-testid={`tpl-sub-del-${i}`} onClick={() => putDraft({ ...d, subtotals: d.subtotals.filter((_, j) => j !== i) })}>
              ×
            </button>
          </div>
          <div className={mod.members}>
            {labels.map((l) => (
              <label key={l}>
                <input type="checkbox" disabled={off} checked={s.members.includes(l)} data-testid={`tpl-sub-${i}-member-${l}`} onChange={(e) => putDraft(toggleMember(d, i, l, e.target.checked))} />
                {l}
              </label>
            ))}
            {!labels.length && <span className={css.note}>Сначала добавьте строки шаблона.</span>}
          </div>
        </div>
      ))}
      <div className={css.row}>
        <button type="button" className={css.btn} disabled={off || d.subtotals.length >= MAX_SUBTOTALS || labels.length < 2} data-testid="tpl-sub-add" onClick={() => putDraft({ ...d, subtotals: [...d.subtotals, { label: "", members: [] }] })}>
          Добавить подытог
        </button>
        <span className={css.note}>В подытог входят не меньше двух строк.</span>
      </div>
      <Issue place="subtotals" />
    </Group>
  );
}

export function TotalPart({ d, off }: PartProps) {
  return (
    <Group title="Итог">
      <label className={css.check}>
        <input type="checkbox" disabled={off} checked={d.hasTotal} data-testid="tpl-has-total" onChange={(e) => setDraft({ hasTotal: e.target.checked })} />
        Итоговая строка
      </label>
      <input className={css.input} value={d.total} maxLength={100} disabled={off || !d.hasTotal} aria-label="Подпись итога" data-testid="tpl-total" onChange={(e) => setDraft({ total: e.target.value })} />
      <Issue place="total" />
    </Group>
  );
}

/** The keys: the rows go by the key «категория», the sections (the form is cut into tables) — by the key chosen here (by the domain). */
export function KeysPart({ d, off }: PartProps) {
  return (
    <Group title="Ключи группировки">
      <label className={css.field}>
        <span>Строки — по ключу</span>
        <select className={css.input} value={d.key} disabled={off} aria-label="Ключ строк" data-testid="tpl-key" onChange={(e) => setDraft({ key: e.target.value })}>
          {ROLES.map(([r, t]) => (
            <option key={r} value={r}>
              {t}
            </option>
          ))}
          {!ROLES.some(([r]) => r === d.key) && <option value={d.key}>{d.key}</option>}
        </select>
      </label>
      <Issue place="key" />
      <label className={css.field}>
        <span>Разделы — по ключу</span>
        <select className={css.input} value={d.section} disabled={off} aria-label="Ключ разделов" data-testid="tpl-section" onChange={(e) => setDraft({ section: e.target.value })}>
          <option value="">без разделов (одна таблица)</option>
          {ROLES.filter(([r]) => r !== d.key).map(([r, t]) => (
            <option key={r} value={r}>
              {t}
            </option>
          ))}
          {d.section && ![...ROLES.map(([r]) => r), ""].includes(d.section) && <option value={d.section}>{d.section}</option>}
        </select>
      </label>
      <span className={css.note}>Разделы режут форму на таблицы: например, по каждому телу (домену) своя таблица.</span>
      <Issue place="section" />
      <label className={css.check}>
        <input type="checkbox" disabled={off} checked={d.balance} data-testid="tpl-balance" onChange={(e) => setDraft({ balance: e.target.checked })} />
        Столбцы «Балансовые» и «Забалансовые»
      </label>
    </Group>
  );
}

/** The columns: which of the four are shown and in what order. */
export function ColumnsPart({ d, off }: PartProps) {
  const move = (a: number, b: number) => setDraft({ order: moveItem(d.order, a, b) });
  return (
    <Group title="Столбцы">
      {d.order.map((m: Metric, i) => (
        <div key={m} className={mod.metric} data-testid={`tpl-col-${m}`}>
          <input type="checkbox" disabled={off} checked={d.on[m]} aria-label={METRIC_TITLES[m]} data-testid={`tpl-col-on-${m}`} onChange={(e) => setDraft({ on: { ...d.on, [m]: e.target.checked } })} />
          <span>{METRIC_TITLES[m]}</span>
          <Move off={off} what={METRIC_TITLES[m]} up={i > 0} down={i < d.order.length - 1} onUp={() => move(i, i - 1)} onDown={() => move(i, i + 1)} />
        </div>
      ))}
      <Issue place="columns" />
    </Group>
  );
}

const Select = ({ label, value, options, off, id, onChange }: { label: string; value: string; options: [string, string][]; off: boolean; id: string; onChange: (v: string) => void }) => (
  <label className={css.field}>
    <span>{label}</span>
    <select className={css.input} value={value} disabled={off} aria-label={label} data-testid={id} onChange={(e) => onChange(e.target.value)}>
      {options.map(([v, t]) => (
        <option key={v} value={v}>
          {t}
        </option>
      ))}
    </select>
  </label>
);

const table = (t: Record<string, [string, number]>): [string, string][] => Object.entries(t).map(([k, [label]]) => [k, label]);

/** The units: the tonnes, the volume, the metal by the base unit of the element (g/t, ppm, ppb → grams; % → tonnes). */
export function UnitsPart({ d, off }: PartProps) {
  const metal = table(METAL_UNITS);
  return (
    <Group title="Единицы">
      <div className={mod.four}>
        <Select label="Тоннаж" id="tpl-unit-tonnes" off={off} value={d.units.tonnes} options={table(TONNES_UNITS)} onChange={(v) => setDraft({ units: { ...d.units, tonnes: v } })} />
        <Select label="Объём" id="tpl-unit-volume" off={off} value={d.units.volume} options={table(VOLUME_UNITS)} onChange={(v) => setDraft({ units: { ...d.units, volume: v } })} />
        <Select label="Металл: г/т, ppm, ppb" id="tpl-unit-metal-g" off={off} value={d.units.metal.g} options={metal} onChange={(v) => setDraft({ units: { ...d.units, metal: { ...d.units.metal, g: v } } })} />
        <Select label="Металл: %" id="tpl-unit-metal-t" off={off} value={d.units.metal.t} options={metal} onChange={(v) => setDraft({ units: { ...d.units, metal: { ...d.units.metal, t: v } } })} />
      </div>
      <Issue place="units" />
    </Group>
  );
}

const DIGITS: [string, string][] = [0, 1, 2, 3, 4, 5, 6].map((n) => [String(n), String(n)]);

export function DecimalsPart({ d, off }: PartProps) {
  const one = (m: Metric, label: string) => <Select key={m} label={label} id={`tpl-dec-${m}`} off={off} value={String(d.decimals[m])} options={DIGITS} onChange={(v) => setDraft({ decimals: { ...d.decimals, [m]: Number(v) } })} />;
  return (
    <Group title="Знаки после запятой">
      <div className={mod.four}>
        {one("tonnes", "Тоннаж")}
        {one("volume", "Объём")}
        {one("grade", "Содержание")}
        {one("metal", "Металл")}
      </div>
      <Issue place="decimals" />
    </Group>
  );
}

/** The sample of the form: made-up round numbers in the units and with the decimals of the draft; the report is not recalculated. */
export function PreviewPart({ d }: { d: Draft }) {
  const [el, setEl] = useState(0);
  const sample = useMemo(() => {
    // the draft is shown as it stands (the check is the save's job): the empty rows are not rows yet
    const t: TemplateBody = { ...asBody(d), labels: d.labels.map((l) => l.trim()).filter(Boolean) };
    return previewForm(t, [SAMPLE_ELEMENTS[el]]);
  }, [d, el]);
  const groups = sample.columns.reduce<{ title: string; n: number }[]>((a, c) => (a.length && a[a.length - 1].title === (c.group ?? "") ? (a[a.length - 1].n++, a) : [...a, { title: c.group ?? "", n: 1 }]), []);
  return (
    <div data-testid="tpl-preview">
      <h4 className={mod.head}>Пример формы</h4>
      <div className={css.row} role="radiogroup" aria-label="Элемент примера">
        {SAMPLE_ELEMENTS.map((e, i) => (
          <label key={e.name} className={css.check}>
            <input type="radio" name="tpl-sample-element" checked={el === i} data-testid={`tpl-sample-el-${i}`} onChange={() => setEl(i)} />
            {e.name}, {e.unit}
          </label>
        ))}
      </div>
      <p className={css.note}>Числа условные: пример показывает порядок строк, подытоги, единицы и знаки. Отчёты не пересчитываются.</p>
      {sample.sections.map((s, si) => (
        <div key={si}>
          {s.title && <h5 className={mod.sectionTitle}>{s.title}</h5>}
          <div className={css.tableWrap}>
            <table className={`${css.table} ${mod.sampleTable}`} data-testid={`tpl-sample-${si}`}>
              <thead>
                {groups.some((g) => g.title) && (
                  <tr>
                    <th />
                    {groups.map((g, gi) => (
                      <th key={gi} colSpan={g.n} data-group="true">
                        {g.title}
                      </th>
                    ))}
                  </tr>
                )}
                <tr>
                  <th>Группа</th>
                  {sample.columns.map((c) => (
                    <th key={c.key}>{c.title}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {s.rows.map((r, ri) => (
                  <tr key={ri} data-type={r.type}>
                    <td>{r.label}</td>
                    {r.cells.map((c, ci) => (
                      <td key={ci}>{c}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

/** The draft as the body of a template for the sample (a loose reading: the sample never refuses, the save checks). */
function asBody(d: Draft): TemplateBody {
  return {
    id: d.id ?? "",
    name: d.name,
    version: d.version ?? 1,
    builtin: d.builtin,
    labels: d.labels,
    subtotals: d.subtotals,
    total: d.hasTotal ? d.total.trim() || null : null,
    key: d.key,
    section: d.section || null,
    columns: d.order.filter((m) => d.on[m]),
    balance: d.balance,
    units: d.units,
    decimals: d.decimals,
  };
}
