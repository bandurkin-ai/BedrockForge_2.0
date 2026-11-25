/**
 * The buttons of the windows that save (the wizard, «Параметры оценки», the plan window) run the commands `estimate.save` and
 * `estimate.plan_save` through the registry (the log, the keys, the assistant see the same save; the handlers are `bind.ts`). A refusal
 * stands under its field or under the bar of the window (`wizardState`, `planState`) and in the status bar. No React.
 */
import type { App } from "../../shell/app";
import { planSaveArgs, planStore } from "./planState";

/** «Сохранить» / «Сохранить как новую» of the form of a row. */
export const saveRowByCommand = (app: App, asNew: boolean): Promise<void> => app.run("estimate.save", asNew ? { as_new: true } : {});

/** «Сохранить» / «Сохранить как новый» of the plan: the working copy of the window as the arguments of the command. */
export const savePlanByCommand = (app: App, asNew: boolean): Promise<void> => app.run("estimate.plan_save", planSaveArgs(planStore.get(), asNew));
