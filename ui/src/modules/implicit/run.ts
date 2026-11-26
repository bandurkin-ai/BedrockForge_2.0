/**
 * The run of a model (ТЗ 8.9.3; ADR 0026 «API»): «Построить» for a model that was never built (`POST /models/{id}/build`, the job
 * `implicit.build`), «Обновить» for a built one (`…/update`, the job `implicit.update`: only the patches of changed data and the
 * bricks they cover are counted again). The job is followed to its end (the stages are in «Задания»); the answer is the text of the
 * result. A model whose checks of the surface failed (`state: "error"`, `iso_check`) is a refusal with the words of the core: no
 * wireframes are written, the former ones stay. No React.
 */
import { refusal } from "../../shell/objectRun";
import { num } from "../../shell/props";
import { loadJob } from "../blocks/build/api";
import { projectStore } from "../projects/state";
import { readBuildResult, refusalOf, startBuild, startUpdate, waitJob, type BuildResult } from "./api";
import { implicitStore, loadDetails, loadRows, modelById } from "./state";

/** «41 230 точек, 57 участков, 41,7 с; наибольшая невязка 3,2e-9» — what the result says in a line. */
export function resultLine(r: BuildResult): string {
  const s = r.stats;
  const parts = [s.points !== null ? `${num(s.points, 0)} точек` : null, s.patches !== null ? `${num(s.patches, 0)} участков` : null, s.seconds !== null ? `${num(s.seconds, 1)} с` : null].filter(Boolean);
  const resid = s.max_residual !== null ? `; наибольшая невязка ${s.max_residual.toExponential(1).replace(".", ",")} м` : "";
  const reused = r.stats.reused.patches || r.stats.reused.bricks ? `; из прежнего построения взято участков ${num(r.stats.reused.patches, 0)}, кирпичей ${num(r.stats.reused.bricks, 0)}` : "";
  return `${parts.join(", ")}${resid}${reused}`;
}

/** What the graph says of a model that is not current (`stale_reason` — «изменена таблица «Литология»», «изменён тренд «Складка»»); null — current. */
export function staleLine(x: { stale: boolean; stale_reason: string | null; source_lost: boolean } | null | undefined): string | null {
  if (!x) return null;
  const why = x.stale_reason ? `: ${x.stale_reason}` : "";
  if (x.source_lost) return `Источник данных модели удалён${why === ": источник удалён" ? "" : why}. Выберите другие данные в мастере (шаг «Домены литологии») и обновите модель.`;
  if (x.stale) return `Модель устарела${why}. «Обновить» пересчитает только изменённые участки.`;
  return null;
}

/** «поверхностей 3, тел 4» of the outputs. */
export function outputsLine(r: BuildResult): string {
  const surfaces = r.outputs.filter((o) => o.kind === "surface").length;
  const bodies = r.outputs.filter((o) => o.kind === "body").length;
  const lines = r.outputs.filter((o) => o.kind === "line").length;
  return [`поверхностей ${surfaces}`, `тел ${bodies}`, ...(lines ? [`линий ${lines}`] : [])].join(", ");
}

/**
 * Builds or updates a model and waits for the job. The mode is the core's own: a model with no results is built, a built one is updated
 * (`mode` forces one). The answer is the Russian text of the result.
 */
export async function runBuild(modelId: number, mode?: "build" | "update"): Promise<string> {
  if (running.has(modelId)) throw refusal("busy", "Модель уже строится по вашей команде.", "Дождитесь окончания задания или отмените его во вкладке «Задания».");
  running.add(modelId);
  try {
    return await runBuildOnce(modelId, mode);
  } finally {
    running.delete(modelId);
  }
}

/** the models whose run this window started and waits for: a second start is refused, not queued */
const running = new Set<number>();

async function runBuildOnce(modelId: number, mode?: "build" | "update"): Promise<string> {
  const row = modelById(modelId);
  if (!row && implicitStore.get().loaded) throw refusal("model_not_found", `Неявная модель №${modelId} не найдена.`, "Номера моделей — в разделе «Неявные модели» дерева.");
  // the version of the model is the one the core stores now (the list could be older: a group saved a moment ago)
  const info = await loadDetails(modelId).catch((e: unknown) => {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  });
  const version = info?.version ?? row?.version;
  if (version === undefined) throw refusal("model_not_found", `Неявная модель №${modelId} не найдена.`, "Номера моделей — в разделе «Неявные модели» дерева.");
  if (info?.state === "building") throw refusal("model_building", "Модель сейчас строится.", "Дождитесь окончания задания или отмените его во вкладке «Задания».");
  const name = info?.name ?? row?.name ?? `№${modelId}`;
  const built = mode ? mode === "update" : !!row?.stats;
  const was = info?.stale ? `Модель была устаревшей${info.stale_reason ? ` (${info.stale_reason})` : ""}. ` : "";
  const uid = projectStore.get().current?.uid ?? null;
  let started;
  try {
    started = await (built ? startUpdate : startBuild)(modelId, version);
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
  let end;
  try {
    end = await waitJob(started);
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
  void loadRows();
  if ((projectStore.get().current?.uid ?? null) !== uid) return "Проект изменился: результат больше не показывается";
  if (end.status === "cancelled") return `Построение модели «${name}» отменено: ничего не записано`;
  if (end.status === "failed") throw refusal(end.error?.code ?? "job_failed", end.error?.message ?? "Модель не построена.", end.error?.hint ?? "Повторите построение.");
  const done = await loadJob(started).catch((e: unknown) => {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  });
  const r = readBuildResult(done.result);
  if (!r) throw refusal("bad_answer", "Ядро вернуло построение без итогов.", "Повторите построение; если повторится — обновите программу.");
  void loadDetails(modelId).catch(() => undefined);
  if (r.state === "error") {
    const e = r.error;
    throw refusal(e?.code ?? "iso_check", e?.message ?? "Поверхность не прошла проверки.", e?.hint ?? "Уменьшите шаг h или проверьте данные группы.");
  }
  return `${was}Модель «${name}» ${built ? "обновлена" : "построена"} (версия ${r.version}): ${outputsLine(r)}; ${resultLine(r)}.`;
}
