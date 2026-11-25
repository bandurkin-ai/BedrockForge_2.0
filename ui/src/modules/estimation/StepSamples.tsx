/**
 * Step 2 of the wizard «Оценка содержаний» (ТЗ 8.8.4, 8.8.2): the samples — the set of holes, the interval table, the numeric field,
 * the domain of the samples (a field and its values, preselected by the name of the domain of the blocks), the soft boundary
 * (the neighbouring domains and the distance d_м), the top cut (a field `*_CUT` or a threshold), the weights (DECL_W when the table
 * has it). Under the form — the counts and the declustered means of the core (`GET /estimate/defaults`) and what it matched
 * between the labels of the blocks and the values of the samples.
 */
import { useContext, useEffect, useState } from "react";
import { AppContext } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { loadFields, loadSets, loadTables, resolveTable, type FieldRow, type SetRow, type TableRow } from "../composites/api";
import { loadCodes, type CodeList } from "../intercepts/api";
import type { DefaultsAnswer } from "./api";
import { chartsStore } from "../charts/state";
import { contactChartsOf } from "./contactLink";
import { sameName, type WeightsChoice } from "./form";
import { useLatest, useSel } from "./hooks";
import { IssueAt, g, nf } from "./Parts";
import mod from "./Wizard.module.css";
import { setForm, wizardStore } from "./wizardState";

/** «Проб: 1 192 из 1 196 (пропуск значения 4, вне домена 310)»; the parts that are zero are not written. */
export function countsText(d: DefaultsAnswer): string {
  const c = d.samples.counts;
  const parts = [
    [c.missing, "пропуск значения"],
    [c.no_position, "нет положения"],
    [c.outside_domain, "вне домена"],
  ]
    .filter(([n]) => (n as number) > 0)
    .map(([n, t]) => `${t} ${nf(n as number)}`);
  return `Проб: ${nf(c.used)} из ${nf(c.rows)}${parts.length ? ` (${parts.join(", ")})` : ""}`;
}

/** The labels of the blocks the core matched with the values of the samples, and those it did not. */
export function mappingNotes(d: DefaultsAnswer, label: string): { matched: string | null; unmatched: string | null } {
  if (!d.mapping || !label) return { matched: null, unmatched: null };
  const hit = d.mapping[label] ?? [];
  return hit.length ? { matched: `Домен блоков «${label}» ← значения проб: ${hit.join(", ")}.`, unmatched: null } : { matched: null, unmatched: `Домену блоков «${label}» не нашлось значения в поле домена проб: отметьте значения вручную.` };
}

export function StepSamples() {
  const f = useSel(wizardStore, (s) => s.form);
  const defaults = useSel(wizardStore, (s) => s.defaults);
  const [error, setError] = useState<string | null>(null);
  const fail = (e: unknown) => setError(errorText(e));
  const sets = useLatest<SetRow[]>(loadSets, [], fail);
  const tables = useLatest<TableRow[]>(f.setId === null ? null : () => loadTables(f.setId!), [f.setId], fail);
  const tableKnown = !!f.table && !!tables?.some((t) => t.table === f.table);
  const fields = useLatest<FieldRow[]>(f.setId === null || !tableKnown ? null : () => loadFields(f.setId!, f.table!), [f.setId, f.table, tableKnown], fail);
  const dom = f.domain;
  const domTable = dom ? dom.table || f.table : null;
  const domFields = useLatest<FieldRow[]>(f.setId === null || !domTable || !tableKnown ? null : () => loadFields(f.setId!, domTable), [f.setId, domTable, tableKnown], fail);
  const codes = useLatest<CodeList>(f.setId === null || !domTable || !dom?.field || !tableKnown ? null : () => loadCodes(f.setId!, domTable, dom.field), [f.setId, domTable, dom?.field, tableKnown], fail);
  const numeric = (fields ?? []).filter((x) => x.kind === "number");
  const data = defaults.data;
  const app = useContext(AppContext);
  const charts = useSel(chartsStore, (c) => c.charts);
  const contacts = contactChartsOf(charts, f);

  // the set: the one given, else the first
  useEffect(() => {
    if (sets && f.setId === null && sets.length) setForm({ setId: sets[0].id });
  }, [sets]);
  // the table: a name becomes its key; none or unknown — the first (assay first); the table of the domain likewise
  useEffect(() => {
    if (!tables) return;
    const k = f.table ? resolveTable(tables, f.table) : null;
    const table = k ?? tables[0]?.table ?? null;
    const dk = dom?.table ? (resolveTable(tables, dom.table) ?? dom.table) : "";
    if (table !== f.table || (dom && dk !== dom.table)) setForm({ table, ...(table !== f.table && !k ? { field: null } : {}), ...(dom ? { domain: { ...dom, table: dk } } : {}) });
  }, [tables]);
  // the field: kept by name; none — the first numeric
  useEffect(() => {
    if (!fields) return;
    const keep = numeric.find((x) => x.name === f.field || x.key === f.field);
    const field = keep?.name ?? numeric[0]?.name ?? null;
    if (field !== f.field) setForm({ field });
  }, [fields]);
  // the values of the domain of the samples: the ones of the same name as the domain of the blocks, when none are chosen
  useEffect(() => {
    if (dom && codes && !dom.values.length && f.label) {
      const same = sameName(f.label, codes.codes);
      if (same.length) setForm({ domain: { ...dom, values: same } });
    }
  }, [codes]);

  const weightValue = f.weights.kind === "field" ? `field:${f.weights.field}` : f.weights.kind;
  const setWeights = (v: string) => setForm({ weights: (v.startsWith("field:") ? { kind: "field", field: v.slice(6) } : { kind: v === "none" ? "none" : "auto" }) as WeightsChoice });
  const toggle = (list: string[], c: string, on: boolean) => (on ? [...list, c] : list.filter((x) => x !== c));
  const neighbours = (codes?.codes ?? []).filter((c) => !dom?.values.includes(c));

  return (
    <>
      <fieldset className={css.group} data-testid="est-step-2">
        <legend>Пробы</legend>
        <label className={css.formRow}>
          <span>Набор скважин</span>
          <select className={css.input} aria-label="Набор скважин" data-testid="est-set" value={f.setId ?? ""} onChange={(e) => setForm({ setId: e.target.value ? Number(e.target.value) : null, table: null, field: null, domain: null, soft: { on: false, domains: [], dist: "0" }, weights: { kind: "auto" } })}>
            {f.setId === null && <option value="">{sets ? "—" : "Чтение…"}</option>}
            {(sets ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <IssueAt at="samples.set_id" id="set" />
        <label className={css.formRow}>
          <span>Таблица</span>
          <select className={css.input} aria-label="Таблица" data-testid="est-table" value={tableKnown ? f.table! : ""} onChange={(e) => setForm({ table: e.target.value || null, field: null, domain: null, soft: { on: false, domains: [], dist: "0" }, weights: { kind: "auto" } })}>
            {!(tables ?? []).length && <option value="">{tables ? "Нет интервальных таблиц" : "Чтение…"}</option>}
            {(tables ?? []).map((t) => (
              <option key={t.table} value={t.table}>
                {t.name}
                {t.origin?.stale ? " (устарел)" : ""}
              </option>
            ))}
          </select>
        </label>
        <IssueAt at="samples.table" id="table" />
        <label className={css.formRow}>
          <span>Поле</span>
          <select className={css.input} aria-label="Поле" data-testid="est-field" value={f.field ?? ""} onChange={(e) => setForm({ field: e.target.value || null })}>
            {!numeric.length && <option value="">{fields ? "Нет числовых полей" : "Чтение…"}</option>}
            {numeric.map((x) => (
              <option key={x.key} value={x.name}>
                {x.name}
                {x.unit ? `, ${x.unit}` : ""}
              </option>
            ))}
          </select>
        </label>
        <IssueAt at="samples.field" id="field" />
      </fieldset>

      <fieldset className={css.group}>
        <legend>Домен проб</legend>
        <label className={css.formRow}>
          <span>Домен</span>
          <select className={css.input} aria-label="Домен проб" data-testid="est-domain" value={dom ? "field" : "none"} onChange={(e) => setForm({ domain: e.target.value === "field" ? { table: "", field: "", values: [] } : null, ...(e.target.value === "field" ? {} : { soft: { on: false, domains: [], dist: "0" } }) })}>
            <option value="none">все пробы</option>
            <option value="field">по полю домена</option>
          </select>
        </label>
        {dom && (
          <>
            <label className={css.formRow}>
              <span>Таблица домена</span>
              <select className={css.input} aria-label="Таблица домена" data-testid="est-domain-table" value={dom.table || f.table || ""} onChange={(e) => setForm({ domain: { table: e.target.value === f.table ? "" : e.target.value, field: "", values: [] } })}>
                {(tables ?? []).map((t) => (
                  <option key={t.table} value={t.table}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <label className={css.formRow}>
              <span>Поле домена</span>
              <select className={css.input} aria-label="Поле домена проб" data-testid="est-domain-col" value={dom.field} onChange={(e) => setForm({ domain: { ...dom, field: e.target.value, values: [] } })}>
                <option value="">—</option>
                {(domFields ?? []).map((x) => (
                  <option key={x.key} value={x.name}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>
            {dom.field && (
              <div className={mod.checklist} role="group" aria-label="Значения домена проб" data-testid="est-domain-values">
                {(codes?.codes ?? []).map((c) => (
                  <label key={c} className={css.check}>
                    <input type="checkbox" checked={dom.values.includes(c)} onChange={(e) => setForm({ domain: { ...dom, values: toggle(dom.values, c, e.target.checked) }, soft: { ...f.soft, domains: f.soft.domains.filter((x) => x !== c) } })} />
                    {c}
                  </label>
                ))}
                {codes && !codes.codes.length && <p className={css.note}>В поле нет значений</p>}
                {codes?.truncated && (
                  <p className={css.note}>
                    Показаны {nf(codes.codes.length)} из {nf(codes.total)}
                  </p>
                )}
              </div>
            )}
          </>
        )}
        <IssueAt at={["samples.domain"]} id="domain" />
      </fieldset>

      <fieldset className={css.group} disabled={!dom}>
        <legend>Мягкая граница</legend>
        <label className={css.check}>
          <input type="checkbox" checked={f.soft.on} data-testid="est-soft-on" onChange={(e) => setForm({ soft: { ...f.soft, on: e.target.checked } })} />
          Пробы соседних доменов допускаются у границы
        </label>
        {f.soft.on && (
          <>
            <label className={css.formRow}>
              <span>Расстояние d_м, м</span>
              <input className={css.input} value={f.soft.dist} inputMode="decimal" aria-label="Расстояние мягкой границы" data-testid="est-soft-dist" onChange={(e) => setForm({ soft: { ...f.soft, dist: e.target.value } })} />
            </label>
            <div className={mod.checklist} role="group" aria-label="Соседние домены" data-testid="est-soft-domains">
              {neighbours.map((c) => (
                <label key={c} className={css.check}>
                  <input type="checkbox" checked={f.soft.domains.includes(c)} onChange={(e) => setForm({ soft: { ...f.soft, domains: toggle(f.soft.domains, c, e.target.checked) } })} />
                  {c}
                </label>
              ))}
              {!neighbours.length && <p className={css.note}>Выберите поле домена проб: соседними становятся его остальные значения.</p>}
            </div>
            {contacts.length > 0 && (
              <div className={mod.links} data-testid="est-soft-contact">
                <span className={css.note}>График контакта этой пары (расстояние d_м выбираете вы):</span>
                {contacts.map((c) => (
                  <button key={c.id} type="button" className={css.btn} disabled={!app} title="Открыть сохранённый график контакта" onClick={() => void app?.registry.execute("chart.open", { chart_id: c.id })}>
                    {c.name}
                  </button>
                ))}
              </div>
            )}
          </>
        )}
        <span className={css.note}>Проба соседнего домена попадает в оценку блока, если она не дальше d_м от центра блока (евклидово расстояние). Без мягкой границы — жёсткая.</span>
        <IssueAt at="samples.soft" id="soft" />
      </fieldset>

      <fieldset className={css.group}>
        <legend>Урезка и веса</legend>
        <label className={css.formRow}>
          <span>Урезка</span>
          <select className={css.input} aria-label="Урезка" data-testid="est-topcut" value={f.topcut.mode} onChange={(e) => setForm({ topcut: { ...f.topcut, mode: e.target.value as "none" | "field" | "value" } })}>
            <option value="none">без урезки</option>
            <option value="field">поле урезанных содержаний</option>
            <option value="value">порог T: min(z, T)</option>
          </select>
        </label>
        {f.topcut.mode === "field" && (
          <label className={css.formRow}>
            <span>Поле урезки</span>
            <select className={css.input} aria-label="Поле урезки" data-testid="est-topcut-field" value={f.topcut.field} onChange={(e) => setForm({ topcut: { ...f.topcut, field: e.target.value } })}>
              <option value="">— выберите поле —</option>
              {numeric
                .filter((x) => x.name !== f.field)
                .map((x) => (
                  <option key={x.key} value={x.name}>
                    {x.name}
                  </option>
                ))}
            </select>
          </label>
        )}
        {f.topcut.mode === "value" && (
          <label className={css.formRow}>
            <span>Порог T</span>
            <input className={css.input} value={f.topcut.value} inputMode="decimal" aria-label="Порог урезки" data-testid="est-topcut-value" onChange={(e) => setForm({ topcut: { ...f.topcut, value: e.target.value } })} />
          </label>
        )}
        <IssueAt at="samples.topcut" id="topcut" />
        <label className={css.formRow}>
          <span>Веса</span>
          <select className={css.input} aria-label="Веса" data-testid="est-weights" value={weightValue} onChange={(e) => setWeights(e.target.value)}>
            <option value="auto">DECL_W, если есть</option>
            <option value="none">без весов</option>
            {numeric
              .filter((x) => x.name !== f.field)
              .map((x) => (
                <option key={x.key} value={`field:${x.name}`}>
                  {x.name}
                </option>
              ))}
          </select>
        </label>
        <IssueAt at="samples.weights" id="weights" />
      </fieldset>

      {error && (
        <p className={css.error} role="alert">
          {error}
        </p>
      )}
      <SamplesSummary data={data} label={f.label} loading={defaults.phase === "loading"} failed={defaults.error?.message ?? null} />
    </>
  );
}

/** What the core said of the data of the form: the counts, the means, the matching of the domains. */
export function SamplesSummary({ data, label, loading, failed }: { data: DefaultsAnswer | null; label: string; loading: boolean; failed: string | null }) {
  if (!data) {
    return (
      <p className={failed ? css.error : css.note} role={failed ? "alert" : undefined} data-testid="est-samples-summary">
        {failed ?? (loading ? "Ядро считает пробы…" : "Выберите модель, набор, таблицу и поле: ядро покажет число проб и декластерированные средние.")}
      </p>
    );
  }
  const unit = data.samples.unit ? ` ${data.samples.unit}` : "";
  const notes = mappingNotes(data, label);
  const dom = data.samples.domain;
  return (
    <div data-testid="est-samples-summary">
      <p className={css.note}>
        {countsText(data)}. Среднее: по пробам {g(data.samples.mean.naive)}
        {unit}, декластерированное {g(data.samples.mean.declus)}
        {unit}; веса: {data.samples.weights ?? "нет"}.
      </p>
      {notes.matched && <p className={css.note}>{notes.matched}</p>}
      {notes.unmatched && (
        <p className={css.error} role="alert" data-testid="est-unmatched">
          {notes.unmatched}
        </p>
      )}
      {dom && (
        <table className={mod.table} data-testid="est-domain-table-info">
          <thead>
            <tr>
              <th className={mod.left}>Значение</th>
              <th>Проб</th>
              <th>Среднее</th>
              <th>Декл. среднее</th>
            </tr>
          </thead>
          <tbody>
            {dom.values.map((v) => (
              <tr key={v.value}>
                <td className={mod.left}>{v.value}</td>
                <td>{nf(v.n)}</td>
                <td>{g(v.naive)}</td>
                <td>{g(v.declus)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
