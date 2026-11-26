/**
 * The handlers of the commands of the groups and of the outputs of a model (ТЗ 8.9.4; ADR 0026 «Команды реестра»; docs/progress/e19-api.md §12):
 * - `implicit.shells`, `implicit.vein`, `implicit.expand` — a window of the group, or (with values) the write of the group (`groupBase.ts`);
 * - `implicit.to_wireframe` — a copy of an output as an ordinary wireframe (`POST /models/{id}/to_wireframe`); without `wireframe_id` the window
 *   «В каркас» lists the outputs;
 * - `implicit.delete` — after the question «Влияние изменения» (`askImpact`: what is built from the model) the model goes with its outputs
 *   (`DELETE /models/{id}`); 409 `in_use` — the list of those who use it.
 * «Обновить» (`implicit.update`) is in `run.ts`. No React.
 */
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { CoreError } from "../../shell/api";
import { askConfirm, showMessage } from "../../shell/dialogs";
import { needWritable, noted, refusal, viewport } from "../../shell/objectRun";
import { focusWindow, openWindow, windowStore } from "../../shell/windows";
import { askImpact } from "../lineage/impactAsk";
import { readUsage, usageText } from "../trends/api";
import { deleteModelApi, refusalOf, toWireframeApi } from "./api";
import { COPY_TITLE, COPY_WINDOW, copyStore } from "./copyState";
import { detailsOf, needModel, NO_MODEL } from "./groupBase";
import { expands, forgetGroups, shells, veins } from "./groupKinds";
import { needModelFor } from "./reasons";
import { implicitStore, loadRows, modelById } from "./state";

/** `implicit.to_wireframe {model_id?, wireframe_id?, name?}`. */
export async function runToWireframe(app: App, a: Record<string, unknown>): Promise<string> {
  const id = needModel(app, a.model_id);
  const info = await detailsOf(id);
  const outs = info.outputs.filter((o) => o.wireframe_id !== null);
  if (typeof a.wireframe_id !== "number") {
    if (!outs.length) throw refusal("not_built", `У модели «${info.name}» нет каркасов-выходов.`, "Постройте модель командой «Обновить».");
    copyStore.set({ modelId: id, wireframeId: outs.length === 1 ? outs[0].wireframe_id : null, name: typeof a.name === "string" ? a.name : "", busy: false, error: null });
    const open = windowStore.get().windows.find((w) => w.kind === COPY_WINDOW)?.id;
    if (open) focusWindow(open);
    else openWindow(COPY_WINDOW, COPY_TITLE, "form", {}, ...viewport());
    return `Открыто окно «${COPY_TITLE}» модели «${info.name}»`;
  }
  if (!outs.some((o) => o.wireframe_id === a.wireframe_id)) throw refusal("not_an_output", `Каркас №${a.wireframe_id} не является выходом модели «${info.name}».`, "Выходы модели — в её ветках «Поверхности» и «Тела» в дереве; копируются только они.");
  try {
    const r = await toWireframeApi(id, a.wireframe_id, typeof a.name === "string" && a.name.trim() ? a.name.trim() : undefined);
    return `Копия «${r.name}» (каркас №${r.wireframe_id}) записана в проект без связи с моделью «${info.name}»: её можно править и считать.`;
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
}

/** `implicit.delete {model_id}`: the question of the graph, then the deletion with the outputs. */
export async function runDelete(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const id = a.model_id as number;
  const row = modelById(id);
  if (!row && implicitStore.get().loaded) throw NO_MODEL(id);
  const name = row?.name ?? `№${id}`;
  const outputs = row?.outputs ?? 0;
  if (source === "user") {
    const tail = outputs ? ` Вместе с ней удалятся каркасы-выходы (${outputs}); копии, сделанные командой «В каркас», останутся.` : "";
    if (!(await askConfirm({ title: "Удалить неявную модель", message: `Удалить модель «${name}»?${tail}`, okLabel: "Удалить", danger: true }))) return "Отменено";
  }
  // what is built from the model (a table «Модель на скважины», a copy with the model as the input): the person decides (Д-35)
  if (!(await askImpact({ kind: "object:implicit_model", id }, `удалить неявную модель «${name}»`))) return "Отменено";
  try {
    const r = await deleteModelApi(id);
    forgetGroups(id);
    return `Модель «${name}» удалена${r.wireframes.length ? ` вместе с каркасами-выходами (${r.wireframes.length})` : ""}`;
  } catch (e) {
    if (e instanceof CoreError && e.code === "in_use") {
      const list = readUsage(e.body ?? {});
      const who = list.length ? usageText(list, 20) : e.message;
      const text = `Модель «${name}» используется: ${who}. Не удалена.`;
      if (source === "user") {
        await showMessage({ title: "Модель используется", message: `Модель «${name}» не удалена — на неё ссылаются: ${who}. Уберите ссылки или удалите эти объекты; полный список — в окне «Влияние изменения».` });
        return text;
      }
      throw refusal("in_use", text, "Уберите ссылки на модель или удалите зависимые объекты и повторите; список — команда «Влияние изменения».");
    }
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  } finally {
    await loadRows();
  }
}

/** The bindings: the groups, «В каркас», «Удалить». */
export function bindGroupCommands(app: App): void {
  app.registry.bind("implicit.shells", noted(app, (a, s) => shells.run(app, a, s)), needWritable(app));
  app.registry.bind("implicit.vein", noted(app, (a, s) => veins.run(app, a, s)), needWritable(app));
  app.registry.bind("implicit.expand", noted(app, (a, s) => expands.run(app, a, s)), needWritable(app));
  app.registry.bind("implicit.to_wireframe", noted(app, (a) => runToWireframe(app, a)), needModelFor(app, needWritable(app), "built"));
  app.registry.bind("implicit.delete", noted(app, (a, s) => runDelete(a, s)), needModelFor(app, needWritable(app), "any"));
}
