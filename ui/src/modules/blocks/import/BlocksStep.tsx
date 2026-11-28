/**
 * The step «Блочная модель» of «Импорт файлов» (ТЗ 7.31, 8.7.2; ADR 0023 решения 5–7): the card of a block file —
 * the roles of its columns, the units, the codes of «пусто», the grid with the turn and the check of three centres,
 * the warnings and the refusal of the core with the rows, the name of the model. A change reads the composition
 * again (`ObjectsWizard.setOptions`); a typed number is applied on Enter or on leaving the field (each keystroke
 * would read the file again). The model of the step — `model.ts`.
 */
import { useEffect, useState } from "react";
import type { FileOk, FileOptions, ObjectsWizard } from "../../importing/objects";
import {
  check3Text,
  choicesOf,
  DELIMITERS,
  draftErrors,
  FIELD_ROLE_TITLES,
  gridText,
  headline,
  isTextFormat,
  listText,
  n,
  parseCodes,
  patchDraft,
  rowsText,
  shownChoice,
  TRIPLE_TITLES,
  turnNote,
  unalignedOf,
  type BlocksDraft,
  type BlocksInfo,
  type Choice,
  type TripleKey,
} from "./model";
import { UnalignedChoice } from "./UnalignedChoice";
import css from "../../importing/ImportWizard.module.css";

const AXES = ["X", "Y", "Z"];

interface Props {
  wizard: ObjectsWizard;
  file: FileOk & { blocks: BlocksInfo };
  options: FileOptions;
}

export function BlocksStep({ wizard, file, options }: Props) {
  const info = file.blocks;
  const draft = options.blocks ?? null;
  const errors = draftErrors(draft);
  const set = (patch: Partial<BlocksDraft>) => void wizard.setOptions(file.path, { blocks: patchDraft(draft, patch) });
  const check = check3Text(info);
  const turn = turnNote(info, draft);
  const stem = file.block_models?.[0]?.name ?? file.file.replace(/\.[^.]+$/, "");
  const unaligned = unalignedOf(info.error); // a refusal with a diagnosis: the panel of the choice (ADR 0030)
  return (
    <div className={css.importStep} data-testid="blocks-step" data-format={info.format}>
      <div data-testid="blocks-headline">
        <b>Блочная модель</b> · {headline(info)}
      </div>
      <div className={css.options}>
        <NameField value={options.name ?? ""} placeholder={stem} fileName={file.file} onApply={(name) => void wizard.setOptions(file.path, { name })} />
        {isTextFormat(info.format) && (
          <label>
            Разделитель
            <select value={draft?.delimiter ?? ""} aria-label={`Разделитель ${file.file}`} onChange={(e) => set({ delimiter: e.target.value })}>
              {DELIMITERS.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.title}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {unaligned && <UnalignedChoice wizard={wizard} file={file} options={options} un={unaligned} />}
      {info.error && !unaligned && (
        <div className={css.error} role="alert" data-testid="blocks-error" data-code={info.error.code}>
          {info.error.message} {info.error.code === "blocks_unaligned" ? "" : info.error.hint}
          {info.error.code === "blocks_unaligned" && (
            <>
              <div data-testid="blocks-error-rows">{rowsText(info.error)}</div>
              <div>{info.error.hint.replace(/^Строки данных[^.]*\.\s*/, "")}</div>
            </>
          )}
        </div>
      )}
      {draft?.layout === "list" && <ListNote info={info} onBack={() => set({ layout: "grid" })} />}
      <ColumnsTable wizard={wizard} file={file} info={info} draft={draft} />
      <EmptyCodes info={info} value={draft?.empty ?? ""} error={errors.empty} onApply={(empty) => set({ empty })} />
      <GridPart info={info} draft={draft} errors={errors} onApply={set} listed={draft?.layout === "list"} />
      {check && (
        <div className={check.ok ? css.note : css.error} data-testid="blocks-check3" data-ok={check.ok ? "yes" : "no"}>
          {check.text}
        </div>
      )}
      {turn && (
        <div className={css.warn} data-testid="blocks-turn">
          {turn} <span className={css.tag}>предварительно</span>
        </div>
      )}
      {info.warnings.length > 0 && (
        <ul className={css.issues} data-testid="blocks-warnings">
          {info.warnings.map((w, i) => (
            <li key={i} className={css.warn}>
              {w}
            </li>
          ))}
        </ul>
      )}
      <div className={css.note}>
        Пустые значения (−99 и другие коды из списка выше) становятся пропусками, а не нулями. Дубликаты и перекрытия блоков находятся при загрузке: они попадут в журнал и в «Свойства» модели.
      </div>
    </div>
  );
}

/** The name of the model: applied on Enter or leaving the field. */
function NameField({ value, placeholder, fileName, onApply }: { value: string; placeholder: string; fileName: string; onApply: (name: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const apply = () => {
    if (draft.trim() !== value.trim()) onApply(draft.trim());
  };
  return (
    <label>
      Название модели
      <input value={draft} placeholder={placeholder} maxLength={200} style={{ width: 220 }} aria-label={`Название модели ${fileName}`} onChange={(e) => setDraft(e.target.value)} onBlur={apply} onKeyDown={(e) => e.key === "Enter" && apply()} />
    </label>
  );
}

function ColumnsTable({ wizard, file, info, draft }: { wizard: ObjectsWizard; file: FileOk; info: BlocksInfo; draft: BlocksDraft | null }) {
  const choose = (column: string, choice: Choice) => void wizard.setOptions(file.path, { blocks: patchDraft(draft, { columns: { ...(draft?.columns ?? {}), [column]: choice } }) });
  const units = (column: string, unit: string) => void wizard.setOptions(file.path, { blocks: patchDraft(draft, { units: { ...(draft?.units ?? {}), [column]: unit } }) });
  return (
    <table className={css.layers} aria-label={`Столбцы ${file.file}`} data-testid="blocks-columns">
      <thead>
        <tr>
          <th>Столбец</th>
          <th>Тип</th>
          <th>Роль</th>
          <th>Единица</th>
          <th>Найденное</th>
        </tr>
      </thead>
      <tbody>
        {info.columns.map((c) => {
          const choice = shownChoice(info, draft, c.name);
          const field = info.fields.find((f) => f.column === c.name);
          const isField = choice !== "skip" && !GEO_SET[choice];
          return (
            <tr key={c.name} data-column={c.name}>
              <td>{c.name}</td>
              <td className={css.note}>{c.type === "number" ? "число" : "текст"}</td>
              <td>
                <select value={choice} aria-label={`Роль столбца ${c.name}`} onChange={(e) => choose(c.name, e.target.value as Choice)}>
                  {choicesOf(c)
                    .filter((o) => o.id !== "auto")
                    .map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.title}
                      </option>
                    ))}
                </select>
              </td>
              <td>
                {isField && c.type === "number" ? (
                  <UnitField value={draft?.units[c.name] ?? field?.unit ?? ""} label={`Единица ${c.name}`} onApply={(u) => units(c.name, u)} />
                ) : (
                  ""
                )}
              </td>
              <td className={css.note}>{foundText(info, c.name, field?.role ?? null)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const GEO_SET: Record<string, true> = { x: true, y: true, z: true, dx: true, dy: true, dz: true };

/** What the core found in the column: a centre, a size, a field with its role and the codes of «пусто» met in it. */
function foundText(info: BlocksInfo, column: string, role: keyof typeof FIELD_ROLE_TITLES | null): string {
  const col = info.columns.find((c) => c.name === column);
  if (col?.role) return `${col.role.toUpperCase()} — по названию`;
  const field = info.fields.find((f) => f.column === column);
  if (!field) return "не загружается";
  const parts = [role ? FIELD_ROLE_TITLES[role] : field.kind === "category" ? "категория" : "поле"];
  if (field.constant) parts.push("одно значение во всех блоках");
  if (field.empty.length) parts.push(`пусто: ${field.empty.map((v) => n(v)).join("; ")}`);
  return parts.join(" · ");
}

function UnitField({ value, label, onApply }: { value: string; label: string; onApply: (unit: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const apply = () => {
    if (draft.trim() !== value.trim()) onApply(draft.trim());
  };
  return <input className={css.unit} value={draft} maxLength={40} aria-label={label} placeholder="—" onChange={(e) => setDraft(e.target.value)} onBlur={apply} onKeyDown={(e) => e.key === "Enter" && apply()} />;
}

function EmptyCodes({ info, value, error, onApply }: { info: BlocksInfo; value: string; error?: string; onApply: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const apply = () => {
    if (draft.trim() !== value.trim()) onApply(draft.trim());
  };
  const offered = info.empty_candidates.filter((c) => !parseCodes(value).codes.includes(c));
  return (
    <div className={css.options} data-testid="blocks-empty">
      <label>
        Коды «пусто»
        <input value={draft} placeholder="например −99" style={{ width: 160 }} aria-label="Коды пустого" aria-invalid={error ? true : undefined} onChange={(e) => setDraft(e.target.value)} onBlur={apply} onKeyDown={(e) => e.key === "Enter" && apply()} />
      </label>
      {offered.map((c) => (
        <span key={c} className={css.note}>
          В файле встречается {n(c)}{" "}
          <button type="button" className={css.linkBtn} aria-label={`Принять код пустого ${n(c)}`} onClick={() => onApply([value.trim(), String(c)].filter(Boolean).join("; "))}>
            Принять как «пусто»
          </button>
        </span>
      ))}
      {error && (
        <span className={css.error} role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

const TRIPLE_ORDER: TripleKey[] = ["corner", "size", "counts"];

/** The model is read as a list of blocks (ADR 0030): what it holds and the way back to the grid. */
function ListNote({ info, onBack }: { info: BlocksInfo; onBack: () => void }) {
  const text = listText(info);
  return (
    <div className={css.group} data-testid="blocks-list">
      <div data-testid="blocks-list-text">
        <b>{text ?? "Модель будет импортирована списком блоков."}</b>
      </div>
      <div className={css.note}>
        Блоки хранятся как есть — центр и размер каждого, без родительской сетки и субблоков. Доступны показ и выбор блока, фильтры, калькулятор, плотность, категории, отчёт «Содержание–тоннаж», экспорт и регуляризация; срез, оценка, ресурсы, карьер, заполнение и OMF — после регуляризации в сетку.
      </div>
      <div className={css.options}>
        <button type="button" data-testid="blocks-list-back" onClick={onBack}>
          Вернуться к сетке
        </button>
      </div>
    </div>
  );
}

function GridPart({ info, draft, errors, onApply, listed = false }: { info: BlocksInfo; draft: BlocksDraft | null; errors: ReturnType<typeof draftErrors>; onApply: (patch: Partial<BlocksDraft>) => void; listed?: boolean }) {
  const g = info.grid;
  const found = (key: TripleKey, i: number): string => {
    const v = g?.[key as "corner" | "size" | "counts" | "div"];
    return v ? n(v[i]) : "";
  };
  return (
    <fieldset className={css.group} data-testid="blocks-grid">
      <legend>Сетка модели</legend>
      <div className={css.note}>
        {listed
          ? "Список блоков не делится на родителей: здесь задаются только поворот вокруг Z и оси, в которых записаны центры."
          : g
            ? `Определена по файлу: ${gridText(g)}.`
            : "Сетку по файлу определить не удалось: укажите её здесь."}{" "}
        {listed ? "" : "Пустое поле — «как в файле»."}
      </div>
      {(listed ? [] : [...TRIPLE_ORDER, ...(info.sub || draft?.div.some(Boolean) ? (["div"] as const) : [])]).map((key) => (
        <TripleField key={key} id={key} title={TRIPLE_TITLES[key]} values={draft?.[key] ?? ["", "", ""]} placeholders={[0, 1, 2].map((i) => found(key, i))} error={errors[key]} onApply={(v) => onApply({ [key]: v })} />
      ))}
      <div className={css.options}>
        <RotationField value={draft?.rotation ?? ""} placeholder={g ? n(g.rotation) : ""} error={errors.rotation} onApply={(rotation) => onApply({ rotation })} />
        {!listed && <TolField value={draft?.tol ?? ""} error={errors.tol} onApply={(tol) => onApply({ tol })} />}
        <label>
          Оси центров в файле
          <select value={draft?.centres ?? "project"} aria-label="Оси центров" onChange={(e) => onApply({ centres: e.target.value as "project" | "model" })}>
            <option value="project">оси проекта</option>
            <option value="model">оси повёрнутой модели</option>
          </select>
        </label>
      </div>
    </fieldset>
  );
}

function RotationField({ value, placeholder, error, onApply }: { value: string; placeholder: string; error?: string; onApply: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const apply = () => {
    if (draft.trim() !== value.trim()) onApply(draft.trim());
  };
  return (
    <>
      <label>
        Поворот вокруг Z, °
        <input value={draft} placeholder={placeholder || "0"} style={{ width: 80 }} aria-label="Поворот вокруг Z" aria-invalid={error ? true : undefined} onChange={(e) => setDraft(e.target.value)} onBlur={apply} onKeyDown={(e) => e.key === "Enter" && apply()} />
      </label>
      {error && (
        <span className={css.error} role="alert">
          {error}
        </span>
      )}
    </>
  );
}

/** How far an edge of a block may be from its line of the grid: empty — by the type of the columns (a number written with
 * two decimals — 0,005 m); at most a quarter of a sub-cell, the core says so. */
function TolField({ value, error, onApply }: { value: string; error?: string; onApply: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const apply = () => {
    if (draft.trim() !== value.trim()) onApply(draft.trim());
  };
  return (
    <>
      <label title="Насколько грань блока может отстоять от линии сетки. Пусто — по типу столбцов файла; не больше четверти подъячейки.">
        Допуск, м
        <input value={draft} placeholder="по столбцам" style={{ width: 90 }} aria-label="Допуск привязки" aria-invalid={error ? true : undefined} onChange={(e) => setDraft(e.target.value)} onBlur={apply} onKeyDown={(e) => e.key === "Enter" && apply()} />
      </label>
      {error && (
        <span className={css.error} role="alert">
          {error}
        </span>
      )}
    </>
  );
}

function TripleField({ id, title, values, placeholders, error, onApply }: { id: TripleKey; title: string; values: [string, string, string]; placeholders: string[]; error?: string; onApply: (v: [string, string, string]) => void }) {
  const [draft, setDraft] = useState<[string, string, string]>(values);
  const key = values.join("\u0001");
  useEffect(() => setDraft(values), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const apply = () => {
    if (draft.join("\u0001") !== key) onApply(draft);
  };
  return (
    <div className={css.options} data-testid={`blocks-grid-${id}`}>
      <span style={{ minWidth: 170 }}>{title}</span>
      {AXES.map((axis, i) => (
        <label key={axis}>
          {axis}
          <input
            value={draft[i]}
            placeholder={placeholders[i]}
            style={{ width: 110 }}
            aria-label={`${title}: ${axis}`}
            aria-invalid={error ? true : undefined}
            onChange={(e) => setDraft(draft.map((v, k) => (k === i ? e.target.value : v)) as [string, string, string])}
            onBlur={apply}
            onKeyDown={(e) => e.key === "Enter" && apply()}
          />
        </label>
      ))}
      {error && (
        <span className={css.error} role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
