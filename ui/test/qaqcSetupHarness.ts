// The commands of the setup of QAQC bound by `bindQaqcSetup` (ADR 0020 §9), declared here until `commands.ts`
// of 14.15 declares them; an app with an open project and the assistant's confirmations accepted.
import { bindQaqcSetup } from "../src/modules/qaqc/bindSetup";
import { createApp, type App } from "../src/shell/app";
import type { Command } from "../src/shell/commands";

/** The commands of ADR 0020 §9 bound by `bindQaqcSetup` (until 14.15 declares them). */
const S = (type: "string" | "integer" | "number" | "boolean") => ({ type });
export const QAQC_STUBS: Command[] = [
  { id: "qaqc.setup", title: "Настройка QAQC", description: "", params: { tab: { type: "string", enum: ["data", "types", "crm", "dl", "limits"] } }, uiOnly: true, stage: "Э14" },
  { id: "qaqc.import", title: "Загрузить контрольные пробы", description: "", params: { path: S("string"), set_id: S("integer"), table: S("string"), name: S("string"), mapping: S("string"), replace_id: S("integer") }, confirm: true, stage: "Э14" },
  { id: "qaqc.open", title: "QAQC", description: "", params: { section: S("string"), data_id: S("integer"), element: S("string"), kind: S("string") }, required: ["section"], stage: "Э14" },
  { id: "qaqc.data_rename", title: "Переименовать данные QAQC", description: "", params: { data_id: S("integer"), name: S("string") }, required: ["data_id", "name"], stage: "Э14" },
  { id: "qaqc.data_refresh", title: "Обновить данные QAQC", description: "", params: { data_id: S("integer") }, required: ["data_id"], confirm: true, stage: "Э14" },
  { id: "qaqc.data_delete", title: "Удалить данные QAQC", description: "", params: { data_id: S("integer") }, required: ["data_id"], confirm: true, stage: "Э14" },
  { id: "qaqc.config", title: "Настройка QAQC: типы, DL, пороги", description: "", params: { types: S("string"), dl: S("string"), empty_is_orig: S("boolean"), sigma_warn: S("number"), sigma_fail: S("number"), run: S("integer"), k_blank: S("number"), k_dup: S("number"), th_group: S("integer"), hard_p: S("number"), hard_c: S("number"), hard_f: S("number"), hard_ump: S("number"), hard_share: S("number"), n_boot: S("integer"), seed: S("integer") }, confirm: true, stage: "Э14" },
  { id: "qaqc.crm", title: "Справочник CRM", description: "", params: { rows: S("string"), remove: S("string") }, confirm: true, stage: "Э14" },
];

export function setupApp(): App {
  const app = createApp({ persist: false });
  app.registry.register(QAQC_STUBS.filter((c) => !app.registry.get(c.id)));
  bindQaqcSetup(app);
  app.registry.hooks = { ...app.registry.hooks, confirm: async () => true };
  app.store.set({ projectName: "Проект", projectReadOnly: false });
  return app;
}
