/** The windows that hold the form of the estimation (the wizard and «Параметры оценки»): while one is open the preview is drawn in 3D (`preview.ts`). */
import { windowStore } from "../../shell/windows";
import { PARAMS_WINDOW } from "./ParamsWindow";
import { WIZARD_WINDOW } from "./wizardState";

export const formWindowOpen = (): boolean => windowStore.get().windows.some((w) => w.kind === WIZARD_WINDOW || w.kind === PARAMS_WINDOW);
