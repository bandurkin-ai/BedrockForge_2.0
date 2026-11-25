/**
 * The words of step 6 of the wizard «Оценка содержаний» (ТЗ 8.8.4: «… → выходные поля → задание»): what the row will do — the model,
 * the domain, the samples, the method, the search with its passes, the fields it writes — and what to look at before the run: the
 * fields that the model has already (the core will ask «Заменить поля?»), no samples in the domain, the warnings of the core about the
 * model of variogram. Pure: the facts come from the form and from the answer of `defaults`.
 */
import { METHOD_TITLES, type DefaultsAnswer } from "./api";
import { thresholdsOf, type EstimationForm } from "./form";
import { isIssue } from "./formBase";
import { anyHy, defaultPrefix, effectiveFields, effectiveFrom, existingFields, fieldNames, passesOf, type PassForm } from "./formSearch";

const nf = (n: number): string => n.toLocaleString("ru-RU");
const g = (n: number | null | undefined): string => (n === null || n === undefined ? "—" : n.toLocaleString("ru-RU", { maximumSignificantDigits: 4 }));

export interface SummaryCtx {
  /** the block model of the form (its name, the number of blocks, the names of its fields); null — not known yet */
  model: { name: string; cells: number; fields: readonly string[] } | null;
  defaults: DefaultsAnswer | null;
  names: { filter: (id: number) => string | null; trend: (id: number) => string | null; variogram: (id: number) => string | null };
}

/** The number of the thresholds of IK of the form (P1…PK); 0 — the thresholds are not (yet) a list. */
export function thresholdCount(f: EstimationForm): number {
  if (f.method !== "ik") return 0;
  const t = thresholdsOf(f.thresholds);
  return isIssue(t) ? 0 : t.length;
}

/** The prefix of the fields: the typed one, else the core's («AU_OK_»). */
export const prefixOf = (f: EstimationForm): string => f.outputs.prefix.trim() || defaultPrefix(f.field ?? "", f.method);

/** The keys written by the row and the names of the fields of the model. */
export function outputsOf(f: EstimationForm, d: DefaultsAnswer | null): { keys: string[]; names: string[] } {
  const params = (d?.params ?? {}) as { outputs?: { fields?: Record<string, string[]>; allowed?: Record<string, string[]> } };
  const keys = effectiveFields(f.method, f.outputs, anyHy(passesOf(f.search, d?.params)), params.outputs?.fields, params.outputs?.allowed?.[f.method]);
  return { keys, names: fieldNames(prefixOf(f), keys, thresholdCount(f)) };
}

/** A pass in one line: «проход 2 — радиусы ×2, проб 4–24, до 3 из скважины, ураганные: t = 20, до 0,5·r1, урезать». */
export function passLine(p: PassForm, i: number): string {
  const parts = [`радиусы ×${p.factor}`, `проб ${p.nMin}–${p.nMax}`];
  if (p.nHole !== "0") parts.push(`до ${p.nHole} из скважины`);
  if (p.nOct !== "0") parts.push(`до ${p.nOct} в октанте`);
  if (p.kOct !== "0") parts.push(`не менее ${p.kOct} октантов`);
  if (p.nHolesMin !== "0") parts.push(`не менее ${p.nHolesMin} скважин`);
  if (p.hy.on) parts.push(`ураганные: t = ${p.hy.t}, до ${p.hy.q}·r1, ${p.hy.mode === "cap" ? "урезать" : "отбросить"}`);
  return `проход ${i + 1} — ${parts.join(", ")}`;
}

/** The ellipsoid in words: the source, the angles, the radii. */
export function ellipsoidText(f: EstimationForm, ctx: SummaryCtx): string {
  const hasModel = f.method === "ik" && f.ikModels === "per" ? f.variogramIds.some((id) => id !== null) : f.variogramId !== null;
  const from = effectiveFrom(f.search, hasModel);
  const d = ctx.defaults?.search;
  const src = from === "model" ? "по модели вариограммы" : from === "trend" ? `по тренду${f.search.trendId !== null ? ` «${ctx.names.trend(f.search.trendId) ?? `№${f.search.trendId}`}»` : ""}` : "вручную";
  const ang = from === "model" && d ? [d.ang1, d.ang2, d.ang3].map(g) : f.search.ang;
  const r = from === "model" && d ? [d.r1, d.r2, d.r3].map(g) : f.search.r;
  const radii = r.some((x) => x !== "" && x !== "—") ? `; радиусы ${r.map((x) => (x === "" ? "—" : x)).join(" × ")} м` : "";
  const angles = ang.some((x) => x !== "" && x !== "—") && from !== "trend" ? `; углы ${ang.map((x) => (x === "" ? "0" : x)).join(" / ")}°` : "";
  return `${src}${radii}${angles}`;
}

/** The lines of the summary: what the row will do, by the steps of the wizard. */
export function summaryRows(f: EstimationForm, ctx: SummaryCtx): { label: string; value: string }[] {
  const d = ctx.defaults;
  const rows: { label: string; value: string }[] = [];
  const add = (label: string, value: string) => rows.push({ label, value });
  add("Блочная модель", ctx.model ? `«${ctx.model.name}», блоков ${nf(ctx.model.cells)}` : "не выбрана");
  add("Домен блоков", f.domainField ? `${f.domainField} = «${f.label}»` : "все блоки модели");
  if (f.filterId !== null) add("Ограничитель", `«${ctx.names.filter(f.filterId) ?? `№${f.filterId}`}»`);
  add("Блок", `${f.sub === "own" ? "каждый субблок оценивается сам" : "оценка в центре родителя"}, дискретизация ${f.disc.join(" × ")}`);
  const unit = d?.samples.unit ? ` (${d.samples.unit})` : "";
  add("Пробы", `${d?.samples.table_name ?? f.table ?? "—"} · ${f.field ?? "—"}${unit}${f.domain ? `; домен проб ${f.domain.field} = ${f.domain.values.map((v) => `«${v}»`).join(", ")}` : ""}`);
  if (f.soft.on) add("Мягкая граница", `соседние домены ${f.soft.domains.map((v) => `«${v}»`).join(", ") || "—"}, d_м = ${f.soft.dist} м`);
  if (f.topcut.mode !== "none") add("Урезка", f.topcut.mode === "field" ? `поле ${f.topcut.field}` : `min(z, ${f.topcut.value})`);
  add("Веса", f.weights.kind === "none" ? "без весов" : f.weights.kind === "field" ? f.weights.field : (d?.samples.weights ?? "нет поля DECL_W — без весов"));
  if (d) add("Проб в оценке", `${nf(d.samples.counts.used)} из ${nf(d.samples.counts.rows)}`);
  let method = METHOD_TITLES[f.method];
  if (f.method === "idw") method += `, p = ${f.power}`;
  if (f.method === "sk") method += f.meanMode === "value" ? `, среднее ${f.meanValue}` : ", декластерированное среднее";
  if (f.method === "ik") method += `, пороги ${f.thresholds || "—"}`;
  add("Метод", method);
  const ids = f.method === "ik" && f.ikModels === "per" ? f.variogramIds : [f.variogramId];
  const vario = ids.flatMap((id) => (id === null ? [] : [`«${ctx.names.variogram(id) ?? `№${id}`}»`]));
  add("Модель вариограммы", vario.length ? vario.join(", ") : "не используется");
  add("Эллипсоид поиска", ellipsoidText(f, ctx));
  passesOf(f.search, d?.params).forEach((p, i) => add(i === 0 ? "Проходы" : "", passLine(p, i)));
  const { names } = outputsOf(f, d);
  add("Выходные поля", `${names.length}: ${names.length > 8 ? `${names.slice(0, 8).join(", ")} и ещё ${names.length - 8}` : names.join(", ")}`);
  return rows;
}

/** What to look at before the run. */
export function wizardWarnings(f: EstimationForm, ctx: SummaryCtx): string[] {
  const out: string[] = [];
  const d = ctx.defaults;
  if (d && d.samples.counts.used === 0) out.push("В домене нет проб: оценка не даст ни одного блока со статусом «оценён».");
  const { names } = outputsOf(f, d);
  const have = ctx.model ? existingFields(ctx.model.fields, names) : [];
  if (have.length) out.push(`В модели уже есть поля: ${have.length > 6 ? `${have.slice(0, 6).join(", ")} и ещё ${have.length - 6}` : have.join(", ")}. При запуске ядро спросит «Заменить поля?»; другой префикс сохранит прежние поля.`);
  for (const w of d?.variogram?.warnings ?? []) out.push(w);
  return out;
}
