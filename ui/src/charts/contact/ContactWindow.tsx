/**
 * The window «Контактный анализ» (ТЗ 8.3; ADR 0019 §4, §11): the contacts of domains A ↔ B along the holes (a text
 * field of the same or another table), or the crossing of a hole with a closed wireframe («до каркаса»: outside /
 * inside); the mean of the field and n by steps b up to D on both sides, the number of contacts; a step line — a
 * hard boundary, a slope — a soft one (8.8.2). CSV of the steps. An edit computes again 300 ms later.
 * Д-28: a model of blocks and the field of its estimate add the profile of the blocks as a second line of the same chart
 * (does the estimate keep the character of the boundary); «Задать мягкую границу…» opens the wizard of the estimation with
 * the pair A | B and the d_м the person chose (the chart only shows it as two marks — nothing is derived).
 */
import { useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { AppContext } from "../../shell/AppContext";
import { blocksStore, loadBlockDetails } from "../../modules/blocks/state";
import { errorText } from "../../shell/props";
import { defaultField } from "../../modules/charts/api";
import type { ContactResult } from "../../modules/charts/apiMore";
import type { ChartWindowProps } from "../../modules/charts/bind";
import { useSavedChart } from "../../modules/charts/saved";
import { DEFAULT_CONTACT_VIEW, type ContactSettings } from "../../modules/charts/settings";
import { sceneObjects } from "../../modules/scene/objects";
import { ChartWindow } from "../ChartWindow";
import { formatNumber } from "../core/axisScale";
import { ChartToolbar } from "../core/ChartToolbar";
import { DomainSource, fieldLabel, keepField, useDomainCodes, useStatsFields } from "../core/Pickers";
import { NumberInput, SampleFilterForm } from "../SampleFilterForm";
import css from "../core/Chart.module.css";
import { ContactChart } from "./ContactChart";
import { blockDefaults, contactCalculator, contactCsv, contactNotes, contactProblem, contactRequest, openSoft, softProblem } from "./model";

const getScene = () => sceneObjects.get();
const getBlocks = () => blocksStore.get();

/** The block models the profile can read: a regular step, with blocks. */
const profileModels = (list: ReturnType<typeof getBlocks>["list"]) => list.filter((m) => !m.grid.variable && m.cells > 0);

function CodeSelect({ label, value, codes, onChange }: { label: string; value: string | null; codes: readonly string[]; onChange: (v: string | null) => void }) {
  const list = value && !codes.includes(value) ? [value, ...codes] : codes;
  return (
    <label>
      {label}
      <select value={value ?? ""} aria-label={label} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">— выберите —</option>
        {list.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </label>
  );
}

export function ContactWindow(props: ChartWindowProps<"contact">) {
  const { setId, setName } = props;
  const [s, setS] = useState<ContactSettings>(props.settings);
  const [result, setResult] = useState<ContactResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const app = useContext(AppContext);
  const scene = useSyncExternalStore(sceneObjects.subscribe, getScene, getScene);
  const bs = useSyncExternalStore(blocksStore.subscribe, getBlocks, getBlocks);
  const [softD, setSoftD] = useState<number | null>(null);
  const [softNote, setSoftNote] = useState<string | null>(null);
  const set = (patch: Partial<ContactSettings>) => setS((prev) => ({ ...prev, ...patch }));
  const saved = useSavedChart("contact", props, s, setS);

  const f = useStatsFields(setId, s.table);
  useEffect(() => {
    const x = f.fields;
    if (x) setS((prev) => ({ ...prev, table: prev.table ?? x.table, field: keepField(x, prev.field) ?? defaultField(x), weight_field: keepField(x, prev.weight_field) }));
  }, [f.fields]);
  const codes = useDomainCodes(setId, s.domain_table ?? s.table, s.domain);
  const calc = useMemo(() => contactCalculator((r) => (setResult(r), setError(null)), (e) => setError(errorText(e)), setBusy), []);
  useEffect(() => () => calc.stop(), [calc]);
  const request = useMemo(() => contactRequest(s), [s]);
  useEffect(() => {
    if (f.fields && request) calc.request(request);
  }, [calc, f.fields, request]);

  const modelId = s.blocks?.model_id ?? null;
  useEffect(() => {
    if (modelId !== null) void loadBlockDetails(modelId);
  }, [modelId]);
  const models = profileModels(bs.list);
  const model = modelId === null ? undefined : (bs.details[modelId] ?? bs.list.find((m) => m.id === modelId));
  const bf = model?.fields ?? [];
  const chooseModel = (id: number | null) => {
    if (id === null) return set({ blocks: null });
    const m = bs.details[id] ?? bs.list.find((x) => x.id === id);
    const d = m ? blockDefaults(m.fields, s) : null;
    set({ blocks: { model_id: id, field: d?.field ?? "", ...(d?.status_field ? { status_field: d.status_field } : {}), ...(d?.domain_field ? { domain_field: d.domain_field } : {}) } });
  };
  const setBlocks = (patch: { field?: string; status_field?: string | null; domain_field?: string | null }) =>
    setS((p) => {
      if (!p.blocks) return p;
      const next: Record<string, unknown> = { ...p.blocks, ...patch };
      for (const k of ["status_field", "domain_field"]) if (!next[k]) delete next[k];
      return { ...p, blocks: next as unknown as NonNullable<ContactSettings["blocks"]> };
    });
  const soft = softProblem(s, softD);
  const solids = scene.wireframes.filter((w) => !w.dtm && w.stats?.closed !== false);
  const byWire = s.wireframe_id !== null;
  const info = f.fields?.numeric.find((v) => v.name === s.field);
  const xLabel = info ? fieldLabel(info) : (s.field ?? "");
  const settingsForm = (
    <>
      <DomainSource setId={setId} value={s} fields={f.fields} onChange={(p) => set({ ...p, ...(p.domain !== undefined && p.domain !== s.domain ? { a: null, b: null } : {}) })} domainEmpty={null} />
      <fieldset>
        <legend>Контакт</legend>
        <div className={css.seg} role="group" aria-label="Контакт">
          <button type="button" aria-pressed={!byWire} title="Смена доменов A и B вдоль ствола" onClick={() => set({ wireframe_id: null })}>
            Домены A | B
          </button>
          <button type="button" aria-pressed={byWire} disabled={!solids.length} title={solids.length ? "Пересечение ствола с замкнутым каркасом: снаружи / внутри" : "В проекте нет замкнутых каркасов"} onClick={() => set({ wireframe_id: solids[0]?.id ?? null })}>
            До каркаса
          </button>
        </div>
        {byWire ? (
          <label>
            Каркас
            <select value={s.wireframe_id ?? ""} aria-label="Каркас" onChange={(e) => set({ wireframe_id: e.target.value ? Number(e.target.value) : null })}>
              {s.wireframe_id !== null && !solids.some((w) => w.id === s.wireframe_id) && <option value={s.wireframe_id}>Каркас №{s.wireframe_id}</option>}
              {solids.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <div className={css.pair}>
            <CodeSelect label="Домен A" value={s.a} codes={codes} onChange={(a) => set({ a })} />
            <CodeSelect label="Домен B" value={s.b} codes={codes} onChange={(b) => set({ b })} />
          </div>
        )}
        <div className={css.pair}>
          <label>
            Расстояние D, м
            <NumberInput label="Расстояние D" value={s.distance} placeholder="20" onChange={(v) => set({ distance: v ?? 20 })} />
          </label>
          <label>
            Шаг b, м
            <NumberInput label="Шаг b" value={s.step} placeholder="1" onChange={(v) => set({ step: v ?? 1 })} />
          </label>
        </div>
      </fieldset>
      <fieldset>
        <legend>Блоки</legend>
        <label>
          Блочная модель
          <select value={modelId ?? ""} aria-label="Блочная модель" onChange={(e) => chooseModel(e.target.value ? Number(e.target.value) : null)}>
            <option value="">{models.length ? "— без профиля блоков —" : "нет блочных моделей с блоками"}</option>
            {modelId !== null && !models.some((m) => m.id === modelId) && <option value={modelId}>Модель №{modelId}</option>}
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        {s.blocks && (
          <>
            <label>
              Поле оценки
              <select value={s.blocks.field} aria-label="Поле оценки блоков" onChange={(e) => setBlocks({ field: e.target.value })}>
                <option value="">— выберите —</option>
                {bf.filter((x) => x.kind === "number" || x.name === s.blocks?.field).map((x) => (
                  <option key={x.name} value={x.name}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Статус оценки (0 — оценён)
              <select value={s.blocks.status_field ?? ""} aria-label="Поле статуса блоков" onChange={(e) => setBlocks({ status_field: e.target.value || null })}>
                <option value="">все блоки</option>
                {bf.filter((x) => x.kind === "number").map((x) => (
                  <option key={x.name} value={x.name}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>
            {!byWire && (
              <label>
                Поле домена модели
                <select value={s.blocks.domain_field ?? ""} aria-label="Поле домена блоков" onChange={(e) => setBlocks({ domain_field: e.target.value || null })}>
                  <option value="">{s.domain ? `как у проб (${s.domain})` : "— выберите —"}</option>
                  {bf.filter((x) => x.kind === "category").map((x) => (
                    <option key={x.name} value={x.name}>
                      {x.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </>
        )}
        <span className={css.note}>Среднее поля оценки по блокам рядом с пробами: расстояние — от центра блока до границы, вес — объём.</span>
      </fieldset>
      <fieldset>
        <legend>Мягкая граница</legend>
        <label>
          Расстояние d_м, м
          <NumberInput label="Расстояние d_м" value={softD} placeholder="задайте" title="Расстояние, на котором пробы соседнего домена допускаются у границы; решение за вами" onChange={(v) => (setSoftD(v), setSoftNote(null))} />
        </label>
        <button
          type="button"
          className={css.action}
          disabled={!app || soft !== null}
          title={soft ?? "Открыть мастер оценки с парой доменов A | B и этим расстоянием"}
          onClick={() => {
            if (app) void openSoft(app, s, softD).then(setSoftNote);
          }}
        >
          Задать мягкую границу…
        </button>
        <span className={css.note}>{softNote ?? (soft && softD !== null ? soft : "График лишь показывает, где проходит d_м: по отметкам на графике решаете вы.")}</span>
      </fieldset>
      <SampleFilterForm value={s} fields={f.fields?.numeric ?? []} onChange={set} />
      <fieldset>
        <legend>Вид</legend>
        <label className={css.check}>
          <input type="checkbox" checked={s.view.counts} onChange={(e) => setS((p) => ({ ...p, view: { ...p.view, counts: e.target.checked } }))} /> Число проб по шагам
        </label>
      </fieldset>
    </>
  );
  const fileName = `Контакт ${setName} ${s.field ?? ""} ${byWire ? `каркас ${s.wireframe_id}` : `${s.a ?? ""}-${s.b ?? ""}`}`.trim();
  const toolbar = <ChartToolbar svgRef={svgRef} fileName={fileName} disabled={!result?.edges.length} csvLabel="CSV шагов" csv={() => (result ? contactCsv(result) : null)} onReset={() => setS((p) => ({ ...p, view: { ...DEFAULT_CONTACT_VIEW } }))} />;
  const empty = !f.fields ? "Расчёт…" : !f.fields.numeric.length ? "В таблице нет числовых полей." : (contactProblem(s) ?? (result && !result.contacts ? "Контактов не найдено." : "Расчёт…"));
  return (
    <ChartWindow
      settings={settingsForm}
      toolbar={toolbar}
      busy={busy}
      error={error ?? f.error}
      empty={empty}
      note={saved.note}
      chart={result && result.contacts > 0 && <ContactChart ref={svgRef} result={result} counts={s.view.counts} xLabel={xLabel} soft={softD} />}
      footer={
        result && (
          <>
            <table className={css.stats} data-testid="contact-stats">
              <thead>
                <tr>
                  <th>Сторона</th>
                  <th>Проб</th>
                  <th>Среднее</th>
                </tr>
              </thead>
              <tbody>
                {[result.a, result.b].map((side, i) => (
                  <tr key={i}>
                    <th>{side.label}</th>
                    <td>{side.n.toLocaleString("ru-RU")}</td>
                    <td>{formatNumber(side.mean, 5)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className={css.muted} data-testid="contact-notes">
              {contactNotes(result).join(" · ")}
            </p>
            {result.conventions && <p className={css.muted}>{result.conventions}</p>}
          </>
        )
      }
    />
  );
}
