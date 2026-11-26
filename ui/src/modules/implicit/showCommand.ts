/**
 * `implicit.show {model_id?, what, group_id?, visible?}` (ТЗ 8.9.4; ADR 0026 «Команды реестра»): the data points of a group (or of every group of the model) and
 * the ellipsoid of the anisotropy in the 3D window. The tree's check boxes and «Свойства» run it; the layers are `scene.ts`. No React.
 */
import type { App } from "../../shell/app";
import { refusal } from "../../shell/objectRun";
import { num } from "../../shell/props";
import { refusalOf } from "./api";
import { NO_MODEL } from "./groupBase";
import { anisotropyOf, drawable, ellipsoidFrame, trendAngles } from "./propsModel";
import { implicitStore, loadDetails, modelById, pickModel } from "./state";
import { KIND_TITLES, kindCounts, setEllipsoid, setPoints, type PointsEntry } from "./viewState";

/** «показано 1 241 из 41 230 (контакт 5, внутри 27, снаружи 1 209)». */
export function entryText(e: PointsEntry): string {
  const k = kindCounts(e);
  const kinds = (Object.keys(k) as (keyof typeof k)[]).filter((c) => k[c] > 0).map((c) => `${KIND_TITLES[c]} ${num(k[c], 0)}`);
  const of = e.truncated ? ` из ${num(e.total, 0)} (в 3D первые по порядку ядра)` : "";
  const gap = e.taken > e.drawn ? `; без координаты пропущено ${num(e.taken - e.drawn, 0)}` : "";
  return `показано ${num(e.drawn, 0)}${of}${kinds.length ? ` (${kinds.join(", ")})` : ""}${gap}`;
}

export async function runShow(app: App, a: Record<string, unknown>): Promise<string> {
  const id = pickModel(a.model_id, app.store.get().selected);
  if (id === null) throw refusal("no_model", "Модель не выбрана.", "Передайте model_id или выберите модель в разделе «Неявные модели» дерева.");
  const row = modelById(id);
  if (!row && implicitStore.get().loaded) throw NO_MODEL(id);
  const visible = a.visible !== false;
  const name = row?.name ?? `№${id}`;
  if (a.what === "ellipsoid") {
    if (visible) {
      const info = await loadDetails(id).catch((e: unknown) => {
        const r = refusalOf(e);
        throw refusal(r.code, r.message, r.hint);
      });
      if (!info) throw refusal("project_changed", "Проект изменился.", "Повторите команду.");
      const an = anisotropyOf(info.meta, trendAngles);
      if (an.source === "none") throw refusal("no_anisotropy", `У модели «${name}» нет анизотропии: ядро изотропно.`, "Задайте углы и отношения осей на шаге «Интерполяция» мастера или выберите тренд.");
      if (!drawable(an) || !ellipsoidFrame(info.meta)) {
        throw refusal("no_ellipsoid", `Эллипсоид модели «${name}» нарисовать нельзя.`, an.source === "trend" ? "Оси заданы структурным трендом (у него локальные оси) или тренд не прочитан: откройте тренд." : "У модели нет охвата: постройте её.");
      }
    }
    setEllipsoid(id, visible);
    return visible ? `Эллипсоид анизотропии модели «${name}» показан в 3D` : `Эллипсоид анизотропии модели «${name}» скрыт`;
  }
  if (a.what !== "points") throw refusal("bad_param", "Что показать: points (точки данных) или ellipsoid (эллипсоид анизотропии).", "Передайте what: points или ellipsoid.");
  const all = row?.group_list ?? [];
  const ids = typeof a.group_id === "number" ? [a.group_id] : all.map((g) => g.id).filter((g): g is number => g !== null);
  if (!ids.length) throw refusal("no_groups", `В модели «${name}» нет групп: показывать нечего.`, "Добавьте домены командой «Домены литологии».");
  for (const g of ids) if (all.length && !all.some((x) => x.id === g)) throw refusal("group_not_found", `В модели «${name}» нет группы №${g}.`, "Номера групп — в ветке «Данные» модели.");
  const lines: string[] = [];
  for (const g of ids) {
    const label = all.find((x) => x.id === g)?.name ?? `№${g}`;
    const e = await setPoints(id, g, visible);
    if (!visible || !e) {
      lines.push(`точки группы «${label}» скрыты`);
      continue;
    }
    if (e.status === "error") {
      await setPoints(id, g, false);
      throw refusal("points_failed", `Точки группы «${label}» не прочитаны: ${e.error ?? "ошибка ядра"}`, "Проверьте таблицу и коды группы в мастере.");
    }
    lines.push(`точки группы «${label}»: ${entryText(e)}`);
  }
  return `${name}: ${lines.join("; ")}`;
}
